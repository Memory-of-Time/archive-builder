from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable

from ..cdx import index_site
from ..config import ProjectConfig, save_project_config
from ..database.repositories import dedupe_is_current, finish_operation, start_operation
from ..events import ProgressEvent, Stopped
from .deduplicate import detect_duplicates
from .downloader import RateLimitDeferred, download_and_extract_pages, download_pages
from .export import export_csv, export_duplicate_report, export_xlsx
from .extractor import extract_downloaded_pages


def _ensure_dedupe(
    cfg: ProjectConfig,
    database: sqlite3.Connection,
    callback: Callable[[ProgressEvent], None] | None,
    *,
    force: bool = False,
) -> None:
    if not cfg.dedup.cross_domain_enabled:
        return
    if force or not dedupe_is_current(database):
        detect_duplicates(cfg, database, callback)
    elif callback:
        callback(ProgressEvent("dedupe", "Duplicate groups are current; reusing existing analysis."))


def run_operation(
    config: ProjectConfig,
    database: sqlite3.Connection,
    mode: str,
    stop_event: threading.Event,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> dict[str, object]:
    cfg = config.normalized()
    save_project_config(cfg)
    run_id = start_operation(database, mode)
    try:
        if mode in {"index", "all"}:
            index_site(cfg, database, stop_event, callback)

        if mode == "download":
            download_pages(cfg, database, stop_event, callback)
        elif mode == "extract":
            extract_downloaded_pages(cfg, database, stop_event, callback)
        elif mode == "all":
            # v0.1.3 Build All is one bounded replay+extract pipeline. Existing valid
            # captures are reused locally and changed rules do not cause re-downloads.
            download_and_extract_pages(cfg, database, stop_event, callback)

        if mode == "dedupe":
            _ensure_dedupe(cfg, database, callback, force=True)
        elif mode == "all":
            _ensure_dedupe(cfg, database, callback)

        paths: dict[str, object] = {}
        if mode in {"export", "all"}:
            # Export only recomputes duplicate analysis if extraction has changed since
            # the last analysis. Build All therefore runs it at most once per revision.
            _ensure_dedupe(cfg, database, callback)
            csv_path = export_csv(database, cfg.output_dir)
            paths["csv"] = csv_path
            duplicate_path = export_duplicate_report(database, cfg.output_dir)
            paths["duplicates_csv"] = duplicate_path
            if cfg.export.create_xlsx_presentation:
                xlsx_path = export_xlsx(
                    database,
                    cfg.output_dir,
                    presentation_max_rows=cfg.export.presentation_max_rows,
                )
                paths["xlsx"] = xlsx_path
            if callback:
                callback(ProgressEvent("export", f"CSV written to {csv_path}"))
                callback(ProgressEvent("export", f"Duplicate report written to {duplicate_path}"))
                if "xlsx" in paths:
                    callback(ProgressEvent("export", f"Workbook written to {paths['xlsx']}"))

        finish_operation(database, run_id, "complete", "Operation complete")
        return paths
    except RateLimitDeferred as exc:
        finish_operation(database, run_id, "paused", str(exc))
        if callback:
            callback(ProgressEvent("network", f"Paused safely: {exc}"))
        raise
    except Stopped:
        finish_operation(database, run_id, "interrupted", "Stopped by user")
        raise
    except Exception as exc:
        finish_operation(database, run_id, "error", str(exc))
        raise
