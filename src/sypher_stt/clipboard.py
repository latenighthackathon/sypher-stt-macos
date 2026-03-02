"""Clipboard and paste module for outputting transcribed text.

Copies text to the macOS clipboard via pyperclip (pbcopy) and simulates
Cmd+V via pynput's keyboard controller (CGEventPost) to paste into the
currently focused window.

pynput's Controller uses CGEventPost(kCGHIDEventTap, …) which works via
the responsible-process mechanism — the same mechanism that makes the
pynput Listener receive hotkey events.  This avoids spawning osascript
(which needs its own AX TCC entry to send keystrokes).
"""

import logging
import threading
import time

import pyperclip
from pynput.keyboard import Controller as _KbController, Key

log = logging.getLogger(__name__)

_kb = _KbController()


def _get_clipboard() -> str:
    """Safely read current clipboard content."""
    try:
        return pyperclip.paste()
    except Exception:
        return ""


def _set_clipboard(text: str) -> None:
    """Safely write to the clipboard."""
    try:
        pyperclip.copy(text)
    except Exception as e:
        log.debug("Clipboard write failed: %s", e)


def paste_text(text: str, restore_clipboard: bool = True) -> None:
    """Copy text to clipboard and paste into the active window via Cmd+V.

    Args:
        text: The transcribed text to paste.
        restore_clipboard: If True, restores the previous clipboard content
            after a short delay (only for text clipboard content).
    """
    if not text:
        return

    old_clipboard = _get_clipboard() if restore_clipboard else None

    _set_clipboard(text)
    time.sleep(0.05)  # Brief settle time before pasting

    try:
        with _kb.pressed(Key.cmd):
            _kb.press('v')
            _kb.release('v')
    except Exception as e:
        log.error("Paste failed: %s", e)
        return

    log.debug("Pasted %d chars into active window.", len(text))

    if old_clipboard is not None:
        def _restore() -> None:
            time.sleep(0.15)
            _set_clipboard(old_clipboard)

        threading.Thread(target=_restore, daemon=True).start()
