"""Response parsing for Twitter GraphQL API.

Converts raw GraphQL response JSON into domain model objects
(Tweet, UserProfile, Author, etc.).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple  # noqa: F401

from .models import Author, Metrics, Tweet, TweetMedia, UserProfile

logger = logging.getLogger(__name__)


# ── Utility helpers ──────────────────────────────────────────────────────


def _deep_get(data, *keys):
    # type: (Any, *Any) -> Any
    """Safely get nested dict/list values.  Supports int keys for list access."""
    current = data
    for key in keys:
        if isinstance(key, int):
            if isinstance(current, list) and 0 <= key < len(current):
                current = current[key]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _parse_int(value, default):
    # type: (Any, int) -> int
    """Best-effort integer conversion.  Handles commas and float strings."""
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _extract_cursor(content):
    # type: (Dict[str, Any]) -> Optional[str]
    """Extract Bottom pagination cursor from timeline content."""
    if content.get("cursorType") == "Bottom":
        return content.get("value")
    return None


# ── Media / Author extraction ────────────────────────────────────────────


def _extract_media(legacy):
    # type: (Dict[str, Any]) -> List[TweetMedia]
    """Extract media items from tweet legacy data."""
    media = []  # type: List[TweetMedia]
    for media_item in _deep_get(legacy, "extended_entities", "media") or []:
        media_type = media_item.get("type", "")
        if media_type == "photo":
            media.append(
                TweetMedia(
                    type="photo",
                    url=media_item.get("media_url_https", ""),
                    width=_deep_get(media_item, "original_info", "width"),
                    height=_deep_get(media_item, "original_info", "height"),
                )
            )
        elif media_type in {"video", "animated_gif"}:
            variants = media_item.get("video_info", {}).get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
            mp4_variants.sort(key=lambda v: v.get("bitrate", 0), reverse=True)
            media.append(
                TweetMedia(
                    type=media_type,
                    url=mp4_variants[0]["url"] if mp4_variants else media_item.get("media_url_https", ""),
                    width=_deep_get(media_item, "original_info", "width"),
                    height=_deep_get(media_item, "original_info", "height"),
                )
            )
    return media


def _extract_author(user_data, user_legacy):
    # type: (Dict[str, Any], Dict[str, Any]) -> Author
    """Extract Author from user result data."""
    user_core = user_data.get("core", {})
    return Author(
        id=user_data.get("rest_id", ""),
        name=user_core.get("name") or user_legacy.get("name") or user_data.get("name", "Unknown"),
        screen_name=(
            user_core.get("screen_name")
            or user_legacy.get("screen_name")
            or user_data.get("screen_name", "unknown")
        ),
        profile_image_url=(
            user_data.get("avatar", {}).get("image_url")
            or user_legacy.get("profile_image_url_https", "")
        ),
        verified=bool(user_data.get("is_blue_verified") or user_legacy.get("verified", False)),
    )


# ── Article parsing ──────────────────────────────────────────────────────


def _find_article_image_url(value):
    # type: (Any) -> Optional[str]
    """Best-effort extraction of the original image URL from article entity data."""
    if isinstance(value, dict):
        for key in (
            "original_img_url",
            "originalImgUrl",
            "original_url",
            "originalUrl",
            "media_url_https",
            "mediaUrlHttps",
            "media_url",
            "mediaUrl",
            "url",
            "src",
            "uri",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                lowered = candidate.lower()
                if (
                    lowered.startswith("https://pbs.twimg.com/")
                    or lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
                    or any(ext in lowered for ext in (".jpg?", ".jpeg?", ".png?", ".gif?", ".webp?"))
                ):
                    return candidate.strip()
        for nested in value.values():
            found = _find_article_image_url(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_article_image_url(item)
            if found:
                return found
    return None


def _find_article_caption(value):
    # type: (Any) -> Optional[str]
    """Best-effort extraction of image caption/alt text from article entity data."""
    if isinstance(value, dict):
        for key in ("caption", "alt", "alt_text", "altText", "title", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for nested in value.values():
            found = _find_article_caption(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_article_caption(item)
            if found:
                return found
    return None


def _normalize_article_entity_map(entity_map):
    # type: (Any) -> Dict[str, Any]
    """Normalize Draft.js entityMap that may arrive as dict or [{key, value}, ...]."""
    if isinstance(entity_map, dict):
        normalized = {}  # type: Dict[str, Any]
        for key, value in entity_map.items():
            normalized[str(key)] = value
        return normalized
    if isinstance(entity_map, list):
        normalized = {}  # type: Dict[str, Any]
        for item in entity_map:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            value = item.get("value")
            if key is None or value is None:
                continue
            normalized[str(key)] = value
        return normalized
    return {}


def _extract_article_media_url_map(article_results):
    # type: (Dict[str, Any]) -> Dict[str, str]
    """Map article media ids/keys to original image URLs when article entities reference IDs only."""
    media_url_map = {}  # type: Dict[str, str]
    media_candidates = []  # type: List[Any]

    cover_media = article_results.get("cover_media")
    if cover_media:
        media_candidates.append(cover_media)
    media_candidates.extend(article_results.get("media_entities") or [])

    for media in media_candidates:
        if not isinstance(media, dict):
            continue
        media_info = media.get("media_info") or {}
        image_url = _find_article_image_url(media_info) or _find_article_image_url(media)
        if not image_url:
            continue
        for key in ("media_id", "media_key", "id"):
            candidate = media.get(key)
            if isinstance(candidate, str) and candidate:
                media_url_map[candidate] = image_url
    return media_url_map


def _extract_article_images(block, entity_map, media_url_map):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, str]) -> List[str]
    """Convert atomic Draft.js image entities to Markdown image lines."""
    parts = []  # type: List[str]
    for entity_range in block.get("entityRanges", []) or []:
        if not isinstance(entity_range, dict):
            continue
        entity_key = entity_range.get("key")
        entity = entity_map.get(str(entity_key)) if entity_key is not None else None
        if not isinstance(entity, dict):
            continue
        image_url = _find_article_image_url(entity)
        if not image_url:
            media_items = _deep_get(entity, "data", "mediaItems") or []
            for media_item in media_items:
                media_id = media_item.get("mediaId") if isinstance(media_item, dict) else None
                if isinstance(media_id, str) and media_id in media_url_map:
                    image_url = media_url_map[media_id]
                    break
        if not image_url:
            continue
        caption = _find_article_caption(entity) or ""
        parts.append("![%s](%s)" % (caption, image_url))
    return parts


def _parse_article(tweet_data):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Extract Twitter Article data (long-form content) from a tweet.

    Returns dict with 'article_title' and 'article_text' keys (None if not an article).
    Converts draft.js content blocks to Markdown.
    """
    article_results = _deep_get(tweet_data, "article", "article_results", "result")
    if not article_results:
        return {"article_title": None, "article_text": None}

    title = article_results.get("title")  # type: Optional[str]
    content_state = article_results.get("content_state", {})
    blocks = content_state.get("blocks", [])
    if not blocks:
        return {"article_title": title, "article_text": None}

    entity_map = _normalize_article_entity_map(content_state.get("entityMap", {}))
    media_url_map = _extract_article_media_url_map(article_results)

    # Convert draft.js blocks to Markdown
    parts = []  # type: List[str]
    ordered_counter = 0
    for block in blocks:
        block_type = block.get("type", "unstyled")  # type: str
        if block_type == "atomic":
            parts.extend(_extract_article_images(block, entity_map, media_url_map))
            ordered_counter = 0
            continue
        text = block.get("text", "")  # type: str
        if not text:
            continue
        if block_type != "ordered-list-item":
            ordered_counter = 0
        if block_type == "header-one":
            parts.append("# %s" % text)
        elif block_type == "header-two":
            parts.append("## %s" % text)
        elif block_type == "header-three":
            parts.append("### %s" % text)
        elif block_type == "blockquote":
            parts.append("> %s" % text)
        elif block_type == "unordered-list-item":
            parts.append("- %s" % text)
        elif block_type == "ordered-list-item":
            ordered_counter += 1
            parts.append("%d. %s" % (ordered_counter, text))
        elif block_type == "code-block":
            parts.append("```\n%s\n```" % text)
        else:
            parts.append(text)

    return {
        "article_title": title,
        "article_text": "\n\n".join(parts) if parts else None,
    }


# ── User parsing ─────────────────────────────────────────────────────────


def parse_user_result(user_data):
    # type: (Dict[str, Any]) -> Optional[UserProfile]
    """Parse a user result object into UserProfile."""
    if user_data.get("__typename") == "UserUnavailable":
        return None
    legacy = user_data.get("legacy", {})
    if not legacy:
        return None
    return UserProfile(
        id=user_data.get("rest_id", ""),
        name=legacy.get("name", ""),
        screen_name=legacy.get("screen_name", ""),
        bio=legacy.get("description", ""),
        location=legacy.get("location", ""),
        url=_deep_get(legacy, "entities", "url", "urls", 0, "expanded_url") or "",
        followers_count=_parse_int(legacy.get("followers_count"), 0),
        following_count=_parse_int(legacy.get("friends_count"), 0),
        tweets_count=_parse_int(legacy.get("statuses_count"), 0),
        likes_count=_parse_int(legacy.get("favourites_count"), 0),
        verified=user_data.get("is_blue_verified", False) or legacy.get("verified", False),
        profile_image_url=legacy.get("profile_image_url_https", ""),
        created_at=legacy.get("created_at", ""),
    )


# ── Tweet parsing ────────────────────────────────────────────────────────


def parse_tweet_result(result, depth=0):
    # type: (Dict[str, Any], int) -> Optional[Tweet]
    """Parse a single TweetResult into a Tweet dataclass."""
    if depth > 2:
        return None

    tweet_data = result
    if result.get("__typename") == "TweetWithVisibilityResults" and result.get("tweet"):
        tweet_data = result["tweet"]
    if tweet_data.get("__typename") == "TweetTombstone":
        return None

    legacy = tweet_data.get("legacy")
    core = tweet_data.get("core")
    if not isinstance(legacy, dict) or not isinstance(core, dict):
        return None

    user = _deep_get(core, "user_results", "result") or {}
    user_legacy = user.get("legacy", {})
    user_core = user.get("core", {})

    is_retweet = bool(_deep_get(legacy, "retweeted_status_result", "result"))
    actual_data = tweet_data
    actual_legacy = legacy
    actual_user = user
    actual_user_legacy = user_legacy

    if is_retweet:
        retweet_result = _deep_get(legacy, "retweeted_status_result", "result") or {}
        if retweet_result.get("__typename") == "TweetWithVisibilityResults" and retweet_result.get("tweet"):
            retweet_result = retweet_result["tweet"]
        rt_legacy = retweet_result.get("legacy")
        rt_core = retweet_result.get("core")
        if isinstance(rt_legacy, dict) and isinstance(rt_core, dict):
            actual_data = retweet_result
            actual_legacy = rt_legacy
            actual_user = _deep_get(rt_core, "user_results", "result") or {}
            actual_user_legacy = actual_user.get("legacy", {})

    media = _extract_media(actual_legacy)
    urls = [item.get("expanded_url", "") for item in _deep_get(actual_legacy, "entities", "urls") or []]
    quoted = _deep_get(actual_data, "quoted_status_result", "result")
    quoted_tweet = parse_tweet_result(quoted, depth=depth + 1) if isinstance(quoted, dict) else None
    author = _extract_author(actual_user, actual_user_legacy)

    retweeted_by = None  # type: Optional[str]
    if is_retweet:
        retweeted_by = user_core.get("screen_name") or user_legacy.get("screen_name", "unknown")

    # Prefer note_tweet full text for long tweets ("Show More")
    note_text = _deep_get(actual_data, "note_tweet", "note_tweet_results", "result", "text")

    return Tweet(
        id=actual_data.get("rest_id", ""),
        text=note_text or actual_legacy.get("full_text", ""),
        author=author,
        metrics=Metrics(
            likes=_parse_int(actual_legacy.get("favorite_count"), 0),
            retweets=_parse_int(actual_legacy.get("retweet_count"), 0),
            replies=_parse_int(actual_legacy.get("reply_count"), 0),
            quotes=_parse_int(actual_legacy.get("quote_count"), 0),
            views=_parse_int(_deep_get(actual_data, "views", "count"), 0),
            bookmarks=_parse_int(actual_legacy.get("bookmark_count"), 0),
        ),
        created_at=actual_legacy.get("created_at", ""),
        media=media,
        urls=urls,
        is_retweet=is_retweet,
        retweeted_by=retweeted_by,
        quoted_tweet=quoted_tweet,
        lang=actual_legacy.get("lang", ""),
        **_parse_article(actual_data),
    )


# ── Timeline response parsing ───────────────────────────────────────────


def parse_timeline_response(data, get_instructions):
    # type: (Any, Callable[[Any], Any]) -> Tuple[List[Tweet], Optional[str]]
    """Parse timeline GraphQL response into tweets and next cursor."""
    tweets = []  # type: List[Tweet]
    next_cursor = None  # type: Optional[str]

    instructions = get_instructions(data)
    if not isinstance(instructions, list):
        logger.warning("No timeline instructions found")
        return tweets, next_cursor

    for instruction in instructions:
        entries = instruction.get("entries") or instruction.get("moduleItems") or []
        for entry in entries:
            content = entry.get("content", {})
            next_cursor = _extract_cursor(content) or next_cursor

            item_content = content.get("itemContent", {})
            result = _deep_get(item_content, "tweet_results", "result")
            if result:
                tweet = parse_tweet_result(result)
                if tweet:
                    tweets.append(tweet)

            for nested_item in content.get("items", []):
                nested_result = _deep_get(
                    nested_item,
                    "item",
                    "itemContent",
                    "tweet_results",
                    "result",
                )
                if nested_result:
                    tweet = parse_tweet_result(nested_result)
                    if tweet:
                        tweets.append(tweet)

    return tweets, next_cursor
