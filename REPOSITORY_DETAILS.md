# Repository details

Product: Archive Builder
Version: 1.0.0
Repository: https://github.com/Memory-of-Time/archive-builder
License: MIT
Python: 3.11+
Desktop UI: Tkinter/ttk
Project store: SQLite
Archive source: Internet Archive Wayback Machine

## Main packages

- `archive_builder/cdx`: CDX request construction, indexing, response handling.
- `archive_builder/builder`: download, preview, workflow, extraction orchestration, duplicate analysis, CSV export.
- `archive_builder/extraction`: configurable rule engine and date parsing.
- `archive_builder/database`: SQLite schema, schema migration, and repositories.
- `archive_builder/ui`: desktop application, duplicate review, and event queue.

## Deduplication model

- CDX capture-level collapse: optional `collapse=urlkey`.
- Logical-post duplicate detection: configurable identity fields after extraction.
- NothingToxic default: Post Number + normalized Title across different hostnames.
- Exact groups auto-merge; probable groups require review.
- Alias URLs remain preserved in SQLite.

## Release artifacts

- `ArchiveBuilder-Windows-x64.zip`
- `ArchiveBuilder-Linux-x64.tar.gz`
- `ArchiveBuilder-macOS-Universal.zip`

Each package has a SHA-256 checksum.

Schema: 3
