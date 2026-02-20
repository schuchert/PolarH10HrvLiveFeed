#!/usr/bin/env python3
"""
Read JSON lines (hr, rr_ms, ts) from stdin; maintain rolling window of RR intervals;
output JSON lines with rmssd_ms, hrv_score (0-100), and optional hr/ts.
Pass-through lines starting with "# " unchanged.
"""

import argparse
import json
import sys
from collections import deque
from datetime import datetime

from src.hrv import rmssd_ms, hrv_score


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def _run(
    window_sec: float,
    min_intervals: int,
):
    # Rolling buffer: (rr_ms, ts) kept for the last window_sec
    buffer: deque[tuple[float, float]] = deque()
    latest_ts: float | None = None
    first_hrv_emitted = False

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
        # Optional artifact rejection: plausible RR range
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
        if len(rr_list) < min_intervals:
            # Not enough for RMSSD yet — emit HR so graph shows heart rate immediately
            out = {"hr": hr, "rmssd_ms": None, "hrv_score": None, "ts": ts}
            print(json.dumps(out), flush=True)
            continue
        try:
            rms = rmssd_ms(rr_list)
            score = hrv_score(rms)
        except ValueError:
            continue
        if not first_hrv_emitted:
            print(_ts() + "First HRV score emitted.", file=sys.stderr)
            first_hrv_emitted = True
        out = {
            "hr": hr,
            "rmssd_ms": round(rms, 2),
            "hrv_score": score,
            "ts": ts,
        }
        print(json.dumps(out), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Compute rolling RMSSD and HRV score (0-100) from RR stream")
    parser.add_argument("--window", "-w", type=float, default=60.0, help="Rolling window in seconds (default 60)")
    parser.add_argument("--min-intervals", "-n", type=int, default=30, help="Min RR intervals before emitting (default 30)")
    args = parser.parse_args()
    _run(window_sec=args.window, min_intervals=args.min_intervals)


if __name__ == "__main__":
    main()
