from __future__ import annotations

import json
import time

from click.testing import CliRunner
import pytest
from rich.console import Console
from twitter_cli.exceptions import UnsupportedFeatureError
import yaml

from twitter_cli.cli import cli
from twitter_cli.formatter import article_to_markdown, print_tweet_table
from twitter_cli.models import Author, Metrics, Tweet, UserProfile
from twitter_cli.serialization import tweets_to_json


def test_cli_user_command_works_with_client_factory(monkeypatch) -> None:
    class FakeClient:
        def fetch_user(self, screen_name: str) -> UserProfile:
            return UserProfile(id="1", name="Alice", screen_name=screen_name)

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()
    result = runner.invoke(cli, ["user", "alice"])
    assert result.exit_code == 0




def test_cli_feed_json_input_path(tmp_path, tweet_factory) -> None:
    json_path = tmp_path / "tweets.json"
    json_path.write_text(tweets_to_json([tweet_factory("1")]), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["feed", "--input", str(json_path), "--json"])
    assert result.exit_code == 0
    assert '"id": "1"' in result.output


def test_cli_feed_input_accepts_structured_json_envelope(tmp_path, tweet_factory) -> None:
    json_path = tmp_path / "tweets.json"
    json_path.write_text(
        (
            "{\n"
            '  "ok": true,\n'
            '  "schema_version": "1",\n'
            '  "data": %s\n'
            "}\n"
        )
        % tweets_to_json([tweet_factory("1")]),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["feed", "--input", str(json_path), "--json"])
    assert result.exit_code == 0
    assert '"id": "1"' in result.output


def test_print_tweet_table_truncates_text_by_default(tweet_factory) -> None:
    long_text = "A" * 140
    console = Console(record=True, width=400)

    print_tweet_table([tweet_factory("1", text=long_text)], console=console)

    output = console.export_text()
    assert ("A" * 117 + "...") in output


def test_print_tweet_table_full_text_shows_untruncated_text(tweet_factory) -> None:
    long_text = "B" * 140
    console = Console(record=True, width=400)

    print_tweet_table([tweet_factory("1", text=long_text)], console=console, full_text=True)

    output = console.export_text()
    assert long_text in output
    assert ("B" * 117 + "...") not in output


@pytest.mark.parametrize(
    "args",
    [
        ["favorites"],
        ["bookmarks"],
        ["search", "x"],
        ["user-posts", "alice"],
        ["likes", "alice"],
        ["list", "123"],
    ],
)
def test_cli_commands_wrap_client_creation_errors(monkeypatch, args) -> None:
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    runner = CliRunner()

    result = runner.invoke(cli, args)

    assert result.exit_code == 1
    assert "boom" in result.output
    assert type(result.exception).__name__ == "SystemExit"


def test_cli_user_error_yaml(monkeypatch) -> None:
    from twitter_cli.exceptions import NotFoundError
    monkeypatch.setenv("OUTPUT", "auto")
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: (_ for _ in ()).throw(NotFoundError("User not found")),
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["user", "alice", "--yaml"])

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_cli_tweet_accepts_shared_url_with_query(monkeypatch) -> None:
    class FakeClient:
        def fetch_tweet_detail(self, tweet_id: str, max_count: int):
            assert tweet_id == "12345"
            assert max_count == 50
            return []

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda: {"fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["tweet", "https://x.com/user/status/12345?s=20"])

    assert result.exit_code == 0


def test_cli_article_accepts_article_url_and_json(monkeypatch) -> None:
    class FakeClient:
        def fetch_article(self, tweet_id: str) -> Tweet:
            assert tweet_id == "12345"
            return Tweet(
                id="12345",
                text="https://t.co/article",
                author=Author(id="u1", name="Alice", screen_name="alice"),
                metrics=Metrics(likes=1, retweets=2, replies=3, views=4, bookmarks=5),
                created_at="2026-03-11",
                article_title="Title",
                article_text="Hello\n\n## Section",
            )

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda: {"fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["article", "https://x.com/user/article/12345?s=20", "--json"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["data"]["id"] == "12345"
    assert payload["data"]["articleTitle"] == "Title"
    assert "Hello" in payload["data"]["articleText"]


def test_cli_article_markdown_output_and_save(monkeypatch, tmp_path) -> None:
    article = Tweet(
        id="12345",
        text="https://t.co/article",
        author=Author(id="u1", name="Alice", screen_name="alice"),
        metrics=Metrics(likes=1, retweets=2, replies=3, views=4, bookmarks=5),
        created_at="2026-03-11",
        article_title="Title",
        article_text="Hello\n\n## Section",
    )

    class FakeClient:
        def fetch_article(self, tweet_id: str) -> Tweet:
            assert tweet_id == "12345"
            return article

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr("twitter_cli.cli.load_config", lambda: {})
    output_path = tmp_path / "article.md"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["article", "12345", "--markdown", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert result.output == article_to_markdown(article)
    assert output_path.read_text(encoding="utf-8") == article_to_markdown(article)


def test_cli_article_markdown_overrides_auto_structured_output(monkeypatch) -> None:
    article = Tweet(
        id="12345",
        text="https://t.co/article",
        author=Author(id="u1", name="Alice", screen_name="alice"),
        metrics=Metrics(likes=1, retweets=2, replies=3, views=4, bookmarks=5),
        created_at="2026-03-11",
        article_title="Title",
        article_text="Hello\n\n## Section",
    )

    class FakeClient:
        def fetch_article(self, tweet_id: str) -> Tweet:
            assert tweet_id == "12345"
            return article

    monkeypatch.setenv("OUTPUT", "auto")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr("twitter_cli.cli.load_config", lambda: {})
    runner = CliRunner()

    result = runner.invoke(cli, ["article", "12345", "--markdown"])

    assert result.exit_code == 0
    assert result.output == article_to_markdown(article)


def test_cli_article_rejects_compact_mode() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["-c", "article", "12345"])

    assert result.exit_code == 2
    assert "does not support --compact" in result.output


def test_cli_bookmark_alias_works(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def bookmark_tweet(self, tweet_id: str) -> bool:
            calls.append(tweet_id)
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["bookmark", "123"])

    assert result.exit_code == 0
    assert calls == ["123"]


def test_cli_whoami_command(monkeypatch) -> None:
    from twitter_cli.models import UserProfile

    class FakeClient:
        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="Test User", screen_name="testuser")

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["whoami"])
    assert result.exit_code == 0

    result_json = runner.invoke(cli, ["whoami", "--json"])
    assert result_json.exit_code == 0
    payload = yaml.safe_load(runner.invoke(cli, ["whoami", "--yaml"]).output)
    assert payload["ok"] is True
    assert payload["data"]["user"]["username"] == "testuser"


def test_cli_status_explicit_api_auth_mode(monkeypatch) -> None:
    class FakeAPIClient:
        def __init__(self, rate_limit_config=None) -> None:
            assert isinstance(rate_limit_config, dict)

        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="API User", screen_name="apiuser")

    monkeypatch.setattr("twitter_cli.cli.TwitterAPIv2Client", FakeAPIClient)
    runner = CliRunner()

    result = runner.invoke(cli, ["--auth-mode", "api", "status", "--json"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["data"]["user"]["username"] == "apiuser"


def test_cli_auto_auth_prefers_api_when_credentials_present(monkeypatch) -> None:
    class FakeAPIClient:
        def __init__(self, rate_limit_config=None) -> None:
            pass

        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="Auto API", screen_name="autoapi")

    monkeypatch.setattr("twitter_cli.cli.has_api_credentials", lambda: True)
    monkeypatch.setattr("twitter_cli.cli.TwitterAPIv2Client", FakeAPIClient)
    monkeypatch.setattr(
        "twitter_cli.cli.get_cookies",
        lambda: pytest.fail("cookie auth should not be used when API credentials are present"),
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["status", "--json"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["data"]["user"]["username"] == "autoapi"


def test_cli_api_mode_unsupported_feed_returns_structured_error(monkeypatch) -> None:
    class FakeAPIClient:
        def __init__(self, rate_limit_config=None) -> None:
            pass

        def fetch_home_timeline(self, count: int):
            raise UnsupportedFeatureError("feed unsupported in api mode")

    monkeypatch.setattr("twitter_cli.cli.TwitterAPIv2Client", FakeAPIClient)
    monkeypatch.setattr("twitter_cli.cli.load_config", lambda: {"fetch": {"count": 5}, "filter": {}, "rateLimit": {}})
    runner = CliRunner()

    result = runner.invoke(cli, ["--auth-mode", "api", "feed", "--json"])

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unsupported_operation"


def test_cli_whoami_auto_yaml(monkeypatch) -> None:
    class FakeClient:
        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="Test User", screen_name="testuser")

    monkeypatch.setenv("OUTPUT", "auto")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["whoami"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["schema_version"] == "1"
    assert payload["data"]["user"]["username"] == "testuser"


def test_cli_status_auto_yaml(monkeypatch) -> None:
    class FakeClient:
        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="Test User", screen_name="testuser")

    monkeypatch.setenv("OUTPUT", "auto")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["schema_version"] == "1"
    assert payload["data"]["authenticated"] is True
    assert payload["data"]["user"]["username"] == "testuser"


def test_cli_reply_command(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def create_tweet(self, text: str, reply_to_id=None, media_ids=None) -> str:
            calls.append({"text": text, "reply_to_id": reply_to_id})
            return "999"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["reply", "12345", "Nice tweet!"])
    assert result.exit_code == 0
    assert calls[0]["reply_to_id"] == "12345"
    assert calls[0]["text"] == "Nice tweet!"


def test_cli_quote_command(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def quote_tweet(self, tweet_id: str, text: str, media_ids=None) -> str:
            calls.append({"tweet_id": tweet_id, "text": text})
            return "888"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["quote", "12345", "Interesting!"])
    assert result.exit_code == 0
    assert calls[0]["tweet_id"] == "12345"
    assert calls[0]["text"] == "Interesting!"


def test_cli_post_json_output(monkeypatch) -> None:
    class FakeClient:
        def create_tweet(self, text: str, reply_to_id=None, media_ids=None) -> str:
            assert text == "hello"
            assert reply_to_id is None
            return "999"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "hello", "--json"])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["data"]["action"] == "post"
    assert payload["data"]["id"] == "999"


def test_cli_like_yaml_output(monkeypatch) -> None:
    class FakeClient:
        def like_tweet(self, tweet_id: str) -> bool:
            assert tweet_id == "123"
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["like", "123", "--yaml"])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["data"]["action"] == "liking_tweet"
    assert payload["data"]["id"] == "123"


def test_cli_follow_json_output(monkeypatch) -> None:
    class FakeClient:
        def resolve_user_id(self, identifier: str) -> str:
            assert identifier == "alice"
            return "42"

        def follow_user(self, user_id: str) -> bool:
            assert user_id == "42"
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["follow", "alice", "--json"])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["data"]["action"] == "follow"
    assert payload["data"]["userId"] == "42"


def test_cli_follow_command(monkeypatch) -> None:
    actions = []

    class FakeClient:
        def resolve_user_id(self, identifier: str) -> str:
            return "42"

        def follow_user(self, user_id: str) -> bool:
            actions.append(("follow", user_id))
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["follow", "alice"])
    assert result.exit_code == 0
    assert actions == [("follow", "42")]


def test_cli_unfollow_command(monkeypatch) -> None:
    actions = []

    class FakeClient:
        def resolve_user_id(self, identifier: str) -> str:
            return "42"

        def unfollow_user(self, user_id: str) -> bool:
            actions.append(("unfollow", user_id))
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["unfollow", "alice"])
    assert result.exit_code == 0
    assert actions == [("unfollow", "42")]


def test_cli_search_advanced_options(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def fetch_search(self, query: str, count: int, product: str):
            captured["query"] = query
            captured["product"] = product
            return []

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda: {"fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, [
        "search", "python",
        "--from", "elonmusk",
        "--lang", "en",
        "--since", "2026-01-01",
        "--has", "links",
        "--exclude", "retweets",
        "--min-likes", "100",
        "-t", "Latest",
        "--json",
    ])

    assert result.exit_code == 0, f"search failed: {result.output}"
    assert captured["query"] == (
        "python from:elonmusk lang:en since:2026-01-01 "
        "filter:links -filter:retweets min_faves:100"
    )
    assert captured["product"] == "Latest"


def test_cli_search_operators_only_no_query(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def fetch_search(self, query: str, count: int, product: str):
            captured["query"] = query
            return []

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda: {"fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["search", "--from", "bbc", "--json"])
    assert result.exit_code == 0, f"search failed: {result.output}"
    assert captured["query"] == "from:bbc"


def test_cli_search_empty_query_no_options() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["search"])
    assert result.exit_code != 0
    assert "Provide a QUERY" in result.output


def test_cli_search_invalid_date_rejected(monkeypatch) -> None:
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: None)
    runner = CliRunner()

    result = runner.invoke(cli, ["search", "python", "--since", "not-a-date"])
    assert result.exit_code != 0
    assert "--since must be in YYYY-MM-DD format" in result.output


def test_cli_search_rejects_reversed_date_range(monkeypatch) -> None:
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: None)
    runner = CliRunner()

    result = runner.invoke(cli, ["search", "python", "--since", "2026-03-02", "--until", "2026-03-01"])
    assert result.exit_code != 0
    assert "--since must be on or before --until" in result.output


def test_cli_compact_mode(tmp_path, tweet_factory) -> None:
    json_path = tmp_path / "tweets.json"
    json_path.write_text(tweets_to_json([tweet_factory("1")]), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["-c", "feed", "--input", str(json_path)])
    assert result.exit_code == 0
    # Compact output should have "author" field with @ prefix
    assert '"@alice"' in result.output
    # Compact output should NOT have full metrics keys
    assert '"metrics"' not in result.output


def _write_cache(cache_file, tweets, created_at=None):
    """Write a test cache file."""
    if created_at is None:
        created_at = time.time()
    entries = [
        {"index": i + 1, "id": t.id, "author": t.author.screen_name, "text": t.text[:80]}
        for i, t in enumerate(tweets)
    ]
    payload = {"created_at": created_at, "tweets": entries}
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_show_happy_path(monkeypatch, tmp_path, tweet_factory):
    """show <N> resolves cached index and fetches tweet detail."""
    tw = tweet_factory("42", text="hello world")
    cache_file = tmp_path / "last_results.json"
    _write_cache(cache_file, [tweet_factory("10"), tw])  # tw is index 2

    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    class FakeClient:
        def fetch_tweet_detail(self, tweet_id, count):
            assert tweet_id == "42"
            return [tw]

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr("twitter_cli.cli.load_config", lambda: {})

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "2"])
    assert result.exit_code == 0


def test_show_empty_cache(monkeypatch, tmp_path):
    """show fails with a helpful message when no cache exists."""
    cache_file = tmp_path / "last_results.json"
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "1"])
    assert result.exit_code != 0
    assert "No cached results" in result.output


def test_show_out_of_range(monkeypatch, tmp_path, tweet_factory):
    """show fails with out-of-range message when index exceeds cache size."""
    cache_file = tmp_path / "last_results.json"
    _write_cache(cache_file, [tweet_factory("1")])
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "99"])
    assert result.exit_code != 0
    assert "out of range" in result.output
    assert "1" in result.output  # cache has 1 tweet


def test_show_expired_cache(monkeypatch, tmp_path, tweet_factory):
    """show treats an expired cache the same as no cache."""
    cache_file = tmp_path / "last_results.json"
    expired_time = time.time() - 7200  # 2 hours ago
    _write_cache(cache_file, [tweet_factory("1")], created_at=expired_time)
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "1"])
    assert result.exit_code != 0
    assert "No cached results" in result.output


def test_show_rejects_zero_index(monkeypatch, tmp_path):
    """show rejects index=0 because indices are 1-based."""
    cache_file = tmp_path / "last_results.json"
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "0"])
    assert result.exit_code != 0


def test_show_rejects_negative_index(monkeypatch, tmp_path):
    """show rejects negative indices."""
    cache_file = tmp_path / "last_results.json"
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "-1"])
    assert result.exit_code != 0


def test_show_malformed_cache_treated_as_empty(monkeypatch, tmp_path):
    """show handles a corrupted cache file gracefully."""
    cache_file = tmp_path / "last_results.json"
    cache_file.write_text("not valid json{{}", encoding="utf-8")
    monkeypatch.setattr("twitter_cli.cache._CACHE_FILE", cache_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["show", "1"])
    assert result.exit_code != 0
    assert "No cached results" in result.output
