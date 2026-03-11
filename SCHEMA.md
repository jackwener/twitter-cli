# Structured Output Schema

`twitter-cli` uses a shared agent-friendly envelope for machine-readable output.

## Success

```yaml
ok: true
schema_version: "1"
data: ...
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
- `status` returns `data.authenticated` plus `data.user`
- `whoami` returns `data.user`
- `article` returns a **single tweet object** (not an array) directly under `data`
- write commands also support explicit `--json` / `--yaml`

## Article Fields

`twitter article <id>` returns a standard tweet object with two additional fields:

```yaml
ok: true
schema_version: "1"
data:
  id: "1234567890"
  text: "https://t.co/..."        # Short URL pointing to the article
  author: { ... }                 # Standard author object
  metrics: { ... }                # Standard engagement metrics
  articleTitle: "Article Title"   # Present only on article tweets; null otherwise
  articleText: |                  # Full article body converted to Markdown
    ## Heading
    Paragraph text...
  # ... all other standard tweet fields
```

`articleTitle` and `articleText` are `null` on regular (non-article) tweets.

## Error Codes

Common structured error codes:

- `not_authenticated`
- `not_found`
- `invalid_input`
- `rate_limited`
- `api_error`
