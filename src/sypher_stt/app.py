"""Sypher STT — macOS main application orchestrator.

Wires all components together:
  HotkeyManager → AudioRecorder → Transcriber → clipboard paste
  TrayApp (rumps) provides the menu bar UI and drives the NSRunLoop.

Architecture note:
  rumps.App.run() owns the main thread (macOS NSRunLoop).
  All heavy work (audio, transcription) runs in daemon threads.
  State is a simple enum read by a rumps timer to update the icon.
"""

import atexit
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
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

# Safety watchdog: if TRANSCRIBING lasts longer than this, auto-reset to IDLE.
# Set well above the longest realistic transcription (large-v3, 2-min audio on
# slow hardware) to avoid false positives. Only fires on genuine hangs.
_TRANSCRIPTION_TIMEOUT_S = 90   # 90s — generous for any model/recording length

# Sentinel distinguishing "no pending device change" from a pending change to
# the system-default device (None is itself a valid audio_device value).
_UNSET = object()


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
        self._restart_run_sh: Optional[Path] = None
        self._restart_watcher_spawned: bool = False
        self._transcribing_since: Optional[float] = None

        # Monotonic id for each transcription session.  Bumped when a new
        # recording starts and when the watchdog fires, so a stale/superseded
        # transcription thread can detect it no longer owns the session and
        # must not paste or reset shared state.
        self._generation: int = 0
        # The exact recorder a hold started on, so release stops that same
        # object even if a config reload swapped self._recorder.
        self._active_recorder: Optional[AudioRecorder] = None
        # Deferred audio-device change to apply once recording finishes.
        self._pending_device = _UNSET

        # Tracked child processes (one instance of each allowed at a time)
        self._settings_proc: Optional[subprocess.Popen] = None
        self._wizard_proc:   Optional[subprocess.Popen] = None

        # Build the tray app
        self._tray = TrayApp(
            on_quit=self._quit,
            on_settings=self._open_settings,
            on_setup=self._open_setup_wizard,
            on_uninstall=self._uninstall,
            on_restart=self._restart,
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

    def _is_current(self, generation: int) -> bool:
        """True if *generation* is still the active transcription session."""
        with self._state_lock:
            return self._generation == generation

    def _on_hotkey_press(self) -> None:
        with self._state_lock:
            if self._state == AppState.RECORDING:
                return  # spurious repeat press while already recording
            if self._processing:
                busy = True
                recorder = None
            else:
                busy = False
                # Snapshot the recorder this hold owns so release stops the
                # same object even if a config reload swaps self._recorder.
                recorder = self._recorder
                self._active_recorder = recorder

        if busy:
            # Hotkey re-triggered while a previous transcription is still
            # running — give the user feedback instead of silently dropping it.
            log.info("Hotkey pressed while transcribing — ignored.")
            if self._config.get("sound_feedback", True):
                play_sound(self._config.get("sound_error", "Basso"))
            return

        if self._config.get("sound_feedback", True):
            play_sound(self._config.get("sound_start", "Ping"))

        try:
            recorder.start_recording()
        except Exception as e:
            log.error("Failed to start recording: %s", e)
            with self._state_lock:
                self._active_recorder = None
            self._tray.notify("Recording Error", "Failed to start recording. Check logs for details.")
            if self._config.get("sound_feedback", True):
                play_sound(self._config.get("sound_error", "Basso"))
            return

        # Enter RECORDING only once the stream is actually live, so a release
        # with no live recording is cleanly distinguishable below.
        with self._state_lock:
            if self._active_recorder is recorder:  # not cancelled meanwhile
                self._state = AppState.RECORDING
        log.info("Recording started.")

    def _on_hotkey_release(self) -> None:
        with self._state_lock:
            # Only transcribe when a recording is genuinely in progress.  Guards
            # release-without-effective-press and re-entrancy.
            if self._state != AppState.RECORDING or self._processing:
                return
            self._processing = True
            self._state = AppState.TRANSCRIBING
            self._transcribing_since = time.monotonic()
            self._generation += 1
            generation = self._generation
            recorder = self._active_recorder or self._recorder
            self._active_recorder = None

        if self._config.get("sound_feedback", True):
            play_sound(self._config.get("sound_stop", "Blow"))

        log.info("Recording stopped, transcribing...")
        audio = recorder.stop_recording()

        def _transcribe() -> None:
            try:
                text = self._transcriber.transcribe(audio)
                if text:
                    if not self._is_current(generation):
                        log.warning("Transcription %d superseded — not pasting.", generation)
                    else:
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
                    # Distinct feedback — the most common real failure mode was
                    # previously indistinguishable from success.
                    log.info("No speech detected.")
                    if self._config.get("sound_feedback", True):
                        play_sound(self._config.get("sound_error", "Basso"))
                    self._tray.notify(
                        "No speech detected",
                        "Nothing was transcribed — try speaking a little louder or longer.",
                    )
            except FileNotFoundError as e:
                log.error("Model not available: %s", e)
                self._set_state(AppState.ERROR)
                self._tray.update_error("No model installed — open Setup Wizard")
                self._tray.notify("Model Not Found", "Open the Setup Wizard to download a model.")
                if self._config.get("sound_feedback", True):
                    play_sound(self._config.get("sound_error", "Basso"))
            except Exception as e:
                log.error("Transcription error: %s", e, exc_info=True)
                self._tray.notify("Transcription Error", "Transcription failed. Check logs for details.")
                if self._config.get("sound_feedback", True):
                    play_sound(self._config.get("sound_error", "Basso"))
            finally:
                with self._state_lock:
                    # Only the owning generation may reset shared state — a
                    # watchdog-superseded stale thread must not clobber a fresh
                    # session that started after it was abandoned.
                    if self._generation == generation:
                        self._processing = False
                        self._transcribing_since = None
                        if self._state == AppState.TRANSCRIBING:
                            self._state = AppState.IDLE

        threading.Thread(target=_transcribe, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    def _open_settings(self) -> None:
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            # Window already open — raise it to the front via SIGUSR1
            try:
                self._settings_proc.send_signal(signal.SIGUSR1)
            except Exception as e:
                log.warning("Could not raise settings window: %s", e)
            return
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

        # Rebuild the recorder only when the device actually changed, and never
        # swap it out from under an in-progress hold — that orphans the live
        # stream and silently drops the user's dictation.  If a hold is active,
        # defer the change until recording finishes (applied by the poll timer).
        new_device = config.get("audio_device")
        with self._state_lock:
            busy = self._state == AppState.RECORDING or self._processing
            same_device = new_device == self._recorder.device
            if same_device:
                self._pending_device = _UNSET
                action = "none"
            elif busy or self._recorder.is_recording:
                self._pending_device = new_device
                action = "defer"
            else:
                self._recorder = AudioRecorder(device=new_device)
                self._pending_device = _UNSET
                action = "rebuilt"
        if action == "rebuilt":
            log.info("Audio device changed to %s; recorder rebuilt.", new_device)
        elif action == "defer":
            log.info("Audio device change to %s deferred until recording ends.", new_device)

    def _poll_config_if_changed(self) -> None:
        """Called by tray timer — reload config if the file was modified."""
        # Safety watchdog: auto-reset if stuck in TRANSCRIBING too long.
        _watchdog_fired = False
        with self._state_lock:
            if (
                self._state == AppState.TRANSCRIBING
                and self._transcribing_since is not None
            ):
                elapsed = time.monotonic() - self._transcribing_since
                if elapsed > _TRANSCRIPTION_TIMEOUT_S:
                    log.error(
                        "Transcription watchdog: stuck for %.0fs, resetting to idle.", elapsed
                    )
                    self._processing = False
                    self._state = AppState.IDLE
                    self._transcribing_since = None
                    # Bump the generation so the abandoned thread can't later
                    # paste or reset state over a fresh session.
                    self._generation += 1
                    _watchdog_fired = True
                else:
                    log.debug("Transcription in progress (%.0fs elapsed).", elapsed)
        if _watchdog_fired:
            if self._config.get("sound_feedback", True):
                play_sound(self._config.get("sound_error", "Basso"))

        # Apply a deferred audio-device change once recording has fully ended.
        with self._state_lock:
            pending = self._pending_device
            idle = self._state == AppState.IDLE and not self._processing
        if pending is not _UNSET and idle and not self._recorder.is_recording:
            with self._state_lock:
                self._pending_device = _UNSET
            if pending != self._recorder.device:
                self._recorder = AudioRecorder(device=pending)
                log.info("Deferred audio device change applied: %s", pending)

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
                # Reuse the hardened restart path (cleanup + spawn watcher that
                # re-runs run.sh) instead of relying on the atexit fallback.
                self._restart()
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

    def _restart(self) -> None:
        """Clean up and spawn a Terminal.app watcher, then let tray quit this process."""
        log.info("Restart requested — cleaning up.")
        self._hotkey_manager.stop()
        if self._recorder.is_recording:
            self._recorder.stop_recording()
        self._terminate_subprocesses()

        # Derive run.sh from the running module (trusted) rather than the
        # world-writable .project_root hint file — a tampered hint must not be
        # able to redirect Restart to attacker-chosen code.
        from sypher_stt.constants import RUN_SH
        if RUN_SH.exists():
            self._restart_run_sh = RUN_SH

        self._restart_requested = True

        # Spawn the watcher NOW — while Python is fully alive — rather than in
        # an atexit handler.  The shell command polls for this process's PID to
        # disappear (guaranteeing the flock is released), then runs run.sh.
        # This sidesteps all atexit / interpreter-shutdown timing issues.
        run_sh = self._restart_run_sh
        if run_sh is not None and run_sh.exists():
            pid = os.getpid()
            cmd = shlex.quote(str(run_sh))
            # Poll until this PID is gone (so the flock is released), then launch
            # run.sh in the same window.  (PID identity isn't perfectly stable,
            # but the flock LOCK_NB backstop prevents any double-launch.)
            wait_cmd = f"while kill -0 {pid} 2>/dev/null; do sleep 0.1; done; bash {cmd}"
            # The command is embedded in an AppleScript `do script "..."` double-
            # quoted literal; escape backslashes and double-quotes (the shell
            # layer is already handled by shlex.quote above) so an install path
            # containing " or \ can't corrupt the statement.
            as_cmd = wait_cmd.replace("\\", "\\\\").replace('"', '\\"')
            try:
                subprocess.Popen(
                    [
                        "osascript",
                        "-e", 'tell application "Terminal"',
                        "-e", "activate",
                        "-e", f'do script "{as_cmd}"',
                        "-e", "end tell",
                    ],
                    close_fds=True,
                )
                self._restart_watcher_spawned = True
                log.info("Restart watcher spawned (watching PID %d).", pid)
            except Exception as e:
                log.error("Failed to spawn restart watcher: %s", e)
        else:
            log.warning("run.sh not found — restart watcher not spawned.")
        # TrayApp._restart_app() calls rumps.quit_application() after this callback

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
            # Clear any prior model-error state now that a model is loaded.
            if self._get_state() == AppState.ERROR:
                self._set_state(AppState.IDLE)
            self._tray.notify(
                "Sypher STT",
                f"Model loaded. Hold {hotkey_display(self._config.get('hotkey', DEFAULT_HOTKEY))} to speak.",
            )
        except FileNotFoundError as e:
            log.error("%s", e)
            self._tray.update_error("No model installed — open Setup Wizard")
            self._set_state(AppState.ERROR)
            self._tray.notify(
                "Model Not Found",
                "Open the Setup Wizard to download a model.",
            )
        except Exception as e:
            log.error("Failed to load model: %s", e)
            self._tray.update_error("Model failed to load — see logs")
            self._set_state(AppState.ERROR)
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

    # ── atexit: release lock + spawn restart ───────────────────────────────
    # NSApplication.terminate_() exits Python via SystemExit (a BaseException).
    # Code after a try/finally block is skipped when SystemExit propagates, so
    # the restart spawn must live in an atexit handler, which runs even during
    # SystemExit-triggered interpreter shutdown.  The handler reads
    # app._restart_requested directly — that flag is set by _restart() BEFORE
    # rumps.quit_application() is called, so it's always True when needed.
    app: Optional["SypherSTT"] = None

    @atexit.register
    def _on_exit() -> None:
        guard.release()
        # Primary restart path: Terminal watcher already spawned in _restart().
        # Only fall back to a direct spawn if the watcher couldn't be created.
        if app is None or not app._restart_requested or app._restart_watcher_spawned:
            return
        run_sh = app._restart_run_sh
        log.info("Watcher not spawned — falling back to direct spawn.")
        if run_sh is not None and run_sh.exists():
            subprocess.Popen([str(run_sh)], close_fds=True)
        else:
            subprocess.Popen([sys.executable, "-m", "sypher_stt.app"], close_fds=True)

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
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as e:
        log.critical("Unhandled exception: %s", e, exc_info=True)
        sys.exit(1)
    # _on_exit() atexit handler runs on interpreter shutdown and handles
    # both guard.release() and the conditional restart spawn.


if __name__ == "__main__":
    main()
