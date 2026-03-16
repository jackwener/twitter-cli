"""Official X/Twitter API v2 client."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import logging
import mimetypes
import os
import time
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, cast

from curl_cffi import requests as _cffi_requests

from .exceptions import (
    AuthenticationError,
    MediaUploadError,
    NetworkError,
    NotFoundError,
    TwitterAPIError,
)
from .models import Author, Metrics, Tweet, TweetMedia, TwitterList, UserProfile

logger = logging.getLogger(__name__)

_API_BASE_URL = "https://api.x.com/2"
_ABSOLUTE_MAX_COUNT = 500
_USER_FIELDS = "created_at,description,entities,location,profile_image_url,public_metrics,verified"
_TWEET_FIELDS = "attachments,author_id,created_at,entities,lang,public_metrics,referenced_tweets"
_MEDIA_FIELDS = "media_key,preview_image_url,type,url,width,height"
_TWEET_EXPANSIONS = "author_id,attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id"
_LIST_FIELDS = "created_at,description,follower_count,id,member_count,name,owner_id,private"
_DETAIL_TWEET_FIELDS = (
    "article,attachments,author_id,conversation_id,created_at,entities,in_reply_to_user_id,"
    "lang,note_tweet,public_metrics,referenced_tweets"
)
_DETAIL_TWEET_EXPANSIONS = (
    "article.cover_media,article.media_entities,author_id,attachments.media_keys,"
    "referenced_tweets.id,referenced_tweets.id.author_id"
)
_api_session: Any = None


def has_api_credentials() -> bool:
    """Return True when official API credentials are configured."""
    return bool(
        os.environ.get("TWITTER_API_ACCESS_TOKEN", "").strip()
        or os.environ.get("TWITTER_API_BEARER_TOKEN", "").strip()
    )


def _get_api_session() -> Any:
    """Return a shared session for official API requests."""
    global _api_session
    if _api_session is None:
        proxy = os.environ.get("TWITTER_PROXY", "").strip()
        _api_session = _cffi_requests.Session(
            proxies={"https": proxy, "http": proxy} if proxy else None,
        )
    return _api_session


class TwitterAPIv2Client:
    """Official X/Twitter API v2 client for a supported subset of commands."""

    _SUPPORTED_IMAGE_TYPES = {
        "image/bmp",
        "image/jpeg",
        "image/pjpeg",
        "image/png",
        "image/tiff",
        "image/webp",
    }
    _SUPPORTED_GIF_TYPES = {"image/gif"}
    _SUPPORTED_VIDEO_TYPES = {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/mp2t",
    }
    _MAX_IMAGE_SIZE = 5 * 1024 * 1024
    _MAX_GIF_SIZE = 15 * 1024 * 1024
    _MAX_VIDEO_SIZE = 512 * 1024 * 1024
    _UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024

    def __init__(self, rate_limit_config: Optional[Dict[str, Any]] = None) -> None:
        self._access_token = os.environ.get("TWITTER_API_ACCESS_TOKEN", "").strip()
        self._bearer_token = os.environ.get("TWITTER_API_BEARER_TOKEN", "").strip()
        self._configured_user_id = os.environ.get("TWITTER_API_USER_ID", "").strip()
        if not self._access_token and not self._bearer_token:
            raise AuthenticationError(
                "Official API mode requires TWITTER_API_ACCESS_TOKEN or TWITTER_API_BEARER_TOKEN."
            )

        rl = rate_limit_config or {}
        self._request_delay = float(rl.get("requestDelay", 2.5))
        self._max_retries = int(rl.get("maxRetries", 3))
        self._retry_base_delay = float(rl.get("retryBaseDelay", 5.0))
        self._max_count = min(int(rl.get("maxCount", 200)), _ABSOLUTE_MAX_COUNT)
        self._me_cache: Optional[UserProfile] = None

    # ── Read operations ──────────────────────────────────────────────

    def fetch_user(self, screen_name: str) -> UserProfile:
        data = self._api_request(
            "GET",
            "/users/by/username/%s" % urllib.parse.quote(screen_name),
            params={"user.fields": _USER_FIELDS},
        )
        user = data.get("data")
        if not isinstance(user, dict):
            raise NotFoundError("User @%s not found" % screen_name)
        return self._parse_user(user)

    def fetch_me(self) -> UserProfile:
        if self._me_cache is not None:
            return self._me_cache
        data = self._api_request(
            "GET",
            "/users/me",
            params={"user.fields": _USER_FIELDS},
            require_user_context=True,
        )
        user = data.get("data")
        if not isinstance(user, dict):
            raise TwitterAPIError(0, "Failed to fetch current user info")
        self._me_cache = self._parse_user(user)
        return self._me_cache

    def resolve_user_id(self, identifier: str) -> str:
        if identifier.isdigit():
            return identifier
        return self.fetch_user(identifier).id

    def fetch_user_tweets(self, user_id: str, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/users/%s/tweets" % user_id,
            count,
            {
                "exclude": "replies",
                "tweet.fields": _TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )

    def fetch_search(
        self,
        query: str,
        count: int = 20,
        product: str = "Top",
        scope: str = "recent",
    ) -> List[Tweet]:
        search_query = query
        sort_order = "relevancy"
        normalized_product = (product or "Top").strip().lower()
        if normalized_product == "latest":
            sort_order = "recency"
        elif normalized_product == "photos":
            sort_order = "recency"
            search_query = "%s has:images" % query
        elif normalized_product == "videos":
            sort_order = "recency"
            search_query = "%s has:videos" % query

        return self._paginate_tweets(
            self._search_path(scope),
            count,
            {
                "query": search_query,
                "sort_order": sort_order,
                "tweet.fields": _TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )

    def fetch_followers(self, user_id: str, count: int = 20) -> List[UserProfile]:
        return self._paginate_users(
            "/users/%s/followers" % user_id,
            count,
            {"user.fields": _USER_FIELDS},
        )

    def fetch_following(self, user_id: str, count: int = 20) -> List[UserProfile]:
        return self._paginate_users(
            "/users/%s/following" % user_id,
            count,
            {"user.fields": _USER_FIELDS},
        )

    def fetch_mentions(self, user_id: str, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/users/%s/mentions" % user_id,
            count,
            {
                "tweet.fields": _DETAIL_TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )

    # ── Write operations ─────────────────────────────────────────────

    def create_tweet(
        self,
        text: str,
        reply_to_id: Optional[str] = None,
        media_ids: Optional[List[str]] = None,
    ) -> str:
        body: Dict[str, Any] = {"text": text}
        if reply_to_id:
            body["reply"] = {"in_reply_to_tweet_id": reply_to_id}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        data = self._api_request("POST", "/tweets", json_body=body, require_user_context=True)
        created = data.get("data") or {}
        tweet_id = str(created.get("id") or "")
        if not tweet_id:
            raise TwitterAPIError(0, "Failed to create tweet")
        self._write_delay()
        return tweet_id

    def quote_tweet(self, tweet_id: str, text: str, media_ids: Optional[List[str]] = None) -> str:
        body: Dict[str, Any] = {"text": text, "quote_tweet_id": tweet_id}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        data = self._api_request(
            "POST",
            "/tweets",
            json_body=body,
            require_user_context=True,
        )
        created = data.get("data") or {}
        created_id = str(created.get("id") or "")
        if not created_id:
            raise TwitterAPIError(0, "Failed to create quote tweet")
        self._write_delay()
        return created_id

    def delete_tweet(self, tweet_id: str) -> bool:
        self._api_request("DELETE", "/tweets/%s" % tweet_id, require_user_context=True)
        self._write_delay()
        return True

    def like_tweet(self, tweet_id: str) -> bool:
        self._api_request(
            "POST",
            "/users/%s/likes" % self._authenticated_user_id(),
            json_body={"tweet_id": tweet_id},
            require_user_context=True,
        )
        self._write_delay()
        return True

    def unlike_tweet(self, tweet_id: str) -> bool:
        self._api_request(
            "DELETE",
            "/users/%s/likes/%s" % (self._authenticated_user_id(), tweet_id),
            require_user_context=True,
        )
        self._write_delay()
        return True

    def retweet(self, tweet_id: str) -> bool:
        self._api_request(
            "POST",
            "/users/%s/retweets" % self._authenticated_user_id(),
            json_body={"tweet_id": tweet_id},
            require_user_context=True,
        )
        self._write_delay()
        return True

    def unretweet(self, tweet_id: str) -> bool:
        self._api_request(
            "DELETE",
            "/users/%s/retweets/%s" % (self._authenticated_user_id(), tweet_id),
            require_user_context=True,
        )
        self._write_delay()
        return True

    def follow_user(self, user_id: str) -> bool:
        self._api_request(
            "POST",
            "/users/%s/following" % self._authenticated_user_id(),
            json_body={"target_user_id": user_id},
            require_user_context=True,
        )
        self._write_delay()
        return True

    def unfollow_user(self, user_id: str) -> bool:
        self._api_request(
            "DELETE",
            "/users/%s/following/%s" % (self._authenticated_user_id(), user_id),
            require_user_context=True,
        )
        self._write_delay()
        return True

    # ── Timeline and bookmark operations ─────────────────────────────

    def fetch_home_timeline(self, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/users/%s/timelines/reverse_chronological" % self._authenticated_user_id(),
            count,
            {
                "tweet.fields": _DETAIL_TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
            require_user_context=True,
        )

    def fetch_following_feed(self, count: int = 20) -> List[Tweet]:
        return self.fetch_home_timeline(count)

    def fetch_bookmarks(self, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/users/%s/bookmarks" % self._authenticated_user_id(),
            count,
            {
                "tweet.fields": _TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
            require_user_context=True,
        )

    def fetch_user_likes(self, user_id: str, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/users/%s/liked_tweets" % user_id,
            count,
            {
                "tweet.fields": _TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )

    def fetch_tweet_detail(self, tweet_id: str, count: int = 20, reply_scope: str = "auto") -> List[Tweet]:
        root_data, includes = self._lookup_tweet_payload(tweet_id, include_article=True)
        root_tweets = self._parse_tweets([root_data], includes)
        if not root_tweets:
            raise NotFoundError("Tweet %s not found" % tweet_id)
        root_tweet = root_tweets[0]
        if count <= 1:
            return [root_tweet]

        conversation_id = str(root_data.get("conversation_id") or root_tweet.id or tweet_id)
        reply_query = (
            "conversation_id:%s" % conversation_id
            if conversation_id == root_tweet.id
            else "in_reply_to_tweet_id:%s" % root_tweet.id
        )
        replies = self._fetch_conversation_replies(
            max(count * 2, count),
            reply_query,
            root_tweet.created_at,
            reply_scope=reply_scope,
        )
        filtered_replies = [tweet for tweet in replies if tweet.id != root_tweet.id]
        filtered_replies.sort(key=lambda tweet: tweet.created_at)
        return [root_tweet] + filtered_replies[: max(count - 1, 0)]

    def fetch_article(self, tweet_id: str) -> Tweet:
        tweet_data, includes = self._lookup_tweet_payload(tweet_id, include_article=True)
        tweets = self._parse_tweets([tweet_data], includes)
        if not tweets:
            raise NotFoundError("Tweet %s not found" % tweet_id)
        article_tweet = tweets[0]
        if article_tweet.article_title is None and article_tweet.article_text is None:
            raise NotFoundError("Tweet %s has no article content" % tweet_id)
        return article_tweet

    def fetch_list_timeline(self, list_id: str, count: int = 20) -> List[Tweet]:
        return self._paginate_tweets(
            "/lists/%s/tweets" % list_id,
            count,
            {
                "tweet.fields": _TWEET_FIELDS,
                "expansions": _TWEET_EXPANSIONS,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )

    def fetch_list(self, list_id: str) -> TwitterList:
        data = self._api_request(
            "GET",
            "/lists/%s" % list_id,
            params={
                "list.fields": _LIST_FIELDS,
                "expansions": "owner_id",
                "user.fields": _USER_FIELDS,
            },
        )
        list_data = data.get("data")
        if not isinstance(list_data, dict):
            raise NotFoundError("List %s not found" % list_id)
        raw_includes = data.get("includes")
        includes: Dict[str, Any] = raw_includes if isinstance(raw_includes, dict) else {}
        return self._parse_list(list_data, includes)

    def fetch_owned_lists(self, user_id: str, count: int = 20) -> List[TwitterList]:
        return self._paginate_lists("/users/%s/owned_lists" % user_id, count)

    def fetch_followed_lists(self, user_id: str, count: int = 20) -> List[TwitterList]:
        return self._paginate_lists(
            "/users/%s/followed_lists" % user_id,
            count,
            require_user_context=True,
        )

    def bookmark_tweet(self, tweet_id: str) -> bool:
        self._api_request(
            "POST",
            "/users/%s/bookmarks" % self._authenticated_user_id(),
            json_body={"tweet_id": tweet_id},
            require_user_context=True,
        )
        self._write_delay()
        return True

    def unbookmark_tweet(self, tweet_id: str) -> bool:
        self._api_request(
            "DELETE",
            "/users/%s/bookmarks/%s" % (self._authenticated_user_id(), tweet_id),
            require_user_context=True,
        )
        self._write_delay()
        return True

    def upload_media(self, path: str, alt_text: Optional[str] = None) -> str:
        if not self._access_token:
            raise AuthenticationError("Official API media upload requires TWITTER_API_ACCESS_TOKEN.")
        if not os.path.isfile(path):
            raise MediaUploadError("File not found: %s" % path)

        file_size = os.path.getsize(path)
        media_type = mimetypes.guess_type(path)[0] or ""
        media_category: Optional[str] = None
        if media_type in self._SUPPORTED_IMAGE_TYPES:
            max_size = self._MAX_IMAGE_SIZE
        elif media_type in self._SUPPORTED_GIF_TYPES:
            max_size = self._MAX_GIF_SIZE
            media_category = "tweet_gif"
        elif media_type in self._SUPPORTED_VIDEO_TYPES:
            max_size = self._MAX_VIDEO_SIZE
            media_category = "tweet_video"
        else:
            raise MediaUploadError(
                "Unsupported media format: %s (supported: bmp, jpeg, png, tiff, webp, gif, mp4, mov, webm)" % media_type,
            )
        if file_size > max_size:
            raise MediaUploadError(
                "File too large: %.1f MB (max %.0f MB)"
                % (file_size / (1024 * 1024), max_size / (1024 * 1024)),
            )
        if media_category is None:
            media_id = self._upload_simple_media(path, media_type)
        else:
            media_id = self._upload_chunked_media(
                path,
                media_type,
                file_size,
                media_category=media_category,
            )
        if alt_text:
            self._apply_media_alt_text(media_id, alt_text)
        return media_id

    # ── Internals ────────────────────────────────────────────────────

    def _authenticated_user_id(self) -> str:
        if self._configured_user_id:
            return self._configured_user_id
        return self.fetch_me().id

    def _api_headers(self, *, require_user_context: bool) -> Dict[str, str]:
        token = self._access_token if require_user_context else (self._access_token or self._bearer_token)
        if require_user_context and not token:
            raise AuthenticationError(
                "Official API user-context commands require TWITTER_API_ACCESS_TOKEN."
            )
        if not token:
            raise AuthenticationError(
                "Official API mode requires TWITTER_API_ACCESS_TOKEN or TWITTER_API_BEARER_TOKEN."
            )
        return {
            "Authorization": "Bearer %s" % token,
            "Accept": "application/json",
            "User-Agent": "twitter-cli",
        }

    def _upload_simple_media(self, path: str, media_type: str) -> str:
        with open(path, "rb") as media_file:
            media = base64.b64encode(media_file.read()).decode("ascii")
        data = self._api_request(
            "POST",
            "/media/upload",
            json_body={
                "media": media,
                "media_category": "tweet_image",
                "media_type": media_type,
                "shared": False,
            },
            require_user_context=True,
        )
        raw_media_payload = data.get("data")
        media_payload: Dict[str, Any] = raw_media_payload if isinstance(raw_media_payload, dict) else {}
        media_id = str(media_payload.get("id") or "")
        if not media_id:
            raise MediaUploadError("Media upload did not return an id")
        self._wait_for_media(media_id, media_payload.get("processing_info"))
        return media_id

    def _upload_chunked_media(
        self,
        path: str,
        media_type: str,
        file_size: int,
        *,
        media_category: str,
    ) -> str:
        initialized = self._api_request(
            "POST",
            "/media/upload/initialize",
            json_body={
                "media_category": media_category,
                "media_type": media_type,
                "shared": False,
                "total_bytes": file_size,
            },
            require_user_context=True,
        )
        raw_init_data = initialized.get("data")
        init_data: Dict[str, Any] = raw_init_data if isinstance(raw_init_data, dict) else {}
        media_id = str(init_data.get("id") or "")
        if not media_id:
            raise MediaUploadError("Media upload initialize did not return an id")

        session = _get_api_session()
        headers = self._api_headers(require_user_context=True)
        for segment_index, chunk in enumerate(self._iter_file_chunks(path)):
            response = session.post(
                "%s/media/upload/%s/append" % (_API_BASE_URL, media_id),
                headers=headers,
                data={"segment_index": str(segment_index)},
                files={"media": ("chunk", chunk)},
                timeout=60,
            )
            payload = self._safe_json(response)
            if response.status_code >= 400:
                raise MediaUploadError(self._extract_error_message(payload, response.text))

        finalized = self._api_request(
            "POST",
            "/media/upload/%s/finalize" % media_id,
            require_user_context=True,
        )
        raw_finalize_data = finalized.get("data")
        finalize_data: Dict[str, Any] = raw_finalize_data if isinstance(raw_finalize_data, dict) else {}
        self._wait_for_media(media_id, finalize_data.get("processing_info"))
        return media_id

    def _iter_file_chunks(self, path: str) -> Iterator[bytes]:
        with open(path, "rb") as media_file:
            while True:
                chunk = media_file.read(self._UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    def _apply_media_alt_text(self, media_id: str, alt_text: str) -> None:
        text = alt_text.strip()
        if not text:
            return
        if len(text) > 1000:
            raise MediaUploadError("Alt text must be 1000 characters or fewer.")
        self._api_request(
            "POST",
            "/media/metadata",
            json_body={
                "id": media_id,
                "metadata": {
                    "alt_text": {
                        "text": text,
                    }
                },
            },
            require_user_context=True,
        )

    def _search_path(self, scope: str) -> str:
        normalized = (scope or "recent").strip().lower()
        if normalized == "all":
            return "/tweets/search/all"
        return "/tweets/search/recent"

    def _fetch_conversation_replies(
        self,
        count: int,
        query: str,
        root_created_at: str,
        *,
        reply_scope: str,
    ) -> List[Tweet]:
        params = {
            "query": query,
            "sort_order": "recency",
            "tweet.fields": _DETAIL_TWEET_FIELDS,
            "expansions": _TWEET_EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "media.fields": _MEDIA_FIELDS,
        }
        scope = (reply_scope or "auto").strip().lower()
        if scope == "recent":
            return self._paginate_tweets("/tweets/search/recent", count, params)
        if scope == "all":
            return self._paginate_tweets("/tweets/search/all", count, params)

        preferred_path = "/tweets/search/all" if self._is_older_than_recent_search(root_created_at) else "/tweets/search/recent"
        replies = self._try_paginate_tweets(preferred_path, count, params)
        if replies is not None:
            return replies
        fallback_path = "/tweets/search/recent" if preferred_path.endswith("/all") else "/tweets/search/all"
        fallback = self._try_paginate_tweets(fallback_path, count, params)
        return fallback if fallback is not None else []

    def _is_older_than_recent_search(self, created_at: str) -> bool:
        if not created_at:
            return False
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return created < datetime.now(timezone.utc) - timedelta(days=7)

    def _try_paginate_tweets(
        self,
        path: str,
        count: int,
        params: Dict[str, Any],
    ) -> Optional[List[Tweet]]:
        try:
            return self._paginate_tweets(path, count, params)
        except TwitterAPIError as exc:
            if path.endswith("/all") and exc.status_code in (403, 404):
                return None
            raise

    def _lookup_tweet_payload(
        self,
        tweet_id: str,
        *,
        include_article: bool = False,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        expansions = _DETAIL_TWEET_EXPANSIONS if include_article else _TWEET_EXPANSIONS
        tweet_fields = _DETAIL_TWEET_FIELDS if include_article else _TWEET_FIELDS
        data = self._api_request(
            "GET",
            "/tweets/%s" % tweet_id,
            params={
                "tweet.fields": tweet_fields,
                "expansions": expansions,
                "user.fields": _USER_FIELDS,
                "media.fields": _MEDIA_FIELDS,
            },
        )
        tweet = data.get("data")
        if not isinstance(tweet, dict):
            raise NotFoundError("Tweet %s not found" % tweet_id)
        raw_includes = data.get("includes")
        includes: Dict[str, Any] = raw_includes if isinstance(raw_includes, dict) else {}
        return tweet, includes

    def _paginate_lists(
        self,
        path: str,
        count: int,
        *,
        require_user_context: bool = False,
    ) -> List[TwitterList]:
        if count <= 0:
            return []
        count = min(count, self._max_count)
        twitter_lists: List[TwitterList] = []
        seen_ids = set()
        next_token: Optional[str] = None

        while len(twitter_lists) < count:
            params: Dict[str, Any] = {
                "list.fields": _LIST_FIELDS,
                "expansions": "owner_id",
                "user.fields": _USER_FIELDS,
                "max_results": max(10, min(100, count - len(twitter_lists))),
            }
            if next_token:
                params["pagination_token"] = next_token

            data = self._api_request(
                "GET",
                path,
                params=params,
                require_user_context=require_user_context,
            )
            raw_items = data.get("data")
            items: List[Any] = raw_items if isinstance(raw_items, list) else []
            raw_includes = data.get("includes")
            includes: Dict[str, Any] = raw_includes if isinstance(raw_includes, dict) else {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                twitter_list = self._parse_list(item, includes)
                if twitter_list.id and twitter_list.id not in seen_ids:
                    seen_ids.add(twitter_list.id)
                    twitter_lists.append(twitter_list)
                    if len(twitter_lists) >= count:
                        break

            meta = data.get("meta") or {}
            next_token = str(meta.get("next_token") or "")
            if not next_token or len(twitter_lists) >= count:
                break
            self._sleep_between_pages()

        return twitter_lists[:count]

    def _wait_for_media(self, media_id: str, processing_info: Any) -> None:
        current_info: Dict[str, Any] = processing_info if isinstance(processing_info, dict) else {}
        while current_info:
            state = str(current_info.get("state") or "")
            if state in {"", "succeeded"}:
                return
            if state == "failed":
                raw_error = current_info.get("error")
                error: Dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
                detail = error.get("detail") or error.get("message") or "Media processing failed"
                raise MediaUploadError(str(detail))
            delay = max(int(current_info.get("check_after_secs") or 1), 1)
            time.sleep(delay)
            status = self._api_request(
                "GET",
                "/media/upload",
                params={"command": "STATUS", "media_id": media_id},
                require_user_context=True,
            )
            raw_data = status.get("data")
            data: Dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            raw_processing_info = data.get("processing_info")
            current_info = raw_processing_info if isinstance(raw_processing_info, dict) else {}

    def _paginate_tweets(
        self,
        path: str,
        count: int,
        params: Dict[str, Any],
        *,
        require_user_context: bool = False,
    ) -> List[Tweet]:
        if count <= 0:
            return []
        count = min(count, self._max_count)
        tweets: List[Tweet] = []
        seen_ids = set()
        next_token: Optional[str] = None

        while len(tweets) < count:
            page_params = dict(params)
            page_params["max_results"] = max(10, min(100, count - len(tweets)))
            if next_token:
                page_params["pagination_token"] = next_token

            data = self._api_request(
                "GET",
                path,
                params=page_params,
                require_user_context=require_user_context,
            )
            raw_page_items = data.get("data")
            page_items: List[Any] = raw_page_items if isinstance(raw_page_items, list) else []
            raw_includes = data.get("includes")
            includes: Dict[str, Any] = raw_includes if isinstance(raw_includes, dict) else {}
            for tweet in self._parse_tweets(page_items, includes):
                if tweet.id and tweet.id not in seen_ids:
                    seen_ids.add(tweet.id)
                    tweets.append(tweet)
                    if len(tweets) >= count:
                        break

            meta = data.get("meta") or {}
            next_token = str(meta.get("next_token") or "")
            if not next_token or len(tweets) >= count:
                break
            self._sleep_between_pages()

        return tweets[:count]

    def _paginate_users(self, path: str, count: int, params: Dict[str, Any]) -> List[UserProfile]:
        if count <= 0:
            return []
        count = min(count, self._max_count)
        users: List[UserProfile] = []
        seen_ids = set()
        next_token: Optional[str] = None

        while len(users) < count:
            page_params = dict(params)
            page_params["max_results"] = max(10, min(100, count - len(users)))
            if next_token:
                page_params["pagination_token"] = next_token

            data = self._api_request("GET", path, params=page_params)
            raw_items = data.get("data")
            items: List[Any] = raw_items if isinstance(raw_items, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                profile = self._parse_user(item)
                if profile.id and profile.id not in seen_ids:
                    seen_ids.add(profile.id)
                    users.append(profile)
                    if len(users) >= count:
                        break

            meta = data.get("meta") or {}
            next_token = str(meta.get("next_token") or "")
            if not next_token or len(users) >= count:
                break
            self._sleep_between_pages()

        return users[:count]

    def _sleep_between_pages(self) -> None:
        if self._request_delay > 0:
            time.sleep(self._request_delay)

    def _write_delay(self) -> None:
        if self._request_delay > 0:
            time.sleep(min(self._request_delay, 2.0))

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        require_user_context: bool = False,
    ) -> Dict[str, Any]:
        token = self._access_token if require_user_context else (self._access_token or self._bearer_token)
        if require_user_context and not token:
            raise AuthenticationError(
                "Official API user-context commands require TWITTER_API_ACCESS_TOKEN."
            )
        if not token:
            raise AuthenticationError(
                "Official API mode requires TWITTER_API_ACCESS_TOKEN or TWITTER_API_BEARER_TOKEN."
            )

        headers = {
            "Authorization": "Bearer %s" % token,
            "Accept": "application/json",
            "User-Agent": "twitter-cli",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        url = "%s%s" % (_API_BASE_URL, path)
        body = json.dumps(json_body) if json_body is not None else None
        session = _get_api_session()
        last_error: Optional[Exception] = None

        for attempt in range(max(self._max_retries, 1)):
            try:
                if method == "GET":
                    response = session.get(url, headers=headers, params=params, timeout=30)
                elif method == "POST":
                    response = session.post(url, headers=headers, params=params, data=body, timeout=30)
                elif method == "DELETE":
                    response = session.delete(url, headers=headers, params=params, timeout=30)
                else:
                    raise RuntimeError("Unsupported HTTP method: %s" % method)
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= max(self._max_retries, 1):
                    break
                time.sleep(self._retry_base_delay * (attempt + 1))
                continue

            payload = self._safe_json(response)
            if response.status_code < 400:
                if isinstance(payload, dict):
                    return payload
                raise NetworkError("Official API returned a non-JSON response")

            message = self._extract_error_message(payload, response.text)
            if response.status_code == 404:
                raise NotFoundError(message)
            if response.status_code == 429 and attempt + 1 < max(self._max_retries, 1):
                time.sleep(self._retry_base_delay * (attempt + 1))
                continue
            raise TwitterAPIError(response.status_code, message)

        raise NetworkError("Official API request failed: %s" % last_error)

    def _safe_json(self, response: Any) -> Any:
        try:
            return response.json()
        except Exception:
            return None

    def _extract_error_message(self, payload: Any, fallback_text: str) -> str:
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    detail = first.get("detail") or first.get("message") or first.get("title")
                    if detail:
                        return str(detail)
            title = payload.get("title")
            detail = payload.get("detail")
            if title and detail:
                return "%s: %s" % (title, detail)
            if detail:
                return str(detail)
            if title:
                return str(title)
        return fallback_text or "Official API request failed"

    def _parse_user(self, user: Dict[str, Any]) -> UserProfile:
        metrics = user.get("public_metrics") or {}
        entities = user.get("entities") or {}
        url_entity = entities.get("url") or {}
        urls = url_entity.get("urls") or []
        expanded_url = ""
        if urls and isinstance(urls[0], dict):
            expanded_url = str(urls[0].get("expanded_url") or urls[0].get("url") or "")

        return UserProfile(
            id=str(user.get("id") or ""),
            name=str(user.get("name") or ""),
            screen_name=str(user.get("username") or ""),
            bio=str(user.get("description") or ""),
            location=str(user.get("location") or ""),
            url=expanded_url,
            followers_count=int(metrics.get("followers_count") or 0),
            following_count=int(metrics.get("following_count") or 0),
            tweets_count=int(metrics.get("tweet_count") or 0),
            likes_count=int(metrics.get("like_count") or 0),
            verified=bool(user.get("verified", False)),
            profile_image_url=str(user.get("profile_image_url") or ""),
            created_at=str(user.get("created_at") or ""),
        )

    def _parse_tweets(self, data: List[Any], includes: Dict[str, Any]) -> List[Tweet]:
        user_map = {
            str(user.get("id")): user
            for user in includes.get("users", [])
            if isinstance(user, dict) and user.get("id")
        }
        media_map = {
            str(media.get("media_key")): media
            for media in includes.get("media", [])
            if isinstance(media, dict) and media.get("media_key")
        }
        tweet_map = {
            str(tweet.get("id")): tweet
            for tweet in includes.get("tweets", [])
            if isinstance(tweet, dict) and tweet.get("id")
        }
        return [
            self._parse_tweet(tweet, user_map, media_map, tweet_map)
            for tweet in data
            if isinstance(tweet, dict)
        ]

    def _parse_tweet(
        self,
        tweet: Dict[str, Any],
        user_map: Dict[str, Dict[str, Any]],
        media_map: Dict[str, Dict[str, Any]],
        tweet_map: Dict[str, Dict[str, Any]],
    ) -> Tweet:
        author_data = user_map.get(str(tweet.get("author_id")), {})
        metrics = tweet.get("public_metrics") or {}
        note_tweet = tweet.get("note_tweet") or {}
        attachments = tweet.get("attachments") or {}
        article = tweet.get("article") or {}
        entities = tweet.get("entities") or {}
        media_items: List[TweetMedia] = []
        seen_media_keys = set()
        for media_key in attachments.get("media_keys") or []:
            media = media_map.get(str(media_key))
            if not media:
                continue
            seen_media_keys.add(str(media_key))
            media_items.append(
                TweetMedia(
                    type=str(media.get("type") or ""),
                    url=str(media.get("url") or media.get("preview_image_url") or ""),
                    width=cast(Optional[int], media.get("width")),
                    height=cast(Optional[int], media.get("height")),
                )
            )

        urls: List[str] = []
        for item in entities.get("urls") or []:
            if not isinstance(item, dict):
                continue
            expanded = item.get("expanded_url") or item.get("unwound_url") or item.get("url")
            if expanded:
                urls.append(str(expanded))

        quoted_tweet = None
        is_retweet = False
        for ref in tweet.get("referenced_tweets") or []:
            if not isinstance(ref, dict):
                continue
            ref_type = str(ref.get("type") or "")
            ref_id = str(ref.get("id") or "")
            if ref_type == "quoted" and ref_id in tweet_map:
                quoted_tweet = self._parse_tweet(tweet_map[ref_id], user_map, media_map, {})
            if ref_type == "retweeted":
                is_retweet = True

        article_title = self._extract_article_title(article)
        article_text = self._extract_article_text(article)
        for media_key in self._extract_article_media_keys(article):
            if media_key in seen_media_keys:
                continue
            media = media_map.get(media_key)
            if not media:
                continue
            seen_media_keys.add(media_key)
            media_items.append(
                TweetMedia(
                    type=str(media.get("type") or ""),
                    url=str(media.get("url") or media.get("preview_image_url") or ""),
                    width=cast(Optional[int], media.get("width")),
                    height=cast(Optional[int], media.get("height")),
                )
            )
        text = str(note_tweet.get("text") or tweet.get("text") or "")

        return Tweet(
            id=str(tweet.get("id") or ""),
            text=text,
            author=Author(
                id=str(author_data.get("id") or tweet.get("author_id") or ""),
                name=str(author_data.get("name") or ""),
                screen_name=str(author_data.get("username") or ""),
                profile_image_url=str(author_data.get("profile_image_url") or ""),
                verified=bool(author_data.get("verified", False)),
            ),
            metrics=Metrics(
                likes=int(metrics.get("like_count") or 0),
                retweets=int(metrics.get("retweet_count") or 0),
                replies=int(metrics.get("reply_count") or 0),
                quotes=int(metrics.get("quote_count") or 0),
                views=int(metrics.get("impression_count") or 0),
                bookmarks=int(metrics.get("bookmark_count") or 0),
            ),
            created_at=str(tweet.get("created_at") or ""),
            media=media_items,
            urls=urls,
            is_retweet=is_retweet,
            lang=str(tweet.get("lang") or ""),
            quoted_tweet=quoted_tweet,
            article_title=article_title,
            article_text=article_text,
        )

    def _parse_list(self, data: Dict[str, Any], includes: Dict[str, Any]) -> TwitterList:
        raw_owners = includes.get("users")
        owners: List[Any] = raw_owners if isinstance(raw_owners, list) else []
        owner_map = {
            str(owner.get("id")): owner
            for owner in owners
            if isinstance(owner, dict) and owner.get("id")
        }
        owner = owner_map.get(str(data.get("owner_id")), {})
        return TwitterList(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            owner_screen_name=str(owner.get("username") or ""),
            description=str(data.get("description") or ""),
            follower_count=int(data.get("follower_count") or 0),
            member_count=int(data.get("member_count") or 0),
            private=bool(data.get("private", False)),
            created_at=str(data.get("created_at") or ""),
        )

    def _extract_article_title(self, article: Any) -> Optional[str]:
        return self._find_nested_text(article, ["title", "headline", "display_title", "name"])

    def _extract_article_text(self, article: Any) -> Optional[str]:
        direct = self._find_nested_text(
            article,
            ["text", "plain_text", "body", "content", "description", "summary", "markdown"],
        )
        if direct:
            return direct
        parts = self._collect_article_text_parts(article)
        if parts:
            return "\n\n".join(parts)
        return None

    def _extract_article_media_keys(self, article: Any) -> List[str]:
        keys: List[str] = []
        self._collect_article_media_keys(article, keys)
        deduped: List[str] = []
        seen = set()
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _find_nested_text(self, value: Any, candidate_keys: List[str]) -> Optional[str]:
        if isinstance(value, dict):
            for key in candidate_keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for nested in value.values():
                found = self._find_nested_text(nested, candidate_keys)
                if found:
                    return found
            return None
        if isinstance(value, list):
            for item in value:
                found = self._find_nested_text(item, candidate_keys)
                if found:
                    return found
        return None

    def _collect_article_text_parts(self, value: Any) -> List[str]:
        parts: List[str] = []
        if isinstance(value, dict):
            for key in ("text", "plain_text", "content", "description", "summary", "markdown"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    parts.append(candidate.strip())
            for nested_key in ("blocks", "items", "paragraphs", "sections", "children", "content"):
                nested = value.get(nested_key)
                parts.extend(self._collect_article_text_parts(nested))
            return self._dedupe_text_parts(parts)
        if isinstance(value, list):
            for item in value:
                parts.extend(self._collect_article_text_parts(item))
        return self._dedupe_text_parts(parts)

    def _collect_article_media_keys(self, value: Any, keys: List[str]) -> None:
        if isinstance(value, dict):
            media_key = value.get("media_key")
            if isinstance(media_key, str):
                keys.append(media_key)
            cover_media_key = value.get("cover_media_key")
            if isinstance(cover_media_key, str):
                keys.append(cover_media_key)
            media_keys = value.get("media_keys")
            if isinstance(media_keys, list):
                for media_key_item in media_keys:
                    if isinstance(media_key_item, str):
                        keys.append(media_key_item)
            for nested in value.values():
                self._collect_article_media_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                self._collect_article_media_keys(item, keys)

    def _dedupe_text_parts(self, parts: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for part in parts:
            normalized = part.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped
