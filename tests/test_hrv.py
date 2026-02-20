"""Tests for HRV metrics: RMSSD and Elite HRV-style 0-100 score."""

import math
import pytest
from src.hrv import rmssd_ms, hrv_score


def test_rmssd_two_intervals():
    """RMSSD with two RR values: single difference."""
    # rr_ms [800, 840] -> diff 40 -> RMSSD = 40
    assert rmssd_ms([800.0, 840.0]) == pytest.approx(40.0, rel=0.01)


def test_rmssd_three_intervals():
    """RMSSD = sqrt(mean(d^2)). Diffs 40, -20. mean(d^2)=1000, sqrt=31.62."""
    assert rmssd_ms([800.0, 840.0, 820.0]) == pytest.approx(31.62, rel=0.01)


def test_rmssd_constant():
    """Constant RR -> zero differences -> RMSSD = 0."""
    assert rmssd_ms([800.0, 800.0, 800.0]) == pytest.approx(0.0, rel=0.01)


def test_rmssd_requires_at_least_two():
    """Need at least 2 intervals for one difference."""
    with pytest.raises(ValueError):
        rmssd_ms([])
    with pytest.raises(ValueError):
        rmssd_ms([800.0])


def test_hrv_score_mid_range():
    """ln(RMSSD) ~4 (RMSSD ~54.6) -> score ~61.5 if scale 0-6.5 -> 100."""
    # ln(55) ≈ 4.0 -> 4/6.5*100 ≈ 61.5
    assert hrv_score(55.0) == pytest.approx(61.5, rel=1.0)


def test_hrv_score_high_rmssd():
    """High RMSSD -> high score, capped at 100."""
    # ln(500) ≈ 6.2 -> 6.2/6.5*100 ≈ 95
    assert hrv_score(500.0) <= 100
    assert hrv_score(500.0) >= 90


def test_hrv_score_low_rmssd():
    """Low RMSSD -> low score, floored at 0."""
    # ln(5) ≈ 1.6 -> 1.6/6.5*100 ≈ 25
    assert hrv_score(5.0) >= 0
    assert hrv_score(1.0) >= 0


def test_hrv_score_clamped_to_0_100():
    """Score is always in [0, 100]."""
    assert 0 <= hrv_score(1.0) <= 100
    assert 0 <= hrv_score(10.0) <= 100
    assert 0 <= hrv_score(100.0) <= 100
    assert 0 <= hrv_score(200.0) <= 100
