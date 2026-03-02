"""Sypher STT — First-run setup wizard.

Uses PyObjC + WKWebView (replaces tkinter which crashes on macOS 26 Tahoe).
Dark two-panel layout: left sidebar (steps + progress) + right content area.
Matches Glaido's design language exactly.

Usage:
    python -m sypher_stt.setup_wizard    # run standalone
    from sypher_stt.setup_wizard import needs_setup, run_wizard
"""

import html
import json
import shutil
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

from sypher_stt.constants import (
    APPDATA_DIR as CONFIG_DIR,
    CONFIG_PATH,
    MODELS_DIR,
    SETUP_FLAG,
)
from sypher_stt.hotkeys import validate_hotkey
from sypher_stt.utils import (
    secure_write_json as _secure_write_json,
    secure_write_text as _secure_write_text,
    check_ax as _check_ax,
    check_mic as _check_mic,
    get_local_models as _get_local_models,
    TT_PASSAGES as _TT_PASSAGES,
    SHARED_HOTKEY_JS as _SHARED_HOTKEY_JS,
)

# ── Model catalog ─────────────────────────────────────────────────────────────
MODEL_CATALOG = [
    ("tiny.en",        "Systran/faster-whisper-tiny.en",          78_000_000, "Tiny",        "~75 MB",
     "Fastest · English-optimized · Best for quick notes"),
    ("base.en",        "Systran/faster-whisper-base.en",         148_000_000, "Base",        "~142 MB",
     "Fast · English-optimized · Good accuracy for everyday dictation"),
    ("small.en",       "Systran/faster-whisper-small.en",        487_000_000, "Small",       "~466 MB",
     "Balanced speed and accuracy · English-optimized"),
    ("medium.en",      "Systran/faster-whisper-medium.en",     1_528_000_000, "Medium",      "~1.5 GB",
     "High accuracy · English-optimized · Best for complex or accented speech"),
    ("large-v3",       "Systran/faster-whisper-large-v3",       3_100_000_000, "Large v3",    "~3.1 GB",
     "1,550M params · Highest accuracy available · Slowest"),
    ("large-v2",       "Systran/faster-whisper-large-v2",       3_100_000_000, "Large v2",    "~3.1 GB",
     "1,550M params · Near Large v3 accuracy · Predecessor to v3"),
]
MODEL_DEFAULT = "base.en"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_hotkey() -> str:
    """Return the configured hotkey as a raw lowercase string (e.g. 'f8')."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text()).get("hotkey", "f8")
        except Exception:
            pass
    return "f8"


def _get_saved_wpm() -> int:
    """Return the user's previously saved typing WPM, or 0 if not set."""
    stats_path = CONFIG_DIR / "stats.json"
    if stats_path.exists():
        try:
            d = json.loads(stats_path.read_text())
            wpm = d.get("typing_wpm", 0)
            if isinstance(wpm, int) and wpm > 0:
                return wpm
        except Exception:
            pass
    return 0


from sypher_stt.utils import get_responsible_app_name as _get_responsible_app_name


# ── HTML/CSS/JS ───────────────────────────────────────────────────────────────

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
  font-family: -apple-system, 'SF Pro Display', 'Helvetica Neue', sans-serif;
  display: flex;
  -webkit-user-select: none;
  user-select: none;
}

/* ── LEFT SIDEBAR ── */
.sidebar {
  width: 38%;
  min-width: 200px;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 28px 0 20px;
  flex-shrink: 0;
  position: relative;
  overflow: hidden;
}
.sidebar-top {
  padding: 0 20px 24px;
}
.sidebar-logo { font-size: 32px; margin-bottom: 12px; }
.sidebar-title {
  font-size: 18px;
  font-weight: 700;
  color: white;
  margin-bottom: 8px;
}
.sidebar-title-grad { color: #818cf8; }
.sidebar-sub { font-size: 12px; color: #6b7280; line-height: 1.5; }

/* Step list */
.step-list {
  padding: 0 20px;
  margin-bottom: 24px;
}
.step-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  font-size: 12px;
}
.step-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
  transition: all 300ms;
}
.step-dot.done   { background: var(--btn-bg); color: white; }
.step-dot.active { background: var(--btn-bg); color: white; }
.step-dot.future { background: var(--border); color: #6b7280; }
.step-label.done   { color: #6b7280; }
.step-label.active { color: white; font-weight: 600; }
.step-label.future { color: #4b5563; }

/* Sidebar main (centered) */
.sidebar-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* Progress bars */
.progress-row {
  padding: 0 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.progress-counter { font-size: 11px; color: #6b7280; }
.progress-bars { display: flex; gap: 5px; }
.progress-bar {
  height: 3px;
  flex: 1;
  border-radius: 2px;
  transition: background 300ms;
}
.progress-bar.done   { background: var(--accent); }
.progress-bar.active { background: var(--accent); }
.progress-bar.future { background: var(--border); }

/* ── RIGHT CONTENT ── */
.content {
  flex: 1;
  background: var(--body-bg);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 16px 32px 12px;
}
.content-inner { flex: 1; display: flex; flex-direction: column; }
.step-page { display: none; flex: 1; flex-direction: column; }
.step-page.active { display: flex; }
.page-body { flex: 1; display: flex; flex-direction: column; justify-content: center; min-height: 0; }

/* Step emoji */
.step-title { font-size: 24px; font-weight: 700; color: white; margin-bottom: 6px; }
.step-desc  { font-size: 13px; color: #6b7280; line-height: 1.6; margin-bottom: 10px; }

/* Feature list card (welcome) */
.feature-card {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 20px;
  margin-bottom: 14px;
}
.feature-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 0;
  font-size: 12px;
  color: #d1d5db;
}
.feature-icon { font-size: 16px; width: 22px; text-align: center; flex-shrink: 0; }

/* Steps card */
.steps-card {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 20px;
  margin-bottom: 10px;
}
.steps-card-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 5px 0;
}
.steps-num {
  background: var(--btn-bg);
  color: white;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
  margin-top: 1px;
}
.steps-text { font-size: 12px; color: #d1d5db; line-height: 1.5; }

/* AX status */
.ax-status {
  font-size: 13px;
  color: #6b7280;
  margin-top: 4px;
  transition: color 300ms;
}
.ax-status.granted { color: #4ade80; }

/* Model cards */
.model-grid { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; max-height: 246px; overflow-y: auto; }
.model-grid::-webkit-scrollbar { width: 4px; }
.model-grid::-webkit-scrollbar-track { background: transparent; }
.model-grid::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.model-card {
  background: var(--cards-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: border-color 150ms, background 150ms;
}
.model-card.selected {
  border-color: var(--accent);
  background: rgba(99,102,241,0.06);
}
.model-radio {
  width: 16px; height: 16px;
  border-radius: 50%;
  border: 2px solid var(--border);
  flex-shrink: 0;
  transition: all 150ms;
  display: flex; align-items: center; justify-content: center;
}
.model-radio.selected { border-color: var(--accent); background: var(--accent); }
.model-radio.selected::after {
  content: '';
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #0d0d0d;
}
.model-info { flex: 1; }
.model-name-row { display: flex; align-items: center; gap: 8px; }
.model-name { font-size: 13px; font-weight: 600; color: white; }
.model-badge {
  background: var(--btn-bg);
  color: white;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.model-installed-badge {
  background: rgba(74,222,128,0.12);
  color: #4ade80;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.model-desc { font-size: 11px; color: #6b7280; margin-top: 2px; }
.model-link { font-size: 10px; color: #52525b; background: none; border: none; padding: 0; margin-top: 5px; cursor: pointer; font-family: inherit; transition: color 150ms; display: inline-block; }
.model-link:hover { color: var(--accent); }
.model-size { font-size: 12px; font-weight: 600; color: #6b7280; flex-shrink: 0; }
.model-card.selected .model-size { color: var(--accent); }

/* Progress bar */
.dl-progress { margin-bottom: 24px; display: none; }
.dl-label { font-size: 13px; color: white; margin-bottom: 10px; }
.dl-track {
  background: var(--cards-bg);
  border-radius: 3px;
  height: 5px;
  overflow: hidden;
}
.dl-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  width: 0%;
  transition: width 400ms;
}
.dl-status { font-size: 11px; color: #6b7280; margin-top: 6px; }

/* Hotkey pill (done page) */
.hotkey-pill {
  display: inline-flex;
  align-items: center;
  background: var(--btn-bg);
  color: white;
  font-size: 26px;
  font-weight: 700;
  padding: 8px 24px;
  border-radius: 10px;
  margin-bottom: 14px;
}
/* Hotkey picker (done page) */
.hotkey-picker { margin-bottom: 10px; }
.hotkey-picker-label { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
.hotkey-picker-grid { display: flex; flex-wrap: wrap; gap: 5px; }
.hk-btn {
  background: linear-gradient(var(--cards-bg), var(--cards-bg)) padding-box,
              linear-gradient(90deg, #4ecdc4, #5890e0, #8060c8, #c05090, #d07040, #c8b030, #90b840) border-box;
  border: 1px solid transparent;
  border-radius: 6px;
  color: #d1d5db;
  font-size: 11px;
  font-weight: 600;
  padding: 5px 9px;
  cursor: pointer;
  transition: opacity 150ms, color 150ms;
}
.hk-btn:hover { opacity: 0.75; color: white; }
.hk-btn.selected {
  background: linear-gradient(90deg, #4ecdc4, #5890e0, #8060c8, #c05090, #d07040, #c8b030, #90b840) padding-box,
              linear-gradient(90deg, #4ecdc4, #5890e0, #8060c8, #c05090, #d07040, #c8b030, #90b840) border-box;
  border: 1px solid transparent;
  color: white;
}

/* Buttons */
.btn-primary {
  background: var(--btn-bg);
  color: white;
  font-size: 14px;
  font-weight: 700;
  padding: 12px 28px;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  transition: opacity 150ms;
}
.btn-primary:hover { background: var(--btn-hover); }
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-link {
  background: none;
  border: none;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
  padding: 4px 0;
}
.btn-link:hover { color: #d1d5db; }
.btn-cancel {
  background: transparent;
  border: 1px solid #3f3f46;
  color: #9ca3af;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 18px;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 150ms, color 150ms;
}
.btn-cancel:hover { border-color: #6b7280; color: #d1d5db; }

/* ── RECORDER OVERLAY ── */
.recorder-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.65); z-index: 200;
  align-items: center; justify-content: center;
}
.recorder-overlay.open { display: flex; }
.recorder-box {
  background: var(--cards-bg); border: 1px solid var(--border);
  border-radius: 14px; padding: 28px 32px; min-width: 300px;
  display: flex; flex-direction: column; align-items: center;
  gap: 14px; text-align: center;
}
.recorder-title { font-size: 15px; font-weight: 700; color: white; }
.recorder-hint  { font-size: 12px; color: #6b7280; min-height: 16px; }
.rec-display-wrap {
  background: var(--body-bg); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 36px;
}
.rec-display { font-size: 22px; font-weight: 700; color: white; font-family: monospace; }
.rec-actions { display: flex; gap: 10px; }

/* Bottom action area */
.page-actions { margin-top: auto; padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.page-cta { display: flex; align-items: center; gap: 12px; }
.page-nav { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border); padding-top: 10px; }

/* Close button */
.close-btn {
  position: absolute;
  top: 12px; right: 12px;
  background: none;
  border: none;
  color: #4b5563;
  font-size: 16px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  z-index: 10;
}
.close-btn:hover { color: white; background: rgba(255,255,255,0.08); }

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
</style>
</head>
<body>

<!-- CLOSE BUTTON -->
<button class="close-btn" onclick="post('close',{})">✕</button>

<!-- LEFT SIDEBAR -->
<div class="sidebar">
  <div class="sidebar-main">
    <div class="sidebar-top">
      <div class="sidebar-title">Sypher<span class="sidebar-title-grad">STT</span></div>
      <div class="sidebar-sub">Push-to-talk voice dictation for macOS. Hold your hotkey, speak naturally, release — text is pasted instantly into any app. Fully offline: transcription runs on-device via Faster Whisper with no internet, no cloud, and no data ever leaving your Mac.</div>
    </div>
    <div class="step-list" id="step-list"></div>
  </div>
  <div class="progress-row" id="progress-row"></div>
</div>

<!-- RIGHT CONTENT -->
<div class="content">
  <div class="content-inner">

    <!-- PAGE 0: Welcome -->
    <div class="step-page active" id="page-0">
      <div class="page-body">
        <div class="step-title">Welcome to SypherSTT</div>
        <div class="step-desc">Type with your voice. Instantly. Privately.</div>
        <div class="feature-card">
          <div class="feature-row"><span class="feature-icon">🎙</span>Hold your hotkey, speak, release — text appears instantly</div>
          <div class="feature-row"><span class="feature-icon">🔒</span>Fully offline — audio never leaves your Mac, ever</div>
          <div class="feature-row"><span class="feature-icon">📋</span>Auto-pastes into any app — Slack, Notes, browser, anywhere</div>
          <div class="feature-row"><span class="feature-icon">⚡</span>Apple Silicon optimized for fast, local transcription</div>
        </div>
      </div>
      <div class="page-actions">
        <div class="page-cta">
          <button class="btn-primary" onclick="advance()">Get Started →</button>
        </div>
        <div class="page-nav">
          <div></div>
          <button class="btn-link" onclick="post('close',{})">Already set up? Skip wizard</button>
        </div>
      </div>
    </div>

    <!-- PAGE 1: Permissions -->
    <div class="step-page" id="page-1">
      <div class="page-body">
        <div class="step-title">Grant Permissions</div>
        <div class="step-desc">SypherSTT needs two macOS permissions to work: Accessibility to detect your hotkey, and Microphone to capture audio.</div>
        <div class="steps-card">
          <div style="font-size:10px;color:var(--accent);font-weight:700;margin-bottom:8px;letter-spacing:0.5px">ACCESSIBILITY &nbsp;<span id="ax-badge" style="color:#4ade80;font-size:10px"></span></div>
          <div class="steps-card-item">
            <div class="steps-num">1</div>
            <div class="steps-text">Open <button onclick="post('open_ax',{})" style="background:none;border:none;padding:0;font-family:inherit;font-size:12px;color:var(--accent-2);cursor:pointer;font-weight:600;text-decoration:underline;text-underline-offset:3px">Privacy &amp; Security → Accessibility</button></div>
          </div>
          <div class="steps-card-item">
            <div class="steps-num">2</div>
            <div class="steps-text">Enable the toggle next to <strong style="color:white">__PROC_NAME__</strong></div>
          </div>
        </div>
        <div class="steps-card">
          <div style="font-size:10px;color:var(--accent);font-weight:700;margin-bottom:8px;letter-spacing:0.5px">MICROPHONE &nbsp;<span id="mic-badge" style="color:#4ade80;font-size:10px"></span></div>
          <div class="steps-card-item">
            <div class="steps-num">1</div>
            <div class="steps-text">Open <button onclick="post('open_mic',{})" style="background:none;border:none;padding:0;font-family:inherit;font-size:12px;color:var(--accent-2);cursor:pointer;font-weight:600;text-decoration:underline;text-underline-offset:3px">Privacy &amp; Security → Microphone</button></div>
          </div>
          <div class="steps-card-item">
            <div class="steps-num">2</div>
            <div class="steps-text">Enable the toggle next to <strong style="color:white">__PROC_NAME__</strong></div>
          </div>
        </div>
      </div>
      <div class="page-actions">
        <div class="page-cta">
          <button class="btn-primary" id="ax-open-btn" onclick="post('open_ax',{})">Enable Accessibility</button>
          <button class="btn-primary" id="ax-next-btn" onclick="advance()" style="display:none">Next →</button>
        </div>
        <div class="page-nav">
          <button class="btn-link" onclick="goBack()">← Back</button>
          <button class="btn-link" onclick="advance()">Skip for now →</button>
        </div>
      </div>
    </div>

    <!-- PAGE 2: Model Download -->
    <div class="step-page" id="page-2">
      <div class="page-body">
        <div class="step-title">Download Faster Whisper Model</div>
        <div class="step-desc">Powered by Faster Whisper (CTranslate2) — runs fully on-device. No internet needed after download.</div>
        <div class="model-grid" id="model-grid"></div>
        <div class="dl-progress" id="dl-progress">
          <div class="dl-label" id="dl-label">Downloading…</div>
          <div class="dl-track"><div class="dl-fill" id="dl-fill"></div></div>
          <div class="dl-status" id="dl-status">Connecting…</div>
        </div>
      </div>
      <div class="page-actions">
        <div class="page-cta">
          <button class="btn-primary" id="dl-btn" onclick="startDownload()">Download Model</button>
          <button class="btn-primary" id="dl-next-btn" onclick="advance()" style="display:none">Next →</button>
        </div>
        <div class="page-nav">
          <button class="btn-link" onclick="goBack()">← Back</button>
          <button class="btn-link" onclick="advance()">Skip (use existing) →</button>
        </div>
      </div>
    </div>

    <!-- PAGE 3: Track Time Saved -->
    <div class="step-page" id="page-3">
      <div class="page-body">
        <div class="step-title">Track Time Saved</div>
        <div class="step-desc">See how much time you save by speaking instead of typing. Usage data is stored exclusively on your device — never transmitted or shared.</div>
        <div class="feature-card">
          <div style="font-size:10px;color:var(--accent);font-weight:700;margin-bottom:8px;letter-spacing:0.5px">WHAT IS TRACKED</div>
          <div class="feature-row"><span class="feature-icon" style="color:#4ade80">✓</span># of words transcribed</div>
          <div class="feature-row"><span class="feature-icon" style="color:#4ade80">✓</span># of characters transcribed</div>
          <div class="feature-row"><span class="feature-icon" style="color:#4ade80">✓</span>Duration of input audio</div>
          <div style="font-size:10px;color:#991b1b;font-weight:700;margin:10px 0 8px;letter-spacing:0.5px">NEVER STORED OR SHARED</div>
          <div class="feature-row"><span class="feature-icon" style="color:#991b1b">✗</span>Transcribed text or conversations</div>
          <div class="feature-row"><span class="feature-icon" style="color:#991b1b">✗</span>Keystrokes, hotkeys, or audio recordings</div>
          <div class="feature-row"><span class="feature-icon" style="color:#991b1b">✗</span>Any personally identifiable information</div>
        </div>
<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:rgba(245,197,24,0.06);border:1px solid rgba(245,197,24,0.2);border-radius:8px" id="wpm-cta">
          <div>
            <div style="font-size:12px;font-weight:600;color:white">⌨️ Typing speed <span id="wpm-result-badge" style="font-size:10px;color:#4ade80;font-weight:700;margin-left:6px;display:none"></span></div>
            <div style="font-size:11px;color:#6b7280;margin-top:2px" id="wpm-sub-desc">Take the test to calibrate your estimated time saved.</div>
          </div>
          <button class="btn-primary" id="wpm-btn" style="padding:7px 14px;font-size:12px;flex-shrink:0" onclick="openWizTypingTest()">Take test →</button>
        </div>
      </div>
      <div class="page-actions">
        <div class="page-cta">
          <button class="btn-primary" id="wpm-continue-btn" style="display:none" onclick="advance()">Continue →</button>
        </div>
        <div class="page-nav">
          <button class="btn-link" onclick="goBack()">← Back</button>
          <div></div>
        </div>
      </div>
    </div>

    <!-- PAGE 4: Done -->
    <div class="step-page" id="page-4">
      <div class="page-body">
        <div class="step-title">You're all set!</div>
        <div class="step-desc" id="done-desc">Hold your hotkey to start recording. Release to transcribe and paste.</div>
        <div class="hotkey-pill" id="hotkey-pill">F8</div>
        <div class="hotkey-picker">
          <div class="hotkey-picker-label">Change hotkey:</div>
          <div class="hotkey-picker-grid" id="hotkey-picker-grid"></div>
        </div>
        <div style="font-size:12px;color:#4b5563;margin-bottom:20px;">Find SypherSTT in your menu bar 🎙</div>
      </div>
      <div class="page-actions">
        <div class="page-cta">
          <button class="btn-primary" onclick="post('finish',{})">Launch SypherSTT →</button>
        </div>
      </div>
    </div>

  </div>
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
      <button class="btn-primary" id="rec-confirm" disabled onclick="confirmRecorder()">Use This</button>
    </div>
    <button class="btn-link" id="rec-retry" style="display:none" onclick="retryRecorder()">Try again →</button>
  </div>
</div>

<!-- OVERWRITE CONFIRM OVERLAY -->
<div class="recorder-overlay" id="overwrite-overlay">
  <div class="recorder-box">
    <div class="recorder-title">Model Already Installed</div>
    <div style="font-size:12px;color:#9ca3af;text-align:center;line-height:1.6;max-width:300px">
      <strong id="ow-model-name" style="color:white"></strong> is already installed on your Mac.<br>
      Re-downloading will delete the existing files and replace them with a fresh copy from HuggingFace.
    </div>
    <div class="rec-actions">
      <button class="btn-cancel" onclick="cancelOverwrite()">Keep Existing</button>
      <button class="btn-primary" style="background:#dc2626;border-color:#dc2626" onclick="confirmOverwrite()">Overwrite &amp; Re-download</button>
    </div>
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
      <button class="btn-cancel" onclick="closeWizTypingTest()">Cancel</button>
      <button class="btn-primary" id="tt-next" style="display:none" onclick="_ttNextRound()">Next passage →</button>
      <button class="btn-primary" id="tt-save" style="display:none" onclick="saveWizWpm()">Save speed</button>
    </div>
  </div>
</div>

<script>
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

const STEPS = ['Welcome', 'Permissions', 'Download Model', 'Track Time Saved', 'All Set'];
let currentStep = 0;
let selectedModel = 'base.en';
let localModels = [];
const HOTKEY_OPTIONS = ['f1','f2','f3','f4','f5','f6','f7','f8','f9','f10','f11','f12','caps_lock'];
let selectedHotkey = 'f8';
const MODELS = [
  {id:'tiny.en',        name:'Tiny',        size:'~75 MB',   desc:'Fastest · English-optimized · Best for quick notes',                          recommended:false},
  {id:'base.en',        name:'Base',        size:'~142 MB',  desc:'Fast · English-optimized · Good accuracy for everyday dictation',              recommended:true},
  {id:'small.en',       name:'Small',       size:'~466 MB',  desc:'Balanced speed and accuracy · English-optimized',                              recommended:false},
  {id:'medium.en',      name:'Medium',      size:'~1.5 GB',  desc:'High accuracy · English-optimized · Best for complex or accented speech',      recommended:false},
  {id:'large-v3',       name:'Large v3',    size:'~3.1 GB',  desc:'1,550M params · Highest accuracy available · Slowest',                        recommended:false},
  {id:'large-v2',       name:'Large v2',    size:'~3.1 GB',  desc:'1,550M params · Near Large v3 accuracy · Predecessor to v3',                  recommended:false},
];

function updateNav() {
  // Step list
  const list = document.getElementById('step-list');
  list.innerHTML = STEPS.map((s,i) => {
    const cls = i < currentStep ? 'done' : i === currentStep ? 'active' : 'future';
    const dot = i < currentStep ? '✓' : (i+1);
    return `<div class="step-item">
      <div class="step-dot ${cls}">${dot}</div>
      <div class="step-label ${cls}">${s}</div>
    </div>`;
  }).join('');

  // Progress bars
  const pr = document.getElementById('progress-row');
  pr.innerHTML = `<div class="progress-counter">${currentStep+1} of ${STEPS.length}</div>
    <div class="progress-bars">${STEPS.map((_,i) => {
      const cls = i <= currentStep ? (i < currentStep ? 'done' : 'active') : 'future';
      return `<div class="progress-bar ${cls}"></div>`;
    }).join('')}</div>`;
}

function showPage(n) {
  document.querySelectorAll('.step-page').forEach(p => p.classList.remove('active'));
  const pg = document.getElementById('page-'+n);
  if (pg) pg.classList.add('active');

  if (n === 2) renderModels();
  if (n === 4) animateDone();
}

function advance() {
  if (currentStep < STEPS.length - 1) {
    currentStep++;
    updateNav();
    showPage(currentStep);
    post('step_changed', {step: currentStep});
  }
}

function goBack() {
  if (currentStep > 0) {
    currentStep--;
    updateNav();
    showPage(currentStep);
    post('step_changed', {step: currentStep});
  }
}

function renderModels() {
  const grid = document.getElementById('model-grid');
  grid.innerHTML = MODELS.map(m => {
    const inst = localModels.includes(m.id);
    const instBadge = inst ? '<span class="model-installed-badge">Installed</span>' : '';
    const link = inst
      ? `<button class="model-link" onclick="openModelFolder(event,${JSON.stringify(m.id)})">Show in Finder ↗</button>`
      : `<button class="model-link" onclick="openModelHF(event,${JSON.stringify(m.id)})">HuggingFace ↗</button>`;
    return `
    <div class="model-card ${m.id===selectedModel?'selected':''}" id="mc-${m.id}" onclick="selectModel(${JSON.stringify(m.id)})">
      <div class="model-radio ${m.id===selectedModel?'selected':''}"></div>
      <div class="model-info">
        <div class="model-name-row">
          <span class="model-name">${m.name}</span>
          ${m.recommended ? '<span class="model-badge">Recommended</span>' : ''}
          ${instBadge}
        </div>
        <div class="model-desc">${m.desc}</div>
        ${link}
      </div>
      <div class="model-size">${m.size}</div>
    </div>`;
  }).join('');
}

function setLocalModels(ids) {
  localModels = ids;
  renderModels();
  _updateDlBtn();
}

function selectModel(id) {
  selectedModel = id;
  renderModels();
  _updateDlBtn();
}

function _updateDlBtn() {
  const btn = document.getElementById('dl-btn');
  if (!btn || btn.style.display === 'none') return;
  btn.textContent = localModels.includes(selectedModel) ? 'Re-download Model' : 'Download Model';
}

function startDownload() {
  if (localModels.includes(selectedModel)) {
    const m = MODELS.find(x => x.id === selectedModel);
    document.getElementById('ow-model-name').textContent = m ? m.name : selectedModel;
    document.getElementById('overwrite-overlay').classList.add('open');
  } else {
    post('start_download', {model: selectedModel});
  }
}

function cancelOverwrite() {
  document.getElementById('overwrite-overlay').classList.remove('open');
}

function confirmOverwrite() {
  document.getElementById('overwrite-overlay').classList.remove('open');
  post('start_download', {model: selectedModel, overwrite: true});
}

function openModelHF(e, id) {
  e.stopPropagation();
  post('open_model_hf', {id});
}

function openModelFolder(e, id) {
  e.stopPropagation();
  post('open_model_folder', {id});
}


function fmtHotkey(raw) {
  return raw === 'caps_lock' ? 'Caps Lock' : raw.toUpperCase();
}

__SHARED_HOTKEY_JS__

function renderHotkeyPicker() {
  const grid = document.getElementById('hotkey-picker-grid');
  if (!grid) return;
  grid.innerHTML = HOTKEY_OPTIONS.map(k => {
    const label = k === 'caps_lock' ? 'Caps' : k.toUpperCase();
    return `<button class="hk-btn ${k === selectedHotkey ? 'selected' : ''}" onclick="selectHotkey('${k}')">${label}</button>`;
  }).join('') +
  `<button class="hk-btn" style="color:var(--accent);border-color:var(--accent);opacity:0.8" onclick="openRecorder()">🎹 Custom…</button>`;
}

function selectHotkey(key) {
  selectedHotkey = key;
  renderHotkeyPicker();
  const display = hotkeyDisplay(key);
  document.getElementById('hotkey-pill').textContent = display;
  document.getElementById('done-desc').textContent =
    'Hold ' + display + ' to start recording. Release to transcribe and paste.';
  post('set_hotkey', {hotkey: key});
}

function animateDone() {
  renderHotkeyPicker();
  post('get_hotkey', {});
}

// Called from Python
function updateAxStatus(granted) {
  if (currentStep !== 1) return;
  const badge = document.getElementById('ax-badge');
  if (badge) { badge.textContent = granted ? '✓ Granted' : ''; }
  if (granted) {
    const ob = document.getElementById('ax-open-btn');
    const nb = document.getElementById('ax-next-btn');
    if (ob) ob.style.display = 'none';
    if (nb) nb.style.display = '';
  }
}

function updateMicStatus(granted) {
  if (currentStep !== 1) return;
  const badge = document.getElementById('mic-badge');
  if (badge) badge.textContent = granted ? '✓ Granted' : '';
}

function updateProgress(pct, label, status) {
  document.getElementById('dl-progress').style.display = 'block';
  document.getElementById('dl-btn').style.display = 'none';
  document.getElementById('dl-fill').style.width = (pct*100) + '%';
  if (label) document.getElementById('dl-label').textContent = label;
  if (status) document.getElementById('dl-status').textContent = status;
}

function downloadComplete() {
  document.getElementById('dl-fill').style.width = '100%';
  document.getElementById('dl-status').textContent = 'Complete ✓';
  document.getElementById('dl-status').style.color = '#4ade80';
  document.getElementById('dl-next-btn').style.display = '';
}

function downloadError(msg) {
  document.getElementById('dl-status').textContent = 'Error: ' + msg;
  document.getElementById('dl-status').style.color = '#f87171';
  document.getElementById('dl-btn').style.display = 'block';
  document.getElementById('dl-btn').textContent = 'Retry';
}

function setHotkey(rawKey) {
  selectedHotkey = rawKey;
  renderHotkeyPicker();
  const display = hotkeyDisplay(rawKey);
  document.getElementById('hotkey-pill').textContent = display;
  document.getElementById('done-desc').textContent =
    'Hold ' + display + ' to start recording. Release to transcribe and paste.';
}

// ── Key recorder ──────────────────────────────────────────────────────────
let _recKeyHandler = null;
let currentRecCombo = '';

const _REC_KEY_MAP = {
  'F1':'f1','F2':'f2','F3':'f3','F4':'f4','F5':'f5','F6':'f6',
  'F7':'f7','F8':'f8','F9':'f9','F10':'f10','F11':'f11','F12':'f12',
  ' ':'space','Enter':'enter','Tab':'tab','Escape':'esc',
  'Delete':'delete','Backspace':'backspace','CapsLock':'caps_lock',
  'Home':'home','End':'end','PageUp':'page_up','PageDown':'page_down',
};
const _REC_MOD_KEYS = new Set(['Control','Alt','Shift','Meta','CapsLock','NumLock','ScrollLock']);

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
  if (!main) return;
  if (main === 'esc' && mods.length === 0) { _stopRecKey(); closeRecorder(); return; }
  _stopRecKey();
  recorderResult([...mods, main].join('+'));
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
  if (combo) selectHotkey(combo);
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

// ── Typing speed test ────────────────────────────────────────────────────────
const _TT_PASSAGES = __TT_PASSAGES__;
let _ttRound = 0, _ttScores = [], _ttStart = null, _ttWpm = 0;

function openWizTypingTest() {
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

function closeWizTypingTest() {
  document.getElementById('tt-overlay').classList.remove('open');
}

function _ttRender(typed) {
  const TT = _TT_PASSAGES[_ttRound].text;
  let h = '';
  for (let i = 0; i < TT.length; i++) {
    const raw = TT[i];
    const ch = raw === '<' ? '&lt;' : raw === '>' ? '&gt;' : raw === '&' ? '&amp;' : raw;
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

function saveWizWpm() {
  if (_ttWpm > 0) {
    post('save_wpm', {wpm: _ttWpm});
    const badge = document.getElementById('wpm-result-badge');
    if (badge) { badge.textContent = '✓ ' + _ttWpm + ' WPM'; badge.style.display = 'inline'; }
    const btn = document.getElementById('wpm-btn');
    if (btn) btn.textContent = 'Retake →';
    const sub = document.getElementById('wpm-sub-desc');
    if (sub) { sub.textContent = 'Speed saved — Sypher STT will calculate your estimated time saved.'; sub.style.color = '#4ade80'; }
    const cont = document.getElementById('wpm-continue-btn');
    if (cont) cont.style.display = '';
  }
  closeWizTypingTest();
}

function setExistingWpm(wpm) {
  if (wpm > 0) {
    const badge = document.getElementById('wpm-result-badge');
    if (badge) { badge.textContent = '✓ ' + wpm + ' WPM'; badge.style.display = 'inline'; }
    const btn = document.getElementById('wpm-btn');
    if (btn) btn.textContent = 'Retake →';
    const sub = document.getElementById('wpm-sub-desc');
    if (sub) { sub.textContent = 'Speed saved — Sypher STT will calculate your estimated time saved.'; sub.style.color = '#4ade80'; }
    const cont = document.getElementById('wpm-continue-btn');
    if (cont) cont.style.display = '';
  }
}

function post(action, data) {
  window.location.href = 'sypher://' + encodeURIComponent(JSON.stringify({action, ...data}));
}

// Init
updateNav();
renderModels();
</script>
</body></html>
"""


# ── PyObjC WKWebView Application ──────────────────────────────────────────────

class SetupWizard:
    """First-run wizard using PyObjC NSWindow + WKWebView.

    Plain Python class — all NSObject subclasses are inner classes inside run()
    so they close over wizard_ref without needing NSObject inheritance here.
    """

    def __init__(self):
        self._step = 0
        self._selected_model = MODEL_DEFAULT
        self._downloading = False
        self._download_success = False
        self._download_error = ""
        self._app = None
        self._window = None
        self._webview = None
        self._ax_timer = None
        self._dl_timer = None
        self._recording = False
        # keep strong refs to NSObject instances so ARC doesn't release them
        self._ns_refs = []

    def run(self):
        from Foundation import NSObject, NSTimer, NSMakeRect
        from AppKit import (
            NSApplication, NSWindow, NSScreen,
            NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, NSColor,
        )
        from WebKit import WKWebView, WKWebViewConfiguration

        wizard_ref = self

        # ── Navigation delegate — intercepts sypher:// messages from JS ───
        class NavDelegate(NSObject):
            def webView_decidePolicyForNavigationAction_decisionHandler_(
                    self, wv, action, handler):
                try:
                    url = str(action.request().URL().absoluteString() or "")
                except Exception:
                    url = ""
                if url.startswith("sypher://"):
                    try:
                        body = json.loads(urllib.parse.unquote(url[9:]))
                        wizard_ref._handle(body.get("action", ""), body)
                    except Exception:
                        pass
                    handler(0)   # WKNavigationActionPolicyCancel
                else:
                    handler(1)   # WKNavigationActionPolicyAllow

        # ── Timer callbacks (NSObject so NSTimer can call them) ───────────
        class TimerTarget(NSObject):
            def pollAx_(self, timer):
                try:
                    if wizard_ref._step == 1:
                        wizard_ref._js(f"updateAxStatus({json.dumps(_check_ax())})")
                        wizard_ref._js(f"updateMicStatus({json.dumps(_check_mic())})")
                except Exception:
                    pass

        # Defined once here so re-entrant start_download calls don't try to
        # re-register an already-registered NSObject class name.
        class DlTimerTarget(NSObject):
            def pollDownload_(self, timer):
                try:
                    wiz = wizard_ref
                    if wiz._download_error:
                        wiz._js(f"downloadError({json.dumps(wiz._download_error)})")
                        timer.invalidate()
                        wiz._dl_timer = None
                        return
                    if wiz._download_success:
                        wiz._js("downloadComplete()")
                        timer.invalidate()
                        wiz._dl_timer = None
                        return
                    dest = MODELS_DIR / wiz._selected_model
                    entry = next((e for e in MODEL_CATALOG if e[0] == wiz._selected_model), None)
                    expected = entry[2] if entry else 148_000_000
                    current = 0
                    if dest.exists():
                        try:
                            current = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
                        except Exception:
                            pass
                    pct = min(current / expected, 0.98) if expected else 0
                    curr_mb = current // 1_000_000
                    exp_mb  = expected // 1_000_000
                    status = f"{curr_mb} MB / {exp_mb} MB  ·  {int(pct*100)}%" if current > 0 else "Downloading…"
                    name  = entry[3] if entry else wiz._selected_model
                    size  = entry[4] if entry else ""
                    label = f"Downloading {name} ({size})…"
                    wiz._js(f"updateProgress({pct}, {json.dumps(label)}, {json.dumps(status)})")
                except Exception:
                    pass

        self._DlTimerTarget = DlTimerTarget
        self._NSTimer = NSTimer

        # ── App delegate ──────────────────────────────────────────────────
        class AppDelegate(NSObject):
            def applicationDidFinishLaunching_(self, notif):
                wizard_ref._window.makeKeyAndOrderFront_(None)
                wizard_ref._window.center()
                wizard_ref._app.activateIgnoringOtherApps_(True)
                # Start AX polling timer
                tt = TimerTarget.alloc().init()
                wizard_ref._ns_refs.append(tt)
                wizard_ref._ax_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.8, tt, b"pollAx:", None, True
                )

            def applicationShouldTerminateAfterLastWindowClosed_(self, app):
                return True

        # ── NSApplication ─────────────────────────────────────────────────
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)
        self._app = app

        # ── Window ────────────────────────────────────────────────────────
        W, H = 960, 540
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
        window.setTitle_("SypherSTT Setup")
        window.setBackgroundColor_(NSColor.colorWithRed_green_blue_alpha_(
            0x09/255, 0x09/255, 0x0b/255, 1.0
        ))
        window.setReleasedWhenClosed_(False)
        try:
            from AppKit import NSAppearance
            window.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        except Exception:
            pass
        self._window = window

        # ── WKWebView ─────────────────────────────────────────────────────
        config = WKWebViewConfiguration.alloc().init()
        cv = window.contentView()
        webview = WKWebView.alloc().initWithFrame_configuration_(cv.frame(), config)
        webview.setAutoresizingMask_(18)

        nav_delegate = NavDelegate.alloc().init()
        self._ns_refs.append(nav_delegate)
        webview.setNavigationDelegate_(nav_delegate)

        cv.addSubview_(webview)
        self._webview = webview
        proc_name = html.escape(_get_responsible_app_name())
        _html = (
            _HTML
            .replace("__PROC_NAME__", proc_name)
            .replace("__TT_PASSAGES__", json.dumps(_TT_PASSAGES))
            .replace("__SHARED_HOTKEY_JS__", _SHARED_HOTKEY_JS)
        )
        webview.loadHTMLString_baseURL_(_html, None)

        # ── Run ───────────────────────────────────────────────────────────
        delegate = AppDelegate.alloc().init()
        self._ns_refs.append(delegate)
        app.setDelegate_(delegate)
        app.run()

    # ── Plain Python helpers ───────────────────────────────────────────────

    def _js(self, script: str):
        """Call JS — must be on main thread."""
        if self._webview is not None:
            self._webview.evaluateJavaScript_completionHandler_(script, None)

    def _handle(self, action: str, body: dict):
        if action == "close":
            self._cleanup()
            self._app.terminate_(None)

        elif action == "step_changed":
            self._step = body.get("step", 0)
            if self._step == 1:
                # User just arrived at the Permissions page — check both immediately.
                self._js(f"updateAxStatus({json.dumps(_check_ax())})")
                self._js(f"updateMicStatus({json.dumps(_check_mic())})")
            elif self._step == 2:
                # User just arrived at the Model Download page — push installed models.
                self._js(f"setLocalModels({json.dumps(_get_local_models())})")
            elif self._step == 3:
                # User just arrived at the Track Time Saved page — push any saved WPM.
                self._js(f"setExistingWpm({json.dumps(_get_saved_wpm())})")

        elif action == "open_ax":
            # Register this process with the Accessibility subsystem so macOS
            # shows the correct entry for the user to enable in System Settings.
            try:
                from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt  # type: ignore[import]
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            except Exception:
                pass
            # Open directly to Privacy > Accessibility (macOS 13+)
            subprocess.Popen(["open",
                "x-apple.systempreferences:"
                "com.apple.settings.PrivacySecurity.extension"
                "?Privacy_Accessibility"])

        elif action == "open_mic":
            try:
                from AVFoundation import AVCaptureDevice  # type: ignore[import]
                _cls = AVCaptureDevice
            except Exception:
                try:
                    import ctypes, objc
                    ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"
                    )
                    _cls = objc.lookUpClass("AVCaptureDevice")
                except Exception:
                    _cls = None
            if _cls is not None:
                try:
                    _cls.requestAccessForMediaType_completionHandler_(
                        "soun",  # AVMediaTypeAudio
                        lambda granted, _=None: self._js(f"updateMicStatus({json.dumps(bool(granted))})"),
                    )
                except Exception:
                    pass
            subprocess.Popen(["open",
                "x-apple.systempreferences:"
                "com.apple.settings.PrivacySecurity.extension"
                "?Privacy_Microphone"])

        elif action == "start_download":
            if self._downloading:
                return
            model = body.get("model", MODEL_DEFAULT)
            valid_models = {e[0] for e in MODEL_CATALOG}
            self._selected_model = model if model in valid_models else MODEL_DEFAULT
            if body.get("overwrite"):
                dest = MODELS_DIR / self._selected_model
                if dest.exists():
                    try:
                        shutil.rmtree(str(dest))
                    except Exception:
                        pass
            self._downloading = True
            self._download_success = False
            self._download_error = ""
            threading.Thread(target=self._download_worker, daemon=True).start()

            dl_target = self._DlTimerTarget.alloc().init()
            self._ns_refs.append(dl_target)
            self._dl_timer = self._NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.45, dl_target, b"pollDownload:", None, True
            )

        elif action == "get_hotkey":
            self._js(f"setHotkey({json.dumps(_get_hotkey())})")

        elif action == "set_hotkey":
            key = body.get("hotkey", "")
            if not validate_hotkey(key):
                return
            config = {}
            if CONFIG_PATH.exists():
                try:
                    config = json.loads(CONFIG_PATH.read_text())
                except Exception:
                    pass
            config["hotkey"] = key
            _secure_write_json(CONFIG_PATH, config)

        elif action == "start_recorder":
            self._recording = True

        elif action == "cancel_recorder":
            self._recording = False

        elif action == "open_model_hf":
            model_id = body.get("id", "")
            valid_models = {e[0] for e in MODEL_CATALOG}
            if model_id in valid_models:
                subprocess.Popen(["open", f"https://huggingface.co/Systran/faster-whisper-{model_id}"])

        elif action == "open_model_folder":
            model_id = body.get("id", "")
            valid_models = {e[0] for e in MODEL_CATALOG}
            if model_id in valid_models:
                folder = MODELS_DIR / model_id
                try:
                    safe = folder.exists() and folder.resolve().is_relative_to(MODELS_DIR.resolve())
                except (ValueError, OSError):
                    safe = False
                if safe:
                    # Reveal the model.bin file in Finder (selects it)
                    model_bin = folder / "model.bin"
                    target = model_bin if model_bin.exists() else folder
                    subprocess.Popen(["open", "-R", str(target)])


        elif action == "save_wpm":
            try:
                wpm = int(body.get("wpm", 0))
                if wpm > 0:
                    stats_path = CONFIG_DIR / "stats.json"
                    stats: dict = {"typing_wpm": 0, "days": {}}
                    if stats_path.exists():
                        try:
                            d = json.loads(stats_path.read_text())
                            if isinstance(d, dict):
                                stats = d
                        except Exception:
                            pass
                    stats["typing_wpm"] = wpm
                    _secure_write_json(stats_path, stats)
            except Exception:
                pass

        elif action == "finish":
            _secure_write_text(SETUP_FLAG, "1\n")
            self._cleanup()
            self._app.terminate_(None)

    def _download_worker(self):
        dest = MODELS_DIR / self._selected_model
        entry = next((e for e in MODEL_CATALOG if e[0] == self._selected_model), None)
        repo_id = entry[1] if entry else f"Systran/faster-whisper-{self._selected_model}"
        try:
            from huggingface_hub import snapshot_download  # type: ignore[import]
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id,
                local_dir=str(dest),
                ignore_patterns=["*.msgpack", "*.h5", "flax_model*",
                                 "tf_model*", "*.ot", "*.pt"],
            )
            self._download_success = True
        except Exception as e:
            print(f"[setup_wizard] model download failed: {e}", file=sys.stderr)
            self._download_error = "Download failed. Check the log for details."
            if dest.exists():
                try:
                    shutil.rmtree(str(dest))
                except Exception:
                    pass
        self._downloading = False

    def _cleanup(self):
        if self._ax_timer:
            self._ax_timer.invalidate()
        if self._dl_timer:
            self._dl_timer.invalidate()


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def needs_setup() -> bool:
    """Return True if the setup wizard should be shown."""
    return not bool(_get_local_models()) or not SETUP_FLAG.exists()


def run_wizard() -> None:
    """Run the wizard synchronously (blocks until the user finishes or exits)."""
    w = SetupWizard()
    w.run()


def main():
    run_wizard()


if __name__ == "__main__":
    main()
