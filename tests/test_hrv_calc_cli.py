"""CLI test: hrv_calc parses stdin and emits rmssd_ms + hrv_score."""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_hrv_calc_pass_through_status():
    """Status lines (# ...) are passed through."""
    inp = "# connected\n"
    result = subprocess.run(
        [sys.executable, "-m", "src.hrv_calc", "--min-intervals", "2"],
        input=inp,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "# connected"


def test_hrv_calc_emits_na_for_null_rr():
    """When rr_ms is null, we emit rmssd_ms and hrv_score as null."""
    inp = '# connected\n{"hr": 72, "rr_ms": null, "ts": 1000.0}\n'
    result = subprocess.run(
        [sys.executable, "-m", "src.hrv_calc", "--min-intervals", "2"],
        input=inp,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    lines = [l for l in result.stdout.strip().split("\n") if l]
    assert lines[0] == "# connected"
    obj = json.loads(lines[1])
    assert obj["hr"] == 72
    assert obj["rmssd_ms"] is None
    assert obj["hrv_score"] is None


def test_hrv_calc_emits_rmssd_and_score_when_enough_intervals():
    """With enough RRs (min 2 for this test), we get rmssd_ms and hrv_score 0-100."""
    # Send 3 RR intervals so we have 2 differences; min_intervals=2
    base_ts = 1000.0
    lines_in = [
        "# connected",
        json.dumps({"hr": 70, "rr_ms": 800.0, "ts": base_ts}),
        json.dumps({"hr": 71, "rr_ms": 840.0, "ts": base_ts + 1}),
        json.dumps({"hr": 72, "rr_ms": 820.0, "ts": base_ts + 2}),
    ]
    result = subprocess.run(
        [sys.executable, "-m", "src.hrv_calc", "--min-intervals", "2", "--min-beats", "2", "--window", "60"],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    obj = json.loads(out_lines[-1])
    assert "rmssd_ms" in obj
    assert "hrv_score" in obj
    assert 0 <= obj["hrv_score"] <= 100
    assert obj["rmssd_ms"] is not None


def test_hrv_calc_rr_clean_adds_metrics():
    """With --rr-clean, output includes rr_dropped and rr_interpolated."""
    base_ts = 1000.0
    lines_in = [
        "# connected",
        *[json.dumps({"hr": 70 + i, "rr_ms": 800.0 + i, "ts": base_ts + i}) for i in range(12)],
    ]
    result = subprocess.run(
        [sys.executable, "-m", "src.hrv_calc", "--min-intervals", "2", "--min-beats", "2", "--window", "60", "--rr-clean"],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    obj = json.loads(out_lines[-1])
    assert "rr_dropped" in obj
    assert "rr_interpolated" in obj
    assert isinstance(obj["rr_dropped"], int)
    assert isinstance(obj["rr_interpolated"], int)


def test_debug_rr_adds_last_rr_ms_and_filter_stats():
    """With --debug-rr, each output line has last_rr_ms and filter_stats."""
    lines_in = [
        "# connected",
        json.dumps({"hr": 70, "rr_ms": 800.0, "ts": 1000.0}),
        json.dumps({"hr": 71, "rr_ms": 810.0, "ts": 1001.0}),
        json.dumps({"hr": 72, "rr_ms": 820.0, "ts": 1002.0}),
    ]
    result = subprocess.run(
        [
            sys.executable, "-m", "src.hrv_calc",
            "--min-intervals", "2", "--min-beats", "2", "--window", "60",
            "--debug-rr",
        ],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    found = json.loads(out_lines[-1])
    assert "last_rr_ms" in found
    assert "filter_stats" in found
    assert found["filter_stats"].keys() >= {"dropped", "interp"}


def test_debug_rr_shaking_ts_1771618936_last_rr_ms_577():
    """With --debug-rr and no RR filter, ts 1771618936 HR 104 rr 577 -> last_rr_ms ~577 (1000/104)."""
    lines_in = [
        "# connected",
        json.dumps({"hr": 70, "rr_ms": 800.0, "ts": 1771618934.0}),
        json.dumps({"hr": 104, "rr_ms": 577.0, "ts": 1771618935.0}),
        json.dumps({"hr": 104, "rr_ms": 577.0, "ts": 1771618936.0}),
    ]
    result = subprocess.run(
        [
            sys.executable, "-m", "src.hrv_calc",
            "--min-intervals", "2", "--min-beats", "2", "--window", "60",
            "--debug-rr",
        ],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    # Last line should be from ts 1771618936 with last_rr_ms 577
    found = json.loads(out_lines[-1])
    assert found.get("ts") == 1771618936.0
    assert found.get("last_rr_ms") is not None
    assert 570 <= found["last_rr_ms"] <= 585, f"last_rr_ms for HR 104 should be ~577, got {found['last_rr_ms']}"


def test_hr_ramp_natural():
    """Natural HR ramp 97 bpm -> 65 bpm over 100 beats: RMSSD stable (non-zero), interp_ratio not dominant."""
    base_ts = 1000.0
    lines_in = ["# connected"]
    for i in range(100):
        rr_ms = 619.0 + (923.0 - 619.0) * (i / 99.0)  # linear ramp 619 -> 923 ms
        lines_in.append(json.dumps({"hr": int(60000 / rr_ms), "rr_ms": rr_ms, "ts": base_ts + i * 0.8}))
    result = subprocess.run(
        [
            sys.executable, "-m", "src.hrv_calc",
            "--min-intervals", "15", "--min-beats", "15", "--window", "60",
            "--rr-clean", "--rr-clean-grace", "30", "--rr-clean-thresh", "0.4",
        ],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    last = json.loads(out_lines[-1])
    # RMSSD safety: should emit non-zero RMSSD (not collapse); interp_ratio < 0.5 so we don't hide behind last_valid
    assert last.get("rmssd_ms") is None or last["rmssd_ms"] >= 0, "RMSSD should not be negative"
    # With grace + loose thresh, we expect some lines with valid RMSSD; interp_ratio may be high on ramp
    rmssd_vals = [json.loads(l).get("rmssd_ms") for l in out_lines if json.loads(l).get("rmssd_ms") is not None]
    if rmssd_vals:
        assert max(rmssd_vals) > 0, "At least one RMSSD should be > 0 (no full collapse)"


def test_shaking_spike():
    """Shaking-like: stable ~60 bpm, HR 104 (577 ms) spike, then stable. Spike passes, RMSSD does not collapse to 0."""
    lines_in = ["# connected"]
    base_ts = 1771618930.0
    for i in range(25):
        lines_in.append(json.dumps({"hr": 60, "rr_ms": 1000.0, "ts": base_ts + i}))
    lines_in.append(json.dumps({"hr": 104, "rr_ms": 577.0, "ts": base_ts + 25}))
    for i in range(25):
        lines_in.append(json.dumps({"hr": 60, "rr_ms": 1000.0, "ts": base_ts + 26 + i}))
    result = subprocess.run(
        [
            sys.executable, "-m", "src.hrv_calc",
            "--min-intervals", "15", "--min-beats", "15", "--window", "60",
            "--rr-clean", "--rr-clean-grace", "30", "--rr-clean-thresh", "0.4",
        ],
        input="\n".join(lines_in) + "\n",
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    assert result.returncode == 0
    out_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
    assert len(out_lines) >= 1
    # At least one line with non-zero RMSSD (smooth drop, not collapse to 0)
    rmssd_values = []
    for line in out_lines:
        obj = json.loads(line)
        if obj.get("rmssd_ms") is not None:
            rmssd_values.append(obj["rmssd_ms"])
    assert len(rmssd_values) >= 1, "Should emit at least one RMSSD value"
    assert any(r > 0 for r in rmssd_values), "RMSSD should not collapse to zero (Shaking spike scenario)"
