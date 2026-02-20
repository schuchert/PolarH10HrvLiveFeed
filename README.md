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

You should see `# connected` then JSON lines on stdout, e.g.:

```json
{"hr": 72, "rr_ms": 850.5, "ts": 1734567890.123}
```

Press Ctrl+C to stop.

**macOS**: Grant Bluetooth access when prompted. The first time you run, ensure no other app is connected to the H10 (e.g. Polar Beat disconnected).

## Step 2: HRV from RR stream (RMSSD + 0–100 score)

Pipe the stream into `hrv_calc` to get rolling RMSSD and an **Elite HRV–style 0–100 score** (ln(RMSSD) mapped to 0–100):

```bash
PYTHONPATH=. python -m src.polar_h10_stream | PYTHONPATH=. python -m src.hrv_calc
```

Options for `hrv_calc`:
- `--window 60` — rolling window in seconds (default 60).
- `--min-intervals 30` — minimum RR intervals before emitting (default 30).

Output includes `rmssd_ms` and `hrv_score` (1–100), e.g.:

```json
{"hr": 76, "rmssd_ms": 42.5, "hrv_score": 58, "ts": 1734567890.5}
```

When there isn’t enough data yet, or when the stream sends `rr_ms: null`, you’ll get `"hrv_score": null` so downstream can show “N/A”.

## Step 3: Live graph (browser + OBS)

Run the full pipeline and open the graph in a browser or OBS:

```bash
PYTHONPATH=. python -m src.polar_h10_stream | PYTHONPATH=. python -m src.hrv_calc | PYTHONPATH=. python -m src.graph_server --port 8765
```

Then open **http://localhost:8765** in a browser. You’ll see a live chart of HRV (0–100) and HR, plus current values.

**OBS:** Add a **Browser Source** with URL `http://localhost:8765`. For a transparent overlay, use `http://localhost:8765?transparent=1` and in OBS set the browser source to use a transparent background (e.g. in the source’s Custom CSS, or in OBS browser settings).

Options for `graph_server`:
- `--port 8765` — HTTP and WebSocket port (default 8765).
- `--host 127.0.0.1` — Bind address (default localhost).
