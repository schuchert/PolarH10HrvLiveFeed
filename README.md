# PolarH10HrvLiveFeed — HRV Live Pipeline (Polar H10 → OBS)

Pipeline: **Polar H10 (BLE)** → RR stream → HRV calc → graph (browser) → OBS.

See [PLAN.md](PLAN.md) for the full design.

## Setup

**Requires Python 3.14.** The setup script ensures the correct version; if you create the venv by hand, use a 3.14 interpreter (e.g. `python3.14 -m venv .venv`).

**One command (macOS/Linux):** from repo root run `./scripts/setup.sh`. It finds or installs Python 3.14, creates `.venv`, and installs deps. Then run `source .venv/bin/activate`.

**Manual / Windows:**

1. **Install Python 3.14** (e.g. [python.org](https://www.python.org/downloads/), `pyenv install 3.14`, or Homebrew).
2. **Create the venv with that interpreter** (do not use plain `python3` unless it’s already 3.14):
   ```bash
   rm -rf .venv
   python3.14 -m venv .venv
   ```
   With **pyenv:** `pyenv install 3.14` then in this repo `python3 -m venv .venv` is enough (`.python-version` selects 3.14).
3. Activate and install: `source .venv/bin/activate`, then `pip install -r requirements-dev.txt`. Run `python scripts/check_python.py` to confirm version.

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

**Auto-reconnect:** If the BLE link drops (e.g. laptop sleep), the stream will wait 15s before the first reconnect (to give Bluetooth time to recover), then rescan and reconnect. Later reconnects use `--reconnect-delay` (default 5s). Use `--reconnect-delay 0` to disable, or `--max-reconnects N` to limit attempts.

**macOS**: Grant Bluetooth access when prompted. The first time you run, ensure no other app is connected to the H10 (e.g. Polar Beat disconnected). If you see **"Peer removed pairing information"** (common after the H10 was used with another device or pairing got out of sync), remove the H10 from **System Settings → Bluetooth** (Forget This Device), then run again so it can pair fresh.

**"No Polar H10 found"?** The script retries scanning several times (see `--scan-retries`). Between attempts it prints tips: strap on and moisten the electrodes, quit Polar Beat (or any app using the H10), and ensure Bluetooth is on. If you use a specific device name, try without `--device` once to see if any H10 appears.

## Step 2: HRV from RR stream (RMSSD + 0–100 score)

Pipe the stream into `hrv_calc` to get rolling RMSSD and an **Elite HRV–style 0–100 score** (ln(RMSSD) mapped to 0–100):

```bash
PYTHONPATH=. python -m src.polar_h10_stream | PYTHONPATH=. python -m src.hrv_calc
```

Options for `hrv_calc`:
- `--window 60` — rolling window in seconds (default 60).
- `--min-intervals 30` — minimum RR intervals before emitting (default 30).
- `--min-beats 15` — minimum beats in RMSSD window before compute (default 15); effective min = max(min-intervals, min-beats).
- `--smooth-output N` — sliding average of last N HRV scores (smooths the graph; spikes damp out). `0` = off (default).
- `--window-short SEC` and `--blend R` — two-window mode: also compute RMSSD on the last SEC seconds and emit `R*score_short + (1-R)*score_long` so the display favors recent values (e.g. `--window-short 20 --blend 0.6`). After movement, the short window recovers first so the blend drops faster.
- `--spike-filter MS` — optional RR spike smoothing (cap change to ±MS ms). `0` = off (default).
- **RR artifact cleaning** (motion spikes): `--rr-clean` enables threshold + Hampel filter before RMSSD. Options: `--rr-clean-thresh 0.35`, `--rr-clean-hampel 11`, `--rr-clean-hampel-sigma 4.0`, `--disable-hampel` (threshold only), `--rr-clean-min-rr 300`, `--rr-clean-max-rr 2000`. `--stats-interval 60` prints RR clean stats to stderr every 60s (default when rr-clean on; 0=off). Output JSON includes `rr_dropped` and `rr_interpolated` when enabled.

Output includes `rmssd_ms` and `hrv_score` (1–100), e.g.:

```json
{"hr": 76, "rmssd_ms": 42.5, "hrv_score": 58, "ts": 1734567890.5}
```

When there isn’t enough data yet (can take up to several minutes with default window/min-intervals), or when the stream sends `rr_ms: null`, you’ll get `"hrv_score": null` so downstream can show “N/A”.

## Step 3: Live graph (browser + OBS)

Run the full pipeline and open the graph in a browser or OBS:

```bash
./run.sh
```

Or run the pipeline manually (no caffeinate):

```bash
PYTHONPATH=. python -m src.polar_h10_stream | PYTHONPATH=. python -m src.hrv_calc | PYTHONPATH=. python -m src.graph_server --port 8765
```

**`run.sh`** keeps the laptop awake (`caffeinate -di`), runs the full pipeline, and exits cleanly on Ctrl+C.

Then open **http://localhost:8765** in a browser (do not start `graph_server` by itself — the run script already runs the full pipeline). You’ll see a live chart of HRV (0–100) and HR, plus current values. **It may take up to several minutes** before HRV data appears (the pipeline needs enough RR intervals in the rolling window); the page will show “Connected” and then “Live” once data is flowing.

**OBS:** Add a **Browser Source** with URL `http://localhost:8765`. For a transparent overlay, use `http://localhost:8765?transparent=1` and in OBS set the browser source to use a transparent background (e.g. in the source’s Custom CSS, or in OBS browser settings).

Options for `graph_server`:
- `--port 8765` — HTTP and WebSocket port (default 8765).
- `--host 127.0.0.1` — Bind address (default localhost).
