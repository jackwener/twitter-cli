"""Cookie authentication for Twitter/X.

Supports:
1. Environment variables: TWITTER_AUTH_TOKEN + TWITTER_CT0
2. Auto-extract from browser via browser-cookie3 (subprocess)
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Dict, Optional

from .constants import BEARER_TOKEN, USER_AGENT

logger = logging.getLogger(__name__)




def load_from_env() -> Optional[Dict[str, str]]:
    """Load cookies from environment variables."""
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = os.environ.get("TWITTER_CT0", "")
    if auth_token and ct0:
        return {"auth_token": auth_token, "ct0": ct0}
    return None


def verify_cookies(auth_token, ct0):
    # type: (str, str) -> Dict[str, Any]
    """Verify cookies by calling a Twitter API endpoint.

    Tries multiple endpoints. Only raises on clear auth failures (401/403).
    For other errors (404, network), returns empty dict (proceed without verification).
    """
    # Endpoints to try, in order of preference
    urls = [
        "https://api.x.com/1.1/account/verify_credentials.json",
        "https://x.com/i/api/1.1/account/settings.json",
    ]

    headers = {
        "Authorization": "Bearer %s" % BEARER_TOKEN,
        "Cookie": "auth_token=%s; ct0=%s" % (auth_token, ct0),
        "X-Csrf-Token": ct0,
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
        "User-Agent": USER_AGENT,
    }

    for url in urls:
        req = urllib.request.Request(url)
        for k, v in headers.items():
            req.add_header(k, v)

        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"screen_name": data.get("screen_name", "")}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    "Cookie expired or invalid (HTTP %d). Please re-login to x.com in your browser." % e.code
                )
            # 404 or other — try next endpoint
            logger.debug("Verification endpoint %s returned HTTP %d, trying next...", url, e.code)
            continue
        except Exception as e:
            logger.debug("Verification endpoint %s failed: %s", url, e)
            continue

    # All endpoints failed with non-auth errors — proceed without verification
    logger.info("Cookie verification skipped (no working endpoint), will verify on first API call")
    return {}


def extract_from_browser() -> Optional[Dict[str, str]]:
    """Auto-extract cookies from local browser using browser-cookie3.

    Tries browsers in order: Chrome -> Edge -> Firefox -> Brave.
    Runs in a subprocess to avoid SQLite database lock issues when the
    browser is running.
    """
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
    for cookie in jar:
        domain = cookie.domain or ""
        if domain.endswith(".x.com") or domain.endswith(".twitter.com") or domain in ("x.com", "twitter.com", ".x.com", ".twitter.com"):
            if cookie.name == "auth_token":
                result["auth_token"] = cookie.value
            elif cookie.name == "ct0":
                result["ct0"] = cookie.value
    if "auth_token" in result and "ct0" in result:
        result["browser"] = name
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
        logger.info("Found cookies in %s", data.get("browser", "unknown"))
        return {"auth_token": data["auth_token"], "ct0": data["ct0"]}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, FileNotFoundError):
        return None


def get_cookies() -> Dict[str, str]:
    """Get Twitter cookies. Priority: env vars -> browser extraction (Chrome/Edge/Firefox/Brave).

    Raises RuntimeError if no cookies found.
    """
    cookies = None  # type: Optional[Dict[str, str]]

    # 1. Try environment variables
    cookies = load_from_env()
    if cookies:
        logger.info("Loaded cookies from environment variables")

    # 2. Try browser extraction (auto-detect)
    if not cookies:
        cookies = extract_from_browser()

    if not cookies:
        raise RuntimeError(
            "No Twitter cookies found.\n"
            "Option 1: Set TWITTER_AUTH_TOKEN and TWITTER_CT0 environment variables\n"
            "Option 2: Make sure you are logged into x.com in your browser (Chrome/Edge/Firefox/Brave)"
        )

    # Verify only for explicit auth failures; transient endpoint issues are tolerated.
    verify_cookies(cookies["auth_token"], cookies["ct0"])
    return cookies
