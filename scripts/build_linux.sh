#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/verify_release.py
PYTHON_LIB_DIR="$(python -c 'import sys; from pathlib import Path; print(Path(sys.base_prefix) / "lib")')"
if [[ -d "$PYTHON_LIB_DIR" ]]; then
  export LD_LIBRARY_PATH="$PYTHON_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
rm -rf build dist release
python -m PyInstaller --noconfirm --clean --windowed --onedir --name ArchiveBuilder --icon assets/archivebuilder.png --add-data "assets/archivebuilder.png:assets" --collect-all httpx --collect-all httpcore --collect-all bs4 --collect-all dateutil --collect-all openpyxl run_app.py
python -m PyInstaller --noconfirm --clean --console --onedir --name ArchiveBuilderCLI --collect-all httpx --collect-all httpcore --collect-all bs4 --collect-all dateutil --collect-all openpyxl run_cli.py
mkdir -p release/ArchiveBuilder-Linux-x64
cp -R dist/ArchiveBuilder release/ArchiveBuilder-Linux-x64/ArchiveBuilder
cp -R dist/ArchiveBuilderCLI release/ArchiveBuilder-Linux-x64/ArchiveBuilderCLI
cp packaging/linux/install.sh packaging/linux/uninstall.sh README.md release/ArchiveBuilder-Linux-x64/
chmod +x release/ArchiveBuilder-Linux-x64/install.sh release/ArchiveBuilder-Linux-x64/uninstall.sh
tar -C release -czf release/ArchiveBuilder-Linux-x64.tar.gz ArchiveBuilder-Linux-x64
( cd release && sha256sum ArchiveBuilder-Linux-x64.tar.gz > ArchiveBuilder-Linux-x64.tar.gz.sha256 )
