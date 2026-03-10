# twitter-cli

[![CI](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/twitter-cli.svg)](https://pypi.org/project/twitter-cli/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.8-blue.svg)](https://pypi.org/project/twitter-cli/)

A terminal-first CLI for Twitter/X: read timelines, bookmarks, and user profiles without API keys.

## More Tools

- [xhs-cli](https://github.com/jackwener/xhs-cli) — Xiaohongshu (小红书) CLI for notes and account workflows
- [bilibili-cli](https://github.com/jackwener/bilibili-cli) — Bilibili CLI for videos, users, search, and feeds
- [discord-cli](https://github.com/jackwener/discord-cli) — Discord CLI for local-first sync, search, and export
- [tg-cli](https://github.com/jackwener/tg-cli) — Telegram CLI for local-first sync, search, and export

[English](#english) | [中文](#中文)

## English

### Features

**Read:**
- Timeline: fetch `for-you` and `following` feeds
- Bookmarks: list saved tweets from your account
- Search: find tweets by keyword with Top/Latest/Photos/Videos tabs
- Tweet detail: view a tweet and its replies
- List timeline: fetch tweets from a Twitter List
- User lookup: fetch user profile, tweets, likes, followers, and following
- Structured output: export any data as YAML or JSON for scripting and AI agent integration
- Optional scoring filter: rank tweets by engagement weights

> **AI Agent Tip:** Prefer `--yaml` for structured output unless a strict JSON parser is required. Non-TTY stdout defaults to YAML automatically. Use `--max` to limit results.

**Write:**
- Post: create new tweets and replies
- Delete: remove your own tweets
- Like / Unlike: manage tweet likes
- Retweet / Unretweet: manage retweets
- Bookmark: bookmark/unbookmark (`favorite/unfavorite` kept as compatibility aliases)

**Auth & Anti-Detection:**
- Cookie auth: use browser cookies or environment variables
- Full cookie forwarding: extracts ALL browser cookies for richer browser context
- TLS fingerprint impersonation: `curl_cffi` with dynamic Chrome version matching
- `x-client-transaction-id` header generation
- Request timing jitter to avoid pattern detection
- Write operation delays (1.5–4s random) to mitigate rate limits
- Proxy support via `TWITTER_PROXY` environment variable

### Installation

```bash
# Recommended: uv tool (fast, isolated)
uv tool install twitter-cli

# Alternative: pipx
pipx install twitter-cli
```

Install from source:

```bash
git clone git@github.com:jackwener/twitter-cli.git
cd twitter-cli
uv sync
```

### Quick Start

```bash
# Fetch home timeline (For You)
twitter feed

# Fetch Following timeline
twitter feed -t following

# Enable ranking filter explicitly
twitter feed --filter
```

### Usage

```bash
# Feed
twitter feed --max 50
twitter feed --json > tweets.json
twitter feed --input tweets.json

# Bookmarks
twitter bookmarks
twitter bookmarks --max 30 --yaml

# Search
twitter search "Claude Code"
twitter search "AI agent" -t Latest --max 50
twitter search "机器学习" --yaml
twitter search "topic" -o results.json         # Save to file
twitter search "trending" --filter              # Apply ranking filter

# Tweet detail (view tweet + replies)
twitter tweet 1234567890
twitter tweet https://x.com/user/status/1234567890

# List timeline
twitter list 1539453138322673664

# User
twitter user elonmusk
twitter user-posts elonmusk --max 20
twitter user-posts elonmusk -o tweets.json
twitter likes elonmusk --max 30
twitter likes elonmusk -o likes.json
twitter followers elonmusk --max 50
twitter following elonmusk --max 50

# Write operations
twitter post "Hello from twitter-cli!"
twitter post "reply text" --reply-to 1234567890
twitter delete 1234567890
twitter like 1234567890
twitter unlike 1234567890
twitter retweet 1234567890
twitter unretweet 1234567890
twitter bookmark 1234567890
twitter unbookmark 1234567890
```

### Authentication

twitter-cli uses this auth priority:

1. **Environment variables**: `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. **Browser cookies** (recommended): auto-extract from Arc/Chrome/Edge/Firefox/Brave

Browser extraction is recommended — it forwards ALL Twitter cookies (not just `auth_token` + `ct0`) and aligns request headers with your local runtime, which is closer to normal browser traffic than minimal cookie auth.

After loading cookies, the CLI performs lightweight verification. Commands that require account access fail fast on clear auth errors (`401/403`).

### Proxy Support

Set `TWITTER_PROXY` to route all requests through a proxy:

```bash
# HTTP proxy
export TWITTER_PROXY=http://127.0.0.1:7890

# SOCKS5 proxy
export TWITTER_PROXY=socks5://127.0.0.1:1080
```

Using a proxy can help reduce IP-based rate limiting risks.

### Configuration

Create `config.yaml` in your working directory:

```yaml
fetch:
  count: 50

filter:
  mode: "topN"          # "topN" | "score" | "all"
  topN: 20
  minScore: 50
  lang: []
  excludeRetweets: false
  weights:
    likes: 1.0
    retweets: 3.0
    replies: 2.0
    bookmarks: 5.0
    views_log: 0.5

rateLimit:
  requestDelay: 2.5     # base delay between requests (randomized ×0.7–1.5)
  maxRetries: 3          # retry count on rate limit (429)
  retryBaseDelay: 5.0    # base delay for exponential backoff
  maxCount: 200          # hard cap on fetched items
```

Fetch behavior:

- `fetch.count` is the default item count for read commands when `--max` is omitted

Filter behavior:

- Default behavior: no ranking filter unless `--filter` is passed
- With `--filter`: tweets are scored/sorted using `config.filter`

Scoring formula:

```text
score = likes_w * likes
      + retweets_w * retweets
      + replies_w * replies
      + bookmarks_w * bookmarks
      + views_log_w * log10(max(views, 1))
```

Mode behavior:

- `mode: "topN"` keeps the highest `topN` tweets by score
- `mode: "score"` keeps tweets where `score >= minScore`
- `mode: "all"` returns all tweets after sorting by score

### Best Practices (Avoiding Bans)

- **Use a proxy** — set `TWITTER_PROXY` to avoid direct IP exposure
- **Keep request volumes low** — use `--max 20` instead of `--max 500`
- **Don't run too frequently** — each startup fetches x.com to initialize anti-detection headers
- **Use browser cookie extraction** — provides full cookie fingerprint
- **Avoid datacenter IPs** — residential proxies are much safer

### Troubleshooting

- `No Twitter cookies found`
  - Ensure you are logged in to `x.com` in a supported browser (Arc/Chrome/Edge/Firefox/Brave).
  - Or set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` manually.
  - Run with `-v` to see browser extraction diagnostics.

- `Cookie expired or invalid (HTTP 401/403)`
  - Re-login to `x.com` and retry.

- `Twitter API error 404`
  - This can happen when upstream GraphQL query IDs rotate.
  - Retry the command; the client attempts a live queryId fallback.

- `Invalid tweet JSON file`
  - Regenerate input using `twitter feed --json > tweets.json`.

### Development

```bash
# Install dev dependencies
uv sync --extra dev

# Lint + tests
uv run ruff check .
uv run pytest -q
```

Current CI validates the project on Python 3.12.

### Project Structure

```text
twitter_cli/
├── __init__.py
├── cli.py
├── client.py
├── auth.py
├── config.py
├── constants.py
├── filter.py
├── formatter.py
├── serialization.py
└── models.py
```

### Use as AI Agent Skill

twitter-cli ships with a [`SKILL.md`](./SKILL.md) so AI agents can execute common X/Twitter workflows.

#### Claude Code / Antigravity

```bash
# Clone into your project's skills directory
mkdir -p .agents/skills
git clone git@github.com:jackwener/twitter-cli.git .agents/skills/twitter-cli

# Or copy SKILL.md only
curl -o .agents/skills/twitter-cli/SKILL.md \
  https://raw.githubusercontent.com/jackwener/twitter-cli/main/SKILL.md
```

#### OpenClaw / ClawHub

Install from ClawHub:

```bash
clawhub install twitter-cli
```

After installation, OpenClaw can call `twitter-cli` commands directly.

## 中文

### 功能概览

**读取:**
- 时间线读取：支持 `for-you` 和 `following`
- 收藏读取：查看账号书签推文
- 搜索：按关键词搜索推文，支持 Top/Latest/Photos/Videos
- 推文详情：查看推文及其回复
- 列表时间线：获取 Twitter List 的推文
- 用户查询：查看用户资料、推文、点赞、粉丝和关注
- 结构化输出：支持 YAML 和 JSON，便于脚本处理和 AI agent 集成

> **AI Agent 提示：** 需要结构化输出时优先使用 `--yaml`，除非下游必须是 JSON。stdout 不是 TTY 时默认输出 YAML。用 `--max` 控制返回数量。

**写入:**
- 发推：发布新推文和回复
- 删除：删除自己的推文
- 点赞 / 取消点赞
- 转推 / 取消转推
- 书签 / 取消书签：bookmark/unbookmark（保留 `favorite/unfavorite` 兼容别名）

**认证与反风控:**
- Cookie 认证：支持环境变量和浏览器自动提取
- 完整 Cookie 转发：提取浏览器中所有 Twitter Cookie，保留更多浏览器上下文
- TLS 指纹伪装：`curl_cffi` 动态匹配 Chrome 版本
- `x-client-transaction-id` 请求头生成
- 请求时序随机化（jitter）
- 写操作随机延迟（1.5–4 秒），降低频率风控
- 代理支持：`TWITTER_PROXY` 环境变量

### 安装

```bash
# 推荐：uv tool
uv tool install twitter-cli
```

### 使用指南

```bash
# 时间线
twitter feed
twitter feed -t following
twitter feed --filter

# 收藏
twitter bookmarks

# 搜索
twitter search "Claude Code"
twitter search "AI agent" -t Latest --max 50
twitter search "topic" -o results.json         # 保存到文件
twitter search "trending" --filter              # 启用排序筛选

# 推文详情
twitter tweet 1234567890

# 列表时间线
twitter list 1539453138322673664

# 用户
twitter user elonmusk
twitter user-posts elonmusk --max 20
twitter user-posts elonmusk -o tweets.json
twitter likes elonmusk --max 30
twitter likes elonmusk -o likes.json
twitter followers elonmusk
twitter following elonmusk

# 写操作
twitter post "你好，世界！"
twitter post "回复内容" --reply-to 1234567890
twitter delete 1234567890
twitter like 1234567890
twitter unlike 1234567890
twitter retweet 1234567890
twitter unretweet 1234567890
twitter bookmark 1234567890
twitter unbookmark 1234567890
```

### 认证说明

认证优先级：

1. **环境变量**：`TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. **浏览器提取**（推荐）：Arc/Chrome/Edge/Firefox/Brave 全量 Cookie 提取

推荐使用浏览器提取方式，会转发所有 Twitter Cookie，并按本机运行环境生成语言和平台请求头；它比仅发送 `auth_token` + `ct0` 更接近普通浏览器流量，但不等于完整浏览器自动化。

### 代理支持

设置 `TWITTER_PROXY` 环境变量即可：

```bash
export TWITTER_PROXY=http://127.0.0.1:7890
# 或 SOCKS5
export TWITTER_PROXY=socks5://127.0.0.1:1080
```

使用代理可以降低 IP 维度的风控风险。

### 筛选算法

未传 `--max` 时，所有读取命令默认使用 `config.yaml` 里的 `fetch.count`。

只有在传入 `--filter` 时才会启用筛选评分；默认不筛选。

评分公式：

```text
score = likes_w * likes
      + retweets_w * retweets
      + replies_w * replies
      + bookmarks_w * bookmarks
      + views_log_w * log10(max(views, 1))
```

模式说明：

- `mode: "topN"`：按分数排序后保留前 `topN` 条
- `mode: "score"`：仅保留 `score >= minScore` 的推文
- `mode: "all"`：按分数排序后全部保留

### 常见问题

- 报错 `No Twitter cookies found`：请先登录 `x.com`，并确认浏览器为 Arc/Chrome/Edge/Firefox/Brave 之一，或手动设置环境变量。
- 如需查看浏览器提取细节，可加 `-v` 打开诊断日志。
- 报错 `Cookie expired or invalid`：Cookie 过期，重新登录后重试。
- 报错 `Twitter API error 404`：通常是 queryId 轮换，重试即可。

### 使用建议（防封号）

- **使用代理** — 设置 `TWITTER_PROXY`，避免裸 IP 直连
- **控制请求量** — 用 `--max 20` 而不是 `--max 500`
- **避免频繁启动** — 每次启动都会访问 x.com 初始化反检测请求头
- **使用浏览器 Cookie 提取** — 提供完整 Cookie 指纹
- **避免数据中心 IP** — 住宅代理更安全
- Cookie 仅在本地使用，不会被本工具上传

### 作为 AI Agent Skill 使用

twitter-cli 提供了 [`SKILL.md`](./SKILL.md)，可让 AI Agent 更稳定地调用本工具。

#### Claude Code / Antigravity

```bash
# 克隆到项目 skills 目录
mkdir -p .agents/skills
git clone git@github.com:jackwener/twitter-cli.git .agents/skills/twitter-cli

# 或仅下载 SKILL.md
curl -o .agents/skills/twitter-cli/SKILL.md \
  https://raw.githubusercontent.com/jackwener/twitter-cli/main/SKILL.md
```

#### OpenClaw / ClawHub

通过 ClawHub 安装：

```bash
clawhub install twitter-cli
```

### 更多工具

- [bilibili-cli](https://github.com/jackwener/bilibili-cli) — Bilibili 视频、用户、搜索与动态 CLI
- [discord-cli](https://github.com/jackwener/discord-cli) — Discord 本地优先同步、检索与导出 CLI
- [tg-cli](https://github.com/jackwener/tg-cli) — Telegram 本地优先同步、检索与导出 CLI
- [xhs-cli](https://github.com/jackwener/xhs-cli) — 小红书笔记与账号工作流 CLI
