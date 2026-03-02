"""Settings window — two-panel layout matching Glaido's design.

Uses PyObjC + WKWebView (replaces tkinter which crashes on macOS 26 Tahoe).
Exact CSS variables from Glaido's compiled app:
  --sidebar-bg: #09090b  --body-bg: #1b1b1d  --cards-bg: #18181b
  --border: #27272a      --hover-bg: #1f1a0e  --theme-gold: #f5c518

Run standalone:  python -m sypher_stt.settings_ui
"""

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from sypher_stt.constants import (
    APPDATA_DIR as _APPDATA_DIR,
    CONFIG_PATH as _CONFIG_PATH,
    AVAILABLE_MODELS as _AVAILABLE_MODELS,
    SYSTEM_SOUNDS as _SYSTEM_SOUNDS,
    MODELS_DIR as _MODELS_DIR,
    LOG_DIR as _LOG_DIR,
)
from sypher_stt.utils import (
    secure_write_json as _secure_write_json,
    secure_write_text as _secure_write_text,
    check_ax as _check_ax,
    check_mic as _check_mic,
    get_local_models as _local_models,
    TT_PASSAGES as _TT_PASSAGES,
    SHARED_HOTKEY_JS as _SHARED_HOTKEY_JS,
)

log = logging.getLogger(__name__)

try:
    from sypher_stt import __version__ as _VERSION
except ImportError:
    _VERSION = "dev"

_GITHUB_REPO  = "latenighthackathon/sypher-stt-macos"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _parse_version(v: str) -> tuple:
    """Parse a version string like '1.2.3' or 'v1.2.3' into a comparable tuple."""
    try:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())
    except Exception:
        return (0,)


# ── Data helpers ──────────────────────────────────────────────────────────────

# Preset hotkeys: (value, display_label)
_HOTKEY_PRESETS: List[Tuple[str, str]] = [
    ("option+space",     "⌥ Space"),
    ("ctrl+space",       "⌃ Space"),
    ("cmd+shift+space",  "⌘⇧ Space"),
    ("ctrl+shift+space", "⌃⇧ Space"),
    ("option+f1",        "⌥ F1"),
    ("option+f2",        "⌥ F2"),
    ("option+f5",        "⌥ F5"),
    ("option+f6",        "⌥ F6"),
    ("f5",               "F5"),
    ("f6",               "F6"),
    ("f7",               "F7"),
    ("f8",               "F8"),
    ("caps_lock",        "Caps Lock"),
]

_DEFAULT_CFG = {
    "hotkey": "f8",
    "model": "base.en",
    "audio_device": None,
    "sound_feedback": True,
    "sound_start": "Ping",
    "sound_stop": "Blow",
    "sound_error": "Basso",
    "record_stats": True,
}

_SOUND_KEYS = {"sound_start", "sound_stop", "sound_error"}

_VALID_HOTKEYS = {v for v, _ in _HOTKEY_PRESETS}


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        try:
            d = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                cfg = dict(_DEFAULT_CFG)
                for k, v in d.items():
                    if k not in _DEFAULT_CFG:
                        continue
                    if k in _SOUND_KEYS:
                        if v in _SYSTEM_SOUNDS:
                            cfg[k] = v
                    elif k == "model":
                        if v in _AVAILABLE_MODELS:
                            cfg[k] = v
                    elif k == "hotkey":
                        from sypher_stt.hotkeys import validate_hotkey as _vh
                        if _vh(v):
                            cfg[k] = v
                    else:
                        cfg[k] = v
                return cfg
        except Exception:
            pass
    return dict(_DEFAULT_CFG)


def _save_config(cfg: dict) -> None:
    _secure_write_json(_CONFIG_PATH, cfg)



def _input_devices() -> List[Tuple[Optional[int], str]]:
    try:
        import sounddevice as sd
        return [(None, "System Default")] + [
            (i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return [(None, "System Default")]


from sypher_stt.utils import get_responsible_app_name as _get_responsible_app_name


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
:root {
  --sidebar-bg: #0a0a10;
  --body-bg:    #0d0d14;
  --cards-bg:   #13131e;
  --border:     #1e1e2e;
  --hover-bg:    #1c1c2e;
  --accent:      #818cf8;
  --accent-2:    #a5b4fc;
  --accent-3:    #6366f1;
  --btn-bg:      #2e2b7a;
  --btn-hover:   #252266;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100vh; overflow: hidden; }
body {
  background: var(--body-bg);
  color: white;
  font-family: -apple-system, 'SF Pro Text', 'Helvetica Neue', sans-serif;
  display: flex;
  -webkit-user-select: none;
  user-select: none;
}

/* ── SIDEBAR ── */
.sidebar {
  width: 200px;
  min-width: 200px;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 20px 0 16px;
  flex-shrink: 0;
}

.sidebar-logo-block {
  padding: 0 16px 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}
.logo-icon { font-size: 22px; display: block; margin-bottom: 6px; }
.logo-name-row { display: flex; align-items: center; gap: 6px; }
.logo-name { font-size: 15px; font-weight: 700; color: white; }
.logo-name-grad { color: #818cf8; }
.version-badge {
  background: var(--btn-bg);
  color: white;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 5px;
  border-radius: 4px;
  line-height: 1.4;
}

nav { padding: 0 8px; flex: 1; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  transition: background 150ms, color 150ms;
  margin-bottom: 2px;
}
.nav-item:hover { background: var(--btn-bg); color: white; }
.nav-item.active { background: var(--btn-bg); color: white; font-weight: 600; }
.nav-icon { width: 18px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.nav-icon svg { display: block; }

.sidebar-footer {
  padding: 10px 16px 0;
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: #374151;
}
.update-badge {
  display: none;
  margin: 6px 10px 4px;
  padding: 6px 10px;
  background: rgba(99,102,241,0.08);
  border: 1px solid var(--accent);
  border-radius: 6px;
  cursor: pointer;
  text-decoration: none;
}
.update-badge:hover { background: rgba(99,102,241,0.15); }
.update-badge-title { color: var(--accent); font-size: 11px; font-weight: 600; }
.update-badge-sub   { color: var(--accent-2); font-size: 10px; margin-top: 1px; }
.update-instructions {
  display: none;
  margin: 0 10px 6px;
  padding: 8px 10px;
  background: #111;
  border: 1px solid #374151;
  border-top: none;
  border-radius: 0 0 6px 6px;
  color: #9ca3af;
  font-size: 10px;
  line-height: 1.7;
}
.update-inst-code {
  margin: 4px 0;
  padding: 4px 6px;
  background: #1a1a1a;
  border-radius: 4px;
  font-family: monospace;
  font-size: 10px;
  color: #d1d5db;
}
.update-inst-link {
  display: inline-block;
  margin-top: 6px;
  color: var(--accent);
  font-size: 10px;
  text-decoration: none;
}
.update-inst-link:hover { text-decoration: underline; }
.check-update-btn {
  display: block;
  width: calc(100% - 20px);
  margin: 4px 10px 2px;
  padding: 5px 10px;
  background: transparent;
  border: 1px solid #374151;
  border-radius: 6px;
  color: #6b7280;
  font-size: 10px;
  cursor: pointer;
  text-align: left;
}
.check-update-btn:hover { border-color: var(--accent); color: var(--accent); }
.check-update-btn:disabled { opacity: 0.5; cursor: default; }

/* ── CONTENT ── */
.content {
  flex: 1;
  background: var(--body-bg);
  overflow-y: auto;
  padding: 28px;
  min-height: 0;
}
.content-title { font-size: 22px; font-weight: 700; color: white; margin-bottom: 6px; }
.content-desc  { font-size: 13px; color: #6b7280; margin-bottom: 24px; }

/* ── SETTING ROWS ── */
.rows { display: flex; flex-direction: column; gap: 14px; }
.row-card {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.row-text { flex: 1; min-width: 0; }
.row-title { font-size: 13px; font-weight: 600; color: white; margin-bottom: 4px; }
.row-desc  { font-size: 12px; color: #6b7280; line-height: 1.5; }
.row-current { font-size: 11px; color: #9ca3af; margin-top: 4px; }

.row-btn {
  background: linear-gradient(var(--cards-bg), var(--cards-bg)) padding-box,
              linear-gradient(90deg, #4ecdc4, #5890e0, #8060c8, #c05090, #d07040, #c8b030, #90b840) border-box;
  color: var(--accent-2);
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: opacity 150ms, color 150ms;
}
.row-btn:hover { opacity: 0.75; color: white; }
.row-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.switch {
  position: relative;
  width: 44px;
  height: 26px;
  border-radius: 13px;
  border: none;
  cursor: pointer;
  flex-shrink: 0;
  padding: 0;
  transition: background 200ms;
}
.switch-on  { background: var(--btn-bg); }
.switch-off { background: #3f3f46; }
.switch-thumb {
  position: absolute;
  top: 3px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: white;
  transition: left 200ms;
  pointer-events: none;
}
.switch-on  .switch-thumb { left: 21px; }
.switch-off .switch-thumb { left: 3px; }

.status-ok  { color: #4ade80; font-size: 12px; font-weight: 500; }
.status-bad { color: #f87171; font-size: 12px; font-weight: 500; }

/* ── MODEL GRID (Settings tab) ── */
.model-grid-settings { display:flex; flex-direction:column; gap:8px; }
.model-card-s {
  background:var(--cards-bg);
  border:1px solid var(--border);
  border-radius:10px;
  padding:14px 16px;
  display:flex;
  align-items:center;
  gap:12px;
  cursor:pointer;
  transition:border-color 150ms,background 150ms;
}
.model-card-s:hover:not(.selected) { border-color:#52525b; }
.model-card-s.selected { border-color:var(--accent); background:rgba(99,102,241,0.06); cursor:default; }
.model-card-s.not-installed { cursor:pointer; }
.model-dl-bar { height:3px; background:var(--border); border-radius:2px; margin:10px 12px 16px; overflow:hidden; position:relative; }
.model-dl-fill { position:absolute; height:100%; width:40%; background:var(--accent); border-radius:2px; animation:dl-slide 1.5s ease-in-out infinite; }
@keyframes dl-slide { 0%{left:-40%} 100%{left:110%} }
.model-radio-s { width:14px; height:14px; border-radius:50%; border:2px solid #52525b; flex-shrink:0; align-self:flex-start; margin-top:2px; }
.model-radio-s.selected { border-color:var(--accent); background:var(--accent); }
.model-info-s { flex:1; min-width:0; }
.model-name-row-s { display:flex; align-items:center; gap:6px; margin-bottom:3px; flex-wrap:wrap; }
.model-name-s { font-size:13px; font-weight:600; color:white; }
.model-badge-s { font-size:9px; font-weight:700; padding:2px 5px; border-radius:4px; text-transform:uppercase; letter-spacing:0.04em; }
.badge-rec    { background:rgba(99,102,241,0.15); color:var(--accent); }
.badge-inst   { background:rgba(74,222,128,0.12); color:#4ade80; }
.badge-noinst { background:rgba(107,114,128,0.12); color:#6b7280; }
.model-desc-s { font-size:11px; color:#6b7280; }
.model-links-s { display:flex; gap:10px; margin-top:5px; }
.model-link-s { font-size:10px; color:#52525b; background:none; border:none; padding:0; cursor:pointer; font-family:inherit; transition:color 150ms; }
.model-link-s:hover { color:var(--accent); }
.model-size-s { font-size:12px; font-weight:600; color:#6b7280; flex-shrink:0; align-self:flex-start; margin-top:1px; }
.model-card-s.selected .model-size-s { color:var(--accent); }
.picker-box.model-picker { min-width:480px; max-height:520px; padding:8px 10px; }

/* ── PICKER OVERLAY ── */
.picker-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.55);
  z-index: 100;
  align-items: center;
  justify-content: center;
}
.picker-overlay.open { display: flex; }
.picker-box {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 6px 0;
  min-width: 220px;
  max-height: 380px;
  overflow-y: auto;
  box-shadow: 0 20px 40px rgba(0,0,0,0.5);
}
.picker-title {
  font-size: 11px;
  font-weight: 600;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 8px 16px 4px;
}
.picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  font-size: 13px;
  color: #9ca3af;
  cursor: pointer;
  transition: background 100ms;
}
.picker-item:hover { background: var(--hover-bg); color: white; }
.picker-item.selected { color: white; font-weight: 600; }
.picker-dot { color: var(--accent-2); font-size: 10px; width: 10px; flex-shrink: 0; }
.picker-sep { height: 1px; background: var(--border); margin: 4px 0; }
.snd-preview-btn { font-size:10px; color:var(--accent); padding:2px 6px; border-radius:4px; cursor:pointer; flex-shrink:0; }
.snd-preview-btn:hover { color:white; background:rgba(255,255,255,0.08); }

/* ── RECORDER OVERLAY ── */
.recorder-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.65);
  z-index: 200;
  align-items: center;
  justify-content: center;
}
.recorder-overlay.open { display: flex; }
.recorder-box {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px 32px;
  min-width: 300px;
  box-shadow: 0 24px 48px rgba(0,0,0,0.65);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  text-align: center;
}
.recorder-title { font-size: 15px; font-weight: 700; color: white; }
.recorder-hint  { font-size: 12px; color: #6b7280; min-height: 16px; }
.rec-display-wrap {
  background: var(--body-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 36px;
  min-width: 180px;
}
.rec-display {
  font-size: 28px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 1px;
}
.rec-actions { display: flex; gap: 10px; margin-top: 4px; }
.btn-cancel {
  background: #3f3f46;
  color: #9ca3af;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  transition: background 150ms;
}
.btn-cancel:hover { background: #52525b; }
.btn-link {
  background: none;
  border: none;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 0;
}
.btn-link:hover { color: #d1d5db; }

/* ── STATS TAB ── */
.stats-grid { display: flex; gap: 12px; margin-bottom: 14px; }
.stat-card {
  flex: 1; background: var(--cards-bg); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px 16px; text-align: center;
}
.stat-num { font-size: 16px; font-weight: 700; color: var(--accent); line-height: 1.1; margin-bottom: 6px; white-space: nowrap; }
.stat-lbl { font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; }
.stat-sub { font-size: 10px; color: #9ca3af; margin-top: 5px; }
.chart-section {
  background: var(--cards-bg); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px 20px; margin-bottom: 8px;
}
.chart-title { font-size: 11px; font-weight: 600; color: #e5e7eb; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.05em; }
.chart-bars { display: flex; align-items: flex-end; gap: 2px; height: 60px; border-bottom: 1px solid var(--border); }
.chart-col { flex: 1; display: flex; align-items: flex-end; height: 100%; cursor: default; }
.chart-bar { width: 100%; background: var(--accent); opacity: 0.6; border-radius: 2px 2px 0 0; transition: opacity 150ms; }
.chart-col:hover .chart-bar { opacity: 1; }
.chart-x { display: flex; justify-content: space-between; font-size: 10px; color: #d1d5db; margin-top: 6px; }
.stat-filter { display: flex; gap: 4px; margin-bottom: 10px; }
.sf-btn {
  background: transparent; border: 1px solid var(--border); color: #6b7280;
  font-size: 11px; font-weight: 500; padding: 5px 10px; border-radius: 6px; cursor: pointer;
  transition: all 150ms;
}
.sf-btn:hover { background: var(--btn-bg); border-color: var(--btn-bg); color: white; }
.sf-btn.sf-active { background: var(--btn-bg); border-color: var(--btn-bg); color: white; font-weight: 600; }
.sf-refresh { margin-left: auto; font-size: 14px; padding: 4px 8px; }

/* ── TYPING TEST OVERLAY ── */
.tt-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.65);
  z-index: 300; align-items: center; justify-content: center;
}
.tt-overlay.open { display: flex; }
.tt-box {
  background: var(--cards-bg); border: 1px solid var(--border); border-radius: 14px;
  padding: 28px 30px; width: 500px; max-width: 92vw;
  box-shadow: 0 24px 48px rgba(0,0,0,0.65);
  display: flex; flex-direction: column; gap: 14px;
}
.tt-title { font-size: 15px; font-weight: 700; color: white; }
.tt-sub   { font-size: 12px; color: #6b7280; }
.tt-passage {
  font-size: 13px; line-height: 1.8; background: var(--body-bg);
  border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  white-space: pre-wrap; user-select: none; -webkit-user-select: none;
}
.tt-ok  { color: #d1d5db; }
.tt-err { color: #f87171; background: rgba(248,113,113,0.2); border-radius: 2px; }
.tt-cursor { color: white; border-bottom: 2px solid var(--accent); }
.tt-dim { color: #374151; }
.tt-input {
  width: 100%; background: var(--body-bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; font-size: 13px; color: white;
  outline: none; resize: none; font-family: inherit;
  -webkit-user-select: text; user-select: text;
}
.tt-input:focus { border-color: var(--accent); }
.tt-result { font-size: 14px; font-weight: 700; color: var(--accent); text-align: center; }
.tt-actions { display: flex; gap: 10px; justify-content: center; }
.rate-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.rate-tabs { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; flex-shrink: 0; }
.rate-tab { padding: 4px 10px; font-size: 11px; font-weight: 600; cursor: pointer; background: transparent; color: #6b7280; border: none; transition: background 150ms, color 150ms; }
.rate-tab.active { background: var(--btn-bg); color: white; }
.rate-input-wrap { display: flex; align-items: center; flex: 1; border: 1px solid var(--border); border-radius: 6px; background: var(--body-bg); overflow: hidden; }
.rate-input-wrap:focus-within { border-color: var(--accent); }
.rate-pfx { padding: 5px 4px 5px 10px; color: #6b7280; font-size: 13px; }
.rate-inp { flex: 1; background: transparent; border: none; outline: none; color: white; font-size: 13px; padding: 5px 10px 5px 2px; -webkit-user-select: text; user-select: text; min-width: 0; }
</style>
</head>
<body>

<!-- LEFT SIDEBAR -->
<div class="sidebar">
  <div class="sidebar-logo-block">
    <div class="logo-name-row">
      <span class="logo-name">Sypher<span class="logo-name-grad">STT</span></span>
    </div>
    <div style="font-size:12px;color:#9ca3af;margin-top:3px">Speech-to-Text</div>
  </div>
  <nav>
    <div class="nav-item active" data-tab="defaults" onclick="switchTab('defaults')">
      <span class="nav-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg></span> Defaults
    </div>
    <div class="nav-item" data-tab="system" onclick="switchTab('system')">
      <span class="nav-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg></span> Sounds
    </div>
    <div class="nav-item" data-tab="permissions" onclick="switchTab('permissions')">
      <span class="nav-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span> Permissions
    </div>
    <div class="nav-item" data-tab="stats" onclick="switchTab('stats')">
      <span class="nav-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></span> Stats
    </div>
  </nav>
  <div class="sidebar-footer" id="ver-footer">v1.0.0</div>
  <button id="check-btn" class="check-update-btn" onclick="manualCheckForUpdate()">↺ Check for Updates</button>
  <div id="update-badge" class="update-badge" onclick="toggleUpdateInstructions()">
    <div class="update-badge-title">↑ Update Available</div>
    <div class="update-badge-sub" id="update-badge-ver">How to update →</div>
  </div>
  <div id="update-instructions" class="update-instructions">
    <div>1. Quit: menu bar → <strong style="color:#d1d5db">Quit</strong></div>
    <div>2. In Terminal:</div>
    <div class="update-inst-code">cd sypher-stt-macos<br>git pull &amp;&amp; ./run.sh</div>
    <a class="update-inst-link" href="#" onclick="post('open_update_guide',{});return false;">View update guide on GitHub →</a>
  </div>
</div>

<!-- RIGHT CONTENT -->
<div class="content" id="content"></div>

<!-- PICKER OVERLAY -->
<div class="picker-overlay" id="picker-overlay" onclick="maybeClosePicker(event)">
  <div class="picker-box" id="picker-box"></div>
</div>

<!-- RECORDER OVERLAY -->
<div class="recorder-overlay" id="recorder-overlay">
  <div class="recorder-box">
    <div class="recorder-title">🎹 Record Shortcut</div>
    <div class="rec-display-wrap">
      <div class="rec-display" id="rec-display">…</div>
    </div>
    <div class="recorder-hint" id="rec-hint">Press your key combination…</div>
    <div class="rec-actions">
      <button class="btn-cancel" onclick="closeRecorder()">Cancel</button>
      <button class="row-btn" id="rec-confirm" disabled onclick="confirmRecorder()">Use This</button>
    </div>
    <button class="btn-link" id="rec-retry" style="display:none" onclick="retryRecorder()">Try again →</button>
  </div>
</div>

<!-- TYPING TEST OVERLAY -->
<div class="tt-overlay" id="tt-overlay">
  <div class="tt-box">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
      <div class="tt-title">⌨️ Typing Speed Test</div>
      <div id="tt-round-badge" style="font-size:11px;font-weight:600;color:#6b7280;flex-shrink:0"></div>
    </div>
    <div class="tt-sub">Type each passage as accurately as you can. Timer starts on your first keystroke.</div>
    <div class="tt-passage" id="tt-passage"></div>
    <textarea class="tt-input" id="tt-input" rows="2"
      placeholder="Start typing here…"
      oninput="onTypeInput()"
      autocorrect="off" autocapitalize="off" spellcheck="false"></textarea>
    <div class="tt-result" id="tt-result" style="display:none"></div>
    <div class="tt-actions">
      <button class="btn-cancel" onclick="closeTypingTest()">Cancel</button>
      <button class="row-btn" id="tt-next" style="display:none" onclick="_ttNextRound()">Next passage →</button>
      <button class="row-btn" id="tt-save" style="display:none" onclick="saveWpm()">Save speed</button>
    </div>
  </div>
</div>

<script>
let currentTab = 'defaults';
let cfg = {};
let devices = [];
let currentRecCombo = '';
let statsData = {};
let _statFilter = 'week';
let _rateEditing = false;
let _rateVisible = false;

// ── Hotkey display ────────────────────────────────────────────────────────
__SHARED_HOTKEY_JS__

// ── Model catalog ─────────────────────────────────────────────────────────

const MODEL_CATALOG = [
  {id:'tiny.en',        name:'Tiny',        size:'~75 MB',   desc:'Fastest · English-optimized · Best for quick notes'},
  {id:'base.en',        name:'Base',        size:'~142 MB',  desc:'Fast · English-optimized · Good accuracy for everyday dictation', recommended:true},
  {id:'small.en',       name:'Small',       size:'~466 MB',  desc:'Balanced speed and accuracy · English-optimized'},
  {id:'medium.en',      name:'Medium',      size:'~1.5 GB',  desc:'High accuracy · English-optimized · Best for complex or accented speech'},
  {id:'large-v3',       name:'Large v3',    size:'~3.1 GB',  desc:'1,550M params · Highest accuracy available · Slowest'},
  {id:'large-v2',       name:'Large v2',    size:'~3.1 GB',  desc:'1,550M params · Near Large v3 accuracy · Predecessor to v3'},
];

function modelDisplayName(id) {
  const m = MODEL_CATALOG.find(m => m.id === id);
  return m ? m.name + ' — ' + id : id;
}

function renderModelCards(cur, local) {
  return MODEL_CATALOG.map(m => {
    const inst = local.includes(m.id);
    const sel  = m.id === cur;
    const cls  = 'model-card-s' + (sel ? ' selected' : '') + (!inst ? ' not-installed' : '');
    const rCls = 'model-radio-s' + (sel ? ' selected' : '');
    const click = sel ? '' : (inst ? `onclick="selectModel(${JSON.stringify(m.id)})"` : `onclick="promptDownloadModel(${JSON.stringify(m.id)})"`);

    const recBadge  = m.recommended ? `<span class="model-badge-s badge-rec">Recommended</span>` : '';
    const instBadge = inst
      ? `<span class="model-badge-s badge-inst">Installed</span>`
      : `<span class="model-badge-s badge-noinst">Not installed</span>`;
    const folderLink = inst
      ? `<button class="model-link-s" onclick="openModelFolder(event,${JSON.stringify(m.id)})">Show in Finder ↗</button>`
      : '';
    return `<div class="${cls}" ${click}>
      <div class="${rCls}"></div>
      <div class="model-info-s">
        <div class="model-name-row-s">
          <span class="model-name-s">${esc(m.name)}</span>${recBadge}${instBadge}
        </div>
        <div class="model-desc-s">${esc(m.desc)}</div>
        <div class="model-links-s">
          <button class="model-link-s" onclick="openModelHF(event,${JSON.stringify(m.id)})">HuggingFace ↗</button>
          ${folderLink}
        </div>
      </div>
      <span class="model-size-s">${esc(m.size)}</span>
    </div>`;
  }).join('');
}

function selectModel(id) {
  document.getElementById('picker-overlay').classList.remove('open');
  post('picked', {type:'model', value:id});
}

function openModelHF(e, id) {
  e.stopPropagation();
  post('open_model_hf', {id});
}

function openModelFolder(e, id) {
  e.stopPropagation();
  post('open_model_folder', {id});
}

function showModelPicker(cur, local) {
  const box = document.getElementById('picker-box');
  box.className = 'picker-box model-picker';
  box.dataset.type = 'model';
  box.innerHTML = '<div class="picker-title">Choose Faster Whisper Model</div>' + renderModelCards(cur, local);
  document.getElementById('picker-overlay').classList.add('open');
}

function promptDownloadModel(id) {
  const m = MODEL_CATALOG.find(x => x.id === id);
  if (!m) return;
  const box = document.getElementById('picker-box');
  box.innerHTML = `
    <div class="picker-title">Download ${esc(m.name)}?</div>
    <div style="padding:6px 12px 2px;font-size:12px;color:#9ca3af">${esc(m.size)} · ${esc(m.desc)}</div>
    <div style="padding:4px 12px 14px;font-size:11px;color:#6b7280">Saved locally — requires an internet connection.</div>
    <div style="display:flex;gap:8px;padding:0 12px 12px">
      <button class="row-btn" style="flex:1" onclick="startModelDownload('${esc(id)}')">Download</button>
      <button class="row-btn" style="flex:1;background:none;border:1px solid #3f3f46;color:#9ca3af"
              onclick="document.getElementById('picker-overlay').classList.remove('open')">Cancel</button>
    </div>`;
}

function startModelDownload(id) {
  const m = MODEL_CATALOG.find(x => x.id === id);
  const box = document.getElementById('picker-box');
  box.innerHTML = `
    <div class="picker-title">Downloading ${esc(m ? m.name : id)}…</div>
    <div style="padding:10px 12px 4px;font-size:12px;color:#9ca3af;text-align:center">
      This may take a few minutes. Please keep the settings window open.
    </div>
    <div class="model-dl-bar"><div class="model-dl-fill"></div></div>`;
  post('download_model', {id});
}

function modelDownloadDone(id, local) {
  document.getElementById('picker-overlay').classList.remove('open');
  post('picked', {type: 'model', value: id});
}

function modelDownloadError(id, msg) {
  const m = MODEL_CATALOG.find(x => x.id === id);
  const box = document.getElementById('picker-box');
  box.innerHTML = `
    <div class="picker-title">Download Failed</div>
    <div style="padding:10px 12px 6px;font-size:12px;color:#f87171;text-align:center">${esc(msg)}</div>
    <div style="padding:0 12px 14px;text-align:center">
      <button class="row-btn" onclick="document.getElementById('picker-overlay').classList.remove('open')">Close</button>
    </div>`;
}

// ── Render tabs ───────────────────────────────────────────────────────────

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.nav-item').forEach(el =>
    el.classList.toggle('active', el.dataset.tab === tab));
  if (tab === 'stats') post('get_stats', {});
  renderTab();
}

function renderTab() {
  const c = document.getElementById('content');
  if      (currentTab === 'defaults')    renderDefaults(c);
  else if (currentTab === 'system')      renderSystem(c);
  else if (currentTab === 'permissions') renderPermissions(c);
  else if (currentTab === 'stats')       renderStats(c);
}

function renderDefaults(c) {
  const hotkey  = hotkeyDisplay(cfg.hotkey || 'f8');
  const model   = cfg.model    || 'base.en';
  const micName = cfg.mic_name || 'System Default';
  const local   = cfg.local_models || [];

  c.innerHTML = `
    <div class="content-title">Settings</div>
    <div class="content-desc">Configure default settings and permissions.</div>
    <div class="rows">
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Set default keyboard shortcut</div>
          <div class="row-desc">Choose your preferred hotkey for SypherSTT.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(hotkey)}</span></div>
        </div>
        <button class="row-btn" onclick="openPicker('hotkey')">Change shortcut</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Set default microphone</div>
          <div class="row-desc">Choose your preferred microphone for SypherSTT to capture your voice.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(micName)}</span></div>
        </div>
        <button class="row-btn" onclick="openPicker('mic')">Select microphone</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Faster Whisper model</div>
          <div class="row-desc">Local speech recognition powered by Faster Whisper (CTranslate2). Runs fully on-device — no internet required. Larger models are more accurate but slower.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(modelDisplayName(model))}</span></div>
        </div>
        <button class="row-btn" onclick="openPicker('model')">Change model</button>
      </div>
    </div>`;
}

function renderSystem(c) {
  const on = cfg.sound_feedback !== false;
  const startSnd = cfg.sound_start || 'Ping';
  const stopSnd  = cfg.sound_stop  || 'Pop';
  const errSnd   = cfg.sound_error || 'Basso';
  const hdr = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
      <div class="content-title" style="margin-bottom:0">Sounds</div>
      <button class="switch ${on?'switch-on':'switch-off'}" onclick="toggleSound()" role="switch" aria-checked="${on}">
        <span class="switch-thumb"></span>
      </button>
    </div>`;
  if (!on) {
    c.innerHTML = `${hdr}
      <div class="content-desc">Sound feedback is disabled.</div>`;
    return;
  }
  c.innerHTML = `${hdr}
    <div class="content-desc">Audio feedback played when recording starts, stops, or fails.</div>
    <div class="rows" style="margin-top:8px">
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Start recording</div>
          <div class="row-desc">Played when recording begins.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(startSnd)}</span></div>
        </div>
        <button class="row-btn" onclick="openSoundPicker('start')">Change</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Stop recording</div>
          <div class="row-desc">Played when recording ends.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(stopSnd)}</span></div>
        </div>
        <button class="row-btn" onclick="openSoundPicker('stop')">Change</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Error</div>
          <div class="row-desc">Played on recording or transcription errors.</div>
          <div class="row-current">Current: <span style="color:var(--accent)">${esc(errSnd)}</span></div>
        </div>
        <button class="row-btn" onclick="openSoundPicker('error')">Change</button>
      </div>
    </div>`;
}

function renderPermissions(c) {
  const axOk  = cfg.ax_granted;
  const axTxt = axOk
    ? '<span class="status-ok">✓ Granted</span>'
    : '<span class="status-bad">✗ Not granted</span>';
  const micOk  = cfg.mic_granted;
  const micTxt = micOk
    ? '<span class="status-ok">✓ Granted</span>'
    : '<span class="status-bad">✗ Not granted</span>';
  const appName = esc(cfg.proc_name || 'SypherSTT');
  c.innerHTML = `
    <div class="content-title">Settings</div>
    <div class="content-desc">Accessibility and microphone access required by SypherSTT.</div>
    <div class="rows">
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Accessibility access</div>
          <div class="row-desc">Required to detect your hotkey while you type in other apps.
            In System Settings → Privacy &amp; Security → Accessibility, look for
            <strong style="color:white">${appName}</strong>.</div>
          <div class="row-current">${axTxt}</div>
        </div>
        <button class="row-btn" onclick="post('open_ax',{})">Open System Settings</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Microphone access</div>
          <div class="row-desc">Required to capture audio from your microphone.
            In System Settings → Privacy &amp; Security → Microphone, look for
            <strong style="color:white">${appName}</strong>.</div>
          <div class="row-current">${micTxt}</div>
        </div>
        <button class="row-btn" onclick="post('open_mic',{})">Open System Settings</button>
      </div>
    </div>`;
}

// ── Stats tab ────────────────────────────────────────────────────────────

function renderStats(c) {
  const enabled = cfg.record_stats !== false;
  const hdr = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
      <div class="content-title" style="margin-bottom:0">Stats</div>
      <button class="switch ${enabled?'switch-on':'switch-off'}" onclick="toggleRecordStats()" role="switch" aria-checked="${enabled}">
        <span class="switch-thumb"></span>
      </button>
    </div>`;

  if (!enabled) {
    c.innerHTML = `${hdr}
      <div class="content-desc">Usage tracking is disabled.</div>`;
    return;
  }

  if (!statsData.days) {
    post('get_stats', {});
    c.innerHTML = `${hdr}
      <div class="content-desc">Loading…</div>`;
    return;
  }
  const now  = new Date();
  const mn   = now.toLocaleString('en-US', {month: 'long'});
  const days = statsData.days;
  const mp   = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0');
  const wkCut = _filterCutoff('week', now);
  const q3Cut = _filterCutoff('3months', now);
  let tw = 0, tc = 0, ta = 0;
  Object.entries(days).forEach(([dt, d]) => {
    const inc = _statFilter === 'month'   ? dt.startsWith(mp)
              : _statFilter === 'week'    ? dt >= wkCut
              : _statFilter === '3months' ? dt >= q3Cut
              : true;
    if (inc) { tw += d.words||0; tc += d.chars||0; ta += d.audio_seconds||0; }
  });
  const wpm = statsData.typing_wpm || 0;
  const savedSec = wpm > 0 ? Math.max(0, (tw / wpm * 60) - ta) : -1;
  const rateMode   = statsData.rate_mode  || 'hourly';
  const rateVal    = statsData.rate_value || 0;
  const hourlyRate = rateMode === 'salary' ? rateVal / 2080 : rateVal;
  const valueSaved = (savedSec >= 0 && hourlyRate > 0) ? (savedSec / 3600) * hourlyRate : -1;
  const chart = _buildChartFiltered(days, _statFilter);
  const maxW  = Math.max(...chart.map(d => d.w), 1);
  const filterLabel = {week:'This week', month:mn+' '+now.getFullYear(), '3months':'Last 3 months', all:'All time'}[_statFilter];
  const chartTitle  = {week:'Daily words — this week', month:'Daily words — this month', '3months':'Weekly words — last 3 months', all:'Weekly words — all time'}[_statFilter];
  const FILTERS = [{f:'week',l:'Week'},{f:'month',l:'Month'},{f:'3months',l:'3 Months'},{f:'all',l:'All Time'}];
  const _eyeBtn = on => '<button onclick="toggleRateVisibility()" style="background:none;border:none;cursor:pointer;padding:0 2px;margin-left:3px;color:#9ca3af;vertical-align:middle;line-height:1">'
    + (on
      ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
      : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>')
    + '</button>';
  let rateDesc;
  if (rateVal > 0 && !_rateEditing) {
    if (_rateVisible) {
      const dispVal = rateMode === 'salary'
        ? '$' + rateVal.toLocaleString(undefined, {maximumFractionDigits: 0}) + ' annual <span style="color:#6b7280;margin:0 5px">·</span> <span style="color:var(--accent-2)">$' + hourlyRate.toFixed(2) + '/hr effective</span>'
        : '<span style="color:var(--accent-2)">$' + rateVal.toFixed(2) + '/hr</span>';
      const salarySub = rateMode === 'salary'
        ? '<br><span style="color:#6b7280;font-size:11px">Salary ÷ 2,080 hrs (52 weeks × 40 hrs/week)</span>'
        : '';
      rateDesc = dispVal + _eyeBtn(true) + salarySub;
    } else {
      rateDesc = '$******' + _eyeBtn(false);
    }
  } else {
    rateDesc = 'Enter your rate to see the estimated dollar value of time saved.';
  }
  c.innerHTML = `
    ${hdr}
    <div class="content-desc">${filterLabel} — transcription activity.</div>
    <div class="stat-filter">
      ${FILTERS.map(({f,l}) => `<button class="sf-btn${_statFilter===f?' sf-active':''}" onclick="setStatFilter('${f}')">${l}</button>`).join('')}
      <button class="sf-btn sf-refresh" onclick="refreshStats()" title="Refresh stats">↻</button>
    </div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-num">${tw.toLocaleString()}</div>
        <div class="stat-lbl">Words transcribed</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">${_fmtD(ta)}</div>
        <div class="stat-lbl">Audio transcribed</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">${savedSec >= 0 ? _fmtHours(savedSec) : '—'}</div>
        <div class="stat-lbl">Est. time saved</div>
      </div>
      ${valueSaved >= 0 ? `
      <div class="stat-card">
        <div class="stat-num">${_fmtMoney(valueSaved)}</div>
        <div class="stat-lbl">Est. value saved</div>
      </div>` : ''}
    </div>
    <div class="chart-section">
      <div class="chart-title">${chartTitle}</div>
      <div class="chart-bars">
        ${chart.map(d => `<div class="chart-col" title="${esc(d.label)}: ${d.w} word${d.w!==1?'s':''}">
          <div class="chart-bar" style="height:${d.w>0?Math.max(3,Math.round(d.w/maxW*100)):0}%"></div>
        </div>`).join('')}
      </div>
      <div class="chart-x">
        <span>${esc(chart[0]?.label||'')}</span>
        <span>${esc(chart[Math.floor(chart.length/2)]?.label||'')}</span>
        <span>${esc(chart[chart.length-1]?.label||'')}</span>
      </div>
    </div>
    <div class="rows">
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Typing speed</div>
          <div class="row-desc">${wpm > 0
            ? `Your speed: <strong style="color:var(--accent)">${wpm} WPM</strong> — used to estimate time saved above.`
            : 'Take the speed test to enable time saved estimates.'}</div>
        </div>
        <button class="row-btn" onclick="openTypingTest()">Take test</button>
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Earnings rate</div>
          <div class="row-desc">${rateDesc}</div>
          ${rateVal > 0 && !_rateEditing ? '' : `
          <div class="rate-row">
            <div class="rate-tabs">
              <button class="rate-tab ${rateMode==='hourly'?'active':''}" onclick="setRateMode('hourly')">Hourly</button>
              <button class="rate-tab ${rateMode==='salary'?'active':''}" onclick="setRateMode('salary')">Salary</button>
            </div>
            <div class="rate-input-wrap">
              <span class="rate-pfx">$</span>
              <input class="rate-inp" id="rate-inp" type="number" min="0" step="1"
                     placeholder="${rateMode==='hourly' ? 'e.g. 75' : 'e.g. 100000'}"
                     value=""
                     onkeydown="if(event.key==='Enter')saveRate()" />
            </div>
            <button class="row-btn" onclick="saveRate()">Save</button>
          </div>`}
        </div>
        ${rateVal > 0 && !_rateEditing ? `<button class="row-btn" onclick="editRate()">Edit</button>` : ''}
      </div>
      <div class="row-card">
        <div class="row-text">
          <div class="row-title">Clear usage stats</div>
          <div class="row-desc">Removes word counts, character counts, and audio durations. Typing speed is kept.
            &nbsp;<button class="btn-link" style="display:inline;font-size:11px;color:var(--accent)" onclick="post('open_log',{})">View log file →</button></div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="row-btn" onclick="post('confirm_clear_log',{})">Clear log</button>
          <button class="row-btn" onclick="confirmClearStats()">Clear stats</button>
        </div>
      </div>
    </div>`;
}

function _buildChart(days, n) {
  const out = [], now = new Date();
  for (let i = n-1; i >= 0; i--) {
    const d = new Date(now); d.setDate(d.getDate()-i);
    const key = d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    out.push({label: (d.getMonth()+1)+'/'+d.getDate(), w: (days[key]||{}).words||0});
  }
  return out;
}

function _fmtN(n) { return n >= 1000 ? (n/1000).toFixed(1)+'k' : String(n); }

function _fmtD(sec) {
  sec = Math.round(sec);
  if (sec < 60) return sec+'s';
  const m = Math.floor(sec/60), s = sec%60;
  if (m < 60) return m+'m'+(s ? ' '+s+'s' : '');
  return Math.floor(m/60)+'h'+(m%60 ? ' '+(m%60)+'m' : '');
}

function _fmtHours(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return m + 'm' + (s ? ' ' + s + 's' : '');
  return Math.floor(m / 60) + 'h' + (m % 60 ? ' ' + (m % 60) + 'm' : '');
}

function _fmtMoney(v) {
  if (v >= 1000) return '$' + (v / 1000).toFixed(1) + 'k';
  return '$' + v.toFixed(2);
}

function _isoDate(d) {
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
}

function _filterCutoff(filter, now) {
  const d = new Date(now);
  if (filter === 'week')    d.setDate(d.getDate() - 6);
  if (filter === '3months') d.setDate(d.getDate() - 89);
  return _isoDate(d);
}

function _buildChartWeekly(days, totalDays) {
  const now = new Date(), weeks = Math.ceil(totalDays / 7), out = [];
  for (let i = weeks - 1; i >= 0; i--) {
    const wEnd = new Date(now); wEnd.setDate(wEnd.getDate() - i * 7);
    const wStart = new Date(wEnd); wStart.setDate(wStart.getDate() - 6);
    let w = 0;
    for (let j = 0; j < 7; j++) {
      const day = new Date(wStart); day.setDate(day.getDate() + j);
      w += (days[_isoDate(day)] || {}).words || 0;
    }
    out.push({label: (wStart.getMonth()+1)+'/'+wStart.getDate(), w});
  }
  return out;
}

function _buildChartFiltered(days, filter) {
  const now = new Date();
  if (filter === 'week')    return _buildChart(days, 7);
  if (filter === 'month')   return _buildChart(days, now.getDate());
  if (filter === '3months') return _buildChartWeekly(days, 91);
  // 'all': daily if ≤30 days of data, weekly otherwise
  const keys = Object.keys(days).sort();
  if (!keys.length) return _buildChart(days, 7);
  const diff = Math.ceil((now - new Date(keys[0]+'T00:00:00')) / 86400000) + 1;
  return diff <= 30 ? _buildChart(days, diff) : _buildChartWeekly(days, diff);
}

function setStatFilter(f) { _statFilter = f; renderTab(); }

function refreshStats() { statsData = {}; post('get_stats', {}); }

function confirmClearStats() { post('confirm_clear_stats', {}); }

// ── Update checking ────────────────────────────────────────────────────────

function _resetCheckBtn() {
  const btn = document.getElementById('check-btn');
  if (btn) { btn.disabled = false; btn.textContent = '↺ Check for Updates'; }
}

function showUpdateBadge(v) {
  // Reset button, then show the update badge
  _resetCheckBtn();
  const badge = document.getElementById('update-badge');
  const sub   = document.getElementById('update-badge-ver');
  if (badge && sub) { sub.textContent = v + ' available'; badge.style.display = 'block'; }
}

function showUpToDate() {
  const btn = document.getElementById('check-btn');
  if (btn) { btn.disabled = false; btn.textContent = '✓ Up to date'; }
  setTimeout(_resetCheckBtn, 3000);
}

function showCheckError() {
  const btn = document.getElementById('check-btn');
  if (btn) { btn.disabled = false; btn.textContent = 'Check failed — try again'; }
  setTimeout(_resetCheckBtn, 4000);
}

function toggleUpdateInstructions() {
  const inst = document.getElementById('update-instructions');
  if (inst) inst.style.display = inst.style.display === 'block' ? 'none' : 'block';
}

function manualCheckForUpdate() {
  const btn = document.getElementById('check-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  post('check_for_update', {});
  // Fallback reset if Python never responds (e.g. hard network timeout)
  setTimeout(() => { if (btn && btn.disabled) _resetCheckBtn(); }, 15000);
}

function updateStats(j) {
  statsData = j;
  if (currentTab === 'stats') renderTab();
}

// ── Typing speed test ────────────────────────────────────────────────────
const _TT_PASSAGES = __TT_PASSAGES__;
let _ttRound = 0, _ttScores = [], _ttStart = null, _ttWpm = 0;

function openTypingTest() {
  _ttRound = 0; _ttScores = [];
  _ttLoadRound();
  document.getElementById('tt-overlay').classList.add('open');
}

function _ttLoadRound() {
  _ttStart = null; _ttWpm = 0;
  document.getElementById('tt-round-badge').textContent = _TT_PASSAGES[_ttRound].round;
  const inp = document.getElementById('tt-input');
  inp.value = ''; inp.disabled = false;
  document.getElementById('tt-result').style.display = 'none';
  document.getElementById('tt-next').style.display   = 'none';
  document.getElementById('tt-save').style.display   = 'none';
  _ttRender('');
  setTimeout(() => inp.focus(), 60);
}

function _ttNextRound() {
  _ttRound++;
  _ttLoadRound();
}

function closeTypingTest() {
  document.getElementById('tt-overlay').classList.remove('open');
}

function _ttRender(typed) {
  const TT = _TT_PASSAGES[_ttRound].text;
  let h = '';
  for (let i = 0; i < TT.length; i++) {
    const raw = TT[i], ch = esc(raw);
    if      (i < typed.length)   h += `<span class="${typed[i]===raw?'tt-ok':'tt-err'}">${ch}</span>`;
    else if (i === typed.length) h += `<span class="tt-cursor">${ch}</span>`;
    else                         h += `<span class="tt-dim">${ch}</span>`;
  }
  document.getElementById('tt-passage').innerHTML = h;
}

function onTypeInput() {
  const TT = _TT_PASSAGES[_ttRound].text;
  const inp = document.getElementById('tt-input');
  const t = inp.value;
  if (!_ttStart && t.length > 0) _ttStart = Date.now();
  _ttRender(t);
  if (_ttStart && t.length >= TT.length) {
    const elapsed = (Date.now() - _ttStart) / 60000;
    let correct = 0;
    for (let i = 0; i < TT.length; i++) if (t[i] === TT[i]) correct++;
    _ttWpm = Math.min(500, Math.max(1, Math.round(correct / 5 / elapsed)));
    _ttScores.push(_ttWpm);
    inp.disabled = true;
    const res = document.getElementById('tt-result');
    if (_ttRound < _TT_PASSAGES.length - 1) {
      res.textContent = 'Round ' + (_ttRound + 1) + ': ' + _ttWpm + ' WPM';
      res.style.display = 'block';
      document.getElementById('tt-next').style.display = 'inline-block';
    } else {
      const avg = Math.round(_ttScores.reduce((a, b) => a + b, 0) / _ttScores.length);
      _ttWpm = avg;
      res.textContent = _ttScores.map((s, i) => 'R' + (i + 1) + ': ' + s).join(' · ') + '  —  Avg: ' + avg + ' WPM';
      res.style.display = 'block';
      document.getElementById('tt-save').style.display = 'inline-block';
    }
  }
}

function saveWpm() {
  if (_ttWpm > 0) { post('save_wpm', {wpm: _ttWpm}); statsData.typing_wpm = _ttWpm; }
  closeTypingTest();
  if (currentTab === 'stats') renderTab();
}

function setRateMode(mode) {
  statsData.rate_mode = mode;
  // Switching tabs always gives a clean empty field — no value carried over
  if (currentTab === 'stats') renderTab();
}

function editRate() {
  _rateEditing = true;
  _rateVisible = false;
  if (currentTab === 'stats') renderTab();
}

function toggleRateVisibility() {
  _rateVisible = !_rateVisible;
  if (currentTab === 'stats') renderTab();
}

function saveRate() {
  const inp = document.getElementById('rate-inp');
  if (!inp) return;
  const val = parseFloat(inp.value) || 0;
  const mode = statsData.rate_mode || 'hourly';
  if (val > 0) {
    statsData.rate_value = val;
    statsData.rate_mode  = mode;
    post('save_rate', {mode, value: val});
  }
  _rateEditing = false;
  _rateVisible = false;
  if (currentTab === 'stats') renderTab();
}

// ── Actions ──────────────────────────────────────────────────────────────

function toggleSound() {
  cfg.sound_feedback = !(cfg.sound_feedback !== false);
  post('set_sound', {value: cfg.sound_feedback});
  renderSystem(document.getElementById('content'));
}

function openSoundPicker(evt) {
  const SOUNDS = ['Basso','Blow','Bottle','Frog','Funk','Glass','Hero','Morse','Ping','Pop','Purr','Sosumi','Submarine','Tink'];
  const cur = cfg['sound_' + evt] || '';
  const titles = {start:'Start Sound', stop:'Stop Sound', error:'Error Sound'};
  const box = document.getElementById('picker-box');
  box.dataset.type = 'sound_' + evt;
  box.innerHTML = `<div class="picker-title">${titles[evt] || 'Choose Sound'}</div>` +
    SOUNDS.map(s => `
      <div class="picker-item ${s===cur?'selected':''}" onclick="pickItem(this.dataset.v)" data-v="${esc(s)}">
        <span class="picker-dot">${s===cur?'●':''}</span>
        <span style="flex:1">${esc(s)}</span>
        <span class="snd-preview-btn" data-snd="${esc(s)}" onclick="previewSound(event,this.dataset.snd)">▶</span>
      </div>`).join('') +
    '<div class="picker-sep"></div>';
  document.getElementById('picker-overlay').classList.add('open');
}

function previewSound(e, name) {
  e.stopPropagation();
  post('preview_sound', {sound: name});
}

function toggleRecordStats() {
  cfg.record_stats = !(cfg.record_stats !== false);
  post('set_record_stats', {value: cfg.record_stats});
  renderStats(document.getElementById('content'));
}

function openPicker(type) {
  post('open_picker', {type: type});
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Hotkey picker ─────────────────────────────────────────────────────────

function showHotkeyPicker(options, current) {
  const box = document.getElementById('picker-box');
  box.dataset.type = 'hotkey';
  box.innerHTML = '<div class="picker-title">Keyboard Shortcut</div>' +
    options.map(opt => `
      <div class="picker-item ${opt.value===current?'selected':''}"
           onclick="pickItem(this.dataset.v)" data-v="${esc(opt.value)}">
        <span class="picker-dot">${opt.value===current?'●':''}</span>
        ${esc(opt.label)}
      </div>`).join('') +
    '<div class="picker-sep"></div>' +
    '<div class="picker-item" onclick="openRecorder()">' +
    '<span class="picker-dot"></span>🎹 Record custom…</div>';
  document.getElementById('picker-overlay').classList.add('open');
}

// ── Generic picker ────────────────────────────────────────────────────────

function showPicker(title, options, current) {
  const box = document.getElementById('picker-box');
  box.innerHTML = `<div class="picker-title">${esc(title)}</div>` +
    options.map(opt => `
      <div class="picker-item ${opt===current?'selected':''}"
           onclick="pickItem(this.dataset.v)" data-v="${esc(opt)}">
        <span class="picker-dot">${opt===current?'●':''}</span>
        ${esc(opt)}
      </div>`).join('') +
    '<div class="picker-sep"></div>';
  document.getElementById('picker-overlay').classList.add('open');
  box.dataset.type = title;
}

function maybeClosePicker(e) {
  if (e.target.id === 'picker-overlay')
    document.getElementById('picker-overlay').classList.remove('open');
}

function pickItem(value) {
  const box = document.getElementById('picker-box');
  document.getElementById('picker-overlay').classList.remove('open');
  post('picked', {type: box.dataset.type, value: value});
}

// ── Key recorder ──────────────────────────────────────────────────────────
// Keys captured via JS keydown events — no pynput/CGEventTap needed.

let _recKeyHandler = null;

const _REC_KEY_MAP = {
  'F1':'f1','F2':'f2','F3':'f3','F4':'f4','F5':'f5','F6':'f6',
  'F7':'f7','F8':'f8','F9':'f9','F10':'f10','F11':'f11','F12':'f12',
  ' ':'space','Enter':'enter','Tab':'tab','Escape':'esc',
  'Delete':'delete','Backspace':'backspace','CapsLock':'caps_lock',
  'Home':'home','End':'end','PageUp':'page_up','PageDown':'page_down',
};
const _REC_MOD_KEYS = new Set([
  'Control','Alt','Shift','Meta','CapsLock','NumLock','ScrollLock'
]);

function _onRecKey(e) {
  if (_REC_MOD_KEYS.has(e.key)) return;
  e.preventDefault();
  e.stopPropagation();
  const mods = [];
  if (e.ctrlKey)  mods.push('ctrl');
  if (e.altKey)   mods.push('option');
  if (e.shiftKey) mods.push('shift');
  if (e.metaKey)  mods.push('cmd');
  let main = _REC_KEY_MAP[e.key];
  if (!main && e.key.length === 1) main = e.key.toLowerCase();
  if (!main) return; // unrecognised key — keep listening
  if (main === 'esc' && mods.length === 0) { _stopRecKey(); closeRecorder(); return; }
  _stopRecKey();
  post('recorder_result', {combo: [...mods, main].join('+')});
}

function _stopRecKey() {
  if (_recKeyHandler) {
    document.removeEventListener('keydown', _recKeyHandler, true);
    _recKeyHandler = null;
  }
}

function _startRecKey() {
  _stopRecKey();
  _recKeyHandler = _onRecKey;
  document.addEventListener('keydown', _recKeyHandler, true);
}

function openRecorder() {
  currentRecCombo = '';
  document.getElementById('rec-display').textContent = '…';
  document.getElementById('rec-hint').textContent = 'Press any key combo — Esc or Cancel to dismiss';
  document.getElementById('rec-confirm').disabled = true;
  document.getElementById('rec-retry').style.display = 'none';
  document.getElementById('picker-overlay').classList.remove('open');
  document.getElementById('recorder-overlay').classList.add('open');
  post('start_recorder', {});
  _startRecKey();
}

function recorderResult(combo) {
  currentRecCombo = combo;
  const valid = isValidHotkey(combo);
  document.getElementById('rec-display').textContent = combo ? hotkeyDisplay(combo) : '…';
  if (valid) {
    document.getElementById('rec-hint').textContent = 'Combo captured! Click "Use This" to save.';
    document.getElementById('rec-confirm').disabled = false;
    document.getElementById('rec-retry').style.display = 'none';
  } else if (combo) {
    document.getElementById('rec-hint').textContent = 'Add a modifier (⌃ ⌥ ⇧ ⌘) and try again.';
    document.getElementById('rec-confirm').disabled = true;
    document.getElementById('rec-retry').style.display = 'block';
  }
}

function confirmRecorder() {
  _stopRecKey();
  const combo = currentRecCombo;
  document.getElementById('recorder-overlay').classList.remove('open');
  if (combo) post('picked', {type: 'hotkey', value: combo});
}

function closeRecorder() {
  _stopRecKey();
  document.getElementById('recorder-overlay').classList.remove('open');
  post('cancel_recorder', {});
}

function retryRecorder() {
  currentRecCombo = '';
  document.getElementById('rec-display').textContent = '…';
  document.getElementById('rec-hint').textContent = 'Press any key combo — Esc or Cancel to dismiss';
  document.getElementById('rec-confirm').disabled = true;
  document.getElementById('rec-retry').style.display = 'none';
  _startRecKey();
}

// ── Init (called from Python after window loads) ──────────────────────────

function init(configJson, devicesJson, version) {
  cfg = configJson;
  devices = devicesJson;
  document.getElementById('ver-footer').textContent = 'v' + version;
  renderTab();
}

function updateConfig(configJson) {
  cfg = configJson;
  renderTab();
}

function post(action, data) {
  window.webkit.messageHandlers.settings.postMessage(JSON.stringify({action, ...data}));
}
</script>
</body></html>
"""


# ── PyObjC WKWebView Application ──────────────────────────────────────────────

class SettingsWindow:
    """Settings window using PyObjC NSWindow + WKWebView."""

    def __init__(self):
        self._cfg = _load_config()
        self._devices = _input_devices()
        self._app = None
        self._window = None
        self._webview = None
        self._ready = False
        self._ns_refs: list = []  # strong refs — prevent ARC from releasing NSObjects
        self._recorder_stop: Optional[threading.Event] = None
        self._rec_dispatcher = None
        self._recording = False  # True while key recorder is active
        self._js_queue: queue.SimpleQueue = queue.SimpleQueue()  # thread-safe JS dispatch
        self._NSTimer = None          # stored in run() for use in _on_loaded
        self._js_poll_timer = None    # strong ref to repeating NSTimer
        self._downloading = False

    def run(self):
        from Foundation import NSObject, NSMakeRect, NSTimer
        from AppKit import (
            NSApplication, NSWindow, NSScreen,
            NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, NSColor,
        )
        from WebKit import WKWebView, WKWebViewConfiguration

        settings_ref = self

        # ── Navigation delegate — detect page load completion ─────────────
        class NavDelegate(NSObject):
            def webView_didFinishNavigation_(self, wv, nav):
                settings_ref._on_loaded()

        # ── Script message handler — receives post() calls from JS ────────
        class MsgHandler(NSObject):
            def userContentController_didReceiveScriptMessage_(self, ctrl, msg):
                try:
                    body = json.loads(str(msg.body()))
                    settings_ref._handle(body.get("action", ""), body)
                except Exception:
                    pass

        # ── Window delegate — block close/quit while key recorder is active ─
        class WinDelegate(NSObject):
            def windowShouldClose_(self, window):
                return not settings_ref._recording

        # ── Cross-thread dispatcher: pynput thread → main thread JS call ──
        class RecorderDispatcher(NSObject):
            def deliverCombo_(self, combo_str):
                settings_ref._on_recorder_result(str(combo_str) if combo_str else "")

        rec_dispatcher = RecorderDispatcher.alloc().init()
        self._ns_refs.append(rec_dispatcher)
        self._rec_dispatcher = rec_dispatcher

        # ── Cross-thread JS dispatcher: NSTimer polls a SimpleQueue ──────────
        # Replaces performSelectorOnMainThread which is unreliable on Python 3.13.
        class JSPoller(NSObject):
            def poll_(self, timer):
                try:
                    while True:
                        script = settings_ref._js_queue.get_nowait()
                        settings_ref._js(script)
                except queue.Empty:
                    pass

        js_poller = JSPoller.alloc().init()
        self._ns_refs.append(js_poller)
        self._js_poller = js_poller
        self._NSTimer = NSTimer

        msg_handler = MsgHandler.alloc().init()
        self._ns_refs.append(msg_handler)

        nav_delegate = NavDelegate.alloc().init()
        self._ns_refs.append(nav_delegate)

        # ── NSApplication ─────────────────────────────────────────────────
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)
        self._app = app

        # ── Window ────────────────────────────────────────────────────────
        W, H = 960, 740
        screen = NSScreen.mainScreen()
        sf = screen.frame()
        x = (sf.size.width - W) / 2
        y = (sf.size.height - H) / 2.5
        frame = NSMakeRect(x, y, W, H)

        style = (NSWindowStyleMaskTitled |
                 NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        window.setTitle_("SypherSTT — Settings")
        window.setReleasedWhenClosed_(False)

        try:
            from AppKit import NSAppearance
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            window.setAppearance_(dark)
        except Exception:
            pass

        self._window = window

        # ── WKWebView ─────────────────────────────────────────────────────
        config = WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(
            msg_handler, "settings"
        )
        cv = window.contentView()
        cv_frame = cv.frame()
        webview = WKWebView.alloc().initWithFrame_configuration_(cv_frame, config)
        webview.setAutoresizingMask_(18)

        webview.setNavigationDelegate_(nav_delegate)

        cv.addSubview_(webview)
        self._webview = webview

        _html = (
            _HTML
            .replace("__TT_PASSAGES__", json.dumps(_TT_PASSAGES))
            .replace("__SHARED_HOTKEY_JS__", _SHARED_HOTKEY_JS)
        )
        webview.loadHTMLString_baseURL_(_html, None)

        # ── App delegate ──────────────────────────────────────────────────
        class AppDelegate(NSObject):
            def applicationDidFinishLaunching_(self, notif):
                settings_ref._window.makeKeyAndOrderFront_(None)
                settings_ref._window.center()
                settings_ref._app.activateIgnoringOtherApps_(True)

            def applicationShouldTerminate_(self, app):
                # Block Cmd+Q while key recorder is active (NSTerminateCancel=0)
                return 0 if settings_ref._recording else 1

            def applicationShouldTerminateAfterLastWindowClosed_(self, app):
                return True

        delegate = AppDelegate.alloc().init()
        self._ns_refs.append(delegate)
        app.setDelegate_(delegate)

        # Attach window delegate (must be set after window is created)
        win_delegate = WinDelegate.alloc().init()
        self._ns_refs.append(win_delegate)
        self._window.setDelegate_(win_delegate)

        app.run()

    def _on_loaded(self):
        """Called by NavDelegate when HTML finishes loading."""
        cfg_copy = dict(self._cfg)

        # Resolve mic name
        dev_idx = cfg_copy.get("audio_device")
        mic_name = "System Default"
        for idx, name in self._devices:
            if idx == dev_idx:
                mic_name = name
                break
        cfg_copy["mic_name"] = mic_name
        cfg_copy["ax_granted"] = _check_ax()
        cfg_copy["mic_granted"] = _check_mic()
        cfg_copy["proc_name"] = _get_responsible_app_name()
        cfg_copy["local_models"] = _local_models()

        dev_opts = [name for _, name in self._devices]

        cfg_json = json.dumps(cfg_copy)
        dev_json = json.dumps(dev_opts)
        ver = json.dumps(str(_VERSION))
        self._js(f"init({cfg_json}, {dev_json}, {ver})")

        # Start NSTimer to drain _js_queue on the main thread every 0.25s
        if self._NSTimer is not None and self._js_poll_timer is None:
            self._js_poll_timer = self._NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.25, self._js_poller, "poll:", None, True
            )

        # Check for update in the background (one-shot, non-blocking)
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _js(self, script: str):
        if self._webview is not None:
            self._webview.evaluateJavaScript_completionHandler_(script, None)

    def _handle(self, action: str, body: dict):
        if action == "open_ax":
            try:
                from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt  # type: ignore[import]
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            except Exception:
                pass
            subprocess.Popen(["open",
                "x-apple.systempreferences:"
                "com.apple.settings.PrivacySecurity.extension"
                "?Privacy_Accessibility"])

        elif action == "open_mic":
            # Request mic permission — this registers SypherSTT in System Settings → Microphone
            try:
                from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import]
                AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVMediaTypeAudio, lambda granted: None
                )
            except Exception:
                pass
            subprocess.Popen(["open",
                "x-apple.systempreferences:"
                "com.apple.settings.PrivacySecurity.extension"
                "?Privacy_Microphone"])

        elif action == "set_sound":
            self._cfg["sound_feedback"] = bool(body.get("value", True))
            _save_config(self._cfg)

        elif action == "preview_sound":
            sound = body.get("sound", "")
            if sound in _SYSTEM_SOUNDS:
                path = f"/System/Library/Sounds/{sound}.aiff"
                threading.Thread(
                    target=lambda p=path: subprocess.Popen(
                        ["afplay", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    ),
                    daemon=True,
                ).start()

        elif action == "set_record_stats":
            self._cfg["record_stats"] = bool(body.get("value", True))
            _save_config(self._cfg)

        elif action == "open_picker":
            self._show_picker(body.get("type", ""))

        elif action == "download_model":
            model_id = body.get("id", "")
            if model_id in _AVAILABLE_MODELS and not self._downloading:
                self._downloading = True
                threading.Thread(target=self._download_model, args=(model_id,), daemon=True).start()

        elif action == "picked":
            self._apply_pick(body.get("type", ""), body.get("value", ""))

        elif action == "start_recorder":
            self._start_recorder()

        elif action == "recorder_result":
            self._on_recorder_result(body.get("combo", ""))

        elif action == "cancel_recorder":
            self._recording = False

        elif action == "get_stats":
            self._send_stats()

        elif action == "confirm_clear_stats":
            self._confirm_clear_stats()

        elif action == "save_wpm":
            try:
                self._save_wpm(int(body.get("wpm", 0)))
            except Exception:
                pass

        elif action == "save_rate":
            try:
                mode = body.get("mode", "hourly")
                if mode not in ("hourly", "salary"):
                    mode = "hourly"
                value = float(body.get("value", 0))
                if value > 0:
                    self._save_rate(mode, value)
            except Exception:
                pass

        elif action == "open_model_hf":
            model_id = body.get("id", "")
            if model_id in _AVAILABLE_MODELS:
                subprocess.Popen(["open", f"https://huggingface.co/Systran/faster-whisper-{model_id}"])

        elif action == "open_model_folder":
            model_id = body.get("id", "")
            folder = _MODELS_DIR / model_id
            if (
                model_id in _AVAILABLE_MODELS
                and not folder.is_symlink()
                and folder.is_dir()
                and folder.resolve().is_relative_to(_MODELS_DIR.resolve())
            ):
                subprocess.Popen(["open", str(folder)])

        elif action == "open_log":
            try:
                log_path = _LOG_DIR / "sypher_stt.log"
                # Fall back to the directory if the log file hasn't been written yet
                target = str(log_path) if log_path.exists() else str(_LOG_DIR)
                subprocess.Popen(["open", target])
            except Exception as e:
                log.error("Failed to open log file: %s", e)

        elif action == "confirm_clear_log":
            self._confirm_clear_log()

        elif action == "check_for_update":
            threading.Thread(target=lambda: self._check_for_update(notify_if_current=True), daemon=True).start()

        elif action == "open_update_guide":
            subprocess.Popen(["open", "https://github.com/latenighthackathon/sypher-stt-macos#updating"])

    def _load_stats_file(self) -> dict:
        _STATS_PATH = _CONFIG_PATH.parent / "stats.json"
        if _STATS_PATH.exists():
            try:
                d = json.loads(_STATS_PATH.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
        return {"typing_wpm": 0, "days": {}}

    def _write_stats_file(self, stats: dict) -> None:
        _STATS_PATH = _CONFIG_PATH.parent / "stats.json"
        _secure_write_json(_STATS_PATH, stats)

    def _send_stats(self) -> None:
        stats = self._load_stats_file()
        self._js(f"updateStats({json.dumps(stats)})")

    def _confirm_clear_stats(self) -> None:
        try:
            from AppKit import NSAlert
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Clear usage stats?")
            alert.setInformativeText_(
                "All word counts, character counts, and audio durations will be removed. "
                "Your typing speed setting is preserved."
            )
            alert.addButtonWithTitle_("Clear Stats")
            alert.addButtonWithTitle_("Cancel")
            alert.setAlertStyle_(1)  # NSAlertStyleWarning
            if alert.runModal() == 1000:  # NSAlertFirstButtonReturn
                stats = self._load_stats_file()
                stats["days"] = {}
                self._write_stats_file(stats)
                self._send_stats()
        except Exception as e:
            log.error("NSAlert error: %s", e)

    def _confirm_clear_log(self) -> None:
        try:
            from AppKit import NSAlert
            log_path = _LOG_DIR / "sypher_stt.log"
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Clear log file?")
            alert.setInformativeText_(
                "The application log file will be emptied. "
                "This cannot be undone."
            )
            alert.addButtonWithTitle_("Clear Log")
            alert.addButtonWithTitle_("Cancel")
            alert.setAlertStyle_(1)  # NSAlertStyleWarning
            if alert.runModal() == 1000:  # NSAlertFirstButtonReturn
                if log_path.exists():
                    fd = os.open(
                        str(log_path),
                        os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                    )
                    os.close(fd)
        except Exception as e:
            log.error("Clear log error: %s", e)

    def _save_wpm(self, wpm: int) -> None:
        if wpm <= 0:
            return
        stats = self._load_stats_file()
        stats["typing_wpm"] = wpm
        self._write_stats_file(stats)

    def _save_rate(self, mode: str, value: float) -> None:
        stats = self._load_stats_file()
        stats["rate_mode"]  = mode
        stats["rate_value"] = value
        self._write_stats_file(stats)

    # ------------------------------------------------------------------ #
    # Auto-update                                                          #
    # ------------------------------------------------------------------ #

    def _check_for_update(self, notify_if_current: bool = False) -> None:
        """Fetch the latest GitHub release tag in a background thread.

        If a newer version is found, calls showUpdateBadge() in the webview.
        When notify_if_current=True (manual check), also calls showUpToDate()
        or showCheckError() so the button always resets with clear feedback.
        """
        try:
            import ssl
            import urllib.request
            # macOS system Python doesn't use the system keychain by default.
            # certifi is installed as a transitive dep of huggingface-hub and
            # provides a reliable CA bundle that avoids SSL_CERTIFICATE_VERIFY_FAILED.
            try:
                import certifi
                _ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                _ctx = ssl.create_default_context()
            req = urllib.request.Request(
                f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
                headers={"User-Agent": f"SypherSTT/{_VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=10, context=_ctx) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "").strip()
            clean = tag.lstrip("v")
            if not _VERSION_RE.fullmatch(clean):
                if notify_if_current:
                    self._js_queue.put("showUpToDate()")
                return
            if _parse_version(clean) > _parse_version(_VERSION):
                self._js_queue.put(f"showUpdateBadge({json.dumps('v' + clean)})")
            elif notify_if_current:
                self._js_queue.put("showUpToDate()")
        except Exception as e:
            log.warning("Update check failed: %s", e)
            if notify_if_current:
                self._js_queue.put("showCheckError()")

    def _show_picker(self, ptype: str):
        if ptype == "hotkey":
            cur = self._cfg.get("hotkey", "f8")
            opts = [{"value": v, "label": l} for v, l in _HOTKEY_PRESETS]
            # If the current hotkey is a custom combo (not in presets), prepend it
            # so it appears selected at the top of the picker list.
            if cur not in _VALID_HOTKEYS:
                from sypher_stt.hotkeys import hotkey_display as _hd
                opts = [{"value": cur, "label": _hd(cur)}] + opts
            self._js(f"showHotkeyPicker({json.dumps(opts)}, {json.dumps(cur)})")

        elif ptype == "mic":
            opts = [name for _, name in self._devices]
            dev_idx = self._cfg.get("audio_device")
            cur = "System Default"
            for idx, name in self._devices:
                if idx == dev_idx:
                    cur = name
                    break
            opts_json = json.dumps(opts)
            cur_json = json.dumps(cur)
            self._js(f"showPicker('mic', {opts_json}, {cur_json})")

        elif ptype == "model":
            local = _local_models()
            cur   = self._cfg.get("model", "base.en")
            self._js(f"showModelPicker({json.dumps(cur)}, {json.dumps(local)})")

    def _apply_pick(self, ptype: str, value: str):
        if ptype == "hotkey":
            from sypher_stt.hotkeys import validate_hotkey
            if not validate_hotkey(value):
                return
            self._cfg["hotkey"] = value.lower()
            _save_config(self._cfg)
            self._refresh()

        elif ptype == "mic":
            idx = None
            for i, name in self._devices:
                if name == value:
                    idx = i
                    break
            self._cfg["audio_device"] = idx
            self._cfg["mic_name"] = value
            _save_config(self._cfg)
            self._refresh()

        elif ptype == "model":
            self._cfg["model"] = value
            _save_config(self._cfg)
            self._refresh()

        elif ptype in ("sound_start", "sound_stop", "sound_error"):
            if value in _SYSTEM_SOUNDS:
                self._cfg[ptype] = value
                _save_config(self._cfg)
                self._refresh()

    def _download_model(self, model_id: str) -> None:
        """Download a Whisper model in a background thread, dispatch result to JS."""
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=f"Systran/faster-whisper-{model_id}",
                local_dir=str(_MODELS_DIR / model_id),
                local_dir_use_symlinks=False,
            )
            self._downloading = False
            local = _local_models()
            self._js_queue.put(f"modelDownloadDone({json.dumps(model_id)}, {json.dumps(local)})")
        except Exception as e:
            self._downloading = False
            self._js_queue.put(f"modelDownloadError('', {json.dumps(str(e))})")

    def _start_recorder(self):
        """Key capture is handled by JS keydown events; this just marks recording active."""
        self._recording = True

    def _on_recorder_result(self, combo: str):
        """Called on main thread when pynput captures a combo."""
        self._recording = False
        self._js(f"recorderResult({json.dumps(combo)})")

    def _refresh(self):
        """Push updated config back to the webview."""
        cfg_copy = dict(self._cfg)
        dev_idx = cfg_copy.get("audio_device")
        mic_name = "System Default"
        for idx, name in self._devices:
            if idx == dev_idx:
                mic_name = name
                break
        cfg_copy["mic_name"] = cfg_copy.get("mic_name") or mic_name
        cfg_copy["ax_granted"] = _check_ax()
        cfg_copy["mic_granted"] = _check_mic()
        cfg_copy["proc_name"] = _get_responsible_app_name()
        cfg_copy["local_models"] = _local_models()
        self._js(f"updateConfig({json.dumps(cfg_copy)})")


def main() -> None:
    SettingsWindow().run()


if __name__ == "__main__":
    main()
