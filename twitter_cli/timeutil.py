"""Time formatting utilities for twitter-cli.

Supports both legacy Twitter/X timestamps (e.g. "Sat Mar 08 12:00:00 +0000 2026")
and official API v2 ISO-8601 timestamps (e.g. "2026-03-08T12:00:00.000Z").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Twitter API timestamp format: "Sat Mar 08 12:00:00 +0000 2026"
_TWITTER_TIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _parse_twitter_time(created_at: str) -> Optional[datetime]:
    """Parse legacy or ISO-8601 Twitter timestamps into a timezone-aware datetime."""
    if not created_at:
        return None
    try:
        return datetime.strptime(created_at, _TWITTER_TIME_FORMAT)
    except (ValueError, TypeError):
        pass

    iso_candidate = created_at
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_candidate)
    except (ValueError, TypeError):
        logger.debug("Failed to parse Twitter timestamp: %s", created_at)
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_local_time(created_at: str) -> str:
    """Convert Twitter timestamp to local time string.

    Returns "2026-03-14 21:08" or the original string on parse failure.
    """
    dt = _parse_twitter_time(created_at)
    if dt is None:
        return created_at
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M")


def format_relative_time(created_at: str) -> str:
    """Convert Twitter timestamp to a relative time string.

    Returns e.g. "2m ago", "3h ago", "5d ago", or the original string on failure.
    """
    dt = _parse_twitter_time(created_at)
    if dt is None:
        return created_at
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "%ds ago" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm ago" % minutes
    hours = minutes // 60
    if hours < 24:
        return "%dh ago" % hours
    days = hours // 24
    if days < 30:
        return "%dd ago" % days
    months = days // 30
    if months < 12:
        return "%dmo ago" % months
    years = days // 365
    return "%dy ago" % years


def format_iso8601(created_at: str) -> str:
    """Convert Twitter timestamp to ISO 8601 format.

    Returns e.g. "2026-03-08T12:00:00+00:00" or the original string on failure.
    """
    dt = _parse_twitter_time(created_at)
    if dt is None:
        return created_at
    return dt.isoformat()
