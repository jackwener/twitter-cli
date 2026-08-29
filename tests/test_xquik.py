"""Tests for the optional Xquik search provider."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from twitter_cli.exceptions import (
    AuthenticationError,
    InvalidInputError,
    RateLimitError,
    TwitterError,
)
from twitter_cli.xquik import _load_client, _raise_api_error, fetch_xquik_search


class FakeTweets:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(tweets=self.results)


class FakeClient:
    def __init__(self, results):
        self.tweets = FakeTweets(results)
        self.x = SimpleNamespace(tweets=self.tweets)


def _author(username="alice"):
    return SimpleNamespace(
        id="10",
        name="Alice",
        username=username,
        profile_picture="https://example.com/alice.jpg",
        verified=False,
        is_verified=False,
        is_blue_verified=True,
    )


def _tweet(**overrides):
    values = {
        "id": "123",
        "text": "short text",
        "author": _author(),
        "like_count": 12,
        "retweet_count": 4,
        "reply_count": 3,
        "quote_count": 2,
        "view_count": 100,
        "bookmark_count": 1,
        "created_at": "2026-08-29T08:15:30Z",
        "media": [],
        "entities": {},
        "retweeted_tweet": None,
        "quoted_tweet": None,
        "note_tweet": None,
        "article": None,
        "lang": "en",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fetch_maps_xquik_page_to_native_tweets() -> None:
    quoted = _tweet(id="122", text="quoted", author=_author("bob"))
    source = _tweet(
        note_tweet=SimpleNamespace(text="complete long post"),
        quoted_tweet=quoted,
        article=SimpleNamespace(title="An article"),
        media=[
            SimpleNamespace(
                type="photo",
                media_url="https://example.com/photo.jpg",
                url="https://x.com/alice/status/123/photo/1",
                width=1200,
                height=800,
            )
        ],
        entities={"urls": [{"expandedUrl": "https://example.com/story"}]},
    )
    client = FakeClient([source])

    tweets = fetch_xquik_search("python", 25, "Top", client=client)

    assert client.tweets.calls == [{"q": "python", "limit": 25, "query_type": "Top"}]
    assert len(tweets) == 1
    assert tweets[0].text == "complete long post"
    assert tweets[0].author.screen_name == "alice"
    assert tweets[0].author.verified is True
    assert tweets[0].metrics.likes == 12
    assert tweets[0].created_at == "Sat Aug 29 08:15:30 +0000 2026"
    assert tweets[0].media[0].url == "https://example.com/photo.jpg"
    assert tweets[0].urls == ["https://example.com/story"]
    assert tweets[0].quoted_tweet is not None
    assert tweets[0].quoted_tweet.author.screen_name == "bob"
    assert tweets[0].article_title == "An article"


def test_fetch_maps_photo_search_to_latest_media_filter() -> None:
    client = FakeClient([])

    assert fetch_xquik_search("cats", 10, "Photos", client=client) == []
    assert client.tweets.calls == [
        {"q": "cats", "limit": 10, "query_type": "Latest", "media_type": "images"}
    ]


def test_fetch_maps_video_search_and_naive_timestamp() -> None:
    client = FakeClient([_tweet(created_at="2026-08-29T08:15:30")])

    tweets = fetch_xquik_search("cats", 10, "Videos", client=client)

    assert client.tweets.calls == [
        {"q": "cats", "limit": 10, "query_type": "Latest", "media_type": "videos"}
    ]
    assert tweets[0].created_at == "Sat Aug 29 08:15:30 +0000 2026"


def test_fetch_preserves_retweet_author_context() -> None:
    original = _tweet(id="200", author=_author("source"), text="original")
    wrapper = _tweet(author=_author("resharer"), retweeted_tweet=original)

    result = fetch_xquik_search("topic", 1, "Latest", client=FakeClient([wrapper]))[0]

    assert result.id == "200"
    assert result.author.screen_name == "source"
    assert result.is_retweet is True
    assert result.retweeted_by == "resharer"


@pytest.mark.parametrize("count", [0, 201])
def test_fetch_rejects_unbounded_result_counts(count: int) -> None:
    with pytest.raises(InvalidInputError, match="--max from 1 to 200"):
        fetch_xquik_search("topic", count, "Latest", client=FakeClient([]))


def test_fetch_rejects_unknown_search_type() -> None:
    with pytest.raises(InvalidInputError, match="does not support"):
        fetch_xquik_search("topic", 10, "People", client=FakeClient([]))


@pytest.mark.parametrize("status", [401, 403])
def test_api_auth_errors_name_the_required_variable(status: int) -> None:
    error = type("APIError", (Exception,), {"status_code": status})()

    with pytest.raises(AuthenticationError, match="X_TWITTER_SCRAPER_API_KEY"):
        _raise_api_error(error)


def test_api_rate_limit_maps_to_structured_error_class() -> None:
    error = type("APIError", (Exception,), {"status_code": 429})()

    with pytest.raises(RateLimitError, match="Retry later"):
        _raise_api_error(error)


def test_missing_optional_sdk_has_install_command(monkeypatch) -> None:
    real_import = __import__

    def missing_sdk(name, *args, **kwargs):
        if name == "x_twitter_scraper":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", missing_sdk)

    with pytest.raises(TwitterError, match=r"twitter-cli\[xquik\]"):
        _load_client()
