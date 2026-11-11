from __future__ import annotations

import codecs
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..cdx.client import PermanentWaybackError, RateLimitDeferred, ReplayBytes, WaybackClient, WaybackError
from ..config import ProjectConfig
from ..constants import EXTRACTOR_VERSION
from ..database.repositories import (
    bump_extraction_revision,
    prepare_download,
    record_error,
    save_download_result,
    save_extraction_result,
)
from ..events import ProgressEvent, Stopped
from ..extraction import ExtractionPlan, compile_extraction_plan, extract_fields
from ..utils import atomic_write_bytes, atomic_write_text, safe_filename_for_url, sha256_bytes, stable_json_hash


WAYBACK_CAPTURE_RE = re.compile(r"/web/(\d{14})")
CHARSET_RE = re.compile(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", re.I)
META_CHARSET_RE = re.compile(
    br"<meta[^>]+charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)|"
    br"<meta[^>]+content\s*=\s*['\"][^'\"]*charset=([A-Za-z0-9._-]+)",
    re.I,
)
BINARY_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1f\x8b", "gzip"),
)
WAYBACK_ERROR_MARKERS = (
    "wayback machine doesn't have that page archived",
    "this url has been excluded from the wayback machine",
    "page cannot be crawled or displayed due to robots.txt",
    "the machine that serves this file is down",
)


@dataclass(slots=True)
class WorkerOutcome:
    page_id: int
    local_path: str = ""
    http_status: int | None = None
    error: str = ""
    retryable: bool = True
    skipped: bool = False
    actual_replay_timestamp: str = ""
    replay_final_url: str = ""
    response_mimetype: str = ""
    response_encoding: str = ""
    content_classification: str = ""
    classification_reason: str = ""
    source_size: int = 0
    source_sha256: str = ""
    source_identity: str = ""
    fields: dict | None = None
    extraction_error: str = ""
    used_network: bool = False


def _emit(callback: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def replay_url(timestamp: str, original_url: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def _mime_only(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().casefold()


def _binary_signature(data: bytes) -> str:
    prefix = data[:32]
    for signature, name in BINARY_SIGNATURES:
        if prefix.startswith(signature):
            return name
    if prefix.startswith(b"RIFF") and len(prefix) >= 12:
        return prefix[8:12].decode("ascii", "ignore").casefold() or "riff"
    return ""


def classify_replay(data: bytes, content_type: str) -> tuple[str, str]:
    signature = _binary_signature(data)
    if signature:
        return "known_binary", f"signature:{signature}"
    mime = _mime_only(content_type)
    if mime.startswith(("image/", "video/", "audio/", "font/")) or mime in {
        "application/pdf", "application/zip", "application/octet-stream", "application/x-rar-compressed",
    }:
        # A textual Wayback error page can be served under the archived binary MIME;
        # inspect a small prefix before treating the MIME as authoritative.
        prefix = data[:4096]
        if b"<html" not in prefix.lower() and b"<!doctype html" not in prefix.lower():
            return "known_binary", f"content-type:{mime}"
    if b"\x00" in data[:4096]:
        return "known_binary", "nul-bytes"
    if mime.startswith("text/") or mime in {"application/xhtml+xml", "application/xml", "text/xml"}:
        return "known_text", f"content-type:{mime}"
    prefix = data[:8192].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in prefix[:2048]:
        return "known_text", "sniff:markup"
    printable = sum(1 for byte in data[:4096] if byte in b"\t\n\r" or 32 <= byte < 127)
    sample = max(1, min(len(data), 4096))
    if printable / sample >= 0.85:
        return "uncertain_text", "sniff:printable"
    return "known_binary", "sniff:nontext"


def decode_document(data: bytes, content_type: str = "", preferred: str = "") -> tuple[str, str]:
    candidates: list[str] = []
    # A byte-order mark is intrinsic evidence and outranks cached/HTTP metadata.
    if data.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        candidates.append("utf-32")
    elif data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        candidates.append("utf-16")
    if preferred:
        candidates.append(preferred)
    match = CHARSET_RE.search(content_type or "")
    if match:
        candidates.append(match.group(1))
    meta = META_CHARSET_RE.search(data[:8192])
    if meta:
        raw = meta.group(1) or meta.group(2)
        if raw:
            candidates.append(raw.decode("ascii", "ignore"))
    candidates.extend(["utf-8", "windows-1252"])
    tried: set[str] = set()
    for candidate in candidates:
        name = (candidate or "").strip().casefold()
        if not name or name in tried:
            continue
        tried.add(name)
        try:
            return data.decode(name, "strict"), name
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace"


def _validate_wayback_final_url(final_url: str) -> str:
    parsed = urlsplit(final_url)
    if (parsed.hostname or "").casefold() != "web.archive.org":
        raise WaybackError(f"replay escaped the Wayback host: {final_url}")
    match = WAYBACK_CAPTURE_RE.search(parsed.path)
    return match.group(1) if match else ""


def _reject_wayback_error_html(text: str) -> None:
    folded = text[:200000].casefold()
    for marker in WAYBACK_ERROR_MARKERS:
        if marker in folded:
            raise WaybackError(f"Wayback returned an error/interstitial page: {marker}")


def _metadata_path(destination: Path) -> Path:
    return destination.with_name(destination.name + ".meta.json")


def _source_identity(selected_timestamp: str, digest: str) -> str:
    return stable_json_hash({"selected_capture_timestamp": selected_timestamp, "sha256": digest}, length=40)


def _write_capture(destination: Path, data: bytes, metadata: dict) -> None:
    atomic_write_bytes(destination, data)
    atomic_write_text(_metadata_path(destination), json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _adopt_capture(destination: Path, selected_timestamp: str) -> tuple[bytes, dict] | None:
    meta_path = _metadata_path(destination)
    if not destination.is_file() or not meta_path.is_file():
        return None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if str(metadata.get("selected_capture_timestamp") or "") != selected_timestamp:
            return None
        data = destination.read_bytes()
        if int(metadata.get("source_size") or -1) != len(data):
            return None
        if str(metadata.get("source_sha256") or "") != sha256_bytes(data):
            return None
        return data, metadata
    except Exception:
        return None


def _network_fetch(
    client: WaybackClient,
    selected_timestamp: str,
    original_url: str,
    destination: Path,
    max_bytes: int,
) -> tuple[bytes, dict]:
    response: ReplayBytes = client.stream_bytes(replay_url(selected_timestamp, original_url), max_bytes)
    actual_timestamp = _validate_wayback_final_url(response.final_url)
    classification, reason = classify_replay(response.data, response.content_type)
    if classification == "known_binary":
        return response.data, {
            "selected_capture_timestamp": selected_timestamp,
            "actual_replay_timestamp": actual_timestamp,
            "final_url": response.final_url,
            "http_status": response.status_code,
            "response_mimetype": _mime_only(response.content_type),
            "response_encoding": "",
            "content_classification": classification,
            "classification_reason": reason,
            "source_size": len(response.data),
            "source_sha256": sha256_bytes(response.data),
        }
    text, encoding = decode_document(response.data, response.content_type)
    _reject_wayback_error_html(text)
    digest = sha256_bytes(response.data)
    metadata = {
        "selected_capture_timestamp": selected_timestamp,
        "actual_replay_timestamp": actual_timestamp,
        "final_url": response.final_url,
        "http_status": response.status_code,
        "response_mimetype": _mime_only(response.content_type),
        "response_encoding": encoding,
        "content_classification": "known_text" if classification != "known_binary" else classification,
        "classification_reason": reason,
        "source_size": len(response.data),
        "source_sha256": digest,
        "source_identity": _source_identity(selected_timestamp, digest),
    }
    _write_capture(destination, response.data, metadata)
    return response.data, metadata


def _extract_from_bytes(data: bytes, url: str, plan: ExtractionPlan, encoding: str, content_type: str) -> tuple[dict, str]:
    text, chosen = decode_document(data, content_type, preferred=encoding)
    results = extract_fields(text, url, plan=plan)
    errors = [f"{name}: {result.error}" for name, result in results.items() if getattr(result, "error", "")]
    return results, "; ".join(errors)


def _candidate_query(combined: bool) -> str:
    if combined:
        return """
            SELECT id,original_url,selected_capture_timestamp,download_state,extraction_state,
                   extraction_fingerprint,extraction_source_identity,extraction_engine_version,
                   local_path,intended_local_path,response_encoding,response_mimetype,source_identity,
                   content_classification
            FROM pages
            WHERE id>? AND id<=?
              AND selected_capture_timestamp IS NOT NULL
              AND COALESCE(content_classification,'')<>'known_binary'
              AND (
                    download_state<>'complete'
                    OR extraction_state<>'complete'
                    OR extraction_fingerprint IS NULL OR extraction_fingerprint<>?
                    OR extraction_source_identity IS NULL OR extraction_source_identity<>COALESCE(source_identity,'')
                    OR extraction_engine_version IS NULL OR extraction_engine_version<>?
                  )
            ORDER BY id LIMIT ?
        """
    return """
        SELECT id,original_url,selected_capture_timestamp,download_state,extraction_state,
               local_path,intended_local_path,response_encoding,response_mimetype,source_identity,
               content_classification
        FROM pages
        WHERE id>? AND id<=?
          AND selected_capture_timestamp IS NOT NULL
          AND COALESCE(content_classification,'')<>'known_binary'
          AND download_state<>'complete'
        ORDER BY id LIMIT ?
    """


def _iter_candidates(
    database: sqlite3.Connection,
    *,
    combined: bool,
    fingerprint: str,
    high_water: int,
    batch_size: int,
) -> Iterator[sqlite3.Row]:
    after_id = 0
    query = _candidate_query(combined)
    while after_id < high_water:
        if combined:
            rows = database.execute(query, (after_id, high_water, fingerprint, EXTRACTOR_VERSION, batch_size)).fetchall()
        else:
            rows = database.execute(query, (after_id, high_water, batch_size)).fetchall()
        if not rows:
            return
        for row in rows:
            after_id = int(row["id"])
            yield row


def _run_pipeline(
    config: ProjectConfig,
    database: sqlite3.Connection,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None,
    *,
    combined: bool,
) -> None:
    cfg = config.normalized()
    plan = compile_extraction_plan(cfg.site) if combined else None
    fingerprint = cfg.site.extraction_fingerprint()
    captures_dir = cfg.output_dir / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)

    predicate = (
        "selected_capture_timestamp IS NOT NULL AND COALESCE(content_classification,'')<>'known_binary' AND "
        "(download_state<>'complete' OR extraction_state<>'complete' OR extraction_fingerprint IS NULL OR extraction_fingerprint<>? "
        "OR extraction_source_identity IS NULL OR extraction_source_identity<>COALESCE(source_identity,'') "
        "OR extraction_engine_version IS NULL OR extraction_engine_version<>?)"
        if combined else
        "selected_capture_timestamp IS NOT NULL AND COALESCE(content_classification,'')<>'known_binary' AND download_state<>'complete'"
    )
    args = (fingerprint, EXTRACTOR_VERSION) if combined else ()
    high_row = database.execute(f"SELECT MAX(id) AS high,COUNT(*) AS total FROM pages WHERE {predicate}", args).fetchone()
    high_water = int(high_row["high"] or 0)
    total = int(high_row["total"] or 0)
    stage = "build" if combined else "download"
    if not total:
        _emit(callback, ProgressEvent(stage, "No pages need download/extraction" if combined else "No pending archived pages to download", 0, 0))
        return

    client = WaybackClient(cfg.network, stop_event, delay=cfg.network.download_delay)
    workers = cfg.network.workers
    window = max(workers, workers * 2)
    iterator = _iter_candidates(
        database, combined=combined, fingerprint=fingerprint, high_water=high_water,
        batch_size=max(64, window * 4),
    )
    in_flight: dict[Future, tuple[int, str]] = {}
    completed = 0
    extracted_any = False
    start_time = time.monotonic()
    last_commit = start_time
    results_since_commit = 0

    def worker(row_data: dict, relative_path: str) -> WorkerOutcome:
        page_id = int(row_data["id"])
        url = str(row_data["original_url"])
        timestamp = str(row_data["selected_capture_timestamp"])
        destination = cfg.output_dir / relative_path
        try:
            # Build All reuses complete local captures for stale/changed extraction rules.
            if str(row_data.get("download_state") or "") == "complete" and destination.is_file():
                data = destination.read_bytes()
                digest = sha256_bytes(data)
                source_identity = _source_identity(timestamp, digest)
                fields = None
                extraction_error = ""
                if combined and plan is not None:
                    fields, extraction_error = _extract_from_bytes(
                        data, url, plan, str(row_data.get("response_encoding") or ""), str(row_data.get("response_mimetype") or "")
                    )
                return WorkerOutcome(
                    page_id=page_id, local_path=relative_path,
                    response_encoding=str(row_data.get("response_encoding") or ""),
                    response_mimetype=str(row_data.get("response_mimetype") or ""),
                    content_classification="known_text", source_size=len(data), source_sha256=digest,
                    source_identity=source_identity, fields=fields, extraction_error=extraction_error,
                )

            adopted = _adopt_capture(destination, timestamp)
            used_network = False
            if adopted is not None:
                data, metadata = adopted
            else:
                used_network = True
                data, metadata = _network_fetch(client, timestamp, url, destination, cfg.network.max_html_bytes)
            if metadata.get("content_classification") == "known_binary":
                return WorkerOutcome(
                    page_id=page_id, http_status=int(metadata.get("http_status") or 0) or None,
                    skipped=True, used_network=used_network,
                    actual_replay_timestamp=str(metadata.get("actual_replay_timestamp") or ""),
                    replay_final_url=str(metadata.get("final_url") or ""),
                    response_mimetype=str(metadata.get("response_mimetype") or ""),
                    content_classification="known_binary",
                    classification_reason=str(metadata.get("classification_reason") or ""),
                    source_size=int(metadata.get("source_size") or 0),
                    source_sha256=str(metadata.get("source_sha256") or ""),
                )
            fields = None
            extraction_error = ""
            if combined and plan is not None:
                fields, extraction_error = _extract_from_bytes(
                    data, url, plan, str(metadata.get("response_encoding") or ""), str(metadata.get("response_mimetype") or "")
                )
            return WorkerOutcome(
                page_id=page_id, local_path=relative_path,
                http_status=int(metadata.get("http_status") or 0) or None,
                actual_replay_timestamp=str(metadata.get("actual_replay_timestamp") or ""),
                replay_final_url=str(metadata.get("final_url") or ""),
                response_mimetype=str(metadata.get("response_mimetype") or ""),
                response_encoding=str(metadata.get("response_encoding") or ""),
                content_classification=str(metadata.get("content_classification") or "known_text"),
                classification_reason=str(metadata.get("classification_reason") or ""),
                source_size=int(metadata.get("source_size") or len(data)),
                source_sha256=str(metadata.get("source_sha256") or sha256_bytes(data)),
                source_identity=str(metadata.get("source_identity") or _source_identity(timestamp, sha256_bytes(data))),
                fields=fields, extraction_error=extraction_error, used_network=used_network,
            )
        except Stopped:
            raise
        except RateLimitDeferred:
            raise
        except PermanentWaybackError as exc:
            return WorkerOutcome(page_id=page_id, error=str(exc), retryable=False)
        except Exception as exc:
            return WorkerOutcome(page_id=page_id, error=str(exc), retryable=True)

    def prepare_and_submit(pool: ThreadPoolExecutor, row: sqlite3.Row) -> None:
        row_data = dict(row)
        timestamp = str(row_data["selected_capture_timestamp"])
        url = str(row_data["original_url"])
        existing = str(row_data.get("local_path") or row_data.get("intended_local_path") or "")
        relative = existing or str(Path("captures") / safe_filename_for_url(url, timestamp))
        if str(row_data.get("download_state") or "") != "complete":
            # This commit occurs before submission so the intended source identity/path
            # survives a process stop between scheduling and grouped result commits.
            prepare_download(database, int(row_data["id"]), relative)
            database.commit()
        in_flight[pool.submit(worker, row_data, relative)] = (int(row_data["id"]), relative)

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="archive-builder-replay") as pool:
            for _ in range(window):
                try:
                    prepare_and_submit(pool, next(iterator))
                except StopIteration:
                    break
            while in_flight:
                if stop_event.is_set():
                    for future in in_flight:
                        future.cancel()
                    raise Stopped
                done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED, timeout=0.2)
                if not done:
                    continue
                for future in done:
                    page_id, _ = in_flight.pop(future)
                    try:
                        outcome = future.result()
                    except RateLimitDeferred:
                        for queued in in_flight:
                            queued.cancel()
                        database.commit()
                        raise
                    if outcome.error:
                        save_download_result(database, page_id, "error", error=outcome.error)
                        record_error(database, stage, "page_download", outcome.error, page_id=page_id, retryable=outcome.retryable)
                    elif outcome.skipped:
                        save_download_result(
                            database, page_id, "skipped", http_status=outcome.http_status,
                            actual_replay_timestamp=outcome.actual_replay_timestamp,
                            replay_final_url=outcome.replay_final_url,
                            response_mimetype=outcome.response_mimetype,
                            content_classification=outcome.content_classification,
                            classification_reason=outcome.classification_reason,
                            source_size=outcome.source_size, source_sha256=outcome.source_sha256,
                        )
                    else:
                        save_download_result(
                            database, page_id, "complete", local_path=outcome.local_path,
                            http_status=outcome.http_status,
                            actual_replay_timestamp=outcome.actual_replay_timestamp,
                            replay_final_url=outcome.replay_final_url,
                            response_mimetype=outcome.response_mimetype,
                            response_encoding=outcome.response_encoding,
                            content_classification=outcome.content_classification,
                            classification_reason=outcome.classification_reason,
                            source_size=outcome.source_size, source_sha256=outcome.source_sha256,
                            source_identity=outcome.source_identity,
                        )
                        if combined and outcome.fields is not None:
                            state = "error" if outcome.extraction_error else "complete"
                            save_extraction_result(
                                database, page_id, fingerprint, outcome.fields, state=state,
                                error=outcome.extraction_error or None,
                                source_identity=outcome.source_identity,
                                extractor_version=EXTRACTOR_VERSION,
                                bump_revision=False,
                            )
                            extracted_any = True
                            if outcome.extraction_error:
                                record_error(database, "extract", "field_extraction", outcome.extraction_error, page_id=page_id, retryable=False)
                    completed += 1
                    results_since_commit += 1
                    now = time.monotonic()
                    if results_since_commit >= 32 or now - last_commit >= 1.0 or completed == total:
                        database.commit()
                        results_since_commit = 0
                        last_commit = now
                    elapsed = max(0.001, now - start_time)
                    success_rate = completed / elapsed
                    _emit(callback, ProgressEvent(
                        stage,
                        f"Processed {completed:,} of {total:,} pages",
                        completed, total,
                        {
                            "completed_pages_per_second": success_rate,
                            "request_starts_per_second": client.gate.starts_per_second,
                            "request_starts": client.gate.starts,
                        },
                    ))
                    try:
                        prepare_and_submit(pool, next(iterator))
                    except StopIteration:
                        pass
        if extracted_any:
            bump_extraction_revision(database)
        database.commit()
    finally:
        client.close()
        # Any submitted item without a committed outcome is recoverable pending work,
        # whether the run stopped, paused on rate limits, or failed unexpectedly.
        database.execute("UPDATE pages SET download_state='pending' WHERE download_state='downloading'")
        database.execute("UPDATE pages SET extraction_state='pending' WHERE extraction_state='extracting'")
        database.commit()


def download_pages(
    config: ProjectConfig,
    database: sqlite3.Connection,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> None:
    _run_pipeline(config, database, stop_event, callback, combined=False)


def download_and_extract_pages(
    config: ProjectConfig,
    database: sqlite3.Connection,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> None:
    _run_pipeline(config, database, stop_event, callback, combined=True)
