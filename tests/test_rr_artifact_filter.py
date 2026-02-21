"""TDD tests for RrArtifactFilter: extreme drop, threshold spike, Hampel, normal heart."""

import math
import pytest

from src.rr_artifact_filter import RrArtifactFilter


def test_filter_extremes():
    """RR outside min/max is dropped (return None)."""
    f = RrArtifactFilter(min_rr_ms=300, max_rr_ms=2000)
    assert f.process_rr(100.0) is None
    assert f.process_rr(3000.0) is None
    assert f.stats()["dropped"] == 2
    # Boundary: 300 and 2000 pass
    assert f.process_rr(300.0) == 300.0
    assert f.process_rr(2000.0) == 2000.0


def test_filter_extremes_nan_invalid():
    """NaN and non-numeric return None."""
    f = RrArtifactFilter(min_rr_ms=300, max_rr_ms=2000)
    assert f.process_rr(float("nan")) is None
    # Non-float returns None per spec
    assert f.process_rr("800") is None  # type: ignore[arg-type]


def test_threshold_spike():
    """After grace, a spike (1600) is replaced by ~mean with optional noise (~800)."""
    f = RrArtifactFilter(thresh_percent=0.35, min_rr_ms=300, max_rr_ms=2000, grace_beats=10)
    for _ in range(10):
        assert f.process_rr(800.0) == 800.0
    # Spike: 1600 vs mean 800, std_eff>=5 -> delta 800 > thresh -> interpolate
    out = f.process_rr(1600.0)
    assert out is not None
    assert 700 <= out <= 950  # mean 800 ± noise
    assert f.stats()["interpolated"] >= 1


def test_hampel_multi_spikes():
    """Noisy series with a spike gets cleaned by Hampel (after grace + threshold warmup)."""
    f = RrArtifactFilter(
        thresh_percent=0.5,
        hampel_window=5,
        hampel_sigma=2.0,
        grace_beats=10,
    )
    for _ in range(10):
        f.process_rr(800.0)
    out = f.process_rr(400.0)
    assert out is not None
    assert out >= 500
    assert f.stats()["interpolated"] >= 1


def test_normal_heart():
    """Sinus rhythm 60–100 bpm: no drops, and at most modest cleaning (typical <10%)."""
    f = RrArtifactFilter(thresh_percent=0.40, hampel_sigma=4.5, grace_beats=30)
    # Simulate ~70 bpm with small variability: 850–860 ms
    for i in range(100):
        rr = 855.0 + 5 * math.sin(i * 0.1)  # 850–860
        out = f.process_rr(rr)
        assert out is not None, f"Normal RR {rr}ms should not be dropped"
    total = f.stats()["dropped"] + f.stats()["interpolated"]
    assert total <= 15, f"Expected ≤15 cleaned for 100 normal beats (loose defaults), got {total}"


def test_buffer_warmup_passthrough():
    """First (grace_beats - 1) RRs pass through; grace_beats=10 so first 9 pass."""
    f = RrArtifactFilter(thresh_percent=0.40, grace_beats=10)
    for i in range(9):
        rr = 800.0 + i
        assert f.process_rr(rr) == rr
    # 10th is first that could be filtered
    assert f.process_rr(800.0) == 800.0


def test_stats():
    """Stats return dropped and interpolated counts."""
    f = RrArtifactFilter(min_rr_ms=300, max_rr_ms=2000)
    f.process_rr(100.0)
    f.process_rr(800.0)
    s = f.stats()
    assert s["dropped"] == 1
    assert s["interpolated"] == 0


def test_sample_data_shaking_spike_smoothed():
    """Simulated Shaking segment: a spike in RR (e.g. 400 ms) gets cleaned so RMSSD is stable."""
    from src.hrv import rmssd_ms

    rr_raw = [1000.0] * 15 + [400.0] + [1000.0] * 14
    f = RrArtifactFilter(thresh_percent=0.40, hampel_sigma=4.5, grace_beats=10)
    rr_cleaned = []
    for r in rr_raw:
        c = f.process_rr(r)
        if c is not None:
            rr_cleaned.append(c)
    assert len(rr_cleaned) >= 29
    rms_raw = rmssd_ms(rr_raw)
    rms_clean = rmssd_ms(rr_cleaned)
    assert rms_clean < rms_raw
    assert f.stats()["interpolated"] >= 1


def test_synthetic_50_plus_spike_1500_survives_loose_params():
    """[750..850]*50 + spike 1500: with loose params RMSSD survives (non-zero), spike cleaned."""
    from src.hrv import rmssd_ms

    base = [750.0 + (i % 101) for i in range(50)]
    rr_raw = base + [1500.0]
    f = RrArtifactFilter(thresh_percent=0.40, hampel_sigma=4.5, buffer_maxlen=30, grace_beats=10)
    rr_cleaned = []
    for r in rr_raw:
        c = f.process_rr(r)
        if c is not None:
            rr_cleaned.append(c)
    assert len(rr_cleaned) >= 50
    rms = rmssd_ms(rr_cleaned)
    assert rms > 0, "RMSSD should not collapse to 0 with loose params"
    assert f.stats()["interpolated"] >= 1, "Spike 1500 should be cleaned"
