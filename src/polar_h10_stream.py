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
import sys
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


# GATT Heart Rate Measurement characteristic (standard 16-bit UUID)
HRM_CHAR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


def _on_hrm(sender_handle: int, data: bytearray, err_stream):
    try:
        parsed = parse_hrm(bytes(data))
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


async def _find_device(device_name: str | None):
    """Resolve device name to a BleakDevice. Returns None if not found."""
    if device_name:
        device = await BleakScanner.find_device_by_name(device_name)
        if device is None:
            print(_ts() + f"No device named '{device_name}' found.", file=sys.stderr)
            return None
        return device
    print(_ts() + "Scanning for Polar H10 (2 min)...", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=120.0)
    polar = [d for d in devices if d.name and "Polar H10" in d.name]
    if not polar:
        print(_ts() + "No Polar H10 found. Make sure the strap is on and in range.", file=sys.stderr)
        return None
    device = polar[0]
    print(_ts() + f"Using: {device.name} ({device.address})", file=sys.stderr)
    return device


async def _run(
    device_name: str | None,
    connect_timeout: float,
    reconnect_delay: float,
    max_reconnects: int,
):
    def callback(sender, data):
        _on_hrm(sender, data, sys.stderr)

    reconnect_count = 0
    while True:
        device = await _find_device(device_name)
        if device is None:
            return 1

        print(_ts() + f"Connecting (timeout {connect_timeout:.0f}s)...", file=sys.stderr)
        try:
            async with BleakClient(device, timeout=connect_timeout) as client:
                await client.start_notify(HRM_CHAR_UUID, callback)
                t = datetime.now().strftime("%H:%M:%S")
                print(f"# connected {t}", flush=True)
                print(_ts() + "Streaming RR intervals (Ctrl+C to stop). Reconnects on drop.", file=sys.stderr)
                try:
                    while True:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if reconnect_delay <= 0:
                print(_ts() + f"Connection lost: {e}. Exiting (reconnect disabled).", file=sys.stderr)
                return 1
            reconnect_count += 1
            if max_reconnects > 0 and reconnect_count >= max_reconnects:
                print(_ts() + f"Connection lost. Max reconnects ({max_reconnects}) reached. Exiting.", file=sys.stderr)
                return 1
            print(_ts() + f"Connection lost: {e}. Reconnecting in {reconnect_delay:.0f}s...", file=sys.stderr)
            await asyncio.sleep(reconnect_delay)
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
    args = parser.parse_args()
    try:
        exit(asyncio.run(_run(args.device, args.connect_timeout, args.reconnect_delay, args.max_reconnects)))
    except KeyboardInterrupt:
        print(_ts() + "Stopped.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
