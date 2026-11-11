from __future__ import annotations

import argparse
import threading
from datetime import datetime
from pathlib import Path

from .builder.preview import preview_page
from .builder.workflow import run_operation
from .config import (
    CONFIG_NAME,
    DedupConfig,
    ExportConfig,
    NetworkConfig,
    PERFORMANCE_CDX_DELAY,
    PERFORMANCE_CDX_WORKERS,
    PERFORMANCE_REPLAY_DELAY,
    PERFORMANCE_WORKERS,
    ProjectConfig,
    SiteProfile,
    load_project_config,
    new_project_config,
    nothingtoxic_profile,
    save_project_config,
)
from .database.connection import open_database
from .constants import APP_NAME, VERSION
from .events import ProgressEvent
from .project import create_project_scaffold


def _load(project: str):
    root = Path(project).expanduser().resolve()
    path = root / CONFIG_NAME
    if path.exists():
        return load_project_config(path)
    cfg = new_project_config(root)
    save_project_config(cfg)
    return cfg


def _progress(event: ProgressEvent) -> None:
    suffix = ""
    if event.current is not None and event.total is not None:
        suffix = f" [{event.current}/{event.total}]"
    detail = event.detail or {}
    if "completed_pages_per_second" in detail or "request_starts_per_second" in detail:
        suffix += (
            f" complete={float(detail.get('completed_pages_per_second') or 0):.2f}/s"
            f" starts={float(detail.get('request_starts_per_second') or 0):.2f}/s"
        )
    print(f"{event.stage}: {event.message}{suffix}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archive-builder")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create a new Archive Builder project")
    init.add_argument("project", help="Dedicated project folder to create")
    init.add_argument("--preset", choices=("nothingtoxic", "custom"), default="nothingtoxic")
    init.add_argument("--name", help="Site profile name")
    init.add_argument("--domain", help="Domain for a custom project, e.g. example.com")
    init.add_argument("--path", default=None, help="Content path prefix, e.g. /media/")
    init.add_argument("--no-subdomains", action="store_true")
    init.add_argument("--from-year", type=int)
    init.add_argument("--to-year", type=int)
    init.add_argument("--workers", type=int, default=PERFORMANCE_WORKERS)
    init.add_argument("--download-delay", type=float, default=PERFORMANCE_REPLAY_DELAY, help="Shared replay request-start spacing in seconds")
    init.add_argument("--cdx-workers", type=int, default=PERFORMANCE_CDX_WORKERS)
    init.add_argument("--cdx-delay", type=float, default=PERFORMANCE_CDX_DELAY, help="Shared CDX request-start spacing in seconds")
    init.add_argument("--day-first", action="store_true", help="Interpret ambiguous numeric posting dates as day-first")
    init.add_argument("--xlsx", action="store_true", help="Also create the optional A-Z formatted XLSX export")
    init.add_argument("--no-collapse-urlkey", action="store_true", help="Do not send collapse=urlkey to CDX")
    init.add_argument("--no-cross-domain-dedupe", action="store_true", help="Disable cross-host duplicate detection")
    init.add_argument("--duplicate-fields", default="post_number,title", help="Comma-separated duplicate identity fields")
    for name in ("index", "download", "extract", "dedupe", "export", "all"):
        cmd = sub.add_parser(name)
        cmd.add_argument("project")
    preview = sub.add_parser("preview", help="Test extraction rules on one original or Wayback URL")
    preview.add_argument("project")
    preview.add_argument("url")
    sub.add_parser("gui", help="Open the desktop interface")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        from .app import main as gui_main
        gui_main()
        return 0

    if args.command == "init":
        root = Path(args.project).expanduser().resolve()
        if args.preset == "nothingtoxic":
            site = nothingtoxic_profile()
        else:
            if not args.domain:
                raise SystemExit("archive-builder init --preset custom requires --domain")
            site = SiteProfile(
                name=args.name or args.domain,
                domain=args.domain,
                include_subdomains=not args.no_subdomains,
                content_path=args.path or "/",
                from_year=args.from_year or 1996,
                to_year=args.to_year or datetime.now().year,
                rules=nothingtoxic_profile().rules,
            )
        if args.name:
            site.name = args.name
        if args.path is not None:
            site.content_path = args.path
        if args.no_subdomains:
            site.include_subdomains = False
        if args.from_year is not None:
            site.from_year = args.from_year
        if args.to_year is not None:
            site.to_year = args.to_year
        site.day_first_dates = bool(args.day_first)

        fields = tuple(part.strip() for part in args.duplicate_fields.split(",") if part.strip())
        dedup = DedupConfig(
            cdx_collapse_urlkey=not args.no_collapse_urlkey,
            cross_domain_enabled=not args.no_cross_domain_dedupe,
            match_fields=fields,
            preferred_domain=site.domain,
        )
        network = NetworkConfig(
            workers=args.workers,
            download_delay=args.download_delay,
            cdx_workers=args.cdx_workers,
            cdx_delay=args.cdx_delay,
        )
        export = ExportConfig(create_xlsx_presentation=bool(args.xlsx))
        cfg = ProjectConfig(root, site, network, dedup, export).normalized()
        scaffold = create_project_scaffold(cfg)
        print(f"Created Archive Builder project at {scaffold.root}")
        print(f"Config: {scaffold.config_path}")
        print(f"Database: {scaffold.database_path}")
        return 0

    cfg = _load(args.project)
    stop_event = threading.Event()
    if args.command == "preview":
        original, timestamp, results = preview_page(cfg, args.url, stop_event)
        print(f"URL: {original}\nCapture: {timestamp}")
        for name, result in results.items():
            print(f"{name}: {result.value!r} ({result.method}, {result.confidence})")
        return 0

    # A CLI operation owns this writer, so recovering abandoned states is explicit here.
    database = open_database(cfg.output_dir, recover_abandoned=True)
    try:
        result = run_operation(cfg, database, args.command, stop_event, _progress)
        for key, value in result.items():
            print(f"{key}: {value}")
    finally:
        database.close()
    return 0
