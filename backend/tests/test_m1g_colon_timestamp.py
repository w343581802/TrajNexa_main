"""Regression for full day-digit timestamps separated from clock by colon.

This tests parser correctness only; it does not assert OCR accuracy.
"""
from app.overlay_ocr import _parse_time


def read(raw: str):
    return _parse_time([{"text": raw, "region": "top_right"}])[0]


def test_colon_separator_preserves_both_day_digits():
    assert read("2026-10-23:14:59:59") == "2026-10-23T14:59:59"
    assert read("2026-10-23:15:00:00") == "2026-10-23T15:00:00"


def test_single_day_digit_not_silently_filled():
    assert read("2026-10-3:14:59:59") == "2026-10-03T14:59:59"
    assert read("2026-10-3/14:59:59") == "2026-10-03T14:59:59"


def test_invalid_calendar_or_incomplete_clock_is_still_rejected():
    assert read("2026-02-30 14:59:59") is None
    assert read("2026-10-23 14:59") is None
    assert read("2026-10-32:14:59:59") is None
