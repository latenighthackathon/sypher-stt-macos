"""Sypher STT — Privacy-first voice-to-text dictation for macOS.

Hold a hotkey to speak, release to transcribe and paste into any window.
Fully offline using local Whisper models via faster-whisper.
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("sypher-stt")
except PackageNotFoundError:
    __version__ = "dev"

__app_name__ = "Sypher STT"
