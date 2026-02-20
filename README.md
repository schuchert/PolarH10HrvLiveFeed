# HRV Live Pipeline (Polar H10 → OBS)

Pipeline: **Polar H10 (BLE)** → RR stream → HRV calc → graph (browser) → OBS.

See [PLAN.md](PLAN.md) for the full design.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
```

## Run tests

```bash
PYTHONPATH=. python -m pytest tests/ -v
```

## Step 1: Stream RR from Polar H10

With the Polar H10 on and in range:

```bash
PYTHONPATH=. python -m src.polar_h10_stream
```

Or specify the device name (e.g. from System Settings → Bluetooth):

```bash
PYTHONPATH=. python -m src.polar_h10_stream --device "POLAR H10 0A3BA92B"
```

You should see JSON lines on stdout, one per heartbeat, e.g.:

```json
{"hr": 72, "rr_ms": 850.5, "ts": 1734567890.123}
```

Press Ctrl+C to stop.

**macOS**: Grant Bluetooth access when prompted. The first time you run, ensure no other app is connected to the H10 (e.g. Polar Beat disconnected).

## Next steps (pipeline)

- **Step 2**: `hrv-calc` — read JSON lines from stdin, rolling RMSSD, print HRV JSON lines.
- **Step 3**: `graph-server` — read HRV stream, serve browser page + WebSocket for OBS.
