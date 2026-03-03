"""macOS menu bar application using rumps.

Provides the system tray icon, status display, and right-click menu.
State transitions are reflected in the menu bar icon in real time via a timer.

States:
  IDLE         — mic outline      (SF Symbol: mic)
  RECORDING    — filled mic       (SF Symbol: mic.fill)
  TRANSCRIBING — waveform         (SF Symbol: waveform)

Falls back to emoji characters if SF Symbols are unavailable.
"""

import logging
from enum import Enum
from typing import Callable, Optional

import rumps

from sypher_stt.hotkeys import hotkey_display

log = logging.getLogger(__name__)


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


# SF Symbol name for each state
_SF_SYMBOLS: dict[AppState, str] = {
    AppState.IDLE:         "mic",
    AppState.RECORDING:    "waveform",         # mic.fill clashes visually with macOS orange privacy indicator
    AppState.TRANSCRIBING: "ellipsis",
}

# Emoji fallback for each state (used if SF Symbols unavailable)
STATE_TITLES: dict[AppState, str] = {
    AppState.IDLE:         "🎙",
    AppState.RECORDING:    "🔴",
    AppState.TRANSCRIBING: "⏳",
}

# Human-readable status for the top menu item
STATE_LABELS: dict[AppState, str] = {
    AppState.IDLE:         "Ready",
    AppState.RECORDING:    "Recording...",
    AppState.TRANSCRIBING: "Transcribing...",
}


class TrayApp(rumps.App):
    """macOS menu bar app with live state icon and context menu."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_settings: Callable[[], None],
        state_getter: Callable[[], AppState],
        on_setup: Optional[Callable[[], None]] = None,
        on_uninstall: Optional[Callable[[], None]] = None,
        on_config_poll: Optional[Callable[[], None]] = None,
        hotkey_name: str = "F5",
        version: str = "",
    ) -> None:
        super().__init__(
            "Sypher STT",
            title=STATE_TITLES[AppState.IDLE],
            quit_button=None,  # We provide our own Quit item
        )

        self._on_quit_cb = on_quit
        self._on_settings_cb = on_settings
        self._on_setup_cb = on_setup
        self._on_uninstall_cb = on_uninstall
        self._state_getter = state_getter
        self._on_config_poll = on_config_poll
        self._hotkey_name = hotkey_name
        self._version = version

        # SF Symbol images (populated in _setup_sf_icons; empty = use emoji fallback)
        self._state_images: dict[AppState, object] = {}

        # Build the menu
        self._status_item = rumps.MenuItem(
            f"Ready — Hold {hotkey_display(hotkey_name)} to speak"
        )
        self._status_item.set_callback(None)  # non-clickable header

        self._version_item = rumps.MenuItem(f"Sypher STT v{version}")
        self._version_item.set_callback(None)

        self.menu = [
            self._status_item,
            self._version_item,
            None,  # separator
            rumps.MenuItem("Settings", callback=self._open_settings),
            rumps.MenuItem("Setup Wizard", callback=self._open_setup),
            None,
            rumps.MenuItem("Uninstall", callback=self._uninstall),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        self._last_state: Optional[AppState] = None  # None forces icon apply on first timer tick
        self._setup_sf_icons()

    # ------------------------------------------------------------------ #
    # SF Symbol icon setup                                                 #
    # ------------------------------------------------------------------ #

    def _setup_sf_icons(self) -> None:
        """Load SF Symbol images for each state. Silent on failure."""
        try:
            from AppKit import NSImage
            images: dict[AppState, object] = {}
            for state, symbol_name in _SF_SYMBOLS.items():
                img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    symbol_name, None
                )
                if img is None:
                    return  # Symbol unavailable — skip all, use emoji fallback
                img.setTemplate_(True)  # Adapts to dark/light mode automatically
                images[state] = img
            self._state_images = images
        except Exception as e:
            log.debug("SF Symbol icons unavailable, using emoji: %s", e)

    def _apply_sf_icon(self, state: AppState) -> bool:
        """Set the SF Symbol image on the status bar button.

        Returns True on success, False to signal caller to use emoji fallback.
        """
        img = self._state_images.get(state)
        if img is None:
            return False
        try:
            btn = self._nsapp.nsstatusitem.button()
            btn.setImage_(img)
            btn.setTitle_("")  # Clear any text directly on the button (avoids rumps fallbackOnName)
            return True
        except Exception as e:
            log.debug("Could not set SF icon: %s", e)
            self._state_images.clear()  # Disable SF icons on failure
            return False

    # ------------------------------------------------------------------ #
    # Timers                                                               #
    # ------------------------------------------------------------------ #

    @rumps.timer(0.2)
    def _update_icon(self, _) -> None:
        state = self._state_getter()
        if state == self._last_state:
            return
        self._last_state = state

        if not self._apply_sf_icon(state):
            self.title = STATE_TITLES[state]

        label = STATE_LABELS[state]
        if state == AppState.IDLE:
            label = f"Ready — Hold {hotkey_display(self._hotkey_name)} to speak"
        self._status_item.title = label

    @rumps.timer(3.0)
    def _poll_config(self, _) -> None:
        """Check if config.json changed (user saved settings) and reload."""
        if self._on_config_poll is not None:
            try:
                self._on_config_poll()
            except Exception as e:
                log.debug("Config poll error: %s", e)

    # ------------------------------------------------------------------ #
    # Menu callbacks                                                       #
    # ------------------------------------------------------------------ #

    def _open_settings(self, _) -> None:
        try:
            self._on_settings_cb()
        except Exception as e:
            log.error("Settings callback error: %s", e)

    def _open_setup(self, _) -> None:
        if self._on_setup_cb is not None:
            try:
                self._on_setup_cb()
            except Exception as e:
                log.error("Setup wizard callback error: %s", e)

    def _uninstall(self, _) -> None:
        if self._on_uninstall_cb is not None:
            try:
                self._on_uninstall_cb()
            except Exception as e:
                log.error("Uninstall callback error: %s", e)

    def _quit(self, _) -> None:
        try:
            self._on_quit_cb()
        except Exception as e:
            log.error("Quit callback error: %s", e)
        rumps.quit_application()

    # ------------------------------------------------------------------ #
    # Public helpers called from app.py background threads                #
    # ------------------------------------------------------------------ #

    def notify(self, title: str, message: str) -> None:
        """Show a macOS notification banner."""
        try:
            rumps.notification(title, "", message)
        except Exception as e:
            log.debug("Notification failed: %s", e)

    def update_hotkey_display(self, hotkey_name: str) -> None:
        """Refresh the hotkey shown in the status menu item."""
        self._hotkey_name = hotkey_name
        if self._last_state in (AppState.IDLE, None):
            self._status_item.title = (
                f"Ready — Hold {hotkey_display(hotkey_name)} to speak"
            )
