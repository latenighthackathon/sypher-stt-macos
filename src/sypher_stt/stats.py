"""Transcription usage statistics — aggregated daily totals only.

Records word count, character count, and audio duration per calendar day.
No transcription text, session content, keystrokes, or personally identifying
information is ever written.  Stats survive restarts; the user can clear them
from the Settings → Stats tab at any time.
"""

import json
import logging
import os
import threading
from datetime import date

log = logging.getLogger(__name__)

from sypher_stt.constants import STATS_PATH

_lock = threading.Lock()


def _load() -> dict:
    if STATS_PATH.exists():
        try:
            d = json.loads(STATS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
            log.warning("stats.json has unexpected format, resetting.")
        except Exception as e:
            log.warning("Failed to load stats.json (%s), resetting.", e)
    return {"typing_wpm": 0, "days": {}}


def _save(stats: dict) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(STATS_PATH),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    os.fchmod(fd, 0o600)  # Enforce mode even if the file already existed
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)


def record_transcription(words: int, chars: int, audio_seconds: float) -> None:
    """Add one transcription to today's running totals.  Thread-safe.

    Only aggregated counts are stored — no text content of any kind.
    """
    if words <= 0 and chars <= 0:
        return
    today = date.today().isoformat()
    with _lock:
        stats = _load()
        day = stats.setdefault("days", {}).setdefault(
            today, {"words": 0, "chars": 0, "audio_seconds": 0.0}
        )
        day["words"] += words
        day["chars"] += chars
        day["audio_seconds"] = round(day["audio_seconds"] + audio_seconds, 1)
        _save(stats)


def clear_stats() -> None:
    """Remove all daily stats; preserve the user's typing WPM setting."""
    with _lock:
        stats = _load()
        stats["days"] = {}
        _save(stats)


def save_wpm(wpm: int) -> None:
    """Persist the user's measured typing speed (words per minute)."""
    if wpm <= 0:
        return
    with _lock:
        stats = _load()
        stats["typing_wpm"] = int(wpm)
        _save(stats)
