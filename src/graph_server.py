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
# Bounded queue so slow WebSocket clients can't block the pipeline
BROADCAST_QUEUE_MAXSIZE = 500
BROADCAST_SEND_TIMEOUT = 2.0


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def _write_data_file_line_sync(
    data_file_list: list,
    data_file_path_list: list,
    line: str,
    logs_dir: Path,
) -> str | None:
    """Append line to session data file; create file if needed. Returns new file path if created, else None. Run in executor to avoid blocking the event loop."""
    if data_file_list[0] is None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = logs_dir / f"data_{stamp}.jsonl"
        data_file_list[0] = open(path, "w", encoding="utf-8")
        data_file_path_list[0] = str(path)
        data_file_list[0].write(line)
        data_file_list[0].flush()
        return str(path)
    data_file_list[0].write(line)
    data_file_list[0].flush()
    return None


def _append_data_file_line_sync(data_file_list: list, line: str) -> None:
    """Append line to existing session data file. Run in executor."""
    if data_file_list[0] is not None and not data_file_list[0].closed:
        data_file_list[0].write(line)
        data_file_list[0].flush()


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
            event = obj.get("event")
            ts = obj.get("ts")
            if event == "region":
                region = obj.get("region")
                if not region:
                    continue
                request.app["current_region"][0] = region
                line = json.dumps({"event": "region", "region": region, "ts": ts}) + "\n"
            elif event == "restart":
                line = json.dumps({"event": "restart", "ts": ts}) + "\n"
            else:
                continue
            data_file_list = request.app["data_file"]
            loop = asyncio.get_running_loop()
            async with request.app["data_file_lock"]:
                await loop.run_in_executor(None, _append_data_file_line_sync, data_file_list, line)
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
    app["current_region"] = [None]  # [0] mutated at runtime to avoid aiohttp deprecation
    app["data_file"] = [None]
    app["data_file_path"] = [None]
    app["data_file_lock"] = asyncio.Lock()
    app["last_printed_count"] = [0]  # avoid duplicate "Received N lines" messages
    app["broadcast_queue"] = asyncio.Queue(maxsize=BROADCAST_QUEUE_MAXSIZE)

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
            elif n % 50 == 0 and n > app["last_printed_count"][0]:
                app["last_printed_count"][0] = n
                print(_ts() + f"Received {n} lines from pipeline.", file=sys.stderr)
            history.append(line)
            if len(history) > MAX_HISTORY:
                history.pop(0)
            # Append to session data file (ts, hr, rmssd_ms, hrv_score, region)
            try:
                obj = json.loads(line)
                obj["region"] = app["current_region"][0]
                out_line = json.dumps(obj) + "\n"
            except (json.JSONDecodeError, TypeError):
                out_line = None
            if out_line is not None:
                loop = asyncio.get_running_loop()
                async with app["data_file_lock"]:
                    new_path = await loop.run_in_executor(
                        None,
                        _write_data_file_line_sync,
                        app["data_file"],
                        app["data_file_path"],
                        out_line,
                        LOGS_DIR,
                    )
                if new_path is not None:
                    print(_ts() + f"Session data file: {new_path}", file=sys.stderr)
            # Don't block on WebSocket send; slow clients would stall the pipeline
            try:
                app["broadcast_queue"].put_nowait(line)
            except asyncio.QueueFull:
                pass  # drop this broadcast; file and history already updated

    async def broadcast_worker(app):
        """Send lines to WebSocket clients with timeout so one slow client can't block the pipeline."""
        queue = app["broadcast_queue"]
        while True:
            try:
                line = await queue.get()
            except asyncio.CancelledError:
                break
            dead = set()
            for ws in list(app["websockets"]):
                try:
                    await asyncio.wait_for(ws.send_str(line), timeout=BROADCAST_SEND_TIMEOUT)
                except (asyncio.TimeoutError, Exception):
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
        app["consume_task"] = asyncio.create_task(consume_queue())
        app["broadcast_task"] = asyncio.create_task(broadcast_worker(app))
        asyncio.create_task(warn_if_no_data(app))

    async def close_data_file(app):
        f = app.get("data_file")
        if f is not None and f[0] is not None and not f[0].closed:
            f[0].close()

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
