"""CLI entry point for twitter-cli.

Read commands:
    twitter feed                      # home timeline
    twitter feed -t following         # following feed
    twitter bookmarks                 # bookmarks
    twitter search "query"            # search tweets
    twitter search "query" --from user  # advanced search
    twitter user elonmusk             # user profile
    twitter user-posts elonmusk       # user tweets
    twitter mentions elonmusk         # user mentions
    twitter likes elonmusk            # user likes
    twitter tweet <id>                # tweet detail + replies
    twitter article <id>              # Twitter Article as Markdown
    twitter list <id>                 # list timeline
    twitter list-info <id>            # list metadata
    twitter owned-lists <handle>      # user-owned lists
    twitter followed-lists <handle>   # followed lists
    twitter followers <handle>        # followers list
    twitter following <handle>        # following list
    twitter whoami                    # current user profile

Write commands:
    twitter post "text"               # post a tweet
    twitter post "text" -i photo.jpg  # post with image(s)
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
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import click
from rich.console import Console

from . import __version__
from .api_client import TwitterAPIv2Client, has_api_credentials
from .auth import get_cookies
from .cache import resolve_cached_tweet, save_tweet_cache
from .exceptions import TwitterError
from .client import TwitterClient
from .config import load_config
from .filter import filter_tweets
from .formatter import (
    article_to_markdown,
    print_filter_stats,
    print_article,
    print_list_detail,
    print_list_table,
    print_tweet_detail,
    print_tweet_table,
    print_user_profile,
    print_user_table,
)
from .models import Tweet, UserProfile
from .output import (
    default_structured_format,
    emit_error,
    emit_structured,
    ensure_utf8_streams,
    error_payload,
    structured_output_options,
    success_payload,
    use_rich_output,
)
from .serialization import (
    twitter_list_to_dict,
    twitter_lists_to_data,
    tweet_to_dict,
    tweets_from_json,
    tweets_to_data,
    tweets_to_compact_json,
    tweets_to_json,
    user_profile_to_dict,
    users_to_data,
)

ConfigDict = Dict[str, Any]
TweetList = List[Tweet]
FetchTweets = Callable[[int], TweetList]
OptionalPath = Optional[str]
StructuredMode = Optional[str]
WritePayload = Dict[str, Any]
WriteOperation = Callable[[Any], WritePayload]

logger = logging.getLogger(__name__)
console = Console(stderr=True)
FEED_TYPES = ["home", "for-you", "following"]
SEARCH_PRODUCTS = ["Top", "Latest", "Photos", "Videos"]
SEARCH_SCOPES = ["recent", "all"]
SEARCH_HAS_CHOICES = ["links", "images", "videos", "media"]
SEARCH_EXCLUDE_CHOICES = ["retweets", "replies", "links"]
REPLY_SCOPES = ["auto", "recent", "all"]
AUTH_MODES = ["auto", "cookie", "api"]


def _agent_user_profile(profile: UserProfile) -> dict:
    """Normalize a Twitter/X profile for structured agent output."""
    data = user_profile_to_dict(profile)
    return {
        "id": data["id"],
        "name": data["name"],
        "username": data["screenName"],
        "screenName": data["screenName"],
        "bio": data["bio"],
        "location": data["location"],
        "url": data["url"],
        "followers": data["followers"],
        "following": data["following"],
        "tweets": data["tweets"],
        "likes": data["likes"],
        "verified": data["verified"],
        "profileImageUrl": data["profileImageUrl"],
        "createdAt": data["createdAt"],
    }


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


def _resolve_auth_mode() -> str:
    ctx = click.get_current_context(silent=True)
    if ctx is not None and ctx.obj and ctx.obj.get("auth_mode"):
        return str(ctx.obj["auth_mode"])
    env_mode = os.environ.get("TWITTER_AUTH_MODE", "auto").strip().lower()
    return env_mode if env_mode in AUTH_MODES else "auto"


def _get_client(config=None, quiet=False):
    # type: (Optional[Dict[str, Any]], bool) -> Any
    """Create an authenticated API client."""
    mode = _resolve_auth_mode()
    if mode == "auto":
        mode = "api" if has_api_credentials() else "cookie"

    rate_limit_config = (config or {}).get("rateLimit")
    if mode == "api":
        if not quiet:
            console.print("\n🔐 Using official X API auth...")
        return TwitterAPIv2Client(rate_limit_config)

    if not quiet:
        console.print("\n🔐 Getting Twitter cookies...")
    cookies = get_cookies()
    return TwitterClient(
        cookies["auth_token"],
        cookies["ct0"],
        rate_limit_config,
        cookie_string=cookies.get("cookie_string"),
    )





def _error_code_from_exc(exc: Exception) -> str:
    """Extract structured error code from an exception."""
    return getattr(exc, "error_code", "api_error")


def _exit_with_error(exc: Exception) -> None:
    if emit_error(_error_code_from_exc(exc), str(exc)):
        sys.exit(1)
    console.print("[red]❌ %s[/red]" % exc)
    sys.exit(1)


def _run_guarded(action):
    # type: (Callable[[], Any]) -> Any
    try:
        return action()
    except (TwitterError, RuntimeError) as exc:
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
        match = re.search(r"/(?:status|article)/(\d+)$", path)
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


def _structured_mode(as_json: bool, as_yaml: bool) -> StructuredMode:
    return default_structured_format(as_json=as_json, as_yaml=as_yaml)


def _emit_mode_payload(payload: object, mode: StructuredMode) -> bool:
    if not mode:
        return False
    emit_structured(payload, as_json=(mode == "json"), as_yaml=(mode == "yaml"))
    return True


def _print_lines(lines: List[str], mode: StructuredMode) -> None:
    if mode:
        return
    for line in lines:
        console.print(line)


def _handle_structured_runtime_error(
    exc: Exception,
    *,
    mode: StructuredMode,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if _emit_mode_payload(
        error_payload(_error_code_from_exc(exc), str(exc), details=details),
        mode,
    ):
        raise SystemExit(1) from None
    _exit_with_error(exc)


def _run_write_command(
    *,
    as_json: bool,
    as_yaml: bool,
    operation: WriteOperation,
    progress_lines: Optional[List[str]] = None,
    success_lines: Optional[List[str]] = None,
    error_details: Optional[Dict[str, Any]] = None,
) -> Optional[WritePayload]:
    mode = _structured_mode(as_json=as_json, as_yaml=as_yaml)
    try:
        client = _get_client(load_config())
        _print_lines(progress_lines or [], mode)
        payload = operation(client)
    except (TwitterError, RuntimeError) as exc:
        _handle_structured_runtime_error(exc, mode=mode, details=error_details)
        return None

    if _emit_mode_payload(payload, mode):
        return payload

    _print_lines(success_lines or ["[green]✅ Done.[/green]"], mode)
    return payload


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--compact", "-c", is_flag=True, help="Compact output (minimal fields, LLM-friendly).")
@click.option(
    "--auth-mode",
    type=click.Choice(AUTH_MODES, case_sensitive=False),
    default=None,
    help="Auth backend: auto, cookie, or api.",
)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx, verbose, compact, auth_mode):
    # type: (Any, bool, bool, Optional[str]) -> None
    """twitter — Twitter/X CLI tool 🐦"""
    ensure_utf8_streams()
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["compact"] = compact
    raw_auth_mode = auth_mode if auth_mode is not None else (os.environ.get("TWITTER_AUTH_MODE") or "auto")
    resolved = raw_auth_mode.strip().lower()
    ctx.obj["auth_mode"] = resolved if resolved in AUTH_MODES else "auto"


def _fetch_and_display(fetch_fn, label, emoji, max_count, as_json, as_yaml, output_file, do_filter, config=None, compact=False, full_text=False):
    # type: (Any, str, str, Optional[int], bool, bool, Optional[str], bool, Optional[dict], bool, bool) -> None
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
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print("💾 Saved to %s\n" % output_file)

    if compact:
        click.echo(tweets_to_compact_json(filtered))
        return

    save_tweet_cache(filtered)

    if emit_structured(tweets_to_data(filtered), as_json=as_json, as_yaml=as_yaml):
        return

    print_tweet_table(
        filtered,
        console,
        title="%s %s — %d tweets" % (emoji, label, len(filtered)),
        full_text=full_text,
    )
    _print_show_hint()
    console.print()


def _run_bookmarks_command(max_count, as_json, as_yaml, output_file, do_filter, compact=False, full_text=False):
    # type: (Optional[int], bool, bool, Optional[str], bool, bool, bool) -> None
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
            full_text=full_text,
        )

    _run_guarded(_run)


@cli.command()
@click.option(
    "--type",
    "-t",
    "feed_type",
    type=click.Choice(FEED_TYPES),
    default="home",
    help="Feed type: home, for-you, or following.",
)
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--input", "-i", "input_file", type=str, default=None, help="Load tweets from JSON file.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save filtered tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def feed(ctx, feed_type, max_count, as_json, as_yaml, input_file, output_file, do_filter, full_text):
    # type: (Any, str, Optional[int], bool, bool, Optional[str], Optional[str], bool, bool) -> None
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
            client = _get_client(config, quiet=not rich_output)
            label = "following feed" if feed_type == "following" else "home timeline"
            if rich_output:
                console.print("📡 Fetching %s (%d tweets)...\n" % (label, fetch_count))
                if isinstance(client, TwitterAPIv2Client) and feed_type == "for-you":
                    console.print(
                        "[yellow]⚠️ API mode does not expose the algorithmic For You feed. "
                        "Using the official reverse-chronological home timeline instead.[/yellow]\n"
                    )
            start = time.time()
            if feed_type == "following":
                tweets = client.fetch_following_feed(fetch_count)
            else:
                tweets = client.fetch_home_timeline(fetch_count)
            elapsed = time.time() - start
            if rich_output:
                console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    filtered = _apply_filter(tweets, do_filter, config, rich_output=rich_output)

    if output_file:
        Path(output_file).write_text(tweets_to_json(filtered), encoding="utf-8")
        if rich_output:
            console.print("💾 Saved filtered tweets to %s\n" % output_file)

    if compact:
        click.echo(tweets_to_compact_json(filtered))
        return

    save_tweet_cache(filtered)

    if emit_structured(tweets_to_data(filtered), as_json=as_json, as_yaml=as_yaml):
        return

    title = "👥 Following" if feed_type == "following" else "📱 Twitter"
    title += " — %d tweets" % len(filtered)
    print_tweet_table(filtered, console, title=title, full_text=full_text)
    _print_show_hint()
    console.print()


@cli.command()
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def favorites(ctx, max_count, as_json, as_yaml, output_file, do_filter, full_text):
    # type: (Any, Optional[int], bool, bool, Optional[str], bool, bool) -> None
    """Fetch bookmarked (favorite) tweets."""
    _run_bookmarks_command(
        max_count,
        as_json,
        as_yaml,
        output_file,
        do_filter,
        compact=ctx.obj.get("compact", False),
        full_text=full_text,
    )


@cli.command(name="bookmarks")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def bookmarks(ctx, max_count, as_json, as_yaml, output_file, do_filter, full_text):
    # type: (Any, Optional[int], bool, bool, Optional[str], bool, bool) -> None
    """Fetch bookmarked tweets."""
    _run_bookmarks_command(
        max_count,
        as_json,
        as_yaml,
        output_file,
        do_filter,
        compact=ctx.obj.get("compact", False),
        full_text=full_text,
    )


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
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching user @%s..." % screen_name)
        profile = client.fetch_user(screen_name)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if not emit_structured(user_profile_to_dict(profile), as_json=as_json, as_yaml=as_yaml):
        console.print()
        print_user_profile(profile, console)


@cli.command("user-posts")
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def user_posts(ctx, screen_name, max_count, as_json, as_yaml, output_file, full_text):
    # type: (Any, str, int, bool, bool, Optional[str], bool) -> None
    """List a user's tweets. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        _fetch_and_display(
            lambda count: client.fetch_user_tweets(profile.id, count),
            "@%s tweets" % screen_name, "📝", max_count, as_json, as_yaml, output_file, False, config,
            compact=compact, full_text=full_text,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def mentions(ctx, screen_name, max_count, as_json, as_yaml, output_file, do_filter, full_text):
    # type: (Any, str, int, bool, bool, Optional[str], bool, bool) -> None
    """Show posts mentioning a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()

    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        _fetch_and_display(
            lambda count: client.fetch_mentions(profile.id, count),
            "@%s mentions" % screen_name,
            "🔔",
            max_count,
            as_json,
            as_yaml,
            output_file,
            do_filter,
            config,
            compact=compact,
            full_text=full_text,
        )

    _run_guarded(_run)


@cli.command()
@click.argument("query", default="")
@click.option(
    "--type",
    "-t",
    "product",
    type=click.Choice(SEARCH_PRODUCTS, case_sensitive=False),
    default="Top",
    help="Search tab: Top, Latest, Photos, or Videos.",
)
@click.option(
    "--scope",
    type=click.Choice(SEARCH_SCOPES, case_sensitive=False),
    default="recent",
    help="Search scope: recent or all.",
)
@click.option("--from", "from_user", type=str, default=None, help="Only tweets from this user.")
@click.option("--to", "to_user", type=str, default=None, help="Only tweets directed at this user.")
@click.option("--lang", type=str, default=None, help="Filter by language (ISO code, e.g. en, fr, ja).")
@click.option("--since", type=str, default=None, help="Tweets since date (YYYY-MM-DD).")
@click.option("--until", type=str, default=None, help="Tweets until date (YYYY-MM-DD).")
@click.option(
    "--has",
    type=click.Choice(SEARCH_HAS_CHOICES, case_sensitive=False),
    multiple=True,
    help="Require content type (links, images, videos, media). Repeatable.",
)
@click.option(
    "--exclude",
    type=click.Choice(SEARCH_EXCLUDE_CHOICES, case_sensitive=False),
    multiple=True,
    help="Exclude content type (retweets, replies, links). Repeatable.",
)
@click.option("--min-likes", type=click.IntRange(min=0), default=None, help="Minimum number of likes.")
@click.option("--min-retweets", type=click.IntRange(min=0), default=None, help="Minimum number of retweets.")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def search(ctx, query, product, scope, from_user, to_user, lang, since, until, has, exclude, min_likes, min_retweets, max_count, as_json, as_yaml, output_file, do_filter, full_text):
    # type: (Any, str, str, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], tuple, tuple, Optional[int], Optional[int], int, bool, bool, Optional[str], bool, bool) -> None
    """Search tweets by QUERY string with optional advanced filters.

    QUERY is the search keywords (optional when using advanced filters).

    Advanced search examples:

    \b
      twitter search "python" --from elonmusk
      twitter search "AI" --lang en --since 2026-01-01
      twitter search "rust" --has links --min-likes 100
      twitter search --from bbc --exclude retweets
    """
    from .search import build_search_query

    try:
        composed_query = build_search_query(
            query,
            from_user=from_user,
            to_user=to_user,
            lang=lang,
            since=since,
            until=until,
            has=list(has) if has else None,
            exclude=list(exclude) if exclude else None,
            min_likes=min_likes,
            min_retweets=min_retweets,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if not composed_query:
        raise click.UsageError("Provide a QUERY or at least one advanced filter (e.g. --from, --lang).")

    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        _fetch_and_display(
            lambda count: client.fetch_search(composed_query, count, product, scope),
            "'%s' (%s, %s)" % (composed_query, product, scope),
            "🔍",
            max_count,
            as_json,
            as_yaml,
            output_file,
            do_filter,
            config,
            compact=compact, full_text=full_text,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max number of tweets to fetch.")
@structured_output_options
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweets to JSON file.")
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def likes(ctx, screen_name, max_count, as_json, as_yaml, output_file, do_filter, full_text):
    # type: (Any, str, int, bool, bool, Optional[str], bool, bool) -> None
    """Show tweets liked by a user. SCREEN_NAME is the @handle (without @).

    NOTE: Twitter/X made all likes private since June 2024. You can only view
    your own likes. Querying another user's likes will return empty results.
    """
    screen_name = screen_name.lstrip("@")
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)

        # Warn if querying another user's likes (Twitter made likes private since June 2024)
        try:
            me = client.fetch_me()
            if me.screen_name.lower() != screen_name.lower():
                if rich_output:
                    console.print(
                        "\n[yellow]⚠️  Twitter/X made all likes private since June 2024. "
                        "You can only view your own likes. "
                        "Querying @%s's likes will likely return empty results.[/yellow]\n" % screen_name
                    )
                else:
                    logger.warning(
                        "Twitter/X made likes private (June 2024). "
                        "Only your own likes are visible. @%s's likes will likely be empty.",
                        screen_name,
                    )
        except Exception:
            pass  # Don't block the command if whoami fails

        _fetch_and_display(
            lambda count: client.fetch_user_likes(profile.id, count),
            "@%s likes" % screen_name, "❤️", max_count, as_json, as_yaml, output_file, do_filter, config,
            compact=compact, full_text=full_text,
        )
    _run_guarded(_run)


@cli.command()
@click.argument("tweet_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max replies to fetch.")
@click.option(
    "--reply-scope",
    type=click.Choice(REPLY_SCOPES, case_sensitive=False),
    default="auto",
    help="Reply search scope: auto, recent, or all.",
)
@click.option("--full-text", is_flag=True, help="Show full reply text in table output.")
@structured_output_options
@click.pass_context
def tweet(ctx, tweet_id, max_count, reply_scope, full_text, as_json, as_yaml):
    # type: (Any, str, int, str, bool, bool, bool) -> None
    """View a tweet and its replies. TWEET_ID is the numeric tweet ID or full URL."""
    compact = ctx.obj.get("compact", False)
    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("🐦 Fetching tweet %s...\n" % tweet_id)
        start = time.time()
        tweets = client.fetch_tweet_detail(
            tweet_id,
            _resolve_configured_count(config, max_count),
            reply_scope=reply_scope,
        )
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    _emit_tweet_detail(tweets, compact=compact, as_json=as_json, as_yaml=as_yaml, full_text=full_text)


def _emit_tweet_detail(tweets, compact, as_json, as_yaml, full_text):
    # type: (list, bool, bool, bool, bool) -> None
    """Render tweet detail + replies in the requested output format."""
    if compact:
        click.echo(tweets_to_compact_json(tweets))
        return

    if emit_structured(tweets_to_data(tweets), as_json=as_json, as_yaml=as_yaml):
        return

    if tweets:
        print_tweet_detail(tweets[0], console)
        if len(tweets) > 1:
            console.print("\n💬 Replies:")
            print_tweet_table(tweets[1:], console, title="💬 Replies — %d" % (len(tweets) - 1), full_text=full_text)
    console.print()


def _print_show_hint():
    # type: () -> None
    """Print a hint about the `show` command."""
    console.print("[dim]💡 Use `twitter show <N>` to view tweet #N from this list.[/dim]")


@cli.command()
@click.argument("index", type=click.IntRange(1))
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max replies to fetch.")
@click.option(
    "--reply-scope",
    type=click.Choice(REPLY_SCOPES, case_sensitive=False),
    default="auto",
    help="Reply search scope: auto, recent, or all.",
)
@click.option("--full-text", is_flag=True, help="Show full reply text in table output.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save tweet detail as JSON to file.")
@structured_output_options
@click.pass_context
def show(ctx, index, max_count, reply_scope, full_text, output_file, as_json, as_yaml):
    # type: (Any, int, Optional[int], str, bool, Optional[str], bool, bool) -> None
    """View tweet #INDEX from the last feed/search results."""
    compact = ctx.obj.get("compact", False)

    tweet_id, cache_size = resolve_cached_tweet(index)
    if tweet_id is None:
        if cache_size == 0:
            raise click.UsageError(
                "No cached results found. Run `twitter feed`, `twitter search`, "
                "`twitter bookmarks`, or another list command first."
            )
        raise click.UsageError(
            "Index %d is out of range (cache has %d tweets)." % (index, cache_size)
        )

    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=compact)
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("🐦 Fetching tweet #%d (id: %s)...\n" % (index, tweet_id))
        start = time.time()
        tweets = client.fetch_tweet_detail(
            tweet_id,
            _resolve_configured_count(config, max_count),
            reply_scope=reply_scope,
        )
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d tweets in %.1fs\n" % (len(tweets), elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if output_file:
        Path(output_file).write_text(tweets_to_json(tweets), encoding="utf-8")
        if rich_output:
            console.print("💾 Saved to %s\n" % output_file)

    _emit_tweet_detail(tweets, compact=compact, as_json=as_json, as_yaml=as_yaml, full_text=full_text)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
@click.option("--markdown", "-m", "as_markdown", is_flag=True, help="Output article as Markdown.")
@click.option("--output", "-o", "output_file", type=str, default=None, help="Save article Markdown to file.")
@click.pass_context
def article(ctx, tweet_id, as_json, as_yaml, as_markdown, output_file):
    # type: (Any, str, bool, bool, bool, Optional[str]) -> None
    """Fetch a Twitter Article. TWEET_ID is the numeric tweet ID or full URL."""
    compact = ctx.obj.get("compact", False)
    if compact:
        raise click.UsageError("`twitter article` does not support --compact. Use --markdown or --output.")
    if as_markdown and (as_json or as_yaml):
        raise click.UsageError("Use only one of --markdown, --json, or --yaml.")

    tweet_id = _normalize_tweet_id(tweet_id)
    config = load_config()
    rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml, compact=False) and not as_markdown
    try:
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("📰 Fetching article %s...\n" % tweet_id)
        start = time.time()
        article_tweet = client.fetch_article(tweet_id)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched article in %.1fs\n" % elapsed)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    markdown = article_to_markdown(article_tweet)
    if output_file:
        Path(output_file).write_text(markdown, encoding="utf-8")
        if rich_output:
            console.print("💾 Saved article Markdown to %s\n" % output_file)

    if as_markdown:
        click.echo(markdown, nl=False)
        return
    if emit_structured(tweet_to_dict(article_tweet), as_json=as_json, as_yaml=as_yaml):
        return

    print_article(article_tweet, console)
    console.print()


@cli.command(name="list")
@click.argument("list_id")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max tweets to fetch.")
@structured_output_options
@click.option("--filter", "do_filter", is_flag=True, help="Enable score-based filtering.")
@click.option("--full-text", is_flag=True, help="Show full tweet text in table output.")
@click.pass_context
def list_timeline(ctx, list_id, max_count, as_json, as_yaml, do_filter, full_text):
    # type: (Any, str, int, bool, bool, bool, bool) -> None
    """Fetch tweets from a Twitter List. LIST_ID is the numeric list ID."""
    compact = ctx.obj.get("compact", False)
    config = load_config()
    def _run():
        client = _get_client(config)
        _fetch_and_display(
            lambda count: client.fetch_list_timeline(list_id, count),
            "list %s" % list_id, "📋", max_count, as_json, as_yaml, None, do_filter, config,
            compact=compact, full_text=full_text,
        )
    _run_guarded(_run)


@cli.command(name="list-info")
@click.argument("list_id")
@structured_output_options
def list_info(list_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Show metadata for a Twitter List. LIST_ID is the numeric list ID."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("📋 Fetching list %s..." % list_id)
        twitter_list = client.fetch_list(list_id)
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(twitter_list_to_dict(twitter_list), as_json=as_json, as_yaml=as_yaml):
        return

    console.print()
    print_list_detail(twitter_list, console)
    console.print()


def _fetch_and_display_lists(
    screen_name: str,
    fetch_fn_name: str,
    label: str,
    max_count: Optional[int],
    as_json: bool,
    as_yaml: bool,
) -> None:
    """Shared fetch-and-display logic for list lookup commands."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("📚 Fetching %s (%d)...\n" % (label, fetch_count))
        start = time.time()
        twitter_lists = getattr(client, fetch_fn_name)(profile.id, fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d %s in %.1fs\n" % (len(twitter_lists), label, elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(twitter_lists_to_data(twitter_lists), as_json=as_json, as_yaml=as_yaml):
        return

    print_list_table(twitter_lists, console, title="📚 @%s %s — %d" % (screen_name, label, len(twitter_lists)))
    console.print()


@cli.command(name="owned-lists")
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max lists to fetch.")
@structured_output_options
def owned_lists(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List Twitter Lists owned by a user. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_lists(screen_name, "fetch_owned_lists", "owned lists", max_count, as_json, as_yaml)


@cli.command(name="followed-lists")
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max lists to fetch.")
@structured_output_options
def followed_lists(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List Twitter Lists followed by a user. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_lists(
        screen_name,
        "fetch_followed_lists",
        "followed lists",
        max_count,
        as_json,
        as_yaml,
    )


def _fetch_and_display_users(
    screen_name: str,
    fetch_fn_name: str,
    label: str,
    max_count: Optional[int],
    as_json: bool,
    as_yaml: bool,
) -> None:
    """Shared fetch-and-display logic for followers/following commands."""
    screen_name = screen_name.lstrip("@")
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching @%s's profile..." % screen_name)
        profile = client.fetch_user(screen_name)
        fetch_count = _resolve_configured_count(config, max_count)
        if rich_output:
            console.print("👥 Fetching %s (%d)...\n" % (label, fetch_count))
        start = time.time()
        users = getattr(client, fetch_fn_name)(profile.id, fetch_count)
        elapsed = time.time() - start
        if rich_output:
            console.print("✅ Fetched %d %s in %.1fs\n" % (len(users), label, elapsed))
    except (TwitterError, RuntimeError) as exc:
        _exit_with_error(exc)

    if emit_structured(users_to_data(users), as_json=as_json, as_yaml=as_yaml):
        return

    print_user_table(users, console, title="👥 @%s %s — %d" % (screen_name, label, len(users)))
    console.print()


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def followers(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List followers of a user. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_users(screen_name, "fetch_followers", "followers", max_count, as_json, as_yaml)


@cli.command()
@click.argument("screen_name")
@click.option("--max", "-n", "max_count", type=int, default=None, help="Max users to fetch.")
@structured_output_options
def following(screen_name, max_count, as_json, as_yaml):
    # type: (str, int, bool, bool) -> None
    """List accounts a user is following. SCREEN_NAME is the @handle (without @)."""
    _fetch_and_display_users(screen_name, "fetch_following", "following", max_count, as_json, as_yaml)


# ── Write commands ──────────────────────────────────────────────────────

_MAX_IMAGES = 4  # Twitter allows up to 4 images per tweet


def _upload_media(client, image_paths, video_path=None, alt_text=None, rich_output=True):
    # type: (Any, tuple, Optional[str], Optional[str], bool) -> list
    """Upload selected media and return list of media_id strings."""
    if image_paths and video_path:
        raise click.UsageError("Use either --image or --video/--file, not both.")
    if alt_text and not isinstance(client, TwitterAPIv2Client):
        raise click.UsageError("--alt-text currently requires --auth-mode api.")
    if video_path and not isinstance(client, TwitterAPIv2Client):
        raise click.UsageError("Video upload currently requires --auth-mode api.")
    if alt_text and not (video_path or len(image_paths) == 1):
        raise click.UsageError("--alt-text requires exactly one uploaded image or one uploaded video.")

    if video_path:
        if rich_output:
            console.print("📤 Uploading video: %s" % video_path)
        return [client.upload_media(video_path, alt_text=alt_text)]

    if not image_paths:
        return []
    if len(image_paths) > _MAX_IMAGES:
        raise click.UsageError("Too many images: max %d, got %d" % (_MAX_IMAGES, len(image_paths)))
    media_ids = []
    for i, path in enumerate(image_paths, 1):
        if rich_output:
            console.print("📤 Uploading image %d/%d: %s" % (i, len(image_paths), path))
        media_ids.append(client.upload_media(path, alt_text=alt_text if len(image_paths) == 1 else None))
    return media_ids


def _write_action(emoji, action_desc, client_method, tweet_id, as_json=False, as_yaml=False):
    # type: (str, str, str, str, bool, bool) -> None
    """Generic write action helper to reduce CLI command boilerplate.

    Emits structured JSON/YAML when piped or when OUTPUT env is set.
    """
    action_name = action_desc.lower().replace(" ", "_")

    def operation(client: Any) -> WritePayload:
        getattr(client, client_method)(tweet_id)
        return {"success": True, "action": action_name, "id": tweet_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["%s %s %s..." % (emoji, action_desc, tweet_id)],
        success_lines=["[green]✅ Done.[/green]"],
        error_details={"action": action_name, "id": tweet_id},
    )


@cli.command()
@click.argument("text")
@click.option("--reply-to", "-r", default=None, help="Reply to this tweet ID.")
@click.option("--image", "-i", "images", multiple=True, type=click.Path(exists=True), help="Attach image (up to 4). Repeatable.")
@click.option("--video", "--file", "video_path", type=click.Path(exists=True), default=None, help="Attach one video file in API mode.")
@click.option("--alt-text", type=str, default=None, help="Optional alt text for a single uploaded media file.")
@structured_output_options
def post(text, reply_to, images, video_path, alt_text, as_json, as_yaml):
    # type: (str, Optional[str], tuple, Optional[str], Optional[str], bool, bool) -> None
    """Post a new tweet. TEXT is the tweet content.

    Attach images with --image / -i (up to 4):

    \b
      twitter post "Hello!" --image photo.jpg
      twitter post "Gallery" -i a.png -i b.png -i c.jpg
    """
    action = "Replying to %s" % reply_to if reply_to else "Posting tweet"
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)

    def operation(client: Any) -> WritePayload:
        media_ids = _upload_media(
            client,
            images,
            video_path=video_path,
            alt_text=alt_text,
            rich_output=rich_output,
        )
        tweet_id = client.create_tweet(text, reply_to_id=reply_to, media_ids=media_ids or None)
        return {"success": True, "action": "post", "id": tweet_id, "url": "https://x.com/i/status/%s" % tweet_id}

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["✏️  %s..." % action],
        success_lines=["[green]✅ Tweet posted![/green]"],
        error_details={"action": "post", "replyTo": reply_to},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 %s" % payload["url"])


@cli.command(name="reply")
@click.argument("tweet_id")
@click.argument("text")
@click.option("--image", "-i", "images", multiple=True, type=click.Path(exists=True), help="Attach image (up to 4). Repeatable.")
@click.option("--video", "--file", "video_path", type=click.Path(exists=True), default=None, help="Attach one video file in API mode.")
@click.option("--alt-text", type=str, default=None, help="Optional alt text for a single uploaded media file.")
@structured_output_options
def reply_tweet(tweet_id, text, images, video_path, alt_text, as_json, as_yaml):
    # type: (str, str, tuple, Optional[str], Optional[str], bool, bool) -> None
    """Reply to a tweet. TWEET_ID is the tweet to reply to, TEXT is the reply content."""
    tweet_id = _normalize_tweet_id(tweet_id)
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)
    def operation(client: Any) -> WritePayload:
        media_ids = _upload_media(
            client,
            images,
            video_path=video_path,
            alt_text=alt_text,
            rich_output=rich_output,
        )
        new_id = client.create_tweet(text, reply_to_id=tweet_id, media_ids=media_ids or None)
        return {
            "success": True,
            "action": "reply",
            "id": new_id,
            "replyTo": tweet_id,
            "url": "https://x.com/i/status/%s" % new_id,
        }

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["💬 Replying to %s..." % tweet_id],
        success_lines=["[green]✅ Reply posted![/green]"],
        error_details={"action": "reply", "replyTo": tweet_id},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 %s" % payload["url"])


@cli.command(name="quote")
@click.argument("tweet_id")
@click.argument("text")
@click.option("--image", "-i", "images", multiple=True, type=click.Path(exists=True), help="Attach image (up to 4). Repeatable.")
@click.option("--video", "--file", "video_path", type=click.Path(exists=True), default=None, help="Attach one video file in API mode.")
@click.option("--alt-text", type=str, default=None, help="Optional alt text for a single uploaded media file.")
@structured_output_options
def quote_tweet(tweet_id, text, images, video_path, alt_text, as_json, as_yaml):
    # type: (str, str, tuple, Optional[str], Optional[str], bool, bool) -> None
    """Quote-tweet a tweet. TWEET_ID is the tweet to quote, TEXT is the commentary."""
    tweet_id = _normalize_tweet_id(tweet_id)
    rich_output = not _structured_mode(as_json=as_json, as_yaml=as_yaml)
    def operation(client: Any) -> WritePayload:
        media_ids = _upload_media(
            client,
            images,
            video_path=video_path,
            alt_text=alt_text,
            rich_output=rich_output,
        )
        new_id = client.quote_tweet(tweet_id, text, media_ids=media_ids or None)
        return {
            "success": True,
            "action": "quote",
            "id": new_id,
            "quotedId": tweet_id,
            "url": "https://x.com/i/status/%s" % new_id,
        }

    payload = _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["🔄 Quoting tweet %s..." % tweet_id],
        success_lines=["[green]✅ Quote tweet posted![/green]"],
        error_details={"action": "quote", "quotedId": tweet_id},
    )
    if payload and not _structured_mode(as_json=as_json, as_yaml=as_yaml):
        console.print("🔗 %s" % payload["url"]) 


@cli.command(name="status")
@structured_output_options
def status(as_json, as_yaml):
    # type: (bool, bool) -> None
    """Check whether the current Twitter/X session is authenticated."""
    config = load_config()
    try:
        rich_output = use_rich_output(as_json=as_json, as_yaml=as_yaml)
        client = _get_client(config, quiet=not rich_output)
        profile = client.fetch_me()
    except (TwitterError, RuntimeError) as exc:
        payload = error_payload(_error_code_from_exc(exc), str(exc))
        if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
            sys.exit(1)
        _exit_with_error(exc)
        return

    payload = success_payload({"authenticated": True, "user": _agent_user_profile(profile)})
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
        client = _get_client(config, quiet=not rich_output)
        if rich_output:
            console.print("👤 Fetching current user...")
        profile = client.fetch_me()
    except (TwitterError, RuntimeError) as exc:
        if emit_structured(error_payload(_error_code_from_exc(exc), str(exc)), as_json=as_json, as_yaml=as_yaml):
            raise SystemExit(1) from None
        _exit_with_error(exc)

    if not emit_structured(success_payload({"user": _agent_user_profile(profile)}), as_json=as_json, as_yaml=as_yaml):
        console.print()
        print_user_profile(profile, console)


@cli.command(name="follow")
@click.argument("screen_name")
@structured_output_options
def follow_user(screen_name, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Follow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: Any) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.follow_user(user_id)
        return {"success": True, "action": "follow", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["👤 Looking up @%s..." % screen_name, "➕ Following @%s..." % screen_name],
        success_lines=["[green]✅ Now following @%s[/green]" % screen_name],
        error_details={"action": "follow", "screenName": screen_name},
    )


@cli.command(name="unfollow")
@click.argument("screen_name")
@structured_output_options
def unfollow_user(screen_name, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Unfollow a user. SCREEN_NAME is the @handle (without @)."""
    screen_name = screen_name.lstrip("@")

    def operation(client: Any) -> WritePayload:
        user_id = client.resolve_user_id(screen_name)
        client.unfollow_user(user_id)
        return {"success": True, "action": "unfollow", "screenName": screen_name, "userId": user_id}

    _run_write_command(
        as_json=as_json,
        as_yaml=as_yaml,
        operation=operation,
        progress_lines=["👤 Looking up @%s..." % screen_name, "➖ Unfollowing @%s..." % screen_name],
        success_lines=["[green]✅ Unfollowed @%s[/green]" % screen_name],
        error_details={"action": "unfollow", "screenName": screen_name},
    )


@cli.command(name="delete")
@click.argument("tweet_id")
@click.confirmation_option(prompt="Are you sure you want to delete this tweet?")
@structured_output_options
def delete_tweet(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Delete a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🗑️", "Deleting tweet", "delete_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def like(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Like a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("❤️", "Liking tweet", "like_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unlike(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Unlike a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("💔", "Unliking tweet", "unlike_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def retweet(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Retweet a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Retweeting", "retweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unretweet(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Undo a retweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔄", "Undoing retweet", "unretweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def favorite(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Bookmark (favorite) a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def bookmark(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Bookmark a tweet. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Bookmarking tweet", "bookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unfavorite(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Remove a tweet from bookmarks (unfavorite). TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Removing bookmark", "unbookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)


@cli.command()
@click.argument("tweet_id")
@structured_output_options
def unbookmark(tweet_id, as_json, as_yaml):
    # type: (str, bool, bool) -> None
    """Remove a tweet from bookmarks. TWEET_ID is the numeric tweet ID."""
    _write_action("🔖", "Removing bookmark", "unbookmark_tweet", tweet_id, as_json=as_json, as_yaml=as_yaml)




if __name__ == "__main__":
    cli()
