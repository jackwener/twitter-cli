# AGENTS.md — Agent Developer Guide for twitter-cli

This file provides context for AI agents working in this repository.

## Project Overview

- **Project**: twitter-cli — A CLI for Twitter/X (read timelines, bookmarks, search, post, reply, etc.)
- **Language**: Python 3.10+
- **Package Manager**: uv (recommended) / pip
- **Repository**: https://github.com/jackwener/twitter-cli

## Build, Lint, and Test Commands

### Installation

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Or using pip
pip install -e ".[dev]"
```

### Linting

```bash
# Run ruff linter
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .
```

### Type Checking

```bash
# Run mypy type checker
uv run mypy twitter_cli
```

### Testing

```bash
# Run all tests (excludes smoke tests by default)
uv run pytest -q

# Run all tests including smoke tests (real API integration)
uv run pytest -m smoke

# Run a specific test file
uv run pytest tests/test_parser_fixtures.py -v

# Run a single test function
uv run pytest tests/test_parser_fixtures.py::test_parse_tweet_basic -v

# Run tests matching a pattern
uv run pytest -k "test_parse" -v

# Run with coverage
uv run pytest --cov=twitter_cli --cov-report=term-missing
```

### Running the CLI

```bash
# After installation
twitter --help

# Or run directly
uv run twitter --help
```

## Code Style Guidelines

### General

- **Line length**: 100 characters (configured in pyproject.toml)
- **Python version**: 3.10+ (minimum supported)
- **Use `from __future__ import annotations`** at the top of all .py files for forward references

### Imports

- Standard library imports first
- Third-party imports second
- Local imports third
- Group imports by type with blank lines between groups
- Use explicit relative imports for local modules (e.g., `from .auth import get_cookies`)

```python
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from rich.console import Console

from .auth import get_cookies
from .client import TwitterClient
```

### Naming Conventions

- **Functions/variables**: `snake_case` (e.g., `load_config`, `tweet_count`)
- **Classes**: `PascalCase` (e.g., `TwitterClient`, `TweetMedia`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `DEFAULT_CONFIG`, `MAX_RETRIES`)
- **Private functions**: Prefix with underscore (e.g., `_resolve_config_path`)

### Type Annotations

- Use Python 3.10+ union syntax: `str | None` instead of `Optional[str]`
- Use `list[...]`, `dict[...]` instead of `List[...]`, `Dict[...]` (with `from __future__ import annotations`)
- Add return type annotations to all functions

```python
def load_config(config_path: str | None) -> dict[str, Any]:
    """Load and normalize config from YAML."""
    ...
```

### Data Models

- Use `@dataclass` from the standard library for simple data models
- Use `field(default_factory=list)` for mutable defaults
- Keep models in `models.py`

```python
@dataclass
class Tweet:
    id: str
    text: str
    author: Author
    metrics: Metrics
    created_at: str
    media: list[TweetMedia] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
```

### Error Handling

- Use custom exception hierarchy in `exceptions.py`
- Base class: `TwitterError(RuntimeError)`
- Specific exceptions: `AuthenticationError`, `RateLimitError`, `NotFoundError`, `NetworkError`, `QueryIdError`, `MediaUploadError`, `TwitterAPIError`

```python
class TwitterError(RuntimeError):
    """Base exception for twitter-cli errors."""

class AuthenticationError(TwitterError):
    """Raised when cookies are missing, expired, or invalid."""
```

### CLI Structure (click)

- Use Click framework for CLI commands
- Group related commands with `@cli.group()`
- Use `@click.option()` for flags and arguments
- Use `click.echo()` for output, `console.print()` for rich output

```python
import click
from rich.console import Console

console = Console(stderr=True)

@click.command()
@click.option("--max", "-m", default=50, help="Maximum number of tweets")
@click.argument("query")
def search(query: str, max: int) -> None:
    """Search for tweets."""
    ...
```

### Testing

- Test files: `tests/test_*.py`
- Use pytest fixtures defined in `conftest.py`
- Use `tweet_factory` fixture to create test tweets
- Use `fixture_loader` to load JSON fixtures from `tests/fixtures/`
- Mark integration tests with `@pytest.mark.smoke` (run separately with `-m smoke`)

```python
def test_parse_tweet_basic(tweet_factory):
    tweet = tweet_factory("123", text="Hello world")
    assert tweet.id == "123"
    assert tweet.text == "Hello world"
```

### Configuration

- Default config in `config.py` as `DEFAULT_CONFIG` constant
- Load from `config.yaml` in current working directory or project root
- Use YAML for configuration files

### Output Formats

- **Rich tables**: Default for interactive terminal use
- **YAML/JSON**: Use `--yaml` or `--json` flags for scripting
- **Compact**: Use `-c` for minimal token output (useful for LLM context)
- Non-TTY stdout automatically defaults to YAML

## Project Structure

```
twitter_cli/
├── __init__.py          # Package init, version
├── cli.py               # Click CLI entry point (main commands)
├── client.py            # Twitter API client (HTTP requests)
├── auth.py              # Cookie extraction & authentication
├── graphql.py           # GraphQL query IDs, URL building
├── parser.py            # Tweet/User/Media parsing logic
├── models.py            # Dataclass models (Tweet, UserProfile, etc.)
├── formatter.py         # Rich table formatting
├── serialization.py     # YAML/JSON output conversion
├── output.py            # Structured output helpers
├── config.py            # Config loading & normalization
├── filter.py            # Tweet ranking/scoring
├── constants.py         # Constants
├── exceptions.py        # Custom exception hierarchy
├── cache.py             # Tweet caching
├── search.py            # Search utilities
└── timeutil.py          # Time utilities
```

## Key Files

- **pyproject.toml**: Project config (dependencies, pytest, ruff, mypy settings)
- **SKILL.md**: Agent skill file for AI agents using twitter-cli
- **SCHEMA.md**: Structured output contract
- **config.yaml**: User configuration (not in repo, created in working directory)

## Common Development Tasks

### Adding a new CLI command

1. Add command function in `cli.py`
2. Use `@cli.command()` decorator
3. Add options with `@click.option()`
4. Call client methods and format output

### Adding a new data field to Tweet

1. Add field to `Tweet` dataclass in `models.py`
2. Update parser in `parser.py` to extract field
3. Update serialization in `serialization.py` if needed
4. Add test in `tests/test_parser_fixtures.py`

### Running a specific test

```bash
# Single test
uv run pytest tests/test_cli.py::test_feed_command -v

# Tests in a specific class
uv run pytest tests/test_cli.py::TestFeedCommand -v

# Tests matching pattern
uv run pytest -k "filter" -v
```

## CI/CD

- GitHub Actions runs on Python 3.10, 3.11, 3.12
- CI validates: ruff check + mypy + pytest
- See `.github/workflows/ci.yml`

## Tips for Agents

- Prefer `--yaml` over `--json` for structured output (more forgiving)
- Use `twitter status --yaml` to check authentication before write operations
- Write operations require full browser cookies, not just auth_token + ct0
- Use `twitter -c` (compact) when token efficiency matters for LLM context
