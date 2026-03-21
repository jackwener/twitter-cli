"""Tests for twitter_cli.timeutil module."""

from __future__ import annotations

from twitter_cli.timeutil import format_iso8601, format_local_time, format_relative_time


SAMPLE_TIMESTAMP = "Sat Mar 08 12:00:00 +0000 2026"
ISO_TIMESTAMP = "2026-03-08T12:00:00.000Z"


# ── format_local_time ────────────────────────────────────────────────────


def test_format_local_time_valid() -> None:
    result = format_local_time(SAMPLE_TIMESTAMP)
    # Should be in YYYY-MM-DD HH:MM format (local timezone)
    assert result.startswith("2026-03-")
    assert ":" in result


def test_format_local_time_empty() -> None:
    assert format_local_time("") == ""


def test_format_local_time_invalid() -> None:
    assert format_local_time("not a date") == "not a date"


def test_format_local_time_iso8601() -> None:
    result = format_local_time(ISO_TIMESTAMP)
    assert result.startswith("2026-03-")
    assert ":" in result


# ── format_relative_time ─────────────────────────────────────────────────


def test_format_relative_time_old() -> None:
    # A timestamp from 2020 should show years ago
    old_ts = "Sat Jan 01 00:00:00 +0000 2020"
    result = format_relative_time(old_ts)
    assert result.endswith("ago")
    assert "y" in result or "mo" in result or "d" in result


def test_format_relative_time_empty() -> None:
    assert format_relative_time("") == ""


def test_format_relative_time_invalid() -> None:
    assert format_relative_time("garbage") == "garbage"


def test_format_relative_time_iso_future() -> None:
    assert format_relative_time("2999-01-01T00:00:00Z") == "just now"


# ── format_iso8601 ───────────────────────────────────────────────────────


def test_format_iso8601_valid() -> None:
    result = format_iso8601(SAMPLE_TIMESTAMP)
    assert result.startswith("2026-03-08T12:00:00")
    assert "+00:00" in result or "Z" in result


def test_format_iso8601_empty() -> None:
    assert format_iso8601("") == ""


def test_format_iso8601_invalid() -> None:
    assert format_iso8601("not a date") == "not a date"


def test_format_iso8601_keeps_iso_timestamp_parseable() -> None:
    result = format_iso8601(ISO_TIMESTAMP)
    assert result.startswith("2026-03-08T12:00:00")
    assert "+00:00" in result


def test_format_iso8601_roundtrip() -> None:
    """ISO 8601 output should be parseable by datetime.fromisoformat."""
    from datetime import datetime

    result = format_iso8601(SAMPLE_TIMESTAMP)
    dt = datetime.fromisoformat(result)
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 8
