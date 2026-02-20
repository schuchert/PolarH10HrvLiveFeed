#!/usr/bin/env python3
"""
Stream RR intervals (and HR) from a Polar H10 over BLE. Outputs JSON lines to stdout.

Usage:
  python -m src.polar_h10_stream [--device "POLAR H10 XXXXXXXX"]

Stdout: "# connected" when linked, then JSON lines. Filter status with e.g. grep -v '^# '
One line per RR: {"hr": 72, "rr_ms": 850.5, "ts": ...}
When a packet has HR but no RR: {"hr": 72, "rr_ms": null, "ts": ...} so downstream can show N/A.

By default the process auto-reconnects if the BLE link drops (--reconnect-delay 5, --max-reconnects 0 = unlimited).
"""

import argparse
import asyncio
import json
import queue
import sys
import traceback
import time
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Install bleak: pip install bleak", file=sys.stderr)
    sys.exit(1)

from src.gatt_hrm import parse_hrm


def _ts():
    return f"[{datetime.now().strftime('%H:%M:%S')}] "


def _verbose(quiet: bool, msg: str, file=sys.stderr):
    if not quiet:
        print(_ts() + msg, file=file, flush=True)


# GATT Heart Rate Measurement characteristic (standard 16-bit UUID)
HRM_CHAR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


def _on_hrm(sender_handle: int, data: bytes, err_stream, first_packet_reported: list):
    try:
        if not first_packet_reported:
            first_packet_reported.append(True)
            print(_ts() + "Received first HRM packet.", file=err_stream, flush=True)  # always log for diagnosis
        parsed = parse_hrm(data)
        hr = parsed["hr"]
        rr_list = parsed["rr_ms"]
        ts = time.time()
        if not rr_list:
            # Have HR but no RR in this packet — emit so downstream can show "live, HRV N/A"
            line = json.dumps({"hr": hr, "rr_ms": None, "ts": ts})
            print(line, flush=True)
        else:
            for rr_ms in rr_list:
                line = json.dumps({"hr": hr, "rr_ms": round(rr_ms, 2), "ts": ts})
                print(line, flush=True)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=err_stream, flush=True)


async def _find_device(device_name: str | None, quiet: bool):
    """Resolve device name to a BleakDevice. Returns None if not found."""
    if device_name:
        _verbose(quiet, f"Looking for device by name: {device_name!r}")
        device = await BleakScanner.find_device_by_name(device_name)
        if device is None:
            print(_ts() + f"No device named '{device_name}' found.", file=sys.stderr)
            return None
        _verbose(quiet, f"Found device: {device.name} ({device.address})")
        return device
    print(_ts() + "Scanning for Polar H10 (2 min)...", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=120.0)
    polar = [d for d in devices if d.name and "Polar H10" in d.name]
    _verbose(quiet, f"Scan complete: {len(devices)} device(s) total, {len(polar)} Polar H10.")
    if not polar:
        print(_ts() + "No Polar H10 found.", file=sys.stderr)
        return None
    device = polar[0]
    print(_ts() + f"Using: {device.name} ({device.address})", file=sys.stderr)
    return device


def _print_scan_tips(quiet: bool):
    if quiet:
        return
    print(
        _ts() + "Tips: Strap on & moisten electrodes; disconnect Polar Beat / other apps from H10; check Bluetooth is on.",
        file=sys.stderr,
    )


async def _run(
    device_name: str | None,
    connect_timeout: float,
    reconnect_delay: float,
    max_reconnects: int,
    scan_retries: int,
    scan_retry_delay: float,
    quiet: bool,
):
    first_packet = []
    hrm_queue = queue.Queue()

    def callback(sender, data):
        try:
            hrm_queue.put_nowait((sender, bytes(data)))
        except queue.Full:
            pass

    async def drain_hrm_queue():
        while True:
            try:
                sender, data = await asyncio.get_event_loop().run_in_executor(None, hrm_queue.get)
            except asyncio.CancelledError:
                raise
            if sender is None:
                return
            _on_hrm(sender, data, sys.stderr, first_packet)

    reconnect_count = 0
    while True:
        device = None
        # After sleep, Bluetooth often needs extra time; use longer wait before first reconnect
        if reconnect_count > 0:
            delay = 15.0 if reconnect_count == 1 else reconnect_delay
            print(_ts() + f"Connection lost. Waiting {delay:.0f}s before reconnect (Bluetooth may need a moment after sleep)...", file=sys.stderr)
            _verbose(quiet, f"Reconnect attempt {reconnect_count} (max={'unlimited' if max_reconnects <= 0 else max_reconnects})")
            await asyncio.sleep(delay)
        for attempt in range(scan_retries):
            _verbose(quiet, f"Scan attempt {attempt + 1}/{scan_retries}")
            device = await _find_device(device_name, quiet)
            if device is not None:
                break
            if attempt < scan_retries - 1:
                _print_scan_tips(quiet)
                print(_ts() + f"Retrying scan in {scan_retry_delay:.0f}s ({attempt + 2}/{scan_retries})...", file=sys.stderr)
                await asyncio.sleep(scan_retry_delay)
        if device is None:
            _print_scan_tips(quiet)
            return 1

        print(_ts() + f"Connecting (timeout {connect_timeout:.0f}s)...", file=sys.stderr)
        _verbose(quiet, f"Connecting to {device.name} at {device.address}")
        drain_task = None
        try:
            async with BleakClient(device, timeout=connect_timeout) as client:
                await client.start_notify(HRM_CHAR_UUID, callback)
                t = datetime.now().strftime("%H:%M:%S")
                print(f"# connected {t}", flush=True)
                print(_ts() + "Streaming RR intervals (Ctrl+C to stop). Reconnects on drop.", file=sys.stderr)
                connect_time = time.time()
                no_data_msg_shown = False
                drain_task = asyncio.create_task(drain_hrm_queue())
                try:
                    while True:
                        await asyncio.sleep(1)
                        if not first_packet and (time.time() - connect_time) > 20 and not no_data_msg_shown:
                            no_data_msg_shown = True
                            print(
                                _ts() + "No HRM packets yet. Strap on & moisten electrodes; try removing H10 from System Settings → Bluetooth and reconnect.",
                                file=sys.stderr,
                            )
                except asyncio.CancelledError:
                    raise
                finally:
                    if drain_task is not None:
                        try:
                            hrm_queue.put_nowait((None, None))
                        except queue.Full:
                            pass
                        drain_task.cancel()
                        try:
                            await drain_task
                        except asyncio.CancelledError:
                            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if drain_task is not None:
                try:
                    hrm_queue.put_nowait((None, None))
                except queue.Full:
                    pass
                drain_task.cancel()
                try:
                    await drain_task
                except asyncio.CancelledError:
                    pass
            print(_ts() + f"Connection lost: {e}", file=sys.stderr)
            if not quiet:
                traceback.print_exc(file=sys.stderr)
            if reconnect_delay <= 0:
                print(_ts() + "Exiting (reconnect disabled).", file=sys.stderr)
                return 1
            reconnect_count += 1
            if max_reconnects > 0 and reconnect_count >= max_reconnects:
                print(_ts() + f"Max reconnects ({max_reconnects}) reached. Exiting.", file=sys.stderr)
                return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Stream Polar H10 RR intervals as JSON lines")
    parser.add_argument("--device", "-d", type=str, default=None, help='Device name, e.g. "POLAR H10 0A3BA92B"')
    parser.add_argument(
        "--connect-timeout", "-t",
        type=float,
        default=90.0,
        help="BLE connection timeout in seconds (default 90). Increase if connection often times out.",
    )
    parser.add_argument(
        "--reconnect-delay", "-r",
        type=float,
        default=5.0,
        help="Seconds to wait before reconnecting after a drop (default 5). Use 0 to disable auto-reconnect.",
    )
    parser.add_argument(
        "--max-reconnects", "-m",
        type=int,
        default=0,
        help="Max auto-reconnect attempts after a drop (default 0 = unlimited).",
    )
    parser.add_argument(
        "--scan-retries",
        type=int,
        default=5,
        help="Number of scans to try if no H10 found (default 5).",
    )
    parser.add_argument(
        "--scan-retry-delay",
        type=float,
        default=15.0,
        help="Seconds to wait between scan retries (default 15).",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Quiet mode: less diagnostic logging (no scan counts, no tracebacks).",
    )
    args = parser.parse_args()
    try:
        exit(asyncio.run(_run(
            args.device, args.connect_timeout, args.reconnect_delay, args.max_reconnects,
            args.scan_retries, args.scan_retry_delay, args.quiet,
        )))
    except KeyboardInterrupt:
        print(_ts() + "Stopped.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
