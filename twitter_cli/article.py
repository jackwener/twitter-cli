"""Helpers for composing X Article content_state payloads."""

from __future__ import annotations

import hashlib
import re
from typing import Any


DraftBlock = dict[str, Any]
DraftEntity = dict[str, Any]
DraftContentState = dict[str, Any]


def _block_key(seed: str, index: int) -> str:
    return hashlib.sha1(("%s:%d" % (seed, index)).encode("utf-8")).hexdigest()[:5]


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _plain_block(block_type: str, text: str, key: str) -> DraftBlock:
    return {
        "data": {},
        "text": text,
        "key": key,
        "type": block_type,
        "entity_ranges": [],
        "inline_style_ranges": [],
    }


def _atomic_block(entity_key: int, key: str) -> DraftBlock:
    return {
        "data": {},
        "text": " ",
        "key": key,
        "type": "atomic",
        "entity_ranges": [{"key": entity_key, "offset": 0, "length": 1}],
        "inline_style_ranges": [],
    }


def _append_entity(content_state: DraftContentState, entity_type: str, mutability: str, data: dict[str, Any]) -> int:
    entity_map = content_state["entity_map"]
    key = len(entity_map)
    entity_map.append(
        {
            "key": str(key),
            "value": {
                "data": data,
                "type": entity_type,
                "mutability": mutability,
            },
        }
    )
    return key


def _extract_links(text: str, content_state: DraftContentState) -> tuple[str, list[dict[str, int]]]:
    ranges: list[dict[str, int]] = []
    output: list[str] = []
    cursor = 0
    for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        output.append(text[cursor:match.start()])
        label = match.group(1)
        url = match.group(2)
        offset = _utf16_len("".join(output))
        output.append(label)
        entity_key = _append_entity(content_state, "LINK", "Mutable", {"url": url})
        ranges.append({"key": entity_key, "offset": offset, "length": _utf16_len(label)})
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output), ranges


def _strip_inline_markup(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def article_markdown_to_content_state(markdown: str) -> DraftContentState:
    """Convert a conservative Markdown subset into X Article content_state.

    Supported block forms: paragraphs, #/## headings, unordered and ordered
    list items, blockquotes, horizontal rules, fenced code blocks, and inline
    Markdown links. Unsupported Markdown is emitted as plain paragraph text.
    """
    content_state: DraftContentState = {"blocks": [], "entity_map": []}
    lines = markdown.splitlines()
    i = 0
    block_index = 0

    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        i += 1
        if not stripped:
            continue

        if stripped.startswith("```"):
            code_lines: list[str] = []
            opening = stripped
            while i < len(lines):
                code_line = lines[i].rstrip()
                i += 1
                if code_line.strip().startswith("```"):
                    break
                code_lines.append(code_line)
            markdown_block = opening + "\n" + "\n".join(code_lines) + "\n```"
            entity_key = _append_entity(content_state, "MARKDOWN", "Mutable", {"markdown": markdown_block})
            content_state["blocks"].append(_atomic_block(entity_key, _block_key(markdown_block, block_index)))
            block_index += 1
            continue

        if stripped == "---":
            entity_key = _append_entity(content_state, "DIVIDER", "Immutable", {})
            content_state["blocks"].append(_atomic_block(entity_key, _block_key(stripped, block_index)))
            block_index += 1
            continue

        block_type = "unstyled"
        text = stripped
        if stripped.startswith("# "):
            block_type = "header-one"
            text = stripped[2:].strip()
        elif stripped.startswith("## "):
            block_type = "header-two"
            text = stripped[3:].strip()
        elif stripped.startswith("> "):
            block_type = "blockquote"
            text = stripped[2:].strip()
        elif stripped.startswith(("- ", "* ")):
            block_type = "unordered-list-item"
            text = stripped[2:].strip()
        else:
            ordered = re.match(r"^\d+\.\s+(.+)$", stripped)
            if ordered:
                block_type = "ordered-list-item"
                text = ordered.group(1).strip()

        text, entity_ranges = _extract_links(_strip_inline_markup(text), content_state)
        block = _plain_block(block_type, text, _block_key(text, block_index))
        block["entity_ranges"] = entity_ranges
        content_state["blocks"].append(block)
        block_index += 1

    if not content_state["blocks"]:
        content_state["blocks"].append(_plain_block("unstyled", "", _block_key("", 0)))

    return content_state
