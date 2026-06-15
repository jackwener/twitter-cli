"""Optional Xquik search provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Tuple, Type

from .exceptions import (
    AuthenticationError,
    InvalidInputError,
    NetworkError,
    RateLimitError,
    TwitterError,
)
from .models import Author, Metrics, Tweet, TweetMedia

_MAX_RESULTS = 200
_PRODUCT_OPTIONS = {
    "Top": {"query_type": "Top"},
    "Latest": {"query_type": "Latest"},
    "Photos": {"query_type": "Latest", "media_type": "images"},
    "Videos": {"query_type": "Latest", "media_type": "videos"},
}
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _load_client() -> Tuple[Any, Type[Exception]]:
    try:
        import x_twitter_scraper
    except ImportError as exc:
        raise TwitterError(
            "Xquik search support is not installed. "
            "Install it with `uv tool install 'twitter-cli[xquik]'`."
        ) from exc
    return x_twitter_scraper.XTwitterScraper(), x_twitter_scraper.APIError


def _raise_api_error(exc: Exception) -> None:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        raise AuthenticationError(
            "Xquik authentication failed. Set X_TWITTER_SCRAPER_API_KEY and retry."
        ) from exc
    if status == 402:
        raise TwitterError("Xquik search needs available credits. Add credits and retry.") from exc
    if status == 429:
        raise RateLimitError("Xquik rate limit reached. Retry later.") from exc
    if status in (400, 422):
        raise InvalidInputError("Xquik rejected the search query or filters.") from exc
    if status is None:
        raise NetworkError("Xquik search could not reach the service. Retry later.") from exc
    raise TwitterError("Xquik search failed with HTTP %s. Retry later." % status) from exc


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _twitter_timestamp(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    offset = parsed.strftime("%z")
    return "%s %s %02d %s %s %04d" % (
        _WEEKDAYS[parsed.weekday()],
        _MONTHS[parsed.month - 1],
        parsed.day,
        parsed.strftime("%H:%M:%S"),
        offset,
        parsed.year,
    )


def _author(value: Any) -> Author:
    if value is None:
        return Author(id="", name="", screen_name="")
    return Author(
        id=str(getattr(value, "id", "") or ""),
        name=str(getattr(value, "name", "") or ""),
        screen_name=str(getattr(value, "username", "") or ""),
        profile_image_url=str(getattr(value, "profile_picture", "") or ""),
        verified=bool(
            getattr(value, "verified", False)
            or getattr(value, "is_verified", False)
            or getattr(value, "is_blue_verified", False)
        ),
    )


def _media(values: Any) -> List[TweetMedia]:
    return [
        TweetMedia(
            type=str(getattr(item, "type", "") or ""),
            url=str(getattr(item, "media_url", "") or getattr(item, "url", "") or ""),
            width=getattr(item, "width", None),
            height=getattr(item, "height", None),
        )
        for item in values or []
    ]


def _urls(entities: Any) -> List[str]:
    if not isinstance(entities, dict):
        return []
    items = entities.get("urls") or []
    return [
        str(item.get("expandedUrl") or item.get("expanded_url") or item.get("url"))
        for item in items
        if isinstance(item, dict)
        and (item.get("expandedUrl") or item.get("expanded_url") or item.get("url"))
    ]


def _tweet(value: Any, depth: int = 0) -> Tweet:
    retweeted = getattr(value, "retweeted_tweet", None)
    actual = retweeted or value
    quoted = getattr(actual, "quoted_tweet", None)
    note_tweet = getattr(actual, "note_tweet", None)
    article = getattr(actual, "article", None)
    retweeted_by = None
    if retweeted is not None:
        retweeted_by = _author(getattr(value, "author", None)).screen_name or None
    return Tweet(
        id=str(getattr(actual, "id", "") or ""),
        text=str(getattr(note_tweet, "text", "") or getattr(actual, "text", "") or ""),
        author=_author(getattr(actual, "author", None)),
        metrics=Metrics(
            likes=_integer(getattr(actual, "like_count", 0)),
            retweets=_integer(getattr(actual, "retweet_count", 0)),
            replies=_integer(getattr(actual, "reply_count", 0)),
            quotes=_integer(getattr(actual, "quote_count", 0)),
            views=_integer(getattr(actual, "view_count", 0)),
            bookmarks=_integer(getattr(actual, "bookmark_count", 0)),
        ),
        created_at=_twitter_timestamp(getattr(actual, "created_at", "")),
        media=_media(getattr(actual, "media", None)),
        urls=_urls(getattr(actual, "entities", None)),
        is_retweet=retweeted is not None,
        retweeted_by=retweeted_by,
        quoted_tweet=_tweet(quoted, depth + 1) if quoted is not None and depth < 2 else None,
        lang=str(getattr(actual, "lang", "") or ""),
        article_title=str(getattr(article, "title", "") or "") or None,
    )


def _request(client: Any, query: str, count: int, product: str) -> Any:
    options = _PRODUCT_OPTIONS.get(product)
    if options is None:
        raise InvalidInputError("Xquik does not support search type %s." % product)
    if count < 1 or count > _MAX_RESULTS:
        raise InvalidInputError("Xquik search accepts --max from 1 to %d." % _MAX_RESULTS)
    return client.x.tweets.search(q=query, limit=count, **options)


def fetch_xquik_search(
    query: str,
    count: int = 20,
    product: str = "Top",
    *,
    client: Any = None,
) -> List[Tweet]:
    """Search through the optional Xquik SDK and return native Tweet models."""
    if client is not None:
        return [_tweet(item) for item in _request(client, query, count, product).tweets]

    sdk_client, api_error = _load_client()
    try:
        page = _request(sdk_client, query, count, product)
    except api_error as exc:
        _raise_api_error(exc)
        raise AssertionError("unreachable")
    return [_tweet(item) for item in page.tweets]
