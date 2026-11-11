from __future__ import annotations

import threading
from pathlib import Path

from archive_builder.builder import workflow
from archive_builder.builder.deduplicate import detect_duplicates, set_duplicate_decision
from archive_builder.builder.export import logical_rows
from archive_builder.config import new_project_config
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import save_extraction_result, upsert_inventory_rows
from archive_builder.extraction import ExtractionResult


def _fields(post: str, title: str):
    return {
        "post_number": ExtractionResult(post,"test","high"),
        "posted_date": ExtractionResult("2008-01-01","test","high",precision="day"),
        "title": ExtractionResult(title,"test","high"),
        "category": ExtractionResult("Cat","test","high"),
        "description": ExtractionResult("Desc","test","high"),
    }


def _add(db, url: str, post: str, title: str):
    upsert_inventory_rows(db, [{"original":url,"timestamp":"20080101000000"}])
    pid = int(db.execute("SELECT id FROM pages WHERE original_url=?", (url,)).fetchone()["id"])
    save_extraction_result(db, pid, "fp", _fields(post,title))
    db.commit()
    return pid


def test_exact_subset_survives_conflicting_same_anchor(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        _add(db, "http://nothingtoxic.com/media/77/", "77", "Same title")
        _add(db, "http://media.nothingtoxic.com/media/77/", "77", "Same title!")
        _add(db, "http://www.nothingtoxic.com/media/77/", "77", "Different title")
        counts = detect_duplicates(cfg, db)
        assert counts["exact"] == 1
        assert counts["probable"] == 1
        groups = db.execute("SELECT status,decision,member_count FROM duplicate_groups ORDER BY status").fetchall()
        assert {(r["status"],r["decision"],r["member_count"]) for r in groups} == {
            ("exact","merge",2), ("probable","review",3)
        }
        # The exact aliases collapse, but the conflicting post stays separate.
        assert len(logical_rows(db)) == 2
    finally:
        db.close()


def test_reviewed_group_membership_change_forces_rereview_but_keeps_canonical(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        canonical = _add(db, "http://nothingtoxic.com/media/88/", "88", "Same")
        _add(db, "http://media.nothingtoxic.com/media/88/", "88", "Same")
        detect_duplicates(cfg, db)
        gid = int(db.execute("SELECT id FROM duplicate_groups WHERE status='exact'").fetchone()["id"])
        set_duplicate_decision(db, gid, "separate")
        _add(db, "http://www.nothingtoxic.com/media/88/", "88", "Same")
        detect_duplicates(cfg, db)
        group = db.execute("SELECT * FROM duplicate_groups WHERE status='exact'").fetchone()
        assert group["decision"] == "review"
        assert group["reviewed"] == 0
        assert group["canonical_page_id"] == canonical
        assert group["member_count"] == 3
    finally:
        db.close()


def test_build_all_runs_duplicate_detection_only_once_per_revision(tmp_path: Path, monkeypatch):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    state = {"current": False, "dedupe_calls": 0}
    monkeypatch.setattr(workflow, "index_site", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "download_and_extract_pages", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "extract_downloaded_pages", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "dedupe_is_current", lambda database: state["current"])
    def dedupe(*args, **kwargs):
        state["dedupe_calls"] += 1
        state["current"] = True
        return {"groups":0,"exact":0,"probable":0,"merged":0,"review":0}
    monkeypatch.setattr(workflow, "detect_duplicates", dedupe)
    monkeypatch.setattr(workflow, "export_csv", lambda *a, **k: tmp_path / "exports" / "archive_builder.csv")
    monkeypatch.setattr(workflow, "export_duplicate_report", lambda *a, **k: tmp_path / "exports" / "archive_builder_duplicates.csv")
    try:
        workflow.run_operation(cfg, db, "all", threading.Event())
        assert state["dedupe_calls"] == 1
    finally:
        db.close()
