"""Tweet formatter for terminal output (rich) and JSON export."""

from __future__ import annotations

from typing import List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .models import Tweet, UserProfile
from .timeutil import format_local_time, format_relative_time


def format_number(n: int) -> str:
    """Format number with K/M suffixes."""
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)


def print_tweet_table(
    tweets: List[Tweet],
    console: Optional[Console] = None,
    title: Optional[str] = None,
    full_text: bool = False,
) -> None:
    """Print tweets as a rich table."""
    if console is None:
        console = Console()

    if not title:
        title = "📱 Twitter — %d tweets" % len(tweets)

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Author", style="cyan", width=18, no_wrap=True)
    table.add_column("Tweet", ratio=3)
    table.add_column("Stats", style="green", width=22, no_wrap=True)
    table.add_column("Score", style="yellow", width=6, justify="right")

    for i, tweet in enumerate(tweets):
        # Author
        verified = " ✓" if tweet.author.verified else ""
        author_text = "@%s%s" % (tweet.author.screen_name, verified)
        if tweet.is_retweet and tweet.retweeted_by:
            author_text += "\n🔄 @%s" % tweet.retweeted_by

        # Tweet text
        text = tweet.text.replace("\n", " ").strip()
        if not full_text and len(text) > 120:
            text = text[:117] + "..."

        # Media indicators
        if tweet.media:
            media_icons = []
            for m in tweet.media:
                if m.type == "photo":
                    media_icons.append("📷")
                elif m.type == "video":
                    media_icons.append("📹")
                else:
                    media_icons.append("🎞️")
            text += " " + " ".join(media_icons)

        # Quoted tweet
        if tweet.quoted_tweet:
            qt = tweet.quoted_tweet
            qt_text = qt.text.replace("\n", " ")
            if not full_text and len(qt_text) > 60:
                qt_text = qt_text[:57] + "..."
            text += "\n┌ @%s: %s" % (qt.author.screen_name, qt_text)

        # Tweet link
        text += "\n🔗 x.com/%s/status/%s" % (tweet.author.screen_name, tweet.id)

        # Stats
        rel_time = format_relative_time(tweet.created_at)
        stats = (
            "❤️ %s  🔄 %s\n💬 %s  👁️ %s\n🕐 %s"
            % (
                format_number(tweet.metrics.likes),
                format_number(tweet.metrics.retweets),
                format_number(tweet.metrics.replies),
                format_number(tweet.metrics.views),
                rel_time,
            )
        )

        # Score
        score_str = "%.1f" % tweet.score if tweet.score is not None else "-"

        table.add_row(str(i + 1), author_text, text, stats, score_str)

    console.print(table)


def print_tweet_detail(tweet: Tweet, console: Optional[Console] = None) -> None:
    """Print a single tweet in detail using a rich panel."""
    if console is None:
        console = Console()

    verified = " ✓" if tweet.author.verified else ""
    header = "@%s%s (%s)" % (tweet.author.screen_name, verified, tweet.author.name)

    body_parts = []

    if tweet.is_retweet and tweet.retweeted_by:
        body_parts.append("🔄 Retweeted by @%s\n" % tweet.retweeted_by)

    body_parts.append(tweet.text)

    if tweet.media:
        body_parts.append("")
        for m in tweet.media:
            icon = "📷" if m.type == "photo" else ("📹" if m.type == "video" else "🎞️")
            body_parts.append("%s %s: %s" % (icon, m.type, m.url))

    if tweet.urls:
        body_parts.append("")
        for url in tweet.urls:
            body_parts.append("🔗 %s" % url)

    if tweet.quoted_tweet:
        qt = tweet.quoted_tweet
        body_parts.append("")
        body_parts.append("┌── Quoted @%s ──" % qt.author.screen_name)
        body_parts.append(qt.text[:200])

    body_parts.append("")
    body_parts.append(
        "❤️ %s  🔄 %s  💬 %s  🔖 %s  👁️ %s"
        % (
            format_number(tweet.metrics.likes),
            format_number(tweet.metrics.retweets),
            format_number(tweet.metrics.replies),
            format_number(tweet.metrics.bookmarks),
            format_number(tweet.metrics.views),
        )
    )
    local_time = format_local_time(tweet.created_at)
    rel_time = format_relative_time(tweet.created_at)
    body_parts.append(
        "🕐 %s (%s) · https://x.com/%s/status/%s"
        % (local_time, rel_time, tweet.author.screen_name, tweet.id)
    )

    console.print(Panel(
        "\n".join(body_parts),
        title=header,
        border_style="blue",
        expand=True,
    ))


def article_to_markdown(tweet: Tweet) -> str:
    """Convert a Twitter Article tweet into a Markdown document."""
    title = tweet.article_title or "Twitter Article"
    lines = [
        "# %s" % title,
        "",
        "- Author: @%s (%s)" % (tweet.author.screen_name, tweet.author.name),
        "- Published: %s" % (tweet.created_at or "unknown"),
        "- URL: https://x.com/%s/status/%s" % (tweet.author.screen_name, tweet.id),
        "- Likes: %s" % format_number(tweet.metrics.likes),
        "- Retweets: %s" % format_number(tweet.metrics.retweets),
        "- Replies: %s" % format_number(tweet.metrics.replies),
        "- Bookmarks: %s" % format_number(tweet.metrics.bookmarks),
        "- Views: %s" % format_number(tweet.metrics.views),
    ]

    if tweet.article_text:
        lines.extend(["", tweet.article_text.strip()])

    return "\n".join(lines).strip() + "\n"


def print_article(tweet: Tweet, console: Optional[Console] = None) -> None:
    """Print a Twitter Article with rich formatting."""
    if console is None:
        console = Console()

    verified = " ✓" if tweet.author.verified else ""
    title = tweet.article_title or "Twitter Article"
    meta_parts = [
        "By @%s%s (%s)" % (tweet.author.screen_name, verified, tweet.author.name),
        "🕐 %s" % tweet.created_at,
        "🔗 x.com/%s/status/%s" % (tweet.author.screen_name, tweet.id),
        "",
        "❤️ %s  🔄 %s  💬 %s  🔖 %s  👁️ %s"
        % (
            format_number(tweet.metrics.likes),
            format_number(tweet.metrics.retweets),
            format_number(tweet.metrics.replies),
            format_number(tweet.metrics.bookmarks),
            format_number(tweet.metrics.views),
        ),
    ]
    console.print(Panel(
        "\n".join(meta_parts),
        title="📰 %s" % title,
        border_style="blue",
        expand=True,
    ))

    if tweet.article_text:
        console.print()
        console.print(Markdown(tweet.article_text))


def print_filter_stats(
    original_count: int,
    filtered: List[Tweet],
    console: Optional[Console] = None,
) -> None:
    """Print filter statistics."""
    if console is None:
        console = Console()

    console.print(
        "📊 Filter: %d → %d tweets" % (original_count, len(filtered))
    )
    if filtered:
        top_score = filtered[0].score or 0.0
        bottom_score = filtered[-1].score or 0.0
        console.print(
            "   Score range: %.1f ~ %.1f" % (bottom_score, top_score)
        )


def print_user_profile(user: UserProfile, console: Optional[Console] = None) -> None:
    """Print user profile as a rich panel."""
    if console is None:
        console = Console()

    verified = " ✓" if user.verified else ""
    header = "@%s%s (%s)" % (user.screen_name, verified, user.name)

    lines = []
    if user.bio:
        lines.append(user.bio)
        lines.append("")

    if user.location:
        lines.append("📍 %s" % user.location)
    if user.url:
        lines.append("🔗 %s" % user.url)
    if user.location or user.url:
        lines.append("")

    lines.append(
        "👥 %s followers · %s following · %s tweets · %s likes"
        % (
            format_number(user.followers_count),
            format_number(user.following_count),
            format_number(user.tweets_count),
            format_number(user.likes_count),
        )
    )

    if user.created_at:
        lines.append("📅 Joined %s" % user.created_at)
    lines.append("🔗 x.com/%s" % user.screen_name)

    console.print(Panel(
        "\n".join(lines),
        title=header,
        border_style="cyan",
        expand=True,
    ))


def print_user_table(
    users: List[UserProfile],
    console: Optional[Console] = None,
    title: Optional[str] = None,
) -> None:
    """Print a list of users as a rich table."""
    if console is None:
        console = Console()

    if not title:
        title = "👥 Users — %d" % len(users)

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("User", style="cyan", width=20, no_wrap=True)
    table.add_column("Bio", ratio=3)
    table.add_column("Stats", style="green", width=22, no_wrap=True)

    for i, user in enumerate(users):
        verified = " ✓" if user.verified else ""
        user_text = "@%s%s\n%s" % (user.screen_name, verified, user.name)

        bio = (user.bio or "").replace("\n", " ").strip()
        if len(bio) > 100:
            bio = bio[:97] + "..."

        stats = (
            "👥 %s followers\n📝 %s following"
            % (
                format_number(user.followers_count),
                format_number(user.following_count),
            )
        )

        table.add_row(str(i + 1), user_text, bio, stats)

    console.print(table)
