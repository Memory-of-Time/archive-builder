# Changelog

## 1.0.0 — 2026-11-11

- First public release, including the v0.1.3 pipeline, chronology, extraction, deduplication, and export improvements.
- Updated package metadata, application title, user agent, Windows version resources, macOS bundle metadata, release notes, and repository links for v1.0.0.
- Added CLI `--version` and a release verifier that rejects mismatched version tags before packaging.
- Consolidated GitHub workflows, issue templates, and dependency updates under `.github`; restored the canonical `.gitignore`.
- Removed Linux CI's unconditional system-Python Tk installation and added explicit runtime/Tcl checks for the selected build Python.
- Kept schema v3, existing project settings, saved captures, extraction rules, and export formats compatible with v0.1.3.
- Kept the status bar visible and made Site & Build scrollable at smaller window sizes; included the selected Linux Python's library directory during frozen dependency discovery.

## 0.1.3

- Applied the Speed and Chronology audit to the Archive Builder acquisition/extraction pipeline.
- Added the Archive Scout v1.0.5-style performance preset: 10 replay workers with 0.125-second shared request-start spacing and 10 CDX workers with 0.75-second shared spacing. Existing saved project network values are preserved until the preset is explicitly applied.
- Changed Build All to use bounded simultaneous download + five-field extraction while retaining independent Download Pages and Extract Fields operations.
- Added keyset candidate batches, a rolling future window near 2x workers, and grouped SQLite result commits by count/time.
- Replaced unconditional per-year CDX traversal with broad full-range coverage first, durable page/resume coverage, bounded pagination, and failure-only range splitting.
- Added strict CDX structural validation, repeated response/token detection, server `urlkey` storage, URL aliases, and verified urlkey reconciliation.
- Added schema v3 with automatic schema-v2 backup/migration and explicit startup recovery rather than resetting active states on ordinary database open.
- Added selected-capture/source provenance, replay final URL/timestamp, MIME/encoding/classification metadata, source hashes, and extraction source/version associations.
- Added atomic capture bytes plus validation sidecars and safe adoption of completed staged captures after interruption.
- Added streamed response-size enforcement, binary signature/MIME/sniff classification, Wayback interstitial/error rejection, and live-site escape rejection.
- Added precompiled extraction plans, lazy page representations, and removal of the hidden 300-character `text_after` truncation.
- Added precision-aware posted dates, configurable day-first parsing, DD/MM/YYYY display for complete dates, and a shared true-date chronology sorter with undated rows last.
- Kept CSV at exactly seven logical fields and added optional XLSX export with an unmerged Data sheet plus A-Z Reading sheet spans.
- Optimized duplicate analysis/export so dedupe runs once per extraction revision, memberships are fetched in bulk, and reviewed choices/canonical records survive unchanged groups.
- Added bounded Results queries and guarded GUI startup/database-open handling.
- Added focused v0.1.3 migration, chronology, export, CDX, replay/content, extraction, dedupe, and bounded-pipeline tests.

## 0.1.2

- Added an optional CDX `collapse=urlkey` project setting, enabled by default.
- Added cross-host duplicate detection for cases where the same logical post appears under different hostnames/subdomains.
- Added configurable duplicate identity fields; the NothingToxic default is `post_number,title`.
- Added conservative duplicate classification: exact matches auto-merge, probable matches remain separate until reviewed.
- Added canonical-record selection using metadata completeness, preferred domain, earliest capture, and deterministic URL ordering.
- Merged logical posts now use the earliest First Capture Date across all alias URLs while preserving every alias in SQLite.
- Added a dedicated **Duplicates** tab with Merge Group, Keep Separate, and Use Selected URL controls.
- Added `archive_builder_duplicates.csv` as an audit report for duplicate groups and aliases.
- Added `archive-builder dedupe` to the CLI and deduplication options to `archive-builder init`.
- Added automatic database migration from schema v1 to schema v2 for existing v0.1.0/v0.1.1 projects.

## 0.1.1

- Added a guided three-step **New Project** wizard.
- New projects now collect project location/name, website target, content path, subdomain scope, capture-year range, and worker count before creation.
- Added NothingToxic and Custom Website starter choices so project creation is not hardcoded to one site.
- New projects immediately create `archive_builder.json`, `archive_builder.sqlite3`, `captures/`, and `exports/` in a dedicated project folder.
- Added cross-platform-safe project-folder naming and protection against overwriting an existing Archive Builder project.
- Content paths now tolerate an optional trailing `*` and normalize it to the same prefix behavior; wildcards are not required.
- Expanded `archive-builder init` with custom-site project options.

## 0.1.0

- Initial Archive Builder repository based on the supplied Archive Scout architecture.
- Added generic archived-site profiles and configurable extraction rules.
- Added NothingToxic starter profile for `nothingtoxic.com` + subdomains under `/media/`.
- Added two-pass CDX indexing to separate first capture from first usable HTML capture.
- Added restart-safe SQLite persistence, concurrent page downloads, extraction preview, and Google Sheets-ready CSV export.
- Added desktop UI, CLI, and offline tests.
- Added release-ready GitHub Actions, platform packaging, checksums, installers, and stable README download links for Windows, Linux, and macOS.
