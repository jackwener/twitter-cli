"""Short-index cache: persist the last displayed tweet list for quick `show` access."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from .models import Tweet

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".twitter-cli"
_CACHE_FILE = _CACHE_DIR / "last_results.json"
_TTL = 3600  # seconds


def save_tweet_cache(tweets: List[Tweet]) -> None:
    """Persist tweet list so indices can be resolved by `show`."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = [
            {"index": i + 1, "id": t.id, "author": t.author.screen_name, "text": t.text[:80]}
            for i, t in enumerate(tweets)
            if t.id
        ]
        payload = {"created_at": time.time(), "tweets": entries}
        _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to write tweet cache: %s", exc)


def _load_cache() -> Optional[List[dict]]:
    """Load and validate the cache file; return tweet entries or None if stale/missing."""
    try:
        if not _CACHE_FILE.exists():
            return None
        payload = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if time.time() - payload.get("created_at", 0) > _TTL:
            return None
        entries = payload.get("tweets", [])
        if not isinstance(entries, list):
            return None
        return [e for e in entries if isinstance(e, dict)]
    except (OSError, json.JSONDecodeError):
        return None


def get_tweet_id_by_index(index: int) -> Optional[str]:
    """Return tweet ID at *index* (1-based), or None if not found."""
    entries = _load_cache()
    if entries is None:
        return None
    for entry in entries:
        if entry.get("index") == index:
            tweet_id = entry.get("id")
            return str(tweet_id) if tweet_id is not None else None
    return None


def get_cache_size() -> int:
    """Return number of tweets in the current cache (0 if unavailable)."""
    entries = _load_cache()
    return len(entries) if entries is not None else 0