"""Unit tests for core client.py functions.

Tests the parsing, header building, media extraction, Chrome target detection,
and feature flag update logic — all without requiring network access.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest


from twitter_cli.client import (
    _best_chrome_target,
    TwitterClient,
)
from twitter_cli.exceptions import TwitterAPIError
from twitter_cli.graphql import (
    FEATURES,
    _build_graphql_url,
    _update_features_from_html,
)
from twitter_cli.parser import (
    _deep_get,
    _extract_cursor,
    _extract_media,
    _parse_int,
    parse_tweet_result,
    parse_user_result,
)


# ── _deep_get ────────────────────────────────────────────────────────────

class TestDeepGet:
    def test_nested_dict(self):
        data = {"a": {"b": {"c": 42}}}
        assert _deep_get(data, "a", "b", "c") == 42

    def test_missing_key(self):
        assert _deep_get({"a": 1}, "b") is None

    def test_deeply_missing(self):
        assert _deep_get({"a": {"b": 1}}, "a", "c", "d") is None

    def test_list_access(self):
        data = {"items": [10, 20, 30]}
        assert _deep_get(data, "items", 1) == 20

    def test_list_out_of_bounds(self):
        data = {"items": [10]}
        assert _deep_get(data, "items", 5) is None

    def test_none_input(self):
        assert _deep_get(None, "a") is None

    def test_empty_keys(self):
        data = {"x": 1}
        assert _deep_get(data) == data


# ── _parse_int ───────────────────────────────────────────────────────────

class TestParseInt:
    def test_normal_int(self):
        assert _parse_int(42, 0) == 42

    def test_string_int(self):
        assert _parse_int("123", 0) == 123

    def test_float_string(self):
        assert _parse_int("99.9", 0) == 99

    def test_comma_separated(self):
        assert _parse_int("1,234", 0) == 1234

    def test_empty_string(self):
        assert _parse_int("", 0) == 0

    def test_none(self):
        assert _parse_int(None, -1) == -1

    def test_invalid(self):
        assert _parse_int("abc", 5) == 5


# ── _extract_cursor ──────────────────────────────────────────────────────

class TestExtractCursor:
    def test_bottom_cursor(self):
        content = {"cursorType": "Bottom", "value": "cursor_abc"}
        assert _extract_cursor(content) == "cursor_abc"

    def test_top_cursor_ignored(self):
        content = {"cursorType": "Top", "value": "cursor_top"}
        assert _extract_cursor(content) is None

    def test_no_cursor(self):
        assert _extract_cursor({}) is None


# ── _extract_media ───────────────────────────────────────────────────────

class TestExtractMedia:
    def test_photo(self):
        legacy = {
            "extended_entities": {
                "media": [
                    {
                        "type": "photo",
                        "media_url_https": "https://pbs.twimg.com/img.jpg",
                        "original_info": {"width": 1200, "height": 800},
                    }
                ]
            }
        }
        media = _extract_media(legacy)
        assert len(media) == 1
        assert media[0].type == "photo"
        assert media[0].url == "https://pbs.twimg.com/img.jpg"
        assert media[0].width == 1200

    def test_video_picks_highest_bitrate(self):
        legacy = {
            "extended_entities": {
                "media": [
                    {
                        "type": "video",
                        "media_url_https": "https://pbs.twimg.com/thumb.jpg",
                        "original_info": {"width": 1920, "height": 1080},
                        "video_info": {
                            "variants": [
                                {"content_type": "video/mp4", "bitrate": 832000, "url": "https://low.mp4"},
                                {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://high.mp4"},
                                {"content_type": "application/x-mpegURL", "url": "https://stream.m3u8"},
                            ]
                        },
                    }
                ]
            }
        }
        media = _extract_media(legacy)
        assert len(media) == 1
        assert media[0].type == "video"
        assert media[0].url == "https://high.mp4"

    def test_no_media(self):
        assert _extract_media({}) == []

    def test_animated_gif(self):
        legacy = {
            "extended_entities": {
                "media": [
                    {
                        "type": "animated_gif",
                        "media_url_https": "https://pbs.twimg.com/gif.mp4",
                        "original_info": {"width": 480, "height": 270},
                        "video_info": {
                            "variants": [
                                {"content_type": "video/mp4", "bitrate": 0, "url": "https://gif.mp4"},
                            ]
                        },
                    }
                ]
            }
        }
        media = _extract_media(legacy)
        assert len(media) == 1
        assert media[0].type == "animated_gif"


# ── _build_graphql_url ───────────────────────────────────────────────────

class TestBuildGraphqlUrl:
    def test_basic_url(self):
        url = _build_graphql_url("abc123", "HomeTimeline", {"count": 20}, {"f1": True})
        assert "graphql/abc123/HomeTimeline" in url
        assert "variables=" in url
        assert "features=" in url

    def test_field_toggles(self):
        url = _build_graphql_url("x", "Op", {}, {}, {"toggle": True})
        assert "fieldToggles=" in url

    def test_false_features_omitted_from_url(self):
        """False-valued features should be omitted to keep URL short (avoid 414)."""
        features = {"enabled_flag": True, "disabled_flag": False, "another_enabled": True}
        url = _build_graphql_url("q", "Op", {}, features)
        assert "enabled_flag" in url
        assert "another_enabled" in url
        assert "disabled_flag" not in url

    def test_url_length_with_full_features(self):
        """URL with full FEATURES dict should stay under 8000 chars (server limit)."""
        url = _build_graphql_url(
            "abc123", "SearchTimeline",
            {"rawQuery": "AI agent", "querySource": "typed_query", "product": "Latest", "count": 50},
            FEATURES,
        )
        assert len(url) < 8000, f"URL too long: {len(url)} chars"


# ── _best_chrome_target ──────────────────────────────────────────────────

class TestBestChromeTarget:
    def test_returns_string(self):
        target = _best_chrome_target()
        assert isinstance(target, str)
        assert "chrome" in target

    def test_fallback_when_no_browser_type(self):
        with patch.dict("sys.modules", {"curl_cffi.requests": MagicMock(BrowserType=MagicMock(side_effect=TypeError))}):
            # Force re-evaluation by clearing cached result
            # When BrowserType iteration fails, should still return a fallback
            target = _best_chrome_target()
            assert isinstance(target, str)


# ── _update_features_from_html ───────────────────────────────────────────

class TestUpdateFeaturesFromHtml:
    def test_updates_existing_feature_flags(self):
        """Should update existing FEATURES keys, not add new ones."""
        original = dict(FEATURES)
        try:
            # Use a key that exists in FEATURES
            existing_key = list(FEATURES.keys())[0]
            original_value = FEATURES[existing_key]
            opposite = "false" if original_value else "true"
            html = '"%s":{"value":%s}' % (existing_key, opposite)
            _update_features_from_html(html)
            assert FEATURES[existing_key] != original_value
        finally:
            FEATURES.clear()
            FEATURES.update(original)

    def test_does_not_add_new_keys(self):
        """Should never add keys not already in FEATURES (prevents URL bloat)."""
        original = dict(FEATURES)
        try:
            html = '"responsive_web_brand_new_feature":{"value":true}'
            _update_features_from_html(html)
            assert "responsive_web_brand_new_feature" not in FEATURES
        finally:
            FEATURES.clear()
            FEATURES.update(original)

    def test_handles_empty_html(self):
        _update_features_from_html("")

    def test_handles_malformed_html(self):
        _update_features_from_html("not json at all {{{")


# ── TwitterClient._build_headers ─────────────────────────────────────────

class TestBuildHeaders:
    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_required_headers_present(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip init"))

        client = TwitterClient.__new__(TwitterClient)
        client._auth_token = "test_token"
        client._ct0 = "test_ct0"
        client._cookie_string = None
        client._request_delay = 2.5
        client._max_retries = 3
        client._retry_base_delay = 5.0
        client._max_count = 200
        client._client_transaction = None
        client._ct_init_attempted = True

        headers = client._build_headers("https://x.com/i/api/graphql/test", "GET")

        assert "Authorization" in headers
        assert "Bearer" in headers["Authorization"]
        assert headers["X-Csrf-Token"] == "test_ct0"
        assert headers["X-Twitter-Auth-Type"] == "OAuth2Session"
        assert "User-Agent" in headers
        assert "sec-ch-ua" in headers

    @patch("twitter_cli.client.get_sec_ch_ua_platform", return_value='"Linux"')
    @patch("twitter_cli.client.get_sec_ch_ua_platform_version", return_value='""')
    @patch("twitter_cli.client.get_sec_ch_ua_arch", return_value='"x86"')
    @patch("twitter_cli.client.get_accept_language", return_value="zh-CN,zh;q=0.9,en;q=0.8")
    @patch("twitter_cli.client.get_twitter_client_language", return_value="zh")
    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_cookie_string_used_when_available(
        self,
        mock_ct_headers,
        mock_session,
        mock_client_language,
        mock_accept_language,
        mock_arch,
        mock_platform_version,
        mock_platform,
    ):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._auth_token = "token"
        client._ct0 = "ct0"
        client._cookie_string = "auth_token=x; ct0=y; other=z"
        client._request_delay = 2.5
        client._max_retries = 3
        client._retry_base_delay = 5.0
        client._max_count = 200
        client._client_transaction = None
        client._ct_init_attempted = True

        headers = client._build_headers()
        assert headers["Cookie"] == "auth_token=x; ct0=y; other=z"
        assert headers["X-Twitter-Client-Language"] == "zh"
        assert headers["Accept-Language"] == "zh-CN,zh;q=0.9,en;q=0.8"
        assert headers["sec-ch-ua-platform"] == '"Linux"'
        assert headers["sec-ch-ua-arch"] == '"x86"'
        assert headers["sec-ch-ua-platform-version"] == '""'


class TestPaginationBehavior:
    def test_continues_when_cursor_advances_without_new_tweets(self):
        client = TwitterClient.__new__(TwitterClient)
        client._request_delay = 0.0
        client._max_count = 200

        responses = iter(
            [
                {"page": 1},
                {"page": 2},
            ]
        )

        def _graphql_get(operation_name, variables, features, field_toggles=None):
            return next(responses)

        def _parse_timeline_response(data, get_instructions):
            if data["page"] == 1:
                return [], "cursor-2"
            return [MagicMock(id="tweet-1")], None

        client._graphql_get = _graphql_get

        with patch('twitter_cli.client.parse_timeline_response', side_effect=_parse_timeline_response):
            tweets = client._fetch_timeline("HomeTimeline", 1, lambda data: data)

        assert [tweet.id for tweet in tweets] == ["tweet-1"]

    def test_stops_when_cursor_does_not_advance(self):
        client = TwitterClient.__new__(TwitterClient)
        client._request_delay = 0.0
        client._max_count = 200

        calls = []

        def _graphql_get(operation_name, variables, features, field_toggles=None):
            calls.append(variables.get("cursor"))
            return {"page": len(calls)}

        client._graphql_get = _graphql_get

        with patch('twitter_cli.client.parse_timeline_response', return_value=([], "cursor-same")):
            tweets = client._fetch_timeline("HomeTimeline", 1, lambda data: data)

        assert tweets == []
        assert calls == [None, "cursor-same"]

    def test_user_list_continues_when_cursor_advances_without_new_users(self):
        client = TwitterClient.__new__(TwitterClient)
        client._request_delay = 0.0
        client._max_count = 200

        responses = iter(
            [
                {"page": 1},
                {"page": 2},
            ]
        )

        def _graphql_get(operation_name, variables, features):
            return next(responses)

        def _parse_user_result(data):
            return MagicMock(id=data["id"], screen_name=data["screen_name"])

        def _get_instructions(data):
            if data["page"] == 1:
                return [
                    {"entries": [{"content": {"entryType": "TimelineTimelineCursor", "cursorType": "Bottom", "value": "cursor-2"}}]}
                ]
            return [
                {
                    "entries": [
                        {
                            "content": {
                                "entryType": "TimelineTimelineItem",
                                "itemContent": {"user_results": {"result": {"id": "user-1", "screen_name": "alice"}}},
                            }
                        }
                    ]
                }
            ]

        client._graphql_get = _graphql_get

        with patch('twitter_cli.client.parse_user_result', side_effect=_parse_user_result):
            users = client._fetch_user_list("Followers", "1", 1, _get_instructions)

        assert [user.screen_name for user in users] == ["alice"]


# ── TwitterClient._parse_tweet_result ─────────────────────────────────────

class TestParseTweetResult:
    SAMPLE_TWEET_RESULT = {
        "__typename": "Tweet",
        "rest_id": "1234567890",
        "core": {
            "user_results": {
                "result": {
                    "rest_id": "user123",
                    "core": {"name": "Test User", "screen_name": "testuser"},
                    "legacy": {
                        "name": "Test User",
                        "screen_name": "testuser",
                        "verified": False,
                        "profile_image_url_https": "https://img.com/avatar.jpg",
                    },
                    "is_blue_verified": True,
                }
            }
        },
        "legacy": {
            "full_text": "Hello world! This is a test tweet.",
            "created_at": "Sat Mar 08 12:00:00 +0000 2026",
            "favorite_count": 100,
            "retweet_count": 25,
            "reply_count": 5,
            "quote_count": 3,
            "bookmark_count": 10,
            "lang": "en",
            "entities": {"urls": []},
        },
        "views": {"count": "5000"},
    }

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_parses_basic_tweet(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        tweet = parse_tweet_result(copy.deepcopy(self.SAMPLE_TWEET_RESULT))
        assert tweet is not None
        assert tweet.id == "1234567890"
        assert tweet.text == "Hello world! This is a test tweet."
        assert tweet.author.screen_name == "testuser"
        assert tweet.author.verified is True  # is_blue_verified
        assert tweet.metrics.likes == 100
        assert tweet.metrics.views == 5000
        assert tweet.lang == "en"
        assert tweet.is_retweet is False

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_parses_tombstone_returns_none(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        result = {"__typename": "TweetTombstone"}
        assert parse_tweet_result(result) is None

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_parses_visibility_wrapper(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        wrapped = {
            "__typename": "TweetWithVisibilityResults",
            "tweet": copy.deepcopy(self.SAMPLE_TWEET_RESULT),
        }
        tweet = parse_tweet_result(wrapped)
        assert tweet is not None
        assert tweet.id == "1234567890"

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_depth_limit(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        assert parse_tweet_result(self.SAMPLE_TWEET_RESULT, depth=3) is None

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_article_atomic_markdown_entity_preserves_fenced_code_block(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        result = copy.deepcopy(self.SAMPLE_TWEET_RESULT)
        result["article"] = {
            "article_results": {
                "result": {
                    "title": "Article title",
                    "content_state": {
                        "blocks": [
                            {"key": "a", "type": "unstyled", "text": "Intro", "entityRanges": []},
                            {"key": "b", "type": "atomic", "text": " ", "entityRanges": [{"offset": 0, "length": 1, "key": 4}]},
                            {"key": "c", "type": "unstyled", "text": "Outro", "entityRanges": []},
                        ],
                        "entityMap": [
                            {
                                "key": "4",
                                "value": {
                                    "type": "MARKDOWN",
                                    "data": {
                                        "markdown": "```markdown\nconst answer = 42;\n```"
                                    },
                                },
                            }
                        ],
                    },
                }
            }
        }

        tweet = parse_tweet_result(result)
        assert tweet is not None
        assert tweet.article_text == "Intro\n\n```markdown\nconst answer = 42;\n```\n\nOutro"

    @patch("twitter_cli.client._get_cffi_session")
    @patch("twitter_cli.client._gen_ct_headers", return_value={})
    def test_article_real_shape_hooeem_like_payload_keeps_embedded_markdown_blocks(self, mock_ct_headers, mock_session):
        mock_session.return_value = MagicMock()
        mock_session.return_value.get = MagicMock(side_effect=Exception("skip"))

        client = TwitterClient.__new__(TwitterClient)
        client._ct_init_attempted = True
        client._client_transaction = None

        result = copy.deepcopy(self.SAMPLE_TWEET_RESULT)
        result["article"] = {
            "article_results": {
                "result": {
                    "title": "I want to become a Claude architect (full course).",
                    "content_state": {
                        "blocks": [
                            {"key": "a", "type": "unstyled", "text": "If you have no idea how to get started go to Claude and paste this prompt which will help you with domain 1:", "entityRanges": []},
                            {"key": "b", "type": "atomic", "text": " ", "entityRanges": [{"offset": 0, "length": 1, "key": 4}]},
                            {"key": "c", "type": "unstyled", "text": "What to build to learn: A multi-tool agent with 3-4 MCP tools.", "entityRanges": []},
                            {"key": "d", "type": "atomic", "text": " ", "entityRanges": [{"offset": 0, "length": 1, "key": 5}]},
                            {"key": "e", "type": "unstyled", "text": "Done.", "entityRanges": []},
                        ],
                        "entityMap": [
                            {
                                "key": "4",
                                "value": {
                                    "type": "MARKDOWN",
                                    "data": {
                                        "markdown": "```markdown\nYou are an expert instructor teaching Domain 1.\n```"
                                    },
                                },
                            },
                            {
                                "key": "5",
                                "value": {
                                    "type": "MARKDOWN",
                                    "data": {
                                        "markdown": "```markdown\nBest for: predictable, structured tasks like code reviews.\n```"
                                    },
                                },
                            },
                        ],
                    },
                }
            }
        }

        tweet = parse_tweet_result(result)
        assert tweet is not None
        assert tweet.article_text == (
            "If you have no idea how to get started go to Claude and paste this prompt which will help you with domain 1:\n\n"
            "```markdown\nYou are an expert instructor teaching Domain 1.\n```\n\n"
            "What to build to learn: A multi-tool agent with 3-4 MCP tools.\n\n"
            "```markdown\nBest for: predictable, structured tasks like code reviews.\n```\n\n"
            "Done."
        )


# ── TwitterAPIError ──────────────────────────────────────────────────────

class TestTwitterAPIError:
    def test_stores_status_code(self):
        err = TwitterAPIError(429, "Rate limited")
        assert err.status_code == 429
        assert "Rate limited" in str(err)

    def test_is_runtime_error(self):
        err = TwitterAPIError(500, "Server error")
        assert isinstance(err, RuntimeError)


class TestParseUserResult:
    def test_coerces_count_fields_to_int(self):
        user = parse_user_result(
            {
                "rest_id": "user-1",
                "legacy": {
                    "name": "Alice",
                    "screen_name": "alice",
                    "followers_count": "1,234",
                    "friends_count": "56",
                    "statuses_count": "78.9",
                    "favourites_count": None,
                },
            }
        )

        assert user is not None
        assert user.followers_count == 1234
        assert user.following_count == 56
        assert user.tweets_count == 78
        assert user.likes_count == 0


# ── upload_media ─────────────────────────────────────────────────────────

class TestUploadMedia:
    """Tests for TwitterClient.upload_media()."""

    def _make_client(self):
        client = TwitterClient.__new__(TwitterClient)
        client._auth_token = "tok"
        client._ct0 = "ct0"
        client._cookie_string = None
        client._request_delay = 0
        client._max_retries = 3
        client._retry_base_delay = 5.0
        client._max_count = 200
        client._client_transaction = None
        client._ct_init_attempted = True
        return client

    @patch("twitter_cli.client._get_cffi_session")
    def test_upload_media_init_append_finalize(self, mock_session, tmp_path):
        """Happy path: INIT → APPEND → FINALIZE returns media_id."""
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # fake JPEG

        mock_resp_init = MagicMock()
        mock_resp_init.status_code = 200
        mock_resp_init.text = '{"media_id_string": "12345"}'

        mock_resp_append = MagicMock()
        mock_resp_append.status_code = 200
        mock_resp_append.text = ""

        mock_resp_finalize = MagicMock()
        mock_resp_finalize.status_code = 200
        mock_resp_finalize.text = '{"media_id_string": "12345"}'

        sess = MagicMock()
        sess.post = MagicMock(side_effect=[mock_resp_init, mock_resp_append, mock_resp_finalize])
        mock_session.return_value = sess

        client = self._make_client()
        media_id = client.upload_media(str(img))

        assert media_id == "12345"
        assert sess.post.call_count == 3

    def test_upload_media_file_not_found(self):
        from twitter_cli.exceptions import MediaUploadError

        client = self._make_client()
        with pytest.raises(MediaUploadError, match="File not found"):
            client.upload_media("/nonexistent/file.jpg")

    def test_upload_media_too_large(self, tmp_path):
        from twitter_cli.exceptions import MediaUploadError

        img = tmp_path / "big.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (6 * 1024 * 1024))  # 6 MB

        client = self._make_client()
        with pytest.raises(MediaUploadError, match="File too large"):
            client.upload_media(str(img))

    def test_upload_media_unsupported_format(self, tmp_path):
        from twitter_cli.exceptions import MediaUploadError

        txt = tmp_path / "notes.txt"
        txt.write_text("hello")

        client = self._make_client()
        with pytest.raises(MediaUploadError, match="Unsupported image format"):
            client.upload_media(str(txt))


# ── create_tweet with media_ids ──────────────────────────────────────────

class TestCreateTweetWithMedia:
    """Tests that media_ids are correctly passed into CreateTweet variables."""

    @patch("twitter_cli.client._get_cffi_session")
    def test_create_tweet_with_media_ids(self, mock_session):
        sess = MagicMock()
        mock_session.return_value = sess

        client = TwitterClient.__new__(TwitterClient)
        client._auth_token = "tok"
        client._ct0 = "ct0"
        client._cookie_string = None
        client._request_delay = 0
        client._max_retries = 0
        client._retry_base_delay = 0
        client._max_count = 200
        client._client_transaction = None
        client._ct_init_attempted = True

        captured_body = {}

        def mock_graphql_post(operation_name, variables, features=None):
            captured_body.update(variables)
            return {"data": {"create_tweet": {"tweet_results": {"result": {"rest_id": "99"}}}}}

        client._graphql_post = mock_graphql_post

        result = client.create_tweet("test", media_ids=["111", "222"])
        assert result == "99"

        entities = captured_body["media"]["media_entities"]
        assert len(entities) == 2
        assert entities[0]["media_id"] == "111"
        assert entities[1]["media_id"] == "222"

    @patch("twitter_cli.client._get_cffi_session")
    def test_create_tweet_without_media_ids(self, mock_session):
        sess = MagicMock()
        mock_session.return_value = sess

        client = TwitterClient.__new__(TwitterClient)
        client._auth_token = "tok"
        client._ct0 = "ct0"
        client._cookie_string = None
        client._request_delay = 0
        client._max_retries = 0
        client._retry_base_delay = 0
        client._max_count = 200
        client._client_transaction = None
        client._ct_init_attempted = True

        captured_body = {}

        def mock_graphql_post(operation_name, variables, features=None):
            captured_body.update(variables)
            return {"data": {"create_tweet": {"tweet_results": {"result": {"rest_id": "88"}}}}}

        client._graphql_post = mock_graphql_post

        result = client.create_tweet("no media")
        assert result == "88"
        assert captured_body["media"]["media_entities"] == []

