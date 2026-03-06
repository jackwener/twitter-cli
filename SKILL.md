---
name: twitter-cli
description: Read Twitter/X timeline, bookmarks, and user posts from terminal without API keys
---

# twitter-cli Skill

Use this skill when the user wants to read Twitter/X content from terminal without API keys.

## Requirements

- `twitter-cli` installed and available in PATH.
- User is logged in to `x.com` in Chrome/Edge/Firefox/Brave, or sets:
  - `TWITTER_AUTH_TOKEN`
  - `TWITTER_CT0`

## Core Commands

```bash
# Home timeline (For You)
twitter feed

# Following timeline
twitter feed -t following

# Bookmarks
twitter favorite

# Likes (own liked tweets)
twitter likes

# User profile and posts
twitter user <screen_name>
twitter user-posts <screen_name> --max 20
```

## JSON / Scripting

```bash
# Export feed as JSON
twitter feed --json > tweets.json

# Read from local JSON file
twitter feed --input tweets.json
```

## Ranking Filter

Filtering is opt-in (disabled by default). Enable with `--filter`.

```bash
twitter feed --filter
twitter favorite --filter
twitter likes --filter
```

The scoring formula:

```text
score = likes_w * likes
      + retweets_w * retweets
      + replies_w * replies
      + bookmarks_w * bookmarks
      + views_log_w * log10(max(views, 1))
```

Configure weights and mode in `config.yaml`.

## Safety Notes

- Do not ask users to share raw cookie values in chat logs.
- Prefer local browser cookie extraction over manual secret copy/paste.
- If auth fails with 401/403, ask the user to re-login to `x.com`.
