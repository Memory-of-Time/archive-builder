from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from archive_builder.cdx.client import WaybackError, parse_cdx
from archive_builder.cdx.parameters import build_params
from archive_builder.config import ProjectConfig, SiteProfile, new_project_config
from archive_builder.constants import DATABASE_NAME
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import upsert_inventory_rows, upsert_representative_rows
from archive_builder.database.schema import BASE_SCHEMA_SQL, DEDUP_SCHEMA_SQL


def test_strict_cdx_rejects_null_bad_width_bad_timestamp_and_bad_framing():
    with pytest.raises(WaybackError):
        parse_cdx(None)
    with pytest.raises(WaybackError):
        parse_cdx([["timestamp","original"],["20080101000000"]])
    with pytest.raises(WaybackError):
        parse_cdx([["timestamp","original"],["2008","http://example.com/"]])
    with pytest.raises(WaybackError):
        parse_cdx([["timestamp","original"],["20080101000000","http://example.com/"],[]])


def test_cdx_params_full_interval_path_scope_urlkey_and_opaque_resume(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    token = "a+b&c%2F日本?nested=http://x/y?a=1&b=2"
    params = build_params(cfg, "inventory", "20000101000000", "20101231235959", token)
    assert ("from", "20000101000000") in params
    assert ("to", "20101231235959") in params
    assert any(k == "fl" and v.startswith("urlkey,") for k, v in params)
    assert ("resumeKey", token) in params
    assert any(k == "filter" and "^https?://" in v and "/media/" in v for k, v in params)


def test_single_host_uses_prefix_query(tmp_path: Path):
    cfg = ProjectConfig(tmp_path, SiteProfile(domain="example.org", content_path="/posts/", include_subdomains=False)).normalized()
    params = build_params(cfg, "inventory", 2008, 2010)
    assert ("matchType", "prefix") in params
    assert ("url", "example.org/posts/*") in params


def test_verified_urlkey_reconciles_legacy_alias_rows(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        a = "http://example.com/post?id=7"
        b = "https://example.com/post?id=7"
        upsert_inventory_rows(db, [
            {"original":a,"timestamp":"20080102000000"},
            {"original":b,"timestamp":"20080103000000"},
        ])
        assert db.execute("SELECT COUNT(*) n FROM pages").fetchone()["n"] == 2
        upsert_inventory_rows(db, [
            {"urlkey":"com,example)/post?id=7","original":a,"timestamp":"20080102000000"},
            {"urlkey":"com,example)/post?id=7","original":b,"timestamp":"20080101000000"},
        ])
        db.commit()
        assert db.execute("SELECT COUNT(*) n FROM pages").fetchone()["n"] == 1
        row = db.execute("SELECT * FROM pages").fetchone()
        assert row["urlkey"] == "com,example)/post?id=7"
        assert row["first_capture_timestamp"] == "20080101000000"
        aliases = db.execute("SELECT original_url FROM page_aliases ORDER BY original_url").fetchall()
        assert {r["original_url"] for r in aliases} == {a,b}
    finally:
        db.close()


def test_schema2_migration_creates_backup_and_preserves_review_and_file(tmp_path: Path):
    path = tmp_path / DATABASE_NAME
    source = tmp_path / "captures" / "legacy.html"
    source.parent.mkdir()
    source.write_text("legacy", encoding="utf-8")
    raw = sqlite3.connect(path)
    try:
        raw.row_factory = sqlite3.Row
        raw.executescript(BASE_SCHEMA_SQL)
        raw.executescript(DEDUP_SCHEMA_SQL)
        raw.execute("INSERT INTO schema_info(version) VALUES(2)")
        raw.execute(
            "INSERT INTO pages(original_url,download_state,extraction_state,local_path,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("http://example.com/post/1","complete","complete","captures/legacy.html","x","x"),
        )
        page_id = raw.execute("SELECT id FROM pages").fetchone()[0]
        raw.execute(
            "INSERT INTO duplicate_groups(match_key,status,confidence,decision,reviewed,canonical_page_id,member_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("old","exact","high","separate",1,page_id,1,"x","x"),
        )
        gid = raw.execute("SELECT id FROM duplicate_groups").fetchone()[0]
        raw.execute("INSERT INTO duplicate_group_members(group_id,page_id,is_canonical,created_at) VALUES(?,?,1,'x')", (gid,page_id))
        raw.commit()
    finally:
        raw.close()

    db = open_database(tmp_path)
    try:
        assert db.execute("SELECT version FROM schema_info").fetchone()["version"] == 3
        group = db.execute("SELECT decision,reviewed FROM duplicate_groups").fetchone()
        assert group["decision"] == "separate" and group["reviewed"] == 1
        assert db.execute("SELECT COUNT(*) n FROM page_aliases").fetchone()["n"] == 1
    finally:
        db.close()
    assert source.read_text(encoding="utf-8") == "legacy"
    assert (tmp_path / "archive_builder.schema2.backup.sqlite3").is_file()


def test_read_only_observer_does_not_reset_live_states(tmp_path: Path):
    writer = open_database(tmp_path)
    try:
        writer.execute(
            "INSERT INTO pages(original_url,download_state,extraction_state,created_at,updated_at) VALUES(?, 'downloading','extracting','x','x')",
            ("http://example.com/1",),
        )
        writer.execute("INSERT INTO operation_runs(operation,status,started_at,updated_at) VALUES('all','running','x','x')")
        writer.commit()
        observer = open_database(tmp_path, read_only=True)
        observer.close()
        row = writer.execute("SELECT download_state,extraction_state FROM pages").fetchone()
        assert row["download_state"] == "downloading"
        assert row["extraction_state"] == "extracting"
        assert writer.execute("SELECT status FROM operation_runs").fetchone()["status"] == "running"
    finally:
        writer.close()
