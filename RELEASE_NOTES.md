# Archive Builder v1.0.0

Archive Builder's first public release builds structured chronological datasets from archived websites. It includes the v0.1.3 acquisition and chronology changes supplied with this release preparation.

## Included features

- A desktop project wizard and CLI for NothingToxic or custom website profiles.
- Resumable CDX discovery, with first capture evidence kept separate from the successful capture selected for extraction.
- A bounded combined download and extraction pipeline, plus independent Download Pages and Extract Fields operations.
- Configurable rules for Post Number, Posted Date, Title, Category, and Description; saved captures can be reprocessed locally when rules change.
- Cross-host duplicate review with preserved aliases and manual decisions.
- Seven-column CSV exports, chronological date sorting, and optional XLSX Data and Reading sheets.
- Windows x64, Linux x64, and macOS Universal2 packaging workflows with checksums.

## Launch changes

Version labels are aligned across Python metadata, CLI `--version`, the GUI title, HTTP user agent, Windows version resources, and macOS bundle metadata. Repository links point to `Memory-of-Time/archive-builder`.

The repository now has one canonical `.github` directory for workflows and templates and a standard `.gitignore`. Build scripts and CI reject inconsistent version metadata; a release tag must match the source version. Linux CI validates the selected Python's Tk/Tcl runtime directly instead of installing Tk for an unrelated system Python.

Site & Build scrolls when needed so controls remain accessible in smaller windows, and the status bar keeps its own space. Linux packaging includes the selected Python's library directory in dependency discovery for installations outside the normal system paths.

## Existing projects

The launch retains schema v3 and the existing project JSON format. v0.1.3 projects need no new database migration. Older schema-v1/v2 projects use the existing backup and migration path; keep a separate copy of an important project before upgrading. Saved explicit network values remain in effect until the performance preset is deliberately applied.

Posted Date comes from page evidence. First Capture Date remains the earliest capture discovered inside the configured capture-year interval, not a claim about all of Wayback history. Partial dates retain their available precision.

## Validation and limits

See [SOURCE_VALIDATION.txt](https://github.com/Memory-of-Time/archive-builder/blob/v1.0.0/SOURCE_VALIDATION.txt) for the exact checks performed. Historical v0.1.3 evidence is retained under `docs/history/` and is not presented as new release testing.

Request pacing does not guarantee completed-download throughput. Live Wayback coverage, long-running performance, native Windows/macOS packages, and external spreadsheet import require their own verification. The packaging workflow must pass before download links can serve the new release.

Windows builds are unsigned and macOS builds use ad-hoc signing; trusted-publisher signing and Apple notarization are not configured.
