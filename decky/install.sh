#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PLUGIN_PARENT="$HOME/homebrew/plugins"
PLUGIN_DIR="$PLUGIN_PARENT/ROG-Control"

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this installer as your normal user, without sudo." >&2
    exit 1
fi
if [ ! -f "$SOURCE_DIR/plugin.json" ] || [ ! -f "$SOURCE_DIR/package.json" ] || \
   [ ! -f "$SOURCE_DIR/main.py" ] || [ ! -f "$SOURCE_DIR/dist/index.js" ]; then
    echo "The plugin files are incomplete. Extract the full ZIP and try again." >&2
    exit 1
fi
if [ ! -d "$PLUGIN_PARENT" ] || ! command -v systemctl >/dev/null 2>&1 || \
   ! systemctl list-unit-files plugin_loader.service --no-legend 2>/dev/null | grep -q '^plugin_loader\.service[[:space:]]'; then
    echo "Decky Loader was not found for this user. Install Decky Loader first." >&2
    exit 1
fi
if [ ! -x "$HOME/.local/bin/rogcontrol" ] && ! command -v rogcontrol >/dev/null 2>&1; then
    echo "ROG Control was not found. Install ROG Control for this user first." >&2
    exit 1
fi
if [ "$SOURCE_DIR" = "$PLUGIN_DIR" ]; then
    echo "The plugin is already installed at $PLUGIN_DIR."
else
    if [ -e "$PLUGIN_DIR" ]; then
        if [ ! -f "$PLUGIN_DIR/package.json" ] || ! python3 - "$PLUGIN_DIR/package.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as manifest:
        valid = json.load(manifest).get('name') == 'rog-control-decky'
except (OSError, ValueError, AttributeError):
    valid = False
sys.exit(0 if valid else 1)
PY
        then
            echo "The destination exists but is not a ROG Control plugin: $PLUGIN_DIR" >&2
            exit 1
        fi
    fi
    STAGE="$(mktemp -d "$PLUGIN_PARENT/.rogcontrol-install.XXXXXXXX")"
    cleanup() {
        if [ -d "$STAGE/previous" ] && [ ! -e "$PLUGIN_DIR" ]; then
            mv -- "$STAGE/previous" "$PLUGIN_DIR"
        fi
        rm -rf -- "$STAGE"
    }
    trap cleanup EXIT
    mkdir "$STAGE/ROG-Control"
    cp -a -- "$SOURCE_DIR/." "$STAGE/ROG-Control/"
    if [ -e "$PLUGIN_DIR" ]; then mv -- "$PLUGIN_DIR" "$STAGE/previous"; fi
    mv -- "$STAGE/ROG-Control" "$PLUGIN_DIR"
    echo "ROG Control Decky plugin installed at $PLUGIN_DIR."
fi

if sudo systemctl restart plugin_loader.service; then
    echo "Decky Loader restarted. Open Decky to use ROG Control."
else
    echo "Plugin installed, but Decky Loader could not be restarted. Restart it manually." >&2
    exit 1
fi
