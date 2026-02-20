"""Tests for GATT Heart Rate Measurement (0x2A37) payload parsing."""

import pytest
from src.gatt_hrm import parse_hrm


def test_hr_only_uint8():
    """HR in UINT8 format, no RR, no EE. Flags=0x00."""
    # Flags 0x00, HR = 72 bpm
    payload = bytes([0x00, 0x48])
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert result["rr_ms"] == []


def test_hr_only_uint16():
    """HR in UINT16 format (flag bit 0 set)."""
    # Flags 0x01, HR = 0x0048 = 72 bpm (little-endian)
    payload = bytes([0x01, 0x48, 0x00])
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert result["rr_ms"] == []


def test_hr_and_one_rr_uint8():
    """HR UINT8 + one RR interval. RR = 768 (1/1024 s) -> 750 ms."""
    # Flags 0x10 (RR present), HR 72, RR 0x0300 = 768 -> 768*1000/1024 = 750
    payload = bytes([0x10, 0x48, 0x00, 0x03])
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert len(result["rr_ms"]) == 1
    assert result["rr_ms"][0] == pytest.approx(750.0, rel=0.01)


def test_hr_and_two_rr_intervals():
    """HR UINT8 + two RR intervals (four bytes)."""
    # Flags 0x10, HR 72, RR1=768 (750ms), RR2=850 (830.08ms)
    payload = bytes([0x10, 0x48, 0x00, 0x03, 0x52, 0x03])
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert len(result["rr_ms"]) == 2
    assert result["rr_ms"][0] == pytest.approx(750.0, rel=0.01)
    assert result["rr_ms"][1] == pytest.approx(850 * 1000 / 1024, rel=0.01)


def test_hr_uint16_with_rr():
    """HR UINT16 + RR intervals. Flags 0x11 (UINT16 + RR)."""
    # Flags 0x11, HR 0x0048=72, RR 0x0400=1024 -> 1000 ms
    payload = bytes([0x11, 0x48, 0x00, 0x00, 0x04])
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert len(result["rr_ms"]) == 1
    assert result["rr_ms"][0] == pytest.approx(1000.0, rel=0.01)


def test_ee_present_no_rr():
    """Energy Expended present (flag bit 3), no RR. HR 1 byte."""
    # Flags 0x08, HR 60, EE 0x0123 (291)
    payload = bytes([0x08, 0x3C, 0x23, 0x01])
    result = parse_hrm(payload)
    assert result["hr"] == 60
    assert result["rr_ms"] == []


def test_ee_and_rr_present_uint8():
    """EE + RR present, HR UINT8. Parsing order: flag, HR(1), EE(2), RR pairs."""
    # Flags 0x18: EE + RR. HR 72, EE 0x0000, RR 800 (800*1000/1024)
    payload = bytes([0x18, 0x48, 0x00, 0x00, 0x20, 0x03])  # RR = 0x0320 = 800
    result = parse_hrm(payload)
    assert result["hr"] == 72
    assert len(result["rr_ms"]) == 1
    assert result["rr_ms"][0] == pytest.approx(800 * 1000 / 1024, rel=0.01)


def test_empty_payload_raises():
    """Too short payload (e.g. empty) should raise or return safe default."""
    with pytest.raises(ValueError):
        parse_hrm(bytes([]))


def test_flag_only_raises():
    """Single byte (flag only) is invalid."""
    with pytest.raises(ValueError):
        parse_hrm(bytes([0x00]))
