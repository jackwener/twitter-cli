"""CLI entry point for twitter-cli.

Read commands:
    twitter feed                      # home timeline (For You)
    twitter feed -t following         # following feed
    twitter bookmarks                 # bookmarks
    twitter search "query"            # search tweets
    twitter user elonmusk             # user profile
    twitter user-posts elonmusk       # user tweets
    twitter likes elonmusk            # user likes
    twitter tweet <id>                # tweet detail + replies
    twitter list <id>                 # list timeline
    twitter followers <handle>        # followers list
    twitter following <handle>        # following list
    twitter whoami                    # current user profile

Write commands:
    twitter post "text"               # post a tweet
    twitter reply <id> "text"         # reply to a tweet
    twitter quote <id> "text"         # quote-tweet
    twitter delete <id>               # delete a tweet
    twitter like/unlike <id>          # like/unlike
    twitter bookmark/unbookmark <id>  # bookmark/unbookmark
    twitter retweet/unretweet <id>    # retweet/unretweet
    twitter follow/unfollow <handle>  # follow/unfollow
"""

from __future__ import annotations

import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path

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
from .output import default_structured_format, emit_structured, structured_output_options, use_rich_output
from .serialization import (
    tweets_from_json,
    tweets_to_data,
    tweets_to_compact_json,
    tweets_to_json,
    user_profile_to_dict,
    users_to_data,
)


console = Console(stderr=True)
FEED_TYPES = ["for-you", "following"]
SEARCH_PRODUCTS = ["Top", "Latest", "Photos", "Videos"]


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


def _get_client(config=None, quiet=False):
    # type: (Optional[Dict[str, Any]], bool) -> TwitterClient
    """Create an authenticated API client."""
    if not quiet:
        console.print("\n🔐 Getting Twitter cookies...")
    cookies = get_cookies()
    rate_limit_config = (config or {}).get("rateLimit")
    return TwitterClient(
        cookies["auth_token"],
        cookies["ct0"],
        rate_limit_config,
        cookie_string=cookies.get("cookie_string"),
    )


def _get_client_for_output(config=None, quiet=False):
    # type: (Optional[Dict[str, Any]], bool) -> TwitterClient
    """Call _get_client while staying compatible with monkeypatched legacy signatures."""
    try:
        return _get_client(config, quiet=quiet)
    except TypeError:
        return _get_client(config)


def _exit_with_error(exc):
    # type: (RuntimeError) -> None
    console.print("[red]❌ %s[/red]" % exc)
    sys.exit(1)


def _run_guarded(action):
    # type: (Callable[[], Any]) -> Any
    try:
        return action()
    except RuntimeError as exc:
        _exit_with_error(exc)


def _resolve_fetch_count(max_count, configured):
    # type: (Optional[int], int) -> int
    """Resolve fetch count with bounds checks."""
    if max_count is not None:
        if max_count <= 0:
            raise RuntimeError("--max must be greater than 0")
        return max_count
    return max(configured, 1)


def _resolve_configured_count(config, max_count):
    # type: (dict, Optional[int]) -> int
    return _resolve_fetch_count(max_count, config.get("fetch", {}).get("count", 50))


def _normalize_tweet_id(value):
    # type: (str) -> str
    """Extract a numeric tweet ID from raw input or a full X/Twitter URL."""
    raw = value.strip()
    if not raw:
        raise RuntimeError("Tweet ID or URL is required")

    parsed = urllib.parse.urlparse(raw)
    candidate = raw
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        match = re.search(r"/status/(\d+)$", path)
        if not match:
            raise RuntimeError("Invalid tweet URL: %s" % value)
        candidate = match.group(1)
    else:
        candidate = raw.rstrip("/").split("/")[-1]
        candidate = candidate.split("?", 1)[0].split("#", 1)[0]

    if not candidate.isdigit():
        raise RuntimeError("Invalid tweet ID: %s" % value)
    return candidate


def _apply_filter(tweets, do_filter, config, rich_output=True):
    # type: (List[Tweet], bool, dict, bool) -> List[Tweet]
    """Optionally apply tweet filtering."""
    if not do_filter:
        return tweets
    filter_config = config.get("filter", {})
    original_count = len(tweets)
    filtered = filter_tweets(tweets, filter_config)
    if rich_output:
        print_filter_stats(original_count, filtered, console)
        console.print()
    return filtered


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--compact", "-c", is_flag=True, help="Compact output (minimal fields, LLM-friendly).")
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx, verbose, compact):
    # type: (Any, bool, bool) -> None
    """twitter — Twitter/X CLI tool 🐦"""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["compact"] = compact


def _fetch_and_display(fetch_fn, label, emoji, max_count, as_json, as_yaml, output_file, do_filter, config=None, compact=False):
    # type: (Any, str, str, Optional[int], bool, bool, Optional[str], bool, Optional[dict], bool) -> None
    """Common fetch-filter-display logic for timeline-like commands."""
    if config is None:
        config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("%s Fetching %s (%d tweets)...\n" % (emoji, label, fetch_count))
        start = time.time()
        tweets = fetch_fn(fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d %s in %.1fs\n" % (len(tweets), label, elapsed))
    except RuntimeError as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print("💾 Saved to %s\n" % output_file)

    if compact:
        click.echo(tweets_to_compact_json(filtered))
        return

    if emit_structured(tweets_to_data(filtered), as_json=as_json, as_yaml=as_yaml):
        return

    print_tweet_table(filtered, console, title="%s %s — %d tweets" % (emoji, label, len(filtered)))
    console.print()


def _run_bookmarks_command(max_count, as_json, as_yaml, output_file, do_filter, compact=False):
    # type: (Optional[int], bool, bool, Optional[str], bool, bool) -> None
    config = load_config()

    def _run():
        client = _get_client(config)
        _fetch_and_display(
            lambda count: client.fetch_bookmarks(count),
            "bookmarks",
            "🔖",
            max_count,
            as_json,
            as_yaml,
            output_file,
            do_filter,
            config,
            compact=compact,
        )

    _run_guarded(_run)


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
@structured_output_options
@click.option("--input", "-i", "input_file", type=str, default=None, help="Load tweets from JSON file.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save filtered tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def feed(ctx, feed_type, max_count, as_json, as_yaml, input_file, output_file, do_filter):
    # type: (Any, str, Optional[int], bool, bool, Optional[str], Optional[str], bool) -> None
    """Fetch home timeline with optional filtering."""
    compact = ctx.obj.get("compact", False)
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    config = load_config()
    try:
        if input_file:
            if rich_output:
                console.print("📂 Loading tweets from %s..." % input_file)
            tweets = _load_tweets_from_json(input_file)
            if rich_output:
                console.print("   Loaded %d tweets" % len(tweets))
        else:
            fetch_count = _resolve_configured_count(config, max_count)
            client = _get_client_for_output(config, quiet=not rich_output)
            label = "following feed" if feed_type == "following" else "home timeline"
            if rich_output:
                console.print("📡 Fetching %s (%d tweets)...\n" % (label, fetch_count))
            start = time.time()
            if feed_type == "following":
                tweets = client.fetch_following_feed(fetch_count)
            else:
                tweets = client.fetch_home_timeline(fetch_count)
            elapsed = time.time() - start
            if rich_output:
                console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except RuntimeError as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print("💾 Saved filtered tweets to %s\n" % output_file)

    if compact:
        click.echo(tweets_to_compact_json(filtered))
        return

    if emit_structured(tweets_to_data(filtered), as_json=as_json, as_yaml=as_yaml):
        return

    title = "👥 Following" if feed_type == "following" else "📱 Twitter"
    title += " — %d tweets" % len(filtered)
    print_tweet_table(filtered, console, title=title)
    console.print()


@cli.command()
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def favorites(ctx, max_count, as_json, as_yaml, output_file, do_filter):
    # type: (Any, Optional[int], bool, bool, Optional[str], bool) -> None
    """Fetch bookmarked (favorite) tweets."""
    _run_bookmarks_command(max_count, as_json, as_yaml, output_file, do_filter, compact=ctx.obj.get("compact", False))


@cli.command(name="bookmarks")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def bookmarks(ctx, max_count, as_json, as_yaml, output_file, do_filter):
    # type: (Any, Optional[int], bool, bool, Optional[str], bool) -> None
    """Fetch bookmarked tweets."""
    _run_bookmarks_command(max_count, as_json, as_yaml, output_file, do_filter, compact=ctx.obj.get("compact", False))


@cli.command()
@click.argument("screen_name")
@structured_output_options
def user(screen_name, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """View a user's profile. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching user @%s..." % screen_name)
        profile = client.fetch_user(screen_name)
    except RuntimeError as exc:
        _exit_with_error(exc)

    if not emit_structured(user_profile_to_dict(profile), as_json=as_json, as_yaml=as_yaml):
        console.print()
        print_user_profile(profile, console)


@cli.command("user-posts")
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.pass_context
def user_posts(ctx, screen_name, max_count, as_json, as_yaml, output_file):
    # type: (Any, str, int, bool, bool, Optional[str]) -> None
    """List a user's tweets. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        _fetch_and_display(
            lambda count: client.fetch_user_tweets(profile.id, count),
            "@%s tweets" % screen_name, "📝", max_count, as_json, as_yaml, output_file, False, config,
            compact=compact,
        )
    _run_guarded(_run)


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
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def search(ctx, query, product, max_count, as_json, as_yaml, output_file, do_filter):
    # type: (Any, str, str, int, bool, bool, Optional[str], bool) -> None
    """Search tweets by QUERY string."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client_for_output(config, quiet=not rich_output)
        _fetch_and_display(
            lambda count: client.fetch_search(query, count, product),
            "'%s' (%s)" % (query, product), "🔍", max_count, as_json, as_yaml, output_file, do_filter, config,
            compact=compact,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def likes(ctx, screen_name, max_count, as_json, as_yaml, output_file, do_filter):
    # type: (Any, str, int, bool, bool, Optional[str], bool) -> None
    """Show tweets liked by a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        _fetch_and_display(
            lambda count: client.fetch_user_likes(profile.id, count),
            "@%s likes" % screen_name, "❤️", max_count, as_json, as_yaml, output_file, do_filter, config,
            compact=compact,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("tweet_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max replies to fetch.")
@structured_output_options
@click.pass_context
def tweet(ctx, tweet_id, max_count, as_json, as_yaml):
    # type: (Any, str, int, bool, bool) -> None
    """View a tweet and its replies. TWEET_ID is the numeric tweet ID or full URL."""
    compact = ctx.obj.get("compact", False)
    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("🐦 Fetching tweet %s...\n" % tweet_id)
        start = time.time()
        tweets = client.fetch_tweet_detail(tweet_id, _resolve_configured_count(config, max_count))
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except RuntimeError as exc:
        _exit_with_error(exc)

    if compact:
        click.echo(tweets_to_compact_json(tweets))
        return

    if emit_structured(tweets_to_data(tweets), as_json=as_json, as_yaml=as_yaml):
        return

    if tweets:
        print_tweet_detail(tweets[0], console)
        if len(tweets) > 1:
            console.print("\n💬 Replies:")
            print_tweet_table(tweets[1:], console, title="💬 Replies — %d" % (len(tweets) - 1))
    console.print()


@cli.command(name="list")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch.")
@structured_output_options
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.pass_context
def list_timeline(ctx, list_id, max_count, as_json, as_yaml, do_filter):
    # type: (Any, str, int, bool, bool, bool) -> None
    """Fetch tweets from a Twitter List. LIST_ID is the numeric list ID."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        client = _get_client(config)
        _fetch_and_display(
            lambda count: client.fetch_list_timeline(list_id, count),
            "list %s" % list_id, "📋", max_count, as_json, as_yaml, None, do_filter, config,
            compact=compact,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def followers(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List followers of a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("👥 Fetching followers (%d)...\n" % fetch_count)
        start = time.time()
        users = client.fetch_followers(profile.id, fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d followers in %.1fs\n" % (len(users), elapsed))
    except RuntimeError as exc:
        _exit_with_error(exc)

    if emit_structured(users_to_data(users), as_json=as_json, as_yaml=as_yaml):
        return

    print_user_table(users, console, title="👥 @%s followers — %d" % (screen_name, len(users)))
    console.print()


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def following(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List accounts a user is following. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("👥 Fetching following (%d)...\n" % fetch_count)
        start = time.time()
        users = client.fetch_following(profile.id, fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d following in %.1fs\n" % (len(users), elapsed))
    except RuntimeError as exc:
        _exit_with_error(exc)

    if emit_structured(users_to_data(users), as_json=as_json, as_yaml=as_yaml):
        return

    print_user_table(users, console, title="👥 @%s following — %d" % (screen_name, len(users)))
    console.print()


# ── Write commands ──────────────────────────────────────────────────────

def _write_action(emoji, action_desc, client_method, tweet_id):
    # type: (str, str, str, str) -> None
    """Generic write action helper to reduce CLI command boilerplate.

    Emits structured JSON/YAML when piped or when OUTPUT env is set.
    """
    try:
        config = load_config()
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            console.print("%s %s %s..." % (emoji, action_desc, tweet_id))
        getattr(client, client_method)(tweet_id)
        result = {"success": True, "action": action_desc.lower().replace(" ", "_"), "id": tweet_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Done.[/green]")
    except RuntimeError as exc:
        result = {"success": False, "action": action_desc.lower().replace(" ", "_"), "id": tweet_id, "error": str(exc)}
        structured = default_structured_format(as_json=False, as_yaml=False)
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
            sys.exit(1)
        _exit_with_error(exc)


@cli.command()
@click.argument("text")
@click.option("--reply-to", "-r", default=None, help="Reply to this tweet ID.")
def post(text, reply_to):
    # type: (str, Optional[str]) -> None
    """Post a new tweet. TEXT is the tweet content."""
    config = load_config()
    try:
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            action = "Replying to %s" % reply_to if reply_to else "Posting tweet"
            console.print("✏️  %s..." % action)
        tweet_id = client.create_tweet(text, reply_to_id=reply_to)
        result = {"success": True, "action": "post", "id": tweet_id, "url": "https://x.com/i/status/%s" % tweet_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Tweet posted![/green]")
            console.print("🔗 https://x.com/i/status/%s" % tweet_id)
    except RuntimeError as exc:
        _exit_with_error(exc)


@cli.command(name="reply")
@click.argument("tweet_id")
@click.argument("text")
def reply_tweet(tweet_id, text):
    # type: (str, str) -> None
    """Reply to a tweet. TWEET_ID is the tweet to reply to, TEXT is the reply content."""
    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    try:
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            console.print("💬 Replying to %s..." % tweet_id)
        new_id = client.create_tweet(text, reply_to_id=tweet_id)
        result = {"success": True, "action": "reply", "id": new_id, "replyTo": tweet_id, "url": "https://x.com/i/status/%s" % new_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Reply posted![/green]")
            console.print("🔗 https://x.com/i/status/%s" % new_id)
    except RuntimeError as exc:
        _exit_with_error(exc)


@cli.command(name="quote")
@click.argument("tweet_id")
@click.argument("text")
def quote_tweet(tweet_id, text):
    # type: (str, str) -> None
    """Quote-tweet a tweet. TWEET_ID is the tweet to quote, TEXT is the commentary."""
    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    try:
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            console.print("🔄 Quoting tweet %s..." % tweet_id)
        new_id = client.quote_tweet(tweet_id, text)
        result = {"success": True, "action": "quote", "id": new_id, "quotedId": tweet_id, "url": "https://x.com/i/status/%s" % new_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Quote tweet posted![/green]")
            console.print("🔗 https://x.com/i/status/%s" % new_id)
    except RuntimeError as exc:
        _exit_with_error(exc)


@cli.command(name="status")
@structured_output_options
def status(as_json, as_yaml):
    # type: (bool, bool) -> None
    """Check whether the current Twitter/X session is authenticated."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client_for_output(config, quiet=not rich_output)
        profile = client.fetch_me()
    except RuntimeError as exc:
        payload = {"authenticated": False, "error": str(exc)}
        if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
            sys.exit(1)
        _exit_with_error(exc)
        return

    payload = {"authenticated": True, "user": user_profile_to_dict(profile)}
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return

    console.print("[green]✅ Authenticated.[/green]")
    console.print("👤 @%s" % profile.screen_name)


@cli.command(name="whoami")
@structured_output_options
def whoami(as_json, as_yaml):
    # type: (bool, bool) -> None
    """Show the currently authenticated user's profile."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client_for_output(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching current user...")
        profile = client.fetch_me()
    except RuntimeError as exc:
        _exit_with_error(exc)

    if not emit_structured(user_profile_to_dict(profile), as_json=as_json, as_yaml=as_yaml):
        console.print()
        print_user_profile(profile, console)


@cli.command(name="follow")
@click.argument("screen_name")
def follow_user(screen_name):
    # type: (str,) -> None
    """Follow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            console.print("👤 Looking up @%s..." % screen_name)
        user_id = client.resolve_user_id(screen_name)
        if not structured:
            console.print("➕ Following @%s..." % screen_name)
        client.follow_user(user_id)
        result = {"success": True, "action": "follow", "screenName": screen_name, "userId": user_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Now following @%s[/green]" % screen_name)
    except RuntimeError as exc:
        _exit_with_error(exc)


@cli.command(name="unfollow")
@click.argument("screen_name")
def unfollow_user(screen_name):
    # type: (str,) -> None
    """Unfollow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        client = _get_client(config)
        structured = default_structured_format(as_json=False, as_yaml=False)
        if not structured:
            console.print("👤 Looking up @%s..." % screen_name)
        user_id = client.resolve_user_id(screen_name)
        if not structured:
            console.print("➖ Unfollowing @%s..." % screen_name)
        client.unfollow_user(user_id)
        result = {"success": True, "action": "unfollow", "screenName": screen_name, "userId": user_id}
        if structured:
            emit_structured(result, as_json=(structured == "json"), as_yaml=(structured == "yaml"))
        else:
            console.print("[green]✅ Unfollowed @%s[/green]" % screen_name)
    except RuntimeError as exc:
        _exit_with_error(exc)


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
def bookmark(tweet_id):
    # type: (str,) -> None
    """Bookmark a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def unfavorite(tweet_id):
    # type: (str,) -> None
    """Remove a tweet from bookmarks (unfavorite). TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Removing bookmark", "unbookmark_tweet", tweet_id)


@cli.command()
@click.argument("tweet_id")
def unbookmark(tweet_id):
    # type: (str,) -> None
    """Remove a tweet from bookmarks. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Removing bookmark", "unbookmark_tweet", tweet_id)


if __name__ == "__main__":
    cli()
