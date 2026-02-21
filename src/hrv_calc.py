#!/usr/bin/env python3
"""
Read JSON lines (hr, rr_ms, ts) from stdin; maintain rolling window of RR intervals;
output JSON lines with rmssd_ms, hrv_score (0-100), and optional hr/ts.
Pass-through lines starting with "# " unchanged.
"""

import argparse
import json
import logging
import sys
import time
from collections import deque
from datetime import datetime

from src.hrv import rmssd_ms, hrv_score, smooth_spikes
from src.rr_artifact_filter import RrArtifactFilter


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def _emit(s: str) -> None:
    """Print to stdout; exit cleanly if downstream closed the pipe (e.g. graph_server died)."""
    try:
        print(s, flush=True)
    except BrokenPipeError:
        sys.exit(0)


def _run(
    window_sec: float,
    min_intervals: int,
    min_beats: int,
    spike_filter_ms: float,
    smooth_output_n: int,
    window_short_sec: float,
    blend: float,
    rr_clean: bool,
    rr_clean_thresh: float,
    rr_clean_grace: int,
    rr_clean_disable_interp_noise: bool,
    rr_clean_hampel_window: int,
    rr_clean_hampel_sigma: float,
    rr_clean_disable_hampel: bool,
    rr_clean_min_rr: float,
    rr_clean_max_rr: float,
    stats_interval_sec: float,
    debug_rr: bool,
    interp_max_fallback: float,
    noise_dynamic: bool,
    window_adaptive: bool,
):
    # Rolling buffer: (rr_ms, ts, was_interpolated) for last window_sec
    buffer: deque[tuple[float, float, bool]] = deque()
    latest_ts: float | None = None
    first_hrv_emitted = False
    last_cleaned_rr: float | None = None
    last_valid_rmssd: float | None = None
    last_valid_score: float | None = None
    last_interp_ratio: float = 0.0
    score_history: deque[float] = deque(maxlen=smooth_output_n) if smooth_output_n > 0 else deque()
    rr_filter: RrArtifactFilter | None = None
    last_stats_time: float = 0.0
    base_effective_min = max(min_intervals, min_beats)

    if rr_clean:
        _log = logging.getLogger("src.hrv_calc")
        rr_filter = RrArtifactFilter(
            thresh_percent=rr_clean_thresh,
            min_rr_ms=rr_clean_min_rr,
            max_rr_ms=rr_clean_max_rr,
            hampel_window=rr_clean_hampel_window,
            hampel_sigma=rr_clean_hampel_sigma,
            disable_hampel=rr_clean_disable_hampel,
            buffer_maxlen=30,
            grace_beats=rr_clean_grace,
            interp_noise=not rr_clean_disable_interp_noise,
            noise_dynamic=noise_dynamic,
            logger_instance=_log,
        )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            _emit(line)
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        hr = obj.get("hr")
        rr_raw = obj.get("rr_ms")
        ts = obj.get("ts")
        if rr_raw is None:
            out = {"hr": hr, "rmssd_ms": None, "hrv_score": None, "ts": ts, "last_rr_ms": last_cleaned_rr, "interp_ratio": last_interp_ratio}
            if debug_rr and rr_filter is not None:
                s = rr_filter.stats()
                out["filter_stats"] = {"dropped": s["dropped"], "interp": s["interpolated"]}
            _emit(json.dumps(out))
            continue
        try:
            rr_val = float(rr_raw)
        except (TypeError, ValueError):
            continue
        was_interp = False
        if rr_filter is not None:
            if noise_dynamic:
                rr_filter.interp_ratio_hint = last_interp_ratio
            rr_val = rr_filter.process_rr(rr_val)
            if rr_val is None:
                continue
            was_interp = rr_filter.last_was_interpolated
        else:
            if rr_val < 300 or rr_val > 2000:
                continue
        if ts is not None:
            latest_ts = ts if latest_ts is None else max(latest_ts, ts)
        buffer.append((rr_val, latest_ts if latest_ts is not None else 0.0, was_interp))
        last_cleaned_rr = rr_val
        # Drop entries older than window_sec
        if latest_ts is not None:
            cutoff = latest_ts - window_sec
            while buffer and buffer[0][1] < cutoff:
                buffer.popleft()
        else:
            max_len = int(window_sec * 1.5)
            while len(buffer) > max_len:
                buffer.popleft()
        rr_list = [b[0] for b in buffer]
        interp_count = sum(1 for b in buffer if b[2])
        interp_ratio = (interp_count / len(buffer)) if buffer else 0.0
        valid_rr_count = len(buffer) - interp_count
        if window_adaptive and rr_filter is not None:
            effective_min = max(min_intervals, 25 + int(10 * interp_ratio))
        else:
            effective_min = base_effective_min
        if spike_filter_ms > 0:
            rr_list = smooth_spikes(rr_list, spike_filter_ms)
        last_interp_ratio = round(interp_ratio, 3)
        if len(rr_list) < effective_min:
            out = {"hr": hr, "rmssd_ms": None, "hrv_score": None, "ts": ts, "last_rr_ms": last_cleaned_rr, "interp_ratio": last_interp_ratio}
            if rr_filter is not None:
                s = rr_filter.stats()
                out["rr_dropped"] = s["dropped"]
                out["rr_interpolated"] = s["interpolated"]
                if debug_rr:
                    out["filter_stats"] = {"dropped": s["dropped"], "interp": s["interpolated"]}
            _emit(json.dumps(out))
            continue
        # RMSSD safety (when rr_clean): high interp ratio or too few valid RRs -> last valid or N/A
        if rr_filter is not None and (interp_ratio > interp_max_fallback or valid_rr_count < 15):
            if interp_ratio > interp_max_fallback and last_valid_rmssd is not None:
                rms_emit = last_valid_rmssd
                score = last_valid_score if last_valid_score is not None else 0
            else:
                rms_emit = None
                score = None
        else:
            use_two_windows = window_short_sec > 0 and 0 < blend < 1 and latest_ts is not None
            try:
                rms = rmssd_ms(rr_list)
                score = hrv_score(rms)
                if use_two_windows:
                    cutoff_short = latest_ts - window_short_sec
                    rr_short = [b[0] for b in buffer if b[1] >= cutoff_short]
                    min_short = max(2, min_intervals // 3)
                    if len(rr_short) >= min_short:
                        rms_short = rmssd_ms(rr_short)
                        score_short = hrv_score(rms_short)
                        score = blend * score_short + (1.0 - blend) * score
                if smooth_output_n > 0:
                    score_history.append(float(score))
                    score = sum(score_history) / len(score_history)
                rms_emit = rms
                if rr_filter is None or (interp_ratio <= interp_max_fallback and valid_rr_count >= 15):
                    last_valid_rmssd = rms
                    last_valid_score = int(round(score))
            except ValueError:
                continue
        if rms_emit is not None and not first_hrv_emitted:
            print(_ts() + "First HRV score emitted.", file=sys.stderr)
            first_hrv_emitted = True
        out = {
            "hr": hr,
            "rmssd_ms": round(rms_emit, 2) if rms_emit is not None else None,
            "hrv_score": int(round(score)) if score is not None else None,
            "ts": ts,
            "last_rr_ms": last_cleaned_rr,
            "interp_ratio": last_interp_ratio,
        }
        if rr_filter is not None:
            s = rr_filter.stats()
            out["rr_dropped"] = s["dropped"]
            out["rr_interpolated"] = s["interpolated"]
            if stats_interval_sec > 0:
                now = time.time()
                if last_stats_time == 0:
                    last_stats_time = now
                elif now - last_stats_time >= stats_interval_sec:
                    print(
                        _ts() + f"RR clean stats: dropped={s['dropped']} interpolated={s['interpolated']}",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_stats_time = now
            if debug_rr:
                out["filter_stats"] = {"dropped": s["dropped"], "interp": s["interpolated"]}
        elif debug_rr:
            out["filter_stats"] = {"dropped": 0, "interp": 0}
        _emit(json.dumps(out))


def main():
    parser = argparse.ArgumentParser(description="Compute rolling RMSSD and HRV score (0-100) from RR stream")
    parser.add_argument("--window", "-w", type=float, default=60.0, help="Rolling window in seconds (default 60)")
    parser.add_argument("--min-intervals", "-n", type=int, default=30, help="Min RR intervals before emitting (default 30)")
    parser.add_argument("--min-beats", type=int, default=15, metavar="N", help="Min beats in RMSSD window before compute (default 15); effective min = max(min-intervals, min-beats)")
    parser.add_argument(
        "--spike-filter", "-s",
        type=float,
        default=0,
        metavar="MS",
        help="Optional: smooth RR spikes by capping change to ±MS ms. 0 = off (default)",
    )
    parser.add_argument(
        "--smooth-output", "-o",
        type=int,
        default=0,
        metavar="N",
        help="Sliding average of last N HRV scores (smooths graph). 0 = off (default)",
    )
    parser.add_argument(
        "--window-short",
        type=float,
        default=0,
        metavar="SEC",
        help="If set with --blend: also compute RMSSD on last SEC seconds and blend with long window (favors recent)",
    )
    parser.add_argument(
        "--blend",
        type=float,
        default=0,
        metavar="R",
        help="With --window-short: emit R*score_short + (1-R)*score_long (e.g. 0.6 = favor recent). Ignored if --window-short 0",
    )
    parser.add_argument(
        "--rr-clean",
        action="store_true",
        help="Enable RR artifact cleaning (threshold + Hampel) before RMSSD",
    )
    parser.add_argument("--rr-clean-thresh", type=float, default=0.4, metavar="P", help="RR clean: threshold (std-based) (default 0.4)")
    parser.add_argument("--rr-clean-grace", type=int, default=30, metavar="N", dest="rr_clean_grace", help="RR clean: grace beats before applying threshold (default 30)")
    parser.add_argument("--disable-interp-noise", action="store_true", dest="rr_clean_disable_interp_noise", help="Disable noise injection on interpolated RR (default: noise on)")
    parser.add_argument("--rr-clean-hampel", type=int, default=11, metavar="N", dest="rr_clean_hampel_window", help="RR clean: Hampel window in beats (default 11)")
    parser.add_argument("--rr-clean-hampel-sigma", type=float, default=4.5, help="RR clean: Hampel MAD multiplier (default 4.5)")
    parser.add_argument("--disable-hampel", action="store_true", dest="rr_clean_disable_hampel", help="Disable Hampel filter (only use threshold filter)")
    parser.add_argument("--rr-clean-min-rr", type=float, default=250, metavar="MS", help="RR clean: min valid RR ms (default 250)")
    parser.add_argument("--rr-clean-max-rr", type=float, default=2200, metavar="MS", help="RR clean: max valid RR ms (default 2200)")
    parser.add_argument("--stats-interval", type=float, default=60.0, metavar="SEC", help="Print RR clean stats to stderr every SEC seconds when --rr-clean (0=off, default 60)")
    parser.add_argument("--debug-rr", action="store_true", help="Add last_rr_ms and filter_stats to every JSONL output line")
    parser.add_argument("--interp-max-fallback", type=float, default=0.65, metavar="R", help="Use last valid RMSSD when interp_ratio > R (default 0.65)")
    parser.add_argument("--noise-dynamic", action="store_true", default=True, help="Wider interpolation noise when interp_ratio high (default True)")
    parser.add_argument("--no-noise-dynamic", action="store_false", dest="noise_dynamic", help="Disable dynamic interpolation noise")
    parser.add_argument("--window-adaptive", action="store_true", default=True, help="Longer min window when interp_ratio high (default True)")
    parser.add_argument("--no-window-adaptive", action="store_false", dest="window_adaptive", help="Disable adaptive window")
    args = parser.parse_args()
    _run(
        window_sec=args.window,
        min_intervals=args.min_intervals,
        min_beats=args.min_beats,
        spike_filter_ms=args.spike_filter,
        smooth_output_n=args.smooth_output,
        window_short_sec=args.window_short,
        blend=args.blend,
        rr_clean=args.rr_clean,
        rr_clean_thresh=args.rr_clean_thresh,
        rr_clean_grace=args.rr_clean_grace,
        rr_clean_disable_interp_noise=args.rr_clean_disable_interp_noise,
        rr_clean_hampel_window=args.rr_clean_hampel_window,
        rr_clean_hampel_sigma=args.rr_clean_hampel_sigma,
        rr_clean_disable_hampel=args.rr_clean_disable_hampel,
        rr_clean_min_rr=args.rr_clean_min_rr,
        rr_clean_max_rr=args.rr_clean_max_rr,
        stats_interval_sec=args.stats_interval if args.rr_clean else 0.0,
        debug_rr=args.debug_rr,
        interp_max_fallback=args.interp_max_fallback,
        noise_dynamic=args.noise_dynamic,
        window_adaptive=args.window_adaptive,
    )


if __name__ == "__main__":
    main()
