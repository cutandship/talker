from __future__ import annotations

import logging
import os
import threading

import numpy as np

from vocabulary import build_initial_prompt
from constants import SAMPLE_RATE

logger = logging.getLogger(__name__)

_TARGET_RMS = 0.07


def _resolve_cpu_threads(requested: int) -> int:
    """0 → physical-core estimate. os.cpu_count() reports logical (HT) on most
    Intel/AMD chips, so halving gets us a reasonable physical-core count. With
    1 logical CPU we keep 1; with 0 (shouldn't happen) we fall back to 1."""
    if requested > 0:
        return requested
    logical = os.cpu_count() or 1
    return max(1, logical // 2)


class Transcriber:
    """Facade in front of pluggable ASR engines (Whisper / GigaAM).

    The whisper path is unchanged; passing `engine="gigaam"` switches to the
    NeMo-based backend (requires `pip install nemo_toolkit[asr]`).
    """

    def __init__(
        self,
        model_size: str = "small",
        language: str | None = None,
        noise_reduction: bool = False,
        normalize: bool = True,
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 0,
        vocabulary: list[str] | None = None,
        engine: str = "whisper",
        gigaam_model: str = "gigaam-v2-ctc",
        nr_mode: str = "non_stationary",
        nr_strength: float = 0.85,
    ) -> None:
        self.nr_mode = nr_mode
        self.nr_strength = max(0.0, min(1.0, float(nr_strength)))
        self.language = language
        self._normalize = normalize
        self._noise_reduction = noise_reduction
        # Mutable so the app can swap vocabulary without reloading the model.
        self.vocabulary: list[str] = list(vocabulary or [])
        # On-screen context fed into initial_prompt per-utterance (concept 25).
        self.context: str = ""
        # Runtime overrides (whisper-mode sets a more permissive value here).
        self.no_speech_threshold: float = 0.6
        self.engine_name = engine
        self._gigaam = None
        # One model instance is shared by the bg pre-decode thread, the
        # foreground pipeline and the API server. ctranslate2 / NeMo decoding is
        # NOT safe to call concurrently on the same handle (state corruption →
        # native abort). Serialize every inference through this lock.
        self._infer_lock = threading.Lock()

        if engine == "gigaam":
            # GigaAM (ru-only) через onnx-asr — лёгкий onnxruntime-стек.
            # Deliberately NO faster-whisper / torch import on this path: gigaam
            # uses onnx's own device selection and never reads `device`, so we
            # keep the heavy torch+CUDA stack (hundreds of MB + threads) out of
            # the gigaam-on-CPU case entirely.
            from gigaam_engine import GigaamEngine
            self._gigaam = GigaamEngine(model_name=gigaam_model)
            self.model = None
            logger.info(f"Engine: gigaam ({gigaam_model})")
        else:
            # Resolve "auto" here only — whisper actually uses `device`. Both the
            # torch probe and the faster-whisper import pull in torch+CUDA, so
            # they stay scoped to the whisper path.
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                    logger.info(f"Auto device resolved to: {device}")
                except ImportError:
                    device = "cpu"
            from faster_whisper import WhisperModel
            # Precision is AUTO (no UI knob): int8 on CPU (fast/light),
            # float16 on GPU (accurate).
            compute_type = "int8" if device == "cpu" else "float16"
            resolved_threads = _resolve_cpu_threads(cpu_threads) if device == "cpu" else 0
            kwargs: dict = dict(device=device, compute_type=compute_type)
            if device == "cpu":
                kwargs["cpu_threads"] = resolved_threads
            self.model = WhisperModel(model_size, **kwargs)
            logger.info(
                f"WhisperModel loaded: size={model_size} device={device} "
                f"compute={compute_type} cpu_threads={resolved_threads if device == 'cpu' else 'n/a'}"
            )

        if noise_reduction:
            try:
                import noisereduce as nr
                self._nr = nr
                logger.info("noisereduce loaded")
            except ImportError:
                logger.warning("noisereduce not installed, noise_reduction disabled")
                self._noise_reduction = False

    def set_vocabulary(self, words: list[str]) -> None:
        self.vocabulary = list(words)

    def warmup(self) -> None:
        """Force a lazily-loaded engine (gigaam) to load its model NOW, so the
        first real dictation isn't stalled mid-record by the load. Whisper and
        gigaam already loads eagerly in __init__, so this is a no-op for them."""
        if self._gigaam is not None:
            self._gigaam.warmup()

    def transcribe(self, audio: np.ndarray) -> str:
        if self._normalize:
            audio = _normalize(audio)

        if self._noise_reduction:
            try:
                # non_stationary handles changing noise (fans, traffic, AC)
                # noticeably better than stationary on real-world recordings.
                # prop_decrease controls how aggressive the cut is — too high
                # eats speech consonants, too low leaves background hiss.
                audio = self._nr.reduce_noise(
                    y=audio, sr=SAMPLE_RATE,
                    stationary=(self.nr_mode == "stationary"),
                    prop_decrease=self.nr_strength,
                )
            except Exception as e:
                # Keep going with the un-denoised audio (fallback below), but
                # log the traceback: a systematic NR failure (e.g. OOM on a long
                # clip) otherwise hides silently behind str(e) on every utterance.
                logger.error(f"noisereduce failed: {e}", exc_info=True)

        with self._infer_lock:
            if self._gigaam is not None:
                return self._gigaam.transcribe(audio).strip()

            initial_prompt = build_initial_prompt(
                self.vocabulary, self.language, self.context) or None

            # Whisper is known to loop ("повторяю повторяю повторяю…") on long
            # recordings when condition_on_previous_text=True — it keeps echoing
            # its own output back as context. Disable conditioning for clips
            # longer than ~60 s so we lose context-awareness but gain stability.
            audio_sec = len(audio) / SAMPLE_RATE
            long_form = audio_sec > 60.0

            segments, _ = self.model.transcribe(
                audio,
                language=self.language,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 1000},
                initial_prompt=initial_prompt,
                beam_size=5,
                temperature=(0.0, 0.2, 0.4),
                condition_on_previous_text=(not long_form),
                # 2.0 (vs default 2.4) cuts hallucinated repetitions harder.
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.0,
                no_speech_threshold=self.no_speech_threshold,
            )
            return "".join(s.text for s in segments).strip()


def _normalize(audio: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 1e-8:
        return audio
    out = audio * (_TARGET_RMS / rms)
    # RMS scaling can blow peaks past ±1.0 on low-RMS / high-crest input (a
    # quiet take with a sharp consonant or a background thump). Scale the whole
    # signal so the peak sits at 1.0 — a transparent peak-limit that preserves
    # waveform shape, unlike hard clipping (which whisper/onnx dislike).
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:
        out = out / peak
    return out
