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
from collections import deque
from datetime import datetime

from src.hrv import rmssd_ms, hrv_score, smooth_spikes
from src.rr_artifact_filter import RrArtifactFilter


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def _run(
    window_sec: float,
    min_intervals: int,
    spike_filter_ms: float,
    smooth_output_n: int,
    window_short_sec: float,
    blend: float,
    rr_clean: bool,
    rr_clean_thresh: float,
    rr_clean_hampel_window: int,
    rr_clean_hampel_sigma: float,
    rr_clean_min_rr: float,
    rr_clean_max_rr: float,
):
    # Rolling buffer: (rr_ms, ts) kept for the last window_sec
    buffer: deque[tuple[float, float]] = deque()
    latest_ts: float | None = None
    first_hrv_emitted = False
    # Sliding average of last N scores (when smooth_output_n > 0)
    score_history: deque[float] = deque(maxlen=smooth_output_n) if smooth_output_n > 0 else deque()
    rr_filter: RrArtifactFilter | None = None
    if rr_clean:
        _log = logging.getLogger("src.hrv_calc")
        rr_filter = RrArtifactFilter(
            thresh_percent=rr_clean_thresh,
            min_rr_ms=rr_clean_min_rr,
            max_rr_ms=rr_clean_max_rr,
            hampel_window=rr_clean_hampel_window,
            hampel_sigma=rr_clean_hampel_sigma,
            logger_instance=_log,
        )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            print(line, flush=True)
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        hr = obj.get("hr")
        rr_raw = obj.get("rr_ms")
        ts = obj.get("ts")
        if rr_raw is None:
            # No RR in this sample — emit N/A so downstream can show "HRV: N/A"
            out = {"hr": hr, "rmssd_ms": None, "hrv_score": None, "ts": ts}
            print(json.dumps(out), flush=True)
            continue
        try:
            rr_val = float(rr_raw)
        except (TypeError, ValueError):
            continue
        if rr_filter is not None:
            rr_val = rr_filter.process_rr(rr_val)
            if rr_val is None:
                continue
        else:
            if rr_val < 300 or rr_val > 2000:
                continue
        if ts is not None:
            latest_ts = ts if latest_ts is None else max(latest_ts, ts)
        buffer.append((rr_val, latest_ts if latest_ts is not None else 0.0))
        # Drop entries older than window_sec
        if latest_ts is not None:
            cutoff = latest_ts - window_sec
            while buffer and buffer[0][1] < cutoff:
                buffer.popleft()
        else:
            # No timestamps: keep by count
            max_len = int(window_sec * 1.5)
            while len(buffer) > max_len:
                buffer.popleft()
        rr_list = [b[0] for b in buffer]
        if spike_filter_ms > 0:
            rr_list = smooth_spikes(rr_list, spike_filter_ms)
        if len(rr_list) < min_intervals:
            # Not enough for RMSSD yet — emit HR so graph shows heart rate immediately
            out = {"hr": hr, "rmssd_ms": None, "hrv_score": None, "ts": ts}
            print(json.dumps(out), flush=True)
            continue
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
        except ValueError:
            continue
        if not first_hrv_emitted:
            print(_ts() + "First HRV score emitted.", file=sys.stderr)
            first_hrv_emitted = True
        out = {
            "hr": hr,
            "rmssd_ms": round(rms_emit, 2),
            "hrv_score": int(round(score)),
            "ts": ts,
        }
        if rr_filter is not None:
            s = rr_filter.stats()
            out["rr_dropped"] = s["dropped"]
            out["rr_interpolated"] = s["interpolated"]
        print(json.dumps(out), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Compute rolling RMSSD and HRV score (0-100) from RR stream")
    parser.add_argument("--window", "-w", type=float, default=60.0, help="Rolling window in seconds (default 60)")
    parser.add_argument("--min-intervals", "-n", type=int, default=30, help="Min RR intervals before emitting (default 30)")
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
    parser.add_argument("--rr-clean-thresh", type=float, default=0.25, metavar="P", help="RR clean: threshold %% deviation from local median (default 0.25)")
    parser.add_argument("--rr-clean-hampel", type=int, default=11, metavar="N", dest="rr_clean_hampel_window", help="RR clean: Hampel window in beats (default 11)")
    parser.add_argument("--rr-clean-hampel-sigma", type=float, default=3.0, help="RR clean: Hampel MAD multiplier (default 3.0)")
    parser.add_argument("--rr-clean-min-rr", type=float, default=300, metavar="MS", help="RR clean: min valid RR ms (default 300)")
    parser.add_argument("--rr-clean-max-rr", type=float, default=2000, metavar="MS", help="RR clean: max valid RR ms (default 2000)")
    args = parser.parse_args()
    _run(
        window_sec=args.window,
        min_intervals=args.min_intervals,
        spike_filter_ms=args.spike_filter,
        smooth_output_n=args.smooth_output,
        window_short_sec=args.window_short,
        blend=args.blend,
        rr_clean=args.rr_clean,
        rr_clean_thresh=args.rr_clean_thresh,
        rr_clean_hampel_window=args.rr_clean_hampel_window,
        rr_clean_hampel_sigma=args.rr_clean_hampel_sigma,
        rr_clean_min_rr=args.rr_clean_min_rr,
        rr_clean_max_rr=args.rr_clean_max_rr,
    )


if __name__ == "__main__":
    main()
