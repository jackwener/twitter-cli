from __future__ import annotations

from click.testing import CliRunner
import pytest
import yaml

from twitter_cli.cli import cli
from twitter_cli.models import UserProfile
from twitter_cli.serialization import tweets_to_json


def test_cli_user_command_works_with_client_factory(monkeypatch) -> None:
    class FakeClient:
        def fetch_user(self, screen_name: str) -> UserProfile:
            return UserProfile(id="1", name="Alice", screen_name=screen_name)

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    runner = CliRunner()
    result = runner.invoke(cli, ["user", "alice"])
    assert result.exit_code == 0


def test_get_client_for_output_does_not_swallow_real_type_error(monkeypatch) -> None:
    def _broken_get_client(config=None, quiet=False):
        raise TypeError("real bug")

    monkeypatch.setattr("twitter_cli.cli._get_client", _broken_get_client)

    with pytest.raises(TypeError, match="real bug"):
        from twitter_cli.cli import _get_client_for_output

        _get_client_for_output({})


def test_cli_feed_json_input_path(tmp_path, tweet_factory) -> None:
    json_path = tmp_path / "tweets.json"
    json_path.write_text(tweets_to_json([tweet_factory("1")]), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["feed", "--input", str(json_path), "--json"])
    assert result.exit_code == 0
    assert '"id": "1"' in result.output


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
        lambda config=None: (_ for _ in ()).throw(RuntimeError("boom")),
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
        lambda config=None: (_ for _ in ()).throw(RuntimeError("User not found")),
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

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    monkeypatch.setattr(
        "twitter_cli.cli.load_config",
        lambda: {"fetch": {"count": 50}, "filter": {}, "rateLimit": {}},
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["tweet", "https://x.com/user/status/12345?s=20"])

    assert result.exit_code == 0


def test_cli_bookmark_alias_works(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def bookmark_tweet(self, tweet_id: str) -> bool:
            calls.append(tweet_id)
            return True

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["bookmark", "123"])

    assert result.exit_code == 0
    assert calls == ["123"]


def test_cli_whoami_command(monkeypatch) -> None:
    from twitter_cli.models import UserProfile

    class FakeClient:
        def fetch_me(self) -> UserProfile:
            return UserProfile(id="42", name="Test User", screen_name="testuser")

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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
    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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
        def create_tweet(self, text: str, reply_to_id=None) -> str:
            calls.append({"text": text, "reply_to_id": reply_to_id})
            return "999"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["reply", "12345", "Nice tweet!"])
    assert result.exit_code == 0
    assert calls[0]["reply_to_id"] == "12345"
    assert calls[0]["text"] == "Nice tweet!"


def test_cli_quote_command(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def quote_tweet(self, tweet_id: str, text: str) -> str:
            calls.append({"tweet_id": tweet_id, "text": text})
            return "888"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["quote", "12345", "Interesting!"])
    assert result.exit_code == 0
    assert calls[0]["tweet_id"] == "12345"
    assert calls[0]["text"] == "Interesting!"


def test_cli_post_json_output(monkeypatch) -> None:
    class FakeClient:
        def create_tweet(self, text: str, reply_to_id=None) -> str:
            assert text == "hello"
            assert reply_to_id is None
            return "999"

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
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

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda config=None: FakeClient())
    runner = CliRunner()

    result = runner.invoke(cli, ["unfollow", "alice"])
    assert result.exit_code == 0
    assert actions == [("unfollow", "42")]


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
