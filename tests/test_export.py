import csv
from pathlib import Path

from archive_builder.builder.export import export_csv
from archive_builder.constants import CSV_COLUMNS
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import save_extraction_result, upsert_inventory_rows
from archive_builder.extraction import ExtractionResult


def fields(post, date, title):
    return {
        "post_number": ExtractionResult(str(post), "test", "high"),
        "posted_date": ExtractionResult(date, "test", "high"),
        "title": ExtractionResult(title, "test", "high"),
        "category": ExtractionResult("Cat", "test", "high"),
        "description": ExtractionResult("Desc", "test", "high"),
    }


def test_csv_columns_and_descending_posted_date(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        upsert_inventory_rows(db, [
            {"original":"http://nothingtoxic.com/media/1","timestamp":"20080101000000"},
            {"original":"http://nothingtoxic.com/media/2","timestamp":"20090101000000"},
        ])
        rows = db.execute("SELECT id,original_url FROM pages ORDER BY original_url").fetchall()
        save_extraction_result(db, rows[0]["id"], "x", fields(1, "2008-01-01", "Older"))
        save_extraction_result(db, rows[1]["id"], "x", fields(2, "2009-01-01", "Newer"))
        db.commit()
        path = export_csv(db, tmp_path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            data = list(csv.reader(handle))
        assert tuple(data[0]) == CSV_COLUMNS
        assert data[1][0] == "2"
        assert data[1][1] == "01/01/2009"
        assert data[1][-1] == "01/01/2009"
        assert data[2][0] == "1"
    finally:
        db.close()
