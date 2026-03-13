# twitter-cli

[![CI](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jackwener/twitter-cli/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/twitter-cli.svg)](https://pypi.org/project/twitter-cli/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://pypi.org/project/twitter-cli/)

A terminal-first CLI for Twitter/X: read timelines, bookmarks, and user profiles without API keys.

## More Tools

- [xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli) — Xiaohongshu (小红书) CLI for notes and account workflows
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
- Article: fetch a Twitter Article and export it as Markdown
- List timeline: fetch tweets from a Twitter List
- User lookup: fetch user profile, tweets, likes, followers, and following
- `--full-text`: disable tweet text truncation in rich table output
- Structured output: export any data as YAML or JSON for scripting and AI agent integration
- Optional scoring filter: rank tweets by engagement weights
- Structured output contract: [SCHEMA.md](./SCHEMA.md)

> **AI Agent Tip:** Prefer `--yaml` for structured output unless a strict JSON parser is required. Non-TTY stdout defaults to YAML automatically. Use `--max` to limit results.

**Write:**
- Post: create new tweets and replies, with optional image attachments (up to 4)
- Quote: quote-tweet with optional images
- Delete: remove your own tweets
- Like / Unlike: manage tweet likes
- Retweet / Unretweet: manage retweets
- Bookmark: bookmark/unbookmark (`favorite/unfavorite` kept as compatibility aliases)
- Write commands also support explicit `--json` / `--yaml` output now
- `--dry-run`: preview what would be sent without executing (all write commands)
- Interactive confirmation: post/reply/quote show a preview + `Send? [Y/n]` prompt in TTY mode (skip with `--yes` / `-y` or `confirm: false` in config)

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

Upgrade to the latest version:

```bash
uv tool upgrade twitter-cli
# Or: pipx upgrade twitter-cli
```

> **Tip:** Upgrade regularly to avoid unexpected errors from outdated API handling.

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
twitter feed --full-text
twitter feed --output tweets.json
twitter feed --input tweets.json
twitter feed --json                    # Structured stdout for scripts/agents

# Bookmarks
twitter bookmarks
twitter bookmarks --full-text
twitter bookmarks --max 30 --yaml

# Search
twitter search "Claude Code"
twitter search "AI agent" -t Latest --max 50
twitter search "AI agent" --full-text
twitter search "机器学习" --yaml
twitter search "python" --from elonmusk --lang en --since 2026-01-01
twitter search --from bbc --exclude retweets --has links
twitter search "topic" -o results.json         # Save to file
twitter search "trending" --filter              # Apply ranking filter

# Tweet detail (view tweet + replies)
twitter tweet 1234567890
twitter tweet 1234567890 --full-text
twitter tweet https://x.com/user/status/1234567890

# Twitter Article
twitter article 1234567890
twitter article https://x.com/user/article/1234567890 --json
twitter article 1234567890 --markdown
twitter article 1234567890 --output article.md

# List timeline
twitter list 1539453138322673664
twitter list 1539453138322673664 --full-text

# User
twitter user elonmusk
twitter user-posts elonmusk --max 20
twitter user-posts elonmusk --full-text
twitter user-posts elonmusk -o tweets.json
twitter likes elonmusk --max 30          # ⚠️ own likes only (private since Jun 2024)
twitter likes elonmusk --full-text
twitter likes elonmusk -o likes.json
twitter followers elonmusk --max 50
twitter following elonmusk --max 50

# Write operations
twitter post "Hello from twitter-cli!"
twitter post "Hello!" --image photo.jpg            # Post with image
twitter post "Gallery" -i a.png -i b.jpg -i c.webp  # Up to 4 images
twitter post "reply text" --reply-to 1234567890
twitter reply 1234567890 "Nice!" -i screenshot.png  # Reply with image
twitter quote 1234567890 "Look" -i chart.png        # Quote with image
twitter post "Hello from twitter-cli!" --json
twitter delete 1234567890
twitter like 1234567890
twitter like 1234567890 --yaml
twitter unlike 1234567890
twitter retweet 1234567890
twitter unretweet 1234567890
twitter bookmark 1234567890
twitter unbookmark 1234567890
twitter follow elonmusk --json

# Dry-run (preview without executing, no auth needed)
twitter post "test" --dry-run
twitter post "test" --dry-run --json
twitter like 123 --dry-run --yaml
twitter follow alice --dry-run

# Confirmation control
twitter post "test"           # Shows preview + Send? [Y/n] in TTY
twitter post "test" --yes     # Skip confirmation prompt
```

### Authentication

twitter-cli uses this auth priority:

1. **Environment variables**: `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. **Browser cookies** (recommended): auto-extract from Arc/Chrome/Edge/Firefox/Brave

Browser extraction is recommended — it forwards ALL Twitter cookies (not just `auth_token` + `ct0`) and aligns request headers with your local runtime, which is closer to normal browser traffic than minimal cookie auth.

**Chrome multi-profile**: All Chrome profiles are scanned automatically. To specify a profile:

```bash
TWITTER_CHROME_PROFILE="Profile 2" twitter feed
```

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
# Prompt for confirmation before posting/replying/quoting (TTY only)
confirm: true

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
- Rich table output truncates long tweet text by default; use `--full-text` to show full body text in list views

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

### Output Modes

- Use the default rich table for interactive reading
- Use `--full-text` when reading long posts in terminal tables
- Use `--yaml` or `--json` for scripts and agent pipelines
- Use `-c` / `--compact` when token efficiency matters more than completeness

### Troubleshooting

- `No Twitter cookies found`
  - Ensure you are logged in to `x.com` in a supported browser (Arc/Chrome/Edge/Firefox/Brave).
  - Or set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` manually.
  - Run with `-v` to see browser extraction diagnostics.

- `Cookie expired or invalid (HTTP 401/403)`
  - Re-login to `x.com` and retry.

- `Unable to get key for cookie decryption` (macOS Keychain)
  - **SSH sessions**: Keychain is locked by default over SSH. Run:
    ```bash
    security unlock-keychain ~/Library/Keychains/login.keychain-db
    ```
  - **Local terminal**: Open **Keychain Access** → search for **"\<Browser\> Safe Storage"** → **Access Control** → add your Terminal app → **Save Changes**.
  - Or click **"Always Allow"** when the Keychain authorization popup appears.

- `Twitter API error 404`
  - This can happen when upstream GraphQL query IDs rotate.
  - Retry the command; the client attempts a live queryId fallback.

- `Invalid tweet JSON file`
  - Regenerate input using `twitter feed --json > tweets.json`.

**Diagnostics command**: Run `twitter doctor` to output a full diagnostic report (version, OS, browser detection, Keychain status, cookie extraction results). Paste this output into bug reports.

Structured error codes commonly include `not_authenticated`, `not_found`, `invalid_input`, `rate_limited`, and `api_error`.

### Development

```bash
# Install dev dependencies
uv sync --extra dev

# Lint + tests
uv run ruff check .
uv run pytest -q
```

Current CI validates the project on Python 3.8, 3.10, and 3.12.

### Project Structure

```text
twitter_cli/
├── __init__.py
├── cli.py
├── client.py
├── graphql.py       # GraphQL query IDs, URL building, JS bundle scanning
├── parser.py        # Tweet, User, Media parsing logic
├── auth.py
├── config.py
├── constants.py
├── exceptions.py
├── filter.py
├── formatter.py
├── output.py
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
- 文章读取：获取 Twitter 长文，并导出为 Markdown
- 列表时间线：获取 Twitter List 的推文
- 用户查询：查看用户资料、推文、点赞、粉丝和关注
- `--full-text`：在 rich table 输出里关闭推文正文截断
- 结构化输出：支持 YAML 和 JSON，便于脚本处理和 AI agent 集成

> **AI Agent 提示：** 需要结构化输出时优先使用 `--yaml`，除非下游必须是 JSON。stdout 不是 TTY 时默认输出 YAML。用 `--max` 控制返回数量。

**写入:**
- 发推：发布新推文和回复，支持附带图片（最多 4 张，支持 JPEG/PNG/GIF/WebP）
- 引用推文：带评论的转发，也支持附带图片
- 删除：删除自己的推文
- 点赞 / 取消点赞
- 转推 / 取消转推
- 书签 / 取消书签：bookmark/unbookmark（保留 `favorite/unfavorite` 兼容别名）
- 写操作现在也显式支持 `--json` / `--yaml`
- `--dry-run`：预览将要发送的内容，不实际执行（所有写操作均支持）
- 交互确认：发推/回复/引用会在 TTY 模式下显示预览 + `Send? [Y/n]` 提示（可用 `--yes` / `-y` 或 config 中 `confirm: false` 跳过）

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

升级到最新版本：

```bash
uv tool upgrade twitter-cli
# 或：pipx upgrade twitter-cli
```

> **提示：** 建议定期升级，避免因版本过旧导致的 API 调用异常。

### 使用指南

```bash
# 时间线
twitter feed
twitter feed -t following
twitter feed --filter
twitter feed --full-text

# 收藏
twitter bookmarks
twitter bookmarks --full-text

# 搜索
twitter search "Claude Code"
twitter search "AI agent" -t Latest --max 50
twitter search "AI agent" --full-text
twitter search "topic" -o results.json         # 保存到文件
twitter search "trending" --filter              # 启用排序筛选

# 推文详情
twitter tweet 1234567890
twitter tweet 1234567890 --full-text

# Twitter 长文
twitter article 1234567890
twitter article https://x.com/user/article/1234567890 --json
twitter article 1234567890 --markdown
twitter article 1234567890 --output article.md

# 列表时间线
twitter list 1539453138322673664
twitter list 1539453138322673664 --full-text

# 用户
twitter user elonmusk
twitter user-posts elonmusk --max 20
twitter user-posts elonmusk --full-text
twitter user-posts elonmusk -o tweets.json
twitter likes elonmusk --max 30           # ⚠️ 仅可查看自己的点赞（2024年6月起平台已私密化）
twitter likes elonmusk --full-text
twitter likes elonmusk -o likes.json
twitter followers elonmusk
twitter following elonmusk

# 写操作
twitter post "你好，世界！"
twitter post "发图" --image photo.jpg              # 带图发推
twitter post "多图" -i a.png -i b.jpg -i c.webp    # 最多 4 张图片
twitter post "回复内容" --reply-to 1234567890
twitter reply 1234567890 "回复" -i screenshot.png   # 带图回复
twitter quote 1234567890 "评论" -i chart.png        # 带图引用
twitter post "你好，世界！" --json
twitter delete 1234567890
twitter like 1234567890
twitter like 1234567890 --yaml
twitter unlike 1234567890
twitter retweet 1234567890
twitter unretweet 1234567890
twitter bookmark 1234567890
twitter unbookmark 1234567890
twitter follow elonmusk --json

# 试运行（预览不执行，无需认证）
twitter post "测试" --dry-run
twitter post "测试" --dry-run --json
twitter like 123 --dry-run --yaml
twitter follow alice --dry-run

# 确认控制
twitter post "测试"           # TTY 下显示预览 + Send? [Y/n]
twitter post "测试" --yes     # 跳过确认提示
```

### 认证说明

认证优先级：

1. **环境变量**：`TWITTER_AUTH_TOKEN` + `TWITTER_CT0`
2. **浏览器提取**（推荐）：Arc/Chrome/Edge/Firefox/Brave 全量 Cookie 提取

推荐使用浏览器提取方式，会转发所有 Twitter Cookie，并按本机运行环境生成语言和平台请求头；它比仅发送 `auth_token` + `ct0` 更接近普通浏览器流量，但不等于完整浏览器自动化。

**Chrome 多 Profile 支持**：会自动遍历所有 Chrome profile。也可以通过环境变量指定：

```bash
TWITTER_CHROME_PROFILE="Profile 2" twitter feed
```

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

rich table 输出默认会截断较长正文；如果需要在列表视图中查看完整正文，可加 `--full-text`。

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
- 报错 `Unable to get key for cookie decryption`（macOS Keychain 问题）：
  - **SSH 远程登录**：Keychain 默认锁定，需手动解锁：
    ```bash
    security unlock-keychain ~/Library/Keychains/login.keychain-db
    ```
  - **本地终端**：打开 **钥匙串访问** → 搜索 **"\<浏览器\> Safe Storage"** → **访问控制** → 添加你的终端 app → **保存更改**。
  - 或在弹出 Keychain 授权时点击 **"始终允许"**。
- 报错 `Twitter API error 404`：通常是 queryId 轮换，重试即可。

**诊断命令**：运行 `twitter doctor` 可输出完整诊断报告（版本、OS、浏览器检测、Keychain 状态、cookie 提取结果），方便提交 bug report。

- 结构化错误码通常会区分 `not_authenticated`、`not_found`、`invalid_input`、`rate_limited`、`api_error`。

### 使用建议（防封号）

- **使用代理** — 设置 `TWITTER_PROXY`，避免裸 IP 直连
- **控制请求量** — 用 `--max 20` 而不是 `--max 500`
- **避免频繁启动** — 每次启动都会访问 x.com 初始化反检测请求头
- **使用浏览器 Cookie 提取** — 提供完整 Cookie 指纹
- **避免数据中心 IP** — 住宅代理更安全
- Cookie 仅在本地使用，不会被本工具上传

### 输出模式建议

- 默认 rich table 适合终端交互式浏览
- 需要在表格里看完整正文时，使用 `--full-text`
- 需要脚本消费时，优先使用 `--yaml` 或 `--json`
- 需要节省 token 时，使用 `-c` / `--compact`

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
- [xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli) — 小红书笔记与账号工作流 CLI
