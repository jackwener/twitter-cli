from __future__ import annotations

import re

from twitter_cli.article import article_markdown_to_content_state


def test_article_markdown_to_content_state_blocks_and_link_entity() -> None:
    content_state = article_markdown_to_content_state(
        "# Title\n\n"
        "Paragraph with [docs](https://example.com) link.\n"
        "- item\n"
        "> quote\n"
        "---\n"
        "```python\n"
        "print('hi')\n"
        "```\n"
    )

    blocks = content_state["blocks"]
    assert [block["type"] for block in blocks] == [
        "header-one",
        "unstyled",
        "unordered-list-item",
        "blockquote",
        "atomic",
        "atomic",
    ]
    assert blocks[0]["text"] == "Title"
    assert blocks[1]["text"] == "Paragraph with docs link."

    entities = content_state["entity_map"]
    assert entities[0]["value"]["type"] == "LINK"
    assert entities[0]["value"]["data"] == {"url": "https://example.com"}
    assert blocks[1]["entity_ranges"] == [{"key": 0, "offset": 15, "length": 4}]

    assert entities[1]["value"]["type"] == "DIVIDER"
    assert entities[2]["value"]["type"] == "MARKDOWN"
    assert "print('hi')" in entities[2]["value"]["data"]["markdown"]


def test_article_markdown_to_content_state_empty_body_has_empty_block() -> None:
    content_state = article_markdown_to_content_state("\n\n")

    assert content_state["entity_map"] == []
    assert len(content_state["blocks"]) == 1
    block = content_state["blocks"][0]
    assert block["data"] == {}
    assert block["text"] == ""
    assert block["type"] == "unstyled"
    assert block["entity_ranges"] == []
    assert block["inline_style_ranges"] == []
    assert re.fullmatch(r"[0-9a-f]{5}", block["key"])
