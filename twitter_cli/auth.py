"""Cookie authentication for Twitter/X.

Supports:
1. Environment variables: TWITTER_AUTH_TOKEN + TWITTER_CT0
2. Auto-extract from browser via browser-cookie3
   Extracts ALL Twitter cookies for full browser-like fingerprint.
   Prefers in-process extraction (required on macOS for Keychain access),
   falls back to subprocess if in-process fails (e.g. SQLite lock).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from .constants import BEARER_TOKEN, get_user_agent

logger = logging.getLogger(__name__)

# Domains to match for Twitter cookies
_TWITTER_DOMAINS = {"x.com", "twitter.com", ".x.com", ".twitter.com"}


def _is_twitter_domain(domain: str) -> bool:
    return domain in _TWITTER_DOMAINS or domain.endswith(".x.com") or domain.endswith(".twitter.com")


# ---------------------------------------------------------------------------
# Keychain / environment diagnostics
# ---------------------------------------------------------------------------

_KEYCHAIN_ERROR_KEYWORDS = (
    "key for cookie decryption",
    "safe storage",
    "keychain",
    "secretstorage",
)


def _diagnose_keychain_issues(diagnostics: List[str]) -> Optional[str]:
    """Analyse extraction diagnostics for Keychain permission issues.

    Returns a user-friendly hint string, or None.
    """
    lowered = " ".join(diagnostics).lower()
    if not any(kw in lowered for kw in _KEYCHAIN_ERROR_KEYWORDS):
        return None

    is_ssh = bool(os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION"))

    if sys.platform == "darwin":
        if is_ssh:
            return (
                "macOS Keychain is locked (SSH session detected).\n"
                "  Fix: security unlock-keychain ~/Library/Keychains/login.keychain-db\n"
                "  Then retry the command."
            )
        return (
            "macOS Keychain permission denied — your terminal is not authorized to read browser cookie encryption keys.\n"
            "  Fix: Open Keychain Access → search for \"<Browser> Safe Storage\" → Access Control → add your Terminal app.\n"
            "  Or click \"Always Allow\" when the Keychain authorization popup appears."
        )
    if sys.platform == "win32":
        return (
            "Windows DPAPI cookie decryption failed.\n"
            "  Possible causes:\n"
            "  1. Chrome is running (locks the cookie database). Try closing Chrome first.\n"
            "  2. browser_cookie3 may not support the latest Chrome cookie encryption format.\n"
            "  3. If Chrome was running, admin privileges may be required for VSS shadowcopy.\n"
            "  Workaround: Set TWITTER_AUTH_TOKEN and TWITTER_CT0 environment variables manually."
        )
    # Linux: gnome-keyring / SecretStorage issues
    return (
        "System keyring access failed — the cookie encryption key could not be retrieved.\n"
        "  If running headless or via SSH, ensure your keyring daemon is unlocked."
    )


def load_from_env() -> Optional[Dict[str, str]]:
    """Load cookies from environment variables."""
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = os.environ.get("TWITTER_CT0", "")
    if auth_token and ct0:
        return {"auth_token": auth_token, "ct0": ct0}
    if auth_token or ct0:
        logger.debug(
            "Environment cookies incomplete: auth_token=%s ct0=%s",
            bool(auth_token),
            bool(ct0),
        )
    return None


def verify_cookies(auth_token: str, ct0: str, cookie_string: Optional[str] = None) -> Dict[str, Any]:
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
    attempts = []

    logger.debug(
        "Verifying Twitter cookies with %s cookie header",
        "full forwarded" if cookie_string else "minimal",
    )

    for url in urls:
        endpoint = url.split("/")[-1]
        try:
            resp = session.get(url, headers=headers, timeout=5)
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    "Cookie expired or invalid (HTTP %d). Please re-login to x.com in your browser." % resp.status_code
                )
            if resp.status_code == 200:
                data = resp.json()
                attempts.append("%s=200" % endpoint)
                logger.debug("Cookie verification succeeded via %s", endpoint)
                return {"screen_name": data.get("screen_name", "")}
            attempts.append("%s=%d" % (endpoint, resp.status_code))
            logger.debug("Verification endpoint %s returned HTTP %d, trying next...", url, resp.status_code)
            continue
        except RuntimeError:
            raise
        except Exception as e:
            attempts.append("%s=%s" % (endpoint, type(e).__name__))
            logger.debug("Verification endpoint %s failed: %s", url, e)
            continue

    # All endpoints failed with non-auth errors — proceed without verification
    logger.info(
        "Cookie verification skipped (attempts: %s), will verify on first API call",
        ", ".join(attempts) if attempts else "none",
    )
    return {}


def _extract_cookies_from_jar(jar: Any, source: str = "unknown") -> Optional[Dict[str, str]]:
    """Extract Twitter cookies from a cookie jar."""
    result: Dict[str, str] = {}
    all_cookies: Dict[str, str] = {}
    twitter_cookie_count = 0
    for cookie in jar:
        domain = cookie.domain or ""
        if _is_twitter_domain(domain):
            twitter_cookie_count += 1
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
    logger.debug(
        "Cookie jar %s did not contain usable Twitter auth cookies (twitter_cookies=%d, auth_token=%s, ct0=%s)",
        source,
        twitter_cookie_count,
        "auth_token" in result,
        "ct0" in result,
    )
    return None


# Base directories for Chromium-based browsers, keyed by browser name.
# Each entry maps to the directory under the platform-specific app data root.
_CHROMIUM_BASE_DIRS: Dict[str, str] = {
    "chrome": os.path.join("Google", "Chrome"),
    "arc": os.path.join("Arc", "User Data"),
    "edge": "Microsoft Edge",
    "brave": os.path.join("BraveSoftware", "Brave-Browser"),
}

# Default browser order for cookie extraction
_DEFAULT_BROWSER_ORDER = ["arc", "chrome", "edge", "firefox", "brave"]


def _get_browser_order() -> List[str]:
    """Return browser extraction order, respecting TWITTER_BROWSER env var."""
    env = os.environ.get("TWITTER_BROWSER", "").strip().lower()
    if not env:
        return _DEFAULT_BROWSER_ORDER
    if env not in {"arc", "chrome", "edge", "firefox", "brave"}:
        logger.warning("TWITTER_BROWSER='%s' is invalid, using default order", env)
        return _DEFAULT_BROWSER_ORDER
    return [env] + [b for b in _DEFAULT_BROWSER_ORDER if b != env]


def _iter_chrome_cookie_files(browser_name: str) -> List[str]:
    """Return cookie file paths for all Chrome profiles.

    If TWITTER_CHROME_PROFILE is set, only that profile is returned.
    Otherwise yields Default first, then Profile 1, Profile 2, ... sorted.
    """
    base_dir = _CHROMIUM_BASE_DIRS.get(browser_name)
    if base_dir is None:
        return []

    if sys.platform == "darwin":
        root = os.path.join(os.path.expanduser("~"), "Library", "Application Support", base_dir)
    elif sys.platform == "win32":
        if browser_name == "edge":
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")
        else:
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), base_dir)
    else:
        if browser_name == "edge":
            root = os.path.join(os.path.expanduser("~"), ".config", "microsoft-edge")
        else:
            root = os.path.join(os.path.expanduser("~"), ".config", base_dir)

    if not os.path.isdir(root):
        return []

    # If user explicitly specifies a profile, only use that one
    env_profile = os.environ.get("TWITTER_CHROME_PROFILE", "").strip()
    if env_profile:
        cookie_path = os.path.join(root, env_profile, "Cookies")
        if os.path.exists(cookie_path):
            logger.debug("Using specified Chrome profile: %s", env_profile)
            return [cookie_path]
        logger.warning("TWITTER_CHROME_PROFILE='%s' not found at %s", env_profile, cookie_path)
        return []

    # Auto-discover: Default first, then Profile N sorted
    paths: List[str] = []
    default_cookies = os.path.join(root, "Default", "Cookies")
    if os.path.exists(default_cookies):
        paths.append(default_cookies)

    profile_dirs = sorted(glob.glob(os.path.join(root, "Profile *")))
    for profile_dir in profile_dirs:
        cookie_file = os.path.join(profile_dir, "Cookies")
        if os.path.exists(cookie_file):
            paths.append(cookie_file)

    return paths


def _extract_in_process() -> Tuple[Optional[Dict[str, str]], List[str]]:
    """Extract cookies in the main process (required on macOS for Keychain access).

    On macOS, Chrome encrypts cookies using a key stored in the system Keychain.
    Child processes do NOT inherit the parent's Keychain authorization, so
    browser_cookie3 must run in the main process to decrypt cookies.

    For Chromium-based browsers, iterates all profiles to find Twitter cookies.

    Returns (cookies_dict | None, diagnostics_list).
    """
    try:
        import browser_cookie3
    except ImportError:
        logger.debug("browser_cookie3 not installed, skipping in-process extraction")
        return None, ["browser-cookie3 not installed"]

    browser_fns = {
        "arc": browser_cookie3.arc,
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
    }
    attempts: List[str] = []
    diagnostics: List[str] = []

    for name in _get_browser_order():
        fn = browser_fns[name]
        if name in _CHROMIUM_BASE_DIRS:
            # Chromium-based: iterate all profiles
            cookie_files = _iter_chrome_cookie_files(name)
            if not cookie_files:
                # No profile dirs found — try the default (no cookie_file arg)
                try:
                    jar = fn()
                except Exception as e:
                    logger.debug("%s in-process extraction failed: %s", name, e)
                    attempts.append("%s=%s" % (name, type(e).__name__))
                    diagnostics.append("%s: %s" % (name, e))
                    continue
                cookies = _extract_cookies_from_jar(jar, source="%s(in-process)" % name)
                if cookies:
                    logger.info("Found cookies in %s (in-process, default)", name)
                    return cookies, diagnostics
                attempts.append("%s=no-cookies" % name)
                continue

            for cookie_file in cookie_files:
                profile_name = os.path.basename(os.path.dirname(cookie_file))
                try:
                    jar = fn(cookie_file=cookie_file)
                except Exception as e:
                    logger.debug("%s[%s] in-process extraction failed: %s", name, profile_name, e)
                    attempts.append("%s[%s]=%s" % (name, profile_name, type(e).__name__))
                    diagnostics.append("%s[%s]: %s" % (name, profile_name, e))
                    continue
                cookies = _extract_cookies_from_jar(jar, source="%s[%s](in-process)" % (name, profile_name))
                if cookies:
                    logger.info("Found cookies in %s profile '%s' (in-process)", name, profile_name)
                    return cookies, diagnostics
                attempts.append("%s[%s]=no-cookies" % (name, profile_name))
        else:
            # Non-Chromium (Firefox): use default behavior
            try:
                jar = fn()
            except Exception as e:
                logger.debug("%s in-process extraction failed: %s", name, e)
                attempts.append("%s=%s" % (name, type(e).__name__))
                diagnostics.append("%s: %s" % (name, e))
                continue
            cookies = _extract_cookies_from_jar(jar, source="%s(in-process)" % name)
            if cookies:
                logger.info("Found cookies in %s (in-process)", name)
                return cookies, diagnostics
            attempts.append("%s=no-cookies" % name)

    if attempts:
        logger.debug("In-process extraction attempts: %s", ", ".join(attempts))
    return None, diagnostics


def _extract_via_subprocess() -> Tuple[Optional[Dict[str, str]], List[str]]:
    """Extract cookies via subprocess (fallback if in-process fails, e.g. SQLite lock).

    Returns (cookies_dict | None, diagnostics_list).
    """
    extract_script = '''
import glob, json, os, sys
try:
    import browser_cookie3
except ImportError:
    print(json.dumps({"error": "browser-cookie3 not installed"}))
    sys.exit(1)

CHROMIUM_BASE_DIRS = {
    "chrome": os.path.join("Google", "Chrome"),
    "arc": os.path.join("Arc", "User Data"),
    "edge": os.path.join("Microsoft Edge"),
    "brave": os.path.join("BraveSoftware", "Brave-Browser"),
}

def iter_cookie_files(browser_name):
    base_dir = CHROMIUM_BASE_DIRS.get(browser_name)
    if base_dir is None:
        return []
    if sys.platform == "darwin":
        root = os.path.join(os.path.expanduser("~"), "Library", "Application Support", base_dir)
    elif sys.platform == "win32":
        if browser_name == "edge":
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")
        else:
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), base_dir)
    else:
        if browser_name == "edge":
            root = os.path.join(os.path.expanduser("~"), ".config", "microsoft-edge")
        else:
            root = os.path.join(os.path.expanduser("~"), ".config", base_dir)
    if not os.path.isdir(root):
        return []
    env_profile = os.environ.get("TWITTER_CHROME_PROFILE", "").strip()
    if env_profile:
        p = os.path.join(root, env_profile, "Cookies")
        return [p] if os.path.exists(p) else []
    paths = []
    d = os.path.join(root, "Default", "Cookies")
    if os.path.exists(d):
        paths.append(d)
    for pd in sorted(glob.glob(os.path.join(root, "Profile *"))):
        cf = os.path.join(pd, "Cookies")
        if os.path.exists(cf):
            paths.append(cf)
    return paths

def extract_from_jar(jar, name, profile=""):
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
        if profile:
            result["profile"] = profile
        result["all_cookies"] = all_cookies
        return result
    return None

DEFAULT_ORDER = ["arc", "chrome", "edge", "firefox", "brave"]
env_browser = os.environ.get("TWITTER_BROWSER", "").strip().lower()
if env_browser in {"arc", "chrome", "edge", "firefox", "brave"}:
    browser_order = [env_browser] + [b for b in DEFAULT_ORDER if b != env_browser]
else:
    browser_order = DEFAULT_ORDER
browser_fns = {
    "arc": browser_cookie3.arc,
    "chrome": browser_cookie3.chrome,
    "edge": browser_cookie3.edge,
    "firefox": browser_cookie3.firefox,
    "brave": browser_cookie3.brave,
}
attempts = []

for name in browser_order:
    fn = browser_fns[name]
    if name in CHROMIUM_BASE_DIRS:
        cookie_files = iter_cookie_files(name)
        if not cookie_files:
            try:
                jar = fn()
            except Exception as exc:
                attempts.append(f"{name}={type(exc).__name__}: {exc}")
                continue
            r = extract_from_jar(jar, name)
            if r:
                print(json.dumps(r))
                sys.exit(0)
            attempts.append(f"{name}=no-cookies")
            continue
        for cf in cookie_files:
            pname = os.path.basename(os.path.dirname(cf))
            try:
                jar = fn(cookie_file=cf)
            except Exception as exc:
                attempts.append(f"{name}[{pname}]={type(exc).__name__}: {exc}")
                continue
            r = extract_from_jar(jar, name, pname)
            if r:
                print(json.dumps(r))
                sys.exit(0)
            attempts.append(f"{name}[{pname}]=no-cookies")
    else:
        try:
            jar = fn()
        except Exception as exc:
            attempts.append(f"{name}={type(exc).__name__}: {exc}")
            continue
        r = extract_from_jar(jar, name)
        if r:
            print(json.dumps(r))
            sys.exit(0)
        attempts.append(f"{name}=no-cookies")

print(json.dumps({
    "error": "No Twitter cookies found in any browser. Make sure you are logged into x.com.",
    "attempts": attempts,
}))
sys.exit(1)
'''

    diagnostics: List[str] = []

    def _run_extract_command(
        cmd: list[str],
        timeout: int,
        label: str,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.debug("Cookie extraction %s timed out", label)
            return None, False
        except FileNotFoundError as exc:
            logger.debug("Cookie extraction %s launcher missing: %s", label, exc)
            return None, False

        output = result.stdout.strip()
        stderr = result.stderr.strip()
        if stderr:
            logger.debug("Cookie extraction stderr from %s: %s", label, stderr[:300])
        if not output:
            logger.debug("Cookie extraction from %s produced no stdout", label)
            return None, True

        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            logger.debug("Cookie extraction %s returned invalid JSON: %s", label, exc)
            return None, True

        if "error" in data:
            attempts = data.get("attempts") or []
            if attempts:
                logger.debug("Subprocess extraction attempts (%s): %s", label, ", ".join(str(item) for item in attempts))
                diagnostics.extend(str(item) for item in attempts)
            retryable = data.get("error") == "browser-cookie3 not installed"
            return None, retryable

        return data, False

    try:
        data, retry_with_uv = _run_extract_command(
            [sys.executable, "-c", extract_script],
            timeout=15,
            label="current env",
        )
        if data is None and retry_with_uv:
            data, _ = _run_extract_command(
                ["uv", "run", "--with", "browser-cookie3", "python", "-c", extract_script],
                timeout=30,
                label="uv fallback",
            )

        if data is None:
            return None, diagnostics
        logger.info("Found cookies in %s (subprocess)", data.get("browser", "unknown"))

        # Build full cookie string from all extracted cookies
        cookies: Dict[str, str] = {"auth_token": data["auth_token"], "ct0": data["ct0"]}
        all_cookies = data.get("all_cookies", {})
        if all_cookies:
            cookie_str = "; ".join("%s=%s" % (k, v) for k, v in all_cookies.items())
            cookies["cookie_string"] = cookie_str
            logger.info("Extracted %d total cookies for full browser fingerprint", len(all_cookies))
        return cookies, diagnostics
    except KeyError as exc:
        logger.debug("Cookie extraction subprocess returned incomplete payload: %s", exc)
        return None, diagnostics


def extract_from_browser() -> Tuple[Optional[Dict[str, str]], List[str]]:
    """Auto-extract ALL Twitter cookies from local browser using browser-cookie3.

    Strategy:
    1. Try in-process first (required on macOS for Keychain access)
    2. Fall back to subprocess (handles SQLite lock when browser is running)

    Returns (cookies_dict | None, diagnostics_list).
    """
    all_diagnostics: List[str] = []

    # 1. In-process (works on macOS, may fail with SQLite lock)
    cookies, diag = _extract_in_process()
    all_diagnostics.extend(diag)
    if cookies:
        return cookies, all_diagnostics

    # 2. Subprocess fallback (handles SQLite lock, but fails on macOS Keychain)
    logger.debug("In-process extraction failed, trying subprocess fallback")
    cookies, diag = _extract_via_subprocess()
    all_diagnostics.extend(diag)
    if not cookies:
        logger.warning("Twitter cookie extraction failed in both in-process and subprocess modes")
    return cookies, all_diagnostics


def get_cookies() -> Dict[str, str]:
    """Get Twitter cookies. Priority: env vars -> browser extraction.

    Raises RuntimeError if no cookies found.
    """
    cookies: Optional[Dict[str, str]] = None
    diagnostics: List[str] = []

    # 1. Try environment variables
    cookies = load_from_env()
    if cookies:
        logger.info("Loaded cookies from environment variables")

    # 2. Try browser extraction (auto-detect)
    if not cookies:
        logger.debug("Attempting browser cookie extraction")
        cookies, diagnostics = extract_from_browser()

    if not cookies:
        lines = ["No Twitter cookies found."]
        # Add actionable Keychain hint when relevant
        hint = _diagnose_keychain_issues(diagnostics)
        if hint:
            lines.append("")
            lines.append("Likely cause:")
            lines.extend("  " + line for line in hint.splitlines())
            lines.append("")
        lines.append("Option 1: Set TWITTER_AUTH_TOKEN and TWITTER_CT0 environment variables")
        lines.append("Option 2: Make sure you are logged into x.com in your browser (Arc/Chrome/Edge/Firefox/Brave)")
        lines.append("")
        lines.append("Run 'twitter -v <command>' for debug diagnostics.")
        raise RuntimeError("\n".join(lines))

    # Verify only for explicit auth failures; transient endpoint issues are tolerated.
    try:
        verify_cookies(cookies["auth_token"], cookies["ct0"], cookies.get("cookie_string"))
    except RuntimeError:
        # Auth failure — re-extract from browser and retry verification
        logger.info("Cookie verification failed, re-extracting from browser")
        fresh_cookies, _ = extract_from_browser()
        if fresh_cookies:
            # Verify fresh cookies — if this also fails, let it raise
            verify_cookies(fresh_cookies["auth_token"], fresh_cookies["ct0"], fresh_cookies.get("cookie_string"))
            return fresh_cookies
        raise
    return cookies
