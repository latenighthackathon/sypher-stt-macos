"""Sypher STT — macOS main application orchestrator.

Wires all components together:
  HotkeyManager → AudioRecorder → Transcriber → clipboard paste
  TrayApp (rumps) provides the menu bar UI and drives the NSRunLoop.

Architecture note:
  rumps.App.run() owns the main thread (macOS NSRunLoop).
  All heavy work (audio, transcription) runs in daemon threads.
  State is a simple enum read by a rumps timer to update the icon.
"""

import logging
import shutil
import signal
import subprocess
import sys
import threading
from typing import Optional

import rumps

from sypher_stt import __version__
from sypher_stt.audio import AudioRecorder
from sypher_stt.clipboard import paste_text
from sypher_stt.config import load_config
from sypher_stt.constants import APPDATA_DIR, CONFIG_PATH, DEFAULT_HOTKEY, LOCK_FILE, LOG_DIR, MODELS_DIR, SAMPLE_RATE
from sypher_stt.hotkeys import HotkeyManager, hotkey_display
from sypher_stt.instance import SingleInstance
from sypher_stt.logger import setup_logging
from sypher_stt.settings import open_settings
from sypher_stt.sounds import play_sound
from sypher_stt.stats import record_transcription
from sypher_stt.transcriber import Transcriber
from sypher_stt.tray import AppState, TrayApp

log = logging.getLogger(__name__)


class SypherSTT:
    """Main application class. Owns all components and drives the run loop."""

    def __init__(self) -> None:
        self._config = load_config()
        self._config_mtime: float = (
            CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0
        )

        # Core components
        self._recorder = AudioRecorder(device=self._config.get("audio_device"))
        self._transcriber = Transcriber(
            model_size=self._config.get("model", "base.en")
        )

        # Hotkey
        self._hotkey_manager = HotkeyManager(
            on_start=self._on_hotkey_press,
            on_stop=self._on_hotkey_release,
            hotkey=self._config.get("hotkey", DEFAULT_HOTKEY),
        )

        # State (read by TrayApp timer, written by callbacks)
        self._state = AppState.IDLE
        self._state_lock = threading.Lock()
        self._processing = False
        self._restart_requested = False

        # Tracked child processes (one instance of each allowed at a time)
        self._settings_proc: Optional[subprocess.Popen] = None
        self._wizard_proc:   Optional[subprocess.Popen] = None

        # Build the tray app
        self._tray = TrayApp(
            on_quit=self._quit,
            on_settings=self._open_settings,
            on_setup=self._open_setup_wizard,
            on_uninstall=self._uninstall,
            state_getter=self._get_state,
            on_config_poll=self._poll_config_if_changed,
            hotkey_name=self._config.get("hotkey", DEFAULT_HOTKEY),
            version=__version__,
        )

    # ------------------------------------------------------------------ #
    # State access                                                         #
    # ------------------------------------------------------------------ #

    def _get_state(self) -> AppState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: AppState) -> None:
        with self._state_lock:
            self._state = state

    # ------------------------------------------------------------------ #
    # Hotkey callbacks (called from pynput thread)                        #
    # ------------------------------------------------------------------ #

    def _on_hotkey_press(self) -> None:
        with self._state_lock:
            if self._processing:
                return
            self._state = AppState.RECORDING

        log.info("Recording started.")
        if self._config.get("sound_feedback", True):
            play_sound(self._config.get("sound_start", "Ping"))

        try:
            self._recorder.start_recording()
        except Exception as e:
            log.error("Failed to start recording: %s", e)
            self._set_state(AppState.IDLE)
            self._tray.notify("Recording Error", "Failed to start recording. Check logs for details.")
            if self._config.get("sound_feedback", True):
                play_sound(self._config.get("sound_error", "Basso"))

    def _on_hotkey_release(self) -> None:
        with self._state_lock:
            if self._processing:
                return
            self._processing = True
            self._state = AppState.TRANSCRIBING

        if self._config.get("sound_feedback", True):
            play_sound(self._config.get("sound_stop", "Blow"))

        log.info("Recording stopped, transcribing...")
        audio = self._recorder.stop_recording()

        def _transcribe() -> None:
            try:
                text = self._transcriber.transcribe(audio)
                if text:
                    paste_text(text)
                    if self._config.get("record_stats", True):
                        audio_secs = round(audio.size / SAMPLE_RATE, 1)
                        log.info("Transcribed %d chars, %.1fs audio.", len(text), audio_secs)
                        try:
                            record_transcription(
                                words=len(text.split()),
                                chars=len(text),
                                audio_seconds=audio_secs,
                            )
                        except Exception as e:
                            log.warning("Stats record failed: %s", e)
                else:
                    log.info("No speech detected.")
            except Exception as e:
                log.error("Transcription error: %s", e, exc_info=True)
                self._tray.notify("Transcription Error", "Transcription failed. Check logs for details.")
                if self._config.get("sound_feedback", True):
                    play_sound(self._config.get("sound_error", "Basso"))
            finally:
                with self._state_lock:
                    self._processing = False
                    self._state = AppState.IDLE

        threading.Thread(target=_transcribe, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    def _open_settings(self) -> None:
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            return  # already open
        self._settings_proc = open_settings()

    def _open_setup_wizard(self) -> None:
        """Launch the setup wizard in a subprocess (non-blocking)."""
        if self._wizard_proc is not None and self._wizard_proc.poll() is None:
            return  # already open
        try:
            self._wizard_proc = subprocess.Popen(
                [sys.executable, "-m", "sypher_stt.setup_wizard"]
            )
            log.info("Setup wizard opened.")
        except Exception as e:
            log.error("Failed to open setup wizard: %s", e)

    def _apply_config(self, config: dict) -> None:
        """Apply a new config dict at runtime."""
        self._config = config
        log.info("Config reloaded.")

        try:
            self._hotkey_manager.hotkey_name = config.get("hotkey", DEFAULT_HOTKEY)
        except ValueError as e:
            log.error("Invalid hotkey in config, keeping current: %s", e)
        self._tray.update_hotkey_display(config.get("hotkey", DEFAULT_HOTKEY))
        self._transcriber.model_size = config.get("model", "base.en")

        # Replace recorder if device changed
        if self._recorder.is_recording:
            self._recorder.stop_recording()
        self._recorder = AudioRecorder(device=config.get("audio_device"))

    def _poll_config_if_changed(self) -> None:
        """Called by tray timer — reload config if the file was modified."""
        if not CONFIG_PATH.exists():
            return
        try:
            mtime = CONFIG_PATH.stat().st_mtime
            if mtime != self._config_mtime:
                self._config_mtime = mtime
                self._apply_config(load_config())
        except OSError:
            pass

        # Check for auto-update restart flag written by settings_ui
        restart_flag = APPDATA_DIR / ".restart"
        if restart_flag.exists():
            try:
                restart_flag.unlink(missing_ok=True)
                log.info("Update restart flag detected — restarting.")
                self._hotkey_manager.stop()
                if self._recorder.is_recording:
                    self._recorder.stop_recording()
                self._terminate_subprocesses()
                self._restart_requested = True
                rumps.quit_application()
            except Exception as e:
                log.error("Auto-restart failed: %s", e)

    # ------------------------------------------------------------------ #
    # Quit                                                                 #
    # ------------------------------------------------------------------ #

    def _terminate_subprocesses(self) -> None:
        for proc in (self._settings_proc, self._wizard_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
        self._settings_proc = None
        self._wizard_proc = None

    def _quit(self) -> None:
        log.info("Shutting down.")
        self._hotkey_manager.stop()
        if self._recorder.is_recording:
            self._recorder.stop_recording()
        self._terminate_subprocesses()
        # TrayApp._quit() calls rumps.quit_application() after this callback

    def _uninstall(self) -> None:
        response = rumps.alert(
            title="Uninstall Sypher STT?",
            message=(
                "This will permanently delete:\n"
                "  • Downloaded Whisper models\n"
                "  • Config and settings\n"
                "  • Logs\n\n"
                "The app files in your project folder will not be removed."
            ),
            ok="Uninstall",
            cancel="Cancel",
        )
        if response != 1:
            return

        log.info("Uninstalling — removing user data.")
        self._hotkey_manager.stop()
        if self._recorder.is_recording:
            self._recorder.stop_recording()
        self._terminate_subprocesses()

        for path in (APPDATA_DIR, LOG_DIR, MODELS_DIR):
            try:
                if path.exists():
                    shutil.rmtree(path)
                    log.info("Removed: %s", path)
            except Exception as e:
                log.warning("Could not remove %s: %s", path, e)
        LOCK_FILE.unlink(missing_ok=True)

        rumps.quit_application()

    # ------------------------------------------------------------------ #
    # Preload                                                              #
    # ------------------------------------------------------------------ #

    def _preload_model(self) -> None:
        try:
            self._transcriber.ensure_model()
            log.info("Model ready.")
            self._tray.notify(
                "Sypher STT",
                f"Model loaded. Hold {hotkey_display(self._config.get('hotkey', DEFAULT_HOTKEY))} to speak.",
            )
        except FileNotFoundError as e:
            log.error("%s", e)
            self._tray.notify(
                "Model Not Found",
                "Run: python scripts/download_model.py",
            )
        except Exception as e:
            log.error("Failed to load model: %s", e)
            self._tray.notify("Model Error", "Failed to load model. Check logs for details.")

    # ------------------------------------------------------------------ #
    # Run                                                                  #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        log.info("=" * 50)
        log.info("Sypher STT v%s starting (macOS).", __version__)
        log.info(
            "Hotkey: %s | Model: %s",
            hotkey_display(self._config.get("hotkey", DEFAULT_HOTKEY)),
            self._config.get("model", "base.en"),
        )
        log.info("=" * 50)

        # Start hotkey listener
        self._hotkey_manager.start()

        # Pre-load model in background
        threading.Thread(target=self._preload_model, daemon=True).start()

        # Hand off to the rumps run loop (blocks until quit)
        self._tray.run()


def main() -> None:
    """Application entry point."""
    # Ignore SIGHUP so the process survives terminal close and post-update
    # parent exit without being killed by the inherited process group signal.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # Menu-bar-only (no Dock icon). Must be set before rumps touches NSApp.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from Foundation import NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_("SypherSTT")
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass

    setup_logging()

    guard = SingleInstance()
    if not guard.acquire():
        log.warning("Sypher STT is already running. Exiting.")
        sys.exit(1)

    restart_needed = False
    try:
        # ── First-run setup wizard ──────────────────────────────────────────
        # Run in a subprocess so its NSApplication run loop doesn't kill this
        # process.  We block until the wizard window closes, then re-check
        # needs_setup(): if the user dismissed without completing, exit cleanly.
        from sypher_stt.setup_wizard import needs_setup
        if needs_setup():
            log.info("Running first-run setup wizard.")
            subprocess.run([sys.executable, "-m", "sypher_stt.setup_wizard"])
            if needs_setup():
                log.info("Setup not completed by user. Exiting.")
                sys.exit(0)

        # ── Launch app ─────────────────────────────────────────────────────
        app = SypherSTT()
        app.run()
        restart_needed = app._restart_requested
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as e:
        log.critical("Unhandled exception: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        guard.release()

    # Spawn a fresh process after the lock is released so it can acquire it.
    # Do NOT use start_new_session=True: setsid() on macOS can place the child
    # in a different Mach bootstrap namespace from the Window Server, which
    # prevents NSStatusItem (menu bar icon) from appearing. SIGHUP survival is
    # handled by the SIG_IGN set at the top of main() instead.
    if restart_needed:
        log.info("Spawning fresh process for update restart.")
        subprocess.Popen(
            [sys.executable, "-m", "sypher_stt.app"],
            close_fds=True,
        )


if __name__ == "__main__":
    main()
