from pathlib import Path

from archive_builder.database.connection import open_database
from archive_builder.database.repositories import upsert_inventory_rows, upsert_representative_rows


def test_first_capture_keeps_earliest(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        upsert_inventory_rows(db, [{"original":"http://nothingtoxic.com/media/1","timestamp":"20090501000000"}])
        upsert_inventory_rows(db, [{"original":"http://nothingtoxic.com/media/1","timestamp":"20080301000000"}])
        upsert_representative_rows(db, [{
            "original":"http://nothingtoxic.com/media/1","timestamp":"20090601000000","mimetype":"text/html","statuscode":"200"
        }])
        db.commit()
        row = db.execute("SELECT * FROM pages").fetchone()
        assert row["first_capture_timestamp"] == "20080301000000"
        assert row["representative_timestamp"] == "20090601000000"
    finally:
        db.close()


def test_schema_v1_project_migrates_to_duplicate_tables(tmp_path: Path):
    import sqlite3
    from archive_builder.constants import DATABASE_NAME
    from archive_builder.database.schema import BASE_SCHEMA_SQL

    path = tmp_path / DATABASE_NAME
    raw = sqlite3.connect(path)
    try:
        raw.executescript(BASE_SCHEMA_SQL)
        raw.execute("INSERT INTO schema_info(version) VALUES(1)")
        raw.commit()
    finally:
        raw.close()

    db = open_database(tmp_path)
    try:
        assert db.execute("SELECT version FROM schema_info").fetchone()["version"] == 3
        names = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "duplicate_groups" in names
        assert "duplicate_group_members" in names
        assert "page_aliases" in names
        assert "index_coverage" in names
    finally:
        db.close()
