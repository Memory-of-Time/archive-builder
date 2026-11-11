from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from archive_builder.builder.export import export_csv, export_xlsx, sort_logical_rows
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import save_extraction_result, upsert_inventory_rows
from archive_builder.extraction import ExtractionResult
from archive_builder.extraction.dates import parse_date


def _fields(post: str, posted: str, precision: str, title: str, description: str = "desc"):
    return {
        "post_number": ExtractionResult(post, "test", "high", raw_value=post),
        "posted_date": ExtractionResult(posted, "test", "high", raw_value=posted, precision=precision),
        "title": ExtractionResult(title, "test", "high", raw_value=title),
        "category": ExtractionResult("Cat", "test", "high", raw_value="Cat"),
        "description": ExtractionResult(description, "test", "high", raw_value=description),
    }


def test_partial_dates_keep_precision_without_fabricating_day():
    assert parse_date("2008").value == "2008"
    assert parse_date("2008").precision == "year"
    assert parse_date("June 2008").value == "2008-06"
    assert parse_date("June 2008").precision == "month"
    assert parse_date("14 June").value == ""
    assert parse_date("02/03/2008", day_first=False).value == "2008-02-03"
    assert parse_date("02/03/2008", day_first=True).value == "2008-03-02"
    assert parse_date("29/02/2008", day_first=True).value == "2008-02-29"


def test_shared_sort_puts_undated_last_and_uses_true_date_order():
    rows = [
        {"posted_date":"2008-12-31", "post_number":"1", "original_url":"a"},
        {"posted_date":"2009-01-01", "post_number":"2", "original_url":"b"},
        {"posted_date":"", "post_number":"999", "original_url":"z"},
        {"posted_date":"2008-06", "post_number":"3", "original_url":"c"},
    ]
    ordered = sort_logical_rows(rows)
    assert [row["posted_date"] for row in ordered] == ["2009-01-01", "2008-12-31", "2008-06", ""]


def test_csv_roundtrip_seven_fields_multiline_unicode_and_ddmmyyyy(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        upsert_inventory_rows(db, [{"original":"http://example.test/post/1", "timestamp":"20080304050607"}])
        page_id = int(db.execute("SELECT id FROM pages").fetchone()["id"])
        save_extraction_result(
            db, page_id, "fp",
            _fields("0001", "2008-03-02", "day", 'A "quoted", title — 日本', "line one\nline two, with comma"),
        )
        db.commit()
        path = export_csv(db, tmp_path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows[0]) == 7
        assert rows[1][0] == "0001"
        assert rows[1][1] == "02/03/2008"
        assert rows[1][2] == 'A "quoted", title — 日本'
        assert rows[1][4] == "line one\nline two, with comma"
        assert rows[1][6] == "04/03/2008"
    finally:
        db.close()


def test_xlsx_has_unmerged_data_and_a_to_z_reading_spans(tmp_path: Path):
    db = open_database(tmp_path)
    try:
        upsert_inventory_rows(db, [{"original":"http://example.test/post/1", "timestamp":"20080304050607"}])
        page_id = int(db.execute("SELECT id FROM pages").fetchone()["id"])
        save_extraction_result(db, page_id, "fp", _fields("0001", "2008-03-02", "day", "Title"))
        db.commit()
        path = export_xlsx(db, tmp_path, presentation_max_rows=100)
    finally:
        db.close()

    wb = load_workbook(path)
    data = wb["Data"]
    reading = wb["Reading"]
    assert not list(data.merged_cells.ranges)
    assert data["A2"].value == "0001"
    assert isinstance(data["B2"].value, (date, __import__("datetime").datetime))
    assert data["B2"].number_format == "dd/mm/yyyy"
    ranges = {str(item) for item in reading.merged_cells.ranges}
    assert {"A1:B1","C1:E1","F1:K1","L1:N1","O1:U1","V1:W1","X1:Z1"}.issubset(ranges)
    assert {"A2:B2","C2:E2","F2:K2","L2:N2","O2:U2","V2:W2","X2:Z2"}.issubset(ranges)
    assert reading.freeze_panes == "A2"
