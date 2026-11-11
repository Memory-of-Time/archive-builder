# Archive Builder v0.1.3 Implementation Report

Historical pre-release record. Its measurements and validation describe v0.1.3, not the v1.0.0 release. See `../../SOURCE_VALIDATION.txt` for current validation.

## Scope

v0.1.3 applies the supplied **Archive Builder Speed and Chronology Audit** to the v0.1.2 codebase. The update keeps Archive Builder's existing project model, desktop UI, CLI, configurable extraction rules, duplicate review workflow, local re-extraction, and independent Posted Date / First Capture Date semantics.

The implementation deliberately does **not** add Archive Scout's keyword scanner, scoring, FTS, Research Intelligence, AI analysis, snippet generation, or unrelated source-normalization features.

## Changed behavior

### Acquisition and combined processing

- New projects default to the audited performance preset: 10 replay workers with 0.125-second shared replay request-start spacing.
- Existing projects keep their saved network values. The GUI exposes an explicit, reversible **Apply Performance Preset** action instead of silently changing old projects.
- **Build All** now uses a bounded rolling download+extract pipeline. It does not enqueue one future for every page.
- **Download Pages** remains acquisition-only and **Extract Fields** remains local extraction/re-extraction, so already downloaded projects do not have to re-download to use the new pipeline.
- Coordinator-owned SQLite writes are batched by result count or elapsed time; workers do not share the writer connection and network waits do not hold database transactions open.
- Progress reports request starts separately from completed pages.
- Shared 429/503 throttling is honored across workers. A rate-limit pause is never shorter than the configured base retry interval when the server does not provide a longer `Retry-After` value.

### CDX indexing and identity

- Indexing begins with the full requested interval rather than unconditionally probing every year.
- Coverage is stored by phase/range/strategy/unit so an incomplete traversal cannot be mislabeled complete from existing rows.
- Broad resume traversal and bounded numbered-page traversal are supported with persisted continuation state.
- Failed ranges may be subdivided for recovery; empty-year/month probes are not performed automatically.
- CDX responses are structurally validated. Malformed/null responses, malformed rows, bad timestamps/URLs, repeated continuation tokens, and repeated responses do not advance coverage as successful.
- Opaque continuation values are passed through one parameter-encoding layer; tests cover literal `+`, `&`, percent escapes, Unicode, and nested original-URL query strings.
- Server `urlkey` and original URL aliases are stored independently. Capture/url identity remains distinct from cross-host logical-post identity.

### Durable capture and extraction state

- Database schema is now **v3**.
- Selected capture identity, actual replay/final URL data, MIME/encoding/classification, source size/hash/identity, intended local path, and extraction source/version associations are persisted.
- Downloads are staged to temporary files and atomically renamed only after validation.
- Capture sidecars retain enough identity/hash metadata to adopt a complete local source after interruption between file completion and database commit.
- Download and extraction states remain independent. A valid saved source with failed/pending extraction can resume locally.
- Completed extraction is associated with source identity, rule fingerprint, and extractor version.

### Replay validation and decoding

- Replay responses are streamed with the size limit enforced during acquisition.
- Known binary signatures/MIME are excluded from normal text extraction; uncertain textual cases are sniffed rather than trusting filename or HTTP 200 alone.
- Wayback error/interstitial HTML and unapproved live-site escapes are rejected as successful archived post pages.
- BOM, response charset, and HTML encoding declarations are considered during decode; the chosen encoding is recorded so later local re-extraction can agree with the initial run.
- Full source HTML/bytes are preserved; Archive Builder does not replace the source with a mandatory visible-text-only copy.

### Extraction plan

- Extraction rules are validated/compiled before replay work starts.
- URL-only rules avoid DOM construction; raw-source rules avoid unnecessary visible-text generation; DOM-dependent rules share a parsed tree.
- Page metadata/JSON-LD/source lookups are reused within a page extraction.
- `text_after` no longer has an undocumented 300-character truncation.
- Missing values, invalid configuration, decode problems, and incomplete evidence remain distinguishable outcomes.

### Chronology and export

- Posted Date remains derived only from posting evidence; First Capture Date remains independent and scope-qualified.
- Partial dates keep their actual precision instead of inventing missing day/month/year components.
- Complete dates display as **DD/MM/YYYY**, while an ISO/typed key controls actual chronology.
- GUI preview, CSV, and XLSX exports use the shared chronological ordering path: newest valid Posted Date first, undated/unresolved records last, deterministic ties.
- The CSV remains exactly seven logical fields.
- Optional `archive_builder.xlsx` contains an unmerged seven-column **Data** sheet plus an A–Z **Reading** presentation sheet.

### Duplicate analysis

- Duplicate analysis is revision-aware so Build All does not immediately repeat an unchanged analysis before export.
- Exact subsets can still be recognized when a wider anchor set contains a conflicting alias.
- Manual decisions/canonical choices are retained when membership is unchanged. Membership changes return the group to review rather than silently extending an old approval.
- Alias URLs and review history remain preserved in SQLite.

## Migration

On the first writable open of a schema-v2 project, Archive Builder creates:

`archive_builder.schema2.backup.sqlite3`

before migrating the working database to schema v3. Existing capture files are not deleted. Values that v0.1.2 never recorded (for example a historical server urlkey) remain unset until verified rather than being invented.

Ordinary database open no longer treats every active operation as abandoned. Recovery is explicit so a second reader/observer does not rewrite a live run's state.

## Validation performed

Development-container acceptance checks on 2026-09-20:

- `python -m pytest -q` — **58 passed**.
- Python package and test sources compile successfully with `compileall`.
- Linux/macOS shell scripts pass `bash -n` syntax validation.
- Both GitHub Actions workflow YAML files parse successfully.
- A Python wheel builds successfully using the locally available build toolchain with build isolation disabled (`archive_builder-0.1.3-py3-none-any.whl`).
- CLI startup/help and v0.1.3 default network configuration import successfully.

The focused test suite covers, among other items:

- schema-2 to schema-3 migration and backup preservation;
- bounded rolling pipeline behavior and interruption/recovery paths;
- shared 429/503 throttling and interruptible retry waits;
- full-range indexing, continuation safety, repeated-token/response detection, and opaque-token encoding;
- server urlkey/alias identity handling;
- replay classification, binary rejection, Wayback error rejection, encoding behavior, and atomic source adoption;
- rule validation/compilation and long text-marker extraction;
- partial-date precision, day-first parsing, true chronological sorting, CSV round trips, and XLSX presentation structure;
- duplicate subset/revision behavior and preservation of review choices when membership is unchanged.

## Benchmark evidence

The supplied audit's controlled 32-page offline fixture measured:

- old 4-worker / 0.5-second separate pipeline: **15.903 s** (~2.01 completed pages/s);
- 10-worker / 0.125-second pacing while still separate: **4.289 s** (~7.46 pages/s);
- 10-worker / 0.125-second combined prototype: **4.039 s** (~7.92 pages/s).

That fixture showed a **3.94× scheduling improvement** over the previous defaults and identical saved extraction evidence across the three profiles. v0.1.3 adopts the corresponding pacing and bounded overlap architecture, but this is not presented as a guaranteed live Internet Archive speedup. Real performance depends on CDX/replay latency, throttling, retries, page size, extraction rules, duplicate analysis, and export work.

The audit's original benchmark/probe scripts target v0.1.2 private helper APIs and are therefore not used unchanged as a v0.1.3 release gate. The v0.1.3 regression suite replaces those private-API probes with acceptance tests against the new durable model.

## Remaining limitations / release gates

- No live Internet Archive integration run was possible in the development container; outbound network availability is environment-dependent.
- Native Windows, Linux, and macOS PyInstaller artifacts must still be produced by GitHub Actions and smoke-tested on their target operating systems.
- macOS notarization and Windows Authenticode signing are not configured.
- Actual Google Sheets import/presentation behavior should be checked during release acceptance; CSV/XLSX file generation itself is covered offline.
- No universal total-project speedup is claimed from the synthetic benchmark alone.

## Applying the patch

Apply the v0.1.3 patch at the root of an existing Archive Builder v0.1.2 repository and replace matching files. Do not copy Python cache directories or local project data into the repository.

After applying the patch:

1. Run `python -m pytest -q`.
2. Launch Archive Builder and open a copy of an existing v0.1.2 project to verify the schema-v2 backup and migration.
3. Test **Apply Performance Preset** only if you want to upgrade an older project's saved network settings.
4. Perform a bounded live Wayback integration run before tagging the release.
5. Tag `v0.1.3` only after native GitHub Actions builds and smoke tests pass.
