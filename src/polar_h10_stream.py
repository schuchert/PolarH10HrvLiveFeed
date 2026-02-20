#!/usr/bin/env python3
"""
Stream RR intervals (and HR) from a Polar H10 over BLE. Outputs JSON lines to stdout.

Usage:
  python -m src.polar_h10_stream [--device "POLAR H10 XXXXXXXX"]

Stdout: "# connected" when linked, then JSON lines. Filter status with e.g. grep -v '^# '
One line per RR: {"hr": 72, "rr_ms": 850.5, "ts": ...}
When a packet has HR but no RR: {"hr": 72, "rr_ms": null, "ts": ...} so downstream can show N/A.
"""

import argparse
import asyncio
import json
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Install bleak: pip install bleak", file=sys.stderr)
    sys.exit(1)

from src.gatt_hrm import parse_hrm


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


async def _run(device_name: str | None, connect_timeout: float):
    if device_name:
        device = await BleakScanner.find_device_by_name(device_name)
        if device is None:
            print(f"No device named '{device_name}' found.", file=sys.stderr)
            return 1
    else:
        print("Scanning for Polar H10 (2 min)...", file=sys.stderr)
        devices = await BleakScanner.discover(timeout=120.0)
        polar = [d for d in devices if d.name and "Polar H10" in d.name]
        if not polar:
            print("No Polar H10 found. Make sure the strap is on and in range.", file=sys.stderr)
            return 1
        device = polar[0]
        print(f"Using: {device.name} ({device.address})", file=sys.stderr)

    print(f"Connecting (timeout {connect_timeout:.0f}s)...", file=sys.stderr)
    def callback(sender, data):
        _on_hrm(sender, data, sys.stderr)

    async with BleakClient(device, timeout=connect_timeout) as client:
        await client.start_notify(HRM_CHAR_UUID, callback)
        print("# connected", flush=True)
        print("Streaming RR intervals (Ctrl+C to stop)...", file=sys.stderr)
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
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
    args = parser.parse_args()
    try:
        exit(asyncio.run(_run(args.device, args.connect_timeout)))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
