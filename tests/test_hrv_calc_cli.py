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
        [sys.executable, "-m", "src.hrv_calc", "--min-intervals", "2", "--window", "60"],
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
