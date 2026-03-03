"""Whisper transcription engine using faster-whisper.

Wraps the faster-whisper library for local, offline speech-to-text.
Models are loaded from the local models/ directory — no internet required.
On Apple Silicon Macs, CTranslate2 uses NEON SIMD for fast CPU inference.
"""

import logging
import os
import threading
import warnings
from pathlib import Path

import numpy as np

from sypher_stt.constants import AVAILABLE_MODELS, DEFAULT_MODEL, MODELS_DIR

log = logging.getLogger(__name__)


def get_local_models() -> list:
    """Return model names that exist locally in the models/ directory."""
    if not MODELS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in MODELS_DIR.iterdir()
        if d.is_dir() and (d / "model.bin").exists()
    )


class Transcriber:
    """Offline speech-to-text using faster-whisper.

    The model is loaded lazily on first transcription to keep startup fast.
    Models are loaded from the local models/ directory.
    """

    def __init__(self, model_size: str = DEFAULT_MODEL) -> None:
        if model_size not in AVAILABLE_MODELS:
            raise ValueError(
                f"Unknown model '{model_size}'. Choose from: {AVAILABLE_MODELS}"
            )
        self._model_size = model_size
        self._model = None
        self._load_lock = threading.Lock()

    def _get_model_path(self) -> Path:
        """Resolve the local path for the configured model."""
        return MODELS_DIR / self._model_size

    def ensure_model(self) -> None:
        """Load the model from the local models/ directory. Thread-safe."""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return  # Double-check after acquiring lock

            model_path = self._get_model_path()
            if not model_path.exists() or not (model_path / "model.bin").exists():
                raise FileNotFoundError(
                    f"Model '{self._model_size}' not found at {model_path}. "
                    f"Run: python scripts/download_model.py {self._model_size}\n"
                    f"Available locally: {get_local_models()}"
                )

            log.info("Loading model '%s' from %s", self._model_size, model_path)
            from faster_whisper import WhisperModel

            # Cap CPU threads to avoid starving other processes (VS Code, etc.).
            # ctranslate2 defaults to 0 (= all cores) which saturates the system.
            _cpu_threads = min(4, os.cpu_count() or 4)
            self._model = WhisperModel(
                str(model_path),
                device="cpu",
                compute_type="auto",
                local_files_only=True,
                cpu_threads=_cpu_threads,
                num_workers=1,
            )
            log.info("Model '%s' loaded successfully.", self._model_size)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio to text.

        Args:
            audio: Float32 numpy array of audio samples at 16kHz mono.

        Returns:
            Transcribed text string. Empty string if audio is too short.
        """
        if audio.size < 1600:  # < 0.1s
            log.debug("Audio too short (%d samples), skipping.", audio.size)
            return ""

        self.ensure_model()
        with self._load_lock:
            model = self._model  # hold lock to guard against concurrent model_size setter

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            segments, info = model.transcribe(
                audio,
                language="en",
                beam_size=5,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                    speech_pad_ms=200,
                    threshold=0.35,
                ),
            )
            text_parts = [seg.text.strip() for seg in segments]
        result = " ".join(text_parts).strip()
        log.info(
            "Transcribed %d chars from %.1fs audio.",
            len(result),
            audio.size / 16000,
        )
        return result

    @property
    def model_size(self) -> str:
        return self._model_size

    @model_size.setter
    def model_size(self, value: str) -> None:
        if value not in AVAILABLE_MODELS:
            raise ValueError(
                f"Unknown model '{value}'. Choose from: {AVAILABLE_MODELS}"
            )
        if value != self._model_size:
            log.info(
                "Model changed from '%s' to '%s'. Will reload on next use.",
                self._model_size,
                value,
            )
            with self._load_lock:
                self._model_size = value
                self._model = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
