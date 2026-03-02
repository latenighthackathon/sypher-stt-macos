"""Sound feedback using macOS system sounds via afplay.

Uses macOS built-in system sounds — no extra dependencies or WAV files.
Plays asynchronously so it never blocks the main thread.
"""

import logging
import subprocess
import threading

from sypher_stt.constants import SYSTEM_SOUNDS

log = logging.getLogger(__name__)

# macOS system sounds in /System/Library/Sounds/
_SOUNDS_DIR = "/System/Library/Sounds"

_DEFAULT_START = "Ping"
_DEFAULT_STOP  = "Blow"
_DEFAULT_ERROR = "Basso"


def _play(name: str) -> None:
    """Play a named macOS system sound via afplay. Silent on failure."""
    if name not in SYSTEM_SOUNDS:
        name = _DEFAULT_START
    try:
        subprocess.Popen(
            ["afplay", f"{_SOUNDS_DIR}/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.debug("Sound playback failed: %s", e)


def play_sound(name: str) -> None:
    """Play any named macOS system sound asynchronously."""
    threading.Thread(target=_play, args=(name,), daemon=True).start()


