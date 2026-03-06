# twitter-cli

[![CI](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/twitter-cli.svg)](https://pypi.org/project/twitter-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/twitter-cli.svg)](https://pypi.org/project/twitter-cli/)

A terminal-first CLI for Twitter/X: read timelines, bookmarks, and user profiles without API keys.

## More Tools

- [xhs-cli](https://github.com/jackwener/xhs-cli) — Xiaohongshu (小红书) CLI for notes and account workflows
- [bilibili-cli](https://github.com/jackwener/bilibili-cli) — Bilibili CLI for videos, users, search, and feeds

[English](#english) | [中文](#中文)

## English

### Features

- Timeline: fetch `for-you` and `following` feeds
- Bookmarks: list saved tweets from your account
- User lookup: fetch user profile and recent tweets
- JSON output: export feed/bookmarks/user tweets for scripting
- Optional scoring filter: rank tweets by engagement weights
- Cookie auth: use browser cookies or environment variables

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
twitter favorite
twitter favorite --max 30 --json

# User
twitter user elonmusk
twitter user-posts elonmusk --max 20
```

### Authentication

twitter-cli uses this auth priority:

1. Environment variables: `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. Browser cookies: auto-extract from Chrome/Edge/Firefox/Brave

After loading cookies, the CLI performs lightweight verification. Commands that require account access fail fast on clear auth errors (`401/403`).

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
```

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

### Troubleshooting

- `No Twitter cookies found`
  - Ensure you are logged in to `x.com` in a supported browser.
  - Or set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` manually.

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

### Project Structure

```text
twitter_cli/
├── __init__.py
├── cli.py
├── client.py
├── auth.py
├── config.py
├── filter.py
├── formatter.py
├── serialization.py
└── models.py
```

### Use as AI Agent Skill

twitter-cli ships with a [`SKILL.md`](./SKILL.md) so AI agents can execute common X/Twitter workflows.

#### Claude Code / Antigravity

```bash
npx skills add jackwener/twitter-cli

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

- 时间线读取：支持 `for-you` 和 `following`
- 收藏读取：查看账号书签推文
- 用户查询：查看用户资料和用户推文
- JSON 输出：便于脚本处理
- 可选筛选：按 engagement score 排序
- Cookie 认证：支持环境变量和浏览器自动提取

### 安装

```bash
# 推荐：uv tool
uv tool install twitter-cli

# 其次：pipx
pipx install twitter-cli
```

### 常用命令

```bash
# 首页推荐流
twitter feed

# Following 流
twitter feed -t following

# 开启筛选（默认不开启）
twitter feed --filter

# 收藏
twitter favorite

# 用户
twitter user elonmusk
twitter user-posts elonmusk --max 20
```

### 认证说明

认证优先级：

1. `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. 浏览器 Cookie 自动提取（Chrome/Edge/Firefox/Brave）

### 筛选算法

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

- 报错 `No Twitter cookies found`：请先登录 `x.com` 或手动设置环境变量。
- 报错 `Cookie expired or invalid`：Cookie 过期，重新登录后重试。
- 报错 `Twitter API error 404`：通常是 queryId 轮换，重试即可。

### 注意事项

- Cookie 登录有平台风控风险，建议使用专用账号。
- Cookie 仅在本地使用，不会被本工具上传。

### 作为 AI Agent Skill 使用

twitter-cli 提供了 [`SKILL.md`](./SKILL.md)，可让 AI Agent 更稳定地调用本工具。

#### Claude Code / Antigravity

```bash
npx skills add jackwener/twitter-cli

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
