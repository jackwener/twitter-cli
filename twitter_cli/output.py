"""Shared structured output helpers for twitter-cli."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

import click
import yaml

_OUTPUT_ENV = "OUTPUT"
_SCHEMA_VERSION = "1"


def default_structured_format(*, as_json: bool, as_yaml: bool) -> str | None:
    """Resolve explicit flags first, then env override, then TTY default."""
    if as_json and as_yaml:
        raise click.UsageError("Use only one of --json or --yaml.")
    if as_yaml:
        return "yaml"
    if as_json:
        return "json"

    output_mode = os.getenv(_OUTPUT_ENV, "auto").strip().lower()
    if output_mode == "yaml":
        return "yaml"
    if output_mode == "json":
        return "json"
    if output_mode == "rich":
        return None

    if not sys.stdout.isatty():
        return "yaml"
    return None


def use_rich_output(*, as_json: bool, as_yaml: bool, compact: bool = False) -> bool:
    """Return True when human-readable rich output should be used."""
    if compact:
        return False
    return default_structured_format(as_json=as_json, as_yaml=as_yaml) is None


def structured_output_options(command: Callable) -> Callable:
    """Add --json/--yaml options to a Click command."""
    command = click.option("--yaml", "as_yaml", is_flag=True, help="Output as YAML.")(command)
    command = click.option("--json", "as_json", is_flag=True, help="Output as JSON.")(command)
    return command


def _emit_payload(payload: Any, fmt: str) -> None:
    """Serialize and echo a payload as JSON or YAML."""
    if fmt == "json":
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(
            yaml.safe_dump(
                payload,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        )


def emit_structured(data: Any, *, as_json: bool, as_yaml: bool) -> bool:
    """Emit structured output and return True when used."""
    fmt = default_structured_format(as_json=as_json, as_yaml=as_yaml)
    if not fmt:
        return False
    _emit_payload(_normalize_success_payload(data), fmt)
    return True


def success_payload(data: Any) -> dict[str, Any]:
    """Wrap structured success data in the shared agent schema."""
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "data": data,
    }


def error_payload(code: str, message: str, *, details: Any | None = None) -> dict[str, Any]:
    """Wrap structured error data in the shared agent schema."""
    error = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "schema_version": _SCHEMA_VERSION,
        "error": error,
    }


def _normalize_success_payload(data: Any) -> Any:
    """Wrap plain structured data in the shared agent success schema."""
    if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION and "ok" in data:
        return data
    return success_payload(data)


def emit_error(
    code: str,
    message: str,
    *,
    as_json: bool | None = None,
    as_yaml: bool | None = None,
    details: Any | None = None,
) -> bool:
    """Emit a structured error when the active output mode is machine-readable."""
    if as_json is None or as_yaml is None:
        ctx = click.get_current_context(silent=True)
        params = ctx.params if ctx is not None else {}
        as_json = bool(params.get("as_json", False)) if as_json is None else as_json
        as_yaml = bool(params.get("as_yaml", False)) if as_yaml is None else as_yaml

    fmt = default_structured_format(as_json=bool(as_json), as_yaml=bool(as_yaml))
    if fmt is None:
        return False

    _emit_payload(error_payload(code, message, details=details), fmt)
    return True
