---
name: twitter-cli
description: Use twitter-cli for ALL Twitter/X operations — reading tweets, articles, posting, replying, quoting, liking, retweeting, following, searching, user lookups. Invoke whenever user requests any Twitter interaction.
author: jackwener
version: "2.0.0"
tags:
  - twitter
  - x
  - social-media
  - terminal
  - cli
---

# twitter-cli — Twitter/X CLI Tool

**Binary:** `twitter`
**Credentials:** browser cookies (auto-extracted) or env vars

## Setup

```bash
# Install (requires Python 3.8+)
uv tool install twitter-cli
# Or: pipx install twitter-cli

# Upgrade to latest (recommended to avoid API errors)
uv tool upgrade twitter-cli
# Or: pipx upgrade twitter-cli
```

## Authentication

**IMPORTANT FOR AGENTS**: Before executing ANY twitter-cli command, you MUST first check if credentials exist. If not, you MUST proactively guide the user through the authentication process. Do NOT assume credentials are configured.

**CRITICAL**: Write operations (posting tweets, replying, quoting) REQUIRE full browser cookies. Only providing `auth_token` + `ct0` via env vars may result in **226 error** ("looks like automated behavior"). For best results, use browser cookie extraction.

### Step 0: Check if already authenticated

```bash
twitter status --yaml >/dev/null && echo "AUTH_OK" || echo "AUTH_NEEDED"
```

If `AUTH_OK`, skip to [Command Reference](#command-reference).
If `AUTH_NEEDED`, proceed to guide the user:

### Step 1: Guide user to authenticate

**Method A: Browser cookie extraction (recommended)**

Ensure user is logged into x.com in one of: Arc, Chrome, Edge, Firefox, Brave. twitter-cli auto-extracts cookies.
All Chrome profiles are scanned automatically. To specify a profile: `TWITTER_CHROME_PROFILE="Profile 2" twitter feed`.

```bash
twitter whoami
```

**Method B: Environment variables**

```bash
export TWITTER_AUTH_TOKEN="<auth_token from browser>"
export TWITTER_CT0="<ct0 from browser>"
twitter whoami
```

**Method C: Full cookie string (for cloud/remote agents)**

Tell the user:

> 我需要你的 Twitter 登录凭证。请按以下步骤获取：
>
> 1. 用 Chrome/Edge/Firefox 打开 https://x.com（确保已登录）
> 2. 按 `F12` 打开开发者工具 → **Network** 标签
> 3. 在页面上刷新，点击任意 `x.com` 请求
> 4. 找到 **Request Headers** → **Cookie:** 这一行，右键 → 复制值
> 5. 把完整 Cookie 字符串发给我
>
> ⚠️ Cookie 包含登录信息，请不要分享给其他人。

Then extract and set env vars:

```bash
FULL_COOKIE="<user's cookie string>"
export TWITTER_AUTH_TOKEN=$(echo "$FULL_COOKIE" | grep -oE 'auth_token=[a-f0-9]+' | cut -d= -f2)
export TWITTER_CT0=$(echo "$FULL_COOKIE" | grep -oE 'ct0=[a-f0-9]+' | cut -d= -f2)
twitter whoami
```

### Step 2: Handle common auth issues

| Symptom | Agent action |
|---------|-------------|
| `No Twitter cookies found` | Guide user to login to x.com in browser, or set env vars |
| Read works, write returns 226 | Full cookies missing — use browser cookie extraction instead of env vars |
| `Cookie expired (401/403)` | Ask user to re-login to x.com and retry |
| User changed password | All old cookies invalidated — re-extract |

## Output Format

### Default: Rich table (human-readable)

```bash
twitter feed                          # Pretty table output
```

### YAML / JSON: structured output

Non-TTY stdout defaults to YAML automatically. Use `OUTPUT=yaml|json|rich|auto` to override.

```bash
twitter feed --yaml
twitter feed --json | jq '.[0].text'
```

All machine-readable output uses the envelope documented in [SCHEMA.md](./SCHEMA.md).
Tweet and user payloads now live under `.data`.

### Compact: `-c` flag (minimal tokens for LLM)

```bash
twitter -c feed --max 10              # Minimal fields, great for LLM context
twitter -c search "AI" --max 20       # ~80% fewer tokens than --json
```

**Compact fields (per tweet):** `id`, `author` (@handle), `text` (truncated 140 chars), `likes`, `rts`, `time` (short format)

## Command Reference

### Read Operations

```bash
twitter status                         # Quick auth check
twitter status --yaml                  # Structured auth status
twitter whoami                         # Current authenticated user
twitter whoami --yaml                  # YAML output
twitter whoami --json                  # JSON output
twitter user elonmusk                  # User profile
twitter user elonmusk --json           # JSON output
twitter feed                           # Home timeline (For You)
twitter feed -t following              # Following timeline
twitter feed --max 50                  # Limit count
twitter feed --filter                  # Enable ranking filter
twitter feed --yaml > tweets.yaml      # Export as YAML
twitter feed --input tweets.json       # Read from local JSON file
twitter bookmarks                      # Bookmarked tweets
twitter bookmarks --max 30 --yaml
twitter search "keyword"               # Search tweets
twitter search "AI agent" -t Latest --max 50
twitter search "topic" -o results.json # Save to file
twitter tweet 1234567890               # Tweet detail + replies
twitter tweet https://x.com/user/status/12345  # Accepts URL
twitter article 1234567890             # Twitter Article (long-form post, body as Markdown)
twitter article https://x.com/user/article/12345  # Accepts /article/ URL
twitter article 1234567890 --json      # Structured output with articleTitle + articleText
twitter list 1539453138322673664       # List timeline
twitter user-posts elonmusk --max 20   # User's tweets
twitter likes elonmusk --max 30        # User's likes (own only, see note)
twitter followers elonmusk --max 50    # Followers
twitter following elonmusk --max 50    # Following
```

### Write Operations

```bash
twitter post "Hello from twitter-cli!"              # Post tweet
twitter reply 1234567890 "Great tweet!"              # Reply (standalone)
twitter post "reply text" --reply-to 1234567890      # Reply (via post)
twitter quote 1234567890 "Interesting take"          # Quote-tweet
twitter delete 1234567890                            # Delete tweet
twitter like 1234567890                              # Like
twitter unlike 1234567890                            # Unlike
twitter retweet 1234567890                           # Retweet
twitter unretweet 1234567890                         # Unretweet
twitter bookmark 1234567890                          # Bookmark
twitter unbookmark 1234567890                        # Unbookmark
twitter follow elonmusk                              # Follow user
twitter unfollow elonmusk                            # Unfollow user
```

## Agent Workflows

### Post and verify

```bash
twitter post "My tweet text" 2>/dev/null
# Output includes tweet URL: 🔗 https://x.com/i/status/<id>
```

### Reply to someone's latest tweet

```bash
TWEET_ID=$(twitter user-posts targetuser --max 1 --json | jq -r '.data[0].id')
twitter reply "$TWEET_ID" "Nice post!"
```

### Create a thread

```bash
# Post first tweet, capture output for tweet ID
twitter post "Thread 1/3: First point"
# Note the tweet ID from output, then:
twitter reply <first_tweet_id> "2/3: Second point"
twitter reply <second_tweet_id> "3/3: Final point"
```

### Quote-tweet with commentary

```bash
TWEET_ID=$(twitter search "interesting topic" --max 1 --json | jq -r '.data[0].id')
twitter quote "$TWEET_ID" "This is a great insight!"
```

### Like all search results

```bash
twitter search "interesting topic" --max 5 --json | jq -r '.data[].id' | while read id; do
  twitter like "$id"
done
```

### Get user info then follow

```bash
twitter user targethandle --json | jq '.data | {username, followers, bio}'
twitter follow targethandle
```

### Find most popular tweets from a user

```bash
twitter user-posts elonmusk --max 20 --json | jq '.data | sort_by(.metrics.likes) | reverse | .[:3] | .[] | {id, text: .text[:80], likes: .metrics.likes}'
```

### Check follower relationship

```bash
MY_NAME=$(twitter whoami --json | jq -r '.data.user.username')
twitter followers "$MY_NAME" --max 200 --json | jq -r '.data[].username' | grep -q "targetuser" && echo "Yes" || echo "No"
```

### Read and summarize a Twitter Article

```bash
# Fetch article as JSON and extract title + body for LLM summarization
twitter article 1234567890 --json | jq '{title: .data.articleTitle, body: .data.articleText}'

# Or use compact mode — note: compact truncates text, prefer --json for full article body
twitter article https://x.com/user/article/1234567890 --json
```

### Daily reading workflow

```bash
# Compact mode for token-efficient LLM context
twitter -c feed -t following --max 30
twitter -c bookmarks --max 20

# Full JSON for analysis
twitter feed -t following --max 30 -o following.json
twitter bookmarks --max 20 -o bookmarks.json
```

### Search with jq filtering

```bash
# Tweets with > 100 likes
twitter search "AI safety" --max 20 --json | jq '[.data[] | select(.metrics.likes > 100)]'

# Extract just text and author
twitter search "rust lang" --max 10 --json | jq '.data[] | {author: .author.screenName, text: .text[:100]}'

# Most engaged tweets
twitter search "topic" --max 20 --json | jq '.data | sort_by(.metrics.likes) | reverse | .[:5] | .[].id'
```

## Ranking Filter

Filtering is opt-in. Enable with `--filter`:

```bash
twitter feed --filter
twitter bookmarks --filter
```

## Error Reference

| Error | Cause | Fix |
|-------|-------|-----|
| `No Twitter cookies found` | Not authenticated | Login to x.com in browser, or set env vars |
| HTTP 226 | Automated detection | Use browser cookie extraction (not env vars) |
| HTTP 401/403 | Cookie expired | Re-login to x.com and retry |
| HTTP 404 | QueryId rotation | Retry (auto-fallback built in) |
| HTTP 429 | Rate limited | Wait 15+ minutes, then retry |
| Error 187 | Duplicate tweet | Change text content |
| Error 186 | Tweet too long | Keep under 280 chars |

## Limitations

- **Text only** — no media/image upload
- **No DMs** — no direct messaging
- **No notifications** — can't read notifications
- **No polls** — can't create polls
- **Single account** — one set of credentials at a time
- **Likes are private** — Twitter/X made all likes private since June 2024. `twitter likes` only works for your own account
- **Articles read-only** — `twitter article` can read Articles but cannot create or edit them

## Safety Notes

- Write operations have built-in random delays (1.5–4s) to avoid rate limits.
- TLS fingerprint and User-Agent are automatically matched to the Chrome version used.
- Do not ask users to share raw cookie values in chat logs.
- Prefer local browser cookie extraction over manual secret copy/paste.
- Agent should treat cookie values as secrets (do not echo to stdout unnecessarily).
