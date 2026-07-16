"""Tests for Twitter Article (long-form) Draft.js parsing enhancements.

Covers:
- _render_article_text_block: inline styles (Bold/Italic/Code/Strikethrough),
  entity links, and mixed style+link on the same block.
- _extract_atomic_content: DIVIDER, TWEET, MARKDOWN entity types.
- _parse_article: end-to-end with synthetic article data.
"""

from __future__ import annotations


from twitter_cli.parser import (
    _extract_atomic_content,
    _normalize_article_entity_map,
    _parse_article,
    _render_article_text_block,
)


# ── _render_article_text_block ──────────────────────────────────────────


class TestRenderArticleTextBlock:
    def test_plain_text_unchanged(self):
        block = {"text": "hello world", "entityRanges": [], "inlineStyleRanges": []}
        assert _render_article_text_block(block, {}) == "hello world"

    def test_empty_text(self):
        assert _render_article_text_block({"text": "", "entityRanges": [], "inlineStyleRanges": []}, {}) == ""

    def test_bold(self):
        block = {
            "text": "LEVEL 1 — ONE-SHOT PROMPTS",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 0, "length": 26, "style": "Bold"}],
        }
        result = _render_article_text_block(block, {})
        assert result == "**LEVEL 1 — ONE-SHOT PROMPTS**"

    def test_italic(self):
        block = {
            "text": "emphasis here",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 9, "length": 4, "style": "Italic"}],
        }
        result = _render_article_text_block(block, {})
        assert result == "emphasis *here*"

    def test_code(self):
        block = {
            "text": "run npm install",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 4, "length": 11, "style": "Code"}],
        }
        result = _render_article_text_block(block, {})
        assert result == "run `npm install`"

    def test_strikethrough(self):
        block = {
            "text": "old text new",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 0, "length": 7, "style": "Strikethrough"}],
        }
        result = _render_article_text_block(block, {})
        assert result == "~~old tex~~t new"  # length 7 from offset 0

    def test_style_case_insensitive(self):
        """Twitter API returns 'Bold' (Title case), not 'BOLD'."""
        block = {
            "text": "hello",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 0, "length": 5, "style": "bold"}],
        }
        assert "**hello**" == _render_article_text_block(block, {})

    def test_out_of_bounds_style_ignored(self):
        block = {
            "text": "short",
            "entityRanges": [],
            "inlineStyleRanges": [{"offset": 0, "length": 100, "style": "Bold"}],
        }
        assert _render_article_text_block(block, {}) == "short"

    def test_link(self):
        block = {
            "text": "Click here for more",
            "entityRanges": [{"key": 0, "offset": 6, "length": 4}],
            "inlineStyleRanges": [],
        }
        entity_map = {"0": {"type": "LINK", "data": {"url": "https://example.com"}}}
        result = _render_article_text_block(block, entity_map)
        assert result == "Click [here](https://example.com) for more"

    def test_link_with_paren_in_url(self):
        block = {
            "text": "see Wikipedia",
            "entityRanges": [{"key": 0, "offset": 4, "length": 9}],
            "inlineStyleRanges": [],
        }
        entity_map = {"0": {"type": "LINK", "data": {"url": "https://en.wikipedia.org/wiki/Test_(page)"}}}
        result = _render_article_text_block(block, entity_map)
        assert "%29" in result  # ) should be encoded

    def test_bold_and_link_mixed(self):
        """The key regression: bold and link on the same block must not corrupt each other's offsets."""
        block = {
            "text": "Click here for more",
            "entityRanges": [{"key": 0, "offset": 6, "length": 4}],
            "inlineStyleRanges": [{"offset": 0, "length": 5, "style": "Bold"}],
        }
        entity_map = {"0": {"type": "LINK", "data": {"url": "https://example.com"}}}
        result = _render_article_text_block(block, entity_map)
        assert "**Click**" in result
        assert "[here](https://example.com)" in result

    def test_multiple_non_overlapping_styles(self):
        block = {
            "text": "bold text and italic text",
            "entityRanges": [],
            "inlineStyleRanges": [
                {"offset": 0, "length": 4, "style": "Bold"},
                {"offset": 14, "length": 6, "style": "Italic"},
            ],
        }
        result = _render_article_text_block(block, {})
        assert result == "**bold** text and *italic* text"


# ── _extract_atomic_content ─────────────────────────────────────────────


class TestExtractAtomicContent:
    def test_divider(self):
        block = {"entityRanges": [{"key": 0, "length": 1, "offset": 0}]}
        entity_map = {"0": {"type": "DIVIDER", "data": {}}}
        assert _extract_atomic_content(block, entity_map) == ["---"]

    def test_embedded_tweet(self):
        block = {"entityRanges": [{"key": 0, "length": 1, "offset": 0}]}
        entity_map = {"0": {"type": "TWEET", "data": {"tweetId": "123456"}}}
        result = _extract_atomic_content(block, entity_map)
        assert len(result) == 1
        assert "Embedded Tweet" in result[0]
        assert "123456" in result[0]
        assert "https://x.com/i/status/123456" in result[0]

    def test_markdown_block(self):
        block = {"entityRanges": [{"key": 0, "length": 1, "offset": 0}]}
        entity_map = {"0": {"type": "MARKDOWN", "data": {"markdown": "# Heading"}}}
        assert _extract_atomic_content(block, entity_map) == ["# Heading"]

    def test_unknown_type_ignored(self):
        block = {"entityRanges": [{"key": 0, "length": 1, "offset": 0}]}
        entity_map = {"0": {"type": "UNKNOWN", "data": {}}}
        assert _extract_atomic_content(block, entity_map) == []

    def test_empty_entity_ranges(self):
        assert _extract_atomic_content({"entityRanges": []}, {}) == []

    def test_multiple_entities(self):
        block = {"entityRanges": [{"key": 0, "length": 1, "offset": 0}, {"key": 1, "length": 1, "offset": 2}]}
        entity_map = {
            "0": {"type": "DIVIDER", "data": {}},
            "1": {"type": "TWEET", "data": {"tweetId": "999"}},
        }
        result = _extract_atomic_content(block, entity_map)
        assert len(result) == 2
        assert result[0] == "---"
        assert "999" in result[1]


# ── _normalize_article_entity_map ───────────────────────────────────────


class TestNormalizeEntityMap:
    def test_list_format(self):
        """Twitter API returns entityMap as [{key, value}, ...]."""
        raw = [{"key": "0", "value": {"type": "LINK", "data": {}}}]
        result = _normalize_article_entity_map(raw)
        assert result == {"0": {"type": "LINK", "data": {}}}

    def test_dict_format(self):
        raw = {"0": {"type": "LINK", "data": {}}}
        result = _normalize_article_entity_map(raw)
        assert result == {"0": {"type": "LINK", "data": {}}}

    def test_empty(self):
        assert _normalize_article_entity_map([]) == {}
        assert _normalize_article_entity_map({}) == {}


# ── _parse_article (end-to-end with synthetic data) ─────────────────────


class TestParseArticle:
    def _make_article(self, blocks, entity_map=None, media_entities=None):
        return {
            "article": {
                "article_results": {
                    "result": {
                        "title": "Test Article",
                        "content_state": {
                            "blocks": blocks,
                            "entityMap": entity_map or [],
                        },
                        "media_entities": media_entities or [],
                        "cover_media": {},
                    }
                }
            }
        }

    def test_basic_blocks(self):
        blocks = [
            {"type": "header-two", "text": "Section", "entityRanges": [], "inlineStyleRanges": []},
            {"type": "unstyled", "text": "Body text", "entityRanges": [], "inlineStyleRanges": []},
            {"type": "blockquote", "text": "A quote", "entityRanges": [], "inlineStyleRanges": []},
        ]
        result = _parse_article(self._make_article(blocks))
        text = result["article_text"]
        assert "## Section" in text
        assert "Body text" in text
        assert "> A quote" in text

    def test_divider_between_sections(self):
        blocks = [
            {"type": "unstyled", "text": "Before", "entityRanges": [], "inlineStyleRanges": []},
            {"type": "atomic", "text": " ", "entityRanges": [{"key": 0, "length": 1, "offset": 0}], "inlineStyleRanges": []},
            {"type": "unstyled", "text": "After", "entityRanges": [], "inlineStyleRanges": []},
        ]
        entity_map = [{"key": "0", "value": {"type": "DIVIDER", "data": {}}}]
        result = _parse_article(self._make_article(blocks, entity_map))
        text = result["article_text"]
        assert "Before" in text
        assert "---" in text
        assert "After" in text

    def test_image_with_media_entities(self):
        blocks = [
            {"type": "atomic", "text": " ", "entityRanges": [{"key": 0, "length": 1, "offset": 0}], "inlineStyleRanges": []},
        ]
        entity_map = [{
            "key": "0",
            "value": {
                "type": "MEDIA",
                "data": {
                    "caption": "A screenshot",
                    "mediaItems": [{"mediaId": "111"}],
                },
            },
        }]
        media_entities = [{
            "media_id": "111",
            "media_info": {"original_img_url": "https://pbs.twimg.com/media/test.jpg"},
        }]
        result = _parse_article(self._make_article(blocks, entity_map, media_entities))
        text = result["article_text"]
        assert "![A screenshot](https://pbs.twimg.com/media/test.jpg)" in text

    def test_no_article_returns_none(self):
        result = _parse_article({"legacy": {}})
        assert result["article_title"] is None
        assert result["article_text"] is None
