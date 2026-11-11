#!/usr/bin/env bash
set -euo pipefail
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/archive-builder"
BIN_PATH="$HOME/.local/bin/archive-builder"
DESKTOP_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/applications/archive-builder.desktop"
rm -rf "$DEST_DIR"
rm -f "$BIN_PATH" "$DESKTOP_PATH"
echo "Archive Builder was uninstalled. Project folders were not removed."
