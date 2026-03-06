from __future__ import annotations

from click.testing import CliRunner

from twitter_cli.cli import cli
from twitter_cli.models import UserProfile
from twitter_cli.serialization import tweets_to_json


def test_cli_user_command_works_with_client_factory(monkeypatch) -> None:
    class FakeClient:
        def fetch_user(self, screen_name: str) -> UserProfile:
            return UserProfile(id="1", name="Alice", screen_name=screen_name)

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda: FakeClient())
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


def test_cli_likes_command(monkeypatch, tweet_factory) -> None:
    fake_tweets = [tweet_factory("10"), tweet_factory("11")]

    class FakeClient:
        def fetch_current_user_id(self) -> str:
            return "999"

        def fetch_likes(self, user_id: str, count: int = 20):
            return fake_tweets

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda: FakeClient())
    runner = CliRunner()
    result = runner.invoke(cli, ["likes", "--max", "5", "--json"])
    assert result.exit_code == 0
    assert '"id": "10"' in result.output
    assert '"id": "11"' in result.output
