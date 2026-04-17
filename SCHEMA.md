# Structured Output Schema

`twitter-cli` uses a shared agent-friendly envelope for machine-readable output.

## Success

```yaml
ok: true
schema_version: "1"
data: ...
pagination:
  nextCursor: "optional-cursor"
```

## Error

```yaml
ok: false
schema_version: "1"
error:
  code: api_error
  message: User @foo not found
```

## Notes

- `--yaml` and `--json` both use this envelope
- non-TTY stdout defaults to YAML
- tweet and user lists are returned under `data`
- timeline-style list commands may also return `pagination.nextCursor`
- `article` returns a single tweet object under `data`
- `status` returns `data.authenticated` plus `data.user`
- `whoami` returns `data.user`
- write commands also support explicit `--json` / `--yaml`

## Article Fields

`twitter article <id> --json` returns the standard tweet object plus:

```yaml
data:
  id: "1234567890"
  articleTitle: "Article Title"
  articleText: |
    # Heading
    Body text...
```

## Long-form Detection Flags

Every tweet object carries two boolean flags that identify long-form content
in search/timeline/feed responses without a second API call:

- `isArticle` — `true` when the tweet is a Twitter Article. Full rich content
  still requires `twitter article <id>` to fetch; this flag just tells you
  it's worth fetching.
- `isNoteTweet` — `true` when the tweet is a long-form "note tweet"
  (longer than the 280-char classic limit). The full text is already in the
  `text` field — no extra call needed.

```yaml
data:
  - id: "1234567890"
    text: "Long tweet body..."
    isArticle: false
    isNoteTweet: true
```

## Error Codes

Common structured error codes:

- `not_authenticated`
- `not_found`
- `invalid_input`
- `rate_limited`
- `api_error`
