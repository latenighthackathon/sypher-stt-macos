#!/usr/bin/env bash
# build_app.sh — Build SypherSTT.app (a proper macOS .app bundle)
#
# The .app launcher uses a Python environment installed in
# ~/Library/Application Support/SypherSTT/venv/ (TCC-unrestricted).
# This lets macOS launch the app from Finder without hitting the
# Desktop-folder permission barrier that blocks .venv/pyvenv.cfg.
#
# Models are stored alongside the venv in
# ~/Library/Application Support/SypherSTT/models/
# Any existing models in the project's models/ folder are migrated once.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="SypherSTT"
APP_DIR="$SCRIPT_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
VERSION="1.0.0"

LIBRARY_BASE="$HOME/Library/Application Support/SypherSTT"
LIBRARY_VENV="$LIBRARY_BASE/venv"
LIBRARY_MODELS="$LIBRARY_BASE/models"
LIBRARY_PYTHON="$LIBRARY_VENV/bin/python"

echo ""
echo "  Building $APP_NAME.app …"
echo ""

# ── Library venv (TCC-unrestricted location) ──────────────────────────────────
if [[ ! -x "$LIBRARY_PYTHON" ]]; then
    echo "  Creating Python environment in ~/Library …"
    python3 -m venv "$LIBRARY_VENV"
    "$LIBRARY_VENV/bin/pip" install --upgrade pip --quiet
    echo "  Installing dependencies (this may take a minute) …"
    "$LIBRARY_VENV/bin/pip" install "$SCRIPT_DIR[download]" pyobjc-framework-WebKit pyobjc-framework-AVFoundation --quiet
    echo "  ✓ Environment ready."
    echo ""
else
    echo "  Updating Python environment …"
    "$LIBRARY_VENV/bin/pip" install "$SCRIPT_DIR[download]" pyobjc-framework-WebKit pyobjc-framework-AVFoundation --quiet
    echo "  ✓ Environment updated."
    echo ""
fi

# ── Migrate models from project root → Library (one-time) ────────────────────
if [[ -d "$SCRIPT_DIR/models" ]] && [[ "$(ls -A "$SCRIPT_DIR/models" 2>/dev/null)" ]]; then
    if [[ ! -d "$LIBRARY_MODELS" ]] || [[ -z "$(ls -A "$LIBRARY_MODELS" 2>/dev/null)" ]]; then
        echo "  Migrating models to ~/Library …"
        mkdir -p "$LIBRARY_MODELS"
        cp -r "$SCRIPT_DIR/models/"* "$LIBRARY_MODELS/"
        echo "  ✓ Models migrated."
        echo ""
    fi
fi

# ── Bundle skeleton ───────────────────────────────────────────────────────────
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"
printf 'APPL????' > "$CONTENTS/PkgInfo"

# ── Info.plist ────────────────────────────────────────────────────────────────
cat > "$CONTENTS/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>com.sypher.stt</string>
  <key>CFBundleName</key>
  <string>SypherSTT</string>
  <key>CFBundleDisplayName</key>
  <string>SypherSTT</string>
  <key>CFBundleExecutable</key>
  <string>SypherSTT</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>????</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>SypherSTT needs microphone access to record your voice for transcription.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>SypherSTT uses Apple Events to paste transcribed text into the active application.</string>
  <key>LSRequiresNativeExecution</key>
  <true/>
</dict>
</plist>
EOF

# ── App icon (Pillow + iconutil) ──────────────────────────────────────────────
echo "  Generating icon …"
if "$LIBRARY_PYTHON" - "$RESOURCES/AppIcon.icns" << 'PYEOF'
import sys, os, subprocess, tempfile, shutil
out = sys.argv[1]
try:
    from PIL import Image, ImageDraw, ImageFont

    def make_icon(sz):
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = max(1, sz // 20)
        d.ellipse(
            [m, m, sz - m, sz - m],
            fill=(13, 13, 13, 255),
            outline=(191, 245, 73, 255),
            width=max(1, sz // 40),
        )
        fs = int(sz * 0.50)
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Apple Color Emoji.ttc", fs
            )
        except Exception:
            font = ImageFont.load_default()
        bb = d.textbbox((0, 0), "🎙", font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text(
            ((sz - tw) / 2 - bb[0], (sz - th) / 2 - bb[1] - sz * 0.04),
            "🎙",
            font=font,
            embedded_color=True,
        )
        return img

    iconset = tempfile.mkdtemp(suffix=".iconset")
    for sz in [16, 32, 64, 128, 256, 512, 1024]:
        make_icon(sz).save(os.path.join(iconset, f"icon_{sz}x{sz}.png"))
        if sz <= 512:
            make_icon(sz * 2).save(
                os.path.join(iconset, f"icon_{sz}x{sz}@2x.png")
            )
    r = subprocess.run(
        ["iconutil", "-c", "icns", iconset, "-o", out], capture_output=True
    )
    shutil.rmtree(iconset)
    if r.returncode != 0:
        print(r.stderr.decode(), file=sys.stderr)
    sys.exit(r.returncode)
except Exception as e:
    print(e, file=sys.stderr)
    sys.exit(1)
PYEOF
then
    echo "  ✓ Icon created."
else
    echo "  (icon skipped)"
fi

# ── Launcher executable (compiled arm64 Mach-O stub) ─────────────────────────
# A compiled C stub is used instead of a bash script so that:
#   1. macOS TCC correctly identifies the process as SypherSTT (not Python/Terminal)
#   2. The stub stays alive as the registered app so macOS TCC attributes
#      Accessibility and mic requests to SypherSTT.app via the responsible-
#      process chain.  Python runs as a child and inherits this attribution.
#   3. Setting arm64 CPU preference avoids needing `arch -arm64` wrapper.
LAUNCHER_SRC="$(mktemp /tmp/stt_launcher_XXXX.c)"
cat > "$LAUNCHER_SRC" << CSRC
#include <stdlib.h>
#include <unistd.h>
#include <spawn.h>
#include <sys/wait.h>
#include <mach/machine.h>

extern char **environ;

#define PYTHON_BIN "$LIBRARY_PYTHON"
#define MODELS_DIR "$LIBRARY_MODELS"

int main(void) {
    setenv("SYPHER_MODELS_DIR", MODELS_DIR, 1);

    char *const args[] = {PYTHON_BIN, "-m", "sypher_stt.app", NULL};

    /* Spawn Python as a child process.
     * The stub (SypherSTT.app, com.sypher.stt) stays alive as the registered
     * app process — macOS TCC uses it as the responsible parent when Python
     * requests Accessibility, microphone, etc.  Python inherits the responsible-
     * process attribution for both event monitoring (pynput) and event posting
     * (pynput keyboard.Controller Cmd+V paste). */
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    cpu_type_t cpu = CPU_TYPE_ARM64;
    posix_spawnattr_setbinpref_np(&attr, 1, &cpu, NULL);

    pid_t pid = -1;
    int err = posix_spawn(&pid, PYTHON_BIN, NULL, &attr,
                          (char *const *)args, environ);
    posix_spawnattr_destroy(&attr);

    if (err != 0 || pid < 0) return 1;

    /* Wait for Python to exit, then propagate its exit code */
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
CSRC

if clang -arch arm64 -O2 -o "$MACOS/$APP_NAME" "$LAUNCHER_SRC" 2>/dev/null; then
    echo "  ✓ Native launcher compiled (arm64 Mach-O)."
else
    echo "  ⚠ clang not found — using bash script launcher (TCC will show Terminal/Python)"
    cat > "$MACOS/$APP_NAME" << EOF
#!/usr/bin/env bash
# SypherSTT.app launcher — fallback (clang unavailable)
PYTHON="$LIBRARY_PYTHON"
export SYPHER_MODELS_DIR="$LIBRARY_MODELS"
if [[ ! -x "\$PYTHON" ]]; then
    osascript -e 'display alert "SypherSTT" message "Python environment not found.\n\nRun ./build_app.sh from the project folder to rebuild." as warning'
    exit 1
fi
exec arch -arm64 "\$PYTHON" -m sypher_stt.app
EOF
    chmod +x "$MACOS/$APP_NAME"
fi
rm -f "$LAUNCHER_SRC"

# ── Ad-hoc code signature (helps TCC identify the bundle) ────────────────────
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null \
    && echo "  ✓ Signed (ad-hoc)." \
    || echo "  (codesign not available — OK)"

echo ""
echo "  ✓ Built:  $APP_DIR"
echo ""
echo "  Launch:   open \"$APP_DIR\""
echo "  Install:  cp -r \"$APP_DIR\" /Applications/"
echo ""
echo "  First launch: right-click → Open if macOS shows a Gatekeeper warning."
echo ""
