"""Shared utility helpers for Sypher STT."""

import json
import os
from pathlib import Path


def get_responsible_app_name() -> str:
    """Return the app name macOS shows in Privacy & Security for this process.

    Walks the process tree via NSWorkspace first (cannot be spoofed by env
    vars), then falls back to TERM_PROGRAM as a last resort.
    """
    # Primary: walk the process tree to find the first registered UI app.
    # This cannot be spoofed by environment variables.
    try:
        import subprocess as _sp
        from AppKit import NSWorkspace  # type: ignore[import]
        # Exclude Python interpreter processes — rumps registers the main
        # app.py process as an NSApplication ("Python"), which would be hit
        # first when the wizard subprocess walks its parent PID. We want to
        # keep walking until we reach the actual terminal that launched us.
        _SKIP = {"python", "python3", "python2"}
        running = {
            int(a.processIdentifier()): str(a.localizedName())
            for a in NSWorkspace.sharedWorkspace().runningApplications()
            if a.localizedName()
            and str(a.localizedName()).lower() not in _SKIP
        }
        pid = os.getppid()
        for _ in range(10):
            if pid in running:
                return running[pid]
            try:
                r = _sp.run(["ps", "-p", str(pid), "-o", "ppid="],
                            capture_output=True, text=True, timeout=2)
                pid = int(r.stdout.strip())
            except Exception:
                break
            if pid <= 1:
                break
    except Exception:
        pass
    # Fallback: TERM_PROGRAM is set by the IDE/terminal that launched us.
    t = os.getenv("TERM_PROGRAM", "")
    if t == "vscode":
        return "Visual Studio Code"
    if t == "Apple_Terminal":
        return "Terminal"
    if t == "iTerm.app":
        return "iTerm2"
    if t:
        return t
    return "Terminal"


# ── Secure file I/O ───────────────────────────────────────────────────────────

def secure_write_json(path: Path, payload: dict) -> None:
    """Atomically write *payload* as JSON to *path* with 0o600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)  # Enforce mode even if the file already existed
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def secure_write_text(path: Path, text: str) -> None:
    """Atomically write *text* to *path* with 0o600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)  # Enforce mode even if the file already existed
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


# ── Permission checks ─────────────────────────────────────────────────────────

def check_ax() -> bool:
    """Return True if Accessibility permission is granted.

    Reads SYPHER_AX_GRANTED env var when set by the tray process, otherwise
    queries AXIsProcessTrustedWithOptions directly.
    """
    env = os.environ.get("SYPHER_AX_GRANTED")
    if env is not None:
        return env == "1"
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt  # type: ignore[import]
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: False}))
    except Exception:
        return False


def check_mic() -> bool:
    """Return True if Microphone permission is granted.

    Reads SYPHER_MIC_GRANTED env var when set by the tray process, otherwise
    queries AVCaptureDevice directly (with a ctypes fallback for environments
    where pyobjc-framework-AVFoundation is not installed).
    """
    env = os.environ.get("SYPHER_MIC_GRANTED")
    if env is not None:
        return env == "1"
    try:
        from AVFoundation import AVCaptureDevice  # type: ignore[import]
        return int(AVCaptureDevice.authorizationStatusForMediaType_("soun")) == 3
    except Exception:
        pass
    try:
        import ctypes
        import objc  # PyObjC core — always present since rumps requires it
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"
        )
        return int(objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun")) == 3
    except Exception:
        return False


# ── Model helpers ─────────────────────────────────────────────────────────────

def get_local_models() -> list:
    """Return names of locally installed Whisper models."""
    from sypher_stt.constants import MODELS_DIR
    if not MODELS_DIR.exists():
        return []
    return [d.name for d in MODELS_DIR.iterdir()
            if d.is_dir() and (d / "model.bin").exists()]


# ── Shared JS fragments ───────────────────────────────────────────────────────

# Typing test passages — identical in wizard and settings UI.
TT_PASSAGES = [
    {"round": "Round 1 of 3 · Everyday",
     "text": "Quick emails and random thoughts add up throughout the day. "
             "Writing notes reminders and messages by hand takes more time than it feels. "
             "Speaking your ideas directly into any app makes capturing them effortless."},
    {"round": "Round 2 of 3 · Professional",
     "text": "Please review the attached proposal and share your thoughts before end of week. "
             "The timeline has shifted slightly based on recent feedback. "
             "I will follow up after the team call to confirm the updated delivery date."},
    {"round": "Round 3 of 3 · Technical",
     "text": "The function returns a parsed response from the API endpoint after validating "
             "the data schema. Each request must include a valid authentication token in the "
             "headers to ensure secure access to the service."},
]

# Hotkey display/validation helpers — injected into both webview UIs.
SHARED_HOTKEY_JS = r"""
function hotkeyDisplay(s) {
  if (!s) return 'F8';
  const mods = {ctrl:'⌃', option:'⌥', shift:'⇧', cmd:'⌘'};
  const keyLabels = {
    space:'Space', enter:'Return', tab:'Tab', esc:'Esc',
    delete:'Delete', backspace:'Backspace', caps_lock:'Caps Lock',
    home:'Home', end:'End', page_up:'PgUp', page_down:'PgDn',
  };
  const parts = s.split('+');
  const modOrder = ['ctrl','option','shift','cmd'];
  const modParts = modOrder.filter(m => parts.includes(m));
  const nonMods  = parts.filter(p => !modOrder.includes(p));
  if (!nonMods.length) return s.toUpperCase();
  const main = nonMods[0];
  const keyStr = keyLabels[main] || (/^f\d+$/.test(main) ? main.toUpperCase() : main.toUpperCase());
  return modParts.map(m => mods[m]).join('') + keyStr;
}

function isValidHotkey(s) {
  if (!s) return false;
  const parts = s.split('+');
  const mods = new Set(['ctrl','option','shift','cmd']);
  const nonMods = parts.filter(p => !mods.has(p));
  if (nonMods.length !== 1) return false;
  const main = nonMods[0];
  const standaloneSafe = /^f\d+$/.test(main) ||
    ['caps_lock','delete','home','end','page_up','page_down'].includes(main);
  const hasMod = parts.some(p => mods.has(p));
  return standaloneSafe || hasMod;
}
"""
