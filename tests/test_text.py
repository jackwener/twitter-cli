from __future__ import annotations

from twitter_cli.text import (
    CREATE_NOTE_TWEET_OPERATION,
    CREATE_TWEET_OPERATION,
    tweet_create_route,
    tweet_weighted_length,
)


def test_weighted_length_uses_twitter_text_v3_unicode_ranges() -> None:
    assert tweet_weighted_length("\u00e9" * 280) == 280
    assert tweet_weighted_length("\u00df" * 280) == 280
    assert tweet_weighted_length("\u4f60" * 140) == 280


def test_weighted_length_normalizes_nfc() -> None:
    assert tweet_weighted_length("A\u0301B") == 2


def test_weighted_length_normalizes_http_url_to_23_characters() -> None:
    assert tweet_weighted_length("Hi http://test.co") == 26
    long_url = "https://example.com/" + ("path/" * 80)
    assert tweet_weighted_length(long_url) == 23


def test_weighted_length_normalizes_common_bare_domain() -> None:
    assert tweet_weighted_length("See example.com/a/very/long/path") == 27


def test_weighted_length_collapses_emoji_sequences() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466"
    flag = "\U0001f1fa\U0001f1f8"
    assert tweet_weighted_length(family * 140) == 280
    assert tweet_weighted_length(flag) == 2


def test_route_uses_weighted_limit() -> None:
    assert tweet_create_route("\u00e9" * 280) == (CREATE_TWEET_OPERATION, 280)
    assert tweet_create_route("\u4f60" * 141) == (CREATE_NOTE_TWEET_OPERATION, 282)
