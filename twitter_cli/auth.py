"""Cookie authentication for Twitter/X.

Supports:
1. Environment variables: TWITTER_AUTH_TOKEN + TWITTER_CT0
2. Auto-extract from browser via browser-cookie3
   Extracts ALL Twitter cookies for full browser-like fingerprint.
   Prefers in-process extraction (required on macOS for Keychain access),
   falls back to subprocess if in-process fails (e.g. SQLite lock).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Dict, Optional

from .constants import BEARER_TOKEN, get_user_agent

logger = logging.getLogger(__name__)

# Domains to match for Twitter cookies
_TWITTER_DOMAINS = {"x.com", "twitter.com", ".x.com", ".twitter.com"}


def _is_twitter_domain(domain):
    # type: (str) -> bool
    return domain in _TWITTER_DOMAINS or domain.endswith(".x.com") or domain.endswith(".twitter.com")


def load_from_env() -> Optional[Dict[str, str]]:
    """Load cookies from environment variables."""
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = os.environ.get("TWITTER_CT0", "")
    if auth_token and ct0:
        return {"auth_token": auth_token, "ct0": ct0}
    return None


def verify_cookies(auth_token, ct0, cookie_string=None):
    # type: (str, str, Optional[str]) -> Dict[str, Any]
    """Verify cookies by calling a Twitter API endpoint.

    Uses curl_cffi for proper TLS fingerprint.
    Tries multiple endpoints. Only raises on clear auth failures (401/403).
    For other errors (404, network), returns empty dict (proceed without verification).
    """
    from .client import _get_cffi_session

    urls = [
        "https://api.x.com/1.1/account/verify_credentials.json",
        "https://x.com/i/api/1.1/account/settings.json",
    ]

    # Use full cookie string if available, otherwise minimal
    cookie_header = cookie_string or "auth_token=%s; ct0=%s" % (auth_token, ct0)

    headers = {
        "Authorization": "Bearer %s" % BEARER_TOKEN,
        "Cookie": cookie_header,
        "X-Csrf-Token": ct0,
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
        "User-Agent": get_user_agent(),
    }

    # Reuse the shared curl_cffi session for consistent TLS fingerprint
    session = _get_cffi_session()

    for url in urls:
        try:
            resp = session.get(url, headers=headers, timeout=5)
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    "Cookie expired or invalid (HTTP %d). Please re-login to x.com in your browser." % resp.status_code
                )
            if resp.status_code == 200:
                data = resp.json()
                return {"screen_name": data.get("screen_name", "")}
            logger.debug("Verification endpoint %s returned HTTP %d, trying next...", url, resp.status_code)
            continue
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug("Verification endpoint %s failed: %s", url, e)
            continue

    # All endpoints failed with non-auth errors — proceed without verification
    logger.info("Cookie verification skipped (no working endpoint), will verify on first API call")
    return {}


def _extract_cookies_from_jar(jar):
    # type: (Any) -> Optional[Dict[str, str]]
    """Extract Twitter cookies from a cookie jar."""
    result = {}  # type: Dict[str, str]
    all_cookies = {}  # type: Dict[str, str]
    for cookie in jar:
        domain = cookie.domain or ""
        if _is_twitter_domain(domain):
            if cookie.name == "auth_token":
                result["auth_token"] = cookie.value
            elif cookie.name == "ct0":
                result["ct0"] = cookie.value
            if cookie.name and cookie.value:
                all_cookies[cookie.name] = cookie.value
    if "auth_token" in result and "ct0" in result:
        cookies = {"auth_token": result["auth_token"], "ct0": result["ct0"]}
        if all_cookies:
            cookies["cookie_string"] = "; ".join("%s=%s" % (k, v) for k, v in all_cookies.items())
            logger.info("Extracted %d total cookies for full browser fingerprint", len(all_cookies))
        return cookies
    return None


def _extract_in_process():
    # type: () -> Optional[Dict[str, str]]
    """Extract cookies in the main process (required on macOS for Keychain access).

    On macOS, Chrome encrypts cookies using a key stored in the system Keychain.
    Child processes do NOT inherit the parent's Keychain authorization, so
    browser_cookie3 must run in the main process to decrypt cookies.
    """
    try:
        import browser_cookie3
    except ImportError:
        logger.debug("browser_cookie3 not installed, skipping in-process extraction")
        return None

    browsers = [
        ("chrome", browser_cookie3.chrome),
        ("edge", browser_cookie3.edge),
        ("firefox", browser_cookie3.firefox),
        ("brave", browser_cookie3.brave),
    ]

    for name, fn in browsers:
        try:
            jar = fn()
        except Exception as e:
            logger.debug("%s in-process extraction failed: %s", name, e)
            continue
        cookies = _extract_cookies_from_jar(jar)
        if cookies:
            logger.info("Found cookies in %s (in-process)", name)
            return cookies
    return None


def _extract_via_subprocess():
    # type: () -> Optional[Dict[str, str]]
    """Extract cookies via subprocess (fallback if in-process fails, e.g. SQLite lock)."""
    extract_script = '''
import json, sys
try:
    import browser_cookie3
except ImportError:
    print(json.dumps({"error": "browser-cookie3 not installed"}))
    sys.exit(1)

browsers = [
    ("chrome", browser_cookie3.chrome),
    ("edge", browser_cookie3.edge),
    ("firefox", browser_cookie3.firefox),
    ("brave", browser_cookie3.brave),
]

for name, fn in browsers:
    try:
        jar = fn()
    except Exception:
        continue
    result = {}
    all_cookies = {}
    for cookie in jar:
        domain = cookie.domain or ""
        if domain.endswith(".x.com") or domain.endswith(".twitter.com") or domain in ("x.com", "twitter.com", ".x.com", ".twitter.com"):
            if cookie.name == "auth_token":
                result["auth_token"] = cookie.value
            elif cookie.name == "ct0":
                result["ct0"] = cookie.value
            if cookie.name and cookie.value:
                all_cookies[cookie.name] = cookie.value
    if "auth_token" in result and "ct0" in result:
        result["browser"] = name
        result["all_cookies"] = all_cookies
        print(json.dumps(result))
        sys.exit(0)

print(json.dumps({"error": "No Twitter cookies found in any browser. Make sure you are logged into x.com."}))
sys.exit(1)
'''

    try:
        result = subprocess.run(
            [sys.executable, "-c", extract_script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip()
        if not output:
            stderr = result.stderr.strip()
            if stderr:
                logger.debug("Cookie extraction stderr from current env: %s", stderr[:300])
                # Maybe browser-cookie3 not installed, try with uv.
                result2 = subprocess.run(
                    ["uv", "run", "--with", "browser-cookie3", "python3", "-c", extract_script],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = result2.stdout.strip()
                if not output:
                    logger.debug("Cookie extraction stderr from uv fallback: %s", result2.stderr.strip()[:300])
                    return None

        data = json.loads(output)
        if "error" in data:
            return None
        logger.info("Found cookies in %s (subprocess)", data.get("browser", "unknown"))

        # Build full cookie string from all extracted cookies
        cookies = {"auth_token": data["auth_token"], "ct0": data["ct0"]}
        all_cookies = data.get("all_cookies", {})
        if all_cookies:
            cookie_str = "; ".join("%s=%s" % (k, v) for k, v in all_cookies.items())
            cookies["cookie_string"] = cookie_str
            logger.info("Extracted %d total cookies for full browser fingerprint", len(all_cookies))
        return cookies
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, FileNotFoundError):
        return None


def extract_from_browser() -> Optional[Dict[str, str]]:
    """Auto-extract ALL Twitter cookies from local browser using browser-cookie3.

    Strategy:
    1. Try in-process first (required on macOS for Keychain access)
    2. Fall back to subprocess (handles SQLite lock when browser is running)
    """
    # 1. In-process (works on macOS, may fail with SQLite lock)
    cookies = _extract_in_process()
    if cookies:
        return cookies

    # 2. Subprocess fallback (handles SQLite lock, but fails on macOS Keychain)
    logger.debug("In-process extraction failed, trying subprocess fallback")
    return _extract_via_subprocess()


def get_cookies() -> Dict[str, str]:
    """Get Twitter cookies. Priority: env vars -> cache file -> browser extraction.

    Raises RuntimeError if no cookies found.
    """
    cookies = None  # type: Optional[Dict[str, str]]

    # 1. Try environment variables
    cookies = load_from_env()
    if cookies:
        logger.info("Loaded cookies from environment variables")

    # 2. Try cached cookies (file cache with TTL)
    if not cookies:
        cookies = _load_cookie_cache()
        if cookies:
            logger.info("Loaded cookies from cache")

    # 3. Try browser extraction (auto-detect)
    if not cookies:
        cookies = extract_from_browser()
        if cookies:
            _save_cookie_cache(cookies)

    if not cookies:
        raise RuntimeError(
            "No Twitter cookies found.\n"
            "Option 1: Set TWITTER_AUTH_TOKEN and TWITTER_CT0 environment variables\n"
            "Option 2: Make sure you are logged into x.com in your browser (Chrome/Edge/Firefox/Brave)"
        )

    # Verify only for explicit auth failures; transient endpoint issues are tolerated.
    try:
        verify_cookies(cookies["auth_token"], cookies["ct0"], cookies.get("cookie_string"))
    except RuntimeError:
        # Auth failure — invalidate cache and re-extract from browser
        logger.info("Cookie verification failed, invalidating cache and re-extracting")
        invalidate_cookie_cache()
        fresh_cookies = extract_from_browser()
        if fresh_cookies:
            _save_cookie_cache(fresh_cookies)
            # Verify fresh cookies — if this also fails, let it raise
            verify_cookies(fresh_cookies["auth_token"], fresh_cookies["ct0"], fresh_cookies.get("cookie_string"))
            return fresh_cookies
        raise
    return cookies


# ── Cookie file cache ───────────────────────────────────────────────────

_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "twitter-cli")
_CACHE_FILE = os.path.join(_CACHE_DIR, "cookies.json")
_CACHE_TTL_SECONDS = 24 * 3600  # 24 hours


def _load_cookie_cache():
    # type: () -> Optional[Dict[str, str]]
    """Load cookies from file cache if within TTL."""
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        import time as _time
        mtime = os.path.getmtime(_CACHE_FILE)
        if _time.time() - mtime > _CACHE_TTL_SECONDS:
            logger.debug("Cookie cache expired (>%ds)", _CACHE_TTL_SECONDS)
            return None
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "auth_token" in data and "ct0" in data:
            return data
    except Exception as exc:
        logger.debug("Failed to load cookie cache: %s", exc)
    return None


def _save_cookie_cache(cookies):
    # type: (Dict[str, str]) -> None
    """Save cookies to file cache."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False)
        # Restrict permissions — cookies are sensitive
        os.chmod(_CACHE_FILE, 0o600)
        logger.info("Saved cookies to cache (%s)", _CACHE_FILE)
    except Exception as exc:
        logger.debug("Failed to save cookie cache: %s", exc)


def invalidate_cookie_cache():
    # type: () -> None
    """Delete the cookie cache file."""
    try:
        if os.path.exists(_CACHE_FILE):
            os.remove(_CACHE_FILE)
            logger.info("Cookie cache invalidated")
    except Exception as exc:
        logger.debug("Failed to invalidate cookie cache: %s", exc)
