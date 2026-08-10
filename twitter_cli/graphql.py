"""GraphQL infrastructure for Twitter API.

Handles queryId resolution, URL building, JS bundle scanning,
and feature flag management.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional  # noqa: F401

from .exceptions import QueryIdError

logger = logging.getLogger(__name__)

# ── Community OpenAPI queryId source ─────────────────────────────────────
TWITTER_OPENAPI_URL = (
    "https://raw.githubusercontent.com/fa0311/"
    "twitter-openapi/refs/heads/main/src/config/placeholder.json"
)

# ── Fallback (hardcoded) queryIds ────────────────────────────────────────
FALLBACK_QUERY_IDS = {
    "HomeTimeline": "HCosKfLNW1AcOo3la3mMgg",
    "HomeLatestTimeline": "U0cdisy7QFIoTfu3-Okw0A",
    "UserByScreenName": "qRednkZG-rn1P6b48NINmQ",
    "UserTweets": "SXVCYB8XHSS25nzIljNtZA",
    "TweetDetail": "XMOz5h24KAZ86qKffKTLdQ",
    "Likes": "xA8fDIbrJfy4ojjjXmSR-A",
    "SearchTimeline": "hyPfJYJ_XAtDYoslQc-Rgg",
    "Bookmarks": "uzboyXSHSJrR-mGJqep0TQ",
    "ListLatestTweetsTimeline": "ZBbXrl0FVnTqp7K6EAADog",
    "Followers": "JNyQdTISpzCkj_1fqxDvFg",
    "Following": "qGZZDF3mp91q7X22s3HxpA",
    "CreateTweet": "WXTdKnLddrQOunD6MhWi3g",
    "DeleteTweet": "VaenaVgh5q5ih7kvyVjgtg",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "CreateRetweet": "ojPdsZsimiJrUGLR1sjVsA",
    "DeleteRetweet": "iQtK4dl5hBmXewYZuEOKVw",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xISYCixDmuS8KNg",
    "TweetResultByRestId": "7xflPyRiUxGVbJd4uWmbfg",
    "BookmarkFoldersSlice": "i78YDd0Tza-dV4SYs58kRg",
    "BookmarkFolderTimeline": "hNY7X2xE2N7HVF6Qb_mu6w",
}

# ── Default feature flags ────────────────────────────────────────────────
_DEFAULT_FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "responsive_web_media_download_video_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "responsive_web_enhance_cards_enabled": False,
}

# Features dict that gets updated dynamically from x.com JS bundles
FEATURES = dict(_DEFAULT_FEATURES)

从 GitHub twitter-openapi 动态更新 features（X 改版后 features 变化频繁）
def _update_features_from_github():
    """从 GitHub twitter-openapi 更新 features 映射"""
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(TWITTER_OPENAPI_URL)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        # 合并所有 endpoint 的 features
        for op_name, op_data in data.items():
            if not isinstance(op_data, dict):
                continue
            # features 可能在 variables 里或直接在节点里
            op_features = op_data.get('variables', {}).get('features', {})
            if not op_features:
                op_features = op_data.get('features', {})
            if isinstance(op_features, dict):
                for k, v in op_features.items():
                    FEATURES[k] = v  # 覆盖已有值也加入新值
        import logging
        logging.getLogger(__name__).info("Updated FEATURES from GitHub: %d keys", len(FEATURES))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("GitHub features update failed: %s", exc)

# 启动时自动更新
_update_features_from_github()

# Module-level caches (not thread-safe — CLI is single-threaded)
_cached_query_ids: Dict[str, str] = {}
_bundles_scanned = False


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


def _scan_bundles(url_fetch_fn):
    # type: (Any) -> None
    """Scan Twitter JS bundles and cache queryId mappings.

    Args:
        url_fetch_fn: Function to fetch URLs (injected to avoid circular import).
    """
    global _bundles_scanned
    if _bundles_scanned:
        return
    _bundles_scanned = True

    try:
        from .constants import get_user_agent
        html = url_fetch_fn("https://x.com/search?q=AI&f=live", {"user-agent": get_user_agent()})
        匹配旧版 responsive-web 和新版 x-web 的 JS URL
        script_pattern = re.compile(
            r'(?:src|href)=["\']'
            r'(https://abs\.twimg\.com/(?:responsive-web/client-web|x-web/x-web)[^"\']+'
            r'\.js)'
            r'["\']'
        )
        script_urls = script_pattern.findall(html)
        
        X 改版后 chunk 文件在 entry-client JS 里引用，需要递归扫描
        # entry-client JS 里 __vite__mapDeps 包含所有 chunk 文件名
        for main_url in list(script_urls):
            try:
                main_bundle = url_fetch_fn(main_url)
                # 找 assets/xxx.js 引用
                chunk_refs = re.findall(r'["\']assets/([^"\']+\.js)["\']', main_bundle)
                base = main_url.rsplit('/', 1)[0] + '/'
                for chunk_ref in chunk_refs:
                    chunk_url = base + 'assets/' + chunk_ref
                    if chunk_url not in script_urls:
                        script_urls.append(chunk_url)
            except Exception:
                continue
    except Exception as exc:  # pragma: no cover - network-dependent branch
        logger.warning("Failed to scan JS bundles: %s", exc)
        return

    for script_url in script_urls:
        try:
            bundle = url_fetch_fn(script_url)
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


def _fetch_from_github(url_fetch_fn, operation_name):
    # type: (Any, str) -> Optional[str]
    """Fetch queryId from community-maintained twitter-openapi file."""
    try:
        payload = url_fetch_fn(TWITTER_OPENAPI_URL)
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


def _resolve_query_id(operation_name, prefer_fallback=True, url_fetch_fn=None):
    # type: (str, bool, Any) -> str
    """Resolve queryId using cache, remote sources, and fallback constants."""
    cached = _cached_query_ids.get(operation_name)
    if cached:
        return cached

    优先从 X main bundle 获取最新 queryId（搜索页面）
    # GitHub twitter-openapi 可能也过期了
    if url_fetch_fn:
        _scan_bundles(url_fetch_fn)
        cached = _cached_query_ids.get(operation_name)
        if cached:
            return cached

        # 如果 _scan_bundles 没找到，再从 GitHub 获取
        github_query_id = _fetch_from_github(url_fetch_fn, operation_name)
        if github_query_id:
            _cached_query_ids[operation_name] = github_query_id
            return github_query_id

    fallback = FALLBACK_QUERY_IDS.get(operation_name)
    if prefer_fallback and fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    if fallback:
        _cached_query_ids[operation_name] = fallback
        return fallback

    raise QueryIdError('Cannot resolve queryId for "%s"' % operation_name)
