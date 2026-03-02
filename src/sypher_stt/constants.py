"""Application-wide constants and macOS path resolution."""

import os
import pwd
from pathlib import Path

# Application identity
APP_NAME = "SypherSTT"

# Paths — resolved at import time
_root = Path(__file__).resolve().parent.parent.parent  # project root

# Use the passwd-database home dir — cannot be spoofed by setting $HOME.
_real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)

# macOS: ~/Library/Application Support/SypherSTT/
APPDATA_DIR = _real_home / "Library" / "Application Support" / APP_NAME
APPDATA_DIR.mkdir(parents=True, exist_ok=True)

# SYPHER_MODELS_DIR can be set to store models outside the project tree
# (e.g. ~/Library/Application Support/SypherSTT/models).  Falls back to
# the project-root models/ dir when running via run.sh for development.
# Must resolve within the user's APPDATA_DIR; hostile values are ignored.
_models_env = os.getenv("SYPHER_MODELS_DIR")
if _models_env:
    _models_candidate = Path(_models_env).resolve()
    # Reject paths outside Application Support/SypherSTT (prevents $HOME spoofing).
    MODELS_DIR = _models_candidate if _models_candidate.is_relative_to(APPDATA_DIR) else _root / "models"
else:
    MODELS_DIR = (_root / "models").resolve()

CONFIG_PATH = APPDATA_DIR / "config.json"
STATS_PATH  = APPDATA_DIR / "stats.json"
SETUP_FLAG  = APPDATA_DIR / ".setup_complete"

# macOS: ~/Library/Logs/SypherSTT/
LOG_DIR = _real_home / "Library" / "Logs" / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Single-instance lock file — kept in APPDATA_DIR (user-owned, not world-writable)
LOCK_FILE = APPDATA_DIR / ".lock"

# Audio
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 1024
MAX_RECORDING_SECONDS = 120

# Whisper
AVAILABLE_MODELS = [
    "tiny.en", "tiny",
    "base.en", "base",
    "small.en", "small",
    "medium.en", "medium",
    "large-v2", "large-v3",
]
DEFAULT_MODEL = "base.en"

# Hotkey
DEFAULT_HOTKEY = "f8"

# macOS system sounds available in /System/Library/Sounds/
SYSTEM_SOUNDS = [
    "Basso", "Blow", "Bottle", "Frog", "Funk", "Glass",
    "Hero", "Morse", "Ping", "Pop", "Purr", "Sosumi",
    "Submarine", "Tink",
]
