from __future__ import annotations

import ctypes
import enum
import logging
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path


def _enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness so tk/pystray render crisply on high-DPI
    screens (4K, scaled laptop displays). Must be called BEFORE any window
    is created. Failure is non-fatal."""
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            logger.debug("_enable_dpi_awareness: suppressed", exc_info=True)


_enable_dpi_awareness()


def _kill_other_talker_instances() -> None:
    """Kill any other Talker python processes so a new launch supersedes the
    old one.

    Detection: any python.exe / pythonw.exe whose **cwd** is our project
    directory AND whose command-line mentions main.py. Sniffing cwd is far
    more reliable than substring-matching cmdline — when Talker is launched
    as `python main.py` from cwd, `cmdline` is just ['python.exe', 'main.py']
    with no absolute path, so a path-substring check finds nothing.

    Requires psutil (pulled in transitively via pycaw)."""
    try:
        import psutil
    except ImportError:
        return
    my_pid = os.getpid()
    here = Path(__file__).resolve().parent
    killed: list[int] = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.info["pid"] == my_pid:
            continue
        name = (p.info.get("name") or "").lower()
        if not (name.startswith("python") or name == "pythonw.exe"):
            continue
        try:
            pcwd = Path(p.cwd()).resolve()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        if pcwd != here:
            continue
        cmd = " ".join(p.info.get("cmdline") or []).lower()
        if "main.py" not in cmd:
            continue
        try:
            p.kill()              # SIGKILL-equivalent on Windows (TerminateProcess)
            killed.append(p.info["pid"])
        except Exception:
            logger.debug("_kill_other_talker_instances: suppressed", exc_info=True)
    if killed:
        # Wait a moment for ports/hooks/mic to release. psutil.wait_procs would
        # be ideal but adds complexity for marginal gain.
        import time as _t
        _t.sleep(0.6)


_kill_other_talker_instances()

# Portable mode: a bundled `models/` folder means we ship offline — gigaam_engine
# loads it locally via path=, and we keep HF off the network entirely.
_EXE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
_bundled_models = _EXE_DIR / "models"
_local_cache = _EXE_DIR / ".cache"
if _local_cache.exists():
    os.environ.setdefault("HF_HOME", str(_local_cache))
if _bundled_models.exists():
    # Defensive: even if some path falls back to HF, never hit the net (model is local).
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
if getattr(sys, "frozen", False):
    # The windowed exe has no stdout/stderr, so huggingface_hub's tqdm progress
    # bar crashes ('NoneType' object has no attribute 'write') while resolving the
    # model. Disable HF progress bars — download progress shows in the tray anyway.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import keyboard
import numpy as np
import pyperclip
import pystray
from PIL import Image, ImageDraw

from cleaner import CleanerChain, build_cleaner_chain
from config import AudioConfig, Config, ReplacementConfig, load_config, save_config
from history_mgr import HistoryManager
from recorder import ContinuousListener, MicMonitor, Recorder, SAMPLE_RATE
from snippets import Snippet, apply_snippets
from backtrack import apply_backtrack
from replacements import (compile_rules, apply_replacements, compile_phonetic,
                          apply_phonetic, default_replacements)
import itn
import injector
import audio_backup
from voice_commands import VoiceCommand, execute_actions, extract_commands
from transcriber import Transcriber
from ui import (
    CancelUndoToast, ClipboardToast, FlowBar, HistoryWindow, LoadingWindow,
    PasteFallbackBubble,
    SettingsWindow, UrlTranscribeWindow, _UiScale, _apply_theme,
    _resolve_fonts,
)

_DIR = Path(__file__).parent
LOG_PATH = _DIR / "talker.log"

# Rotate instead of growing forever: 2 backups × 1 MB. RotatingFileHandler
# rolls over mid-session too, so a chatty week can't balloon the file.
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    handlers=[RotatingFileHandler(str(LOG_PATH), maxBytes=1_000_000,
                                  backupCount=2, encoding="utf-8")],
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Capture the Python stack of EVERY thread on a native crash (access violation
# etc.) into crash.log. The recurring 0xc0000005 in _ctypes.pyd gives no Python
# traceback on its own — faulthandler dumps what each thread was executing at
# the moment of the fault, pinpointing the exact culprit line.
try:
    import faulthandler
    _CRASH_PATH = _DIR / "crash.log"
    # Keep crash.log bounded. Never truncate on start (it must keep the LAST
    # crash for post-mortem) — but once it outgrows 1 MB, shift it aside and
    # start fresh: one generation of history, like the rotating main log.
    try:
        if _CRASH_PATH.exists() and _CRASH_PATH.stat().st_size > 1_000_000:
            os.replace(_CRASH_PATH, _DIR / "crash.log.1")
    except OSError:
        pass
    _CRASH_FP = open(_CRASH_PATH, "a", encoding="utf-8")
    _CRASH_FP.write("\n===== faulthandler armed =====\n")
    _CRASH_FP.flush()
    faulthandler.enable(file=_CRASH_FP, all_threads=True)
except Exception:
    logger.debug("faulthandler enable failed", exc_info=True)

CONTINUOUS_HOTKEY = "ctrl+alt+space"

# Hard cap on a single recording (in case key release is lost in fullscreen
# apps, RDP, UAC, etc.). Recording auto-stops and the pipeline runs as if the
# user had released the key.
#   - hold-режим PTT: потерянный KEY_UP — единственный сценарий «вечной»
#     записи; никто не держит клавишу 20 минут, так что 20 мин — щедрый
#     потолок, после которого это точно залипание, а не диктовка.
#   - toggle / single_shot: пользователь останавливает сам — длинные встречи
#     и лекции легитимны, оставляем 120 мин (16 kHz mono float32 ≈ 64 KB/s,
#     120 мин ≈ 460 MB RAM — терпимо).
MAX_RECORDING_SEC = 7200.0
MAX_RECORDING_HOLD_SEC = 1200.0


class State(enum.Enum):
    LOADING    = "loading"
    IDLE       = "idle"
    RECORDING  = "recording"
    PROCESSING = "processing"
    LISTENING  = "listening"
    ERROR      = "error"

_COLORS = {
    State.LOADING:    "#2196F3",
    State.IDLE:       "#9E9E9E",
    State.RECORDING:  "#F44336",
    State.PROCESSING: "#FF9800",
    State.LISTENING:  "#00BCD4",
    State.ERROR:      "#FF5722",
}

_LABELS = {
    State.LOADING:    "Загрузка модели…",
    State.IDLE:       "Готов",
    State.RECORDING:  "Запись…",
    State.PROCESSING: "Обработка…",
    State.LISTENING:  "Слушаю…",
    State.ERROR:      "Ошибка (см. лог)",
}

_TRAY_TITLES = {s: f"Talker — {v}" for s, v in _LABELS.items()}


class _BgJob:
    """
    Pre-transcribes audio in the background while push-to-talk is held.

    Works on incremental chunks, not the full growing buffer:
      - Every _INTERVAL seconds it takes only the NEW audio since the last pass
        and transcribes that chunk independently.
      - Results accumulate as a list of (end_sample, text) segments.
      - finish() transcribes the remaining tail and joins everything.

    This keeps each background Whisper call bounded to _INTERVAL seconds of
    audio, so a 5-minute recording produces ~60 small calls of 5s each rather
    than one enormous call at the end.
    """
    # Short recordings — frequent (3 s) chunked decodes so the tail is small.
    # Long recordings (> 5 min) — relax to 15 s chunks so we don't pin a CPU
    # core at 100 % for two hours. We still finish in time because the audio
    # is way longer than each chunk's decode.
    _INTERVAL_SHORT   = 3.0
    _INTERVAL_LONG    = 15.0
    _LONG_THRESHOLD_S = 300.0   # 5 min
    _MIN_NEW_SEC      = 1.5
    _SR               = SAMPLE_RATE

    def __init__(self, recorder: Recorder, transcriber: Transcriber,
                 on_chunk=None) -> None:
        """
        on_chunk(text): optional callback invoked from the bg thread each time
        a new chunk has been transcribed. When provided, partial text can be
        streamed into the active app as you speak.
        """
        self._recorder    = recorder
        self._transcriber = transcriber
        self._on_chunk    = on_chunk
        self._lock        = threading.Lock()
        self._segments:   list[tuple[int, str]] = []   # (end_sample, text)
        self._stop        = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Adaptive interval: snappy chunks while the recording is short,
            # relaxed cadence once it gets long.
            # Cheap length probe (no buffer copy) just to pick the cadence; the
            # actual audio is snapshotted once, after the wait.
            cur_len = self._recorder.samples_captured() / self._SR
            interval = (self._INTERVAL_LONG if cur_len > self._LONG_THRESHOLD_S
                        else self._INTERVAL_SHORT)
            # Event-wait, not sleep: finish() wakes us instantly instead of
            # waiting out the rest of a 3–15 s nap.
            if self._stop.wait(interval):
                break

            if self._stop.is_set():
                break
            audio = self._recorder.snapshot()
            if audio is None:
                continue

            with self._lock:
                last_end = self._segments[-1][0] if self._segments else 0

            chunk = audio[last_end:]
            if len(chunk) / self._SR < self._MIN_NEW_SEC:
                continue                                 # not enough new audio yet

            try:
                text = self._transcriber.transcribe(chunk)
                if text:
                    with self._lock:
                        self._segments.append((len(audio), text))
                    logger.debug(
                        f"BgJob chunk {last_end/self._SR:.1f}s–{len(audio)/self._SR:.1f}s"
                        f" → {len(text)} chars"
                    )
                    if self._on_chunk:
                        try:
                            self._on_chunk(text)
                        except Exception:
                            logger.exception("BgJob on_chunk callback failed")
            except Exception:
                logger.exception("BgJob chunk transcription error")

    def finish(self, full_audio: np.ndarray) -> str:
        """Stop background loop, transcribe the uncovered tail, return full text."""
        self._stop.set()
        # Wait for the worker to actually exit before snapshotting segments.
        # Without the join a chunk decoded right now lands in _segments AFTER
        # our snapshot — its text is lost from the stitched result (and, in
        # streaming mode, was already typed on screen → duplicate after the
        # rollback). The worker wakes from its wait instantly on the event;
        # the only real wait here is an in-flight transcribe call, and we're
        # about to spend comparable time transcribing the tail anyway.
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=30.0)
            if t.is_alive():
                logger.warning("BgJob worker still busy after 30s — "
                               "finishing without it")

        with self._lock:
            segments = list(self._segments)

        last_end = segments[-1][0] if segments else 0
        tail     = full_audio[last_end:]

        parts = [text for _, text in segments]

        if len(tail) / self._SR >= 0.3:
            try:
                tail_text = self._transcriber.transcribe(tail)
                if tail_text:
                    parts.append(tail_text)
            except Exception:
                logger.exception("BgJob tail transcription error")

        if parts:
            return " ".join(parts).strip()

        # No background results at all — plain transcription
        return self._transcriber.transcribe(full_audio)


def _make_icon(color: str, size: int = 256) -> Image.Image:
    """Generate a state-coloured circle for the tray.

    Windows downscales the icon to ~16/24/32 px depending on DPI settings,
    so rendering at 256 means good antialiasing at any scale. We also add a
    subtle dark ring so the icon reads on light tray backgrounds.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = max(2, size // 16)        # margin
    # Outer ring for contrast on light Windows themes
    d.ellipse([m // 2, m // 2, size - m // 2, size - m // 2],
              outline=(20, 20, 20, 200), width=max(2, size // 32))
    d.ellipse([m, m, size - m, size - m], fill=color)
    return img


class App:
    def __init__(self) -> None:
        self.config: Config = load_config()
        # Dictation earcons (concept 36) — hooks live in _duck_start/_duck_restore
        # and the empty/pre-stop paths. Safe no-op if winsound is unavailable.
        from sounds import SoundPlayer
        self._snd = SoundPlayer.from_config(self.config)
        # Force a no-op-but-applying set (_UiScale.set skips when value is
        # close to current, so seed to a different value first to ensure the
        # ctk.set_widget_scaling hook fires on startup).
        if abs(self.config.ui.font_scale - _UiScale.value) < 0.01:
            _UiScale.value = 0.0
        _UiScale.set(self.config.ui.font_scale)
        _apply_theme(getattr(self.config.ui, "theme", "dark"))
        self.recorder = Recorder()
        self.transcriber: Transcriber | None = None
        self.cleaner_chain: CleanerChain = build_cleaner_chain(
            self.config.cleaners,
            punctuation_fallback=self.config.output.punctuation_fallback,
        )
        # Text-aid artifacts (22/26/27): replacement rules + phonetic index.
        # Rebuilt on settings save.
        self._replacement_rules: list = []
        self._phonetic: dict = {}
        self._rebuild_text_aids()
        self.history = HistoryManager(
            max_entries=self.config.history.max_entries,
            retention_days=self.config.history.retention_days,
        )
        self.icon: pystray.Icon | None = None
        self.root: tk.Tk | None = None
        self.flowbar: FlowBar | None = None
        self.bubble: PasteFallbackBubble | None = None
        self.cleaning_enabled = True
        self._overlay_visible = True

        self._state = State.LOADING
        self._icons = {s: _make_icon(c) for s, c in _COLORS.items()}
        self._lock = threading.Lock()
        self._recording = False
        self._processing = False
        # Hands-free: number of segment transcriptions currently running. The
        # manual-stop flush waits for this to hit 0 (final tail landed in the
        # gate buffer) instead of a blind fixed delay → snappy like PTT.
        self._seg_inflight = 0
        # Inter-dictation spacing: remember the last char we inserted and when,
        # so a quick follow-up dictation gets a leading space instead of being
        # glued to the previous one («…капусту.1. Купить» → «…капусту. 1. Купить»).
        self._last_paste_char = ""
        self._last_paste_time = 0.0

        self._continuous_mode = False
        self._continuous_listener: ContinuousListener | None = None
        self._stop_watcher = None          # аудио-стоп (stop_word.py), на сессию
        self._bg_job: _BgJob | None = None
        self._record_watchdog: threading.Timer | None = None

        self._whisper_mode = self.config.whisper_mode.enabled

        # Streaming state — how many chars we've already inserted via partial
        # callbacks; used to roll back via Backspace on commit when LLM changes
        # the text.
        self._stream_inserted: int = 0
        self._stream_active: bool = False

        # Local HTTP API (optional)
        self._api_server = None
        # True, когда API-сервер подняли ради веб-окон Настроек/Истории —
        # тогда выключение галки «Local API» его не останавливает.
        self._api_for_ui = False

        # Wake-word listener (optional)
        self._wake_listener = None
        self._wake_session_timer: threading.Timer | None = None
        # Live mic-level probe for the Settings «test your mic» meter (lazy).
        self._mic_monitor = MicMonitor(on_acquire=self._mic_test_acquire,
                                       on_release=self._mic_test_release)

        # Audio ducker (optional, lazy)
        from audio_ducker import AudioDucker
        self._ducker = AudioDucker(duck_level=self.config.audio.duck_level,
                                    mode=self.config.audio.duck_mode)
        # ВСЕ вызовы дакера — через ОДИН выделенный поток. pycaw/comtypes
        # COM-объекты должны создаваться и Release'иться на одном потоке: GC,
        # запускающий Release с чужого потока (клик ✕ на Tk-потоке → restore →
        # comtypes Release → 0xc0000005, см. crash.log), роняет процесс
        # нативно. FIFO-очередь заодно сохраняет порядок start→restore.
        import queue
        self._duck_jobs: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._duck_worker, daemon=True,
                         name="ducker").start()
        # If a previous run crashed mid-duck, the system volume may be stuck low
        # (or fully muted at duck_level=0). Put it back now.
        try:
            AudioDucker.recover_stuck_volume()
        except Exception:
            logger.debug("duck recovery at startup failed", exc_info=True)

        # «User is currently typing» detector — used to skip auto-paste and
        # show the bubble instead. Updated on every non-modifier KEY_DOWN
        # in _register_hooks. 0 means "never observed".
        self._last_user_keypress: float = 0.0
        # Undo-cancel: holds the just-cancelled audio for a few seconds so the
        # «Вернуть» toast can restore (transcribe + insert) it.
        self._undo_audio = None
        self._undo_timer: threading.Timer | None = None
        # When set, _process() runs on this audio instead of stopping the
        # recorder (used by the undo-restore path).
        self._pending_audio = None

        # Hands-free voice gate («талкер старт … стоп»), opt-in via
        # config.output.voice_gate, used in continuous mode. Buffers speech
        # between start/stop and honours the «ввод» Enter trigger. (Role-tone /
        # «причеши» polish needed the removed LLM and are gone.)
        self._vgate = None
        try:
            from voice_gate import VoiceGate
            from voice_triggers import build_stop_phrases
            self._vgate = VoiceGate(
                stop_phrases=build_stop_phrases(),
                fuzzy_thr=self.config.wake.stop_fuzzy)
        except Exception:
            logger.exception("voice gate init failed (app continues without it)")

    # ── State ──────────────────────────────────────────────────────────────────

    def _set_state(self, state: State) -> None:
        prev = self._state
        self._state = state
        if self.icon:
            self.icon.icon = self._icons[state]
            self.icon.title = _TRAY_TITLES[state]
        if self.root and self.flowbar:
            s = state.value
            # A fast engine (GigaAM ~226 MB) can finish loading on the bg thread
            # before tkinter's mainloop is up, so root.after() raises "main
            # thread is not in main loop". Harmless — the UI will reflect the
            # state once it's running — so swallow it instead of failing the load.
            try:
                self.root.after(0, lambda: self.flowbar.set_state(s))
            except RuntimeError:
                pass
        # No centered toast — the spinner inside the pill IS the loading /
        # «обрабатываю» indicator. Keep the LoadingWindow object hidden.
        if self.root and getattr(self, "loading_win", None):
            if state != State.PROCESSING:
                try:
                    self.root.after(0, self.loading_win.hide)
                except RuntimeError:
                    pass

    # ── Model loading ───────────────────────────────────────────────────────────

    @staticmethod
    def _whisper_repo_id(model: str) -> str:
        """faster-whisper resolves bare sizes («small») to Systran repos; a
        namespaced id («mobiuslabsgmbh/faster-whisper-large-v3-turbo») IS the
        repo. Mirror that here for the cache probe / pre-download."""
        return model if "/" in model else f"Systran/faster-whisper-{model}"

    def _predownload_whisper(self, repo: str) -> None:
        """Download the model into the HF cache BEFORE constructing the engine,
        streaming percent into the tray title — otherwise a 1.5 GB first run
        looks like a hang (pill spins, zero feedback). Any failure just falls
        through: WhisperModel() retries the download itself."""
        app = self

        try:
            from huggingface_hub import snapshot_download
            from tqdm import tqdm as _tqdm

            class _TrayTqdm(_tqdm):
                _last_pct = -1

                def update(self, n=1):
                    super().update(n)
                    # Only the big weight files are worth narrating (the repo
                    # also has tiny json/tokenizer files that flicker 0→100).
                    if not self.total or self.total < 50_000_000:
                        return
                    pct = int(self.n * 100 / self.total)
                    if pct != _TrayTqdm._last_pct:
                        _TrayTqdm._last_pct = pct
                        app._tray_progress(pct)

            snapshot_download(repo, tqdm_class=_TrayTqdm)
        except Exception:
            logger.warning("Model pre-download failed — engine will retry",
                           exc_info=True)

    def _tray_progress(self, pct: int) -> None:
        if self.icon:
            try:
                self.icon.title = f"Talker — скачиваю модель {pct}%"
            except Exception:
                pass

    def _load_model(self) -> None:
        try:
            cfg = self.config.stt
            acfg = self.config.audio
            # Probe HF cache so we can tell the user if this run will go through
            # a slow first-time download (~150 MB for small, ~1.5 GB for
            # large-v3-turbo). The model still loads in *this background
            # thread* — the UI stays responsive throughout.
            if cfg.engine == "whisper":
                from pathlib import Path
                repo = self._whisper_repo_id(cfg.model)
                hf_home = Path(os.environ.get("HF_HOME") or
                               Path.home() / ".cache" / "huggingface")
                # HF hub layout: models--{org}--{name}. The old probe assumed
                # the Systran pattern for every model, so namespaced ids always
                # looked «not cached» → ложное «Скачиваю модель…» на каждый старт.
                cache_dir = hf_home / "hub" / ("models--" + repo.replace("/", "--"))
                if not cache_dir.exists():
                    logger.info(f"Model {repo!r} not cached — downloading now")
                    self._notify(
                        f"Скачиваю модель {cfg.model}… "
                        "Можно работать дальше — после загрузки активируется."
                    )
                    self._predownload_whisper(repo)

            self.transcriber = Transcriber(
                model_size=cfg.model,
                language=cfg.language or None,
                normalize=acfg.normalize,
                noise_reduction=acfg.noise_reduction,
                device=cfg.device,
                compute_type=cfg.compute_type,
                cpu_threads=cfg.cpu_threads,
                vocabulary=self.config.vocabulary.words,
                engine=cfg.engine,
                gigaam_model=cfg.gigaam_model,
                nr_mode=acfg.nr_mode,
                nr_strength=acfg.nr_strength,
            )
            self._apply_runtime_overrides()
            # Warm up lazily-loaded engines (gigaam) NOW, while State.LOADING is
            # still showing, so the model is ready before the first dictation —
            # otherwise the first Ctrl+Alt+Space stalls ~2 s on the model load
            # and the loading indicator shows late.
            try:
                self.transcriber.warmup()
            except Exception:
                logger.warning("Engine warmup failed (will load on first use)",
                               exc_info=True)
            # Log the ACTUAL engine/model, not always the whisper size — at
            # engine=gigaam: the whisper `model` size is irrelevant.
            if cfg.engine == "gigaam":
                _model_label = cfg.gigaam_model
            else:
                _model_label = cfg.model
            logger.info(f"Engine '{cfg.engine}' ready: model={_model_label} device={cfg.device}")
            self._notify(f"Talker готов ({cfg.engine}: {_model_label})")
            self._set_state(State.IDLE)
            self._hide_splash()
            self._maybe_show_onboarding()
        except Exception:
            logger.exception("Failed to load model")
            self._set_state(State.ERROR)
            self._hide_splash()

    def _maybe_show_onboarding(self) -> None:
        """First run only: a one-time tip anchored to the pill that says how to
        dictate (the app otherwise starts silently in the tray and the user has
        to guess about the PTT key). Dismissal persists the flag."""
        if self.config.ui.onboarding_shown:
            return
        if not (self.root and self.flowbar):
            return
        self.config.ui.onboarding_shown = True   # never twice per session

        def _show() -> None:
            try:
                from ui import OnboardingTip
                key = " + ".join(p.strip().title()
                                 for p in self.config.hotkey.key.split("+"))
                OnboardingTip(self.root, anchor_xy=self.flowbar.anchor_xy,
                              anchor_size=self.flowbar.anchor_size,
                              hotkey_label=key or "Right Alt")
            except Exception:
                logger.debug("onboarding tip failed", exc_info=True)

        try:
            self.root.after(900, _show)
        except RuntimeError:
            pass

    def _hide_splash(self) -> None:
        """Dismiss the startup splash from the bg load thread. Marshals to the
        GUI thread; swallows the «main thread is not in main loop» RuntimeError
        a fast engine can hit if it finishes before mainloop is up (the splash
        is then withdrawn once the queued callback runs)."""
        sp = getattr(self, "splash", None)
        if sp is None or not self.root:
            return
        try:
            self.root.after(0, sp.hide)
        except RuntimeError:
            pass

    def _apply_runtime_overrides(self) -> None:
        """Mutate transcriber knobs without reloading the model (whisper-mode,
        vocabulary changes, etc.). Safe to call any time."""
        if self.transcriber is None:
            return
        self.transcriber.set_vocabulary(self.config.vocabulary.words)
        if self._whisper_mode:
            self.transcriber.no_speech_threshold = self.config.whisper_mode.no_speech_threshold
        else:
            self.transcriber.no_speech_threshold = 0.6

    def _rebuild_text_aids(self) -> None:
        """Recompile replacement rules (22) + phonetic index (26). Cheap; call at
        startup and after settings save. Independent of the transcriber."""
        # Built-in dictionary (IT-terms + well-known brand/product names) ALWAYS
        # applies — it's the only way GigaAM (RU-only, Cyrillic out) yields
        # «Microsoft» instead of «майкрософт». User replacements stack on top.
        builtins = [
            ReplacementConfig(
                to=d.get("to", ""), from_=list(d.get("from_", [])),
                whole_word=d.get("whole_word", True),
                phonetic=d.get("phonetic", False), sounds=d.get("sounds", ""))
            for d in default_replacements()
        ]
        rules = builtins + self.config.replacements
        self._replacement_rules = compile_rules(rules)
        self._phonetic = compile_phonetic(rules)

    # ── Hook registration ───────────────────────────────────────────────────────

    # Right Alt on Russian keyboard layout can appear as "alt gr" or "altgr"
    # instead of "right alt". keyboard.hook() catches all events so we can
    # match by name with aliases, which is more reliable than on_press_key().
    _RIGHT_ALT_ALIASES = frozenset({"right alt", "alt gr", "altgr", "right menu"})
    # Right Ctrl — keep it specific ("right ctrl" only). NOT bare "ctrl": that
    # would collide with the Ctrl+Alt+Space continuous combo (Ctrl is a modifier
    # there) and make every Ctrl press start PTT.
    _RIGHT_CTRL_ALIASES = frozenset({"right ctrl"})
    # All alias groups; a PTT target matches the whole group it belongs to.
    _PTT_ALIAS_GROUPS = (_RIGHT_ALT_ALIASES, _RIGHT_CTRL_ALIASES)

    @classmethod
    def _ptt_aliases(cls, target: str) -> "frozenset[str]":
        """Return the alias group containing `target` (so right-alt/alt-gr or
        right-ctrl all match), or empty if the key has no aliases."""
        for grp in cls._PTT_ALIAS_GROUPS:
            if target in grp:
                return grp
        return frozenset()

    # Keys that don't count as "user is typing" — modifiers and lock keys.
    # Everything else (letters, numbers, space, enter, backspace, …) does.
    _NON_TYPING_KEYS = frozenset({
        "ctrl", "left ctrl", "right ctrl",
        "alt", "left alt", "right alt", "alt gr", "altgr", "right menu",
        "shift", "left shift", "right shift",
        "windows", "left windows", "right windows",
        "caps lock", "num lock", "scroll lock",
        "menu", "fn",
    })

    def _register_hooks(self) -> None:
        keyboard.unhook_all()
        target = self.config.hotkey.key.lower().strip()
        logger.info(
            f"Registering hooks: PTT={target!r} mode={self.config.hotkey.mode!r}, "
            f"continuous={CONTINUOUS_HOTKEY!r}"
        )

        # Track modifier state manually. `keyboard.add_hotkey` collides with
        # `keyboard.hook` in newer versions of the library (only one of them
        # fires) — so we detect ctrl+alt+space inside the same hook.
        self._mods_held = {"ctrl": False, "alt": False}
        self._cont_combo_armed = False    # so we trigger once per press
        self._ptt_key_down = False        # debounce PTT auto-repeat / phantom edges

        def _on_key(event: keyboard.KeyboardEvent) -> None:
            name = (event.name or "").lower().strip()

            # TEMP DIAG: log every key event (scan_code helps tell L/R apart).
            if os.environ.get("TALKER_KEYDIAG"):
                logger.info(f"KEYDIAG {name!r} "
                            f"{'DOWN' if event.event_type==keyboard.KEY_DOWN else 'UP'} "
                            f"sc={getattr(event,'scan_code',None)}")

            # "User is typing" timestamp — anything that isn't a pure modifier
            # / lock counts. PTT release also resets it after the recording
            # ends (see _on_release) so PTT itself doesn't poison the signal.
            if (event.event_type == keyboard.KEY_DOWN
                    and name not in self._NON_TYPING_KEYS):
                # Exclude our own PTT and continuous trigger so they don't
                # mark themselves as "user typing".
                if not (target in self._RIGHT_ALT_ALIASES
                        and name in self._RIGHT_ALT_ALIASES):
                    if name != "space" or not (self._mods_held["ctrl"]
                                                and self._mods_held["alt"]):
                        self._last_user_keypress = time.monotonic()

            # Track modifier presses
            if name in ("ctrl", "left ctrl", "right ctrl"):
                self._mods_held["ctrl"] = event.event_type == keyboard.KEY_DOWN
                if event.event_type == keyboard.KEY_UP:
                    self._cont_combo_armed = False
            elif name in ("alt", "left alt", "right alt", "alt gr", "altgr"):
                self._mods_held["alt"] = event.event_type == keyboard.KEY_DOWN
                if event.event_type == keyboard.KEY_UP:
                    self._cont_combo_armed = False

            # Continuous-mode combo: ctrl+alt+space
            if (name == "space"
                    and event.event_type == keyboard.KEY_DOWN
                    and self._mods_held["ctrl"]
                    and self._mods_held["alt"]
                    and not self._cont_combo_armed):
                self._cont_combo_armed = True
                logger.info("Continuous hotkey (ctrl+alt+space) triggered")
                self._toggle_continuous()
                return
            if name == "space" and event.event_type == keyboard.KEY_UP:
                self._cont_combo_armed = False

            # PTT match: a key may report under several names for the same
            # physical key (right alt ↔ alt gr; right ctrl on some layouts).
            # Match the whole alias group so all spellings count.
            aliases = self._ptt_aliases(target)
            sc = getattr(event, "scan_code", None)
            match = (name in aliases) if aliases else (name == target)
            # AltGr layouts (RU): the `keyboard` lib swallows the real right-alt and
            # emits only a synthetic LEFT-CTRL edge (scan_code 541). Treat that edge
            # as the PTT key when right-alt is the target. Real left ctrl is sc 29,
            # not 541, so this won't fire on a normal ctrl press.
            if not match and target in self._RIGHT_ALT_ALIASES and sc == 541:
                match = True
            if not match:
                return

            # Debounce / dedup. Windows fires repeated KEY_DOWN (auto-repeat)
            # while held, and AltGr-class keys emit phantom UP/DOWN pairs that
            # arrive out of order. Without this, _recording state thrashes
            # (UP without a prior DOWN, double DOWN) and PTT misfires. We act
            # only on real edges: first DOWN starts, the matching UP stops;
            # everything in between is ignored.
            if event.event_type == keyboard.KEY_DOWN:
                if self._ptt_key_down:
                    return                       # auto-repeat / phantom — ignore
                self._ptt_key_down = True
                # Pressing the PTT key IS a deliberate «dictate now» — forgive any
                # prior typing (e.g. selecting text just before) so the typing-
                # guard doesn't skip the paste («вставляется через раз»). You
                # can't type while holding PTT, so this stays clean.
                self._last_user_keypress = 0.0
                logger.info(f"PTT key {name!r} DOWN")
                self._on_press(event)
            elif event.event_type == keyboard.KEY_UP:
                if not self._ptt_key_down:
                    return                       # phantom UP without a DOWN
                self._ptt_key_down = False
                logger.info(f"PTT key {name!r} UP")
                self._on_release(event)

        keyboard.hook(_on_key)
        logger.info(f"Hooks installed: PTT={target!r}, continuous=ctrl+alt+space (manual)")

        wm_hotkey = (self.config.whisper_mode.hotkey or "").strip()
        if wm_hotkey:
            try:
                keyboard.add_hotkey(wm_hotkey, self._toggle_whisper_mode,
                                     suppress=False)
            except Exception as e:
                logger.warning(f"Failed to register whisper-mode hotkey {wm_hotkey!r}: {e}")

    # ── Push-to-talk hotkey ─────────────────────────────────────────────────────

    def _current_gain(self) -> float:
        if self._whisper_mode:
            return self.config.whisper_mode.mic_gain
        return self.config.audio.mic_gain

    def _duck_worker(self) -> None:
        """The one thread that owns every pycaw/comtypes call (see __init__)."""
        while True:
            job = self._duck_jobs.get()
            try:
                job()
            except Exception:
                logger.exception("ducker job failed")

    def _system_output_peak(self, timeout: float = 0.3) -> "float | None":
        """Пик системного вывода 0..1 — замер выполняется на COM-потоке дакера
        (см. system_meter.py), ответа ждём не дольше timeout. None = не узнали
        (гард тогда пропускает сегмент как обычно, без блокировки)."""
        done = threading.Event()
        box: list = [None]

        def _probe() -> None:
            try:
                from system_meter import system_output_peak
                box[0] = system_output_peak()
            finally:
                done.set()

        self._duck_jobs.put(_probe)
        done.wait(timeout)
        return box[0]

    def _duck_start(self) -> None:
        # Earcon FIRST, before ducking — otherwise the start sound is muted by
        # our own ducker (concept 36 contract). Single choke-point for all
        # record-start paths (press / continuous / command).
        self._snd.start()
        if self.config.audio.duck_other_apps:
            self._duck_jobs.put(self._ducker.start)

    def _duck_restore(self) -> None:
        def _restore_then_earcon() -> None:
            try:
                self._ducker.restore()
            finally:
                # Stop earcon LAST, after restoring other apps' volume.
                self._snd.stop()
        self._duck_jobs.put(_restore_then_earcon)

    def _abort_start(self, what: str) -> None:
        """Roll back after a recorder/listener fails to start (mic unplugged or
        held by another app). Without this, the 'recording' flag stays set and
        every later hotkey press is a silent no-op until restart. Call only from
        an `except` block (uses logger.exception)."""
        logger.exception(f"{what} failed to start")
        with self._lock:
            self._recording = False
        self._continuous_mode = False
        self._disarm_watchdog()
        lst = self._continuous_listener
        if lst is not None:
            try:
                lst.stop()
            except Exception:
                logger.debug("_abort_start: suppressed", exc_info=True)
            self._continuous_listener = None
        try:
            self._duck_restore()
        except Exception:
            logger.debug("_abort_start: suppressed", exc_info=True)
        self._notify("Не удалось начать запись — проверь микрофон")
        self._set_state(State.IDLE)

    def _on_press(self, event: keyboard.KeyboardEvent) -> None:
        if self._state == State.LOADING:
            logger.info("PTT ignored: model still loading")
            self._notify("Модель ещё грузится — подожди пару секунд")
            return
        # PTT during continuous: just ignore (state is LISTENING) — user has
        # to stop continuous first. We log it so it's obvious in the log if
        # PTT appears unresponsive.
        if self._state == State.LISTENING:
            logger.info("PTT ignored: continuous mode is active "
                        "(stop it via Ctrl+Alt+Space or tray menu)")
            return

        if self.config.hotkey.mode == "hold":
            with self._lock:
                if self._recording or self._processing:
                    return
                self._recording = True
            try:
                self.recorder.start(self.config.audio.mic_index, gain=self._current_gain(),
                                    source=self.config.audio.source)
                self._duck_start()
                self._start_bg_job()
                self._arm_watchdog(MAX_RECORDING_HOLD_SEC)
                self._set_state(State.RECORDING)
            except Exception:
                self._abort_start("PTT recording")

        elif self.config.hotkey.mode == "toggle":
            with self._lock:
                if self._processing:
                    return
                if self._recording:
                    self._recording = False
                    do_stop = True
                else:
                    self._recording = True
                    do_stop = False
            if do_stop:
                self._disarm_watchdog()
                threading.Thread(target=self._process, daemon=True).start()
            else:
                try:
                    self.recorder.start(self.config.audio.mic_index, gain=self._current_gain(),
                                    source=self.config.audio.source)
                    self._start_bg_job()
                    self._arm_watchdog()
                    self._set_state(State.RECORDING)
                except Exception:
                    self._abort_start("PTT recording")

    def _on_release(self, event: keyboard.KeyboardEvent) -> None:
        if self.config.hotkey.mode != "hold" or self._continuous_mode:
            return
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        # pre-stop tick (mic only — in loopback it would land in the capture).
        if self.config.audio.source != "system":
            self._snd.pre_stop()
        self._disarm_watchdog()
        threading.Thread(target=self._process, daemon=True).start()

    # ── Recording watchdog ──────────────────────────────────────────────────────

    def _arm_watchdog(self, seconds: float = MAX_RECORDING_SEC) -> None:
        """Force-stop recording if release event is missed (fullscreen apps,
        RDP, UAC). Hold-mode PTT passes the much shorter MAX_RECORDING_HOLD_SEC
        — nobody holds a key for 20 minutes, so by then it's a stuck key, not a
        dictation; toggle/single-shot keep the 2 h meeting ceiling."""
        self._disarm_watchdog()
        self._watchdog_sec = seconds
        self._record_watchdog = threading.Timer(seconds, self._force_release)
        self._record_watchdog.daemon = True
        self._record_watchdog.start()

    def _disarm_watchdog(self) -> None:
        if self._record_watchdog:
            self._record_watchdog.cancel()
            self._record_watchdog = None

    def _force_release(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        logger.warning("Watchdog: forcing stop after "
                       f"{getattr(self, '_watchdog_sec', MAX_RECORDING_SEC):.0f}s")
        threading.Thread(target=self._process, daemon=True).start()

    # ── Continuous (hands-free) mode ────────────────────────────────────────────

    def _toggle_continuous(self) -> None:
        if self._state == State.LOADING:
            return
        if self._continuous_mode:
            self._stop_continuous()
        else:
            self._start_continuous()

    def _start_continuous(self) -> None:
        with self._lock:
            if self._recording:
                self._recording = False
                self.recorder.stop()

        self._continuous_mode = True
        cfg = self.config.continuous

        # ── single_shot: long recording, one paste at the end ──
        # The hotkey acts as a toggle (start → user speaks → stop). No VAD
        # cutting, no partial inserts. Waveform reflects live mic RMS because
        # we use the regular Recorder.
        if cfg.mode == "single_shot":
            try:
                self.recorder.start(
                    self.config.audio.mic_index,
                    gain=self._current_gain(),
                    source=self.config.audio.source,
                )
                self._start_bg_job()      # bg pre-decoding for speed at finish
                self._duck_start()
                self._set_state(State.RECORDING)
                logger.info("Continuous mode (single_shot) started")
            except Exception:
                self._abort_start("Continuous (single_shot)")
            return

        # ── vad_segments: classic VAD-chunked continuous ──
        if self._whisper_mode:
            wm = self.config.whisper_mode
            aggr = wm.vad_aggressiveness
            silence = wm.silence_secs
        else:
            aggr = cfg.vad_aggressiveness
            silence = cfg.silence_secs

        # Аудио-стоп: openWakeWord-модели («стоп-стоп»/«Талкер стоп») слушают
        # кадры сессии и закрывают её мгновенно — текстовый стоп остаётся
        # фолбэком. Watcher одноразовый, живёт ровно сессию.
        self._stop_watcher = None
        try:
            stop_models = [m for m in self.config.wake.stop_models if m]
            if stop_models:
                from stop_word import StopWordWatcher
                self._stop_watcher = StopWordWatcher(
                    model_paths=stop_models,
                    on_stop=self._on_audio_stop,
                    threshold=self.config.wake.stop_threshold)
        except Exception:
            logger.warning("Stop-word watcher unavailable", exc_info=True)
            self._stop_watcher = None

        try:
            self._continuous_listener = ContinuousListener(
                on_segment=self._on_continuous_segment,
                aggressiveness=aggr,
                silence_secs=silence,
                mic_index=self.config.audio.mic_index,
                gain=self._current_gain(),
                vad_engine=cfg.vad_engine,
                on_frame=(self._stop_watcher.feed
                          if self._stop_watcher is not None else None),
            )
            self._continuous_listener.start()
            # Drive the pill's waveform from the live continuous mic level so the
            # bars bounce with your voice during hands-free (not the idle PTT rec).
            if self.flowbar:
                self.flowbar.set_level_source(self._continuous_listener)
            # With voice_gate on, OPEN the dictation buffer now so EVERY way into
            # continuous works (Ctrl+Alt+Space AND wake) — otherwise the gate is
            # inactive and silently drops every segment («модель не работает»).
            if self._vgate is not None and self.config.output.voice_gate:
                self._vgate.begin()
            self._duck_start()
            self._set_state(State.LISTENING)
            logger.info(f"Continuous mode (vad_segments) started")
        except Exception:
            self._abort_start("Continuous (vad_segments)")

    def _on_audio_stop(self, name: str) -> None:
        """Аудио-модель услышала стоп-команду — закрыть сессию тем же путём,
        что Ctrl+Alt+Space (буфер дольётся через drain-and-flush; текст самой
        фразы вырежет текстовый гейт). Модель «ввод-ввод» дополнительно
        взводит submit: после вставки будет нажат Enter (отправить)."""
        if not self._continuous_mode:
            return
        # Submit-модели: «стоп-да» (основная) и синонимы прошлых итераций.
        nm = (name or "").lower()
        if (any(k in nm for k in ("stop_da", "otprav", "vvod"))
                and self._vgate is not None):
            try:
                self._vgate.arm_submit()
            except Exception:
                logger.exception("arm_submit failed")
        logger.info(f"Audio stop ({name}) → ending hands-free session")
        self._stop_continuous()

    def _close_stop_watcher(self) -> None:
        w = getattr(self, "_stop_watcher", None)
        if w is not None:
            try:
                w.close()
            except Exception:
                logger.debug("stop watcher close failed", exc_info=True)
            self._stop_watcher = None

    def _stop_continuous(self) -> None:
        was_single = (self.config.continuous.mode == "single_shot")
        self._continuous_mode = False
        self._close_stop_watcher()
        # pre-stop tick (mic only — in loopback it would land in the capture).
        if self.config.audio.source != "system":
            self._snd.pre_stop()

        stopped_listener = self._continuous_listener
        if self._continuous_listener:
            self._continuous_listener.stop()
            self._continuous_listener = None
        # Revert the pill's waveform to the PTT recorder.
        if self.flowbar:
            self.flowbar.set_level_source(None)

        if was_single:
            # Treat single_shot stop the same as PTT release: run the full
            # pipeline once on everything we recorded.
            logger.info("Continuous (single_shot) stopped — processing buffer")
            threading.Thread(target=self._process, daemon=True).start()
            return

        # voice_gate: a NON-voice stop (Ctrl+Alt+Space toggle / ✓ button / wake
        # timeout) must still emit whatever was buffered. The listener's final
        # tail utterance is emitted right after stop() but its transcription
        # lands ~0.3-1 s later; flushing NOW loses it (arrives after reset →
        # dropped). Instead of a blind fixed delay, wait for the listener to
        # drain AND the tail transcription to finish, then flush at once — as
        # snappy as PTT. flush() returns None on the voice-«стоп» path.
        if self._vgate is not None and self.config.output.voice_gate:
            threading.Thread(target=self._drain_and_flush,
                             args=(stopped_listener,), daemon=True,
                             name="gate-flush").start()

        self._duck_restore()
        self._set_state(State.IDLE)
        logger.info("Continuous mode stopped")
        # If a wake session was open (Hey Jarvis), END it cleanly on this manual
        # stop too: cancel the pending 30 s timeout and re-arm wake. Without this
        # the lingering timer later resumes wake on its own — feels like the app
        # «сама запускается» after you closed it with Ctrl+Alt+Space.
        if self._wake_session_timer is not None:
            self._on_wake_session_end()

    def _drain_and_flush(self, listener) -> None:
        """Wait until the just-stopped listener has emitted its final tail AND
        that tail's transcription has landed in the gate buffer, then flush —
        instead of a blind fixed delay. Typical wait ≈ tail transcription time
        (~0.3-1 s) vs the old hard 1.8 s; if there's no tail (stopped in
        silence) it flushes almost immediately. All waits are bounded so a stuck
        transcription can't hang the flush."""
        try:
            tail_expected = bool(getattr(listener, "in_speech", False))
            if listener is not None:
                try:
                    listener.wait_drained(timeout=2.0)   # tail _emit spawned (or none)
                except Exception:
                    logger.debug("_drain_and_flush: suppressed", exc_info=True)
            # If a tail is coming, give its transcription thread a moment to
            # register as in-flight before we start draining (bounded ~0.6 s).
            if tail_expected:
                t0 = time.monotonic()
                while self._seg_inflight == 0 and time.monotonic() - t0 < 0.6:
                    if self._continuous_mode:
                        return                            # a new session took over
                    time.sleep(0.02)
            # Drain any running segment transcription (bounded ~3 s safety).
            deadline = time.monotonic() + 3.0
            while self._seg_inflight > 0 and time.monotonic() < deadline:
                if self._continuous_mode:
                    return
                time.sleep(0.03)
        except Exception:
            logger.exception("drain-and-flush wait failed")
        self._flush_gate_buffer()

    def _flush_gate_buffer(self) -> None:
        """Emit + clear buffered hands-free dictation after a manual stop, once
        the listener's final in-flight segment has landed in the buffer (see
        _drain_and_flush). Skips if a NEW continuous session has already started
        (its begin() owns the buffer)."""
        if self._vgate is None or self._continuous_mode:
            return
        try:
            res = self._vgate.flush()
            if res:
                self._gate_emit(res)
        except Exception:
            logger.exception("delayed gate flush failed")

    def _on_continuous_segment(self, audio) -> None:
        with self._lock:
            if self._processing:
                logger.info("Continuous: segment skipped (still processing)")
                return
            self._processing = True
            self._seg_inflight += 1

        # In a hands-free voice-gate session every VAD chunk is just BUFFERED
        # until the stop phrase — nothing is inserted per-chunk. Flashing
        # «Обработка…» (orange) on each internal slice made the pill flicker
        # teal→orange→teal mid-sentence («проскакивает жёлтая штука, гаснет»).
        # So while buffering keep «Слушаю» (teal); switch to PROCESSING only when
        # we actually insert text (stop phrase below, or the non-gate vad path).
        gate_buffering = (self._vgate is not None and self.config.output.voice_gate)
        if not gate_buffering:
            self._set_state(State.PROCESSING)
        try:
            if self.transcriber is None:
                return

            # Медиа-гард: фильм/музыка в колонках протекают в микрофон и VAD
            # честно слышит «речь» — но утечка заметно тише голоса у микрофона.
            # Если системный вывод громко играет, а сегмент тихий — это кино
            # говорит, не пользователь: дропаем ДО транскрипции (и CPU целее).
            mg = self.config.continuous
            if mg.media_guard:
                try:
                    seg_rms = float(np.sqrt(np.mean(np.square(audio))))
                except Exception:
                    seg_rms = 1.0
                if seg_rms < mg.media_guard_min_rms:
                    peak = self._system_output_peak()
                    if peak is not None and peak > mg.media_guard_sys_peak:
                        logger.info(
                            f"Media guard: dropped segment (sys_peak={peak:.2f}, "
                            f"mic_rms={seg_rms:.4f} < {mg.media_guard_min_rms})")
                        return

            raw = self.transcriber.transcribe(audio)
            if not raw:
                return

            # Patch 2 feature 1 — hands-free voice gate. A wake word (Hey Jarvis)
            # opens the session (_vgate.begin); speech buffers from the first
            # segment; a stop phrase finalises AND ends the session.
            if gate_buffering:
                # NB: log the LENGTH, never the content — talker.log must not
                # collect what the user dictates (privacy promise of the app).
                logger.info(f"Voice-gate segment: {len(raw)} chars "
                            f"(active={self._vgate.active})")
                res = self._vgate.feed(raw)
                if res is None:
                    return                       # still buffering → pill stays «Слушаю»
                self._set_state(State.PROCESSING)   # real insert → show «Обработка…»
                self._gate_emit(res)
                # «стоп» also ENDS the session (stop recording + re-arm wake),
                # like releasing Ctrl+Alt+Space. Defer off this listener thread —
                # can't stop the listener from inside its own segment callback.
                threading.Thread(target=self._on_wake_session_end, daemon=True,
                                 name="wake-end").start()
                return

            text_in, exact_match, actions = self._pre_clean_pipeline(raw)

            if exact_match or not self.cleaning_enabled:
                text = text_in
            else:
                text, cleaned = self.cleaner_chain.clean(text_in)
                if not cleaned:
                    self._notify("Очистка недоступна — сырой текст")

            if self.config.output.number_format:
                text = itn.normalize(text, self.config.stt.language)

            raw_text = text
            text = self._apply_voice_formatting(text)
            method = self._paste(text)
            if actions:
                execute_actions(actions)
            self.history.append(text, raw=raw_text)
            self._show_bubble(text, inject_method=method)
            logger.info(f"Continuous: inserted {len(text)} chars (snippet={exact_match})")

        except Exception:
            logger.exception("Continuous segment error")
        finally:
            with self._lock:
                self._processing = False
                self._seg_inflight = max(0, self._seg_inflight - 1)
            if self._continuous_mode:
                self._set_state(State.LISTENING)
            else:
                self._set_state(State.IDLE)

    def _gate_emit(self, res: dict) -> None:
        """Insert one completed hands-free session («Hey Jarvis … стоп-стоп»):
        pre-clean (voice commands → snippets → замены → фильтр паразитов →
        backtrack) → clean → number-format → paste. «ввод» presses Enter after."""
        text = res.get("text", "")
        if not text:
            return
        # Run the SAME pre-clean pipeline as PTT/continuous. Hands-free used to
        # skip it, so слова-паразиты/заминки («ну / короче / а-а»), замены,
        # голосовые команды и шаблоны в hands-free не применялись.
        text_in, exact_match, actions = self._pre_clean_pipeline(text)
        if exact_match or not self.cleaning_enabled:
            text = text_in
        else:
            text, cleaned = self.cleaner_chain.clean(text_in)
            if not cleaned:
                self._notify("Очистка недоступна — сырой текст")
        if self.config.output.number_format:
            text = itn.normalize(text, self.config.stt.language)
        raw_text = text
        text = self._apply_voice_formatting(text)
        method = self._paste(text)
        if res.get("submit"):                   # «ввод» → press Enter after insert
            try:
                keyboard.send("enter")
            except Exception:
                logger.exception("voice-gate submit Enter failed")
        if actions:
            execute_actions(actions)
        self.history.append(text, raw=raw_text)
        self._show_bubble(text, inject_method=method)
        logger.info(f"Voice-gate: inserted {len(text)} chars")

    def _start_bg_job(self) -> None:
        if not self.transcriber:
            return
        # ONNX engine (GigaAM) is NOT thread-safe: a background
        # pre-decode running concurrently with the final transcription crashes
        # the process natively (0xc0000005 in onnxruntime — confirmed via
        # faulthandler). They're also fast enough that incremental pre-decode
        # buys nothing. So skip the bg job entirely for them; only Whisper
        # (slow, thread-safe) uses it. Streaming insert is Whisper-only too.
        if self.config.stt.engine == "gigaam":
            self._stream_active = False
            with self._lock:
                self._bg_job = None
            return
        cb = None
        if self.config.output.streaming:
            self._stream_inserted = 0
            self._stream_active = True
            cb = self._on_stream_chunk
        else:
            self._stream_active = False
        job = _BgJob(self.recorder, self.transcriber, on_chunk=cb)
        with self._lock:
            self._bg_job = job
        job.start()   # outside the lock — .start()/.finish() can block on inference

    def _on_stream_chunk(self, text: str) -> None:
        """Called from BgJob thread on each decoded chunk while streaming."""
        if not text or not self._stream_active:
            return
        # Add a trailing space so chunks don't glue together.
        out = text.strip() + " "
        method = injector.inject(
            out,
            mode=self.config.output.injection_mode,
            restore_clipboard=False,   # never disturb clipboard mid-utterance
        )
        if method != "none":
            self._stream_inserted += len(out)

    # ── Push-to-talk pipeline ───────────────────────────────────────────────────

    def _process(self) -> None:
        with self._lock:
            if self._processing:
                return
            self._processing = True
            bg_job, self._bg_job = self._bg_job, None
        self._set_state(State.PROCESSING)

        error = False
        audio = None      # kept in scope so the except-handler can back it up
        try:
            # Undo-restore feeds held audio here instead of re-stopping the
            # (already stopped) recorder. Swap under the lock — it's written
            # from the undo path on another thread.
            with self._lock:
                pending, self._pending_audio = self._pending_audio, None
            if pending is not None:
                audio = pending
            else:
                audio = self.recorder.stop()
            # Restore other apps' volume now that we've stopped capturing
            self._duck_restore()

            if audio is None:
                logger.info("Too short, ignored")
                return

            if self.transcriber is None:
                logger.error("Transcriber not ready")
                return

            # On-screen context priming (25/30) — Whisper engine only
            # bg_job pre-decodes audio in 3-second chunks for speed at finish
            # — but each chunk is decoded *without* the surrounding context,
            # which kills accuracy on flowing speech (poems, sentences with
            # repeated words). Only use bg_job's stitched result when the
            # audio is long enough that re-decoding the whole thing would be
            # painful (~20 s on medium / large CPU). For shorter clips we
            # always full-re-decode for maximum quality.
            audio_len_s = len(audio) / SAMPLE_RATE
            if bg_job is not None and audio_len_s > 20.0:
                raw = bg_job.finish(audio)
                logger.info(f"Used bg_job result ({audio_len_s:.1f}s audio)")
            else:
                raw = self.transcriber.transcribe(audio)
                if bg_job is not None:
                    logger.info(
                        f"Bypassed bg_job for quality ({audio_len_s:.1f}s ≤ 20s)"
                    )
            if not raw:
                logger.info("Empty STT result")
                self._backup_failed_audio(audio, "empty")
                self._snd.empty()
                return

            # Pre-cleanup pipeline: voice commands → snippets → backtrack
            text_in, exact_match, actions = self._pre_clean_pipeline(raw)

            if exact_match or not self.cleaning_enabled:
                text = text_in
            else:
                text, cleaned = self.cleaner_chain.clean(text_in)
                if not cleaned:
                    self._notify("Очистка недоступна — вставлен сырой текст")

            # Numbers → digits (24, local ITN path B; works without LLM)
            if self.config.output.number_format:
                text = itn.normalize(text, self.config.stt.language)

            # If we streamed partials, decide whether to keep them as-is or
            # roll back and replace with the cleaned text.
            inserted = self._stream_inserted
            self._stream_active = False
            self._stream_inserted = 0

            raw_text = text
            text = self._apply_voice_formatting(text)
            if inserted > 0:
                method = "sendinput"   # streaming partials => mostly sendinput
                if text.strip() == raw.strip():
                    logger.info(f"Stream: kept partials, {inserted} chars")
                else:
                    injector.send_backspace(inserted)
                    method = self._paste(text)
                    logger.info(f"Stream: rolled back {inserted} → inserted final {len(text)}")
            else:
                method = self._paste(text)

            if actions:
                execute_actions(actions)

            self.history.append(text, raw=raw_text)
            self._show_bubble(text, inject_method=method)
            logger.info(
                f"Inserted {len(text)} chars (snippet={exact_match}, "
                f"actions={len(actions)})"
            )

        except Exception:
            logger.exception("Processing error")
            error = True
            self._backup_failed_audio(audio, "error")
        finally:
            with self._lock:
                self._processing = False
            if error:
                self._set_state(State.ERROR)
                # Clear the error after 3 s — but ONLY if we're still showing it.
                # If a new recording/processing started meanwhile, this stale
                # timer must not stomp the current state back to IDLE.
                threading.Timer(
                    3.0,
                    lambda: self._set_state(State.IDLE)
                    if self._state == State.ERROR else None,
                ).start()
            else:
                self._set_state(State.IDLE)

    # Below this duration an empty STT result is almost always an accidental
    # key tap, not a lost dictation — don't clutter recovery/ with those.
    _BACKUP_MIN_SEC = 2.0

    def _backup_failed_audio(self, audio, reason: str) -> None:
        """Insurance (user choice: failures only). When STT returns nothing or
        crashes, keep the raw audio in recovery/ so a long dictation isn't lost.
        On success nothing is written; trivially short clips are ignored."""
        try:
            if audio is None or len(audio) < int(self._BACKUP_MIN_SEC * SAMPLE_RATE):
                return
            path = audio_backup.save_failed(audio, SAMPLE_RATE, reason)
            self._notify(f"Распознавание не удалось — аудио сохранено: recovery/{path.name}")
        except Exception:
            logger.debug("failed-audio backup error", exc_info=True)

    def _recovery_cleanup_tick(self) -> None:
        """Periodic, time-based cleanup of recovery/ (every 6 h). Age-based, so
        it never wipes a recent backup — see audio_backup.cleanup_old()."""
        try:
            audio_backup.cleanup_old()
        except Exception:
            logger.debug("recovery cleanup tick failed", exc_info=True)
        finally:
            if self.root:
                self.root.after(6 * 60 * 60 * 1000, self._recovery_cleanup_tick)

    # ── Shared helpers ──────────────────────────────────────────────────────────

    # Time window after the last user keystroke during which we *don't*
    # auto-paste — the user is clearly still typing, so jamming our text
    # in mid-sentence would be annoying. Bubble appears instead.
    _TYPING_GUARD_SEC = 3.0

    def _apply_voice_formatting(self, text: str) -> str:
        """Apply voice structure commands («пункт один», «новый абзац», «тире»,
        «первое… второе…») → real lists/paragraphs. Deterministic, never invents
        words. Called BEFORE _paste so callers can store raw vs formatted in
        history (and offer a per-entry toggle). No-op if disabled or no commands."""
        if not self.config.output.voice_formatting:
            return text
        try:
            from text_format import apply_formatting
            return apply_formatting(text)
        except Exception:
            logger.exception("voice formatting failed; using raw text")
            return text

    def _paste(self, text: str) -> str:
        """Returns the injection method used: 'uia' / 'sendinput' / 'clipboard'
        / 'none' / 'skipped_typing'. Callers can decide whether to fall back
        to the bubble."""
        # User-is-typing guard
        idle = time.monotonic() - self._last_user_keypress
        if self._last_user_keypress > 0 and idle < self._TYPING_GUARD_SEC:
            logger.info(
                f"Paste skipped: user pressed a key {idle:.1f}s ago — "
                "showing bubble instead"
            )
            return "skipped_typing"

        if self.config.output.smart_format:
            try:
                from cursor_format import read_caret_context, adjust_for_context
                ctx = read_caret_context()
                text = adjust_for_context(text, ctx)
            except Exception:
                logger.exception("smart_format failed; using raw text")
        # Profanity masking — last step before injection, after punctuation
        # cleanup, so it never fights the cleaner. Deterministic dict.
        # (faithguard.py guarded the now-removed LLM cleaner — it's unused on
        # this path; voice formatting is applied earlier, in
        # _apply_text_transforms, so history keeps raw and formatted variants.)
        if self.config.output.mask_profanity:
            try:
                from profanity import mask_profanity
                text = mask_profanity(text, style=self.config.output.profanity_style)
            except Exception:
                logger.exception("profanity masking failed; using raw text")

        # Clean text for the clipboard auto-copy — captured BEFORE the
        # inter-dictation leading-space hack below, so a manual Ctrl+V doesn't
        # paste a stray leading space.
        clip_text = text

        # Inter-dictation spacing — add a leading space so a quick follow-up
        # dictation isn't glued to the previous insert («…капусту.1.» → «… 1.»).
        # Only when: the previous insert was recent (continuation, not a fresh
        # cursor elsewhere), didn't already end with whitespace/open-bracket, and
        # this text doesn't already start with a separator/punctuation. We track
        # our own inserts (UIA caret reading returns None here, can't be trusted).
        if (text and self._last_paste_char
                and (time.monotonic() - self._last_paste_time) < 8.0
                and self._last_paste_char not in " \t\n([«\"'"
                and text[0] not in " \t\n.,;:!?)»"):
            text = " " + text

        method = injector.inject(
            text,
            mode=self.config.output.injection_mode,
            restore_clipboard=self.config.output.restore_clipboard,
        )
        if method == "none":
            logger.warning("All injection methods failed — bubble is the only path")
        else:
            logger.debug(f"Injected via {method}")
            # Remember the tail for inter-dictation spacing next time.
            self._last_paste_char = text[-1] if text else ""
            self._last_paste_time = time.monotonic()

        # Auto-copy the dictated text to the clipboard so the user can re-paste
        # it manually. Overrides restore_clipboard when on: cancel the restore
        # that clipboard-mode injection scheduled, else it would wipe our text
        # ~4 s later. Runs on success AND on "none" (clipboard is then the only
        # way to recover the text).
        if self.config.output.copy_to_clipboard and clip_text:
            try:
                pyperclip.copy(clip_text)
                injector.cancel_clipboard_restore()
            except Exception:
                logger.debug("copy_to_clipboard failed", exc_info=True)
        return method

    def _show_bubble(self, text: str, inject_method: str = "uia") -> None:
        """Decide whether to pop the paste-fallback bubble.
            bubble_mode = "off"        — never
            bubble_mode = "on_failure" — when injection didn't reach the field
                                         OR when we deliberately skipped paste
                                         because the user is typing
            bubble_mode = "always"     — every time
        """
        mode = self.config.output.bubble_mode or "on_failure"
        if self.config.output.show_bubble:
            mode = "always"
        # «Текст ушёл в буфер, а не прямо в поле» = «некуда вставить»:
        #   none           — все методы инъекции провалились;
        #   skipped_typing — вставку пропустили (пользователь печатает);
        #   clipboard      — вставка пошла через буфер обмена. В режиме
        #                    injection_mode="clipboard" это КАЖДЫЙ раз; в "auto"
        #                    — как фолбэк, когда прямой ввод (UIA/SendInput) не
        #                    удался, т.е. ровно «некуда вставить напрямую».
        # Без "clipboard" в этом списке тост в clipboard-режиме не показывался.
        fellback = inject_method in ("none", "skipped_typing", "clipboard")
        show = mode == "always" or (mode == "on_failure" and fellback)
        if not show:
            # Text reached the field directly → no fallback bubble. But if we
            # auto-copied it (output.copy_to_clipboard), flash a quick, fading
            # «✓ Скопировано» toast so the user knows Ctrl+V is armed. (When the
            # big bubble DOES show, it already says «…скопировано», so we don't
            # double up.) Not on skipped_typing — that path didn't copy.
            if (self.config.output.copy_to_clipboard
                    and inject_method != "skipped_typing"):
                self._show_copied_toast()
            return
        if self.root and self.bubble:
            preview = text.strip()
            # Tell the panel WHY it popped → it picks the right status line and
            # (when the text went to the clipboard) re-copies it so «Ctrl+V» works.
            reason = ("typing" if inject_method == "skipped_typing"
                      else "failed" if inject_method in ("none", "clipboard")
                      else "always")
            # The toast reads «Текст скопирован в буфер обмена» and the bubble
            # re-copies the text for the user — so it MUST stay on the clipboard.
            # Clipboard injection scheduled a restore that would put the OLD
            # clipboard back ~4 s later (emptying the dictated text → the toast
            # «lies»). Cancel that restore whenever we make the «copied» promise.
            if reason in ("failed", "typing"):
                injector.cancel_clipboard_restore()
            self.root.after(0, lambda: self.bubble.show(preview, reason=reason))

    def _show_copied_toast(self) -> None:
        """Quick, self-dismissing «✓ Скопировано в буфер обмена» confirmation
        after an auto-copy (see _show_bubble). Marshalled onto the Tk thread."""
        toast = getattr(self, "copied_toast", None)
        if self.root and toast:
            self.root.after(0, toast.show)

    def _restore_pill_front(self) -> None:
        """Re-assert the pill on top after a transient toast hides. A topmost
        window appearing then vanishing can shuffle the Windows z-order and leave
        the pill buried behind the active window («пилюля пропадает после
        диктовки»). set_behind(False) re-sets -topmost + lift; it does NOT
        deiconify, so a user-hidden pill stays hidden."""
        if self.flowbar:
            try:
                self.flowbar.set_behind(False)
            except Exception:
                logger.debug("restore pill front failed", exc_info=True)

    def _on_bubble_correction(self, original: str, corrected: str) -> None:
        """User pressed «✎ Поправить» and saved a correction in the bubble.
        Diff word-level; auto-add proper-noun-looking words to the vocabulary."""
        try:
            from vocabulary import extract_learnable
            learned = extract_learnable(original, corrected)
            if not learned:
                logger.info("Bubble correction: no learnable words extracted")
                return

            existing = {w.lower() for w in self.config.vocabulary.words}
            added = [w for w in learned if w.lower() not in existing]
            if not added:
                logger.info(f"Bubble correction: all {len(learned)} words already in vocab")
                return

            self.config.vocabulary.words.extend(added)
            save_config(self.config)
            self._apply_runtime_overrides()    # push new vocab to transcriber
            logger.info(f"Auto-learned {len(added)} words: {added}")
            self._notify(f"Выучено: {', '.join(added[:5])}"
                         + (f" +{len(added)-5}" if len(added) > 5 else ""))
        except Exception:
            logger.exception("Auto-learning failed")

    def _build_snippet_objects(self) -> list[Snippet]:
        return [
            Snippet(trigger=s.trigger, body=s.body,
                    match=s.match, case_sensitive=s.case_sensitive)
            for s in self.config.snippets
            if s.trigger and s.body
        ]

    def _build_voice_commands(self) -> list[VoiceCommand]:
        return [
            VoiceCommand(phrase=v.phrase, action=v.action, value=v.value)
            for v in self.config.voice_commands
            if v.phrase
        ]

    def _pre_clean_pipeline(self, raw: str) -> tuple[str, bool, list]:
        """Run pre-LLM transforms in order:
            voice commands → snippets → backtrack.
        Returns (text, snippet_exact_match, action_list)."""
        text = raw
        actions: list = []
        # 1) Voice commands — strip out marker-prefixed phrases and collect actions
        if self.config.output.voice_commands:
            text, actions = extract_commands(
                text,
                self._build_voice_commands(),
                allow_standalone_tail=self.config.output.voice_commands_standalone_tail,
            )
        # 2) Snippets
        text, exact = apply_snippets(text, self._build_snippet_objects())
        if exact:
            return text, True, actions
        # 2.5) Replacement dictionary (22) + phonetic match (26) — canonical terms
        text = apply_replacements(text, self._replacement_rules)
        text = apply_phonetic(text, self._phonetic)
        # 2.6) Filler stripper — deterministic, no LLM. Removes «ну/короче/типа»
        # and hesitations «э-э/эм/мм». Only deletes from a fixed list — never
        # invents.
        if self.config.output.remove_fillers:
            import filler
            text = filler.strip_fillers(text)
        # 3) Backtrack
        if self.config.output.backtrack:
            text = apply_backtrack(text, self.config.stt.language)
        return text, False, actions

    # ── Whisper mode (soft speech) ──────────────────────────────────────────────

    def _toggle_whisper_mode(self, icon=None, item=None) -> None:
        self._whisper_mode = not self._whisper_mode
        self.config.whisper_mode.enabled = self._whisper_mode
        save_config(self.config)
        self._apply_runtime_overrides()
        # If continuous is running, restart with new VAD params so the change
        # takes effect immediately.
        if self._continuous_mode:
            self._stop_continuous()
            self._start_continuous()
        logger.info(f"Whisper mode {'on' if self._whisper_mode else 'off'}")
        self._notify("Тихий режим: " + ("вкл" if self._whisper_mode else "выкл"))

    def _notify(self, message: str) -> None:
        try:
            if self.icon:
                self.icon.notify(message)
        except Exception:
            logger.info(f"Notification: {message}")

    # ── Tray / settings / history ───────────────────────────────────────────────

    def _hooks_pause(self) -> None:
        """Detach ALL global keyboard hooks — used by the Settings hotkey
        capture so pressing the future PTT key doesn't start a recording."""
        try:
            keyboard.unhook_all()
        except Exception:
            logger.exception("hooks pause failed")

    # ── Веб-окна Настроек/Истории (web_ui.html в окне Edge --app) ──────────────

    @staticmethod
    def _find_edge() -> "str | None":
        import shutil
        p = shutil.which("msedge")
        if p:
            return p
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                cand = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if cand.exists():
                    return str(cand)
        return None

    def _open_web_ui(self, page: str) -> bool:
        """Открыть веб-Настройки/Историю отдельным окном Edge (--app: без
        вкладок и адресной строки — выглядит как обычное окно приложения).
        False = не получилось, вызывающий откатывается на старые Tk-окна."""
        try:
            if self._api_server is None:
                self._start_api_server()
                self._api_for_ui = self._api_server is not None
            if self._api_server is None:
                return False
            port = self._api_server.actual_port or self.config.api.port
            from api_server import _ensure_token
            url = (f"http://127.0.0.1:{port}/ui?page={page}"
                   f"&token={_ensure_token()}")
            edge = self._find_edge()
            if edge:
                import subprocess
                subprocess.Popen([edge, f"--app={url}", "--window-size=1040,760"])
            else:
                import webbrowser
                webbrowser.open(url)
            logger.info(f"Web UI opened: page={page} "
                        f"({'edge app' if edge else 'default browser'})")
            return True
        except Exception:
            logger.exception("web UI open failed — falling back to Tk windows")
            return False

    def _show_settings(self, icon=None, item=None) -> None:
        if getattr(self.config.ui, "web_windows", True) and self._open_web_ui("main"):
            return
        if self.root:
            self.root.after(0, lambda: SettingsWindow.open(
                self.root, self._on_settings_saved, self._apply_widget_live,
                player=self._snd, mic_monitor=self._mic_monitor,
                hook_pause=self._hooks_pause,
                hook_resume=self._register_hooks))
            self._suppress_pill()

    def _apply_widget_live(self, cfg: Config) -> None:
        """Lightweight live preview of widget appearance (size/opacity) from the
        Settings header «Виджет −/+» buttons — just repaints the pill, no model
        reload / hook re-register (unlike the full _on_settings_saved)."""
        if self.root and self.flowbar:
            self.root.after(0, lambda: self.flowbar.apply_widget_cfg(cfg.widget))

    # ── Transcribe an audio/video file (Settings → «Транскрибировать аудиофайл») ─

    def _transcribe_file_ui(self) -> None:
        """Pick a file, transcribe it with the LOADED model on a bg thread, drop
        the text into History + the clipboard, and toast the result."""
        if not self.transcriber:
            self._notify("Модель ещё грузится — подожди пару секунд")
            return
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="Выбери аудио/видео файл для транскрибации",
            filetypes=[
                ("Аудио/видео", "*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.aac "
                                "*.mp4 *.mkv *.webm *.mov *.avi"),
                ("Все файлы", "*.*"),
            ])
        if not path:
            return
        self._notify(f"Транскрибирую «{os.path.basename(path)}»…")
        threading.Thread(target=self._transcribe_file_worker, args=(path,),
                         daemon=True, name="file-transcribe").start()

    def _transcribe_file_worker(self, path: str) -> None:
        try:
            from faster_whisper.audio import decode_audio
            from constants import SAMPLE_RATE
            audio = decode_audio(path, sampling_rate=SAMPLE_RATE)   # 16 kHz float32 mono
            text = (self.transcriber.transcribe(audio) or "").strip()
            if not text:
                self._notify("Речь не распознана (пусто)")
                return
            self.history.append(text)
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                logger.debug("_transcribe_file_worker: suppressed", exc_info=True)
            logger.info(f"File transcribed: {os.path.basename(path)} → {len(text)} chars")
            self._notify(f"✓ Готово: {len(text)} симв. — в Истории и буфере обмена")
        except Exception:
            logger.exception("File transcription failed")
            self._notify("Ошибка транскрибации файла — см. лог")

    _BUSY_STATES = (State.RECORDING, State.LISTENING, State.PROCESSING, State.LOADING)

    def _prebuild_windows(self) -> None:
        """Warm the cheap «Вернуть» toast. Тяжёлые Tk-пребилды Настроек/Истории
        (~0.9 s / ~3 s на GUI-потоке) убраны: основные окна теперь веб-страница
        (web_ui.html) и открываются мгновенно; Tk-версии — только фолбэк и
        строятся по требованию."""
        if not self.root:
            return
        if self._state in self._BUSY_STATES:
            self.root.after(3000, self._prebuild_windows)
            return
        try:
            if getattr(self, "undo_toast", None):
                self.undo_toast.prebuild()   # warm the «Вернуть» toast (cheap)
            if getattr(self, "copied_toast", None):
                self.copied_toast.prebuild() # warm the «Скопировано» toast (cheap)
        except Exception:
            logger.debug("toast prebuild failed", exc_info=True)

    def _suppress_pill(self) -> None:
        """Keep the pill on top and visible while a Settings/History window is
        open — the user wants it to stay on screen. (We used to drop its
        topmost flag here, which let other windows cover it so it looked like it
        vanished.) set_behind(False) keeps it always-on-top; it does NOT
        deiconify, so a manually-hidden pill stays hidden."""
        if self.root and self.flowbar:
            self.root.after(0, lambda: self.flowbar.set_behind(False))

    def _show_url_transcribe(self, icon=None, item=None) -> None:
        if self.root:
            self.root.after(0, lambda: UrlTranscribeWindow.open(self.root))

    def _show_history(self, icon=None, item=None) -> None:
        if getattr(self.config.ui, "web_windows", True) and self._open_web_ui("history"):
            return
        if self.root:
            self.root.after(
                0, lambda: HistoryWindow.open(self.root, self.history, self._show_settings,
                                              on_transcribe_file=self._transcribe_file_ui)
            )
            self._suppress_pill()

    def _on_settings_saved(self, cfg: Config) -> None:
        old_stt = (
            self.config.stt.model,
            self.config.stt.language,
            self.config.stt.device,
            self.config.stt.compute_type,
            self.config.stt.cpu_threads,
            self.config.stt.engine,
            self.config.stt.gigaam_model,
        )
        self.config = cfg
        # Theme + widget appearance apply live now (no restart needed).
        _apply_theme(getattr(cfg.ui, "theme", "dark"))
        if self.flowbar:
            try:
                self.flowbar.apply_widget_cfg(cfg.widget)
            except Exception:
                logger.exception("apply_widget_cfg failed")
        self.cleaner_chain = build_cleaner_chain(
            cfg.cleaners,
            punctuation_fallback=cfg.output.punctuation_fallback,
        )
        # Rebuild the gate so the «стоп-стоп» sensitivity from Settings applies
        # without a restart.
        try:
            if getattr(self, "_vgate", None) is not None:
                from voice_gate import VoiceGate
                from voice_triggers import build_stop_phrases
                self._vgate = VoiceGate(
                    stop_phrases=build_stop_phrases(),
                    fuzzy_thr=self.config.wake.stop_fuzzy)
        except Exception:
            logger.debug("voice gate rebuild after save failed", exc_info=True)
        self._rebuild_text_aids()
        self._register_hooks()
        # History policy
        self.history.set_policy(cfg.history.max_entries, cfg.history.retention_days)
        # Update ducker options
        self._ducker.set_options(mode=cfg.audio.duck_mode,
                                  duck_level=cfg.audio.duck_level)
        # API enable/disable toggle
        api_running = self._api_server is not None
        if cfg.api.enabled and not api_running:
            self._start_api_server()
        elif not cfg.api.enabled and api_running and not self._api_for_ui:
            # Если сервер подняли ради веб-окон Настроек/Истории — не гасим,
            # иначе само окно настроек умрёт сразу после «Сохранить».
            self._stop_api_server()

        # Wake-word listener toggle
        wake_running = self._wake_listener is not None
        if cfg.wake.enabled and not wake_running:
            self._start_wake_listener()
        elif not cfg.wake.enabled and wake_running:
            self._stop_wake_listener()
        elif wake_running:
            # Already running → apply the «Hey Jarvis» sensitivity live (the
            # listener reads _threshold every frame, no model reload needed).
            try:
                self._wake_listener._threshold = float(cfg.wake.threshold)
            except Exception:
                logger.debug("wake threshold live-update failed", exc_info=True)
        # Whisper mode flag (could have changed via Settings checkbox)
        self._whisper_mode = cfg.whisper_mode.enabled
        new_stt = (cfg.stt.model, cfg.stt.language, cfg.stt.device,
                   cfg.stt.compute_type, cfg.stt.cpu_threads,
                   cfg.stt.engine, cfg.stt.gigaam_model)
        if new_stt != old_stt:
            self.transcriber = None
            self._set_state(State.LOADING)
            threading.Thread(target=self._load_model, daemon=True).start()
        else:
            # Cheap path: vocabulary / whisper-mode thresholds change live.
            self._apply_runtime_overrides()
        logger.info("Settings saved and applied")

    # ── Language switching ──────────────────────────────────────────────────────

    _LANGUAGES = [
        ("ru", "Русский"),
        ("en", "English"),
        ("de", "Deutsch"),
        ("fr", "Français"),
        ("zh", "中文"),
        ("",   "Авто"),
    ]

    def _set_language(self, lang: str) -> None:
        self.config.stt.language = lang
        if self.transcriber:
            self.transcriber.language = lang or None
        save_config(self.config)
        logger.info(f"Language set to: {lang or 'auto'}")

    def _toggle_cleaning(self, icon=None, item=None) -> None:
        self.cleaning_enabled = not self.cleaning_enabled
        logger.info(f"Cleaning {'on' if self.cleaning_enabled else 'off'}")

    def _cancel_recording(self) -> None:
        """User clicked ✕ — stop the recording WITHOUT pasting, but keep the
        audio for a few seconds so «Вернуть» can restore it. Nothing reaches
        history unless restored.

        The blocking parts — recorder.stop() and audio un-ducking — run on a
        worker thread so this click handler returns immediately. Done inline
        they freeze the Tk loop, so the ✕ scale animation and the «Вернуть»
        toast pop in with a visible stutter. UI flips to IDLE synchronously for
        instant feedback; _set_state and _arm_undo already marshal through
        root.after, so they are safe to call off the main thread."""
        logger.info(f"Cancel clicked: continuous={self._continuous_mode}, "
                    f"recording={self._recording}, state={self._state.value}")
        continuous = self._continuous_mode
        # Continuous mode: STOP the listener (not the PTT recorder — it isn't
        # running here), discard the gate buffer (cancel = don't insert), and end
        # any wake session. Without this the listener kept capturing and the
        # session re-armed itself — «панель сама выскакивает».
        if continuous:
            self._continuous_mode = False
            self._close_stop_watcher()
            if self._continuous_listener:
                try: self._continuous_listener.stop()
                except Exception: logger.exception("cancel: listener stop failed")
                self._continuous_listener = None
            if self.flowbar:
                self.flowbar.set_level_source(None)
            if self._vgate is not None:
                self._vgate.reset()
            self._duck_restore()
            self._set_state(State.IDLE)
            if self._wake_session_timer is not None:
                self._on_wake_session_end()
            logger.info("Continuous recording cancelled by user")
            return
        elif not self._recording:
            return
        else:                                   # PTT
            with self._lock:
                self._recording = False
            self._disarm_watchdog()
            self._stream_active = False
            self._stream_inserted = 0
        with self._lock:
            self._bg_job = None
        self._set_state(State.IDLE)             # instant visual feedback

        def _finish() -> None:
            audio = None
            try:
                audio = self.recorder.stop()
            except Exception:
                logger.exception("cancel: recorder.stop failed")
            self._duck_restore()
            logger.info("%s recording cancelled by user",
                        "Continuous" if continuous else "PTT")
            self._arm_undo(audio)

        threading.Thread(target=_finish, daemon=True,
                         name="cancel-finish").start()

    # ── Undo-cancel ──────────────────────────────────────────────────────────
    _UNDO_SEC = 4.0

    def _arm_undo(self, audio) -> None:
        """Hold the cancelled audio and show the «Вернуть» toast for _UNDO_SEC.
        After the window passes the audio is discarded for good."""
        self._cancel_undo_timer()
        with self._lock:
            self._undo_audio = audio
        if audio is None:
            return
        if self.root and getattr(self, "undo_toast", None):
            self.root.after(0, self.undo_toast.show)
        self._undo_timer = threading.Timer(self._UNDO_SEC, self._clear_undo)
        self._undo_timer.daemon = True
        self._undo_timer.start()

    def _undo_cancel(self) -> None:
        """«Вернуть» pressed — restore the cancelled recording: transcribe and
        insert it (and record to history) via the normal pipeline."""
        self._cancel_undo_timer()
        with self._lock:
            audio, self._undo_audio = self._undo_audio, None
        if audio is None:
            return
        self._set_state(State.PROCESSING)
        with self._lock:
            self._pending_audio = audio
        threading.Thread(target=self._process, daemon=True).start()
        logger.info("Undo cancel — restoring recording")

    def _clear_undo(self) -> None:
        """Undo window elapsed — discard the held audio for good."""
        self._undo_timer = None
        with self._lock:
            self._undo_audio = None
        if self.root and getattr(self, "undo_toast", None):
            self.root.after(0, self.undo_toast.hide)

    def _cancel_undo_timer(self) -> None:
        if self._undo_timer is not None:
            try:
                self._undo_timer.cancel()
            except Exception:
                logger.debug("_cancel_undo_timer: suppressed", exc_info=True)
            self._undo_timer = None

    def _confirm_recording(self) -> None:
        """User clicked ✓ — stop recording and run pipeline (paste)."""
        logger.info(f"Confirm clicked: continuous={self._continuous_mode}, "
                    f"recording={self._recording}, state={self._state.value}")
        if self._continuous_mode:
            self._set_state(State.PROCESSING)
            self._stop_continuous()
            return
        if not self._recording:
            # Already released/cancelled (e.g. _on_release ran first) — do NOT
            # flip to PROCESSING here, or the pill stays "busy" forever with no
            # _process to clear it. Leave the state as-is.
            return
        # Snap UI to "busy" immediately so the user sees feedback the moment
        # they click — the actual processing runs in a thread.
        self._set_state(State.PROCESSING)
        with self._lock:
            self._recording = False
        self._disarm_watchdog()
        threading.Thread(target=self._process, daemon=True).start()

    def _toggle_bubble(self, icon=None, item=None) -> None:
        self.config.output.show_bubble = not self.config.output.show_bubble
        save_config(self.config)
        self._notify("Bubble " + ("on" if self.config.output.show_bubble else "off"))

    def _toggle_overlay(self, icon=None, item=None) -> None:
        self._overlay_visible = not self._overlay_visible
        if self.root and self.flowbar:
            fn = self.flowbar.show if self._overlay_visible else self.flowbar.hide
            self.root.after(0, fn)

    def _open_log(self, icon=None, item=None) -> None:
        try:
            os.startfile(str(LOG_PATH))
        except Exception as e:
            logger.error(f"Cannot open log: {e}")

    def _start_api_server(self) -> None:
        """Spin up the local HTTP API. Failure (e.g. fastapi missing) is logged
        but doesn't crash Talker."""
        try:
            from api_server import ApiServer
            self._api_server = ApiServer(self, port=self.config.api.port)
            self._api_server.start()
        except Exception as e:
            logger.warning(f"API server startup failed: {e}")
            self._notify(f"API не запущен: {e}")
            self._api_server = None

    def _stop_api_server(self) -> None:
        if self._api_server is not None:
            try:
                self._api_server.stop()
            except Exception:
                logger.exception("API stop failed")
            self._api_server = None

    # ── Wake word ──────────────────────────────────────────────────────────────

    def _start_wake_listener(self) -> None:
        try:
            from wake_word import WakeWordListener
            # Кастомная модель («Эй, Талкер»): путь к .onnx в wake.model_path;
            # пусто → встроенная (hey_jarvis).
            custom = (self.config.wake.model_path or "").strip()
            self._wake_listener = WakeWordListener(
                on_wake=self._on_wake_triggered,
                model_name=self.config.wake.model,
                threshold=self.config.wake.threshold,
                cooldown_sec=self.config.wake.cooldown_sec,
                mic_index=self.config.audio.mic_index,
                model_paths=[custom] if custom else None,
            )
            self._wake_listener.start()
        except Exception as e:
            logger.warning(f"Wake listener startup failed: {e}")
            self._notify(f"Wake word не запущен: {e}")
            self._wake_listener = None

    def _stop_wake_listener(self) -> None:
        if self._wake_listener is not None:
            try:
                self._wake_listener.stop()
            except Exception:
                logger.exception("Wake stop failed")
            self._wake_listener = None
        if self._wake_session_timer is not None:
            try: self._wake_session_timer.cancel()
            except Exception: logger.debug("_stop_wake_listener: suppressed", exc_info=True)
            self._wake_session_timer = None

    def _mic_test_acquire(self) -> None:
        """Free the mic for the Settings mic-test meter: pause the wake listener
        (it holds the default device; a 2nd stream on it would read silence)."""
        if self._wake_listener is not None:
            try: self._wake_listener.pause()
            except Exception: logger.exception("mic-test wake pause failed")

    def _mic_test_release(self) -> None:
        """Mic-test meter closed → resume the wake listener."""
        if self._wake_listener is not None:
            try: self._wake_listener.resume()
            except Exception: logger.exception("mic-test wake resume failed")

    def _on_wake_triggered(self, model_name: str) -> None:
        """Wake-word fired. NB: called from the wake listener's AUDIO CALLBACK
        thread — must not touch the stream or block here, so hand off to a
        worker thread that opens the dictation session."""
        logger.info(f"Wake → starting {self.config.wake.session_sec:.0f}s session")
        threading.Thread(target=self._run_wake_session, daemon=True,
                         name="wake-session").start()

    def _run_wake_session(self) -> None:
        if self._state in (State.LOADING,):
            return
        # Медиа-гард для самого wake: громкий фильм/музыка в колонках выбивают
        # «Hey Jarvis» на чужой речи со score до 0.94 — порогом не лечится.
        # Пока колонки громко играют, wake сессию не открывает (wake-слушатель
        # остаётся на взводе; cooldown в 3 с не даёт лог-шторма, плюс свой
        # троттлинг INFO раз в минуту).
        mg = self.config.continuous
        if mg.media_guard:
            # Measure the speaker peak DIRECTLY on this (fresh wake-session)
            # thread. The shared _system_output_peak() routes through the
            # ducker's COM-job queue, which isn't drained while idle (no
            # recording in progress) → it returned None and the guard NEVER
            # fired. CoInitialize lets pycaw run right here.
            peak = None
            try:
                import comtypes
                try: comtypes.CoInitialize()
                except Exception: pass
                try:
                    from system_meter import system_output_peak
                    peak = system_output_peak()
                finally:
                    try: comtypes.CoUninitialize()
                    except Exception: pass
            except Exception:
                logger.debug("wake speaker-peak probe failed", exc_info=True)
            if peak is not None and peak > mg.media_guard_sys_peak:
                now = time.monotonic()
                if now - getattr(self, "_wake_supp_log_t", 0.0) > 60.0:
                    self._wake_supp_log_t = now
                    logger.info(f"Wake suppressed: media is playing "
                                f"(sys_peak={peak:.2f} > {mg.media_guard_sys_peak})")
                else:
                    logger.debug(f"Wake suppressed (sys_peak={peak:.2f})")
                return
        # CRITICAL: free the mic. The wake listener and the continuous listener
        # can't both hold the same input device — the second stream just gets
        # silence (rms=0, nothing recorded). Pause wake for the session, resume
        # it when the session ends.
        if self._wake_listener is not None:
            try: self._wake_listener.pause()
            except Exception: logger.exception("wake pause failed")
        # The wake word IS the «старт»: _start_continuous() already opens the
        # dictation buffer (_vgate.begin) when voice_gate is on, so no spoken
        # «талкер старт» is needed. A stop phrase («джарвис/талкер стоп») ends it.
        if not self._continuous_mode:
            self._start_continuous()

        # Schedule auto-stop
        if self._wake_session_timer is not None:
            self._wake_session_timer.cancel()
        self._wake_session_timer = threading.Timer(
            self.config.wake.session_sec, self._on_wake_session_end,
        )
        self._wake_session_timer.daemon = True
        self._wake_session_timer.start()

    def _on_wake_session_end(self) -> None:
        """End the hands-free session: stop recording and re-arm the wake word —
        like releasing Ctrl+Alt+Space. Fired by the «стоп» phrase or the timeout."""
        if self._wake_session_timer is not None:
            try: self._wake_session_timer.cancel()
            except Exception: logger.debug("_on_wake_session_end: suppressed", exc_info=True)
            self._wake_session_timer = None
        # NB: do NOT reset the gate here — _stop_continuous() flushes the buffer
        # (so a 30 s timeout still inserts what was dictated, not discards it).
        if self._continuous_mode:
            self._stop_continuous()
        # Mic is free again → re-arm the wake listener for the next «Hey Jarvis».
        if self._wake_listener is not None:
            try: self._wake_listener.resume()
            except Exception: logger.exception("wake resume failed")

    def _quit(self, icon=None, item=None) -> None:
        logger.info("Shutting down…")
        self._disarm_watchdog()
        self._stop_api_server()
        self._stop_wake_listener()
        if self._continuous_mode:
            self._stop_continuous()
        if self.config.history.on_quit_clear:
            try:
                self.history.clear()
                logger.info("History cleared on quit (privacy)")
            except Exception:
                logger.exception("on_quit_clear failed")
        try:
            keyboard.unhook_all()
        except Exception:
            logger.debug("_quit: suppressed", exc_info=True)
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                logger.debug("_quit: suppressed", exc_info=True)
        if self.root:
            try:
                # Flush the pill's current position FIRST (on the Tk thread, before
                # quit) so it reopens exactly where it was — the drag-save is
                # debounced and would otherwise miss a «подвинул и сразу закрыл».
                if self.flowbar:
                    self.root.after(0, self.flowbar.save_position_now)
                self.root.after(0, self.root.quit)
            except Exception:
                logger.debug("_quit: suppressed", exc_info=True)
        # Force-exit if anything (keyboard hook thread, pyaudio, uvicorn,
        # pystray's tray thread, GIL-held ctranslate2 worker) is keeping the
        # process alive after the normal mainloop teardown. 1.5 s gives quiet
        # daemons a chance to finish.
        def _force_exit() -> None:
            logger.info("Forcing process exit")
            os._exit(0)
        threading.Timer(1.5, _force_exit).start()

    # ── Entry point ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("Starting Talker")

        self.root = tk.Tk()
        self.root.withdraw()
        _resolve_fonts(self.root)   # pick Inter / Segoe UI Variable now root exists
        self.flowbar = FlowBar(
            self.root,
            self.recorder,
            on_open=self._show_history,
            on_settings=self._show_settings,
            on_quit=self._quit,
            on_url=self._show_url_transcribe,
            on_whisper_toggle=self._toggle_whisper_mode,
            widget_cfg=self.config.widget,
            on_bubble_toggle=self._toggle_bubble,
            on_cancel=self._cancel_recording,
            on_confirm=self._confirm_recording,
            on_record=self._toggle_continuous,   # left-click pill → start/stop
        )
        self.bubble = PasteFallbackBubble(
            self.root,
            anchor_xy=self.flowbar.anchor_xy,
            anchor_size=self.flowbar.anchor_size,   # centre the toast over the pill
            on_correction=self._on_bubble_correction,
        )
        self.undo_toast = CancelUndoToast(
            self.root,
            anchor_xy=self.flowbar.anchor_xy,
            on_undo=self._undo_cancel,
            seconds=4.0,
        )
        # Quick «✓ Скопировано в буфер обмена» confirmation after an auto-copy.
        # on_hidden re-lifts the pill: a transient topmost window can shuffle the
        # z-order and leave the pill buried behind the active window otherwise.
        self.copied_toast = ClipboardToast(
            self.root,
            anchor_xy=self.flowbar.anchor_xy,
            on_hidden=self._restore_pill_front,
        )
        # Toast window — appears during PROCESSING (after user spoke) so it's
        # obvious that we're transcribing, not idle. Hidden during plain
        # startup (model loading) — that's covered by the spinner in the pill.
        self.loading_win = LoadingWindow(self.root)

        # No startup window at all — loading is shown by the PILL's own spinner
        # (State.LOADING). _hide_splash is a safe no-op now.
        self.splash = None
        # CRITICAL: FADE THE PILL IN before the blocking backend preload below
        # freezes the main thread. Otherwise the un-drawn pill shows as a black box
        # during load. update_idletasks() alone only settles geometry/transparency;
        # it does NOT run the pill's _tick (an after-callback) so the alpha never
        # fades in — pump a few real frames here instead. The loading indicator is
        # a STATIC dot (no animation — the CPU-bound model load can't sustain a
        # smooth spinner anyway), so once it's faded in there's nothing to keep
        # pumping: it just sits visible until the bg load flips the pill to IDLE.
        try:
            _fade_until = time.monotonic() + 0.35
            while time.monotonic() < _fade_until:
                self.root.update()
                time.sleep(0.016)
        except Exception:
            logger.debug("run: suppressed", exc_info=True)

        # Pre-import the heavy STT backend HERE, on the main thread. Importing
        # onnx_asr (which loads onnxruntime's native DLLs) lazily inside the bg
        # worker thread during recording crashes the process natively
        # (0xc0000005 in _ctypes.pyd — confirmed via faulthandler). Doing it on
        # the main thread at startup makes the later worker `import onnx_asr` a
        # cached no-op, so the bg pre-decode never triggers the native load.
        if self.config.stt.engine == "gigaam":
            try:
                import onnx_asr  # noqa: F401
                # CRITICAL: warm numpy.fft on the MAIN thread too. GigaAM's
                # onnx_asr preprocessor calls numpy.fft lazily during the FIRST
                # real transcription — and that lazy sub-module import (pulls in
                # native pocketfft via _ctypes) crashes the process with
                # 0xc0000005 when it happens on the bg worker thread (confirmed
                # via faulthandler crash.log: numpy/fft/__init__ → _ctypes).
                # Importing it here makes the worker's use a cached no-op.
                import numpy.fft  # noqa: F401
                import numpy.linalg  # noqa: F401  (same lazy-native-load risk)
                _ = numpy.fft.rfft(np.zeros(16, dtype=np.float32))  # force native init
                logger.info("Pre-imported onnx_asr + numpy.fft on main thread")
            except Exception:
                logger.warning("onnx_asr/numpy preload failed", exc_info=True)

        # (Embedded local LLM «gemma» removed — no llama_cpp preload needed.)

        threading.Thread(target=self._load_model, daemon=True).start()
        self._register_hooks()

        # Pre-build History + Settings windows in the background (hidden) a few
        # seconds after launch, so the FIRST open is instant instead of stalling
        # ~0.9 s / ~3 s on the build. Skips while busy (see _prebuild_windows).
        self.root.after(4000, self._prebuild_windows)

        # Housekeep the failed-dictation backups (recovery/). Time-based, NOT
        # session-based: a sweep on every startup that wiped the previous run's
        # files would defeat the point after a system failure. cleanup_old()
        # deletes purely by age, so recent backups (incl. a just-crashed session)
        # survive. First sweep ~9 s after launch, then every 6 h.
        self.root.after(9000, self._recovery_cleanup_tick)

        # Optional local HTTP API
        if self.config.api.enabled:
            self._start_api_server()
        else:
            # Грев для веб-окон Настроек/Истории: поднять сервер заранее
            # (localhost-only, под токеном), чтобы первый клик по «Настройки»
            # открывал окно сразу, а не ждал импорта fastapi и старта uvicorn.
            # В worker-потоке — импорт fastapi на Tk-потоке заметно бы фризил.
            def _warm_api() -> None:
                if self._api_server is None:
                    self._start_api_server()
                    self._api_for_ui = self._api_server is not None
            self.root.after(5000, lambda: threading.Thread(
                target=_warm_api, daemon=True, name="api-warm").start())

        # Optional wake-word listener
        if self.config.wake.enabled:
            self._start_wake_listener()

        menu = pystray.Menu(
            # default=True → double-click (activate) on the tray icon opens this.
            pystray.MenuItem("История", self._show_history, default=True),
            pystray.MenuItem("Настройки", self._show_settings),
            # Checkbox: shows a tick while the pill is visible. Toggling hides it
            # (and a user-hidden pill stays hidden across dictations — set_state
            # respects FlowBar._user_hidden). The tray stays reachable even when
            # the pill is hidden, so this is the way back.
            pystray.MenuItem("Показывать виджет", self._toggle_overlay,
                             checked=lambda item: self._overlay_visible),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._quit),
        )

        # Use the CURRENT state, not a hardcoded LOADING: a fast engine (GigaAM
        # from a warm cache, ~1 s) can finish loading — and call _set_state(IDLE)
        # — BEFORE this line runs. Back then self.icon was still None, so
        # _set_state skipped the tray (the `if self.icon:` guard) and the pill
        # re-sync below only fixes the pill. Reading self._state here means the
        # tray opens already showing «готов» instead of a frozen «Загрузка
        # модели…». If still loading, it correctly shows LOADING and the later
        # _set_state(IDLE) updates the live icon (self.icon is no longer None).
        self.icon = pystray.Icon(
            "Talker", self._icons[self._state], _TRAY_TITLES[self._state], menu=menu,
        )
        self.icon.run_detached()

        # A fast engine (GigaAM ~1 s) can finish loading on the bg thread BEFORE
        # mainloop is up — that _set_state(IDLE)'s root.after() then raises "main
        # thread is not in main loop" and is swallowed (see _set_state), so the
        # pill is stuck on the LOADING spinner forever ("вечная загрузка"). Once
        # mainloop is running, re-apply the real current state to the pill so a
        # finished load actually clears the spinner. Harmless if still loading.
        if self.flowbar:
            self.root.after(150, lambda: self.flowbar.set_state(self._state.value))

        self.root.mainloop()


if __name__ == "__main__":
    try:
        # File-mode: invoked via Explorer context menu or CLI. Skip tray, run
        # pipeline, exit. Detected by presence of --transcribe in argv.
        if any(a == "--transcribe" or a.startswith("--transcribe=") for a in sys.argv[1:]):
            from file_mode import run_cli
            sys.exit(run_cli(sys.argv[1:]))
        App().run()
    except SystemExit:
        raise
    except BaseException:
        # Under pythonw.exe there is no console, so an uncaught exception here is
        # lost (sys.stderr is None) and the app just vanishes. Log it first.
        logger.exception("Fatal: Talker crashed during startup/run")
        raise
