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


def test_api_client_fetch_user_404_maps_to_not_found(monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_API_BEARER_TOKEN", "bearer-token")
    session = DummySession([DummyResponse(404, {"title": "Not Found Error", "detail": "User not found"})])
    monkeypatch.setattr("twitter_cli.api_client._get_api_session", lambda: session)

    client = TwitterAPIv2Client({"requestDelay": 0, "maxRetries": 1})

    with pytest.raises(NotFoundError, match="User not found"):
        client.fetch_user("missing")
