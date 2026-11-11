# Archive Builder v1.0.0

Archive Builder turns archived websites into structured, chronological datasets. It uses the Wayback Machine for URL discovery and captures, SQLite for resumable project state, configurable extraction rules for page fields, and CSV export for Google Sheets.

## Downloads

- **[Download for Windows x64](https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-Windows-x64.zip)**
- **[Download for Linux x64](https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-Linux-x64.tar.gz)**
- **[Download for macOS Intel and Apple Silicon](https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-macOS-Universal.zip)**

These links target the latest published GitHub release. The v1.0.0 packages become available after the release workflow succeeds and publishes the release.

## What Archive Builder does

Archive Builder is a separate repository inspired by the architecture of **Archive Scout**. It keeps the parts that are useful for large archive jobs—CDX-first indexing, persistent SQLite state, resumability, bounded worker-thread networking, progress events, and a desktop UI—but focuses on building structured site indexes instead of keyword research.

The first bundled profile is **NothingToxic**. It indexes archived `nothingtoxic.com` pages, including subdomains, whose path begins with `/media/`. NothingToxic is the first test case, not a hardcoded limitation: the same project system can be configured for other sites.

## What is included in v1.0.0

This is the first public release. It includes the acquisition and chronology improvements from v0.1.3, with the same project format and database schema.

- New projects use the performance preset: **10 replay workers**, **0.125 s shared replay request-start spacing**, **10 CDX workers**, and **0.75 s shared CDX spacing**.
- Existing project JSON files keep their saved network values. Use **Apply Performance Preset** in the GUI if you want to upgrade an older project's settings explicitly.
- **Build All** overlaps download and five-field extraction through a bounded rolling queue instead of completing the entire download stage first.
- **Download Pages** and **Extract Fields** remain independent operations. Changed extraction rules reprocess saved local captures without redownloading them.
- CDX indexing starts with the complete requested date interval instead of automatically probing every year. Numbered pagination/resume traversal is persisted and failed broad ranges can be split for recovery.
- Server `urlkey`, URL aliases, selected capture identity, actual replay provenance, content classification, encoding, source hashes, and extraction/source associations are persisted in schema v3.
- Capture files are written atomically with validation metadata so a complete file can be adopted after an interruption between file completion and database commit.
- Posted dates keep their actual precision. Archive Builder no longer invents January 1, the first of a month, or year 1900 for incomplete date evidence.
- Complete dates are displayed as **DD/MM/YYYY** while internal sorting remains chronological.
- Optional XLSX export provides an A–Z reading layout while keeping an unmerged seven-column Data sheet.

See `RELEASE_NOTES.md` for the launch summary and `SOURCE_VALIDATION.txt` for checks performed and remaining verification. Request-start spacing is a pacing setting, not a guarantee of completed pages per second.

## Output columns

The primary CSV is always exactly:

```text
Post Number | Posted Date | Title | Category | Description | URL | First Capture Date
```

`Post Number` is always the leftmost column. `Posted Date` is extracted from the archived content page. `First Capture Date` is derived independently from the earliest Wayback capture discovered **inside the project's selected capture-year scope**. If you restrict a project to a subset of a site's history, First Capture Date means the earliest capture found in that configured interval; use the site's full archival interval when you need the earliest capture Archive Builder can discover across the full history.

Results with valid posting dates are sorted newest to oldest. Undated/unresolved pages are preserved and sorted last.

## Creating a project

Click **New…** in the desktop app to open the project wizard. It asks for the dedicated project name/location, starter profile, domain, content-path prefix, subdomain scope, capture years, network settings, date interpretation, export options, and deduplication options.

When you click **Create Project**, Archive Builder creates the complete project scaffold immediately:

```text
<Project Name>/
├── archive_builder.json
├── archive_builder.sqlite3
├── captures/
└── exports/
```

Choose **NothingToxic** for the bundled starter values or **Custom website** for another site. Domains should be entered as `example.com`; content paths should be entered as `/media/` or `/posts/`. No asterisk is required. A trailing `*` is accepted and normalized away for convenience.

New projects use the performance preset. On an older project, clicking **Apply Performance Preset** changes the visible network controls; save the project if you want those values persisted.

After creation, customize extraction rules in the main window and use Preview before starting a full build.

## NothingToxic starter profile

A new project starts with:

- Domain: `nothingtoxic.com`
- Include subdomains: yes
- Content path: `/media/`
- Capture years: 1996 through the current year
- Extraction fields: Post Number, Posted Date, Title, Category, Description
- Extraction methods: configurable from the GUI
- CDX `collapse=urlkey`: enabled by default
- Cross-host duplicate detection: enabled by default
- Duplicate identity fields: `post_number,title`
- Replay workers/request spacing: `10` / `0.125 s`
- CDX workers/request spacing: `10` / `0.75 s`

Archive Builder indexes matching URLs, records each URL's first known capture within the selected project scope, selects a usable archived capture for scanning, and stores results in the project database. Repeated captures of the same server URL key can be collapsed at the CDX layer, while aliases and cross-host logical duplicates remain auditable.

## Configurable extraction rules

Each field can use one of these methods:

- `auto` — common metadata and HTML heuristics
- `css_selector` — CSS selector, optionally reading an attribute
- `meta` — metadata such as `property=article:published_time` or `name=description`
- `regex` — regular expression against visible text, raw HTML, or URL
- `text_after` — take text following a marker on the matched text line
- `text_between` — take text between start/end markers
- `url_regex` — regular expression against the original URL

Rules are compiled and validated before a run. Archive Builder lazily builds only the source representations the active rules need, and a malformed rule fails before replay work begins instead of silently completing pages.

`text_after` no longer has the old implicit 300-character truncation. Long descriptions are preserved unless your configured rule itself bounds them.

The **Preview** tab lets you test the current extraction rules against a sample archived page before running the complete site.

## Posted-date interpretation

Posted Date comes only from page/posting evidence. It is never replaced by First Capture Date.

- Full dates are stored internally as ISO dates and displayed as `DD/MM/YYYY`.
- Month-only evidence stays month precision, such as `06/2008` for display.
- Year-only evidence stays year precision, such as `2008`.
- Ambiguous numeric dates use the project's **Day first dates** setting unless an extraction rule supplies an explicit date format.
- Missing date parts are not fabricated merely to make a row sortable.

All exports and the Results preview use the same chronology sorter: newest Posted Date first, undated entries last, deterministic ties.

## Resumable indexing

Archive Builder uses SQLite as the authoritative project state. A project can be stopped and reopened without starting over. It keeps track of discovered URL identity/aliases, capture metadata, coverage state, download state, extraction state, failures, source identity, and rule fingerprints.

Indexing intentionally separates two questions:

1. What is the earliest capture found for this logical archived URL in the selected scope?
2. Which successful capture should be selected and replayed for extraction?

Indexing begins with the whole requested interval. Small scopes can complete with one broad response per phase. Larger scopes can use bounded CDX pagination/resume state. Date ranges are subdivided only after actual query failure rather than unconditionally paying an empty-year request cost.

## Downloading, extraction, and Build All

**Build All** performs:

```text
Index → combined Download + Extract → Deduplicate if stale → Export
```

Replay work uses a rolling executor window near twice the worker count; Archive Builder does not create one future for every URL in a large project. The single coordinator owns SQLite writes and commits completed results in batches (32 results or about one second), while workers continue acquisition/extraction.

A complete local capture is reusable. If extraction fails, rules change, or the extractor version changes, **Extract Fields** or Build All can process the saved HTML locally without forcing another Wayback request. If the selected capture timestamp itself changes, the stale source association is invalidated.

Progress distinguishes **request starts/s** from **completed pages/s**. Request spacing controls starts, not guaranteed successful-page throughput.

## Replay validation and saved captures

Archive Builder streams replay responses with the configured size limit, records the final Wayback replay URL/timestamp, MIME type and chosen encoding, and classifies actual bytes before extraction.

Known binary signatures are not fed to the text extractor. HTML/text served under misleading MIME can still be recovered by sniffing. Wayback error/interstitial HTML and redirects that escape `web.archive.org` are rejected as successful page captures.

Successful textual sources are preserved as full original bytes in `captures/` with a small `.meta.json` sidecar containing the selected timestamp and source hash. The sidecar lets Archive Builder safely adopt a fully written capture after an interrupted database-commit window.

## Duplicate handling

Archive Builder has two independent deduplication layers:

1. **CDX `collapse=urlkey`** — optional and enabled by default. Server `urlkey` is also stored so identity can be reconciled across independently returned pages/windows.
2. **Cross-host post matching** — runs after field extraction. This detects cases where different archived URLs represent the same logical post, such as `nothingtoxic.com/media/...` and `media.nothingtoxic.com/media/...`.

The default NothingToxic logical-post identity is `post_number,title`. URL/capture identity and logical-post identity remain separate concepts.

- **Exact**: all configured identity fields match across different hostnames. Exact groups default to **Merge**.
- **Probable**: the anchor field matches but another identity field is missing or conflicts. Probable groups default to **Review**.

The **Duplicates** tab lets you **Merge Group**, **Keep Separate**, or choose the canonical URL. Manual decisions survive unchanged group membership. If membership changes, Archive Builder keeps a still-valid canonical choice but forces the group back to review rather than silently extending an old approval.

Deduplication runs once per changed extraction revision and is reused for immediate export. No alias URL is deleted from SQLite.

## CSV and optional XLSX export

`archive_builder.csv` is the complete clean data export and always contains exactly seven fields in the documented order. CSV quoting preserves commas, quotes, Unicode, and multiline descriptions. Spreadsheet-dangerous leading formula characters are escaped only in exported cells; the raw extracted value remains in SQLite.

Enable **Create XLSX presentation** if you also want `archive_builder.xlsx`:

- **Data** sheet: seven unmerged fields for sorting/filtering, typed complete dates with `dd/mm/yyyy`, wrapped text, frozen header, and URL hyperlinks.
- **Reading** sheet: A–Z presentation with these horizontal spans:
  - A:B Post Number
  - C:E Posted Date
  - F:K Title
  - L:N Category
  - O:U Description
  - V:W URL
  - X:Z First Capture Date

The reading sheet has a configurable row cap so a huge presentation workbook is not mandatory; the CSV remains complete regardless.

Exports use temporary files/atomic replacement so an interrupted export does not overwrite the previous complete CSV.

## Using the packaged applications

### Windows

Download `ArchiveBuilder-Windows-x64.zip`, extract it, and run `ArchiveBuilder\ArchiveBuilder.exe`. The package also includes the CLI and optional install/uninstall helpers. Windows may display a SmartScreen warning for unsigned community builds.

### macOS

Download `ArchiveBuilder-macOS-Universal.zip`, extract it, and open `Archive Builder.app`. The package is built as a Universal2 application for Intel and Apple Silicon and targets macOS 12 Monterey or newer. It also contains `ArchiveBuilderCLI`.

### Linux

Download `ArchiveBuilder-Linux-x64.tar.gz`, extract it, and run the bundled `install.sh` or launch the application directly. The package contains both GUI and CLI builds.

## Running from source

Python 3.11 or newer is required.

Tkinter is required for the GUI. Official Python installers include it on Windows and macOS; Linux source installations may require your distribution's Python Tk package.

```bash
git clone https://github.com/Memory-of-Time/archive-builder.git
cd archive-builder
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
python run_app.py
```

On Windows, activate the virtual environment with `.venv\Scripts\activate` instead.

## CLI

```bash
archive-builder init ~/ArchiveBuilder/nothingtoxic
archive-builder index ~/ArchiveBuilder/nothingtoxic
archive-builder download ~/ArchiveBuilder/nothingtoxic
archive-builder extract ~/ArchiveBuilder/nothingtoxic
archive-builder dedupe ~/ArchiveBuilder/nothingtoxic
archive-builder export ~/ArchiveBuilder/nothingtoxic
```

Run the complete pipeline:

```bash
archive-builder all ~/ArchiveBuilder/nothingtoxic
```

Preview a page using the currently configured rules:

```bash
archive-builder preview ~/ArchiveBuilder/nothingtoxic "https://nothingtoxic.com/media/example"
```

Run `archive-builder init --help` for project-creation options, including worker pacing, day-first dates, XLSX presentation export, and deduplication settings.

Run `archive-builder --version` to identify the installed release. `python -m archive_builder` and `python run_cli.py` accept the same CLI commands.

## Project layout

A project folder contains:

```text
archive_builder.json
archive_builder.sqlite3
archive_builder.schema2.backup.sqlite3   # created once when a schema-2 project migrates
captures/
exports/archive_builder.csv
exports/archive_builder_duplicates.csv
exports/archive_builder.xlsx             # optional
```

CSV/XLSX files are derived exports; SQLite is the persistent working database. Capture HTML and its `.meta.json` provenance sidecars are durable local source evidence.

## Schema v3 migration

Opening an existing schema-v2 project for writing upgrades it to schema v3. Before migration, Archive Builder creates `archive_builder.schema2.backup.sqlite3` with SQLite's backup API. Existing pages, extracted fields, source paths, errors, aliases that were represented as page URLs, operation history, and duplicate review records are retained. Identity/provenance fields that v0.1.2 never stored remain unknown until verified by later indexing/replay rather than being guessed.

Read-only database observers do not perform migration/recovery and simply opening a database no longer resets another writer's running state. Recovery of abandoned running states is an explicit writer action used when opening a project for work.

## GitHub releases

The repository contains two GitHub Actions workflows:

- `.github/workflows/tests.yml` — cross-platform source tests on pushes and pull requests
- `.github/workflows/build-and-release.yml` — builds Windows, Linux, and universal macOS packages; a version tag publishes all three to GitHub Releases

To publish a release after the workflows are green:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The release job then uploads:

```text
ArchiveBuilder-Windows-x64.zip
ArchiveBuilder-Windows-x64.zip.sha256
ArchiveBuilder-Linux-x64.tar.gz
ArchiveBuilder-Linux-x64.tar.gz.sha256
ArchiveBuilder-macOS-Universal.zip
ArchiveBuilder-macOS-Universal.zip.sha256
```

See `docs/GITHUB_SETUP.md` and `UPLOAD_CHECKLIST.txt` before a public release.

## Generalizing to other websites

NothingToxic is the first profile rather than the final scope. Change the domain, subdomain setting, content path, date range, date interpretation, extraction rules, duplicate identity, and export options to use the same pipeline on another archived site.

## License

MIT. See `LICENSE`.
