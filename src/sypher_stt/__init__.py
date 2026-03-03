"""Sypher STT — Privacy-first voice-to-text dictation for macOS.

Hold a hotkey to speak, release to transcribe and paste into any window.
Fully offline using local Whisper models via faster-whisper.
"""

# Version is hardcoded here so in-place file updates during auto-update
# immediately reflect the correct version on next startup, without relying
# on importlib.metadata (which reads from the .dist-info directory that
# is not replaced during the in-place update).
__version__ = "1.1.0"

__app_name__ = "Sypher STT"
