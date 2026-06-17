"""WASAPI loopback recorder — captures whatever is playing on the speakers
without a virtual audio cable. Lets Talker transcribe Zoom/Teams/Discord
audio (the other side) in addition to (or instead of) the microphone.

Lazy import of pyaudiowpatch — Talker itself doesn't depend on it.
    pip install pyaudiowpatch

See concept/20_system_audio_loopback.md.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from constants import SAMPLE_RATE

logger = logging.getLogger(__name__)

MIN_DURATION_SEC = 0.3


class LoopbackRecorder:
    """API-compatible with Recorder: start(), stop(), snapshot(), current_rms.

    Captures the *default speaker loopback* device via WASAPI. Resamples if the
    device's native sample rate is something other than 16 kHz so the rest of
    the pipeline (Whisper, VAD) keeps working.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._frames: list[np.ndarray] = []
        self._stream: Any | None = None
        self._pa: Any | None = None
        self.current_rms: float = 0.0
        self._gain: float = 1.0
        self._native_rate: int = sample_rate
        self._native_channels: int = 1
        self._pa_continue: int = 0   # pa.paContinue (0); real value set in start()
        self._lock = threading.Lock()   # guards _frames (callback vs snapshot/stop)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self, mic_index: int = -1, gain: float = 1.0) -> None:
        """`mic_index` is ignored — we always grab the default loopback device.
        `gain` is applied software-side to the captured samples."""
        try:
            import pyaudiowpatch as pa
        except ImportError as e:
            raise RuntimeError(
                "pyaudiowpatch не установлен. Чтобы писать системный звук:\n"
                "    pip install pyaudiowpatch\n"
                "и перезапусти Talker."
            ) from e

        self._frames = []
        self._gain = max(0.1, float(gain))
        self._pa_continue = pa.paContinue   # cache: avoid re-import in callback
        self._pa = pa.PyAudio()

        try:
            dev = self._pa.get_default_wasapi_loopback()
        except OSError as e:
            self._pa.terminate()
            self._pa = None
            raise RuntimeError(
                f"WASAPI loopback недоступен: {e}. "
                "Запусти Talker под учёткой с правом на аудио."
            ) from e

        self._native_rate = int(dev["defaultSampleRate"])
        self._native_channels = max(1, int(dev["maxInputChannels"]))

        # We always ask for float32; channels = native (we downmix in callback).
        self._stream = self._pa.open(
            format=pa.paFloat32,
            channels=self._native_channels,
            rate=self._native_rate,
            input=True,
            frames_per_buffer=1024,
            input_device_index=int(dev["index"]),
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        logger.info(
            f"Loopback recording from {dev['name']} "
            f"({self._native_rate} Hz, {self._native_channels} ch)"
        )

    def _callback(self, in_data: bytes, frame_count: int,
                  time_info: dict, status: int):
        try:
            audio = np.frombuffer(in_data, dtype=np.float32)
            # Downmix to mono if needed
            if self._native_channels > 1:
                audio = audio.reshape(-1, self._native_channels).mean(axis=1)
            if self._gain != 1.0:
                audio = np.clip(audio * self._gain, -1.0, 1.0)
            buf = audio.copy()
            if buf.size:
                self.current_rms = float(np.sqrt(np.mean(buf ** 2)))
            with self._lock:                 # only the shared-list mutation
                self._frames.append(buf)
        except Exception:
            logger.exception("Loopback callback error")
        return (None, self._pa_continue)

    def snapshot(self) -> np.ndarray | None:
        with self._lock:
            frames = self._frames[:]
        if not frames:
            return None
        audio = np.concatenate(frames, axis=0)
        return self._resample_if_needed(audio)

    def samples_captured(self) -> int:
        """Cheap captured-sample count (no copy) for pacing probes — scaled to
        the 16 kHz OUTPUT rate so it matches len(snapshot()). snapshot()
        resamples from the device's native rate (often 44.1/48 kHz), so the raw
        native count would over-report ~3x and skew the streaming cadence."""
        with self._lock:
            native = sum(f.size for f in self._frames)
        if self._native_rate == self.sample_rate:
            return native
        return int(round(native * self.sample_rate / self._native_rate))

    def stop(self) -> np.ndarray | None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                logger.exception("Loopback stream close error")
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

        with self._lock:
            frames = self._frames[:]
        if not frames:
            return None
        audio = np.concatenate(frames, axis=0)
        if len(audio) / self._native_rate < MIN_DURATION_SEC:
            return None
        return self._resample_if_needed(audio)

    # ── Resampling ────────────────────────────────────────────────────────────

    def _resample_if_needed(self, audio: np.ndarray) -> np.ndarray:
        """faster-whisper expects 16 kHz; many speaker devices report 44.1 / 48
        kHz. Cheap linear resample is good enough for STT — quality loss is
        negligible compared to model error."""
        if self._native_rate == self.sample_rate:
            return audio.astype(np.float32, copy=False)
        ratio = self.sample_rate / self._native_rate
        new_len = int(round(len(audio) * ratio))
        if new_len <= 0:
            return audio[:0]
        # np.interp is O(n) and accurate enough for STT
        idx = np.linspace(0, len(audio) - 1, num=new_len)
        return np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
