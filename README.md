# Sypher STT — macOS

> Privacy-first, push-to-talk voice dictation for macOS. Hold a key, speak, release — transcribed text is pasted instantly. Fully offline and private: transcription runs locally via Faster Whisper, with no API calls, no cloud services, and no audio ever leaving your machine.

---

## How It Works

### Transcription pipeline

```
Hold hotkey → AudioRecorder (sounddevice, 16kHz) → Whisper (faster-whisper, CPU)
           → pyperclip + Cmd+V → text appears in focused window
```

1. **Hold your hotkey** — audio capture starts immediately (16 kHz mono).
2. **Release** — recording stops; audio is passed to Faster Whisper on-device.
3. **Transcription** — text is returned in milliseconds to a few seconds depending on your model.
4. **Auto-paste** — text is written to the clipboard and `Cmd+V` is simulated into the focused window.

---

### Menu bar

| Icon | State |
|------|-------|
| 🎙 | Ready — waiting for hotkey |
| 🔴 | Recording |
| ⏳ | Transcribing |

**Menu items:**

| Item | Description |
|------|-------------|
| Status line | Shows current state and active hotkey |
| Version | `Sypher STT v<version>` |
| **Settings** | Opens the 4-tab settings panel |
| **Setup Wizard** | Re-runs the first-run wizard |
| **Uninstall** | Removes all app data and config |
| **Quit** | Exits the app |

---

### Settings

| Tab | Contents |
|-----|----------|
| **Defaults** | Hotkey picker, microphone device, Whisper model selection (download or switch) |
| **Sounds** | Toggle sound feedback; pick start / stop / error sounds from macOS system sounds |
| **Permissions** | Live status of Accessibility and Microphone grants; links to open System Settings |
| **Stats** | Toggle stats collection; word/character/audio/time-saved cards; filter by week / month / 3 months / all time; bar chart; typing speed test; clear stats; view or clear log |

<div align="center">

![Settings tab](screenshots/settings-tab.png)

![Record shortcut overlay](screenshots/hotkeys.png)

</div>

---

### Stats

All stats are stored locally in:

```
~/Library/Application Support/SypherSTT/stats.json
```

**Recorded per day:** word count, character count, audio duration. No transcribed text or keystrokes are ever written.

**Optional calibration:**
- **Typing speed** — built-in WPM test; result powers the *Est. time saved* card.
- **Earnings rate** — hourly rate or salary to power the *Est. value saved* card.

**From Settings → Stats you can:**
- **Clear stats** — wipes all daily counts (typing speed and earnings rate are preserved)
- **Clear log** — empties `~/Library/Logs/SypherSTT/sypher_stt.log`
- **View log file** — opens the log in your default text editor

The log rotates automatically (5 MB × 3 backups) and records only lifecycle events and per-transcription summaries.

<div align="center">

![Stats tab](screenshots/stats-tab.png)

</div>

---

## Features

- **Menu bar app** — no Dock icon, lives quietly in the status bar
- **Push-to-talk** — hold a configurable hotkey (default: F8) to record
- **100% private & local** — Whisper runs on your Mac; no audio, text, or data ever leaves your machine
- **Apple Silicon optimized** — CTranslate2 uses NEON SIMD for fast CPU inference
- **Auto-paste** — transcribed text is pasted into whatever window is focused
- **Sound feedback** — configurable macOS system sounds for start / stop / error events
- **Live icon** — menu bar icon changes with state: 🎙 → 🔴 → ⏳
- **Setup wizard** — first-run wizard walks you through permissions, model download, and hotkey setup
- **Settings UI** — four-tab panel for hotkey, model, mic, sounds, permissions, and stats
- **Stats** — local-only usage tracking with words transcribed, audio duration, and estimated time saved
- **Typing speed test** — built-in WPM test to calibrate time-saved estimates
- **Update check** — background check against GitHub releases; notifies when a new version is available

---

## Privacy & Security

- **No network access during transcription.** Whisper runs entirely on your CPU via [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No API calls, no servers, no audio transmitted anywhere.
- **Your audio never leaves your machine.** Audio is captured and processed in local memory, then immediately discarded — never written to disk, never sent over a network.
- **No telemetry, no analytics, no accounts.** No tracking, no login. The only outbound call is a version check against `api.github.com` when Settings opens — it sends only the app version. No audio, transcriptions, or personal data are ever transmitted.
- **Usage stats are local-only and opt-out.** Only aggregate counts (words, characters, audio duration per day) are recorded — no transcribed text or keystrokes. Disable in **Settings → Stats**.
- **Everything stays on your machine.** Config, stats, and logs live under `~/Library/Application Support/SypherSTT/` and `~/Library/Logs/SypherSTT/` with user-only (`600`) file permissions.

<div align="center">

![App log output](screenshots/minimal-app-logs.png)

*Logs are privacy minded and only show # of characters transcribed + duration of the input audio*

</div>

---

## Quick Start

```bash
# 1. In Terminal, copy/paste these commands:
git clone https://github.com/latenighthackathon/sypher-stt-macos
cd sypher-stt-macos

# 2. Run (creates venv + launches setup wizard automatically)
chmod +x run.sh
./run.sh
```

---

## Manual Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Download a model (required before first run)
python scripts/download_model.py base.en

# Start
python -m sypher_stt.app
```

---

## Updating

### Check for updates

Open the menu bar icon → **Settings**. The sidebar shows a **↺ Check for Updates** button. If a newer version is available, an update badge appears with instructions.

### How to update

1. Copy the command:

```bash
cd sypher-stt-macos && git pull && ./run.sh
```

2. Quit Sypher STT from the menu bar icon → **Quit**
3. Quit your current Terminal window and open a new one
4. Paste the command and press Enter

---

## Requirements

- **macOS 15 Sequoia or macOS 26 Tahoe** (Apple Silicon or Intel)
- **Python 3.9+**
- **Microphone access**
- **Accessibility permission** — required for global hotkey capture (see [below](#accessibility-permission-required-for-hotkey))

**Python dependencies** (installed automatically by `run.sh` or `pip install -e .`):

| Package | Purpose |
|---------|---------|
| `faster-whisper` | Local Whisper speech recognition engine |
| `sounddevice` | Microphone audio capture |
| `numpy` | Audio buffer processing |
| `pynput` | Global hotkey listener |
| `pyperclip` | Clipboard integration |
| `rumps` | macOS menu bar app framework |
| `pyobjc-framework-WebKit` | Native macOS settings UI (WKWebView) |
| `huggingface-hub` *(optional)* | First-run model download |

---

## Models

All models use [Faster Whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) and run fully offline.

| Model | Size | Description |
|-------|------|-------------|
| `tiny.en` | ~75 MB | Fastest · Best for quick notes |
| `base.en` | ~142 MB | Fast · Good accuracy for everyday dictation ✓ **recommended** |
| `small.en` | ~466 MB | Balanced speed and accuracy |
| `medium.en` | ~1.5 GB | High accuracy · Best for complex or accented speech |
| `large-v3` | ~3.1 GB | 1,550M params · Highest accuracy available · Slowest |
| `large-v2` | ~3.1 GB | 1,550M params · Near Large v3 accuracy · Predecessor to v3 |

```bash
# List all models and their local status
python scripts/download_model.py --list

# Download a specific model
python scripts/download_model.py small.en
```

Models are downloaded from [Hugging Face](https://huggingface.co/Systran) and stored in `models/` at the project root by default. Set `SYPHER_MODELS_DIR` to override — must be an absolute path inside `~/Library/Application Support/SypherSTT/`.

---

## Accessibility Permission (required for hotkey)

macOS requires **Accessibility permission** for any app that monitors global keyboard events.

1. Open **System Settings → Privacy & Security → Accessibility**
2. Add your **Terminal** (or whichever app you launch Sypher STT from) and toggle it **on**
3. Restart Sypher STT

If skipped, the hotkey won't fire (the menu bar icon will still appear). The setup wizard covers this on first run.

---

## Configuration

Settings are stored in:
```
~/Library/Application Support/SypherSTT/config.json
```

| Key | Default | Description |
|-----|---------|-------------|
| `hotkey` | `"f8"` | Push-to-talk key. Supports F1–F12, Caps Lock, and modifier combos (e.g. `"option+space"`, `"ctrl+shift+f1"`). |
| `model` | `"base.en"` | Whisper model to use. Must be one of the supported model IDs. |
| `audio_device` | `null` | Input device index (`null` = system default). |
| `sound_feedback` | `true` | Play system sounds on record start / stop / error. |
| `sound_start` | `"Ping"` | macOS system sound played when recording starts. |
| `sound_stop` | `"Blow"` | macOS system sound played when recording stops. |
| `sound_error` | `"Basso"` | macOS system sound played on transcription error. |
| `record_stats` | `true` | Record per-session word count, character count, and audio duration. When `false`, nothing is written to `stats.json` or the log. |

Changes are picked up within 3 seconds without restarting.

---

## License

MIT © 2026 [LateNightHackathon](https://github.com/latenighthackathon)

Free to use, modify, distribute, and sublicense — see [LICENSE](LICENSE) for the full text.
