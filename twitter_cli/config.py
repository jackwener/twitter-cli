"""Configuration loader with YAML parsing and normalization."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG: Dict[str, Any] = {
    "confirm": True,
    "fetch": {
        "count": 50,
    },
    "filter": {
        "mode": "topN",
        "topN": 20,
        "minScore": 50,
        "lang": [],
        "excludeRetweets": False,
        "weights": {
            "likes": 1.0,
            "retweets": 3.0,
            "replies": 2.0,
            "bookmarks": 5.0,
            "views_log": 0.5,
        },
    },
    "rateLimit": {
        "requestDelay": 2.5,
        "maxRetries": 3,
        "retryBaseDelay": 5.0,
        "maxCount": 200,
    },
}


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and normalize config from YAML, merged with defaults."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    path = _resolve_config_path(config_path)
    if not path:
        return config

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read config file %s: %s", path, exc)
        return config

    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse YAML config %s: %s", path, exc)
        return config

    if not isinstance(parsed, dict):
        logger.warning("Config root must be a mapping, got %s", type(parsed).__name__)
        return config

    merged = _deep_merge(config, parsed)
    return _normalize_config(merged)


def _resolve_config_path(config_path: Optional[str]) -> Optional[Path]:
    """Find config path from explicit argument or default locations."""
    if config_path:
        path = Path(config_path)
        return path if path.exists() else None

    candidates = [
        Path.cwd() / "config.yaml",
        Path(__file__).parent.parent / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _deep_merge(target: Dict[str, Any], source: Mapping[str, Any]) -> Dict[str, Any]:
    """Deep merge source into target (source values override target)."""
    result = copy.deepcopy(target)
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize shape and value types."""
    normalized = copy.deepcopy(DEFAULT_CONFIG)
    merged = _deep_merge(normalized, config)

    merged["confirm"] = bool(merged.get("confirm", True))

    fetch = merged.get("fetch")
    if not isinstance(fetch, dict):
        fetch = {}
    fetch_count = _as_int(fetch.get("count"), DEFAULT_CONFIG["fetch"]["count"])
    fetch["count"] = max(fetch_count, 1)
    merged["fetch"] = fetch

    filter_config = merged.get("filter")
    if not isinstance(filter_config, dict):
        filter_config = {}
    mode = str(filter_config.get("mode", "topN"))
    if mode not in {"topN", "score", "all"}:
        mode = "topN"
    filter_config["mode"] = mode
    filter_config["topN"] = max(_as_int(filter_config.get("topN"), 20), 1)
    filter_config["minScore"] = _as_float(filter_config.get("minScore"), 50.0)
    filter_config["excludeRetweets"] = bool(filter_config.get("excludeRetweets", False))

    langs = filter_config.get("lang", [])
    if not isinstance(langs, list):
        langs = []
    filter_config["lang"] = [str(lang) for lang in langs if str(lang)]

    weights = filter_config.get("weights", {})
    if not isinstance(weights, dict):
        weights = {}
    normalized_weights = {}
    default_weights = DEFAULT_CONFIG["filter"]["weights"]
    for key, default_value in default_weights.items():
        normalized_weights[key] = _as_float(weights.get(key), float(default_value))
    filter_config["weights"] = normalized_weights
    merged["filter"] = filter_config

    # Normalize rateLimit section
    rl = merged.get("rateLimit")
    if not isinstance(rl, dict):
        rl = {}
    default_rl = DEFAULT_CONFIG["rateLimit"]
    rl["requestDelay"] = max(_as_float(rl.get("requestDelay"), default_rl["requestDelay"]), 0.0)
    rl["maxRetries"] = max(_as_int(rl.get("maxRetries"), default_rl["maxRetries"]), 0)
    rl["retryBaseDelay"] = max(_as_float(rl.get("retryBaseDelay"), default_rl["retryBaseDelay"]), 1.0)
    rl["maxCount"] = max(_as_int(rl.get("maxCount"), default_rl["maxCount"]), 1)
    merged["rateLimit"] = rl

    return merged


def _as_int(value: Any, default: int) -> int:
    """Best-effort int conversion."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    """Best-effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
