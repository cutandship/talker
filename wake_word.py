"""Wake-word activation via openWakeWord.

Optional — install with:
    pip install openwakeword onnxruntime

Pre-trained models that come with the library:
    "hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "weather"

For custom wake words ("эй талкер" etc.) train via openWakeWord's recipe and
point `model_paths` at your .onnx/.tflite file.

See concept/21_wake_word.md.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from constants import SAMPLE_RATE

logger = logging.getLogger(__name__)


FRAME_SAMPLES = 1280     # 80 ms — openWakeWord stride


class WakeWordListener:
    """Polls the default microphone, runs openWakeWord per frame, calls
    `on_wake()` when any model exceeds `threshold`. Has a built-in cooldown
    to prevent self-retriggering."""

    def __init__(self,
                 on_wake: Callable[[str], None],
                 model_name: str = "hey_jarvis",
                 threshold: float = 0.5,
                 cooldown_sec: float = 3.0,
                 mic_index: int = -1,
                 model_paths: list[str] | None = None) -> None:
        self._on_wake = on_wake
        self._model_name = model_name
        self._threshold = float(threshold)
        self._cooldown = float(cooldown_sec)
        self._mic_index = mic_index
        self._model_paths = model_paths
        self._stream = None
        self._model = None
        self._stop = threading.Event()
        self._last_trigger: float = 0.0
        self._thread: threading.Thread | None = None
        # Кадров подряд выше порога (см. _process): одиночный пик — не wake.
        self._consec = 0
        # ONNX inference runs OFF the PortAudio callback (see _callback/_loop):
        # heavy work in the real-time audio thread can stall it and glitch input.
        self._q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self._worker: threading.Thread | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        try:
            from openwakeword.model import Model
        except ImportError as e:
            raise RuntimeError(
                "openwakeword не установлен. Чтобы включить wake word:\n"
                "    pip install openwakeword onnxruntime\n"
                "и перезапусти Talker."
            ) from e

        # Either a custom file path or pick a bundled pre-trained model.
        if self._model_paths:
            self._model = Model(wakeword_models=self._model_paths,
                                inference_framework="onnx")
        else:
            self._model = Model(wakeword_models=[self._model_name],
                                inference_framework="onnx")

        import sounddevice as sd
        self._q = queue.Queue()
        self._stop.clear()
        self._start_worker()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=None if self._mic_index < 0 else self._mic_index,
            callback=self._callback,
        )
        self._stream.start()
        logger.info(f"Wake word listener started: model={self._model_name} "
                    f"threshold={self._threshold}")

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)        # terminate the inference worker
        self._worker = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Wake stream close failed")
            self._stream = None

    def pause(self) -> None:
        """Close the audio stream but KEEP the loaded model, so resume() is
        instant. Frees the microphone while a dictation session runs — the wake
        and continuous listeners can't both hold the same input device (the
        second stream just gets silence). Call from a thread OTHER than the audio
        callback."""
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Wake pause failed")
            self._stream = None

    def resume(self) -> None:
        """Reopen the audio stream (model stays loaded). No-op if not started or
        already running."""
        if self._model is None or self._stream is not None:
            return
        import sounddevice as sd
        # CRITICAL: clear the model's rolling audio buffer. openWakeWord keeps the
        # last ~1.5 s of audio embeddings; the frames captured right before pause()
        # were the user's OWN «Hey Jarvis» + dictation, which score ~0.99 the
        # instant we resume and re-trigger a session by themselves («сама
        # запускается»). reset() drops that stale context.
        try:
            self._model.reset()
        except Exception:
            logger.debug("wake model reset failed", exc_info=True)
        # Belt-and-suspenders: a fresh cooldown so the first frames after resume
        # can't fire even if reset() is unavailable.
        self._last_trigger = time.monotonic()
        self._consec = 0
        # drop frames queued during pause, then ensure the worker is running
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._stop.clear()
        self._start_worker()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=None if self._mic_index < 0 else self._mic_index,
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Wake word listener resumed")

    # ── Audio callback (real-time thread): enqueue only ──────────────────────

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        # NO heavy work here: ONNX predict() in the PortAudio callback can block
        # the real-time thread and cause input overruns. Just hand the frame to
        # the worker (stop_word.py follows the same rule).
        if self._stop.is_set() or self._model is None:
            return
        # .copy(): sounddevice reuses the indata buffer across callbacks.
        self._q.put(indata.reshape(-1).copy())

    # ── Inference worker (own thread) ────────────────────────────────────────

    def _start_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, daemon=True,
                                            name="wake-word")
            self._worker.start()

    def _loop(self) -> None:
        while True:
            audio = self._q.get()
            if audio is None:
                return
            if self._stop.is_set() or self._model is None:
                continue
            self._process(audio)

    def _process(self, audio: np.ndarray) -> None:
        try:
            # openWakeWord expects int16 numpy mono
            prediction = self._model.predict(audio)
            now = time.monotonic()
            if now - self._last_trigger < self._cooldown:
                return
            name = max(prediction, key=prediction.get) if prediction else ""
            score = float(prediction.get(name, 0.0)) if name else 0.0
            if score > self._threshold:
                # Настоящее «Hey Jarvis» держит score высоким несколько кадров
                # подряд (шаг 80 мс); одиночный кадр — случайный выброс (хлопок,
                # пик в чужой речи) и wake не открывает.
                self._consec += 1
                if self._consec < 3:
                    return
                self._consec = 0
                self._last_trigger = now
                logger.info(f"Wake triggered: {name} (score={score:.2f})")
                try:
                    self._on_wake(name)
                except Exception:
                    logger.exception("on_wake handler failed")
            else:
                self._consec = 0
        except Exception:
            logger.exception("Wake predict error")
