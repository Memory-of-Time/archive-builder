from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from archive_builder.builder.deduplicate import detect_duplicates, set_duplicate_decision
from archive_builder.builder.downloader import _reject_wayback_error_html
from archive_builder.cdx import indexer
from archive_builder.cdx.client import CDXRows, SharedRateGate, WaybackClient, WaybackError
from archive_builder.cdx.parameters import build_params
from archive_builder.config import NetworkConfig, new_project_config
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import (
    classify_cdx_row,
    save_extraction_result,
    upsert_inventory_rows,
    upsert_representative_rows,
)
from archive_builder.extraction import ExtractionResult


class _Response:
    def __init__(self, status: int, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = b"[]"
        self.text = "[]"
        self.url = httpx.URL("https://web.archive.org/cdx/search/cdx")
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", str(self.url)), response=httpx.Response(self.status_code))


class _SequenceHTTP:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
    def get(self, *args, **kwargs):
        return _Response(next(self.statuses))
    def close(self):
        pass


class _Gate:
    def __init__(self):
        self.waits = 0
        self.pauses = []
    def wait(self, stop_event):
        self.waits += 1
    def pause(self, seconds):
        self.pauses.append(seconds)
    @property
    def starts_per_second(self):
        return 0.0


@pytest.mark.parametrize("status", [429, 503])
def test_shared_gate_receives_rate_limit_pause_and_is_reused(status):
    network = NetworkConfig(retries=2, retry_base_seconds=0.5, retry_max_seconds=1.0)
    stop = threading.Event()
    gate = _Gate()
    first = WaybackClient(network, stop, shared_gate=gate)
    second = WaybackClient(network, stop, shared_gate=gate)
    first.client.close(); second.client.close()
    first.client = _SequenceHTTP([status, 200])
    second.client = _SequenceHTTP([200])
    try:
        assert first._request("https://example.invalid").status_code == 200
        assert second._request("https://example.invalid").status_code == 200
        assert gate.pauses and gate.pauses[0] >= 0.5
        # Retry + the second worker both go through the same shared request gate.
        assert gate.waits == 3
    finally:
        first.close(); second.close()


def test_opaque_resume_token_round_trips_through_httpx_encoding(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    token = "a+b&c%2F日本?nested=http://x/y?a=1&b=2"
    params = build_params(cfg, "inventory", 2000, 2010, token)
    request = httpx.Request("GET", "https://web.archive.org/cdx/search/cdx", params=params)
    decoded = dict(parse_qsl(urlsplit(str(request.url)).query, keep_blank_values=True))
    assert decoded["resumeKey"] == token


def test_full_range_indexing_finishes_in_two_broad_calls_when_no_continuation(tmp_path: Path, monkeypatch):
    cfg = new_project_config(tmp_path)
    cfg.site.from_year = 2000
    cfg.site.to_year = 2010
    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            self.gate = type("G", (), {"starts_per_second": 0.0})()
        def close(self): pass
        def cdx(self, params):
            calls.append(list(params))
            return CDXRows([], None, f"fingerprint-{len(calls)}")
        def num_pages(self, *a, **k):
            raise AssertionError("no continuation means no page-count probe")

    monkeypatch.setattr(indexer, "WaybackClient", FakeClient)
    db = open_database(tmp_path)
    try:
        indexer.index_site(cfg, db, threading.Event())
        assert len(calls) == 2  # inventory + representative, not 11 years x 2
        for params in calls:
            assert ("from", "20000101000000") in params
            assert ("to", "20101231235959") in params
    finally:
        db.close()


def test_repeated_resume_token_stops_without_marking_range_complete(tmp_path: Path, monkeypatch):
    cfg = new_project_config(tmp_path)
    cfg.site.from_year = 2008
    cfg.site.to_year = 2008
    sequence = iter([
        CDXRows([], "opaque-token", "fp-1"),
        CDXRows([], "opaque-token", "fp-2"),
    ])

    class FakeClient:
        def __init__(self, *a, **k):
            self.gate = type("G", (), {"starts_per_second": 0.0})()
        def close(self): pass
        def cdx(self, params): return next(sequence)
        def num_pages(self, params, page_size=9): return 0

    monkeypatch.setattr(indexer, "WaybackClient", FakeClient)
    db = open_database(tmp_path)
    try:
        with pytest.raises(WaybackError, match="continuation token"):
            indexer.index_site(cfg, db, threading.Event())
        row = db.execute("SELECT complete,resume_key FROM index_coverage WHERE phase='inventory'").fetchone()
        assert row is not None
        assert row["complete"] == 0
        assert row["resume_key"] == "opaque-token"
    finally:
        db.close()


def test_out_of_order_overlapping_cdx_rows_preserve_earliest_capture_and_selected_capture(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        key = "com,example)/media/7"
        url = "http://example.com/media/7"
        upsert_inventory_rows(db, [{"urlkey": key, "original": url, "timestamp": "20090101000000"}])
        upsert_inventory_rows(db, [{"urlkey": key, "original": url, "timestamp": "20070101000000"}])
        upsert_representative_rows(db, [{"urlkey": key, "original": url, "timestamp": "20081201000000", "mimetype": "text/html", "statuscode": "200"}])
        upsert_representative_rows(db, [{"urlkey": key, "original": url, "timestamp": "20071101000000", "mimetype": "application/xhtml+xml", "statuscode": "200"}])
        db.commit()
        row = db.execute("SELECT first_capture_timestamp,selected_capture_timestamp,representative_mimetype FROM pages").fetchone()
        assert row["first_capture_timestamp"] == "20070101000000"
        assert row["selected_capture_timestamp"] == "20071101000000"
        assert row["representative_mimetype"] == "application/xhtml+xml"
    finally:
        db.close()


def test_cdx_classification_handles_xhtml_revisit_and_binary_deliberately():
    assert classify_cdx_row({"mimetype": "application/xhtml+xml"})[0] == "known_text"
    assert classify_cdx_row({"mimetype": "warc/revisit"})[0] == "uncertain"
    assert classify_cdx_row({"mimetype": "video/mp4"})[0] == "known_binary"


def test_wayback_error_html_is_not_accepted_as_post_content():
    with pytest.raises(WaybackError, match="error/interstitial"):
        _reject_wayback_error_html("<html>The Wayback Machine doesn't have that page archived.</html>")


def _fields(post: str, title: str):
    return {
        "post_number": ExtractionResult(post, "test", "high", raw_value=post),
        "posted_date": ExtractionResult("2008-06-14", "test", "high", raw_value="2008-06-14", precision="day"),
        "title": ExtractionResult(title, "test", "high", raw_value=title),
        "category": ExtractionResult("x", "test", "high", raw_value="x"),
        "description": ExtractionResult("x", "test", "high", raw_value="x"),
    }


def test_schema2_review_survives_first_v013_dedupe_when_members_are_unchanged(tmp_path: Path):
    # Build the project through the v3 API, then emulate the legacy v2 group fields that
    # lacked identity/member signatures. The migration behavior is the important case.
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        urls = ["http://nothingtoxic.com/media/88/", "http://media.nothingtoxic.com/media/88/"]
        ids = []
        for i, url in enumerate(urls):
            db.execute("INSERT INTO pages(original_url,extraction_state,created_at,updated_at) VALUES(?,'complete','x','x')", (url,))
            page_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0]); ids.append(page_id)
            save_extraction_result(db, page_id, "old", _fields("88", "Same"), bump_revision=False)
        db.commit()
        detect_duplicates(cfg, db)
        group = db.execute("SELECT id,canonical_page_id FROM duplicate_groups WHERE status='exact'").fetchone()
        canonical = int(group["canonical_page_id"])
        set_duplicate_decision(db, int(group["id"]), "separate")
        # Emulate fields absent from a schema-v2 duplicate group after migration.
        db.execute("UPDATE duplicate_groups SET identity_key=NULL,member_signature=NULL")
        db.commit()
        detect_duplicates(cfg, db)
        group2 = db.execute("SELECT decision,reviewed,canonical_page_id FROM duplicate_groups WHERE status='exact'").fetchone()
        assert group2["decision"] == "separate"
        assert group2["reviewed"] == 1
        assert int(group2["canonical_page_id"]) == canonical
    finally:
        db.close()
