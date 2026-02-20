#!/usr/bin/env python3
"""
Stream RR intervals (and HR) from a Polar H10 over BLE. Outputs JSON lines to stdout.

Usage:
  python -m src.polar_h10_stream [--device "POLAR H10 XXXXXXXX"]

One line per RR interval: {"hr": 72, "rr_ms": 850.5, "ts": 1234567890.123}
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


def _on_hrm(sender_handle: int, data: bytearray, out_stream):
    try:
        parsed = parse_hrm(bytes(data))
        hr = parsed["hr"]
        for rr_ms in parsed["rr_ms"]:
            line = json.dumps({"hr": hr, "rr_ms": round(rr_ms, 2), "ts": time.time()})
            print(line, flush=True)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=out_stream, flush=True)


async def _run(device_name: str | None):
    if device_name:
        device = await BleakScanner.find_device_by_name(device_name)
        if device is None:
            print(f"No device named '{device_name}' found.", file=sys.stderr)
            return 1
    else:
        print("Scanning for Polar H10 (5s)...", file=sys.stderr)
        devices = await BleakScanner.discover(timeout=5.0)
        polar = [d for d in devices if d.name and "Polar H10" in d.name]
        if not polar:
            print("No Polar H10 found. Make sure the strap is on and in range.", file=sys.stderr)
            return 1
        device = polar[0]
        print(f"Using: {device.name} ({device.address})", file=sys.stderr)

    def callback(sender, data):
        _on_hrm(sender, data, sys.stderr)

    async with BleakClient(device) as client:
        await client.start_notify(HRM_CHAR_UUID, callback)
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
    args = parser.parse_args()
    try:
        exit(asyncio.run(_run(args.device)))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
