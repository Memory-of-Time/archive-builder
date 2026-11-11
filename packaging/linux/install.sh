#!/usr/bin/env bash
set -euo pipefail
PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
GUI_SOURCE="$PACKAGE_DIR/ArchiveBuilder"
CLI_SOURCE="$PACKAGE_DIR/ArchiveBuilderCLI"
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/archive-builder"
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DEST_DIR" "$BIN_DIR" "$APPLICATIONS_DIR"
rm -rf "$DEST_DIR/ArchiveBuilder" "$DEST_DIR/ArchiveBuilderCLI"
cp -R "$GUI_SOURCE" "$DEST_DIR/ArchiveBuilder"
cp -R "$CLI_SOURCE" "$DEST_DIR/ArchiveBuilderCLI"
ln -sf "$DEST_DIR/ArchiveBuilderCLI/ArchiveBuilderCLI" "$BIN_DIR/archive-builder"
cat > "$APPLICATIONS_DIR/archive-builder.desktop" <<DESKTOP
[Desktop Entry]
Name=Archive Builder
Comment=Build structured datasets from archived websites
Exec=$DEST_DIR/ArchiveBuilder/ArchiveBuilder
Terminal=false
Type=Application
Categories=Utility;Education;
DESKTOP
chmod +x "$APPLICATIONS_DIR/archive-builder.desktop"
printf 'Archive Builder installed. GUI: application menu. CLI: %s/archive-builder.\n' "$BIN_DIR"
