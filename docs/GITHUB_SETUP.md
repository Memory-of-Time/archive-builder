# GitHub setup

Archive Builder is configured for `https://github.com/Memory-of-Time/archive-builder`.

## Workflows

`.github/workflows/tests.yml` runs the test suite on Ubuntu, Windows, Intel macOS, and Apple Silicon macOS with Python 3.11 and 3.12.

`.github/workflows/build-and-release.yml` builds the three user-facing packages. Manual dispatch builds downloadable Actions artifacts. Pushing a tag beginning with `v` also creates/updates a GitHub Release and uploads all platform packages and checksums.

## First release

1. Push the complete repository to the `main` branch.
2. Confirm the Tests workflow passes.
3. Run Build All Platforms manually once and inspect all three artifacts.
4. Tag the validated commit `v1.0.0`. The source-version check rejects a different tag.
5. Push the tag.
6. Confirm the GitHub Release contains all six release assets (three packages and three SHA-256 files).
7. Test each package on a native machine.

The README uses `/releases/latest/download/...` URLs, so once a release exists the three download links automatically resolve to the latest release without editing the README.

## Signing

Windows Authenticode signing and Apple Developer ID notarization are not configured for v1.0.0. The workflow can build without external signing secrets; unsigned Windows/macOS builds may display operating-system security warnings.

## Source layout

Upload the contents of the source folder at the repository root, including `.github/` and `.gitignore`. The old `github/` and `gitignore` aliases are no longer used. All source files fit in a single upload batch of fewer than 100 files; dotfiles still need to be included explicitly when using a file picker.

Each build checks release metadata before packaging. Linux jobs use `actions/setup-python` and probe its Tk/Tcl runtime directly. They do not install `python3-tk` for the runner's separate system Python.
