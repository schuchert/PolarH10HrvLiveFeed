#!/usr/bin/env python3
"""
Read HRV JSON lines from stdin; serve a live graph page and push data via WebSocket.

Usage (as final stage of pipeline):
  polar_h10_stream | hrv_calc | python -m src.graph_server --port 8765

Then open http://localhost:8765 in a browser or OBS Browser Source.
"""

import argparse
import asyncio
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    print("Install aiohttp: pip install aiohttp", file=sys.stderr)
    sys.exit(1)


# Directory containing static files (index.html)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# Directory for session data files (separate from log)
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
# Max data points to keep and send to new clients
MAX_HISTORY = 300


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def stdin_reader(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, received_count: list):
    """Run in thread: read lines from stdin, put into queue. received_count[0] = total lines."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue  # skip status lines (e.g. "# connected HH:MM:SS") for broadcast
        received_count[0] += 1
        asyncio.run_coroutine_threadsafe(queue.put(line), loop)


async def handle_index(request: web.Request) -> web.Response:
    """Serve the graph page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="static/index.html not found", status=404)
    body = index_path.read_text()
    return web.Response(content_type="text/html", text=body)


async def handle_status(request: web.Request) -> web.Response:
    """Return whether the server is receiving pipeline data (for debugging)."""
    n = request.app["stdin_count"][0]
    return web.json_response({"stdin_lines": n, "receiving": n > 0})


async def handle_ws(request: web.Request) -> web.StreamResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["websockets"].add(ws)
    # Send recent history so new clients see something immediately
    for line in request.app["history"]:
        try:
            await ws.send_str(line)
        except Exception:
            break
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                obj = json.loads(msg.data)
            except (json.JSONDecodeError, TypeError):
                continue
            if obj.get("event") != "region":
                continue
            region = obj.get("region")
            ts = obj.get("ts")
            if not region:
                continue
            request.app["current_region"] = region
            # Append region marker to session data file
            async with request.app["data_file_lock"]:
                f = request.app.get("data_file")
                if f is not None and not f.closed:
                    line = json.dumps({"event": "region", "region": region, "ts": ts}) + "\n"
                    f.write(line)
                    f.flush()
    finally:
        request.app["websockets"].discard(ws)
    return ws


def main():
    parser = argparse.ArgumentParser(description="HRV graph server: stdin → WebSocket + HTTP")
    parser.add_argument("--port", "-p", type=int, default=8765, help="Port (default 8765)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    args = parser.parse_args()

    app = web.Application()
    app["websockets"] = set()
    app["history"] = []  # list of JSON strings
    app["queue"] = asyncio.Queue()
    app["stdin_count"] = [0]  # mutable so stdin_reader can increment
    app["current_region"] = None
    app["data_file"] = None
    app["data_file_path"] = None
    app["data_file_lock"] = asyncio.Lock()

    app.router.add_get("/", handle_index)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/ws", handle_ws)

    async def consume_queue():
        queue = app["queue"]
        history = app["history"]
        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            n = app["stdin_count"][0]
            if n == 1:
                print(_ts() + "Receiving data from pipeline (first line).", file=sys.stderr)
            elif n % 50 == 0 and n > 0:
                print(_ts() + f"Received {n} lines from pipeline.", file=sys.stderr)
            history.append(line)
            if len(history) > MAX_HISTORY:
                history.pop(0)
            # Append to session data file (ts, hr, rmssd_ms, hrv_score, region)
            try:
                obj = json.loads(line)
                obj["region"] = app["current_region"]
                out_line = json.dumps(obj) + "\n"
            except (json.JSONDecodeError, TypeError):
                out_line = None
            if out_line is not None:
                async with app["data_file_lock"]:
                    if app["data_file"] is None:
                        LOGS_DIR.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        path = LOGS_DIR / f"data_{stamp}.jsonl"
                        app["data_file"] = open(path, "w", encoding="utf-8")
                        app["data_file_path"] = str(path)
                        print(_ts() + f"Session data file: {path}", file=sys.stderr)
                    app["data_file"].write(out_line)
                    app["data_file"].flush()
            dead = set()
            for ws in app["websockets"]:
                try:
                    await ws.send_str(line)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                app["websockets"].discard(ws)

    async def warn_if_no_data(app):
        """After 90s, if we still have zero stdin lines, print a warning. (hrv_calc needs ~30 RR intervals before it emits, so 10s was too soon.)"""
        await asyncio.sleep(90)
        if app["stdin_count"][0] == 0:
            print(
                _ts() + "\n*** No data received on stdin after 90s. Run the FULL pipeline in ONE terminal:\n"
                "  PYTHONPATH=. python -m src.polar_h10_stream | \\\n"
                "  PYTHONPATH=. python -m src.hrv_calc | \\\n"
                "  PYTHONPATH=. python -m src.graph_server --port 8765\n"
                "Then open http://localhost:8765 (do not start graph_server by itself).\n"
                "Normally HRV data appears within 1–2 minutes once the pipeline is running.\n",
                file=sys.stderr,
            )

    async def start_background_tasks(app):
        loop = asyncio.get_running_loop()
        t = threading.Thread(
            target=stdin_reader,
            args=(app["queue"], loop, app["stdin_count"]),
            daemon=True,
        )
        t.start()
        app["broadcast_task"] = asyncio.create_task(consume_queue())
        asyncio.create_task(warn_if_no_data(app))

    async def close_data_file(app):
        if app.get("data_file") is not None and not app["data_file"].closed:
            app["data_file"].close()

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(close_data_file)

    print(_ts() + f"Graph server: http://{args.host}:{args.port}", file=sys.stderr)
    print(
        _ts() + "This process must receive HRV data on stdin. Run the FULL pipeline in this terminal:",
        file=sys.stderr,
    )
    print(
        _ts() + "  PYTHONPATH=. python -m src.polar_h10_stream | "
        "PYTHONPATH=. python -m src.hrv_calc | "
        "PYTHONPATH=. python -m src.graph_server -p " + str(args.port),
        file=sys.stderr,
    )
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
