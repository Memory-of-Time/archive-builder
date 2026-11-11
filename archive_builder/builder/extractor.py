from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from ..config import ProjectConfig
from ..constants import EXTRACTOR_VERSION
from ..database.repositories import bump_extraction_revision, record_error, save_extraction_result
from ..events import ProgressEvent, Stopped
from ..extraction import compile_extraction_plan, extract_fields
from .downloader import decode_document


def _emit(callback: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def _candidate_rows(
    database: sqlite3.Connection,
    fingerprint: str,
    extractor_version: str,
    *,
    batch_size: int = 500,
) -> Iterator[sqlite3.Row]:
    """Yield extraction candidates with keyset pagination and a finite high-water mark."""
    row = database.execute(
        """
        SELECT MAX(id) AS max_id FROM pages
        WHERE download_state='complete' AND local_path IS NOT NULL
          AND COALESCE(content_classification,'')<>'known_binary'
        """
    ).fetchone()
    high_water = int(row["max_id"] or 0) if row else 0
    last_id = 0
    while high_water and last_id < high_water:
        rows = database.execute(
            """
            SELECT id,original_url,local_path,response_encoding,response_mimetype,source_identity,
                   extraction_state,extraction_fingerprint,extraction_source_identity,extraction_engine_version
            FROM pages
            WHERE id>? AND id<=?
              AND download_state='complete' AND local_path IS NOT NULL
              AND COALESCE(content_classification,'')<>'known_binary'
              AND (
                    extraction_state<>'complete'
                 OR extraction_fingerprint IS NULL OR extraction_fingerprint<>?
                 OR COALESCE(extraction_source_identity,'')<>COALESCE(source_identity,'')
                 OR COALESCE(extraction_engine_version,'')<>?
              )
            ORDER BY id
            LIMIT ?
            """,
            (last_id, high_water, fingerprint, extractor_version, int(batch_size)),
        ).fetchall()
        if not rows:
            break
        for item in rows:
            last_id = int(item["id"])
            yield item


def _count_candidates(database: sqlite3.Connection, fingerprint: str, extractor_version: str) -> int:
    row = database.execute(
        """
        SELECT COUNT(*) AS n FROM pages
        WHERE download_state='complete' AND local_path IS NOT NULL
          AND COALESCE(content_classification,'')<>'known_binary'
          AND (
                extraction_state<>'complete'
             OR extraction_fingerprint IS NULL OR extraction_fingerprint<>?
             OR COALESCE(extraction_source_identity,'')<>COALESCE(source_identity,'')
             OR COALESCE(extraction_engine_version,'')<>?
          )
        """,
        (fingerprint, extractor_version),
    ).fetchone()
    return int(row["n"] or 0)


def extract_downloaded_pages(
    config: ProjectConfig,
    database: sqlite3.Connection,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> None:
    cfg = config.normalized()
    fingerprint = cfg.site.extraction_fingerprint()
    # Invalid CSS/regex/text rules must fail before page state is changed.
    plan = compile_extraction_plan(cfg.site)
    total = _count_candidates(database, fingerprint, EXTRACTOR_VERSION)
    if not total:
        _emit(callback, ProgressEvent("extract", "No pages need extraction", 0, 0))
        return

    completed = 0
    changed = False
    since_commit = 0
    last_commit = time.monotonic()
    start = last_commit

    try:
        for row in _candidate_rows(database, fingerprint, EXTRACTOR_VERSION):
            if stop_event.is_set():
                raise Stopped
            page_id = int(row["id"])
            database.execute(
                "UPDATE pages SET extraction_state='extracting',last_error=NULL,updated_at=datetime('now') WHERE id=?",
                (page_id,),
            )
            try:
                path = cfg.output_dir / str(row["local_path"])
                payload = path.read_bytes()
                html, chosen_encoding = decode_document(
                    payload,
                    str(row["response_mimetype"] or ""),
                    preferred=str(row["response_encoding"] or ""),
                )
                results = extract_fields(html, str(row["original_url"]), cfg.site, plan=plan)
                field_errors = [
                    f"{name}: {result.error}"
                    for name, result in results.items()
                    if getattr(result, "error", "")
                ]
                if field_errors:
                    state = "error"
                    error = "; ".join(field_errors)[:4000]
                    record_error(database, "extract", "field_extraction", error, page_id=page_id, retryable=False)
                else:
                    state = "complete"
                    error = None
                save_extraction_result(
                    database,
                    page_id,
                    fingerprint,
                    results,
                    state=state,
                    error=error,
                    source_identity=str(row["source_identity"] or ""),
                    extractor_version=EXTRACTOR_VERSION,
                    bump_revision=False,
                )
                # Preserve the encoding actually selected during local re-extraction when
                # the network response omitted/underspecified it.
                if chosen_encoding and not str(row["response_encoding"] or ""):
                    database.execute(
                        "UPDATE pages SET response_encoding=? WHERE id=?",
                        (chosen_encoding, page_id),
                    )
                changed = True
            except Exception as exc:
                record_error(database, "extract", "field_extraction", str(exc), page_id=page_id, retryable=False)
                database.execute(
                    "UPDATE pages SET extraction_state='error',last_error=?,updated_at=datetime('now') WHERE id=?",
                    (str(exc)[:4000], page_id),
                )
                changed = True

            completed += 1
            since_commit += 1
            now = time.monotonic()
            if since_commit >= 32 or now - last_commit >= 1.0 or completed == total:
                database.commit()
                since_commit = 0
                last_commit = now
            rate = completed / max(0.001, now - start)
            _emit(
                callback,
                ProgressEvent(
                    "extract",
                    f"Extracted {completed:,} of {total:,} pages",
                    completed,
                    total,
                    {"completed_pages_per_second": rate, "request_starts_per_second": 0.0},
                ),
            )
    except Stopped:
        database.execute("UPDATE pages SET extraction_state='pending' WHERE extraction_state='extracting'")
        database.commit()
        raise
    finally:
        if changed:
            bump_extraction_revision(database)
        database.commit()
