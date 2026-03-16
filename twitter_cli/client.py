"""Twitter GraphQL API client."""

from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import os
import random
import time
import urllib.parse
from typing import TYPE_CHECKING, Any, Callable, cast

import bs4
from curl_cffi import requests as _cffi_requests
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers as _gen_ct_headers, get_ondemand_file_url

from .constants import (
    BEARER_TOKEN,
    SEC_CH_UA_BITNESS,
    SEC_CH_UA_MOBILE,
    SEC_CH_UA_MODEL,
    get_accept_language,
    get_sec_ch_ua_arch,
    get_sec_ch_ua,
    get_sec_ch_ua_full_version,
    get_sec_ch_ua_full_version_list,
    get_sec_ch_ua_platform,
    get_sec_ch_ua_platform_version,
    get_twitter_client_language,
    get_user_agent,
    sync_chrome_version,
)
from .exceptions import (
    MediaUploadError,
    NotFoundError,
    TwitterAPIError,
    UnsupportedFeatureError,
)
from .graphql import (
    FALLBACK_QUERY_IDS,
    FEATURES,
    _build_graphql_url,
    _invalidate_query_id,
    _resolve_query_id,
    _update_features_from_html,
)
from .models import UserProfile
from .parser import (
    _deep_get,
    _parse_int,
    parse_timeline_response,
    parse_tweet_result,
    parse_user_result,
)

if TYPE_CHECKING:
    from typing import Dict, List, Optional, Set, Tuple  # noqa: F401

    from .models import Tweet  # noqa: F401

logger = logging.getLogger(__name__)

# Shared curl_cffi session (single-threaded CLI)
_cffi_session = None

TimelineInstructionGetter = Callable[[Any], Any]

# Hard ceiling to prevent accidental massive fetches
_ABSOLUTE_MAX_COUNT = 500


# ── Session management ───────────────────────────────────────────────────


def _best_chrome_target():
    # type: () -> str
    """Detect the best available Chrome impersonation target at runtime.

    curl_cffi versions differ in which Chrome targets they ship.
    e.g. 0.14.0 has chrome133a but not chrome133.
    """
    try:
        from curl_cffi.requests import BrowserType
        available = {e.value for e in BrowserType}
    except ImportError:
        # curl_cffi not installed or BrowserType not available
        logger.debug("curl_cffi.BrowserType not available, using fallback targets")
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
        proxy = os.environ.get("TWITTER_PROXY", "")
        target = _best_chrome_target()
        sync_chrome_version(target)  # align UA/sec-ch-ua with impersonate target
        _cffi_session = _cffi_requests.Session(
            impersonate=cast(Any, target),
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


# ── TwitterClient ────────────────────────────────────────────────────────


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

    # ── Read operations ──────────────────────────────────────────────

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
            raise NotFoundError("User @%s not found" % screen_name)

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

        def get_likes_instructions(data):
            # type: (Any) -> Any
            # New path (2024+): data.user.result.timeline.timeline.instructions
            instructions = _deep_get(data, "data", "user", "result", "timeline", "timeline", "instructions")
            if instructions is None:
                # Legacy path: data.user.result.timeline_v2.timeline.instructions
                instructions = _deep_get(data, "data", "user", "result", "timeline_v2", "timeline", "instructions")
            return instructions

        return self._fetch_timeline(
            "Likes",
            count,
            get_likes_instructions,
            extra_variables={
                "userId": user_id,
                "includePromotedContent": False,
                "withClientEventToken": False,
                "withBirdwatchNotes": False,
                "withVoice": True,
            },
            override_base_variables=True,
        )

    def fetch_search(self, query, count=20, product="Top", scope="recent"):
        # type: (str, int, str, str) -> List[Tweet]
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

    def fetch_tweet_detail(self, tweet_id, count=20, reply_scope="auto"):
        # type: (str, int, str) -> List[Tweet]
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

    def fetch_mentions(self, user_id, count=20):
        # type: (str, int) -> List[Tweet]
        raise UnsupportedFeatureError("`mentions` is supported only in official API mode.")

    def fetch_article(self, tweet_id):
        # type: (str) -> Tweet
        """Fetch a Twitter Article by tweet ID."""
        logger.debug("fetch_article: tweet_id=%s", tweet_id)

        data = self._graphql_get(
            "TweetResultByRestId",
            variables={
                "tweetId": tweet_id,
                "withCommunity": False,
                "includePromotedContent": False,
                "withVoice": False,
            },
            features={
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "articles_preview_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
            },
            field_toggles={
                "withArticleRichContentState": True,
                "withArticlePlainText": True,
            },
        )

        result = _deep_get(data, "data", "tweetResult", "result")
        if not result:
            raise NotFoundError("Article not found: tweet_id=%s" % tweet_id)

        tweet = parse_tweet_result(result)
        if tweet is None or (tweet.article_title is None and tweet.article_text is None):
            raise NotFoundError("Tweet %s has no article content" % tweet_id)

        logger.info("fetch_article: tweet_id=%s", tweet_id)
        return tweet

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

    def fetch_list(self, list_id):
        # type: (str) -> Any
        raise UnsupportedFeatureError("`list-info` is supported only in official API mode.")

    def fetch_owned_lists(self, user_id, count=20):
        # type: (str, int) -> List[Any]
        raise UnsupportedFeatureError("`owned-lists` is supported only in official API mode.")

    def fetch_followed_lists(self, user_id, count=20):
        # type: (str, int) -> List[Any]
        raise UnsupportedFeatureError("`followed-lists` is supported only in official API mode.")

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

    # ── Write operations ─────────────────────────────────────────────

    # Supported image MIME types and max file size (5 MB)
    _SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    _MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

    def _write_delay(self):
        # type: () -> None
        """Sleep a random interval after write operations to avoid rate limits."""
        delay = random.uniform(1.5, 4.0)
        logger.debug("Write operation delay: %.1fs", delay)
        time.sleep(delay)

    def upload_media(self, file_path, alt_text=None):
        # type: (str, Any) -> str
        """Upload an image file to Twitter.  Returns the media_id string.

        Uses Twitter's chunked upload API (INIT → APPEND → FINALIZE).
        Supports JPEG, PNG, GIF, and WebP images up to 5 MB.
        """
        if not os.path.isfile(file_path):
            raise MediaUploadError("File not found: %s" % file_path)

        file_size = os.path.getsize(file_path)
        if file_size > self._MAX_IMAGE_SIZE:
            raise MediaUploadError(
                "File too large: %.1f MB (max %.0f MB)"
                % (file_size / (1024 * 1024), self._MAX_IMAGE_SIZE / (1024 * 1024))
            )

        media_type = mimetypes.guess_type(file_path)[0] or ""
        if media_type not in self._SUPPORTED_IMAGE_TYPES:
            raise MediaUploadError(
                "Unsupported image format: %s (supported: jpeg, png, gif, webp)" % media_type
            )

        upload_url = "https://upload.twitter.com/i/media/upload.json"
        session = _get_cffi_session()

        # ── INIT ─────────────────────────────────────────────────────
        headers = self._build_headers(url=upload_url, method="POST")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        init_data = {
            "command": "INIT",
            "total_bytes": str(file_size),
            "media_type": media_type,
        }
        resp = session.post(upload_url, headers=headers, data=init_data, timeout=30)
        if resp.status_code >= 400:
            raise MediaUploadError("INIT failed (HTTP %d): %s" % (resp.status_code, resp.text[:300]))
        try:
            init_result = json.loads(resp.text)
        except (json.JSONDecodeError, ValueError):
            raise MediaUploadError("INIT returned invalid JSON")
        media_id = init_result.get("media_id_string", "")
        if not media_id:
            raise MediaUploadError("INIT did not return media_id")
        logger.info("Media INIT: media_id=%s", media_id)

        # ── APPEND ───────────────────────────────────────────────────
        with open(file_path, "rb") as f:
            media_data = base64.b64encode(f.read()).decode("ascii")

        headers = self._build_headers(url=upload_url, method="POST")
        # Remove JSON content-type — curl_cffi handles multipart encoding
        headers.pop("Content-Type", None)
        append_data = {
            "command": "APPEND",
            "media_id": media_id,
            "segment_index": "0",
            "media_data": media_data,
        }
        resp = session.post(upload_url, headers=headers, data=append_data, timeout=60)
        if resp.status_code >= 400:
            raise MediaUploadError("APPEND failed (HTTP %d): %s" % (resp.status_code, resp.text[:300]))
        logger.info("Media APPEND: segment 0 uploaded")

        # ── FINALIZE ─────────────────────────────────────────────────
        headers = self._build_headers(url=upload_url, method="POST")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        finalize_data = {
            "command": "FINALIZE",
            "media_id": media_id,
        }
        resp = session.post(upload_url, headers=headers, data=finalize_data, timeout=30)
        if resp.status_code >= 400:
            raise MediaUploadError("FINALIZE failed (HTTP %d): %s" % (resp.status_code, resp.text[:300]))
        logger.info("Media FINALIZE: media_id=%s ready", media_id)

        return media_id

    def create_tweet(self, text, reply_to_id=None, media_ids=None):
        # type: (str, Optional[str], Optional[List[str]]) -> str
        """Post a new tweet.  Returns the new tweet ID.

        Args:
            text: Tweet text content.
            reply_to_id: Optional tweet ID to reply to.
            media_ids: Optional list of media IDs (from upload_media) to attach.
        """
        media_entities = []
        if media_ids:
            media_entities = [{"media_id": mid, "tagged_users": []} for mid in media_ids]
        variables = {
            "tweet_text": text,
            "media": {"media_entities": media_entities, "possibly_sensitive": False},
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
        raise TwitterAPIError(0, "Failed to create tweet")

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
        """Fetch the currently authenticated user's profile.

        Twitter's /account/multi/list.json endpoint changed its response format:
        - Old: list of dicts with nested "user" objects (rich fields)
        - New: {"users": [...]} with minimal fields (user_id, name, screen_name)

        When the response only has minimal fields, we use the screen_name to
        fetch the full profile via the GraphQL UserByScreenName endpoint.
        """
        url = "https://x.com/i/api/1.1/account/multi/list.json"
        data = self._api_get(url)

        screen_name = None

        # New format: {"users": [{"user_id": ..., "screen_name": ..., ...}]}
        if isinstance(data, dict) and "users" in data:
            users = data["users"]
            if isinstance(users, list) and users:
                user_data = users[0]
                screen_name = user_data.get("screen_name")

        # Old format: [{"user": {"id_str": ..., ...}}]
        elif isinstance(data, list) and data:
            user_data = data[0].get("user", {})
            if user_data:
                # Old format had rich fields — try to build profile directly
                sn = user_data.get("screen_name", "")
                if user_data.get("followers_count") is not None:
                    return UserProfile(
                        id=str(user_data.get("id_str", "")),
                        name=user_data.get("name", ""),
                        screen_name=sn,
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
                screen_name = sn

        # Use screen_name to fetch full profile via GraphQL
        if screen_name:
            logger.info("Fetching full profile for @%s via GraphQL", screen_name)
            return self.fetch_user(screen_name)

        raise TwitterAPIError(0, "Failed to fetch current user info")

    def quote_tweet(self, tweet_id, text, media_ids=None):
        # type: (str, str, Optional[List[str]]) -> str
        """Quote-tweet a tweet.  Returns the new tweet ID.

        Args:
            tweet_id: The tweet ID to quote.
            text: Commentary text.
            media_ids: Optional list of media IDs (from upload_media) to attach.
        """
        media_entities = []
        if media_ids:
            media_entities = [{"media_id": mid, "tagged_users": []} for mid in media_ids]
        variables = {
            "tweet_text": text,
            "attachment_url": "https://x.com/i/status/%s" % tweet_id,
            "media": {"media_entities": media_entities, "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "dark_request": False,
        }
        data = self._graphql_post("CreateTweet", variables, FEATURES)
        self._write_delay()
        result = _deep_get(data, "data", "create_tweet", "tweet_results", "result")
        if result:
            return result.get("rest_id", "")
        raise TwitterAPIError(0, "Failed to create quote tweet")

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
            raise TwitterAPIError(response.status_code, "Failed to follow user")
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
            raise TwitterAPIError(response.status_code, "Failed to unfollow user")
        self._write_delay()
        return True

    # ── Internal: timeline / user list fetchers ──────────────────────

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
            variables: Dict[str, Any]
            if override_base_variables:
                variables = {"count": min(count - len(tweets) + 5, 40)}
            else:
                variables = {
                    "count": min(count - len(tweets) + 5, 40),
                    "includePromotedContent": False,
                    "latestControlAvailable": True,
                    "requestContext": "launch",
                }
            if extra_variables:
                variables.update(extra_variables)
            if cursor:
                variables["cursor"] = cursor

            data = self._graphql_get(operation_name, variables, FEATURES, field_toggles=field_toggles)
            new_tweets, next_cursor = parse_timeline_response(data, get_instructions)

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
                            user = parse_user_result(user_results)
                            if user:
                                new_users.append(user)
                    elif entry_type == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            next_cursor = content.get("value")

            for user in new_users:
                if user.id and user.id not in seen_ids:
                    seen_ids.add(user.id)
                    users.append(user)

            if not next_cursor:
                break
            if next_cursor == cursor:
                logger.debug("User list pagination stopped because cursor did not advance: %s", next_cursor)
                break
            cursor = next_cursor

            if not new_users:
                logger.debug("User list page returned no users but exposed next cursor; continuing pagination")

            if len(users) < count and self._request_delay > 0:
                time.sleep(self._request_delay * random.uniform(0.7, 1.5))

        return users[:count]

    # ── Internal: GraphQL request methods ────────────────────────────

    def _graphql_get(self, operation_name, variables, features, field_toggles=None):
        # type: (str, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Issue GraphQL GET request with automatic stale-fallback retry."""
        query_id = _resolve_query_id(operation_name, prefer_fallback=True, url_fetch_fn=_url_fetch)
        using_fallback = query_id == FALLBACK_QUERY_IDS.get(operation_name)
        url = _build_graphql_url(query_id, operation_name, variables, features, field_toggles)

        try:
            return self._api_get(url)
        except TwitterAPIError as exc:
            # Fallback query IDs can go stale. Retry with live lookup if 404/422.
            if exc.status_code in (404, 422) and using_fallback:
                logger.info("Retrying %s with live queryId after %d", operation_name, exc.status_code)
                _invalidate_query_id(operation_name)
                refreshed_query_id = _resolve_query_id(operation_name, prefer_fallback=False, url_fetch_fn=_url_fetch)
                retry_url = _build_graphql_url(refreshed_query_id, operation_name, variables, features, field_toggles)
                return self._api_get(retry_url)
            raise

    def _graphql_post(self, operation_name, variables, features=None):
        # type: (str, Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Issue GraphQL POST request with automatic stale-fallback retry."""
        query_id = _resolve_query_id(operation_name, prefer_fallback=True, url_fetch_fn=_url_fetch)
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
            if exc.status_code in (404, 422) and using_fallback:
                logger.info("Retrying POST %s with live queryId after %d", operation_name, exc.status_code)
                _invalidate_query_id(operation_name)
                refreshed = _resolve_query_id(operation_name, prefer_fallback=False, url_fetch_fn=_url_fetch)
                return _do_post(refreshed)
            raise

    # ── Internal: HTTP request engine ────────────────────────────────

    def _api_get(self, url):
        # type: (str) -> Dict[str, Any]
        """Make authenticated GET request to Twitter API."""
        return self._api_request(url, method="GET")

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

    # ── Internal: Anti-detection / headers ───────────────────────────

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
            "sec-ch-ua-arch": get_sec_ch_ua_arch(),
            "sec-ch-ua-bitness": SEC_CH_UA_BITNESS,
            "sec-ch-ua-full-version": get_sec_ch_ua_full_version(),
            "sec-ch-ua-full-version-list": get_sec_ch_ua_full_version_list(),
            "sec-ch-ua-model": SEC_CH_UA_MODEL,
            "sec-ch-ua-platform-version": get_sec_ch_ua_platform_version(),
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
