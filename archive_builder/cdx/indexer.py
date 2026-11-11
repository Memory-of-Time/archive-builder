from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..config import ProjectConfig
from ..database.repositories import (
    get_index_coverage,
    record_error,
    save_index_coverage,
    upsert_inventory_rows,
    upsert_representative_rows,
)
from ..events import ProgressEvent, Stopped
from .client import RateLimitDeferred, WaybackClient, WaybackError
from .parameters import build_params, query_signature


@dataclass(frozen=True, slots=True)
class PreparedScope:
    domain: str
    include_subdomains: bool
    content_path: str

    def matches(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except Exception:
            return False
        host = (parsed.hostname or "").casefold()
        if self.include_subdomains:
            host_ok = host == self.domain or host.endswith("." + self.domain)
        else:
            host_ok = host == self.domain
        return host_ok and parsed.path.startswith(self.content_path)


def prepare_scope(config: ProjectConfig) -> PreparedScope:
    cfg = config.normalized()
    return PreparedScope(cfg.site.domain, cfg.site.include_subdomains, cfg.site.content_path)


def _emit(callback: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def _store_rows(database: sqlite3.Connection, phase: str, rows: list[dict[str, str]], scope: PreparedScope) -> int:
    matching = [row for row in rows if scope.matches(row.get("original", ""))]
    if phase == "inventory":
        upsert_inventory_rows(database, matching)
    else:
        upsert_representative_rows(database, matching)
    return len(matching)


def _resume_range(
    cfg: ProjectConfig,
    database: sqlite3.Connection,
    client: WaybackClient,
    scope: PreparedScope,
    phase: str,
    start_year: int,
    end_year: int,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None,
    *,
    allow_paged_switch: bool,
    split_depth: int = 0,
) -> None:
    signature = query_signature(cfg, phase)
    range_start = f"{start_year:04d}0101000000"
    range_end = f"{end_year:04d}1231235959"
    unit_key = "resume"
    state = get_index_coverage(database, phase, range_start, range_end, "resume", unit_key, signature)
    if state and int(state["complete"]):
        _emit(callback, ProgressEvent("index", f"{phase.title()} {start_year}–{end_year} already complete"))
        return

    resume_key = str(state["resume_key"]) if state and state["resume_key"] else None
    seen = int(state["seen"] or 0) if state else 0
    seen_tokens: set[str] = set()
    if resume_key:
        seen_tokens.add(resume_key)
    seen_fingerprints: set[str] = set()
    first_response = resume_key is None

    while True:
        if stop_event.is_set():
            raise Stopped
        params = build_params(cfg, phase, range_start, range_end, resume_key)
        try:
            result = client.cdx(params)
        except RateLimitDeferred:
            raise
        except WaybackError as exc:
            # Only split an actually failed range, never pre-probe every year/month.
            if start_year < end_year and split_depth < 5:
                middle = (start_year + end_year) // 2
                record_error(database, "index", "range_split", f"{phase} {start_year}-{end_year}: {exc}")
                database.commit()
                _resume_range(
                    cfg, database, client, scope, phase, start_year, middle,
                    stop_event, callback, allow_paged_switch=False, split_depth=split_depth + 1,
                )
                _resume_range(
                    cfg, database, client, scope, phase, middle + 1, end_year,
                    stop_event, callback, allow_paged_switch=False, split_depth=split_depth + 1,
                )
                save_index_coverage(
                    database, phase, range_start, range_end, "resume", unit_key, signature,
                    None, True, seen, "split-complete",
                )
                database.commit()
                return
            record_error(database, "index", "cdx_request", f"{phase} {start_year}-{end_year}: {exc}")
            database.commit()
            raise

        # Valid rows are idempotently staged, but repeated response/token loops do not
        # advance coverage as though traversal completed.
        if result.response_fingerprint in seen_fingerprints:
            save_index_coverage(
                database, phase, range_start, range_end, "resume", unit_key, signature,
                resume_key, False, seen, result.response_fingerprint,
            )
            database.commit()
            raise WaybackError("CDX repeated the same response; progress was preserved incomplete")
        seen_fingerprints.add(result.response_fingerprint)

        matched = _store_rows(database, phase, result.rows, scope)
        seen += matched
        next_key = result.resume_key
        if next_key and (next_key == resume_key or next_key in seen_tokens):
            save_index_coverage(
                database, phase, range_start, range_end, "resume", unit_key, signature,
                resume_key, False, seen, result.response_fingerprint,
            )
            database.commit()
            raise WaybackError("CDX repeated a continuation token; progress was preserved incomplete")
        if next_key:
            seen_tokens.add(next_key)

        save_index_coverage(
            database, phase, range_start, range_end, "resume", unit_key, signature,
            next_key, next_key is None, seen, result.response_fingerprint,
        )
        database.commit()
        _emit(callback, ProgressEvent(
            "index",
            f"{phase.title()} {start_year}–{end_year}: {seen:,} matching URLs processed",
            detail={"phase": phase, "seen": seen, "strategy": "resume", "request_starts_per_second": client.gate.starts_per_second},
        ))

        if next_key is None:
            return

        # A continuation key proves this is not a one-response scope. At that point,
        # try the CDX Pagination API once. If unsupported, retain the known-good resume
        # strategy without further endpoint probing.
        if first_response and allow_paged_switch:
            try:
                pages = client.num_pages(params, page_size=9)
            except WaybackError:
                pages = 0
            if pages > 1:
                try:
                    _paged_range(
                        cfg, database, client, scope, phase, start_year, end_year,
                        pages, stop_event, callback,
                    )
                    save_index_coverage(
                        database, phase, range_start, range_end, "resume", unit_key, signature,
                        None, True, seen, "paged-complete",
                    )
                    database.commit()
                    return
                except RateLimitDeferred:
                    raise
                except WaybackError:
                    # Keep the stable resume key from the validated broad response and
                    # continue sequentially. Completed paged units remain durable.
                    pass
        first_response = False
        resume_key = next_key


def _paged_range(
    cfg: ProjectConfig,
    database: sqlite3.Connection,
    client: WaybackClient,
    scope: PreparedScope,
    phase: str,
    start_year: int,
    end_year: int,
    page_count: int,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None,
) -> None:
    signature = query_signature(cfg, phase)
    range_start = f"{start_year:04d}0101000000"
    range_end = f"{end_year:04d}1231235959"
    workers = cfg.network.cdx_workers
    window = max(workers, workers * 2)
    completed = 0
    def pending_page_iterator():
        nonlocal completed
        for page in range(page_count):
            state = get_index_coverage(database, phase, range_start, range_end, "paged", str(page), signature)
            if state and int(state["complete"]):
                completed += 1
                continue
            yield page

    iterator = iter(pending_page_iterator())
    in_flight: dict[Future, int] = {}

    def submit(pool: ThreadPoolExecutor, page: int) -> None:
        params = build_params(cfg, phase, range_start, range_end, page=page, page_size=9)
        in_flight[pool.submit(client.cdx, params)] = page

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="archive-builder-cdx") as pool:
        for _ in range(window):
            try:
                submit(pool, next(iterator))
            except StopIteration:
                break
        while in_flight:
            if stop_event.is_set():
                for future in in_flight:
                    future.cancel()
                raise Stopped
            done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED, timeout=0.25)
            if not done:
                continue
            for future in done:
                page = in_flight.pop(future)
                result = future.result()
                matched = _store_rows(database, phase, result.rows, scope)
                save_index_coverage(
                    database, phase, range_start, range_end, "paged", str(page), signature,
                    None, True, matched, result.response_fingerprint,
                )
                database.commit()
                completed += 1
                _emit(callback, ProgressEvent(
                    "index",
                    f"{phase.title()} paged traversal: {completed:,}/{page_count:,} pages complete",
                    completed, page_count,
                    {"phase": phase, "strategy": "paged", "page": page, "request_starts_per_second": client.gate.starts_per_second},
                ))
                try:
                    submit(pool, next(iterator))
                except StopIteration:
                    pass


def index_site(
    config: ProjectConfig,
    database: sqlite3.Connection,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> None:
    cfg = config.normalized()
    scope = prepare_scope(cfg)
    client = WaybackClient(cfg.network, stop_event, delay=cfg.network.cdx_delay)
    try:
        for phase in ("inventory", "representative"):
            _emit(callback, ProgressEvent(
                "index",
                f"Starting {phase} coverage for {cfg.site.from_year}–{cfg.site.to_year}",
                detail={"phase": phase, "strategy": "broad-first"},
            ))
            _resume_range(
                cfg, database, client, scope, phase,
                cfg.site.from_year, cfg.site.to_year,
                stop_event, callback, allow_paged_switch=True,
            )
    finally:
        client.close()
