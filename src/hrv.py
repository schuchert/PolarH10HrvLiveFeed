"""
HRV metrics from RR intervals: RMSSD and Elite HRV-style 0-100 score.

Elite HRV: ln(RMSSD) mapped to 0-100 (ln typically 0..6.5). We use the same
idea with a linear map; exact calibration is proprietary.
"""

import math


def rmssd_ms(rr_ms: list[float]) -> float:
    """
    Root Mean Square of Successive Differences (in ms).
    Requires at least 2 RR intervals.
    """
    if len(rr_ms) < 2:
        raise ValueError("Need at least 2 RR intervals for RMSSD")
    diffs = [rr_ms[i + 1] - rr_ms[i] for i in range(len(rr_ms) - 1)]
    mean_sq = sum(d * d for d in diffs) / len(diffs)
    return math.sqrt(mean_sq)


# ln(RMSSD) typically 0..6.5 per Elite HRV; we map linearly to 0-100
LN_RMSSD_MAX = 6.5


def hrv_score(rmssd_ms_val: float) -> int:
    """
    Elite HRV-style 0-100 score from RMSSD (ms).
    Uses ln(RMSSD) mapped to 0-100; clamped to [0, 100].
    """
    if rmssd_ms_val <= 0:
        return 0
    ln_rmssd = math.log(max(rmssd_ms_val, 1.0))
    # Linear map 0..LN_RMSSD_MAX -> 0..100
    raw = (ln_rmssd / LN_RMSSD_MAX) * 100.0
    return int(round(max(0, min(100, raw))))
