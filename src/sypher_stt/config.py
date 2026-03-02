"""Configuration management for Sypher STT.

Config is stored in ~/Library/Application Support/SypherSTT/config.json.
All values are validated against whitelists on load.
"""

import json
import logging
import os

from sypher_stt.constants import (
    AVAILABLE_MODELS,
    CONFIG_PATH,
    DEFAULT_HOTKEY,
    DEFAULT_MODEL,
    SYSTEM_SOUNDS,
)
from sypher_stt.hotkeys import validate_hotkey

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict = {
    "hotkey": DEFAULT_HOTKEY,
    "model": DEFAULT_MODEL,
    "audio_device": None,
    "sound_feedback": True,
    "sound_start": "Ping",
    "sound_stop": "Blow",
    "sound_error": "Basso",
    "record_stats": True,
}


def load_config() -> dict:
    """Load configuration from disk, or return defaults.

    Validates all values against whitelists to prevent injection
    of arbitrary model names or unknown config keys.
    """
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                log.warning("Config file is not a dict, using defaults.")
                return dict(DEFAULT_CONFIG)

            config = dict(DEFAULT_CONFIG)

            if validate_hotkey(saved.get("hotkey", "")):
                config["hotkey"] = saved["hotkey"]
            if saved.get("model") in AVAILABLE_MODELS:
                config["model"] = saved["model"]
            _dev = saved.get("audio_device")
            if _dev is None or (isinstance(_dev, int) and _dev >= 0):
                config["audio_device"] = _dev
            if isinstance(saved.get("sound_feedback"), bool):
                config["sound_feedback"] = saved["sound_feedback"]
            for _skey in ("sound_start", "sound_stop", "sound_error"):
                if saved.get(_skey) in SYSTEM_SOUNDS:
                    config[_skey] = saved[_skey]
            if isinstance(saved.get("record_stats"), bool):
                config["record_stats"] = saved["record_stats"]

            log.debug("Loaded config from %s", CONFIG_PATH)
            return config
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load config (%s), using defaults.", e)

    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    """Save configuration to disk."""
    try:
        fd = os.open(str(CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)  # Enforce mode even if the file already existed
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        log.debug("Saved config to %s", CONFIG_PATH)
    except IOError as e:
        log.error("Failed to save config: %s", e)
