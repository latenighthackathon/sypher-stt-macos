#!/usr/bin/env bash
# Sypher STT — macOS launcher
# Creates the virtual environment on first run, then starts the app.
# The setup wizard handles model download and Accessibility setup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"

# ── Ensure venv exists ───────────────────────────────────────────────────────
if [[ ! -x "$PYTHON" ]]; then
    echo ""
    echo "  Setting up Sypher STT for the first time…"
    echo ""
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip --quiet
    "$VENV/bin/pip" install -e "$SCRIPT_DIR[download]" --quiet
    echo "  ✓ Dependencies installed."
    echo ""
fi

# ── Save project root so the in-app updater can always find src/ ─────────────
APPDATA_DIR="$HOME/Library/Application Support/SypherSTT"
mkdir -p "$APPDATA_DIR"
printf '%s' "$SCRIPT_DIR" > "$APPDATA_DIR/.project_root"

# ── Launch ───────────────────────────────────────────────────────────────────
exec "$PYTHON" -m sypher_stt.app
