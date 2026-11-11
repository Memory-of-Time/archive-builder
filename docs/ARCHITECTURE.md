# Archive Builder Architecture

## Archive Scout reference

The supplied Archive Scout repository was used as the architectural reference. Archive Builder intentionally carries forward these patterns:

- SQLite is the authoritative work-state store.
- Work survives application restarts and interrupted runs.
- Wayback CDX discovery happens before content retrieval.
- Network work runs outside the Tk UI thread.
- Progress is delivered through small/coalesced progress events.
- Long-running queues are persisted instead of represented only in memory.
- CSV is a derived export, not the project database.

## Pipeline

```text
Site profile
  -> CDX inventory pass
  -> CDX representative-HTML pass
  -> pages table
  -> resumable archived HTML download
  -> configurable field extraction
  -> extracted_fields table
  -> duplicate analysis
  -> logical-post groups
  -> chronological CSV export
```

## CDX duplicate handling

`DedupConfig.cdx_collapse_urlkey` controls whether Archive Builder sends `collapse=urlkey` to the CDX API. It is enabled by default. This reduces repeated archive captures of the same normalized URL during discovery, but Archive Builder still retains the earliest capture timestamp separately.

The setting is included in the resumable CDX query signature. Changing it creates a new indexing signature rather than incorrectly resuming an incompatible query.

## Why there are two CDX passes

A single query cannot safely represent both concepts required by Archive Builder:

1. the earliest capture of a URL, and
2. the earliest capture that is a successful HTML page suitable for extraction.

The inventory pass therefore avoids HTTP/MIME restrictions and records the earliest capture found for every unique content URL. The representative pass applies `statuscode:200` and `mimetype:text/html` and records the earliest usable HTML capture. The CSV's `First Capture Date` comes from the inventory timestamp.

## Generic site profiles

A site profile defines:

- domain
- whether subdomains are included
- content path
- archive capture year range
- five field extraction rules

A project also contains a generic deduplication profile defining:

- whether CDX `collapse=urlkey` is enabled
- whether cross-host logical-post matching is enabled
- which extracted fields form the duplicate identity
- whether matches must cross hostnames
- which domain is preferred when selecting a canonical URL

NothingToxic is the starter profile (`nothingtoxic.com`, subdomains enabled, `/media/`) rather than a hardcoded limitation.

## Extraction rule model

Each of the five fields can independently use:

- automatic heuristic extraction
- CSS selector
- metadata selector
- regular expression
- text after marker
- text between markers
- URL regular expression

Changing extraction rules changes a profile fingerprint. Previously downloaded pages can then be re-extracted without repeating the Wayback indexing/download stages.

## Logical-post duplicate analysis

Duplicate analysis happens after extraction because the strongest identity often comes from fields that do not exist in CDX metadata.

The default NothingToxic duplicate identity is:

```text
post_number + title
```

The first configured identity field is treated as the anchor. With the default settings, pages with the same Post Number on different hostnames become candidates. If all configured fields are present and normalize to the same value, the group is **exact** and defaults to `merge`. If the anchor matches but a secondary field is missing or differs, the group is **probable** and defaults to `review`.

Title normalization removes differences in case, repeated whitespace, HTML entities, and punctuation. This means titles such as `Guy Crashes Motorcycle!` and `Guy Crashes Motorcycle` can still be exact matches.

Probable groups are deliberately not auto-merged. A user can review them in the Duplicates tab and choose Merge or Keep Separate.

## Canonical records and first-capture dates

All alias URLs remain in the `pages` table. A duplicate group chooses one canonical page using this priority:

1. most complete extracted metadata
2. preferred domain/hostname
3. earliest first capture
4. deterministic URL order

When a group is exported as one logical post, the canonical URL is used. Missing fields may be filled from another alias in the same group. The logical post's First Capture Date is the earliest first-capture timestamp across every URL in that group.

## Persistence

`archive_builder.sqlite3` contains:

- `pages` — one row per unique archived content URL
- `extracted_fields` — current structured values and provenance per field
- `index_state` — resumable CDX phase/year state and resume keys
- `operation_runs` — operation history/recovery status
- `errors` — non-fatal and fatal pipeline errors
- `duplicate_groups` — exact/probable logical-post groups, review decisions, canonical page
- `duplicate_group_members` — every alias URL belonging to a duplicate group

Schema v2 is introduced in Archive Builder v0.1.2. Existing schema-v1 projects are migrated automatically when opened.

On startup, transient `downloading` / `extracting` states are returned to a resumable state.


## v0.1.3 pipeline and schema v3

Archive Builder v0.1.3 changes the runtime pipeline without changing the seven logical output fields. New projects use 10 replay workers with a shared 0.125-second request-start gate and 10 CDX workers with a shared 0.75-second gate. Old project JSON files keep their saved explicit values until the performance preset is applied.

### Coverage-aware indexing

Each of the inventory and representative phases begins with the complete requested interval. Resume coverage is stored by phase/range/strategy/unit/signature. If a broad response proves pagination is required, numbered pages are processed through a bounded rolling executor. Failed broad ranges may be split; successful scopes are not subdivided into years automatically. A repeated continuation token or repeated response fingerprint stops the traversal with incomplete coverage preserved.

CDX rows carry the server `urlkey`. The database stores original URL aliases under verified urlkey identity so independently returned pages/windows cannot create chronology-dependent identity decisions. Cross-host post deduplication remains a later, extraction-derived layer.

### Combined replay and extraction

Build All uses a rolling window near `2 * workers`. The coordinator selects candidates through keyset batches and persists each intended source path before submission. A worker streams and validates the replay, atomically installs full source bytes plus a provenance sidecar, decodes the source, and applies the precompiled five-field extraction plan. The coordinator is the only SQLite writer and flushes result groups by count or elapsed time.

Download state and extraction state remain independent. Saved captures can be re-extracted locally after rule or extractor changes. Extraction completion is associated with source identity, rule fingerprint, and extractor version.

### Schema v3

Schema v3 adds:

- `page_aliases` for original URL aliases tied to verified server identity
- `index_coverage` for phase/range/strategy/page-or-resume completion
- capture/replay provenance fields on `pages`
- source size/hash/identity and extraction-source/version fields
- raw field values and date precision
- duplicate identity/member signatures and `duplicate_review_history`
- `project_state` extraction/dedupe revisions

A writable open of a schema-v2 project first creates an SQLite backup named `archive_builder.schema2.backup.sqlite3`. Ordinary/read-only opens do not reset transient states; abandoned-writer recovery is explicit.

### Chronology and export

Posted dates are stored as full day (`YYYY-MM-DD`), month (`YYYY-MM`), year (`YYYY`), or unresolved. Missing parts are not fabricated. Complete human-facing dates use DD/MM/YYYY, while one shared ISO/precision-aware sorter drives Results, CSV and XLSX ordering.

The CSV remains exactly seven logical fields. Optional XLSX adds an unmerged Data sheet and an A-Z Reading sheet with horizontal spans A:B, C:E, F:K, L:N, O:U, V:W and X:Z.
