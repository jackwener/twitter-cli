from __future__ import annotations

import json

import pytest

from twitter_cli.api_client import TwitterAPIv2Client
from twitter_cli.exceptions import AuthenticationError, NotFoundError


class DummyResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class DummySession:
    def __init__(self, responses: list[DummyResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict | None, str | None, dict]] = []

    def get(self, url: str, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params, None, headers or {}))
        return self._responses.pop(0)

    def post(self, url: str, headers=None, params=None, data=None, timeout=None):
        self.calls.append(("POST", url, params, data, headers or {}))
        return self._responses.pop(0)

    def delete(self, url: str, headers=None, params=None, timeout=None):
        self.calls.append(("DELETE", url, params, None, headers or {}))
        return self._responses.pop(0)


def test_api_client_fetch_search_parses_expansions(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "1",
                            "text": "hello world",
                            "author_id": "u1",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "lang": "en",
                            "public_metrics": {
                                "like_count": 1,
                                "retweet_count": 2,
                                "reply_count": 3,
                                "quote_count": 4,
                            },
                            "entities": {
                                "urls": [{"expanded_url": "https://example.com"}],
                            },
                            "attachments": {"media_keys": ["m1"]},
                            "referenced_tweets": [{"type": "quoted", "id": "2"}],
                        }
                    ],
                    "includes": {
                        "users": [
                            {"id": "u1", "name": "Alice", "username": "alice", "verified": True},
                            {"id": "u2", "name": "Bob", "username": "bob"},
                        ],
                        "media": [
                            {
                                "media_key": "m1",
                                "type": "photo",
                                "url": "https://img.example/photo.jpg",
                                "width": 800,
                                "height": 600,
                            }
                        ],
                        "tweets": [
                            {
                                "id": "2",
                                "text": "quoted tweet",
                                "author_id": "u2",
                                "created_at": "2026-03-07T12:00:00.000Z",
                                "public_metrics": {},
                            }
                        ],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_search("python", count=1, product="Photos")

    assert len(tweets) == 1
    assert tweets[0].author.screen_name == "alice"
    assert tweets[0].media[0].url == "https://img.example/photo.jpg"
    assert tweets[0].quoted_tweet is not None
    assert tweets[0].quoted_tweet.author.screen_name == "bob"
    assert tweets[0].created_at == "2026-03-08T12:00:00.000Z"
    assert session.calls[0][0] == "GET"
    assert session.calls[0][2]["query"] == "python has:images"
    assert session.calls[0][2]["sort_order"] == "recency"


def test_api_client_fetch_search_all_scope_uses_full_archive_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession([DummyResponse(200, {"data": [], "meta": {"result_count": 0}})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_search("python", count=1, scope="all")

    assert tweets == []
    assert session.calls[0][1].endswith("/tweets/search/all")


def test_api_client_fetch_me_requires_user_context(monkeypatch) -> None:
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})

    with pytest.raises(AuthenticationError):
        client.fetch_me()


def test_api_client_create_tweet_uses_access_token(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession([DummyResponse(200, {"data": {"id": "123", "text": "hi"}})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweet_id = client.create_tweet("hi")

    assert tweet_id == "123"
    method, url, _params, data, headers = session.calls[0]
    assert method == "POST"
    assert url.endswith("/tweets")
    assert headers["Authorization"] == "Bearer access-token"
    assert json.loads(data) == {"text": "hi"}


def test_api_client_create_tweet_supports_media_ids(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession([DummyResponse(200, {"data": {"id": "123", "text": "hi"}})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweet_id = client.create_tweet("hi", media_ids=["m1", "m2"])

    assert tweet_id == "123"
    assert json.loads(session.calls[0][3]) == {
        "text": "hi",
        "media": {"media_ids": ["m1", "m2"]},
    }


def test_api_client_fetch_user_404_maps_to_not_found(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    session = DummySession([DummyResponse(404, {"title": "Not Found Error", "detail": "User not found"})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})

    with pytest.raises(NotFoundError, match="User not found"):
        client.fetch_user("missing")


def test_api_client_fetch_home_timeline_uses_reverse_chronological_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(200, {"data": {"id": "me", "name": "Alice", "username": "alice"}}),
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "10",
                            "text": "timeline post",
                            "author_id": "u1",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "public_metrics": {"like_count": 2},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                    "meta": {"result_count": 1},
                },
            ),
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_home_timeline(count=1)

    assert [tweet.id for tweet in tweets] == ["10"]
    assert session.calls[0][1].endswith("/users/me")
    assert session.calls[1][1].endswith("/users/me/timelines/reverse_chronological")


def test_api_client_fetch_mentions_uses_mentions_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "12",
                            "text": "@alice hi",
                            "author_id": "u2",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "public_metrics": {"like_count": 1},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u2", "name": "Bob", "username": "bob"}],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_mentions("42", count=1)

    assert [tweet.id for tweet in tweets] == ["12"]
    assert session.calls[0][1].endswith("/users/42/mentions")


def test_api_client_fetch_list_timeline_uses_lists_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "11",
                            "text": "from a list",
                            "author_id": "u1",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "public_metrics": {"like_count": 5},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_list_timeline("123", count=1)

    assert [tweet.id for tweet in tweets] == ["11"]
    assert session.calls[0][1].endswith("/lists/123/tweets")


def test_api_client_fetch_tweet_detail_uses_lookup_and_search(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                        "data": {
                            "id": "123",
                            "text": "root",
                            "author_id": "u1",
                            "conversation_id": "123",
                            "created_at": "2026-03-12T12:00:00.000Z",
                            "public_metrics": {"reply_count": 1},
                        },
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                },
            ),
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "123",
                            "text": "root",
                            "author_id": "u1",
                            "created_at": "2026-03-12T12:00:00.000Z",
                            "public_metrics": {"reply_count": 1},
                        },
                        {
                            "id": "124",
                            "text": "reply",
                            "author_id": "u2",
                            "created_at": "2026-03-12T12:05:00.000Z",
                            "public_metrics": {"like_count": 1},
                        },
                    ],
                    "includes": {
                        "users": [
                            {"id": "u1", "name": "Alice", "username": "alice"},
                            {"id": "u2", "name": "Bob", "username": "bob"},
                        ],
                    },
                    "meta": {"result_count": 2},
                },
            ),
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_tweet_detail("123", count=5)

    assert [tweet.id for tweet in tweets] == ["123", "124"]
    assert session.calls[0][1].endswith("/tweets/123")
    assert session.calls[1][1].endswith("/tweets/search/recent")
    assert session.calls[1][2]["query"] == "conversation_id:123"


def test_api_client_fetch_tweet_detail_auto_tries_full_archive_for_old_posts(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": {
                        "id": "123",
                        "text": "root",
                        "author_id": "u1",
                        "conversation_id": "123",
                        "created_at": "2026-01-01T12:00:00.000Z",
                        "public_metrics": {"reply_count": 1},
                    },
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                },
            ),
            DummyResponse(403, {"detail": "forbidden"}),
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "124",
                            "text": "recent reply",
                            "author_id": "u2",
                            "created_at": "2026-03-08T12:05:00.000Z",
                            "public_metrics": {"like_count": 1},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u2", "name": "Bob", "username": "bob"}],
                    },
                    "meta": {"result_count": 1},
                },
            ),
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_tweet_detail("123", count=5, reply_scope="auto")

    assert [tweet.id for tweet in tweets] == ["123", "124"]
    assert session.calls[1][1].endswith("/tweets/search/all")
    assert session.calls[2][1].endswith("/tweets/search/recent")


def test_api_client_fetch_bookmarks_requires_user_context(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": {"id": "me", "name": "Alice", "username": "alice"},
                },
            ),
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "21",
                            "text": "saved post",
                            "author_id": "u2",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "public_metrics": {"bookmark_count": 3},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u2", "name": "Bob", "username": "bob"}],
                    },
                    "meta": {"result_count": 1},
                },
            ),
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_bookmarks(count=1)

    assert [tweet.id for tweet in tweets] == ["21"]
    assert session.calls[0][1].endswith("/users/me")
    assert session.calls[1][1].endswith("/users/me/bookmarks")
    assert session.calls[1][4]["Authorization"] == "Bearer access-token"


def test_api_client_fetch_article_parses_article_fields(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": {
                        "id": "55",
                        "text": "article teaser",
                        "author_id": "u1",
                        "created_at": "2026-03-08T12:00:00.000Z",
                        "public_metrics": {"like_count": 4},
                        "article": {"title": "Title", "text": "Body text"},
                    },
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweet = client.fetch_article("55")

    assert tweet.id == "55"
    assert tweet.article_title == "Title"
    assert tweet.article_text == "Body text"


def test_api_client_fetch_article_merges_article_media(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": {
                        "id": "56",
                        "text": "article teaser",
                        "author_id": "u1",
                        "created_at": "2026-03-08T12:00:00.000Z",
                        "public_metrics": {},
                        "article": {"title": "Title", "text": "Body text", "cover_media_key": "m2"},
                    },
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                        "media": [
                            {
                                "media_key": "m2",
                                "type": "photo",
                                "url": "https://img.example/article.jpg",
                                "width": 1280,
                                "height": 720,
                            }
                        ],
                    },
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweet = client.fetch_article("56")

    assert tweet.media[0].url == "https://img.example/article.jpg"


def test_api_client_fetch_user_likes_uses_liked_tweets_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [
                        {
                            "id": "31",
                            "text": "liked post",
                            "author_id": "u3",
                            "created_at": "2026-03-08T12:00:00.000Z",
                            "public_metrics": {"like_count": 7},
                        }
                    ],
                    "includes": {
                        "users": [{"id": "u3", "name": "Cara", "username": "cara"}],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    tweets = client.fetch_user_likes("42", count=1)

    assert [tweet.id for tweet in tweets] == ["31"]
    assert session.calls[0][1].endswith("/users/42/liked_tweets")


def test_api_client_fetch_list_returns_metadata(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": {
                        "id": "200",
                        "name": "Python",
                        "description": "Language news",
                        "owner_id": "u1",
                        "follower_count": 12,
                        "member_count": 34,
                        "private": False,
                        "created_at": "2026-03-01T00:00:00.000Z",
                    },
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    twitter_list = client.fetch_list("200")

    assert twitter_list.name == "Python"
    assert twitter_list.owner_screen_name == "alice"
    assert session.calls[0][1].endswith("/lists/200")


def test_api_client_fetch_owned_lists_uses_owned_lists_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    monkeypatch.delenv("TWITTER_API_ACCESS_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [{"id": "201", "name": "Owned", "owner_id": "u1"}],
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    twitter_lists = client.fetch_owned_lists("42", count=1)

    assert [twitter_list.id for twitter_list in twitter_lists] == ["201"]
    assert session.calls[0][1].endswith("/users/42/owned_lists")


def test_api_client_fetch_followed_lists_requires_user_context(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "data": [{"id": "202", "name": "Followed", "owner_id": "u1"}],
                    "includes": {
                        "users": [{"id": "u1", "name": "Alice", "username": "alice"}],
                    },
                    "meta": {"result_count": 1},
                },
            )
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    twitter_lists = client.fetch_followed_lists("42", count=1)

    assert [twitter_list.id for twitter_list in twitter_lists] == ["202"]
    assert session.calls[0][1].endswith("/users/42/followed_lists")
    assert session.calls[0][4]["Authorization"] == "Bearer access-token"


def test_api_client_bookmark_write_endpoints_use_user_context(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    session = DummySession(
        [
            DummyResponse(200, {"data": {"id": "me", "name": "Alice", "username": "alice"}}),
            DummyResponse(200, {"data": {"bookmarked": True}}),
            DummyResponse(200, {"data": {"removed": True}}),
        ]
    )
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})

    assert client.bookmark_tweet("99") is True
    assert client.unbookmark_tweet("99") is True
    assert session.calls[1][0] == "POST"
    assert session.calls[1][1].endswith("/users/me/bookmarks")
    assert json.loads(session.calls[1][3]) == {"tweet_id": "99"}
    assert session.calls[2][0] == "DELETE"
    assert session.calls[2][1].endswith("/users/me/bookmarks/99")


def test_api_client_upload_media_uses_v2_media_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TWITTER_API_ACCESS_TOKEN", "access-token")
    monkeypatch.delenv("TWITTER_API_BEARER_TOKEN", raising=False)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    session = DummySession([DummyResponse(200, {"data": {"id": "m1"}})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})
    media_id = client.upload_media(str(image_path))

    assert media_id == "m1"
    assert session.calls[0][1].endswith("/media/upload")
    payload = json.loads(session.calls[0][3])
    assert payload["media_category"] == "tweet_image"
    assert payload["media_type"] == "image/png"
