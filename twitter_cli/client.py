"""Twitter GraphQL API client."""

from __future__ import annotations

import json
import logging
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .models import Author, Metrics, Tweet, TweetMedia, UserProfile

logger = logging.getLogger(__name__)


BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

FALLBACK_QUERY_IDS = {
    "HomeTimeline": "c-CzHF1LboFilMpsx4ZCrQ",
    "HomeLatestTimeline": "BKB7oi212Fi7kQtCBGE4zA",
    "Bookmarks": "VFdMm9iVZxlU6hD86gfW_A",
    "UserByScreenName": "1VOOyvKkiI3FMmkeDNxM9A",
    "UserTweets": "E3opETHurmVJflFsUBVuUQ",
    "Likes": "yrFhBQCFo96rIBH28a4T2A",
    "Viewer": "zWQLM9HIVahRSUvzUH4lDw",
}

TWITTER_OPENAPI_URL = (
    "https://raw.githubusercontent.com/fa0311/twitter-openapi/"
    "main/src/config/placeholder.json"
)

FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_cached_query_ids = {}  # type: Dict[str, str]
_bundles_scanned = False


class TwitterAPIError(RuntimeError):
    """Represents HTTP/network errors from Twitter APIs."""

    def __init__(self, status_code, message):
        # type: (int, str) -> None
        super().__init__(message)
        self.status_code = status_code


def _create_ssl_context():
    # type: () -> ssl.SSLContext
    """Create SSL context for urllib."""
    return ssl.create_default_context()


def _url_fetch(url, headers=None):
    # type: (str, Optional[Dict[str, str]]) -> str
    """Simple URL fetch for metadata/bootstrap lookups."""
    req = urllib.request.Request(url)
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    with urllib.request.urlopen(req, context=_create_ssl_context(), timeout=30) as response:
        return response.read().decode("utf-8")


def _build_graphql_url(query_id, operation_name, variables, features):
    # type: (str, str, Dict[str, Any], Dict[str, Any]) -> str
    """Build GraphQL GET URL with encoded variables/features."""
    return "https://x.com/i/api/graphql/%s/%s?variables=%s&features=%s" % (
        query_id,
        operation_name,
        urllib.parse.quote(json.dumps(variables, separators=(",", ":"))),
        urllib.parse.quote(json.dumps(features, separators=(",", ":"))),
    )


def _scan_bundles():
    # type: () -> None
    """Scan Twitter JS bundles and cache queryId mappings."""
    global _bundles_scanned
    if _bundles_scanned:
        return
    _bundles_scanned = True

    try:
        html = _url_fetch("https://x.com", {"user-agent": USER_AGENT})
        script_pattern = re.compile(
            r'(?:src|href)=["\']'
            r'(https://abs\.twimg\.com/responsive-web/client-web[^"\']+\.js)'
            r'["\']'
        )
        script_urls = script_pattern.findall(html)
    except Exception as exc:  # pragma: no cover - network-dependent branch
        logger.warning("Failed to scan JS bundles: %s", exc)
        return

    for script_url in script_urls:
        try:
            bundle = _url_fetch(script_url)
            op_pattern = re.compile(
                r'queryId:\s*"([A-Za-z0-9_-]+)"[^}]{0,200}'
                r'operationName:\s*"([^"]+)"'
            )
            for match in op_pattern.finditer(bundle):
                query_id, operation_name = match.group(1), match.group(2)
                _cached_query_ids.setdefault(operation_name, query_id)
        except Exception:
            continue

    logger.info("Scanned %d JS bundles, cached %d query IDs", len(script_urls), len(_cached_query_ids))


def _fetch_from_github(operation_name):
    # type: (str) -> Optional[str]
    """Fetch queryId from community-maintained twitter-openapi file."""
    try:
        payload = _url_fetch(TWITTER_OPENAPI_URL)
        parsed = json.loads(payload)
        operation = parsed.get(operation_name, {})
        query_id = operation.get("queryId")
        if isinstance(query_id, str) and query_id:
            return query_id
    except Exception as exc:  # pragma: no cover - network-dependent branch
        logger.debug("GitHub queryId lookup failed: %s", exc)
    return None


def _invalidate_query_id(operation_name):
    # type: (str) -> None
    """Remove a cached queryId for an operation."""
    _cached_query_ids.pop(operation_name, None)


def _resolve_query_id(operation_name, prefer_fallback=True):
    # type: (str, bool) -> str
    """Resolve queryId using cache, remote sources, and fallback constants."""
    cached = _cached_query_ids.get(operation_name)
    if cached:
        return cached

    fallback = FALLBACK_QUERY_IDS.get(operation_name)
    if prefer_fallback and fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    github_query_id = _fetch_from_github(operation_name)
    if github_query_id:
        _cached_query_ids[operation_name] = github_query_id
        return github_query_id

    _scan_bundles()
    cached = _cached_query_ids.get(operation_name)
    if cached:
        return cached

    if fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    raise RuntimeError('Cannot resolve queryId for "%s"' % operation_name)


class TwitterClient:
    """Twitter GraphQL API client using cookie authentication."""

    def __init__(self, auth_token, ct0):
        # type: (str, str) -> None
        self._auth_token = auth_token
        self._ct0 = ct0

    def fetch_home_timeline(self, count=20):
        # type: (int) -> List[Tweet]
        """Fetch home timeline tweets."""
        return self._fetch_timeline(
            "HomeTimeline",
            count,
            lambda data: _deep_get(data, "data", "home", "home_timeline_urt", "instructions"),
        )

    def fetch_following_feed(self, count=20):
        # type: (int) -> List[Tweet]
        """Fetch chronological following feed."""
        return self._fetch_timeline(
            "HomeLatestTimeline",
            count,
            lambda data: _deep_get(data, "data", "home", "home_timeline_urt", "instructions"),
        )

    def fetch_bookmarks(self, count=50):
        # type: (int) -> List[Tweet]
        """Fetch bookmarked tweets."""
        def get_instructions(data):
            # type: (Any) -> Any
            instructions = _deep_get(data, "data", "bookmark_timeline", "timeline", "instructions")
            if instructions is None:
                instructions = _deep_get(data, "data", "bookmark_timeline_v2", "timeline", "instructions")
            return instructions

        return self._fetch_timeline("Bookmarks", count, get_instructions)

    def fetch_user(self, screen_name):
        # type: (str) -> UserProfile
        """Fetch user profile by screen name."""
        variables = {
            "screen_name": screen_name,
            "withSafetyModeUserFields": True,
        }
        features = {
            "hidden_profile_subscriptions_enabled": True,
            "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "subscriptions_verification_info_is_identity_verified_enabled": True,
            "subscriptions_verification_info_verified_since_enabled": True,
            "highlights_tweets_tab_ui_enabled": True,
            "responsive_web_twitter_article_notes_tab_enabled": True,
            "subscriptions_feature_can_gift_premium": True,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
        }
        data = self._graphql_get("UserByScreenName", variables, features)
        result = _deep_get(data, "data", "user", "result")
        if not result:
            raise RuntimeError("User @%s not found" % screen_name)

        legacy = result.get("legacy", {})
        core = result.get("core", {})
        return UserProfile(
            id=result.get("rest_id", ""),
            name=core.get("name") or legacy.get("name", ""),
            screen_name=core.get("screen_name") or legacy.get("screen_name", screen_name),
            bio=legacy.get("description", ""),
            location=legacy.get("location", ""),
            url=(
                legacy.get("entities", {}).get("url", {}).get("urls", [{}])[0].get("expanded_url", "")
                if legacy.get("entities", {}).get("url")
                else ""
            ),
            followers_count=_to_int(legacy.get("followers_count"), 0),
            following_count=_to_int(legacy.get("friends_count"), 0),
            tweets_count=_to_int(legacy.get("statuses_count"), 0),
            likes_count=_to_int(legacy.get("favourites_count"), 0),
            verified=bool(result.get("is_blue_verified") or legacy.get("verified", False)),
            profile_image_url=legacy.get("profile_image_url_https", ""),
            created_at=legacy.get("created_at", ""),
        )

    def fetch_user_tweets(self, user_id, count=20):
        # type: (str, int) -> List[Tweet]
        """Fetch tweets posted by a user."""
        return self._fetch_timeline(
            "UserTweets",
            count,
            lambda data: _deep_get(data, "data", "user", "result", "timeline_v2", "timeline", "instructions"),
            extra_variables={
                "userId": user_id,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            },
        )

    def fetch_current_user_id(self):
        # type: () -> str
        """Fetch the authenticated user's ID via the Viewer endpoint."""
        features = {
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
        }
        data = self._graphql_get("Viewer", {}, features)
        user_id = _deep_get(data, "data", "viewer", "user_results", "result", "rest_id")
        if not user_id:
            raise RuntimeError("Could not resolve current user ID")
        return user_id

    def fetch_likes(self, user_id, count=20):
        # type: (str, int) -> List[Tweet]
        """Fetch liked tweets for a user (only works for the authenticated user)."""
        return self._fetch_timeline(
            "Likes",
            count,
            lambda data: _deep_get(data, "data", "user", "result", "timeline", "timeline", "instructions"),
            extra_variables={"userId": user_id},
        )

    def _fetch_timeline(self, operation_name, count, get_instructions, extra_variables=None):
        # type: (str, int, Callable[[Any], Any], Optional[Dict[str, Any]]) -> List[Tweet]
        """Generic timeline fetcher with pagination and deduplication."""
        if count <= 0:
            return []

        tweets = []  # type: List[Tweet]
        seen_ids = set()  # type: Set[str]
        cursor = None  # type: Optional[str]
        attempts = 0
        max_attempts = int(math.ceil(count / 20.0)) + 2

        while len(tweets) < count and attempts < max_attempts:
            attempts += 1
            variables = {
                "count": min(count - len(tweets) + 5, 40),
                "includePromotedContent": False,
                "latestControlAvailable": True,
                "requestContext": "launch",
            }  # type: Dict[str, Any]
            if extra_variables:
                variables.update(extra_variables)
            if cursor:
                variables["cursor"] = cursor

            data = self._graphql_get(operation_name, variables, FEATURES)
            new_tweets, next_cursor = self._parse_timeline_response(data, get_instructions)

            for tweet in new_tweets:
                if tweet.id and tweet.id not in seen_ids:
                    seen_ids.add(tweet.id)
                    tweets.append(tweet)

            if not next_cursor or not new_tweets:
                break
            cursor = next_cursor

        return tweets[:count]

    def _graphql_get(self, operation_name, variables, features):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Issue GraphQL GET request with automatic stale-fallback retry."""
        query_id = _resolve_query_id(operation_name, prefer_fallback=True)
        using_fallback = query_id == FALLBACK_QUERY_IDS.get(operation_name)
        url = _build_graphql_url(query_id, operation_name, variables, features)

        try:
            return self._api_get(url)
        except TwitterAPIError as exc:
            # Fallback query IDs can go stale. Retry with live lookup if 404.
            if exc.status_code == 404 and using_fallback:
                logger.info("Retrying %s with live queryId after 404", operation_name)
                _invalidate_query_id(operation_name)
                refreshed_query_id = _resolve_query_id(operation_name, prefer_fallback=False)
                retry_url = _build_graphql_url(refreshed_query_id, operation_name, variables, features)
                return self._api_get(retry_url)
            raise RuntimeError(str(exc))

    def _build_headers(self):
        # type: () -> Dict[str, str]
        """Build shared headers for authenticated API calls."""
        return {
            "Authorization": "Bearer %s" % BEARER_TOKEN,
            "Cookie": "auth_token=%s; ct0=%s" % (self._auth_token, self._ct0),
            "X-Csrf-Token": self._ct0,
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Client-Language": "en",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": "https://x.com/home",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _api_get(self, url):
        # type: (str) -> Dict[str, Any]
        """Make authenticated GET request to Twitter API."""
        headers = self._build_headers()
        request = urllib.request.Request(url)
        for key, value in headers.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, context=_create_ssl_context(), timeout=30) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = "Twitter API error %d: %s" % (exc.code, body[:500])
            raise TwitterAPIError(exc.code, message)
        except urllib.error.URLError as exc:
            raise TwitterAPIError(0, "Twitter API network error: %s" % exc.reason)

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            raise TwitterAPIError(0, "Twitter API returned invalid JSON")

        if isinstance(parsed, dict) and parsed.get("errors"):
            message = parsed["errors"][0].get("message", "Unknown error")
            raise TwitterAPIError(0, "Twitter API returned errors: %s" % message)
        return parsed

    def _parse_timeline_response(self, data, get_instructions):
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
                    tweet = self._parse_tweet_result(result)
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
                        tweet = self._parse_tweet_result(nested_result)
                        if tweet:
                            tweets.append(tweet)

        return tweets, next_cursor

    def _parse_tweet_result(self, result, depth=0):
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

        media = []  # type: List[TweetMedia]
        for media_item in _deep_get(actual_legacy, "extended_entities", "media") or []:
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
                mp4_variants = [item for item in variants if item.get("content_type") == "video/mp4"]
                mp4_variants.sort(key=lambda item: item.get("bitrate", 0), reverse=True)
                media.append(
                    TweetMedia(
                        type=media_type,
                        url=mp4_variants[0]["url"] if mp4_variants else media_item.get("media_url_https", ""),
                        width=_deep_get(media_item, "original_info", "width"),
                        height=_deep_get(media_item, "original_info", "height"),
                    )
                )

        urls = [item.get("expanded_url", "") for item in _deep_get(actual_legacy, "entities", "urls") or []]
        quoted = _deep_get(actual_data, "quoted_status_result", "result")
        quoted_tweet = self._parse_tweet_result(quoted, depth=depth + 1) if isinstance(quoted, dict) else None

        actual_user_core = actual_user.get("core", {})
        user_name = actual_user_core.get("name") or actual_user_legacy.get("name") or actual_user.get("name", "Unknown")
        user_screen_name = (
            actual_user_core.get("screen_name")
            or actual_user_legacy.get("screen_name")
            or actual_user.get("screen_name", "unknown")
        )
        user_profile_image = actual_user.get("avatar", {}).get("image_url") or actual_user_legacy.get("profile_image_url_https", "")
        user_verified = bool(actual_user.get("is_blue_verified") or actual_user_legacy.get("verified", False))
        retweeted_by = None  # type: Optional[str]
        if is_retweet:
            retweeted_by = user_core.get("screen_name") or user_legacy.get("screen_name", "unknown")

        return Tweet(
            id=actual_data.get("rest_id", ""),
            text=actual_legacy.get("full_text", ""),
            author=Author(
                id=actual_user.get("rest_id", ""),
                name=user_name,
                screen_name=user_screen_name,
                profile_image_url=user_profile_image,
                verified=user_verified,
            ),
            metrics=Metrics(
                likes=_to_int(actual_legacy.get("favorite_count"), 0),
                retweets=_to_int(actual_legacy.get("retweet_count"), 0),
                replies=_to_int(actual_legacy.get("reply_count"), 0),
                quotes=_to_int(actual_legacy.get("quote_count"), 0),
                views=_to_int(_deep_get(actual_data, "views", "count"), 0),
                bookmarks=_to_int(actual_legacy.get("bookmark_count"), 0),
            ),
            created_at=actual_legacy.get("created_at", ""),
            media=media,
            urls=urls,
            is_retweet=is_retweet,
            retweeted_by=retweeted_by,
            quoted_tweet=quoted_tweet,
            lang=actual_legacy.get("lang", ""),
        )


def _deep_get(data, *keys):
    # type: (Any, *str) -> Any
    """Safely get nested dict values."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_cursor(content):
    # type: (Dict[str, Any]) -> Optional[str]
    """Extract pagination cursor from timeline content."""
    if content.get("cursorType") == "Bottom":
        return content.get("value")
    if content.get("entryType") == "TimelineTimelineCursor":
        return content.get("value")
    return None


def _to_int(value, default):
    # type: (Any, int) -> int
    """Best-effort integer conversion."""
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default
