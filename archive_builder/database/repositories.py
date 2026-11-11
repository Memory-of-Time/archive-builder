from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

from ..utils import utc_now


TEXT_MIMES = {
    "text/html", "application/xhtml+xml", "text/plain", "text/xml", "application/xml",
}


def classify_cdx_row(row: Mapping[str, str]) -> tuple[str, str]:
    mime = str(row.get("mimetype") or "").strip().casefold().split(";", 1)[0]
    if mime in TEXT_MIMES:
        return "known_text", f"cdx:{mime}"
    if mime == "warc/revisit":
        return "uncertain", "cdx:revisit"
    if mime.startswith(("image/", "video/", "audio/", "font/")):
        return "known_binary", f"cdx:{mime}"
    if mime in {
        "application/pdf", "application/zip", "application/x-rar-compressed",
        "application/octet-stream", "application/x-7z-compressed",
    }:
        return "known_binary", f"cdx:{mime}"
    return "uncertain", f"cdx:{mime or 'unknown'}"


def _valid_capture_row(row: Mapping[str, str]) -> tuple[str, str, str] | None:
    url = str(row.get("original") or "").strip()
    timestamp = str(row.get("timestamp") or "").strip()
    urlkey = str(row.get("urlkey") or "").strip()
    if not url or len(timestamp) != 14 or not timestamp.isdigit():
        return None
    return url, timestamp, urlkey


def _merge_verified_urlkey_pages(database: sqlite3.Connection, target_id: int, source_id: int, now: str) -> int:
    """Reconcile two legacy rows only after CDX proves they share the same urlkey.

    Unknown schema-v2 identities are never guessed. Files remain on disk; if only the
    source row has a completed capture/extraction, that durable state is adopted by the
    target before the redundant logical row is removed.
    """
    if target_id == source_id:
        return target_id
    target = database.execute("SELECT * FROM pages WHERE id=?", (target_id,)).fetchone()
    source = database.execute("SELECT * FROM pages WHERE id=?", (source_id,)).fetchone()
    if target is None or source is None:
        return target_id

    def earliest(a, b):
        vals = [str(v) for v in (a, b) if v]
        return min(vals) if vals else None

    first_capture = earliest(target["first_capture_timestamp"], source["first_capture_timestamp"])
    # Prefer the row with the earlier selected/representative capture for archive identity.
    target_selected = str(target["selected_capture_timestamp"] or target["representative_timestamp"] or "")
    source_selected = str(source["selected_capture_timestamp"] or source["representative_timestamp"] or "")
    adopt_source_capture = bool(source_selected and (not target_selected or source_selected < target_selected))

    capture_cols = [
        "representative_timestamp","representative_mimetype","representative_statuscode",
        "selected_capture_timestamp","selected_capture_digest","selected_capture_length",
        "intended_local_path","actual_replay_timestamp","replay_final_url","response_mimetype",
        "response_encoding","content_classification","classification_reason","source_size",
        "source_sha256","source_identity","local_path","http_status","download_state",
        "download_started_at",
    ]
    extraction_cols = [
        "extraction_state","extraction_fingerprint","extraction_source_identity",
        "extraction_engine_version","last_error",
    ]
    updates = {"first_capture_timestamp": first_capture, "updated_at": now}
    if adopt_source_capture or (str(target["download_state"] or "") != "complete" and str(source["download_state"] or "") == "complete"):
        for col in capture_cols:
            updates[col] = source[col]
        if str(source["extraction_state"] or "") == "complete":
            for col in extraction_cols:
                updates[col] = source[col]
    elif str(target["extraction_state"] or "") != "complete" and str(source["extraction_state"] or "") == "complete" and str(target["source_identity"] or "") == str(source["source_identity"] or ""):
        for col in extraction_cols:
            updates[col] = source[col]

    assignments = ",".join(f"{name}=?" for name in updates)
    database.execute(f"UPDATE pages SET {assignments} WHERE id=?", (*updates.values(), target_id))

    # Preserve extracted fields only when target lacks that field.
    database.execute(
        """
        INSERT OR IGNORE INTO extracted_fields(page_id,field_name,value,method,confidence,error,updated_at,raw_value,precision)
        SELECT ?,field_name,value,method,confidence,error,updated_at,raw_value,precision
        FROM extracted_fields WHERE page_id=?
        """,
        (target_id, source_id),
    )
    database.execute("UPDATE page_aliases SET page_id=?,updated_at=? WHERE page_id=?", (target_id, now, source_id))
    database.execute("UPDATE errors SET page_id=? WHERE page_id=?", (target_id, source_id))
    memberships = database.execute(
        "SELECT group_id,is_canonical,created_at FROM duplicate_group_members WHERE page_id=?", (source_id,)
    ).fetchall()
    for member in memberships:
        database.execute(
            "INSERT OR IGNORE INTO duplicate_group_members(group_id,page_id,is_canonical,created_at) VALUES(?,?,?,?)",
            (member["group_id"], target_id, member["is_canonical"], member["created_at"]),
        )
    database.execute("DELETE FROM duplicate_group_members WHERE page_id=?", (source_id,))
    database.execute("UPDATE duplicate_groups SET canonical_page_id=? WHERE canonical_page_id=?", (target_id, source_id))
    database.execute("UPDATE duplicate_review_history SET canonical_page_id=? WHERE canonical_page_id=?", (target_id, source_id))
    database.execute("DELETE FROM pages WHERE id=?", (source_id,))
    database.execute(
        "INSERT INTO project_state(key,value,updated_at) VALUES('dedupe_revision','-1',?) "
        "ON CONFLICT(key) DO UPDATE SET value='-1',updated_at=excluded.updated_at",
        (now,),
    )
    return target_id


def _resolve_page(database: sqlite3.Connection, original_url: str, urlkey: str, now: str) -> int:
    urlkey_row = None
    if urlkey:
        urlkey_row = database.execute("SELECT id FROM pages WHERE urlkey=? ORDER BY id LIMIT 1", (urlkey,)).fetchone()
    alias_row = database.execute(
        "SELECT page_id AS id FROM page_aliases WHERE original_url=? LIMIT 1", (original_url,)
    ).fetchone()
    if alias_row is None:
        alias_row = database.execute("SELECT id FROM pages WHERE original_url=? LIMIT 1", (original_url,)).fetchone()

    if urlkey_row is not None and alias_row is not None and int(urlkey_row["id"]) != int(alias_row["id"]):
        page_id = _merge_verified_urlkey_pages(database, int(urlkey_row["id"]), int(alias_row["id"]), now)
    elif urlkey_row is not None:
        page_id = int(urlkey_row["id"])
    elif alias_row is not None:
        page_id = int(alias_row["id"])
    else:
        cursor = database.execute(
            "INSERT INTO pages(original_url,urlkey,created_at,updated_at) VALUES(?,?,?,?)",
            (original_url, urlkey or None, now, now),
        )
        return int(cursor.lastrowid)

    if urlkey:
        database.execute("UPDATE pages SET urlkey=COALESCE(urlkey,?) WHERE id=?", (urlkey, page_id))
    return page_id


def _upsert_alias(
    database: sqlite3.Connection,
    page_id: int,
    original_url: str,
    urlkey: str,
    timestamp: str,
    representative: bool,
    now: str,
) -> None:
    database.execute(
        """
        INSERT INTO page_aliases(
            page_id,original_url,urlkey,first_capture_timestamp,representative_timestamp,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(original_url) DO UPDATE SET
            page_id=excluded.page_id,
            urlkey=COALESCE(page_aliases.urlkey,excluded.urlkey),
            first_capture_timestamp=CASE
                WHEN page_aliases.first_capture_timestamp IS NULL OR excluded.first_capture_timestamp < page_aliases.first_capture_timestamp
                THEN excluded.first_capture_timestamp ELSE page_aliases.first_capture_timestamp END,
            representative_timestamp=CASE
                WHEN excluded.representative_timestamp IS NULL THEN page_aliases.representative_timestamp
                WHEN page_aliases.representative_timestamp IS NULL OR excluded.representative_timestamp < page_aliases.representative_timestamp
                THEN excluded.representative_timestamp ELSE page_aliases.representative_timestamp END,
            updated_at=excluded.updated_at
        """,
        (page_id, original_url, urlkey or None, timestamp, timestamp if representative else None, now, now),
    )


def upsert_inventory_rows(database: sqlite3.Connection, rows: Iterable[Mapping[str, str]]) -> int:
    before = database.total_changes
    now = utc_now()
    for row in rows:
        valid = _valid_capture_row(row)
        if not valid:
            continue
        url, timestamp, urlkey = valid
        page_id = _resolve_page(database, url, urlkey, now)
        database.execute(
            """
            UPDATE pages SET
                first_capture_timestamp=CASE
                    WHEN first_capture_timestamp IS NULL OR ? < first_capture_timestamp THEN ? ELSE first_capture_timestamp END,
                urlkey=COALESCE(urlkey,?),
                updated_at=?
            WHERE id=?
            """,
            (timestamp, timestamp, urlkey or None, now, page_id),
        )
        _upsert_alias(database, page_id, url, urlkey, timestamp, False, now)
    return database.total_changes - before


def upsert_representative_rows(database: sqlite3.Connection, rows: Iterable[Mapping[str, str]]) -> int:
    before = database.total_changes
    now = utc_now()
    for row in rows:
        valid = _valid_capture_row(row)
        if not valid:
            continue
        url, timestamp, urlkey = valid
        page_id = _resolve_page(database, url, urlkey, now)
        existing = database.execute(
            "SELECT representative_timestamp,selected_capture_timestamp FROM pages WHERE id=?", (page_id,)
        ).fetchone()
        current = str(existing["representative_timestamp"] or "") if existing else ""
        selected_changed = not current or timestamp < current
        classification, reason = classify_cdx_row(row)
        # Representative rows also establish a scoped first capture only if no inventory
        # evidence exists yet. The inventory phase remains authoritative when present.
        database.execute(
            """
            UPDATE pages SET
                first_capture_timestamp=CASE
                    WHEN first_capture_timestamp IS NULL OR ? < first_capture_timestamp THEN ? ELSE first_capture_timestamp END,
                urlkey=COALESCE(urlkey,?),
                representative_timestamp=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE representative_timestamp END,
                representative_mimetype=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE representative_mimetype END,
                representative_statuscode=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE representative_statuscode END,
                selected_capture_timestamp=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE selected_capture_timestamp END,
                selected_capture_digest=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE selected_capture_digest END,
                selected_capture_length=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE selected_capture_length END,
                content_classification=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE content_classification END,
                classification_reason=CASE
                    WHEN representative_timestamp IS NULL OR ? < representative_timestamp THEN ? ELSE classification_reason END,
                updated_at=?
            WHERE id=?
            """,
            (
                timestamp, timestamp, urlkey or None,
                timestamp, timestamp,
                timestamp, str(row.get("mimetype") or ""),
                timestamp, str(row.get("statuscode") or ""),
                timestamp, timestamp,
                timestamp, str(row.get("digest") or ""),
                timestamp, str(row.get("length") or ""),
                timestamp, classification,
                timestamp, reason,
                now, page_id,
            ),
        )
        _upsert_alias(database, page_id, url, urlkey, timestamp, True, now)
        if selected_changed:
            # A changed selected capture invalidates only the source association; old files
            # stay on disk and can still be audited manually.
            database.execute(
                """
                UPDATE pages SET download_state='pending',extraction_state='pending',
                    extraction_fingerprint=NULL,extraction_source_identity=NULL,extraction_engine_version=NULL,
                    local_path=NULL,intended_local_path=NULL,http_status=NULL,actual_replay_timestamp=NULL,
                    replay_final_url=NULL,response_mimetype=NULL,response_encoding=NULL,source_size=NULL,
                    source_sha256=NULL,source_identity=NULL,last_error=NULL,updated_at=?
                WHERE id=?
                """,
                (now, page_id),
            )
    return database.total_changes - before


def get_index_state(database: sqlite3.Connection, phase: str, year: int, signature: str) -> sqlite3.Row | None:
    # Legacy compatibility for tests/tools. v0.1.3 runtime uses index_coverage.
    return database.execute(
        "SELECT * FROM index_state WHERE phase=? AND year=? AND query_signature=?",
        (phase, year, signature),
    ).fetchone()


def save_index_state(
    database: sqlite3.Connection,
    phase: str,
    year: int,
    signature: str,
    resume_key: str | None,
    complete: bool,
    seen: int,
) -> None:
    database.execute(
        """
        INSERT INTO index_state(phase,year,query_signature,resume_key,complete,seen,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(phase,year,query_signature) DO UPDATE SET
            resume_key=excluded.resume_key,complete=excluded.complete,seen=excluded.seen,updated_at=excluded.updated_at
        """,
        (phase, year, signature, resume_key, int(complete), int(seen), utc_now()),
    )


def get_index_coverage(
    database: sqlite3.Connection,
    phase: str,
    range_start: str,
    range_end: str,
    strategy: str,
    unit_key: str,
    signature: str,
) -> sqlite3.Row | None:
    return database.execute(
        """
        SELECT * FROM index_coverage
        WHERE phase=? AND range_start=? AND range_end=? AND strategy=? AND unit_key=? AND query_signature=?
        """,
        (phase, range_start, range_end, strategy, unit_key, signature),
    ).fetchone()


def save_index_coverage(
    database: sqlite3.Connection,
    phase: str,
    range_start: str,
    range_end: str,
    strategy: str,
    unit_key: str,
    signature: str,
    resume_key: str | None,
    complete: bool,
    seen: int,
    response_fingerprint: str = "",
) -> None:
    database.execute(
        """
        INSERT INTO index_coverage(
            phase,range_start,range_end,strategy,unit_key,query_signature,resume_key,complete,seen,response_fingerprint,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(phase,range_start,range_end,strategy,unit_key,query_signature) DO UPDATE SET
            resume_key=excluded.resume_key,complete=excluded.complete,seen=excluded.seen,
            response_fingerprint=excluded.response_fingerprint,updated_at=excluded.updated_at
        """,
        (
            phase, range_start, range_end, strategy, unit_key, signature,
            resume_key, int(complete), int(seen), response_fingerprint or None, utc_now(),
        ),
    )


def get_project_state(database: sqlite3.Connection, key: str, default: str = "") -> str:
    row = database.execute("SELECT value FROM project_state WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else default


def set_project_state(database: sqlite3.Connection, key: str, value: str) -> None:
    database.execute(
        """
        INSERT INTO project_state(key,value,updated_at) VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """,
        (key, str(value), utc_now()),
    )


def bump_extraction_revision(database: sqlite3.Connection) -> int:
    current = int(get_project_state(database, "extraction_revision", "0") or 0) + 1
    set_project_state(database, "extraction_revision", str(current))
    return current


def dedupe_is_current(database: sqlite3.Connection) -> bool:
    return get_project_state(database, "dedupe_revision", "-1") == get_project_state(database, "extraction_revision", "0")


def mark_dedupe_current(database: sqlite3.Connection) -> None:
    set_project_state(database, "dedupe_revision", get_project_state(database, "extraction_revision", "0"))


def start_operation(database: sqlite3.Connection, operation: str) -> int:
    now = utc_now()
    cursor = database.execute(
        "INSERT INTO operation_runs(operation,status,started_at,updated_at) VALUES(?,'running',?,?)",
        (operation, now, now),
    )
    database.commit()
    return int(cursor.lastrowid)


def finish_operation(database: sqlite3.Connection, run_id: int, status: str, message: str = "") -> None:
    now = utc_now()
    database.execute(
        "UPDATE operation_runs SET status=?,message=?,completed_at=?,updated_at=? WHERE id=?",
        (status, message, now, now, run_id),
    )
    database.commit()


def record_error(
    database: sqlite3.Connection,
    operation: str,
    category: str,
    message: str,
    page_id: int | None = None,
    retryable: bool = True,
) -> None:
    database.execute(
        "INSERT INTO errors(page_id,operation,category,message,retryable,created_at) VALUES(?,?,?,?,?,?)",
        (page_id, operation, category, message[:4000], int(retryable), utc_now()),
    )


def prepare_download(database: sqlite3.Connection, page_id: int, intended_local_path: str) -> None:
    database.execute(
        "UPDATE pages SET download_state='downloading',intended_local_path=?,download_started_at=?,last_error=NULL,updated_at=? WHERE id=?",
        (intended_local_path, utc_now(), utc_now(), page_id),
    )


def save_download_result(
    database: sqlite3.Connection,
    page_id: int,
    state: str,
    local_path: str | None = None,
    http_status: int | None = None,
    error: str | None = None,
    *,
    actual_replay_timestamp: str | None = None,
    replay_final_url: str | None = None,
    response_mimetype: str | None = None,
    response_encoding: str | None = None,
    content_classification: str | None = None,
    classification_reason: str | None = None,
    source_size: int | None = None,
    source_sha256: str | None = None,
    source_identity: str | None = None,
) -> None:
    database.execute(
        """
        UPDATE pages SET download_state=?,local_path=COALESCE(?,local_path),http_status=?,last_error=?,
            actual_replay_timestamp=COALESCE(?,actual_replay_timestamp),
            replay_final_url=COALESCE(?,replay_final_url),response_mimetype=COALESCE(?,response_mimetype),
            response_encoding=COALESCE(?,response_encoding),content_classification=COALESCE(?,content_classification),
            classification_reason=COALESCE(?,classification_reason),source_size=COALESCE(?,source_size),
            source_sha256=COALESCE(?,source_sha256),source_identity=COALESCE(?,source_identity),updated_at=?
        WHERE id=?
        """,
        (
            state, local_path, http_status, error,
            actual_replay_timestamp, replay_final_url, response_mimetype, response_encoding,
            content_classification, classification_reason, source_size, source_sha256, source_identity,
            utc_now(), page_id,
        ),
    )


def save_extraction_result(
    database: sqlite3.Connection,
    page_id: int,
    fingerprint: str,
    fields: dict[str, object],
    state: str = "complete",
    error: str | None = None,
    *,
    source_identity: str | None = None,
    extractor_version: str | None = None,
    bump_revision: bool = True,
) -> None:
    now = utc_now()
    database.execute("DELETE FROM extracted_fields WHERE page_id=?", (page_id,))
    for field_name, result in fields.items():
        value = getattr(result, "value", None)
        method = getattr(result, "method", "")
        confidence = getattr(result, "confidence", "")
        field_error = getattr(result, "error", "")
        raw_value = getattr(result, "raw_value", None)
        precision = getattr(result, "precision", "")
        database.execute(
            """
            INSERT INTO extracted_fields(
                page_id,field_name,value,method,confidence,error,raw_value,precision,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (page_id, field_name, value, method, confidence, field_error, raw_value, precision, now),
        )
    database.execute(
        """
        UPDATE pages SET extraction_state=?,extraction_fingerprint=?,extraction_source_identity=COALESCE(?,source_identity),
            extraction_engine_version=?,last_error=?,updated_at=? WHERE id=?
        """,
        (state, fingerprint, source_identity, extractor_version, error, now, page_id),
    )
    if bump_revision:
        bump_extraction_revision(database)


def page_counts(database: sqlite3.Connection) -> dict[str, int]:
    row = database.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN selected_capture_timestamp IS NOT NULL AND COALESCE(content_classification,'')<>'known_binary' THEN 1 ELSE 0 END) AS downloadable,
          SUM(CASE WHEN download_state='complete' THEN 1 ELSE 0 END) AS downloaded,
          SUM(CASE WHEN extraction_state='complete' THEN 1 ELSE 0 END) AS extracted,
          SUM(CASE WHEN download_state='error' OR extraction_state='error' THEN 1 ELSE 0 END) AS errors
        FROM pages
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}
