"""Download a Whisper model for local use.

Downloads CTranslate2-format models from HuggingFace into the models/ directory.
Only needs to be run once per model. After download, the app runs fully offline.

Usage:
    python scripts/download_model.py              # downloads base.en (default)
    python scripts/download_model.py small.en     # downloads small.en
    python scripts/download_model.py --list       # list available models
"""

import argparse
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

MODEL_REPOS: dict = {
    "tiny.en":          "Systran/faster-whisper-tiny.en",
    "tiny":             "Systran/faster-whisper-tiny",
    "base.en":          "Systran/faster-whisper-base.en",
    "base":             "Systran/faster-whisper-base",
    "small.en":         "Systran/faster-whisper-small.en",
    "small":            "Systran/faster-whisper-small",
    "medium.en":        "Systran/faster-whisper-medium.en",
    "medium":           "Systran/faster-whisper-medium",
    "large-v2":         "Systran/faster-whisper-large-v2",
    "large-v3":         "Systran/faster-whisper-large-v3",
    "large-v3-turbo":   "Systran/faster-whisper-large-v3-turbo",
}

MODEL_SIZES: dict = {
    "tiny.en":          "~75 MB",
    "tiny":             "~75 MB",
    "base.en":          "~142 MB",
    "base":             "~142 MB",
    "small.en":         "~466 MB",
    "small":            "~466 MB",
    "medium.en":        "~1.5 GB",
    "medium":           "~1.5 GB",
    "large-v2":         "~2.9 GB",
    "large-v3":         "~2.9 GB",
    "large-v3-turbo":   "~1.5 GB",
}


def list_models() -> None:
    """Print available models with sizes and local status."""
    print("\nAvailable Whisper models:\n")
    print(f"  {'Model':<20} {'Size':<12} Status")
    print(f"  {'─' * 20} {'─' * 12} {'─' * 12}")
    for name, size in MODEL_SIZES.items():
        local_path = MODELS_DIR / name
        status = "✓ installed" if (local_path / "model.bin").exists() else ""
        print(f"  {name:<20} {size:<12} {status}")
    print()


def download_model(model_name: str) -> None:
    """Download a model from HuggingFace to the local models/ directory."""
    if model_name not in MODEL_REPOS:
        print(f"Error: Unknown model '{model_name}'.")
        print(f"Available: {', '.join(MODEL_REPOS.keys())}")
        sys.exit(1)

    dest = MODELS_DIR / model_name
    if (dest / "model.bin").exists():
        print(f"Model '{model_name}' is already downloaded at {dest}")
        return

    repo = MODEL_REPOS[model_name]
    size = MODEL_SIZES.get(model_name, "unknown size")
    print(f"Downloading '{model_name}' ({size}) from {repo}...")
    print(f"Destination: {dest}\n")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("Error: huggingface_hub is required. Install with:")
        print("  pip install huggingface-hub")
        sys.exit(1)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo, local_dir=str(dest))
    print(f"\nDone! Model '{model_name}' downloaded to {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Whisper models for Sypher STT.",
    )
    parser.add_argument(
        "model",
        nargs="?",
        default="base.en",
        help="Model to download (default: base.en)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available models and exit",
    )
    args = parser.parse_args()

    if args.list:
        list_models()
        return

    download_model(args.model)


if __name__ == "__main__":
    main()
