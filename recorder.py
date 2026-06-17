from __future__ import annotations

import collections
import logging
import queue
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from constants import SAMPLE_RATE   # re-exported (main.py does `from recorder import SAMPLE_RATE`)

logger = logging.getLogger(__name__)

MIN_DURATION_SEC = 0.3


class Recorder:
    """Facade over a mic-based sounddevice input *or* a WASAPI loopback
    capture. Pick the backend via `source` argument to `start()`:
        "mic"    — microphone (default, existing behaviour)
        "system" — speaker loopback (Zoom / YouTube / Discord audio)
    See concept/20_system_audio_loopback.md.
    """

    # Pre-roll: keep the mic stream warm and retain the last N seconds so the
    # first words aren't clipped by device-open latency / speaking just before
    # the hotkey registers.
    PREROLL_SEC = 0.8

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self.current_rms: float = 0.0
        self._gain: float = 1.0
        # If running in loopback mode we delegate to LoopbackRecorder, which
        # has the same public surface. self._delegate is non-None while a
        # loopback session is active.
        self._delegate = None
        # Always-on pre-roll ring buffer (~PREROLL_SEC). Filled by the warm
        # stream even between recordings; seeded into _frames on start().
        self._ring: "collections.deque[np.ndarray]" = collections.deque()
        self._ring_samples = 0
        self._ring_max = int(self.PREROLL_SEC * sample_rate)
        self._capturing = False
        self._mic_index = -2          # -2 = stream never opened
        self._lock = threading.Lock()

    def _ensure_stream(self, mic_index: int) -> None:
        """Open (or reopen on device change) the always-on capture stream."""
        if self._stream is not None and self._mic_index == mic_index:
            return
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            self._ring.clear()
            self._ring_samples = 0
        self._mic_index = mic_index
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=None if mic_index < 0 else mic_index,
            callback=self._callback,
        )
        self._stream.start()

    def start(self, mic_index: int = -1, gain: float = 1.0,
              source: str = "mic") -> None:
        if source == "system":
            from loopback_recorder import LoopbackRecorder
            self._delegate = LoopbackRecorder(self.sample_rate)
            self._delegate.start(mic_index=mic_index, gain=gain)
            return

        self._delegate = None
        self._gain = max(0.1, float(gain))
        self._ensure_stream(mic_index)
        with self._lock:
            # Seed with pre-roll (audio captured just before the press).
            self._frames = list(self._ring)
            self._capturing = True

    @property
    def current_rms_live(self) -> float:
        """RMS works through the delegate when loopback is active."""
        if self._delegate is not None:
            return self._delegate.current_rms
        return self.current_rms

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        # Apply software gain (for whisper-mode quiet speech). Clip protects
        # against saturation when gain is high — better quiet+clean than loud+clipped.
        # Wrapped so an exception never propagates into PortAudio (stream abort).
        try:
            x = indata.copy()
            if self._gain != 1.0:
                x = np.clip(x * self._gain, -1.0, 1.0)
            self.current_rms = float(np.sqrt(np.mean(x ** 2)))
            with self._lock:
                self._ring.append(x)
                self._ring_samples += len(x)
                while self._ring_samples > self._ring_max and len(self._ring) > 1:
                    self._ring_samples -= len(self._ring.popleft())
                if self._capturing:
                    self._frames.append(x)
        except Exception:
            logger.exception("Recorder audio callback failed")

    def snapshot(self) -> np.ndarray | None:
        """Concatenated copy of audio captured so far."""
        if self._delegate is not None:
            return self._delegate.snapshot()
        with self._lock:
            frames = self._frames[:]
        if not frames:
            return None
        return np.concatenate(frames, axis=0).flatten()

    def samples_captured(self) -> int:
        """Sample count captured so far WITHOUT copying/concatenating the buffer
        — a cheap length probe for pacing (matches len(snapshot()))."""
        if self._delegate is not None:
            return self._delegate.samples_captured()
        with self._lock:
            return sum(f.size for f in self._frames)

    def stop(self) -> np.ndarray | None:
        if self._delegate is not None:
            audio = self._delegate.stop()
            self._delegate = None
            return audio

        with self._lock:
            self._capturing = False
            frames = self._frames[:]
            self._frames = []
        # Keep the stream warm (don't close) so the next recording has pre-roll.

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0).flatten()
        if len(audio) / self.sample_rate < MIN_DURATION_SEC:
            return None
        return audio


# ── VAD backends ──────────────────────────────────────────────────────────────

class _WebRtcVad:
    """Lightweight GMM-based VAD from WebRTC (Google). Frame must be 10/20/30ms."""
    FRAME_MS = 30
    FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 480

    def __init__(self, aggressiveness: int) -> None:
        import webrtcvad
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, chunk: np.ndarray) -> bool:
        pcm = (chunk * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        try:
            return self._vad.is_speech(pcm, SAMPLE_RATE)
        except Exception:
            return False


class _TenVad:
    """Modern DNN VAD (TEN-framework, Apache 2.0). Higher precision than WebRTC,
    lower latency than Silero. 256-sample frame (16 ms) at 16 kHz.

    See https://github.com/TEN-framework/ten-vad
    """
    FRAME_MS = 16
    FRAME_SAMPLES = 256
    # webrtcvad aggressiveness 0..3 → threshold mapping (empirical)
    _THRESHOLDS = {0: 0.30, 1: 0.45, 2: 0.60, 3: 0.75}

    def __init__(self, aggressiveness: int) -> None:
        from ten_vad import TenVad
        threshold = self._THRESHOLDS.get(aggressiveness, 0.45)
        self._vad = TenVad(hop_size=self.FRAME_SAMPLES, threshold=threshold)

    def is_speech(self, chunk: np.ndarray) -> bool:
        pcm = (chunk * 32767).clip(-32768, 32767).astype(np.int16)
        try:
            prob, flag = self._vad.process(pcm)
            return bool(flag)
        except Exception:
            return False


def _make_vad(engine: str, aggressiveness: int):
    """Returns (vad, frame_samples). Auto resolves to ten if available, else webrtc."""
    if engine == "auto":
        try:
            v = _TenVad(aggressiveness)
            logger.info("VAD engine: ten-vad (auto-resolved)")
            return v, _TenVad.FRAME_SAMPLES
        except Exception as e:
            logger.info(f"ten-vad unavailable ({e}); falling back to webrtcvad")
            engine = "webrtc"

    if engine == "ten":
        try:
            v = _TenVad(aggressiveness)
            logger.info("VAD engine: ten-vad")
            return v, _TenVad.FRAME_SAMPLES
        except Exception as e:
            logger.warning(f"ten-vad requested but failed to load ({e}); using webrtcvad")

    v = _WebRtcVad(aggressiveness)
    logger.info("VAD engine: webrtcvad")
    return v, _WebRtcVad.FRAME_SAMPLES


# ── Continuous listener ──────────────────────────────────────────────────────

class ContinuousListener:
    """Hands-free VAD listener.

    Architecture:
    - sounddevice InputStream → raw PCM chunks → queue
    - worker thread reads queue, feeds VAD frame-by-frame
    - speech + hangover state machine: on utterance end, calls on_segment(audio)
    - pre-speech ring buffer: includes ~300ms before speech starts so first
      words are not clipped
    """

    _SAMPLE_RATE = SAMPLE_RATE
    _MAX_SPEECH_SEC = 30.0    # 30 s hard cap per utterance

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        aggressiveness: int = 1,
        silence_secs: float = 1.2,
        mic_index: int = -1,
        gain: float = 1.0,
        vad_engine: str = "auto",
        on_frame: "Callable[[np.ndarray], None] | None" = None,
    ) -> None:
        # on_frame: лёгкий отвод сырых (post-gain) кадров из аудио-коллбэка —
        # для аудио-стоп-модели (stop_word.py). Обязан быть дешёвым (только
        # queue.put) и не бросать.
        self._on_frame = on_frame
        self._vad, self._frame_samples = _make_vad(vad_engine, aggressiveness)
        self._frame_ms = self._frame_samples * 1000 // self._SAMPLE_RATE
        self._on_segment = on_segment
        self._mic_index = mic_index
        self._gain = max(0.1, float(gain))
        self._hangover_frames = max(1, round(silence_secs * 1000 / self._frame_ms))
        # Pre-speech ring (~300ms) and max speech length recomputed against
        # actual frame size so the math works for both 16 ms (ten) and 30 ms (webrtc).
        self._pre_speech_frames = max(1, 300 // self._frame_ms)
        self._max_speech_frames = max(1, int(self._MAX_SPEECH_SEC * 1000 / self._frame_ms))
        self._min_speech_frames = max(1, 240 // self._frame_ms)

        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._worker: threading.Thread | None = None
        # Live mic level (post-gain RMS), updated every audio block — the pill's
        # waveform reads this so it bounces with your voice during hands-free.
        self.current_rms: float = 0.0
        # Mirror of the VAD loop's «inside an utterance» state — lets the caller
        # know on stop() whether a final tail segment is still coming (so the
        # hands-free flush waits for it instead of a blind fixed delay).
        self.in_speech: bool = False
        # Set once the worker has fully drained the queue on stop() (the tail
        # _emit, if any, has been spawned). The caller waits on this then on the
        # in-flight transcription before flushing — snappy, no fixed 1.8 s wait.
        self._drained = threading.Event()

    def wait_drained(self, timeout: float = 2.0) -> bool:
        """Block until the worker has processed the stop sentinel (tail emitted,
        if there was one). Returns True if drained, False on timeout."""
        return self._drained.wait(timeout)

    def start(self) -> None:
        self._drained.clear()
        self.in_speech = False
        self._stream = sd.InputStream(
            samplerate=self._SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self._frame_samples,
            device=None if self._mic_index < 0 else self._mic_index,
            callback=self._cb,
        )
        self._worker = threading.Thread(target=self._vad_loop, daemon=True)
        self._worker.start()
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("ContinuousListener stream close failed")
            self._stream = None
        # ALWAYS send the sentinel, even if closing the stream raised — otherwise
        # the worker thread blocks on queue.get() forever (a leaked, wedged
        # daemon holding the mic; continuous mode then can't cleanly restart).
        self._q.put(None)

    def _cb(self, indata: np.ndarray, frames: int, time, status) -> None:
        # Never let an exception propagate into PortAudio — it aborts the stream
        # (and can crash natively). Drop the bad block instead.
        try:
            flat = indata.flatten()
            if self._gain != 1.0:
                flat = np.clip(flat * self._gain, -1.0, 1.0)
            self.current_rms = float(np.sqrt(np.mean(flat ** 2))) if flat.size else 0.0
            chunk = flat.copy()
            self._q.put(chunk)
            if self._on_frame is not None:
                try:
                    self._on_frame(chunk)
                except Exception:
                    logger.debug("on_frame tap failed", exc_info=True)
        except Exception:
            logger.exception("Continuous audio callback failed")

    def _vad_loop(self) -> None:
        ring: collections.deque[np.ndarray] = collections.deque(
            maxlen=self._pre_speech_frames
        )
        speech_frames: list[np.ndarray] = []
        hangover = 0
        in_speech = False

        while True:
            chunk = self._q.get()

            if chunk is None:
                if in_speech and len(speech_frames) >= self._min_speech_frames:
                    try:
                        self._emit(speech_frames)
                    except Exception:
                        logger.exception("VAD final emit failed")
                self.in_speech = False
                self._drained.set()      # queue drained → tail (if any) spawned
                return

            # Guard the whole iteration: a raise here would silently kill the
            # worker thread and wedge hands-free mode until restart (no traceback
            # under pythonw). On error, drop the in-flight utterance and continue.
            try:
                is_speech = self._vad.is_speech(chunk)

                if is_speech:
                    if not in_speech:
                        speech_frames = list(ring) + [chunk]
                        in_speech = True
                    else:
                        speech_frames.append(chunk)
                    hangover = 0

                    if len(speech_frames) >= self._max_speech_frames:
                        self._emit(speech_frames)
                        speech_frames = []

                elif in_speech:
                    speech_frames.append(chunk)
                    hangover += 1
                    if hangover >= self._hangover_frames:
                        if len(speech_frames) >= self._min_speech_frames:
                            self._emit(speech_frames)
                        speech_frames = []
                        hangover = 0
                        in_speech = False
                else:
                    ring.append(chunk)
            except Exception:
                logger.exception("VAD loop iteration failed; resetting utterance")
                speech_frames, hangover, in_speech = [], 0, False

            self.in_speech = in_speech   # mirror for stop()'s tail heuristic

    def _emit(self, frames: list[np.ndarray]) -> None:
        audio = np.concatenate(frames)
        threading.Thread(target=self._on_segment, args=(audio,), daemon=True).start()


class MicMonitor:
    """Live RMS probe of ONE input device for the Settings «test your mic» meter.

    Opens a tiny float32 InputStream on the chosen device and exposes its
    post-nothing RMS via `current_rms` (read it from a Tk after()-loop). The wake
    listener (and any capture) holds the default mic — two streams on the same
    device fight and one reads silence — so `on_acquire`/`on_release` let the
    caller free the mic (pause wake) around the probe. Acquire is idempotent:
    switching devices reopens the stream without releasing the mic.
    """

    def __init__(self, on_acquire: "Callable[[], None] | None" = None,
                 on_release: "Callable[[], None] | None" = None) -> None:
        self._on_acquire = on_acquire
        self._on_release = on_release
        self._stream: sd.InputStream | None = None
        self._acquired = False
        self.current_rms: float = 0.0

    def start(self, device_index: int = -1) -> None:
        """(Re)open the probe stream on `device_index` (-1 = system default).
        Acquires the mic (pauses wake) on first use."""
        self._close_stream()
        if not self._acquired:
            if self._on_acquire:
                try:
                    self._on_acquire()
                except Exception:
                    logger.debug("MicMonitor acquire failed", exc_info=True)
            self._acquired = True
        try:
            dev = None if device_index is None or device_index < 0 else int(device_index)
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=1024, device=dev, callback=self._cb)
            self._stream.start()
        except Exception:
            logger.exception("MicMonitor start failed (device=%s)", device_index)
            self._stream = None
            self.current_rms = 0.0

    def _cb(self, indata, frames, time, status) -> None:
        try:
            flat = indata.reshape(-1)
            self.current_rms = float(np.sqrt(np.mean(flat ** 2))) if flat.size else 0.0
        except Exception:
            self.current_rms = 0.0

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("MicMonitor stream close failed", exc_info=True)
            self._stream = None
        self.current_rms = 0.0

    def stop(self) -> None:
        """Close the probe stream AND release the mic (resume wake)."""
        self._close_stream()
        if self._acquired:
            if self._on_release:
                try:
                    self._on_release()
                except Exception:
                    logger.debug("MicMonitor release failed", exc_info=True)
            self._acquired = False
