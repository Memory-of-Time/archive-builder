from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

from ..constants import CSV_COLUMNS
from ..extraction.dates import date_sort_key, display_date
from ..utils import timestamp_to_date


FIELD_KEYS = ("post_number", "posted_date", "title", "category", "description")


def _post_number_sort(value: str) -> tuple[int, int | str, str]:
    text = (value or "").strip()
    try:
        return (1, int(text), text)
    except ValueError:
        return (0, text.casefold(), text)


def _spreadsheet_safe(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _raw_rows(database: sqlite3.Connection) -> list[dict[str, object]]:
    rows = database.execute(
        """
        SELECT p.id,p.original_url,p.first_capture_timestamp,
               MAX(CASE WHEN e.field_name='post_number' THEN e.value END) AS post_number,
               MAX(CASE WHEN e.field_name='posted_date' THEN e.value END) AS posted_date,
               MAX(CASE WHEN e.field_name='posted_date' THEN e.precision END) AS posted_precision,
               MAX(CASE WHEN e.field_name='title' THEN e.value END) AS title,
               MAX(CASE WHEN e.field_name='category' THEN e.value END) AS category,
               MAX(CASE WHEN e.field_name='description' THEN e.value END) AS description
        FROM pages p
        LEFT JOIN extracted_fields e ON e.page_id=p.id
        GROUP BY p.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _merge_memberships(database: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict[int, list[int]]]:
    groups = database.execute(
        """
        SELECT id,canonical_page_id,first_capture_timestamp
        FROM duplicate_groups WHERE decision='merge' ORDER BY id
        """
    ).fetchall()
    memberships: dict[int, list[int]] = {}
    if not groups:
        return groups, memberships
    rows = database.execute(
        """
        SELECT m.group_id,m.page_id,m.is_canonical
        FROM duplicate_group_members m
        JOIN duplicate_groups g ON g.id=m.group_id
        WHERE g.decision='merge'
        ORDER BY m.group_id,m.is_canonical DESC,m.page_id
        """
    ).fetchall()
    for row in rows:
        memberships.setdefault(int(row["group_id"]), []).append(int(row["page_id"]))
    return groups, memberships


def logical_rows(database: sqlite3.Connection) -> list[dict[str, object]]:
    """Return one row per logical post, using bulk duplicate membership retrieval."""
    raw = _raw_rows(database)
    by_id = {int(row["id"]): row for row in raw}
    consumed: set[int] = set()
    logical: list[dict[str, object]] = []

    groups, memberships = _merge_memberships(database)
    for group in groups:
        member_ids = [
            page_id for page_id in memberships.get(int(group["id"]), [])
            if page_id in by_id and page_id not in consumed
        ]
        if len(member_ids) < 2:
            continue
        canonical_id = int(group["canonical_page_id"] or member_ids[0])
        if canonical_id not in by_id:
            canonical_id = member_ids[0]
        canonical = dict(by_id[canonical_id])
        members = [by_id[page_id] for page_id in member_ids]

        for field_name in FIELD_KEYS:
            if str(canonical.get(field_name) or "").strip():
                continue
            for member in members:
                value = str(member.get(field_name) or "").strip()
                if value:
                    canonical[field_name] = value
                    if field_name == "posted_date":
                        canonical["posted_precision"] = member.get("posted_precision") or ""
                    break

        timestamps = [
            str(member.get("first_capture_timestamp") or "")
            for member in members if str(member.get("first_capture_timestamp") or "")
        ]
        if str(group["first_capture_timestamp"] or ""):
            timestamps.append(str(group["first_capture_timestamp"]))
        canonical["first_capture_timestamp"] = min(timestamps) if timestamps else ""
        canonical["duplicate_group_id"] = int(group["id"])
        canonical["alias_count"] = len(member_ids)
        logical.append(canonical)
        consumed.update(member_ids)

    logical.extend(row for row in raw if int(row["id"]) not in consumed)
    return logical


def sort_logical_rows(data: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Shared newest-first chronology sorter for GUI, CSV, and XLSX.

    Undated rows always sort last. Partial dates retain their precision rather than being
    fabricated into exact days.
    """
    rows = list(data)
    dated = [row for row in rows if date_sort_key(str(row.get("posted_date") or "")) != (0, 0, 0, 0)]
    undated = [row for row in rows if date_sort_key(str(row.get("posted_date") or "")) == (0, 0, 0, 0)]
    dated.sort(
        key=lambda row: (
            date_sort_key(str(row.get("posted_date") or ""), str(row.get("posted_precision") or "")),
            _post_number_sort(str(row.get("post_number") or "")),
            str(row.get("original_url") or ""),
        ),
        reverse=True,
    )
    undated.sort(
        key=lambda row: (
            _post_number_sort(str(row.get("post_number") or "")),
            str(row.get("original_url") or ""),
        ),
        reverse=True,
    )
    return dated + undated


def preview_logical_rows(database: sqlite3.Connection, limit: int = 500) -> list[dict[str, object]]:
    """Return a bounded Results-tab view without materializing the entire archive.

    The query uses canonical pages for currently merged groups and excludes their alias
    members. Export remains the authoritative metadata-fill path.
    """
    limit = min(5000, max(1, int(limit)))
    rows = database.execute(
        """
        WITH fields AS (
          SELECT p.id,p.original_url,p.first_capture_timestamp,
                 MAX(CASE WHEN e.field_name='post_number' THEN e.value END) AS post_number,
                 MAX(CASE WHEN e.field_name='posted_date' THEN e.value END) AS posted_date,
                 MAX(CASE WHEN e.field_name='posted_date' THEN e.precision END) AS posted_precision,
                 MAX(CASE WHEN e.field_name='title' THEN e.value END) AS title,
                 MAX(CASE WHEN e.field_name='category' THEN e.value END) AS category,
                 MAX(CASE WHEN e.field_name='description' THEN e.value END) AS description
          FROM pages p LEFT JOIN extracted_fields e ON e.page_id=p.id
          GROUP BY p.id
        ),
        merged_members AS (
          SELECT m.page_id FROM duplicate_group_members m
          JOIN duplicate_groups g ON g.id=m.group_id WHERE g.decision='merge'
        ),
        visible AS (
          SELECT f.* FROM fields f
          WHERE NOT EXISTS (SELECT 1 FROM merged_members mm WHERE mm.page_id=f.id)
          UNION ALL
          SELECT f.* FROM duplicate_groups g JOIN fields f ON f.id=g.canonical_page_id
          WHERE g.decision='merge'
        )
        SELECT DISTINCT * FROM visible
        ORDER BY CASE WHEN posted_date IS NULL OR posted_date='' THEN 1 ELSE 0 END,
                 posted_date DESC,post_number DESC,original_url DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return sort_logical_rows(dict(row) for row in rows)[:limit]


def _atomic_csv(path: Path, rows: Iterable[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            for row in rows:
                writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def export_csv(database: sqlite3.Connection, output_dir: Path, filename: str = "archive_builder.csv") -> Path:
    data = sort_logical_rows(logical_rows(database))
    path = Path(output_dir) / "exports" / filename

    def rows():
        yield list(CSV_COLUMNS)
        for row in data:
            yield [
                _spreadsheet_safe(row.get("post_number") or ""),
                display_date(str(row.get("posted_date") or "")),
                _spreadsheet_safe(row.get("title") or ""),
                _spreadsheet_safe(row.get("category") or ""),
                _spreadsheet_safe(row.get("description") or ""),
                _spreadsheet_safe(row.get("original_url") or ""),
                timestamp_to_date(str(row.get("first_capture_timestamp") or ""), display=True),
            ]

    _atomic_csv(path, rows())
    return path


def export_duplicate_report(
    database: sqlite3.Connection,
    output_dir: Path,
    filename: str = "archive_builder_duplicates.csv",
) -> Path:
    path = Path(output_dir) / "exports" / filename
    rows = database.execute(
        """
        SELECT g.id AS group_id,g.status,g.confidence,g.decision,g.reviewed,
               g.post_number AS group_post_number,g.title AS group_title,
               g.first_capture_timestamp AS group_first_capture,
               m.is_canonical,p.id AS page_id,p.original_url,p.first_capture_timestamp,
               MAX(CASE WHEN e.field_name='post_number' THEN e.value END) AS post_number,
               MAX(CASE WHEN e.field_name='title' THEN e.value END) AS title
        FROM duplicate_groups g
        JOIN duplicate_group_members m ON m.group_id=g.id
        JOIN pages p ON p.id=m.page_id
        LEFT JOIN extracted_fields e ON e.page_id=p.id
        GROUP BY g.id,p.id
        ORDER BY g.id,m.is_canonical DESC,p.original_url
        """
    ).fetchall()

    def report_rows():
        yield [
            "Group ID", "Status", "Confidence", "Decision", "Reviewed", "Canonical",
            "Post Number", "Title", "URL", "URL First Capture Date", "Group First Capture Date",
        ]
        for row in rows:
            yield [
                row["group_id"], row["status"], row["confidence"], row["decision"],
                "yes" if row["reviewed"] else "no", "yes" if row["is_canonical"] else "no",
                _spreadsheet_safe(row["post_number"] or row["group_post_number"] or ""),
                _spreadsheet_safe(row["title"] or row["group_title"] or ""),
                _spreadsheet_safe(row["original_url"]),
                timestamp_to_date(str(row["first_capture_timestamp"] or ""), display=True),
                timestamp_to_date(str(row["group_first_capture"] or ""), display=True),
            ]

    _atomic_csv(path, report_rows())
    return path


def _typed_posted_date(row: dict[str, object]) -> date | str:
    text = str(row.get("posted_date") or "")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return display_date(text)


def _typed_capture_date(timestamp: str) -> date | str:
    text = timestamp_to_date(timestamp)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return ""


def export_xlsx(
    database: sqlite3.Connection,
    output_dir: Path,
    filename: str = "archive_builder.xlsx",
    *,
    presentation_max_rows: int = 50000,
) -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as exc:  # pragma: no cover - packaging dependency guard
        raise RuntimeError("XLSX export requires openpyxl") from exc

    data = sort_logical_rows(logical_rows(database))
    path = Path(output_dir) / "exports" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    data_ws = wb.active
    data_ws.title = "Data"
    reading = wb.create_sheet("Reading")

    headers = list(CSV_COLUMNS)
    data_ws.append(headers)
    data_ws.freeze_panes = "A2"
    for cell in data_ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)

    for row in data:
        values = [
            _spreadsheet_safe(row.get("post_number") or ""),
            _typed_posted_date(row),
            _spreadsheet_safe(row.get("title") or ""),
            _spreadsheet_safe(row.get("category") or ""),
            _spreadsheet_safe(row.get("description") or ""),
            _spreadsheet_safe(row.get("original_url") or ""),
            _typed_capture_date(str(row.get("first_capture_timestamp") or "")),
        ]
        data_ws.append(values)
        r = data_ws.max_row
        for col in range(1, 8):
            data_ws.cell(r, col).alignment = Alignment(vertical="top", wrap_text=True)
        if isinstance(values[1], date):
            data_ws.cell(r, 2).number_format = "dd/mm/yyyy"
        if isinstance(values[6], date):
            data_ws.cell(r, 7).number_format = "dd/mm/yyyy"
        if str(row.get("original_url") or "").startswith(("http://", "https://")):
            data_ws.cell(r, 6).hyperlink = str(row.get("original_url"))
            data_ws.cell(r, 6).style = "Hyperlink"

    # Practical widths for the machine-friendly sheet; no merged cells.
    for col, width in {"A":16,"B":16,"C":42,"D":24,"E":72,"F":58,"G":18}.items():
        data_ws.column_dimensions[col].width = width

    spans = [
        (1, 2, "Post Number"), (3, 5, "Posted Date"), (6, 11, "Title"),
        (12, 14, "Category"), (15, 21, "Description"), (22, 23, "URL"),
        (24, 26, "First Capture Date"),
    ]
    for start_col, end_col, label in spans:
        reading.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
        cell = reading.cell(1, start_col, label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    reading.freeze_panes = "A2"
    for index in range(1, 27):
        reading.column_dimensions[chr(64 + index)].width = 10

    max_rows = min(len(data), max(1, int(presentation_max_rows)))
    for idx, row in enumerate(data[:max_rows], start=2):
        values = [
            _spreadsheet_safe(row.get("post_number") or ""),
            _typed_posted_date(row),
            _spreadsheet_safe(row.get("title") or ""),
            _spreadsheet_safe(row.get("category") or ""),
            _spreadsheet_safe(row.get("description") or ""),
            _spreadsheet_safe(row.get("original_url") or ""),
            _typed_capture_date(str(row.get("first_capture_timestamp") or "")),
        ]
        for (start_col, end_col, _), value in zip(spans, values):
            reading.merge_cells(start_row=idx, start_column=start_col, end_row=idx, end_column=end_col)
            cell = reading.cell(idx, start_col, value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if isinstance(values[1], date):
            reading.cell(idx, 3).number_format = "dd/mm/yyyy"
        if isinstance(values[6], date):
            reading.cell(idx, 24).number_format = "dd/mm/yyyy"
        url = str(row.get("original_url") or "")
        if url.startswith(("http://", "https://")):
            reading.cell(idx, 22).hyperlink = url
            reading.cell(idx, 22).style = "Hyperlink"

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp.xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp_name)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return path
