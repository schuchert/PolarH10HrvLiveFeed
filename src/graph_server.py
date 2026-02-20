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
        async for _ in ws:
            pass
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

    app.on_startup.append(start_background_tasks)

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
