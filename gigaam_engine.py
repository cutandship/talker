"""GigaAM v2/v3 STT-движок через onnx-asr (концепт 29).

Ленивый импорт onnx_asr — без onnxruntime в
рантайме приложение не падает на старте. GigaAM — только русский, без
initial_prompt/biasing (словарь концепта 05 на нём не действует).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

from constants import SAMPLE_RATE

logger = logging.getLogger(__name__)


def _bundled_model_dir(model_name: str) -> "Path | None":
    """Plain bundled model folder next to the exe (build.py --bundle-model writes
    models/<name>). Loading via this path keeps onnx_asr fully OFFLINE — no HF
    cache, no snapshot symlinks (which don't survive a copy to another PC)."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    d = base / "models" / model_name
    return d if d.is_dir() else None

# GigaAM is trained/profiled on speech segments up to 30 s (onnx_asr's
# profile_max_shapes: waveform_len_ms=30_000, opt=20_000). Feeding a longer
# clip to recognize() in one shot makes the RNNT decoder degrade or drop the
# tail — long dictations come back truncated. So we split anything longer than
# _MAX_CHUNK_SEC into pieces at the quietest point (a natural pause) inside the
# [_SPLIT_FROM_SEC, _MAX_CHUNK_SEC] window, recognize each, and join. Cutting on
# silence means no word straddles a boundary, so no overlap/dedup is needed.
_SR = SAMPLE_RATE
_MAX_CHUNK_SEC = 28.0      # hard ceiling per recognize() call (< 30 s limit)
_SPLIT_FROM_SEC = 20.0     # earliest point to look for a pause to cut on
_FRAME_SEC = 0.02          # 20 ms energy frames for pause detection


class GigaamEngine:
    """Тонкая обёртка над onnx_asr.load_model(...).recognize(audio)."""

    def __init__(self, model_name: str = "gigaam-v2-ctc",
                 quantization: str | None = "int8") -> None:
        self._model_name = model_name
        self._quantization = quantization   # int8 = меньше диск/RAM на CPU
        self._model = None
        # Lazy load isn't otherwise serialized: warmup() (bg thread) and
        # transcribe() (foreground) can both enter _get() at once and load the
        # weights twice (double RAM, racing cache writes). Guard the load.
        self._load_lock = threading.Lock()

    def _get(self):
        with self._load_lock:
            if self._model is None:
                import onnx_asr  # ленивый: onnxruntime (+ качает веса, если не вшито)
                local = _bundled_model_dir(self._model_name)
                try:
                    if local is not None:
                        # Вшитая плоская папка → офлайн через path=, без сети.
                        self._model = onnx_asr.load_model(
                            self._model_name, str(local), quantization=self._quantization)
                    else:
                        self._model = onnx_asr.load_model(
                            self._model_name, quantization=self._quantization)
                    logger.info(
                        f"GigaAM loaded: {self._model_name} "
                        f"(q={self._quantization}, local={local is not None})")
                except Exception as e:
                    # Не у всех вариантов (напр. некоторых v3) есть int8-веса —
                    # падать нельзя. Грузим в полной точности как фолбэк.
                    if self._quantization is not None:
                        logger.warning(
                            f"GigaAM {self._model_name} q={self._quantization} "
                            f"failed ({e}); retrying without quantization")
                        self._model = (onnx_asr.load_model(self._model_name, str(local))
                                       if local is not None
                                       else onnx_asr.load_model(self._model_name))
                        logger.info(f"GigaAM loaded: {self._model_name} (q=none)")
                    else:
                        raise
        return self._model

    def warmup(self) -> None:
        """Load the model NOW instead of lazily on the first transcribe — keeps
        the first dictation from stalling on a ~2 s model load."""
        self._get()

    def set_vocabulary(self, words: list[str]) -> None:
        # GigaAM не поддерживает initial_prompt/biasing — no-op для совместимости
        # с интерфейсом Transcriber (словарь концепта 05 здесь не применяется).
        return

    def transcribe(self, audio) -> str:
        # onnx_asr.recognize принимает numpy float32 PCM; sample_rate явно 16 кГц
        # (как отдаёт recorder). Контракт подтверждён интроспекцией onnx-asr 0.11.
        model = self._get()
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)

        # Short clip → single shot (unchanged fast path).
        if len(audio) <= int(_MAX_CHUNK_SEC * _SR):
            return (model.recognize(audio, sample_rate=_SR) or "").strip()

        # Long dictation → split on pauses so the tail isn't dropped.
        chunks = self._split_long(audio)
        logger.info(
            f"Long audio {len(audio)/_SR:.0f}s → {len(chunks)} chunks "
            f"(GigaAM 30s limit)")
        parts: list[str] = []
        for idx, ch in enumerate(chunks):
            # Per-chunk guard: if one chunk fails (onnx hiccup, odd length), keep
            # the others instead of losing the whole dictation. Partial text beats
            # no text — this is the resilient version of an incremental cache.
            try:
                txt = (model.recognize(ch, sample_rate=_SR) or "").strip()
            except Exception:
                logger.warning(f"chunk {idx + 1}/{len(chunks)} failed; keeping rest",
                               exc_info=True)
                continue
            if txt:
                parts.append(txt)
        return " ".join(parts).strip()

    @staticmethod
    def _split_long(audio: np.ndarray) -> list[np.ndarray]:
        """Split long audio into ≤_MAX_CHUNK_SEC pieces, cutting at the quietest
        20 ms frame inside [_SPLIT_FROM_SEC, _MAX_CHUNK_SEC] of each piece so the
        boundary lands in a pause. The pieces exactly tile the input — no samples
        lost, no overlap (verified by concatenation)."""
        max_n = int(_MAX_CHUNK_SEC * _SR)
        from_n = int(_SPLIT_FROM_SEC * _SR)
        frame = max(1, int(_FRAME_SEC * _SR))
        n = len(audio)
        out: list[np.ndarray] = []
        i = 0
        while i < n:
            if n - i <= max_n:
                out.append(audio[i:])
                break
            lo, hi = i + from_n, i + max_n
            # Quietest frame in [lo, hi) → cut at its centre (a natural pause).
            # Vectorized: reshape the window into whole 20 ms frames, take the
            # mean energy of each at once, argmin = first quietest frame (same
            # tie-break as the old strict-`<` loop). Kept in float32 like before.
            nf = (hi - lo) // frame
            if nf > 0:
                window = audio[lo:lo + nf * frame].reshape(nf, frame)
                energies = np.mean(window ** 2, axis=1)
                k = int(np.argmin(energies))
                cut = lo + k * frame + frame // 2
            else:
                cut = hi
            out.append(audio[i:cut])
            i = cut
        return out
