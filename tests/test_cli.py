from __future__ import annotations

from click.testing import CliRunner
import pytest
from rich.console import Console
import yaml

import json

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


def test_get_client_propagates_real_type_error(monkeypatch) -> None:
    def _broken_get_client(config=None, quiet=False):
        raise TypeError("real bug")

    monkeypatch.setattr("twitter_cli.cli._get_client", _broken_get_client)

    with pytest.raises(TypeError, match="real bug"):
        from twitter_cli.cli import _get_client

        _get_client({})


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
    monkeypatch.setenv("OUTPUT", "auto")
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: (_ for _ in ()).throw(RuntimeError("User not found")),
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


# ---------------------------------------------------------------------------
# Dry-run & confirmation tests
# ---------------------------------------------------------------------------


def test_post_dry_run_json(monkeypatch) -> None:
    """--dry-run --json prints payload without envelope, no client created."""
    client_created = []
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: client_created.append(1) or None,
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "hello world", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["action"] == "post"
    assert payload["text"] == "hello world"
    assert "ok" not in payload  # no envelope
    assert client_created == []  # no client created


def test_post_dry_run_plain(monkeypatch) -> None:
    """--dry-run in TTY prints '--- Dry Run ---' + fields."""
    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: None,
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "hello world", "--dry-run"])

    assert result.exit_code == 0
    assert "--- Dry Run ---" in result.output
    assert "Action: post" in result.output
    assert "Text: hello world" in result.output


def test_post_dry_run_with_images(monkeypatch, tmp_path) -> None:
    """Images listed in dry-run output."""
    img1 = tmp_path / "a.png"
    img2 = tmp_path / "b.png"
    img1.write_bytes(b"\x89PNG")
    img2.write_bytes(b"\x89PNG")

    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: None,
    )
    runner = CliRunner()

    result = runner.invoke(
        cli, ["post", "pics", "--dry-run", "-i", str(img1), "-i", str(img2)]
    )

    assert result.exit_code == 0
    assert "--- Dry Run ---" in result.output
    assert "a.png" in result.output
    assert "b.png" in result.output


def test_like_dry_run_yaml(monkeypatch) -> None:
    """--dry-run works on simple write actions via YAML."""
    client_created = []
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: client_created.append(1) or None,
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["like", "456", "--dry-run", "--yaml"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["action"] == "liking_tweet"
    assert payload["id"] == "456"
    assert client_created == []


def test_follow_dry_run(monkeypatch) -> None:
    """--dry-run shows screenName, no user ID resolution."""
    client_created = []
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: client_created.append(1) or None,
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["follow", "alice", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["action"] == "follow"
    assert payload["screenName"] == "alice"
    assert client_created == []


def test_post_confirmation_accept(monkeypatch) -> None:
    """TTY + 'y' input → tweet posted."""
    class FakeClient:
        def create_tweet(self, text, reply_to_id=None, media_ids=None):
            return "999"

    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda config_path=None: {"confirm": True, "fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    monkeypatch.setattr("twitter_cli.cli._is_interactive", lambda: True)
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "test message"], input="y\n")

    assert result.exit_code == 0
    assert "--- Preview ---" in result.output
    assert "Send?" in result.output
    assert "Tweet posted" in result.output


def test_post_confirmation_decline(monkeypatch) -> None:
    """TTY + 'n' input → cancelled, no API call."""
    client_created = []
    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr(
        "twitter_cli.cli._get_client",
        lambda config=None, quiet=False: client_created.append(1) or None,
    )
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda config_path=None: {"confirm": True, "fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    monkeypatch.setattr("twitter_cli.cli._is_interactive", lambda: True)
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "test message"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled." in result.output
    assert client_created == []


def test_post_yes_flag_skips_confirm(monkeypatch) -> None:
    """--yes skips prompt."""
    class FakeClient:
        def create_tweet(self, text, reply_to_id=None, media_ids=None):
            return "999"

    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda config_path=None: {"confirm": True, "fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "test message", "--yes"])

    assert result.exit_code == 0
    assert "Send?" not in result.output
    assert "Tweet posted" in result.output


def test_post_non_tty_auto_sends(monkeypatch) -> None:
    """Non-TTY skips prompt automatically (structured mode)."""
    class FakeClient:
        def create_tweet(self, text, reply_to_id=None, media_ids=None):
            return "999"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "test message", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "Send?" not in result.output


def test_post_config_confirm_false(monkeypatch) -> None:
    """confirm: false in config skips prompt."""
    class FakeClient:
        def create_tweet(self, text, reply_to_id=None, media_ids=None):
            return "999"

    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda config_path=None: {"confirm": False, "fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["post", "test message"])

    assert result.exit_code == 0
    assert "Send?" not in result.output
    assert "Tweet posted" in result.output


def test_like_no_confirmation(monkeypatch) -> None:
    """Simple write actions never prompt."""
    class FakeClient:
        def like_tweet(self, tweet_id):
            return True

    monkeypatch.setenv("OUTPUT", "rich")
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None, quiet=False: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["like", "123"])

    assert result.exit_code == 0
    assert "Send?" not in result.output
    assert "Done" in result.output
