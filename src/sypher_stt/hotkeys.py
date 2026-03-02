"""Global hotkey manager for push-to-talk recording.

Supports single keys (e.g. "f5", "caps_lock") and modifier+key combos
(e.g. "option+space", "cmd+shift+space", "ctrl+f5").

On macOS, pynput requires Accessibility permission granted in
System Settings → Privacy & Security → Accessibility.
"""

import logging
import threading
from typing import Callable, Optional

from pynput import keyboard

_RELEASE_DEBOUNCE = 0.08  # seconds — absorbs spurious key-up events between key-repeats

log = logging.getLogger(__name__)

# ── Modifier canonicalization ─────────────────────────────────────────────────

_MOD_CANONICAL: dict = {}
for _k, _name in [
    (keyboard.Key.ctrl,    "ctrl"),
    (keyboard.Key.ctrl_l,  "ctrl"),
    (keyboard.Key.ctrl_r,  "ctrl"),
    (keyboard.Key.cmd,     "cmd"),
    (keyboard.Key.cmd_l,   "cmd"),
    (keyboard.Key.cmd_r,   "cmd"),
    (keyboard.Key.shift,   "shift"),
    (keyboard.Key.shift_l, "shift"),
    (keyboard.Key.shift_r, "shift"),
    (keyboard.Key.alt,     "option"),
    (keyboard.Key.alt_l,   "option"),
    (keyboard.Key.alt_r,   "option"),
]:
    _MOD_CANONICAL[_k] = _name

_MODIFIERS: frozenset = frozenset({"ctrl", "cmd", "shift", "option"})

# Keys safe to use without a modifier (function keys + navigation)
_STANDALONE_KEYS: frozenset = frozenset({
    "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12",
    "caps_lock", "delete", "home", "end", "page_up", "page_down",
})

# Keys that need at least one modifier to avoid intercepting normal typing
_COMBO_ONLY_KEYS: frozenset = frozenset({
    "space", "enter", "tab", "esc", "backspace",
})

# Input normalization aliases
_KEY_ALIASES: dict = {
    "command": "cmd",
    "control": "ctrl",
    "alt":     "option",
    "return":  "enter",
    "del":     "delete",
    "escape":  "esc",
}

# Canonical name → pynput Key (kept for any code that still imports KEY_MAP)
KEY_MAP: dict = {
    "f1": keyboard.Key.f1,   "f2": keyboard.Key.f2,   "f3": keyboard.Key.f3,
    "f4": keyboard.Key.f4,   "f5": keyboard.Key.f5,   "f6": keyboard.Key.f6,
    "f7": keyboard.Key.f7,   "f8": keyboard.Key.f8,   "f9": keyboard.Key.f9,
    "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
    "caps_lock":  keyboard.Key.caps_lock,
    "delete":     keyboard.Key.delete,
    "backspace":  keyboard.Key.backspace,
    "home":       keyboard.Key.home,
    "end":        keyboard.Key.end,
    "page_up":    keyboard.Key.page_up,
    "page_down":  keyboard.Key.page_down,
    "space":      keyboard.Key.space,
    "enter":      keyboard.Key.enter,
    "tab":        keyboard.Key.tab,
    "esc":        keyboard.Key.esc,
}


# ── Key normalization ──────────────────────────────────────────────────────────

def _normalize_key(key) -> str:
    """Convert a pynput key object to a canonical lowercase string."""
    if key in _MOD_CANONICAL:
        return _MOD_CANONICAL[key]
    if isinstance(key, keyboard.Key):
        return key.name  # "f5", "space", "caps_lock", etc.
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char.lower()
        if key.vk:
            return f"vk{key.vk}"
    return str(key).lower()


# ── Public hotkey utilities ────────────────────────────────────────────────────

def parse_hotkey(s: str) -> frozenset:
    """Parse a hotkey string into a frozenset of canonical key names.

    Examples::

        parse_hotkey("f5")              → frozenset({"f5"})
        parse_hotkey("option+space")    → frozenset({"option", "space"})
        parse_hotkey("cmd+shift+space") → frozenset({"cmd", "shift", "space"})
    """
    parts = [p.strip().lower() for p in s.split("+") if p.strip()]
    return frozenset(_KEY_ALIASES.get(p, p) for p in parts)


def validate_hotkey(s: str) -> bool:
    """Return True if *s* is a valid hotkey string."""
    if not s or not isinstance(s, str):
        return False
    parts = parse_hotkey(s)
    if not parts:
        return False
    non_mods = parts - _MODIFIERS
    if len(non_mods) != 1:
        return False  # must have exactly one main key
    main = next(iter(non_mods))
    has_mod = bool(parts & _MODIFIERS)

    if main in _STANDALONE_KEYS:
        return True  # safe alone (F-keys, nav keys)
    if main in _COMBO_ONLY_KEYS:
        return has_mod  # space/enter/esc need a modifier
    if len(main) == 1 and main.isalnum():
        return has_mod  # letters/digits need a modifier
    return False


def hotkey_display(s: str) -> str:
    """Return a human-readable display string for a hotkey.

    Examples::

        hotkey_display("f5")              → "F5"
        hotkey_display("option+space")    → "⌥Space"
        hotkey_display("cmd+shift+space") → "⌘⇧Space"
        hotkey_display("ctrl+f5")         → "⌃F5"
    """
    parts = parse_hotkey(s)
    mod_order = ["ctrl", "option", "shift", "cmd"]
    mods = [m for m in mod_order if m in parts]
    non_mods = parts - _MODIFIERS
    if not non_mods:
        return s.upper()
    main = next(iter(non_mods))
    if main in _KEY_DISPLAY:
        key_str = _KEY_DISPLAY[main]
    elif main.startswith("f") and main[1:].isdigit():
        key_str = main.upper()
    elif len(main) == 1:
        key_str = main.upper()
    else:
        key_str = main.capitalize()
    return "".join(_MOD_SYMBOLS[m] for m in mods) + key_str


# Display helpers — module-level so hotkey_display() doesn't rebuild them each call
_MOD_SYMBOLS: dict = {"ctrl": "⌃", "option": "⌥", "shift": "⇧", "cmd": "⌘"}
_KEY_DISPLAY: dict = {
    "space": "Space",   "enter": "Return",  "tab": "Tab",
    "esc": "Esc",       "delete": "Delete", "backspace": "Backspace",
    "caps_lock": "Caps", "home": "Home",    "end": "End",
    "page_up": "Pg↑",   "page_down": "Pg↓",
}

# ── HotkeyManager ─────────────────────────────────────────────────────────────

class HotkeyManager:
    """Global push-to-talk hotkey listener supporting single keys and combos.

    Hold the configured key combination to trigger on_start.
    Releasing any key in the combo triggers on_stop.
    Thread-safe with lock-protected state.

    Usage::

        mgr = HotkeyManager(on_start=record, on_stop=stop, hotkey="option+space")
        mgr.start()   # non-blocking
        ...
        mgr.stop()
    """

    def __init__(
        self,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        hotkey: str = "f5",
    ) -> None:
        if not validate_hotkey(hotkey):
            raise ValueError(
                f"Invalid hotkey '{hotkey}'. "
                "Use a key name (f5, caps_lock) or a combo (option+space, ctrl+f5)."
            )
        self._on_start = on_start
        self._on_stop = on_stop
        self._hotkey_name = hotkey
        self._combo = parse_hotkey(hotkey)
        self._listener: Optional[keyboard.Listener] = None
        self._pressed: set = set()
        self._is_held = False
        self._held_lock = threading.Lock()
        self._active = False
        self._stop_timer: Optional[threading.Timer] = None

    def _combo_satisfied(self) -> bool:
        return self._combo.issubset(self._pressed)

    def _on_press(self, key) -> None:
        norm = _normalize_key(key)
        if not self._active:
            return
        with self._held_lock:
            # Cancel a pending debounced stop — key is still held.
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            self._pressed.add(norm)
            if not (self._combo_satisfied() and not self._is_held):
                return
            self._is_held = True
        try:
            self._on_start()
        except Exception as e:
            log.error("on_start callback error: %s", e, exc_info=True)

    def _on_release(self, key) -> None:
        if not self._active:
            return
        norm = _normalize_key(key)
        with self._held_lock:
            self._pressed.discard(norm)
            if not (self._is_held and not self._combo_satisfied()):
                return
            # Debounce: wait before firing stop so spurious inter-repeat
            # key-up events (seen on some Macs) don't restart recording.
            if self._stop_timer is not None:
                self._stop_timer.cancel()
            t = threading.Timer(_RELEASE_DEBOUNCE, self._fire_stop)
            t.daemon = True
            self._stop_timer = t
        t.start()

    def _fire_stop(self) -> None:
        with self._held_lock:
            self._stop_timer = None
            if not self._is_held:
                return
            self._is_held = False
        try:
            self._on_stop()
        except Exception as e:
            log.error("on_stop callback error: %s", e, exc_info=True)

    def start(self) -> None:
        """Start listening for the hotkey. Non-blocking."""
        self._active = True
        self._pressed.clear()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        self._listener.wait()  # block until the CGEventTap is live
        log.info("Hotkey listener started (%s).", self._hotkey_name)

    def stop(self) -> None:
        """Stop listening for the hotkey."""
        self._active = False
        with self._held_lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
        if self._listener is not None:
            self._listener.stop()
            self._listener.join(timeout=2.0)
        self._listener = None
        log.info("Hotkey listener stopped.")

    @property
    def hotkey_name(self) -> str:
        return self._hotkey_name

    @hotkey_name.setter
    def hotkey_name(self, value: str) -> None:
        if not validate_hotkey(value):
            raise ValueError(f"Invalid hotkey '{value}'.")
        self._hotkey_name = value
        self._combo = parse_hotkey(value)
        with self._held_lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            self._pressed.clear()
            self._is_held = False
        log.info("Hotkey changed to %s.", value)
