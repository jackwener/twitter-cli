from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from twitter_cli.models import Author, Metrics, Tweet

os.environ.setdefault("OUTPUT", "rich")


@pytest.fixture()
def tweet_factory():
    def _make_tweet(tweet_id: str = "1", **overrides: Any) -> Tweet:
        metrics = overrides.pop(
            "metrics",
            Metrics(likes=10, retweets=2, replies=1, quotes=0, views=120, bookmarks=3),
        )
        author = overrides.pop(
            "author",
            Author(id="u1", name="Alice", screen_name="alice", verified=False),
        )
        return Tweet(
            id=tweet_id,
            text=overrides.pop("text", "hello"),
            author=author,
            metrics=metrics,
            created_at=overrides.pop("created_at", "2025-01-01"),
            media=overrides.pop("media", []),
            urls=overrides.pop("urls", []),
            is_retweet=overrides.pop("is_retweet", False),
            lang=overrides.pop("lang", "en"),
            retweeted_by=overrides.pop("retweeted_by", None),
            quoted_tweet=overrides.pop("quoted_tweet", None),
            score=overrides.pop("score", 0.0),
            article_title=overrides.pop("article_title", None),
            article_text=overrides.pop("article_text", None),
        )

    return _make_tweet


@pytest.fixture()
def fixture_loader():
    fixture_dir = Path(__file__).parent / "fixtures"

    def _load(name: str) -> Any:
        return json.loads((fixture_dir / name).read_text(encoding="utf-8"))

    return _load
