#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/verify_release.py
rm -rf build dist release
export MACOSX_DEPLOYMENT_TARGET="12.0"
python -m PyInstaller --noconfirm --clean --windowed --name "Archive Builder" --icon assets/archivebuilder.icns --add-data "assets/archivebuilder.png:assets" --target-arch universal2 --collect-all httpx --collect-all httpcore --collect-all bs4 --collect-all dateutil --collect-all openpyxl run_app.py
python -m PyInstaller --noconfirm --clean --console --onefile --name ArchiveBuilderCLI --target-arch universal2 --collect-all httpx --collect-all httpcore --collect-all bs4 --collect-all dateutil --collect-all openpyxl run_cli.py
APP="dist/Archive Builder.app"
python - "$APP/Contents/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path
from archive_builder.constants import VERSION

path = Path(sys.argv[1])
metadata = plistlib.loads(path.read_bytes())
metadata.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION,
                CFBundleIdentifier="org.memory-of-time.archive-builder",
                LSMinimumSystemVersion="12.0")
path.write_bytes(plistlib.dumps(metadata))
PY
CLI="dist/ArchiveBuilderCLI"
PACKAGE="release/ArchiveBuilder-macOS-Universal"
ZIP="release/ArchiveBuilder-macOS-Universal.zip"
TEMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
VERIFY_ROOT="$(mktemp -d "$TEMP_ROOT/archive-builder-macos-verify.XXXXXX")"
EXTRACTED_APP="$VERIFY_ROOT/ArchiveBuilder-macOS-Universal/Archive Builder.app"
EXTRACTED_CLI="$VERIFY_ROOT/ArchiveBuilder-macOS-Universal/ArchiveBuilderCLI"
cleanup() { rm -rf "$VERIFY_ROOT"; }
trap cleanup EXIT
python scripts/verify_macos_bundle.py "$APP"
codesign --force --deep --sign - "$APP"
codesign --force --sign - "$CLI"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign --verify --strict --verbose=2 "$CLI"
ARCHIVE_BUILDER_STARTUP_PROBE=1 "$APP/Contents/MacOS/Archive Builder"
"$CLI" --help >/dev/null
rm -rf "$PACKAGE" "$ZIP" "$ZIP.sha256"
mkdir -p "$PACKAGE"
ditto "$APP" "$PACKAGE/Archive Builder.app"
cp "$CLI" "$PACKAGE/ArchiveBuilderCLI"
cp README.md "$PACKAGE/README.md"
ditto -c -k --sequesterRsrc --keepParent "$PACKAGE" "$ZIP"
ditto -x -k "$ZIP" "$VERIFY_ROOT"
python scripts/verify_macos_bundle.py "$EXTRACTED_APP"
codesign --verify --deep --strict --verbose=2 "$EXTRACTED_APP"
codesign --verify --strict --verbose=2 "$EXTRACTED_CLI"
"$EXTRACTED_CLI" --help >/dev/null
( cd release && shasum -a 256 ArchiveBuilder-macOS-Universal.zip > ArchiveBuilder-macOS-Universal.zip.sha256 )
trap - EXIT
rm -rf "$VERIFY_ROOT"
