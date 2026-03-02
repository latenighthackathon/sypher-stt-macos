"""Settings launcher for Sypher STT.

Opens the settings UI in a separate process to avoid conflicts with
the rumps NSRunLoop running on the main thread.

Permission status (AX, Microphone) is checked in the main process and
forwarded to the settings subprocess via environment variables.
"""

import logging
import os
import subprocess
import sys
from typing import Optional

log = logging.getLogger(__name__)


def _ax_granted() -> bool:
    """Check whether Accessibility is granted for the current process.

    When launched from terminal/run.sh, the responsible process is Terminal.app
    (or equivalent), so AXIsProcessTrustedWithOptions returns True if Terminal
    has the AX toggle on.  Falls back to a functional AX probe: if AX is
    accessible, AXUIElementCopyAttributeValue succeeds; kAXErrorAPIDisabled
    (-25211) means it is definitively not granted.
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt  # type: ignore[import]
        trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: False})
        log.info("AX check (AXIsProcessTrustedWithOptions no-prompt): %s", trusted)
        if trusted:
            return True
    except Exception as e:
        log.info("AX check (AXIsProcessTrustedWithOptions) exception: %s", e)
    try:
        from ApplicationServices import (  # type: ignore[import]
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
            kAXFocusedApplicationAttribute,
        )
        elem = AXUIElementCreateSystemWide()
        result = AXUIElementCopyAttributeValue(elem, kAXFocusedApplicationAttribute, None)
        log.info("AX functional check raw result: %r (type %s)", result, type(result).__name__)
        err = int(result[0])
        log.info("AX functional check error code: %d (%s)", err,
                 "kAXErrorSuccess" if err == 0 else "kAXErrorAPIDisabled" if err == -25211 else "other")
        # kAXErrorAPIDisabled (-25211) means AX is definitively not granted.
        # Any other error (e.g. -25204 kAXErrorCannotComplete) means the AX
        # API IS accessible — the query itself failed for an unrelated reason.
        return err != -25211
    except Exception as e:
        log.info("AX functional check exception: %s", e)
    return False


def _mic_granted() -> bool:
    """Check Microphone access.

    AVFoundation returns 0 (not determined) for Python's own TCC entry when
    launched from Terminal, even though mic works via Terminal's responsible-
    process grant.  Only statuses 1/2 mean the mic is actually blocked.
    Falls back to a sounddevice device query if AVFoundation is unavailable.
    """
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import]
        status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
        if status == 3:      # explicitly authorized for Python
            return True
        if status in (1, 2): # restricted or denied
            return False
        # status == 0 (not determined) — fall through to sounddevice check
    except Exception:
        pass  # AVFoundation unavailable in this venv — fall through

    # Confirm a usable input device is present (no TCC prompt, just HAL query).
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        return bool(dev["max_input_channels"] > 0)
    except Exception:
        return False


def open_settings() -> Optional[subprocess.Popen]:
    """Launch the settings window as a detached subprocess.

    Returns the Popen object so the caller can track and terminate it on quit.
    The subprocess writes config.json when the user saves.
    The main app picks up changes via its config polling timer.
    """
    ax = _ax_granted()
    mic = _mic_granted()
    log.info("Opening settings: ax_granted=%s mic_granted=%s", ax, mic)

    env = os.environ.copy()
    env["SYPHER_AX_GRANTED"] = "1" if ax else "0"
    env["SYPHER_MIC_GRANTED"] = "1" if mic else "0"

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "sypher_stt.settings_ui"],
            close_fds=True,
            env=env,
        )
        log.info("Settings window opened.")
        return proc
    except Exception as e:
        log.error("Failed to open settings: %s", e)
        return None
