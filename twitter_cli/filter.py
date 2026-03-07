"""Tweet filtering and engagement scoring.

Scores tweets by a weighted engagement formula and filters by
configurable rules (topN, min score, language, etc.).
"""

from __future__ import annotations

from dataclasses import replace
import math

from .config import _as_float, _as_int

DEFAULT_WEIGHTS = {
    "likes": 1.0,
    "retweets": 3.0,
    "replies": 2.0,
    "bookmarks": 5.0,
    "views_log": 0.5,
}


def score_tweet(tweet, weights=None):
    # type: (Tweet, Optional[FilterWeights]) -> float
    """Calculate engagement score for a single tweet.

    Formula:
      score = w_likes × likes
            + w_retweets × retweets
            + w_replies × replies
            + w_bookmarks × bookmarks
            + w_views_log × log10(views)
    """
    weight_map = _build_weights(weights or {})
    m = tweet.metrics
    return (
        weight_map["likes"] * m.likes
        + weight_map["retweets"] * m.retweets
        + weight_map["replies"] * m.replies
        + weight_map["bookmarks"] * m.bookmarks
        + weight_map["views_log"] * math.log10(max(m.views, 1))
    )


def filter_tweets(tweets, config):
    # type: (Sequence[Tweet], Mapping[str, Any]) -> List[Tweet]
    """Filter and rank tweets according to config.

    Config keys:
      mode: "topN" | "score" | "all"
      topN: int
      minScore: float
      lang: list[str]  (empty = no filter)
      excludeRetweets: bool
      weights: dict
    """
    filtered = list(tweets)

    # 1. Language filter
    lang_filter = config.get("lang", [])
    if lang_filter:
        lang_set = {str(lang) for lang in lang_filter if str(lang)}
        filtered = [tweet for tweet in filtered if tweet.lang in lang_set]

    # 2. Exclude retweets
    if config.get("excludeRetweets", False):
        filtered = [tweet for tweet in filtered if not tweet.is_retweet]

    # 3. Score all tweets
    weights = _build_weights(config.get("weights", {}))
    scored = [replace(tweet, score=round(score_tweet(tweet, weights), 1)) for tweet in filtered]

    # 4. Sort by score (descending)
    scored.sort(key=lambda tweet: tweet.score, reverse=True)

    # 5. Apply filter mode
    mode = str(config.get("mode", "topN"))
    if mode == "topN":
        top_n = max(_as_int(config.get("topN"), 20), 1)
        return scored[:top_n]
    if mode == "score":
        min_score = _as_float(config.get("minScore"), 50.0)
        return [tweet for tweet in scored if tweet.score >= min_score]
    return scored


def _build_weights(raw_weights):
    # type: (Mapping[str, Any]) -> Dict[str, float]
    """Merge custom weights with defaults and coerce to float."""
    merged = {}
    for key, default_value in DEFAULT_WEIGHTS.items():
        merged[key] = _as_float(raw_weights.get(key), default_value)
    return merged
