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
        # Fail closed: only a clean success (kAXErrorSuccess == 0) proves the AX
        # API is usable.  Any non-zero code (kAXErrorAPIDisabled -25211,
        # kAXErrorCannotComplete -25204, etc.) is treated as NOT granted rather
        # than assuming access we may not have.
        return err == 0
    except Exception as e:
        log.info("AX functional check exception: %s", e)
    return False


def _mic_granted() -> bool:
    """Check Microphone access via the TCC authorization status.

    Returns True only for status 3 (authorized).  Status 0 (not determined)
    means the user has not yet consented and is reported as NOT granted — the
    setup wizard / settings "Enable Microphone" button is what triggers the
    real consent prompt.  We deliberately do NOT fall back to a device-presence
    probe: a microphone existing is unrelated to the user having granted access
    (that fail-open made the badge lie).
    """
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import]
        return int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)) == 3
    except Exception:
        pass  # AVFoundation unavailable in this venv — try the ctypes path
    try:
        import ctypes
        import objc  # PyObjC core — always present since rumps requires it
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"
        )
        return int(objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun")) == 3
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
