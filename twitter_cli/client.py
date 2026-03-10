"""Twitter GraphQL API client."""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
import urllib.parse

import bs4
from curl_cffi import requests as _cffi_requests
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers as _gen_ct_headers, get_ondemand_file_url

from .constants import (
    BEARER_TOKEN,
    SEC_CH_UA_ARCH,
    SEC_CH_UA_BITNESS,
    SEC_CH_UA_MOBILE,
    SEC_CH_UA_MODEL,
    SEC_CH_UA_PLATFORM_VERSION,
    get_accept_language,
    get_sec_ch_ua,
    get_sec_ch_ua_full_version,
    get_sec_ch_ua_full_version_list,
    get_sec_ch_ua_platform,
    get_twitter_client_language,
    get_user_agent,
    sync_chrome_version,
)
from .models import Author, Metrics, Tweet, TweetMedia, UserProfile

logger = logging.getLogger(__name__)

# Shared curl_cffi session — impersonates Chrome 133 TLS/JA3/HTTP2 fingerprint
_cffi_session = None  # type: Optional[Any]  # lazy init


FALLBACK_QUERY_IDS = {
    # Read operations
    "HomeTimeline": "c-CzHF1LboFilMpsx4ZCrQ",
    "HomeLatestTimeline": "BKB7oi212Fi7kQtCBGE4zA",
    "Bookmarks": "VFdMm9iVZxlU6hD86gfW_A",
    "UserByScreenName": "1VOOyvKkiI3FMmkeDNxM9A",
    "UserTweets": "E3opETHurmVJflFsUBVuUQ",
    "SearchTimeline": "nWemVnGJ6A5eQAR5-oQeAg",
    "Likes": "lIDpu_NWL7_VhimGGt0o6A",
    "TweetDetail": "xd_EMdYvB9hfZsZ6Idri0w",
    "ListLatestTweetsTimeline": "RlZzktZY_9wJynoepm8ZsA",
    "Followers": "IOh4aS6UdGWGJUYTqliQ7Q",
    "Following": "zx6e-TLzRkeDO_a7p4b3JQ",
    # Write operations
    "CreateTweet": "IID9x6WsdMnTlXnzXGq8ng",
    "DeleteTweet": "VaenaVgh5q5ih7kvyVjgtg",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "CreateRetweet": "ojPdsZsimiJrUGLR1sjUtA",
    "DeleteRetweet": "iQtK4dl5hBmXewYZuEOKVw",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xzyS1GN3a6cj-mQ",
}

TWITTER_OPENAPI_URL = (
    "https://raw.githubusercontent.com/fa0311/twitter-openapi/"
    "main/src/config/placeholder.json"
)

# Essential features only — keep this list SMALL to avoid 414/431 URI Too Long.
# Twitter's API defaults missing features to False, so we only need True-valued ones
# that affect tweet data we actually consume. Each additional key adds ~60 chars to URL.
_DEFAULT_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

# Features dict that gets updated dynamically from x.com JS bundles
FEATURES = dict(_DEFAULT_FEATURES)

# Module-level caches (not thread-safe — CLI is single-threaded)
_cached_query_ids = {}  # type: Dict[str, str]
_bundles_scanned = False


class TwitterAPIError(RuntimeError):
    """Represents HTTP/network errors from Twitter APIs."""

    def __init__(self, status_code, message):
        # type: (int, str) -> None
        super().__init__(message)
        self.status_code = status_code

def _best_chrome_target():
    # type: () -> str
    """Detect the best available Chrome impersonation target at runtime.

    curl_cffi versions differ in which Chrome targets they ship.
    e.g. 0.14.0 has chrome133a but not chrome133.
    """
    try:
        from curl_cffi.requests import BrowserType
        available = {e.value for e in BrowserType}
    except Exception:
        available = set()

    # Preference order: exact chrome versions, then suffixed variants
    for target in ("chrome133", "chrome133a", "chrome136", "chrome131", "chrome130"):
        if target in available:
            return target
    # Fallback: pick highest chrome* with a pure numeric suffix
    chrome_targets = sorted(
        [v for v in available if v.startswith("chrome") and v.replace("chrome", "").isdigit()],
        key=lambda x: int(x.replace("chrome", "")),
        reverse=True,
    )
    return chrome_targets[0] if chrome_targets else "chrome131"


def _get_cffi_session():
    # type: () -> Any
    """Return shared curl_cffi session with Chrome impersonation and optional proxy."""
    global _cffi_session
    if _cffi_session is None:
        import os
        proxy = os.environ.get("TWITTER_PROXY", "")
        target = _best_chrome_target()
        sync_chrome_version(target)  # align UA/sec-ch-ua with impersonate target
        _cffi_session = _cffi_requests.Session(
            impersonate=target,
            proxies={"https": proxy, "http": proxy} if proxy else None,
        )
        logger.info("curl_cffi impersonating %s", target)
        if proxy:
            logger.info("Using proxy: %s", proxy[:20] + "...")
    return _cffi_session


def _url_fetch(url, headers=None):
    # type: (str, Optional[Dict[str, str]]) -> str
    """URL fetch using curl_cffi for proper TLS fingerprint."""
    session = _get_cffi_session()
    resp = session.get(url, headers=headers or {}, timeout=30)
    resp.raise_for_status()
    return resp.text


def _build_graphql_url(query_id, operation_name, variables, features, field_toggles=None):
    # type: (str, str, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> str
    """Build GraphQL GET URL with encoded variables/features/fieldToggles.

    Only includes True-valued feature flags in the URL to avoid 414 URI Too Long.
    Twitter's API defaults missing features to False.
    """
    # Compact features: omit False values to keep URL under server limits
    compact_features = {k: v for k, v in features.items() if v is not False}
    url = "https://x.com/i/api/graphql/%s/%s?variables=%s&features=%s" % (
        query_id,
        operation_name,
        urllib.parse.quote(json.dumps(variables, separators=(",", ":"))),
        urllib.parse.quote(json.dumps(compact_features, separators=(",", ":"))),
    )
    if field_toggles:
        url += "&fieldToggles=%s" % urllib.parse.quote(
            json.dumps(field_toggles, separators=(",", ":"))
        )
    return url


def _scan_bundles():
    # type: () -> None
    """Scan Twitter JS bundles and cache queryId mappings."""
    global _bundles_scanned
    if _bundles_scanned:
        return
    _bundles_scanned = True

    try:
        html = _url_fetch("https://x.com", {"user-agent": get_user_agent()})
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


def _update_features_from_html(html):
    # type: (str) -> None
    """Extract live feature flags from x.com HTML and update the global FEATURES dict.

    Twitter embeds feature switch config in inline scripts on the homepage.
    We parse these to keep FEATURES in sync with the current frontend.
    Only UPDATES existing keys — never adds new ones to avoid URL bloat.
    """
    try:
        feature_pattern = re.compile(
            r'"([a-z][a-z0-9_]+)":\s*\{\s*"value"\s*:\s*(true|false)',
            re.IGNORECASE,
        )
        found = 0
        for match in feature_pattern.finditer(html):
            key = match.group(1)
            value = match.group(2).lower() == "true"
            # Only update keys already in FEATURES — never add new ones
            # Adding new keys inflates URL length, causing 414/431 errors
            if key in FEATURES and FEATURES[key] != value:
                logger.debug("Feature flag updated: %s = %s -> %s", key, FEATURES[key], value)
                FEATURES[key] = value
                found += 1
        if found:
            logger.info("Updated %d feature flags from x.com", found)
    except Exception as exc:
        logger.debug("Feature extraction from HTML failed: %s", exc)


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


# Hard ceiling to prevent accidental massive fetches
_ABSOLUTE_MAX_COUNT = 500


class TwitterClient:
    """Twitter GraphQL API client using cookie authentication."""

    def __init__(self, auth_token, ct0, rate_limit_config=None, cookie_string=None):
        # type: (str, str, Optional[Dict[str, Any]], Optional[str]) -> None
        self._auth_token = auth_token
        self._ct0 = ct0
        self._cookie_string = cookie_string  # Full browser cookie string
        rl = rate_limit_config or {}
        self._request_delay = float(rl.get("requestDelay", 2.5))
        self._max_retries = int(rl.get("maxRetries", 3))
        self._retry_base_delay = float(rl.get("retryBaseDelay", 5.0))
        self._max_count = min(int(rl.get("maxCount", 200)), _ABSOLUTE_MAX_COUNT)
        self._client_transaction = None  # type: Optional[Any]
        self._ct_init_attempted = False
        # Eagerly initialize ClientTransaction on construction
        self._ensure_client_transaction()

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

    def resolve_user_id(self, identifier):
        # type: (str) -> str
        """Resolve a user identifier (screen_name or numeric user_id) to numeric user_id.

        If identifier is all digits, returns it as-is. Otherwise fetches the user profile.
        """
        if identifier.isdigit():
            return identifier
        profile = self.fetch_user(identifier)
        return profile.id

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
        return UserProfile(
            id=result.get("rest_id", ""),
            name=legacy.get("name", ""),
            screen_name=legacy.get("screen_name", screen_name),
            bio=legacy.get("description", ""),
            location=legacy.get("location", ""),
            url=_deep_get(legacy, "entities", "url", "urls", 0, "expanded_url") or "",
            followers_count=_parse_int(legacy.get("followers_count"), 0),
            following_count=_parse_int(legacy.get("friends_count"), 0),
            tweets_count=_parse_int(legacy.get("statuses_count"), 0),
            likes_count=_parse_int(legacy.get("favourites_count"), 0),
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

    def fetch_user_likes(self, user_id, count=20):
        # type: (str, int) -> List[Tweet]
        """Fetch tweets liked by a user."""
        return self._fetch_timeline(
            "Likes",
            count,
            lambda data: _deep_get(data, "data", "user", "result", "timeline_v2", "timeline", "instructions"),
            extra_variables={
                "userId": user_id,
                "includePromotedContent": False,
                "withClientEventToken": False,
                "withBirdwatchNotes": False,
                "withVoice": True,
            },
            override_base_variables=True,
        )

    def fetch_search(self, query, count=20, product="Top"):
        # type: (str, int, str) -> List[Tweet]
        """Search tweets by query.

        Args:
            query: Search query string.
            count: Max number of tweets to return.
            product: Search tab — "Top", "Latest", "People", "Photos", "Videos".
        """
        return self._fetch_timeline(
            "SearchTimeline",
            count,
            lambda data: _deep_get(
                data, "data", "search_by_raw_query", "search_timeline", "timeline", "instructions",
            ),
            extra_variables={
                "rawQuery": query,
                "querySource": "typed_query",
                "product": product,
            },
            override_base_variables=True,
        )

    def fetch_tweet_detail(self, tweet_id, count=20):
        # type: (str, int) -> List[Tweet]
        """Fetch a tweet and its conversation thread (replies)."""
        return self._fetch_timeline(
            "TweetDetail",
            count,
            lambda data: _deep_get(data, "data", "tweetResult", "result", "timeline", "instructions")
            or _deep_get(data, "data", "threaded_conversation_with_injections_v2", "instructions"),
            extra_variables={
                "focalTweetId": tweet_id,
                "referrer": "tweet",
                "with_rux_injections": False,
                "includePromotedContent": True,
                "rankingMode": "Relevance",
                "withCommunity": True,
                "withQuickPromoteEligibilityTweetFields": True,
                "withBirdwatchNotes": True,
                "withVoice": True,
            },
            override_base_variables=True,
            field_toggles={
                "withArticleRichContentState": True,
                "withArticlePlainText": False,
                "withGrokAnalyze": False,
                "withDisallowedReplyControls": False,
            },
        )

    def fetch_list_timeline(self, list_id, count=20):
        # type: (str, int) -> List[Tweet]
        """Fetch tweets from a Twitter List."""
        return self._fetch_timeline(
            "ListLatestTweetsTimeline",
            count,
            lambda data: _deep_get(data, "data", "list", "tweets_timeline", "timeline", "instructions"),
            extra_variables={"listId": list_id},
            override_base_variables=True,
        )

    def fetch_followers(self, user_id, count=20):
        # type: (str, int) -> List[UserProfile]
        """Fetch followers of a user."""
        return self._fetch_user_list(
            "Followers", user_id, count,
            lambda data: _deep_get(data, "data", "user", "result", "timeline", "timeline", "instructions"),
        )

    def fetch_following(self, user_id, count=20):
        # type: (str, int) -> List[UserProfile]
        """Fetch users that a user is following."""
        return self._fetch_user_list(
            "Following", user_id, count,
            lambda data: _deep_get(data, "data", "user", "result", "timeline", "timeline", "instructions"),
        )

    # ── Write operations ────────────────────────────────────────────────

    def _write_delay(self):
        # type: () -> None
        """Sleep a random interval after write operations to avoid rate limits."""
        delay = random.uniform(1.5, 4.0)
        logger.debug("Write operation delay: %.1fs", delay)
        time.sleep(delay)

    def create_tweet(self, text, reply_to_id=None):
        # type: (str, Optional[str]) -> str
        """Post a new tweet.  Returns the new tweet ID."""
        variables = {
            "tweet_text": text,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "dark_request": False,
        }  # type: Dict[str, Any]
        if reply_to_id:
            variables["reply"] = {
                "in_reply_to_tweet_id": reply_to_id,
                "exclude_reply_user_ids": [],
            }
        data = self._graphql_post("CreateTweet", variables, FEATURES)
        self._write_delay()
        result = _deep_get(data, "data", "create_tweet", "tweet_results", "result")
        if result:
            return result.get("rest_id", "")
        raise RuntimeError("Failed to create tweet")

    def delete_tweet(self, tweet_id):
        # type: (str) -> bool
        """Delete a tweet.  Returns True on success."""
        variables = {"tweet_id": tweet_id, "dark_request": False}
        self._graphql_post("DeleteTweet", variables)
        self._write_delay()
        return True

    def like_tweet(self, tweet_id):
        # type: (str) -> bool
        """Like a tweet.  Returns True on success."""
        self._graphql_post("FavoriteTweet", {"tweet_id": tweet_id})
        self._write_delay()
        return True

    def unlike_tweet(self, tweet_id):
        # type: (str) -> bool
        """Unlike a tweet.  Returns True on success."""
        self._graphql_post("UnfavoriteTweet", {"tweet_id": tweet_id, "dark_request": False})
        self._write_delay()
        return True

    def retweet(self, tweet_id):
        # type: (str) -> bool
        """Retweet a tweet.  Returns True on success."""
        self._graphql_post("CreateRetweet", {"tweet_id": tweet_id, "dark_request": False})
        self._write_delay()
        return True

    def unretweet(self, tweet_id):
        # type: (str) -> bool
        """Undo a retweet.  Returns True on success."""
        self._graphql_post("DeleteRetweet", {"source_tweet_id": tweet_id, "dark_request": False})
        self._write_delay()
        return True

    def bookmark_tweet(self, tweet_id):
        # type: (str) -> bool
        """Bookmark a tweet.  Returns True on success."""
        self._graphql_post("CreateBookmark", {"tweet_id": tweet_id})
        self._write_delay()
        return True

    def unbookmark_tweet(self, tweet_id):
        # type: (str) -> bool
        """Remove a tweet from bookmarks.  Returns True on success."""
        self._graphql_post("DeleteBookmark", {"tweet_id": tweet_id})
        self._write_delay()
        return True

    def fetch_me(self):
        # type: () -> UserProfile
        """Fetch the currently authenticated user's profile."""
        url = "https://x.com/i/api/1.1/account/multi/list.json"
        data = self._api_get(url)
        if isinstance(data, list) and data:
            user_data = data[0].get("user", {})
            if user_data:
                return UserProfile(
                    id=str(user_data.get("id_str", "")),
                    name=user_data.get("name", ""),
                    screen_name=user_data.get("screen_name", ""),
                    bio=user_data.get("description", ""),
                    location=user_data.get("location", ""),
                    url=_deep_get(user_data, "entities", "url", "urls", 0, "expanded_url") or "",
                    followers_count=_parse_int(user_data.get("followers_count"), 0),
                    following_count=_parse_int(user_data.get("friends_count"), 0),
                    tweets_count=_parse_int(user_data.get("statuses_count"), 0),
                    likes_count=_parse_int(user_data.get("favourites_count"), 0),
                    verified=bool(user_data.get("verified", False)),
                    profile_image_url=user_data.get("profile_image_url_https", ""),
                    created_at=user_data.get("created_at", ""),
                )
        raise RuntimeError("Failed to fetch current user info")

    def quote_tweet(self, tweet_id, text):
        # type: (str, str) -> str
        """Quote-tweet a tweet.  Returns the new tweet ID."""
        variables = {
            "tweet_text": text,
            "attachment_url": "https://x.com/i/status/%s" % tweet_id,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "dark_request": False,
        }
        data = self._graphql_post("CreateTweet", variables, FEATURES)
        self._write_delay()
        result = _deep_get(data, "data", "create_tweet", "tweet_results", "result")
        if result:
            return result.get("rest_id", "")
        raise RuntimeError("Failed to create quote tweet")

    def follow_user(self, user_id):
        # type: (str) -> bool
        """Follow a user by user ID.  Returns True on success."""
        url = "https://x.com/i/api/1.1/friendships/create.json"
        body = {"user_id": user_id, "include_profile_interstitial_type": "1"}
        headers = self._build_headers(url=url, method="POST")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        session = _get_cffi_session()
        response = session.post(url, headers=headers, data=body, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError("Failed to follow user: HTTP %d" % response.status_code)
        self._write_delay()
        return True

    def unfollow_user(self, user_id):
        # type: (str) -> bool
        """Unfollow a user by user ID.  Returns True on success."""
        url = "https://x.com/i/api/1.1/friendships/destroy.json"
        body = {"user_id": user_id, "include_profile_interstitial_type": "1"}
        headers = self._build_headers(url=url, method="POST")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        session = _get_cffi_session()
        response = session.post(url, headers=headers, data=body, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError("Failed to unfollow user: HTTP %d" % response.status_code)
        self._write_delay()
        return True

    def _fetch_timeline(self, operation_name, count, get_instructions, extra_variables=None, override_base_variables=False, field_toggles=None):
        # type: (str, int, Callable[[Any], Any], Optional[Dict[str, Any]], bool, Optional[Dict[str, Any]]) -> List[Tweet]
        """Generic timeline fetcher with pagination and deduplication.

        Args:
            override_base_variables: If True, use only extra_variables + count/cursor
                instead of the default timeline base variables. Needed for
                endpoints like SearchTimeline that reject unknown variables.
        """
        if count <= 0:
            return []

        # Enforce max count cap
        count = min(count, self._max_count)

        tweets = []  # type: List[Tweet]
        seen_ids = set()  # type: Set[str]
        cursor = None  # type: Optional[str]
        attempts = 0
        max_attempts = int(math.ceil(count / 20.0)) + 2

        while len(tweets) < count and attempts < max_attempts:
            attempts += 1
            if override_base_variables:
                variables = {"count": min(count - len(tweets) + 5, 40)}  # type: Dict[str, Any]
            else:
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

            data = self._graphql_get(operation_name, variables, FEATURES, field_toggles=field_toggles)
            new_tweets, next_cursor = self._parse_timeline_response(data, get_instructions)

            for tweet in new_tweets:
                if tweet.id and tweet.id not in seen_ids:
                    seen_ids.add(tweet.id)
                    tweets.append(tweet)

            if not next_cursor:
                break
            if next_cursor == cursor:
                logger.debug("Timeline pagination stopped because cursor did not advance: %s", next_cursor)
                break
            cursor = next_cursor

            if not new_tweets:
                logger.debug("Timeline page returned no tweets but exposed next cursor; continuing pagination")

            # Rate-limit: sleep between paginated requests with jitter
            if len(tweets) < count and self._request_delay > 0:
                jitter = self._request_delay * random.uniform(0.7, 1.5)
                logger.debug("Sleeping %.1fs between requests", jitter)
                time.sleep(jitter)

        return tweets[:count]

    def _graphql_get(self, operation_name, variables, features, field_toggles=None):
        # type: (str, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Issue GraphQL GET request with automatic stale-fallback retry."""
        query_id = _resolve_query_id(operation_name, prefer_fallback=True)
        using_fallback = query_id == FALLBACK_QUERY_IDS.get(operation_name)
        url = _build_graphql_url(query_id, operation_name, variables, features, field_toggles)

        try:
            return self._api_get(url)
        except TwitterAPIError as exc:
            # Fallback query IDs can go stale. Retry with live lookup if 404.
            if exc.status_code == 404 and using_fallback:
                logger.info("Retrying %s with live queryId after 404", operation_name)
                _invalidate_query_id(operation_name)
                refreshed_query_id = _resolve_query_id(operation_name, prefer_fallback=False)
                retry_url = _build_graphql_url(refreshed_query_id, operation_name, variables, features, field_toggles)
                return self._api_get(retry_url)
            raise RuntimeError(str(exc))

    @staticmethod
    def _ct_cache_path():
        # type: () -> str
        """Return path for transaction cache file."""
        home = os.path.expanduser("~")
        return os.path.join(home, ".twitter-cli", "transaction_cache.json")

    def _load_ct_cache(self):
        # type: () -> bool
        """Try to load ClientTransaction from cache.  Returns True on success."""
        try:
            cache_path = self._ct_cache_path()
            if not os.path.exists(cache_path):
                return False
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            # Check TTL (1 hour)
            if time.time() - cache.get("created_at", 0) > 3600:
                return False
            home_html = cache.get("home_html", "")
            ondemand_text = cache.get("ondemand_text", "")
            if not home_html or not ondemand_text:
                return False
            home_page_response = bs4.BeautifulSoup(home_html, "html.parser")
            self._client_transaction = ClientTransaction(
                home_page_response=home_page_response,
                ondemand_file_response=ondemand_text,
            )
            _update_features_from_html(home_html)
            logger.info("ClientTransaction loaded from cache")
            return True
        except Exception as exc:
            logger.debug("Failed to load CT cache: %s", exc)
            return False

    def _save_ct_cache(self, home_html, ondemand_text):
        # type: (str, str) -> None
        """Save transaction data to cache file."""
        try:
            cache_path = self._ct_cache_path()
            cache_dir = os.path.dirname(cache_path)
            os.makedirs(cache_dir, exist_ok=True)
            cache = {
                "home_html": home_html,
                "ondemand_text": ondemand_text,
                "created_at": time.time(),
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            logger.debug("Saved CT cache to %s", cache_path)
        except Exception as exc:
            logger.debug("Failed to save CT cache: %s", exc)

    def _ensure_client_transaction(self):
        # type: () -> None
        """Initialize ClientTransaction for x-client-transaction-id header.

        Tries cache first (1h TTL), then fetches fresh data from x.com.
        Also attempts to extract live feature flags from JS bundles.
        """
        if self._ct_init_attempted:
            return
        self._ct_init_attempted = True

        # Try loading from cache first
        if self._load_ct_cache():
            return

        try:
            # Use curl_cffi for ClientTransaction init to maintain consistent
            # Chrome TLS fingerprint. Using Python requests here would leak
            # a different TLS fingerprint on the same IP — a detection vector.
            cffi_session = _get_cffi_session()
            ct_headers = _gen_ct_headers()
            home_page = cffi_session.get(
                "https://x.com", headers=ct_headers, timeout=10,
            )
            home_page_response = bs4.BeautifulSoup(home_page.content, "html.parser")
            ondemand_url = get_ondemand_file_url(response=home_page_response)
            ondemand_file = cffi_session.get(
                ondemand_url, headers=ct_headers, timeout=10,
            )
            self._client_transaction = ClientTransaction(
                home_page_response=home_page_response,
                ondemand_file_response=ondemand_file.text,
            )
            logger.info("ClientTransaction initialized for x-client-transaction-id")

            # Try to extract live FEATURES from the homepage JS bundles
            _update_features_from_html(home_page.text)

            # Save to cache for future use
            self._save_ct_cache(home_page.text, ondemand_file.text)
        except Exception as exc:
            logger.warning("Failed to init ClientTransaction: %s", exc)

    def _build_headers(self, url="", method="GET"):
        # type: (str, str) -> Dict[str, str]
        """Build shared headers for authenticated API calls."""
        headers = {
            "Authorization": "Bearer %s" % BEARER_TOKEN,
            "Cookie": self._cookie_string or "auth_token=%s; ct0=%s" % (self._auth_token, self._ct0),
            "X-Csrf-Token": self._ct0,
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Client-Language": get_twitter_client_language(),
            "User-Agent": get_user_agent(),
            "Origin": "https://x.com",
            "Referer": "https://x.com/",
            "Accept": "*/*",
            "Accept-Language": get_accept_language(),
            "sec-ch-ua": get_sec_ch_ua(),
            "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
            "sec-ch-ua-platform": get_sec_ch_ua_platform(),
            "sec-ch-ua-arch": SEC_CH_UA_ARCH,
            "sec-ch-ua-bitness": SEC_CH_UA_BITNESS,
            "sec-ch-ua-full-version": get_sec_ch_ua_full_version(),
            "sec-ch-ua-full-version-list": get_sec_ch_ua_full_version_list(),
            "sec-ch-ua-model": SEC_CH_UA_MODEL,
            "sec-ch-ua-platform-version": SEC_CH_UA_PLATFORM_VERSION,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if method == "POST":
            headers["Content-Type"] = "application/json"
            headers["Referer"] = "https://x.com/compose/post"
            headers["Priority"] = "u=1, i"
        # Generate x-client-transaction-id if available
        if self._client_transaction and url:
            try:
                path = urllib.parse.urlparse(url).path
                tid = self._client_transaction.generate_transaction_id(
                    method=method, path=path,
                )
                headers["X-Client-Transaction-Id"] = tid
            except Exception as exc:
                logger.debug("Failed to generate transaction id: %s", exc)
        return headers

    def _api_get(self, url):
        # type: (str) -> Dict[str, Any]
        """Make authenticated GET request to Twitter API."""
        return self._api_request(url, method="GET")

    def _graphql_post(self, operation_name, variables, features=None):
        # type: (str, Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Issue GraphQL POST request with automatic stale-fallback retry."""
        query_id = _resolve_query_id(operation_name, prefer_fallback=True)
        using_fallback = query_id == FALLBACK_QUERY_IDS.get(operation_name)

        def _do_post(qid):
            # type: (str) -> Dict[str, Any]
            url = "https://x.com/i/api/graphql/%s/%s" % (qid, operation_name)
            body = {"variables": variables, "queryId": qid}  # type: Dict[str, Any]
            if features:
                body["features"] = features
            return self._api_request(url, method="POST", body=body)

        try:
            return _do_post(query_id)
        except TwitterAPIError as exc:
            if exc.status_code == 404 and using_fallback:
                logger.info("Retrying POST %s with live queryId after 404", operation_name)
                _invalidate_query_id(operation_name)
                refreshed = _resolve_query_id(operation_name, prefer_fallback=False)
                return _do_post(refreshed)
            raise RuntimeError(str(exc))

    def _api_request(self, url, method="GET", body=None):
        # type: (str, str, Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Make authenticated request to Twitter API with retry on rate limits.

        Uses curl_cffi for Chrome TLS/JA3/HTTP2 fingerprint impersonation.
        Handles both GET and POST. Retries on HTTP 429 and JSON error code 88.
        """
        headers = self._build_headers(url=url, method=method)
        session = _get_cffi_session()
        json_body = body  # curl_cffi handles JSON serialization

        for attempt in range(self._max_retries + 1):
            try:
                if method == "POST":
                    response = session.post(
                        url, headers=headers, json=json_body, timeout=30,
                    )
                else:
                    response = session.get(url, headers=headers, timeout=30)

                status_code = response.status_code
                if status_code == 429 and attempt < self._max_retries:
                    wait = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 2)
                    logger.warning(
                        "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                        wait, attempt + 1, self._max_retries,
                    )
                    time.sleep(wait)
                    continue
                if status_code >= 400:
                    message = "Twitter API error %d: %s" % (status_code, response.text[:500])
                    raise TwitterAPIError(status_code, message)

                payload = response.text
            except TwitterAPIError:
                raise
            except Exception as exc:
                raise TwitterAPIError(0, "Twitter API network error: %s" % exc)

            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                raise TwitterAPIError(0, "Twitter API returned invalid JSON")

            if isinstance(parsed, dict) and parsed.get("errors"):
                err_msg = parsed["errors"][0].get("message", "Unknown error")
                # Rate limit can also surface as a JSON error (code 88)
                err_code = parsed["errors"][0].get("code", 0)
                if err_code == 88 and attempt < self._max_retries:
                    wait = self._retry_base_delay * (2 ** attempt) + random.uniform(0, 2)
                    logger.warning(
                        "Rate limited (code 88), retrying in %.1fs (attempt %d/%d)",
                        wait, attempt + 1, self._max_retries,
                    )
                    time.sleep(wait)
                    continue
                # Write operation rate limits (retweet/like/bookmark limits)
                # Code 348 = "retweet limit", 327 = "already retweeted"
                # Provide user-friendly message
                if err_code in (348, 349):
                    raise TwitterAPIError(
                        429, "Rate limited: %s (try again later, recommended wait: 15+ minutes)" % err_msg
                    )
                raise TwitterAPIError(0, "Twitter API returned errors: %s" % err_msg)

            # GraphQL write mutations return errors in data.errors (separate from top-level)
            if isinstance(parsed, dict) and "data" in parsed:
                data_obj = parsed["data"]
                if isinstance(data_obj, dict):
                    for key, val in data_obj.items():
                        if isinstance(val, dict) and val.get("errors"):
                            inner_errors = val["errors"]
                            if inner_errors:
                                inner_msg = inner_errors[0].get("message", "Unknown error")
                                raise TwitterAPIError(0, "Twitter API: %s" % inner_msg)

            return parsed

        # Should not be reached, but just in case
        raise TwitterAPIError(429, "Rate limited after %d retries" % self._max_retries)

    def _fetch_user_list(self, operation_name, user_id, count, get_instructions):
        # type: (str, str, int, Callable[[Any], Any]) -> List[UserProfile]
        """Generic user list fetcher (for followers/following) with pagination."""
        if count <= 0:
            return []
        count = min(count, self._max_count)
        users = []  # type: List[UserProfile]
        seen_ids = set()  # type: Set[str]
        cursor = None  # type: Optional[str]
        attempts = 0
        max_attempts = int(math.ceil(count / 20.0)) + 2

        while len(users) < count and attempts < max_attempts:
            attempts += 1
            variables = {
                "userId": user_id,
                "count": min(count - len(users) + 5, 40),
                "includePromotedContent": False,
            }  # type: Dict[str, Any]
            if cursor:
                variables["cursor"] = cursor

            data = self._graphql_get(operation_name, variables, FEATURES)
            instructions = get_instructions(data)
            if not instructions:
                logger.warning("No user list instructions found")
                break

            new_users = []  # type: List[UserProfile]
            next_cursor = None  # type: Optional[str]
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    content = entry.get("content", {})
                    entry_type = content.get("entryType", "")

                    if entry_type == "TimelineTimelineItem":
                        item = content.get("itemContent", {})
                        user_results = _deep_get(item, "user_results", "result")
                        if user_results:
                            user = self._parse_user_result(user_results)
                            if user:
                                new_users.append(user)
                    elif entry_type == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            next_cursor = content.get("value")

            for user in new_users:
                if user.id and user.id not in seen_ids:
                    seen_ids.add(user.id)
                    users.append(user)

            if not next_cursor or not new_users:
                break
            cursor = next_cursor

            if len(users) < count and self._request_delay > 0:
                time.sleep(self._request_delay * random.uniform(0.7, 1.5))

        return users[:count]

    @staticmethod
    def _parse_user_result(user_data):
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

        media = _extract_media(actual_legacy)
        urls = [item.get("expanded_url", "") for item in _deep_get(actual_legacy, "entities", "urls") or []]
        quoted = _deep_get(actual_data, "quoted_status_result", "result")
        quoted_tweet = self._parse_tweet_result(quoted, depth=depth + 1) if isinstance(quoted, dict) else None
        author = _extract_author(actual_user, actual_user_legacy)

        retweeted_by = None  # type: Optional[str]
        if is_retweet:
            retweeted_by = user_core.get("screen_name") or user_legacy.get("screen_name", "unknown")

        return Tweet(
            id=actual_data.get("rest_id", ""),
            text=actual_legacy.get("full_text", ""),
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

    # Convert draft.js blocks to Markdown
    parts = []  # type: List[str]
    ordered_counter = 0
    for block in blocks:
        block_type = block.get("type", "unstyled")  # type: str
        if block_type == "atomic":
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


def _extract_cursor(content):
    # type: (Dict[str, Any]) -> Optional[str]
    """Extract Bottom pagination cursor from timeline content."""
    if content.get("cursorType") == "Bottom":
        return content.get("value")
    return None


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
