#!/usr/bin/env python3
"""Exit 0 if Python >= 3.14, else 1 with a clear message. Run after activating the venv."""
import sys

if sys.version_info >= (3, 14):
    print(f"OK: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    sys.exit(0)
else:
    v = sys.version_info
    print(
        f"This project requires Python 3.14+. You have {v.major}.{v.minor}.{v.micro}.",
        file=sys.stderr,
    )
    print(
        "Install Python 3.14, then recreate the venv with that interpreter:",
        file=sys.stderr,
    )
    print("  rm -rf .venv && python3.14 -m venv .venv && source .venv/bin/activate", file=sys.stderr)
    sys.exit(1)
