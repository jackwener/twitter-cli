"""Shared constants for twitter-cli."""

import os
import re
import sys

BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# Default Chrome version — updated by _best_chrome_target() at runtime
_DEFAULT_CHROME_VERSION = "133"
_chrome_version = _DEFAULT_CHROME_VERSION  # mutable, set by sync_chrome_version()


def sync_chrome_version(impersonate_target):
    # type: (str) -> None
    """Sync USER_AGENT / SEC_CH_UA with the actual impersonate target.

    Called once when _get_cffi_session() picks a target (e.g. "chrome136").
    """
    global _chrome_version
    match = re.search(r"(\d+)", impersonate_target)
    if match:
        _chrome_version = match.group(1)


def get_user_agent():
    # type: () -> str
    if sys.platform == "darwin":
        platform = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("win"):
        platform = "Windows NT 10.0; Win64; x64"
    else:
        platform = "X11; Linux x86_64"
    return (
        "Mozilla/5.0 (%s) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/%s.0.0.0 Safari/537.36" % (platform, _chrome_version)
    )


def get_sec_ch_ua():
    # type: () -> str
    return '"Chromium";v="%s", "Not(A:Brand";v="99", "Google Chrome";v="%s"' % (
        _chrome_version, _chrome_version,
    )


def get_sec_ch_ua_full_version():
    # type: () -> str
    return '"%s.0.0.0"' % _chrome_version


def get_sec_ch_ua_full_version_list():
    # type: () -> str
    return '"Google Chrome";v="%s.0.0.0", "Chromium";v="%s.0.0.0", "Not.A/Brand";v="99.0.0.0"' % (
        _chrome_version, _chrome_version,
    )


def _get_locale_tag():
    # type: () -> str
    raw = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or "en_US.UTF-8"
    )
    tag = raw.split(".", 1)[0].replace("_", "-")
    return tag or "en-US"


def get_accept_language():
    # type: () -> str
    tag = _get_locale_tag()
    language = tag.split("-", 1)[0] or "en"
    if tag == language:
        return "%s,%s;q=0.9,en;q=0.8" % (tag, language)
    return "%s,%s;q=0.9,en;q=0.8" % (tag, language)


def get_twitter_client_language():
    # type: () -> str
    return _get_locale_tag().split("-", 1)[0] or "en"


def get_sec_ch_ua_platform():
    # type: () -> str
    if sys.platform == "darwin":
        return '"macOS"'
    if sys.platform.startswith("win"):
        return '"Windows"'
    return '"Linux"'


def get_sec_ch_ua_arch():
    # type: () -> str
    machine = (os.uname().machine if hasattr(os, "uname") else "").lower()
    if "arm" in machine or "aarch" in machine:
        return '"arm"'
    if "86" in machine or "amd64" in machine or "x64" in machine:
        return '"x86"'
    return '""'


def get_sec_ch_ua_platform_version():
    # type: () -> str
    if sys.platform == "darwin":
        return '"15.0.0"'
    if sys.platform.startswith("win"):
        return '"10.0.0"'
    return '""'


# Static Client Hints
SEC_CH_UA_MOBILE = "?0"
SEC_CH_UA_PLATFORM = get_sec_ch_ua_platform()
SEC_CH_UA_ARCH = get_sec_ch_ua_arch()
SEC_CH_UA_BITNESS = '"64"'
SEC_CH_UA_MODEL = '""'
SEC_CH_UA_PLATFORM_VERSION = get_sec_ch_ua_platform_version()

# Legacy aliases — modules that import these get the default value.
# _build_headers() should use get_user_agent() / get_sec_ch_ua() instead.
USER_AGENT = get_user_agent()
SEC_CH_UA = get_sec_ch_ua()
