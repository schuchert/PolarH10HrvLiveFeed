"""Tests for HRV metrics: RMSSD and Elite HRV-style 0-100 score."""

import math
import pytest
from src.hrv import filter_spikes, rmssd_ms, hrv_score, smooth_spikes


def test_filter_spikes_off():
    """Spike filter 0 or negative returns copy."""
    rr = [800.0, 840.0, 820.0]
    assert filter_spikes(rr, 0) == rr
    assert filter_spikes(rr, -1) == rr


def test_filter_spikes_keeps_first():
    """First interval is always kept."""
    assert filter_spikes([900.0], 200) == [900.0]
    assert filter_spikes([900.0, 500.0], 200) == [900.0]  # 500 differs >200 from 900


def test_filter_spikes_removes_large_jump():
    """Intervals that differ from previous kept by >threshold are dropped."""
    # 800 -> 840 (40) ok; 840 -> 600 (240) drop 600 if threshold 200; 600 -> 620 (20) would need previous kept = 840, so 620 differs from 840 by 220, drop
    # So with threshold 200: keep 800, 840; drop 600; keep 620? No - we compare to *last kept*, so after 840 we drop 600 (diff 240), then 620 vs 840 is 220 > 200 so drop. So we get [800, 840].
    assert filter_spikes([800.0, 840.0, 600.0, 620.0], 200) == [800.0, 840.0]
    # 800, 840, 850: all within 200 of previous kept -> [800, 840, 850]
    assert filter_spikes([800.0, 840.0, 850.0], 200) == [800.0, 840.0, 850.0]


def test_smooth_spikes_caps_change():
    """Smooth keeps same length; caps change to ±max_change_ms."""
    # 800, 840 (40 ok), 600 (240 over 200 -> cap to 840-200=640), 620 (from 640, -20 ok) -> [800, 840, 640, 620]
    assert smooth_spikes([800.0, 840.0, 600.0, 620.0], 200) == [800.0, 840.0, 640.0, 620.0]
    assert smooth_spikes([800.0, 840.0, 850.0], 200) == [800.0, 840.0, 850.0]
    assert smooth_spikes([100.0], 200) == [100.0]
    assert smooth_spikes([100.0, 500.0], 200) == [100.0, 300.0]  # cap +200


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
