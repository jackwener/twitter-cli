"""Data models for twitter-cli.

Defines Tweet, Author, Metrics, and TweetMedia as simple dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Author:
    id: str
    name: str
    screen_name: str
    profile_image_url: str = ""
    verified: bool = False


@dataclass
class Metrics:
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    views: int = 0
    bookmarks: int = 0


@dataclass
class TweetMedia:
    type: str  # "photo" | "video" | "animated_gif"
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class Tweet:
    id: str
    text: str
    author: Author
    metrics: Metrics
    created_at: str
    media: List[TweetMedia] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    is_retweet: bool = False
    lang: str = ""
    retweeted_by: Optional[str] = None
    quoted_tweet: Optional[Tweet] = None
    score: Optional[float] = None
    article_title: Optional[str] = None
    article_text: Optional[str] = None


@dataclass
class UserProfile:
    id: str
    name: str
    screen_name: str
    bio: str = ""
    location: str = ""
    url: str = ""
    followers_count: int = 0
    following_count: int = 0
    tweets_count: int = 0
    likes_count: int = 0
    verified: bool = False
    profile_image_url: str = ""
    created_at: str = ""
