"""
Parse GATT Heart Rate Measurement characteristic (0x2A37) payloads.

Flags byte: bit 0 = HR 16-bit, bit 3 = Energy Expended present, bit 4 = RR present.
RR intervals: UINT16 LE, unit 1/1024 s → rr_ms = value * 1000 / 1024.
"""


def parse_hrm(data: bytes) -> dict:
    """
    Parse a Heart Rate Measurement characteristic value.

    Returns dict with:
      - hr: int (bpm)
      - rr_ms: list[float] (RR intervals in milliseconds)
    """
    if len(data) < 2:
        raise ValueError("HRM payload too short")

    flags = data[0]
    hr_16bit = bool(flags & 0x01)
    ee_present = bool(flags & 0x08)
    rr_present = bool(flags & 0x10)

    offset = 1

    # Heart rate
    if hr_16bit:
        if len(data) < offset + 2:
            raise ValueError("HRM payload too short for HR")
        hr = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
    else:
        hr = data[offset]
        offset += 1

    # Energy expended
    if ee_present:
        if len(data) < offset + 2:
            raise ValueError("HRM payload too short for EE")
        offset += 2

    # RR intervals (pairs of UINT16 LE)
    rr_ms: list[float] = []
    if rr_present:
        while len(data) >= offset + 2:
            raw = int.from_bytes(data[offset : offset + 2], "little")
            rr_ms.append(raw * 1000.0 / 1024.0)
            offset += 2

    return {"hr": hr, "rr_ms": rr_ms}
