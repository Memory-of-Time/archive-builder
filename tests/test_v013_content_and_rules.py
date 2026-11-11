from __future__ import annotations

import codecs

import pytest

from archive_builder.builder.downloader import classify_replay, decode_document
from archive_builder.config import ExtractionRule, SiteProfile
from archive_builder.extraction import compile_extraction_plan, extract_fields


def _profile(rule: ExtractionRule) -> SiteProfile:
    p = SiteProfile(domain="example.com", content_path="/")
    p.rules = {name: ExtractionRule(method="auto") for name in ("post_number","posted_date","title","category","description")}
    p.rules["description"] = rule
    return p.normalized()


def test_binary_signatures_and_text_under_misleading_mime_are_deliberate():
    assert classify_replay(b"\x89PNG\r\n\x1a\nrest", "text/html")[0] == "known_binary"
    assert classify_replay(b"<!doctype html><html><title>x</title></html>", "application/octet-stream")[0] == "known_text"
    assert classify_replay(b"plain enough printable text " * 20, "application/x-weird")[0] in {"uncertain_text","known_text"}


def test_decoding_handles_bom_utf8_and_windows_1252_meta():
    text, enc = decode_document(codecs.BOM_UTF8 + "café".encode("utf-8"), "text/html; charset=windows-1252")
    assert text == "café"
    assert enc == "utf-8-sig"
    payload = b'<html><head><meta charset="windows-1252"></head><body>caf\xe9</body></html>'
    text, enc = decode_document(payload, "text/html")
    assert "café" in text
    assert enc == "windows-1252"


def test_invalid_rules_fail_before_work():
    p = _profile(ExtractionRule(method="regex", source="text", pattern="("))
    with pytest.raises(ValueError):
        compile_extraction_plan(p)
    p2 = _profile(ExtractionRule(method="text_between", source="text", pattern="Start", end_marker=""))
    with pytest.raises(ValueError):
        compile_extraction_plan(p2)


def test_text_after_has_no_300_character_truncation():
    long_value = "x" * 700
    html = f"<html><body>Description: {long_value}\nNext: value</body></html>"
    p = _profile(ExtractionRule(method="text_after", source="text", pattern="Description:"))
    result = extract_fields(html, "http://example.com/1", p)["description"]
    assert result.value == long_value
    assert len(result.value) == 700
