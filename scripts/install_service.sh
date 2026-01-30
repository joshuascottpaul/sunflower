#!/bin/sh
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
LOG_DIR="$HOME/Library/Logs/sunflower"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="com.joshuascottpaul.sunflower"
TEMPLATE="$REPO_DIR/launchd/${PLIST_LABEL}.plist.template"
TARGET="$LAUNCH_AGENTS_DIR/${PLIST_LABEL}.plist"

mkdir -p "$LOG_DIR" "$LAUNCH_AGENTS_DIR"

sed \
  -e "s|{{REPO_DIR}}|$REPO_DIR|g" \
  -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
  -e "s|{{LOG_DIR}}|$LOG_DIR|g" \
  "$TEMPLATE" > "$TARGET"

launchctl unload "$TARGET" 2>/dev/null || true
launchctl load "$TARGET"

printf "Installed and loaded %s\n" "$TARGET"
