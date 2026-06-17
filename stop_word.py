"""Аудио-стоп для hands-free: openWakeWord-модели («стоп-стоп», «Талкер,
стоп») поверх кадров continuous-листенера.

Зачем: текстовый стоп ждёт паузу VAD (~1.2 с) + транскрипцию — итого 2–3 с
между «стоп» и реакцией. Аудио-детект срабатывает прямо во время фразы.

Архитектура: во время сессии wake-слушатель на паузе (микрофон занят
continuous-листенером), поэтому стоп-модель питается кадрами самого листенера
через лёгкий отвод on_frame: аудио-коллбэк только кладёт чанк в очередь, вся
работа (накопление до 80-мс кадров, инференс) — в отдельном потоке.

Watcher одноразовый: создаётся на сессию, close() на её конце. Текстовый
«стоп-стоп» (voice_gate) остаётся работать параллельно как фолбэк.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 1280          # 80 мс @ 16 kHz — шаг openWakeWord


class StopWordWatcher:
    def __init__(self,
                 model_paths: list[str],
                 on_stop: Callable[[str], None],
                 threshold: float = 0.6,
                 patience: int = 2) -> None:
        """model_paths: пути к .onnx ИЛИ имена встроенных моделей openWakeWord
        (для обкатки водопровода годится и "hey_jarvis")."""
        from openwakeword.model import Model
        self._model = Model(wakeword_models=list(model_paths),
                            inference_framework="onnx")
        self._on_stop = on_stop
        self._threshold = float(threshold)
        self._patience = max(1, int(patience))
        self._consec = 0
        self._fired = False
        self._buf = np.zeros(0, dtype=np.float32)
        self._q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="stop-word")
        self._thread.start()
        logger.info(f"Stop-word watcher: models={list(model_paths)} "
                    f"thr={self._threshold} patience={self._patience}")

    # Из аудио-коллбэка листенера: НИКАКОЙ работы, только в очередь.
    def feed(self, chunk: np.ndarray) -> None:
        if not self._closed.is_set():
            self._q.put(chunk)

    def close(self) -> None:
        self._closed.set()
        self._q.put(None)

    def _loop(self) -> None:
        while True:
            chunk = self._q.get()
            if chunk is None or self._closed.is_set():
                return
            try:
                self._buf = np.concatenate([self._buf, chunk.astype(np.float32)])
                while len(self._buf) >= FRAME_SAMPLES:
                    frame, self._buf = (self._buf[:FRAME_SAMPLES],
                                        self._buf[FRAME_SAMPLES:])
                    self._infer(frame)
            except Exception:
                logger.exception("stop-word loop failed")
                self._buf = np.zeros(0, dtype=np.float32)

    def _infer(self, frame: np.ndarray) -> None:
        pcm = (frame * 32767).clip(-32768, 32767).astype(np.int16)
        pred = self._model.predict(pcm)
        if not pred:
            return
        name = max(pred, key=pred.get)
        score = float(pred[name])
        if score > self._threshold:
            # Как и у wake: одиночный кадр выше порога — не детект; настоящая
            # фраза держит score несколько кадров подряд.
            self._consec += 1
            if self._consec >= self._patience and not self._fired:
                self._fired = True       # одноразовый: сессия сейчас закроется
                logger.info(f"Stop word detected: {name} (score={score:.2f})")
                try:
                    self._on_stop(name)
                except Exception:
                    logger.exception("on_stop handler failed")
        else:
            self._consec = 0
