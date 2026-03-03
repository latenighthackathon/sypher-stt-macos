"""Audio recorder module for capturing microphone input.

Uses sounddevice to capture audio from the default microphone
as float32 numpy arrays at 16kHz mono — the format Whisper expects.
Thread-safe with bounded recording duration.
"""

import logging
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from sypher_stt.constants import (
    BLOCK_SIZE,
    CHANNELS,
    MAX_RECORDING_SECONDS,
    SAMPLE_RATE,
)

log = logging.getLogger(__name__)


class AudioRecorder:
    """Thread-safe push-to-talk audio recorder.

    Usage:
        recorder = AudioRecorder()
        recorder.start_recording()
        # ... user speaks ...
        audio = recorder.stop_recording()
    """

    def __init__(self, device: Optional[int] = None) -> None:
        """Initialize the recorder.

        Args:
            device: Audio input device index. None = system default.
        """
        self._device = device
        self._chunks: list = []
        self._samples_recorded: int = 0
        self._stream: Optional[sd.InputStream] = None
        self._recording = threading.Event()
        self._lock = threading.Lock()

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Called by sounddevice for each audio block during recording."""
        if status:
            log.warning("Audio stream status: %s", status)
        if not self._recording.is_set():
            return
        with self._lock:
            self._chunks.append(indata[:, 0].copy())
            self._samples_recorded += frames
            if self._samples_recorded >= SAMPLE_RATE * MAX_RECORDING_SECONDS:
                log.warning(
                    "Max recording duration (%ds) reached, auto-stopping.",
                    MAX_RECORDING_SECONDS,
                )
                self._recording.clear()

    def start_recording(self) -> None:
        """Begin capturing audio from the microphone."""
        with self._lock:
            self._chunks = []
            self._samples_recorded = 0
            self._recording.set()
            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    blocksize=BLOCK_SIZE,
                    device=self._device,
                    callback=self._audio_callback,
                )
                self._stream.start()
            except sd.PortAudioError as e:
                self._recording.clear()
                log.error("Failed to open microphone: %s", e)
                raise

    def stop_recording(self) -> np.ndarray:
        """Stop capturing and return the recorded audio.

        Returns:
            Numpy float32 array of audio samples at 16kHz mono.
            Empty array if nothing was recorded.
        """
        # Phase 1: signal stop and take ownership of the stream under the lock,
        # but do NOT call stream.stop()/close() here.  sounddevice blocks in
        # stream.stop() until the current callback invocation returns, and
        # _audio_callback also acquires self._lock — holding the lock across
        # stream.stop() would deadlock.
        with self._lock:
            self._recording.clear()
            stream = self._stream
            self._stream = None

        # Phase 2: stop/close outside the lock so the callback can finish.
        # Any in-flight callback that passed the is_set() guard will acquire
        # the lock, append its chunk, and return before stream.stop() returns.
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log.warning("Error closing audio stream: %s", e)

        # Phase 3: collect chunks (stream is fully stopped, no more callbacks).
        with self._lock:
            if self._chunks:
                audio = np.concatenate(self._chunks)
                self._chunks = []
                duration = len(audio) / SAMPLE_RATE
                log.debug(
                    "Recorded %.1fs of audio (%d samples)", duration, len(audio)
                )
                return audio

            return np.array([], dtype=np.float32)

    @property
    def is_recording(self) -> bool:
        """Whether the recorder is currently capturing audio."""
        return self._recording.is_set()

    @staticmethod
    def list_devices() -> list:
        """Return available audio input devices."""
        devices = sd.query_devices()
        result = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                result.append({"index": i, "name": dev["name"]})
        return result
