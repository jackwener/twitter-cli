"""CLI entry point for twitter-cli.

Read commands:
    twitter feed                      # home timeline (For You)
    twitter feed -t following         # following feed
    twitter favorites                 # bookmarks
    twitter search "query"            # search tweets
    twitter user elonmusk             # user profile
    twitter user-posts elonmusk       # user tweets
    twitter likes elonmusk            # user likes
    twitter tweet <id>                # tweet detail + replies
    twitter list <id>                 # list timeline
    twitter followers <handle>        # followers list
    twitter following <handle>        # following list

Write commands:
    twitter post "text"               # post a tweet
    twitter delete <id>               # delete a tweet
    twitter like/unlike <id>          # like/unlike
    twitter favorite/unfavorite <id>  # bookmark/unbookmark
    twitter retweet/unretweet <id>    # retweet/unretweet
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import json

import click
from rich.console import Console

from . import __version__
from .auth import get_cookies
from .client import TwitterClient
from .config import load_config
from .filter import filter_tweets
from .formatter import (
    print_filter_stats,
    print_tweet_detail,
    print_tweet_table,
    print_user_profile,
    print_user_table,
)
from .serialization import tweets_from_json, tweets_to_json, user_profile_to_dict, users_to_json


console = Console(stderr=True)
FEED_TYPES = ["for-you", "following"]


def _setup_logging(verbose):
    # type: (bool) -> None
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load_tweets_from_json(path):
    # type: (str) -> List[Tweet]
    """Load tweets from a JSON file (previously exported)."""
    file_path = Path(path)
    if not file_path.exists():
        raise RuntimeError("Input file not found: %s" % path)

    try:
        raw = file_path.read_text(encoding="utf-8")
        return tweets_from_json(raw)
    except (ValueError, OSError) as exc:
        raise RuntimeError("Invalid tweet JSON file %s: %s" % (path, exc))


def _get_client(config=None):
    # type: (Optional[Dict[str, Any]]) -> TwitterClient
    """Create an authenticated API client."""
    console.print("\n🔐 Getting Twitter cookies...")
    cookies = get_cookies()
    rate_limit_config = (config or {}).get("rateLimit")
    return TwitterClient(
        cookies["auth_token"],
        cookies["ct0"],
        rate_limit_config,
        cookie_string=cookies.get("cookie_string"),
    )


def _resolve_fetch_count(max_count, configured):
    # type: (Optional[int], int) -> int
    """Resolve fetch count with bounds checks."""
    if max_count is not None:
        if max_count <= 0:
            raise RuntimeError("--max must be greater than 0")
        return max_count
    return max(configured, 1)


def _apply_filter(tweets, do_filter, config):
    # type: (List[Tweet], bool, dict) -> List[Tweet]
    """Optionally apply tweet filtering."""
    if not do_filter:
        return tweets
    filter_config = config.get("filter", {})
    original_count = len(tweets)
    filtered = filter_tweets(tweets, filter_config)
    print_filter_stats(original_count, filtered, console)
    console.print()
    return filtered


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.version_option(version=__version__)
def cli(verbose):
    # type: (bool) -> None
    """twitter — Twitter/X CLI tool 🐦"""
    _setup_logging(verbose)


def _fetch_and_display(fetch_fn, label, emoji, max_count, as_json, output_file, do_filter, config=None):
    # type: (Any, str, str, Optional[int], bool, Optional[str], bool, Optional[dict]) -> None
    """Common fetch-filter-display logic for timeline-like commands."""
    if config is None:
        config = load_config()
    try:
        fetch_count = _resolve_fetch_count(max_count, config.get("fetch", {}).get("count", 50))
        console.print("%s Fetching %s (%d tweets)...\n" % (emoji, label, fetch_count))
        start = time.time()
        tweets = fetch_fn(fetch_count)
        elapsed = time.time() - start
        console.print("✅ Fetched %d %s in %.1fs\n" % (len(tweets), label, elapsed))
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    filtered = _apply_filter(tweets, do_filter, config)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        console.print("💾 Saved to %s\n" % output_file)

    if as_json:
        click.echo(tweets_to_json(filtered))
        return

    print_tweet_table(filtered, console, title="%s %s — %d tweets" % (emoji, label, len(filtered)))
    console.print()


@cli.command()
@click.option(
    "--type",
    "-t",
    "feed_type",
    type=click.Choice(FEED_TYPES),
    default="for-you",
    help="Feed type: for-you (algorithmic) or following (chronological).",
)
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--input", "-i", "input_file", type=str, default=None, help="Load tweets from JSON file.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save filtered tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
def feed(feed_type, max_count, as_json, input_file, output_file, do_filter):
    # type: (str, Optional[int], bool, Optional[str], Optional[str], bool) -> None
    """Fetch home timeline with optional filtering."""
    config = load_config()
    try:
        if input_file:
            console.print("📂 Loading tweets from %s..." % input_file)
            tweets = _load_tweets_from_json(input_file)
            console.print("   Loaded %d tweets" % len(tweets))
        else:
            fetch_count = _resolve_fetch_count(max_count, config.get("fetch", {}).get("count", 50))
            client = _get_client(config)
            label = "following feed" if feed_type == "following" else "home timeline"
            console.print("📡 Fetching %s (%d tweets)...\n" % (label, fetch_count))
            start = time.time()
            if feed_type == "following":
                tweets = client.fetch_following_feed(fetch_count)
            else:
                tweets = client.fetch_home_timeline(fetch_count)
            elapsed = time.time() - start
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    filtered = _apply_filter(tweets, do_filter, config)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        console.print("💾 Saved filtered tweets to %s\n" % output_file)

    if as_json:
        click.echo(tweets_to_json(filtered))
        return

    title = "👥 Following" if feed_type == "following" else "📱 Twitter"
    title += " — %d tweets" % len(filtered)
    print_tweet_table(filtered, console, title=title)
    console.print()


@cli.command()
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
def favorites(max_count, as_json, output_file, do_filter):
    # type: (Optional[int], bool, Optional[str], bool) -> None
    """Fetch bookmarked (favorite) tweets."""
    config = load_config()
    client = _get_client(config)
    _fetch_and_display(
        lambda count: client.fetch_bookmarks(count),
        "favorites", "🔖", max_count, as_json, output_file, do_filter, config,
    )


@cli.command()
@click.argument("screen_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def user(screen_name, as_json):
    # type: (str, bool) -> None
    """View a user's profile. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        client = _get_client(config)
        console.print("👤 Fetching user @%s..." % screen_name)
        profile = client.fetch_user(screen_name)
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(user_profile_to_dict(profile), ensure_ascii=False, indent=2))
    else:
        console.print()
        print_user_profile(profile, console)


@cli.command("user-posts")
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max number of tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
def user_posts(screen_name, max_count, as_json, output_file):
    # type: (str, int, bool, Optional[str]) -> None
    """List a user's tweets. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    client = _get_client(config)
    console.print("👤 Fetching @%s's profile..." % screen_name)
    try:
        profile = client.fetch_user(screen_name)
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)
    _fetch_and_display(
        lambda count: client.fetch_user_tweets(profile.id, count),
        "@%s tweets" % screen_name, "📝", max_count, as_json, output_file, False, config,
    )


SEARCH_PRODUCTS = ["Top", "Latest", "Photos", "Videos"]


@cli.command()
@click.argument("query")
@click.option(
    "--type",
    "-t",
    "product",
    type=click.Choice(SEARCH_PRODUCTS, case_sensitive=False),
    default="Top",
    help="Search tab: Top, Latest, Photos, or Videos.",
)
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max number of tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
def search(query, product, max_count, as_json, output_file, do_filter):
    # type: (str, str, int, bool, Optional[str], bool) -> None
    """Search tweets by QUERY string."""
    config = load_config()
    client = _get_client(config)
    _fetch_and_display(
        lambda count: client.fetch_search(query, count, product),
        "'%s' (%s)" % (query, product), "🔍", max_count, as_json, output_file, do_filter, config,
    )


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max number of tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
def likes(screen_name, max_count, as_json, output_file, do_filter):
    # type: (str, int, bool, Optional[str], bool) -> None
    """Show tweets liked by a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    client = _get_client(config)
    console.print("👤 Fetching @%s's profile..." % screen_name)
    try:
        profile = client.fetch_user(screen_name)
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)
    _fetch_and_display(
        lambda count: client.fetch_user_likes(profile.id, count),
        "@%s likes" % screen_name, "❤️", max_count, as_json, output_file, do_filter, config,
    )


@cli.command()
@click.argument("tweet_id")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max replies to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def tweet(tweet_id, max_count, as_json):
    # type: (str, int, bool) -> None
    """View a tweet and its replies. TWEET_ID is the numeric tweet ID or full URL."""
    # Extract tweet ID from URL if given
    tweet_id = tweet_id.strip().rstrip("/").split("/")[-1]
    config = load_config()
    try:
        client = _get_client(config)
        console.print("🐦 Fetching tweet %s...\n" % tweet_id)
        start = time.time()
        tweets = client.fetch_tweet_detail(tweet_id, max_count)
        elapsed = time.time() - start
        console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    if as_json:
        click.echo(tweets_to_json(tweets))
        return

    if tweets:
        print_tweet_detail(tweets[0], console)
        if len(tweets) > 1:
            console.print("\n💬 Replies:")
            print_tweet_table(tweets[1:], console, title="💬 Replies — %d" % (len(tweets) - 1))
    console.print()


@cli.command(name="list")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max tweets to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
def list_timeline(list_id, max_count, as_json, do_filter):
    # type: (str, int, bool, bool) -> None
    """Fetch tweets from a Twitter List. LIST_ID is the numeric list ID."""
    config = load_config()
    client = _get_client(config)
    _fetch_and_display(
        lambda count: client.fetch_list_timeline(list_id, count),
        "list %s" % list_id, "📋", max_count, as_json, None, do_filter, config,
    )


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max users to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def followers(screen_name, max_count, as_json):
    # type: (str, int, bool) -> None
    """List followers of a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        client = _get_client(config)
        console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        console.print("👥 Fetching followers (%d)...\n" % max_count)
        start = time.time()
        users = client.fetch_followers(profile.id, max_count)
        elapsed = time.time() - start
        console.print("✅ Fetched %d followers in %.1fs\n" % (len(users), elapsed))
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    if as_json:
        click.echo(users_to_json(users))
        return

    print_user_table(users, console, title="👥 @%s followers — %d" % (screen_name, len(users)))
    console.print()


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=20, help="Max users to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def following(screen_name, max_count, as_json):
    # type: (str, int, bool) -> None
    """List accounts a user is following. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        client = _get_client(config)
        console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        console.print("👥 Fetching following (%d)...\n" % max_count)
        start = time.time()
        users = client.fetch_following(profile.id, max_count)
        elapsed = time.time() - start
        console.print("✅ Fetched %d following in %.1fs\n" % (len(users), elapsed))
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)

    if as_json:
        click.echo(users_to_json(users))
        return

    print_user_table(users, console, title="👥 @%s following — %d" % (screen_name, len(users)))
    console.print()


# ── Write commands ──────────────────────────────────────────────────────

def _write_action(emoji, action_desc, client_method, tweet_id):
    # type: (str, str, str, str) -> None
    """Generic write action helper to reduce CLI command boilerplate."""
    try:
        config = load_config()
        client = _get_client(config)
        console.print("%s %s %s..." % (emoji, action_desc, tweet_id))
        getattr(client, client_method)(tweet_id)
        console.print("[green]✅ Done.[/green]")
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)


@cli.command()
@click.argument("text")
@click.option("--reply-to", "-r", default=None, help="Reply to this tweet ID.")
def post(text, reply_to):
    # type: (str, Optional[str]) -> None
    """Post a new tweet. TEXT is the tweet content."""
    config = load_config()
    try:
        client = _get_client(config)
        action = "Replying to %s" % reply_to if reply_to else "Posting tweet"
        console.print("✏️  %s..." % action)
        tweet_id = client.create_tweet(text, reply_to_id=reply_to)
        console.print("[green]✅ Tweet posted![/green]")
        console.print("🔗 https://x.com/i/status/%s" % tweet_id)
    except RuntimeError as exc:
        console.print("[red]❌ %s[/red]" % exc)
        sys.exit(1)


@cli.command(name="delete")
@click.argument("tweet_id")
@click.confirmation_option(prompt="Are you sure you want to delete this tweet?")
def delete_tweet(tweet_id):
    # type: (str,) -> None
    """Delete a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🗑️", "Deleting tweet", "delete_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def like(tweet_id):
    # type: (str,) -> None
    """Like a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("❤️", "Liking tweet", "like_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def unlike(tweet_id):
    # type: (str,) -> None
    """Unlike a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("💔", "Unliking tweet", "unlike_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def retweet(tweet_id):
    # type: (str,) -> None
    """Retweet a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Retweeting", "retweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def unretweet(tweet_id):
    # type: (str,) -> None
    """Undo a retweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Undoing retweet", "unretweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def favorite(tweet_id):
    # type: (str,) -> None
    """Bookmark (favorite) a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def unfavorite(tweet_id):
    # type: (str,) -> None
    """Remove a tweet from bookmarks (unfavorite). TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Removing bookmark", "unbookmark_tweet", tweet_id)


if __name__ == "__main__":
    cli()
