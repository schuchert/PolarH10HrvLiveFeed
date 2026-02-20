# Live HRV Pipeline for Polar H10 → OBS

Plan for: **Polar H10 (BLE)** → **live RR stream** → **HRV computation** → **live graph** in a window suitable for **OBS**.

---

## 1. Data flow (high level)

```
Polar H10 (BLE)
    → [BLE client] RR intervals (+ HR)
    → [HRV calculator] rolling RMSSD (and optional SDNN)
    → [Graph app] live chart
    → OBS: capture graph window (browser or app)
```

- **RR intervals**: time between heartbeats (ms). Polar H10 sends these over standard Bluetooth Heart Rate Profile when connected.
- **HRV**: Heart Rate Variability. For a “live” feel we’ll use a **rolling window** (e.g. 60–120 s) and output **RMSSD** (and optionally SDNN) every few seconds.

---

## 2. Polar H10 on macOS

- **Official Polar BLE SDK**: Android and iOS only — not for macOS.
- **Approach on Mac**: Use standard **GATT Heart Rate Service** (UUID `0x180D`), characteristic **Heart Rate Measurement** (`0x2A37`). The H10 supports this when used as a normal BLE heart rate sensor (connect to the device; it will send HR + RR in the same characteristic).
- **Desktop BLE stack** (pick one):
  - **Python + [bleak](https://github.com/hbldh/bleak)** – cross‑platform, works well on macOS.
  - **Node + [@abandonware/noble](https://github.com/abandonware/noble)** – Node BLE for macOS/Linux (noble is unmaintained but abandonware fork is used in practice).

Recommendation: **Python + bleak** for BLE and data handling; optional Node only if you prefer JS for the graph server.

---

## 3. Pipeline options

### Option A: Single process (simplest to run)

One app that:

1. Connects to Polar H10 (bleak), subscribes to HRM.
2. Parses HR and RR from the GATT payload (flag byte, then HR 1/2 bytes, then RR pairs as UINT16; RR resolution = 1/1024 s → multiply by 1000/1024 for ms).
3. Keeps a rolling buffer of RR intervals (e.g. last 2 minutes).
4. Every N seconds (e.g. 5 s), computes RMSSD (and optionally SDNN) over the window, then:
5. Serves a **small HTTP page** with a live graph and a **WebSocket** that pushes `{ rmssd_ms, sdnn_ms?, hr?, ts }` to the browser.

**OBS**: Add **Browser Source** → URL `http://localhost:PORT` (and optionally make background transparent in the page + OBS browser source settings).

### Option B: Unix-style pipeline (modular)

- **Tool 1 – BLE → stdout**  
  Connects to H10, parses HRM, prints JSON lines:  
  `{"hr":72,"rr_ms":850,"ts":1234567890.123}`  
  (one line per RR or per notification; you can normalize to one line per RR.)

- **Tool 2 – RR → HRV**  
  Reads stdin (JSON lines with `rr_ms`), maintains rolling window, prints JSON lines:  
  `{"rmssd_ms":42,"sdnn_ms":55,"window_s":60,"ts":...}`  

- **Tool 3 – Graph**  
  Reads stdin (or a local socket) and either:
  - Opens a **browser window** (or serves a page) that receives the stream via WebSocket (graph process also runs a small WS server that forwards stdin to clients), or
  - Runs a small **Electron/Tauri** window that draws the graph and can be captured by OBS.

Example shell:

```bash
polar-h10-stream --device "POLAR H10 12345678" | hrv-calc --window 60 | graph-server --port 8765
```

Then open `http://localhost:8765` and add that as Browser Source in OBS.

---

## 4. HRV computation (rolling window)

- **RMSSD** (Root Mean Square of Successive Differences):  
  - From RR series in ms: differences `d[i] = rr[i+1] - rr[i]`, then  
  - `RMSSD = sqrt(mean(d^2))` (in ms).  
  - Reflects short-term, beat-to-beat variability; good for “live” feedback.

- **SDNN** (optional): standard deviation of RR in the window (in ms).

- **Window**: 60 s is a common minimum; 2 minutes is more stable. For “live” streaming, 60–120 s with an update every 5–10 s is a reasonable default.

- **Edge cases**:  
  - Require minimum number of RR intervals (e.g. ≥ 30) before emitting RMSSD.  
  - Optional: simple artifact rejection (e.g. discard RR &lt; 300 ms or &gt; 2000 ms, or outliers beyond 2 SD).

---

## 5. Graph and OBS

- **Recommended**: **Browser-based graph**  
  - One local HTTP server (Python or Node) serves one HTML page.  
  - Page connects back via WebSocket and receives `{ rmssd_ms, hr?, ts }` (and optionally `sdnn_ms`).  
  - Draw with **Chart.js** (line/time chart) or **Lightweight Charts** or raw canvas.  
  - **OBS**: Add **Browser Source** → `http://localhost:PORT`. Set width/height; use transparent background in HTML/CSS if you want overlay.

- **Alternative**: **Standalone window** (e.g. Electron or Tauri) that opens a small, borderless, optionally transparent window. OBS **Window Capture** that window. More setup, same data flow.

- **Transparency**: For overlay in OBS, use a transparent background in the page (e.g. `background: transparent`, and in OBS browser source set “Custom CSS” to remove default background if needed).

---

## 6. Suggested tech stack (concrete)

| Layer           | Suggestion        | Alternative        |
|----------------|-------------------|--------------------|
| BLE (macOS)    | Python + bleak    | Node + @abandonware/noble |
| RR → HRV       | Python (numpy)    | Any (formula is small)   |
| Stream format  | JSON lines        | WebSocket from start     |
| Graph          | HTML + JS + WebSocket | Electron/Tauri      |
| Server         | Python (e.g. aiohttp or FastAPI + WebSocket) | Node (Express + ws) |

**Minimal single-process sketch**:

- **Python 3**  
  - `bleak`: connect to H10, subscribe to `0x2A37`, parse flag + HR + RR (RR in 1/1024 s → ms).  
  - Rolling buffer of RR (e.g. 2 min), compute RMSSD every 5 s.  
  - `aiohttp` or `FastAPI`: one route serves `index.html`, one WebSocket endpoint broadcasts latest HRV (and optionally HR) to all connected browsers.  
- **Frontend**  
  - Single HTML page; JS connects to `ws://localhost:PORT/ws`, appends `rmssd_ms` (and optionally HR) to a time series, updates Chart.js (or similar) for a live line graph.

---

## 7. Implementation order

1. **BLE client**  
   - List/select Polar H10, connect, subscribe to Heart Rate Measurement, parse HR + RR, print JSON lines to stdout (or hold in memory for next step).  
   - Verify RR values look sane (e.g. 600–1200 ms at rest).

2. **HRV module**  
   - Consume RR stream (or in-memory list), rolling window, compute RMSSD (and optionally SDNN), output at fixed interval.

3. **Single server**  
   - Combine BLE + HRV in one process; expose HTTP (graph page) + WebSocket (push HRV/HR).  
   - Test in browser without OBS.

4. **Graph UI**  
   - Time series of RMSSD (and optionally HR), transparent background, optional fullscreen or fixed size for OBS.

5. **OBS**  
   - Browser Source → `http://localhost:PORT`, resize, position, transparency as needed.

(If you later want Option B, split step 1 into a `polar-h10-stream` CLI and step 2 into `hrv-calc`; keep the same graph/server idea.)

---

## 8. GATT parsing reference (HRM 0x2A37)

- **Byte 0 – Flags**  
  - Bit 0: HR format (0 = UINT8, 1 = UINT16).  
  - Bit 4: RR present (1 = yes).  
  - Bit 3: Energy Expended present.
- **HR**: 1 byte if UINT8, 2 bytes (little-endian) if UINT16.
- **Energy Expended** (if present): 2 bytes, little-endian.
- **RR intervals**: consecutive 2-byte UINT16, little-endian; unit = 1/1024 s → `rr_ms = (value * 1000) / 1024`.

---

## 9. Files / repo layout (suggestion)

```
HRV/
  README.md           # How to run, deps, OBS setup
  PLAN.md             # This file
  requirements.txt    # bleak, aiohttp (or fastapi+uvicorn), numpy
  src/
    ble_hr.py         # Polar H10 connect + HRM subscribe + RR parse
    hrv.py            # Rolling window, RMSSD (and SDNN)
    server.py         # HTTP + WebSocket server, ties BLE + HRV
  static/
    index.html        # Graph page + WebSocket client
```

You can keep everything in a single `server.py` and one `index.html` at first, then split into `ble_hr`, `hrv`, and `server` as above.

---

## 10. Summary

- **Polar H10 on Mac**: Use standard GATT Heart Rate (0x180D / 0x2A37) via **Python + bleak** (or Node + noble).
- **Live HRV**: Rolling window of RR (e.g. 60–120 s), output **RMSSD** (and optionally SDNN) every few seconds.
- **OBS**: **Browser Source** to a local page that shows a live graph fed by WebSocket; transparent background if you want overlay.
- **Pipeline**: Either one process (BLE + HRV + HTTP/WS + graph) or three tools (stream → HRV → graph server) with the same graph and OBS setup.

If you tell me your preference (single process vs. three separate tools, and Python vs. Node for the server), the next step is to sketch the actual BLE connection and HRM parsing code, then the HRV math, then the server and one minimal graph page.
