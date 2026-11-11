import csv
from pathlib import Path

from archive_builder.builder.deduplicate import detect_duplicates, set_duplicate_decision
from archive_builder.builder.export import export_csv, export_duplicate_report, logical_rows
from archive_builder.config import DedupConfig, new_project_config
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import save_extraction_result, upsert_inventory_rows
from archive_builder.extraction import ExtractionResult


def fields(post, date, title, category="Cat", description="Desc"):
    return {
        "post_number": ExtractionResult(str(post), "test", "high"),
        "posted_date": ExtractionResult(date, "test", "high"),
        "title": ExtractionResult(title, "test", "high"),
        "category": ExtractionResult(category, "test", "high"),
        "description": ExtractionResult(description, "test", "high"),
    }


def add_page(db, url, timestamp, post, date, title, **kwargs):
    upsert_inventory_rows(db, [{"original": url, "timestamp": timestamp}])
    row = db.execute("SELECT id FROM pages WHERE original_url=?", (url,)).fetchone()
    save_extraction_result(db, int(row["id"]), "fingerprint", fields(post, date, title, **kwargs))
    db.commit()
    return int(row["id"])


def test_exact_cross_host_duplicate_auto_merges_and_keeps_earliest_capture(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        root_id = add_page(
            db, "http://nothingtoxic.com/media/48192/", "20080509000000",
            48192, "2008-05-01", "Guy Crashes Motorcycle!",
        )
        add_page(
            db, "http://media.nothingtoxic.com/media/48192/", "20080417000000",
            48192, "2008-05-01", "Guy Crashes Motorcycle",
        )
        counts = detect_duplicates(cfg, db)
        assert counts["exact"] == 1
        group = db.execute("SELECT * FROM duplicate_groups").fetchone()
        assert group["decision"] == "merge"
        assert group["canonical_page_id"] == root_id
        assert group["first_capture_timestamp"] == "20080417000000"
        rows = logical_rows(db)
        assert len(rows) == 1
        assert rows[0]["original_url"] == "http://nothingtoxic.com/media/48192/"
        assert rows[0]["first_capture_timestamp"] == "20080417000000"
    finally:
        db.close()


def test_same_post_number_different_title_is_probable_and_not_auto_merged(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/9/", "20080101000000", 9, "2008-01-01", "First title")
        add_page(db, "http://media.nothingtoxic.com/media/9/", "20080102000000", 9, "2008-01-01", "Different title")
        counts = detect_duplicates(cfg, db)
        assert counts["probable"] == 1
        group = db.execute("SELECT * FROM duplicate_groups").fetchone()
        assert group["decision"] == "review"
        assert len(logical_rows(db)) == 2
    finally:
        db.close()


def test_same_title_different_post_number_is_not_grouped(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/1/", "20080101000000", 1, "2008-01-01", "Repeated title")
        add_page(db, "http://media.nothingtoxic.com/media/2/", "20080102000000", 2, "2008-01-02", "Repeated title")
        counts = detect_duplicates(cfg, db)
        assert counts["groups"] == 0
        assert len(logical_rows(db)) == 2
    finally:
        db.close()


def test_user_can_keep_exact_group_separate(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/7/", "20080101000000", 7, "2008-01-01", "Same")
        add_page(db, "http://media.nothingtoxic.com/media/7/", "20080102000000", 7, "2008-01-01", "Same")
        detect_duplicates(cfg, db)
        group = db.execute("SELECT id FROM duplicate_groups").fetchone()
        set_duplicate_decision(db, int(group["id"]), "separate")
        # A fresh analysis preserves the reviewed decision via the stable match key.
        detect_duplicates(cfg, db)
        assert db.execute("SELECT decision FROM duplicate_groups").fetchone()["decision"] == "separate"
        assert len(logical_rows(db)) == 2
    finally:
        db.close()


def test_export_contains_one_row_for_merged_aliases(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/11/", "20080103000000", 11, "2008-01-02", "Same post")
        add_page(db, "http://media.nothingtoxic.com/media/11/", "20080101000000", 11, "2008-01-02", "Same post")
        detect_duplicates(cfg, db)
        path = export_csv(db, tmp_path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 2
        assert rows[1][0] == "11"
        assert rows[1][-1] == "01/01/2008"
    finally:
        db.close()


def test_cross_domain_dedupe_can_be_disabled(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    cfg.dedup = DedupConfig(cross_domain_enabled=False)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/4/", "20080101000000", 4, "2008-01-01", "Same")
        add_page(db, "http://media.nothingtoxic.com/media/4/", "20080102000000", 4, "2008-01-01", "Same")
        counts = detect_duplicates(cfg, db)
        assert counts["groups"] == 0
    finally:
        db.close()


def test_duplicate_audit_report_lists_both_alias_urls(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    db = open_database(tmp_path)
    try:
        add_page(db, "http://nothingtoxic.com/media/21/", "20080103000000", 21, "2008-01-02", "Alias test")
        add_page(db, "http://media.nothingtoxic.com/media/21/", "20080101000000", 21, "2008-01-02", "Alias test")
        detect_duplicates(cfg, db)
        path = export_duplicate_report(db, tmp_path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 3
        urls = {rows[1][8], rows[2][8]}
        assert urls == {
            "http://nothingtoxic.com/media/21/",
            "http://media.nothingtoxic.com/media/21/",
        }
    finally:
        db.close()
