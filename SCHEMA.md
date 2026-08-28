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

## Sensitive Media Fields

Tweet objects in `--json` / `--yaml` include Twitter's sensitive-media labels so
frontends can skip inlining adult or graphic photos:

```yaml
data:
  - id: "1234567890"
    possiblySensitive: true
    media:
      - type: photo
        url: https://pbs.twimg.com/media/example.jpg
        width: 1200
        height: 800
        adultContent: true
        graphicViolence: false
        otherWarning: false
```

- `possiblySensitive` is the tweet-level `legacy.possibly_sensitive` flag
- media warning fields come from `ext_sensitive_media_warning` /
  `sensitive_media_warning` on the media entity (false when absent)
- quoted tweets include `possiblySensitive` on `quotedTweet`

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

## Error Codes

Common structured error codes:

- `not_authenticated`
- `not_found`
- `invalid_input`
- `rate_limited`
- `api_error`
