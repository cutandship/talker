from __future__ import annotations

import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
import tomllib

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.toml"

# Serialize concurrent saves (GUI thread + /vocabulary API + web-UI, all on
# the uvicorn thread) so they can't collide on the temp file.
_save_lock = threading.Lock()

_DEFAULT = """\
[hotkey]
key  = "right alt"      # клавиша push-to-talk
mode = "hold"           # "hold" или "toggle"

[stt]
engine       = "gigaam"  # "gigaam" — русский, быстрый | "whisper" — любой язык
gigaam_model = "gigaam-v3-e2e-rnnt"
model        = "small"   # размер whisper: tiny | base | small | medium | large-v3
language     = "ru"      # "" = автоопределение, "ru", "en" и т.д.
device       = "cpu"     # "cpu" | "cuda" | "auto"
compute_type = "int8"    # cpu: int8 / float32;  cuda: float16 / int8_float16
cpu_threads  = 0         # 0 = авто (физические ядра); поднимать на CPU с большим числом ядер

[output]
restore_clipboard = true
show_bubble       = true   # всплывашка с кнопкой «Копировать» после каждой расшифровки

# Цепочка очистки — пробуется сверху вниз, берётся первый результат.
# Раскомментируйте нужные блоки и заполните api_key / model.

# [[cleaner]]
# type     = "api"
# base_url = "https://openrouter.ai/api/v1"
# api_key  = "sk-or-..."
# model    = "meta-llama/llama-3.2-3b-instruct:free"

# [[cleaner]]
# type  = "ollama"
# url   = "http://localhost:11434"
# model = "gemma2:2b"

# Непрерывный режим (Ctrl+Alt+Space): параметры VAD.
[continuous]
silence_secs       = 1.2  # секунды тишины для завершения реплики
vad_aggressiveness = 1    # 0=мягкий, 1=умеренный, 2=жёсткий, 3=очень жёсткий
vad_engine         = "auto"  # "auto" | "ten" | "webrtc";  auto = ten если установлен

# Аудио: микрофон и предобработка перед транскрипцией.
[audio]
normalize       = true   # нормализовать уровень записи (рекомендуется)
noise_reduction = false  # спектральное шумоподавление (добавляет ~1-2 с)
mic_index       = -1     # -1 = системный по умолчанию
mic_gain        = 1.0    # программное усиление; 2.5 в whisper-mode для тихой речи

# Whisper Mode — тихая / шёпотная речь. Активируется хоткеем или из меню.
[whisper_mode]
enabled                 = false
hotkey                  = ""    # например "ctrl+alt+w"; пусто = только из меню
mic_gain                = 2.5
vad_aggressiveness      = 0
silence_secs            = 1.8
no_speech_threshold     = 0.4   # ниже = терпимее к тихой речи

# История.
[history]
max_entries     = 1000
retention_days  = 0     # 0 = бессрочно; иначе удалять записи старше N дней
on_quit_clear   = false # очищать всё при выходе

# Словарь для Whisper initial_prompt (имена, термины, бренды).
[vocabulary]
words = []

# Сниппеты: триггер → текст. См. match:
#   "exact"    — вся фраза равна триггеру (fuzzy)
#   "prefix"   — фраза начинается с триггера, остальное идёт в {param}
#   "anywhere" — заменить триггер внутри текста
# В body можно использовать {param}, {date}, {time}.
#
# [[snippet]]
# trigger = "моя подпись"
# body    = "С уважением,\\nГеоргий"
# match   = "exact"

# Запасной вариант: вставить сырой STT-текст без очистки.
[[cleaner]]
type = "noop"
"""


@dataclass
class HotkeyConfig:
    key: str = "right alt"
    mode: str = "hold"


@dataclass
class SttConfig:
    model: str = "small"
    language: str = "ru"
    device: str = "cpu"          # "cpu" | "cuda" | "auto"
    compute_type: str = "int8"   # int8 / float16 / float32 / int8_float16
    cpu_threads: int = 0         # 0 = авто (физическое число ядер ≈ os.cpu_count()//2)
    # GigaAM по умолчанию: продукт RU-first, модель ~250 MB против ~1.6 GB у
    # whisper large-v3-turbo, грузится за секунды и пунктуирует сама. Whisper
    # остаётся выбором для других языков (переключается в Настройках).
    engine: str = "gigaam"       # "gigaam" (русский) | "whisper" (любой язык)
    gigaam_model: str = "gigaam-v3-e2e-rnnt"   # onnx-asr id (ru-only)
    context_priming: bool = False  # 25 — OFF по умолчанию: чтение каретки через UIA
                                   # ДО транскрипции валит worker-поток (нативный
                                   # краш COM). Включать после фикса COM-init потока.
    context_ocr: bool = False      # 30 — OCR-скриншот окна в контекст (медленнее)
    speculative: bool = False      # 34 — спекулятивный декод (Whisper, не large-v3)
    draft_model: str = ""          # 34 — draft-модель (distil-whisper/tiny)


@dataclass
class OutputConfig:
    restore_clipboard: bool = True
    show_bubble: bool = False           # legacy "always show"
    bubble_mode: str = "on_failure"     # "always" | "on_failure" | "off"
    injection_mode: str = "auto"   # "auto" | "uia" | "sendinput" | "clipboard"
    streaming: bool = False        # вставлять текст по ходу речи (см. _BgJob)
    punctuation_fallback: bool = False  # GigaAM v3 пунктуирует сама → фолбэк OFF по умолчанию
    remove_fillers: bool = True    # детерминированный скрипт-стриппер паразитов и
                                   # звуков-заминок («ну/короче/типа», «э-э/эм/мм») —
                                   # работает без ИИ (filler.py); ничего не
                                   # выдумывает, только режет.
    smart_format: bool = False     # OFF: GigaAM v3 пишет чисто; контекст-фиксы давали глюки
    backtrack: bool = False        # OFF по умолчанию: эвристика «нет, я имел в виду …» переправляла лишнее
    voice_commands: bool = True    # inline команды («talker новый абзац»)
    voice_commands_standalone_tail: bool = False   # opt-in: команда без маркера в конце
    voice_gate: bool = False       # hands-free «Hey Jarvis … стоп-стоп» (continuous)
    number_format: bool = False       # 24 — числа цифрами (itn.py); OFF: мисфайрит на GigaAM v3
    paste_last_hotkey: str = ""       # 33 — «вставить последний транскрипт»
    scratchpad_hotkey: str = ""       # 33 — открыть скретчпад последних транскриптов
    mask_profanity: bool = False      # маскировать мат («хуй» → «х*й») перед вставкой
    profanity_style: str = "vowels"   # vowels («х*й») | edges («х**й»)
    voice_formatting: bool = True     # форматировать списки/абзацы по голосовым
                                      # командам («пункт один», «новый абзац»,
                                      # «тире», «первое… второе…») — text_format.py


@dataclass
class CleanerConfig:
    # LLM cleaners removed — only "noop" (passthrough) and "punctuation" remain.
    type: str = "noop"


@dataclass
class ContinuousConfig:
    silence_secs: float = 1.2
    vad_aggressiveness: int = 1
    vad_engine: str = "auto"          # "auto" | "ten" | "webrtc"
    # "single_shot": continuous hotkey toggles a long recording (one paste
    #                at the end). Waveform shows live mic level.
    # "vad_segments": old behaviour — VAD chops speech into chunks, each
    #                 chunk gets inserted as soon as it's ready.
    mode: str = "single_shot"
    # Медиа-гард: если колонки громко играют (фильм/музыка), а сигнал в
    # микрофоне слабый — сегмент считается утечкой звука колонок и НЕ
    # вставляется. Голос рядом с микрофоном заметно громче утечки и проходит.
    media_guard: bool = True
    media_guard_sys_peak: float = 0.12   # «колонки играют», если пик вывода выше
    media_guard_min_rms: float = 0.015   # ниже этого RMS при играющих колонках — дроп


@dataclass
class AudioConfig:
    normalize: bool = True
    noise_reduction: bool = False
    mic_index: int = -1               # -1 = system default
    mic_gain: float = 1.0
    source: str = "mic"               # "mic" | "system" (WASAPI loopback)
    duck_other_apps: bool = False     # mute Spotify/YouTube while recording
    duck_level: float = 0.2           # 0.0 = full mute, 1.0 = no change
    duck_mode: str = "master"         # "master" = system volume (recommended)
                                      # "sessions" = per-app (skips Talker)
    # Noise-reduction tuning. "non_stationary" handles real-life backgrounds
    # (fans, traffic, AC) far better than the old "stationary" mode.
    nr_mode: str = "non_stationary"   # "stationary" | "non_stationary"
    nr_strength: float = 0.85         # 0.0 = no reduction, 1.0 = max (may
                                      # eat speech consonants if too high)


@dataclass
class WhisperModeConfig:
    """Tuned settings for soft / whispered speech."""
    enabled: bool = False
    hotkey: str = ""                  # пусто = без хоткея, только из меню
    mic_gain: float = 2.5
    vad_aggressiveness: int = 0
    silence_secs: float = 1.8
    no_speech_threshold: float = 0.4


@dataclass
class HistoryConfig:
    max_entries: int = 1000
    retention_days: int = 0           # 0 = бессрочно
    on_quit_clear: bool = False


@dataclass
class SoundsConfig:
    # Earcon'ы диктовки (концепт 36, часть A). Значения start/stop/pre_stop/empty
    # = имена вариантов из sounds.PALETTE (дефолты = sounds.DEFAULT_SELECTION).
    enabled: bool = True
    volume: float = 0.9
    start: str = "Восходящий"
    stop: str = "Нисходящий"
    pre_stop: str = "Тик"
    empty: str = "Два низких"


@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)   # 28 — не предлагать снова


@dataclass
class SnippetConfig:
    trigger: str = ""
    body: str = ""
    match: str = "exact"              # "exact" | "prefix" | "anywhere"
    case_sensitive: bool = False


@dataclass
class ReplacementConfig:
    """22 — детерминированная замена после STT. `from` (TOML) → `from_` (Python,
    т.к. `from` — ключевое слово)."""
    to: str = ""
    from_: list[str] = field(default_factory=list)
    whole_word: bool = True
    sounds: str = ""          # 26 — как звучит (источник фонокода)
    phonetic: bool = False    # 26 — ловить близкие по звучанию


@dataclass
class UiConfig:
    font_scale: float = 1.9           # global font multiplier (1.0 = old default)
    theme: str = "dark"               # "dark" | "light" | "system" (follow OS)
    onboarding_shown: bool = False    # one-time first-run tip near the pill
    web_windows: bool = True          # Настройки/История в окне Edge (web_ui.html);
                                      # false = старые Tk-окна


@dataclass
class WidgetConfig:
    """Floating pill (FlowBar) appearance and position."""
    scale: float = 0.6                # multiplier on top of UI scale
    opacity: float = 0.8              # window alpha (0.0 transparent, 1.0 solid)
    show_listening_label: bool = False  # show "Слушаю" text in continuous mode
    show_glow: bool = False           # pulsing colored halo around the pill
    show_when_idle: bool = True       # hide entirely while idle (still visible while recording)
    # Last known position of the pill (top-left, including glow padding).
    # -1 means "use the default corner" — set on first launch and on drag.
    # LEGACY: still honoured as a 'free' absolute position when pos_x >= 0.
    pos_x: int = -1
    pos_y: int = -1
    # Position model (concept 36-C): anchor zone + offset. Survives resolution/
    # monitor changes (zones recompute). anchor="free" → off_x/off_y are the
    # absolute top-left; snap pulls a drop near a zone back onto that zone.
    anchor: str = "bottom-center"
    off_x: int = 0
    off_y: int = 0
    snap: bool = True


@dataclass
class VoiceCommandConfig:
    phrase: str = ""
    action: str = "insert"     # "insert" | "key"
    value: str = ""


@dataclass
class WakeConfig:
    """Hands-free wake-word activation. Optional — requires openwakeword."""
    enabled: bool = False
    model: str = "hey_jarvis"
    # Кастомная wake-модель («Эй, Талкер» и т.п.): путь к .onnx. Пусто →
    # используется встроенная `model` выше.
    model_path: str = ""
    threshold: float = 0.75         # «Hey Jarvis» порог: ниже = срабатывает легче,
                                    # но и больше ложных (модель путает фон)
    cooldown_sec: float = 3.0
    session_sec: float = 30.0       # how long the continuous-listen session lasts after a wake
    stop_fuzzy: float = 0.82        # «стоп-стоп» fuzzy-порог: ниже = ловит сильнее
                                    # искажённое «стоп» (но и чаще ложно)
    # Аудио-стоп (stop_word.py): .onnx-модели («стоп-стоп», «Талкер стоп»),
    # которые слушают кадры сессии и закрывают её мгновенно, без ожидания
    # VAD-паузы и STT. Пустой список = только текстовый стоп.
    stop_models: list[str] = field(default_factory=list)
    stop_threshold: float = 0.6


@dataclass
class ApiConfig:
    """Local HTTP API for external integrations (Raycast/vim/scripts).
    Disabled by default; binds to 127.0.0.1 only. See concept/14."""
    enabled: bool = False
    port: int = 7869


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    continuous: ContinuousConfig = field(default_factory=ContinuousConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cleaners: list[CleanerConfig] = field(
        default_factory=lambda: [CleanerConfig(type="noop")]
    )
    whisper_mode: WhisperModeConfig = field(default_factory=WhisperModeConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    sounds: SoundsConfig = field(default_factory=SoundsConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    snippets: list[SnippetConfig] = field(default_factory=list)
    replacements: list[ReplacementConfig] = field(default_factory=list)
    ui: UiConfig = field(default_factory=UiConfig)
    widget: WidgetConfig = field(default_factory=WidgetConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    voice_commands: list[VoiceCommandConfig] = field(default_factory=list)
    wake: WakeConfig = field(default_factory=WakeConfig)


def _pick(data, cls) -> dict:
    # A section that isn't a table (old/hand-edited config, e.g. `hotkey = "x"`
    # instead of `[hotkey]`) must not crash the whole load — return no overrides
    # so the dataclass defaults stand.
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in cls.__dataclass_fields__}


def _escape_toml_str(s: str) -> str:
    """Escape `"` and `\\` inside a double-quoted TOML string. Newlines become \\n."""
    return (s.replace("\\", "\\\\")
              .replace('"', '\\"')
              .replace("\n", "\\n")
              .replace("\r", "")
              .replace("\t", "\\t"))


def save_config(cfg: Config) -> None:
    esc = _escape_toml_str   # every string field goes through the escaper —
    # a stray «"» or backslash in any of them must not corrupt the TOML.
    lines: list[str] = [
        "[hotkey]",
        f'key  = "{esc(cfg.hotkey.key)}"',
        f'mode = "{esc(cfg.hotkey.mode)}"',
        "",
        "[stt]",
        f'model        = "{esc(cfg.stt.model)}"',
        f'language     = "{esc(cfg.stt.language)}"',
        f'device       = "{esc(cfg.stt.device)}"',
        f'compute_type = "{esc(cfg.stt.compute_type)}"',
        f'cpu_threads  = {cfg.stt.cpu_threads}',
        f'engine       = "{esc(cfg.stt.engine)}"',
        f'gigaam_model = "{esc(cfg.stt.gigaam_model)}"',
        f'context_priming = {"true" if cfg.stt.context_priming else "false"}',
        f'context_ocr     = {"true" if cfg.stt.context_ocr else "false"}',
        f'speculative     = {"true" if cfg.stt.speculative else "false"}',
        f'draft_model     = "{esc(cfg.stt.draft_model)}"',
        "",
        "[output]",
        f'restore_clipboard = {"true" if cfg.output.restore_clipboard else "false"}',
        f'show_bubble       = {"true" if cfg.output.show_bubble else "false"}',
        f'bubble_mode       = "{esc(cfg.output.bubble_mode)}"',
        f'injection_mode    = "{esc(cfg.output.injection_mode)}"',
        f'streaming         = {"true" if cfg.output.streaming else "false"}',
        f'punctuation_fallback = {"true" if cfg.output.punctuation_fallback else "false"}',
        f'remove_fillers    = {"true" if cfg.output.remove_fillers else "false"}',
        f'smart_format      = {"true" if cfg.output.smart_format else "false"}',
        f'backtrack         = {"true" if cfg.output.backtrack else "false"}',
        f'voice_commands    = {"true" if cfg.output.voice_commands else "false"}',
        f'voice_commands_standalone_tail = {"true" if cfg.output.voice_commands_standalone_tail else "false"}',
        f'voice_gate        = {"true" if cfg.output.voice_gate else "false"}',
        f'number_format      = {"true" if cfg.output.number_format else "false"}',
        f'paste_last_hotkey  = "{_escape_toml_str(cfg.output.paste_last_hotkey)}"',
        f'scratchpad_hotkey  = "{_escape_toml_str(cfg.output.scratchpad_hotkey)}"',
        f'mask_profanity     = {"true" if cfg.output.mask_profanity else "false"}',
        f'profanity_style    = "{esc(cfg.output.profanity_style)}"',
        f'voice_formatting   = {"true" if cfg.output.voice_formatting else "false"}',
        "",
        "[continuous]",
        f"silence_secs       = {cfg.continuous.silence_secs}",
        f"vad_aggressiveness = {cfg.continuous.vad_aggressiveness}",
        f'vad_engine         = "{esc(cfg.continuous.vad_engine)}"',
        f'mode               = "{esc(cfg.continuous.mode)}"',
        f'media_guard          = {"true" if cfg.continuous.media_guard else "false"}',
        f"media_guard_sys_peak = {cfg.continuous.media_guard_sys_peak}",
        f"media_guard_min_rms  = {cfg.continuous.media_guard_min_rms}",
        "",
        "[audio]",
        f"normalize       = {'true' if cfg.audio.normalize else 'false'}",
        f"noise_reduction = {'true' if cfg.audio.noise_reduction else 'false'}",
        f"mic_index       = {cfg.audio.mic_index}",
        f"mic_gain        = {cfg.audio.mic_gain}",
        f'source          = "{esc(cfg.audio.source)}"',
        f'duck_other_apps = {"true" if cfg.audio.duck_other_apps else "false"}',
        f"duck_level      = {cfg.audio.duck_level}",
        f'duck_mode       = "{esc(cfg.audio.duck_mode)}"',
        f'nr_mode         = "{esc(cfg.audio.nr_mode)}"',
        f"nr_strength     = {cfg.audio.nr_strength}",
        "",
        "[whisper_mode]",
        f'enabled             = {"true" if cfg.whisper_mode.enabled else "false"}',
        f'hotkey              = "{esc(cfg.whisper_mode.hotkey)}"',
        f"mic_gain            = {cfg.whisper_mode.mic_gain}",
        f"vad_aggressiveness  = {cfg.whisper_mode.vad_aggressiveness}",
        f"silence_secs        = {cfg.whisper_mode.silence_secs}",
        f"no_speech_threshold = {cfg.whisper_mode.no_speech_threshold}",
        "",
        "[history]",
        f"max_entries    = {cfg.history.max_entries}",
        f"retention_days = {cfg.history.retention_days}",
        f'on_quit_clear  = {"true" if cfg.history.on_quit_clear else "false"}',
        "",
        "[sounds]",
        f'enabled  = {"true" if cfg.sounds.enabled else "false"}',
        f"volume   = {cfg.sounds.volume}",
        f'start    = "{_escape_toml_str(cfg.sounds.start)}"',
        f'stop     = "{_escape_toml_str(cfg.sounds.stop)}"',
        f'pre_stop = "{_escape_toml_str(cfg.sounds.pre_stop)}"',
        f'empty    = "{_escape_toml_str(cfg.sounds.empty)}"',
        "",
        "[vocabulary]",
        "words = [" + ", ".join(f'"{_escape_toml_str(w)}"' for w in cfg.vocabulary.words) + "]",
        "blacklist = [" + ", ".join(f'"{_escape_toml_str(w)}"' for w in cfg.vocabulary.blacklist) + "]",
        "",
        "[ui]",
        f"font_scale = {cfg.ui.font_scale}",
        f'theme = "{esc(cfg.ui.theme)}"',
        f'onboarding_shown = {"true" if cfg.ui.onboarding_shown else "false"}',
        f'web_windows = {"true" if cfg.ui.web_windows else "false"}',
        "",
        "[widget]",
        f"scale                 = {cfg.widget.scale}",
        f"opacity               = {cfg.widget.opacity}",
        f'show_listening_label  = {"true" if cfg.widget.show_listening_label else "false"}',
        f'show_glow             = {"true" if cfg.widget.show_glow else "false"}',
        f'show_when_idle        = {"true" if cfg.widget.show_when_idle else "false"}',
        f"pos_x                 = {cfg.widget.pos_x}",
        f"pos_y                 = {cfg.widget.pos_y}",
        f'anchor                = "{esc(cfg.widget.anchor)}"',
        f"off_x                 = {cfg.widget.off_x}",
        f"off_y                 = {cfg.widget.off_y}",
        f'snap                  = {"true" if cfg.widget.snap else "false"}',
        "",
        "[api]",
        f'enabled = {"true" if cfg.api.enabled else "false"}',
        f"port    = {cfg.api.port}",
        "",
        "[wake]",
        f'enabled      = {"true" if cfg.wake.enabled else "false"}',
        f'model        = "{esc(cfg.wake.model)}"',
        f'model_path   = "{esc(cfg.wake.model_path)}"',
        f"threshold    = {cfg.wake.threshold}",
        f"cooldown_sec = {cfg.wake.cooldown_sec}",
        f"session_sec  = {cfg.wake.session_sec}",
        f"stop_fuzzy   = {cfg.wake.stop_fuzzy}",
        "stop_models  = [" + ", ".join(f'"{esc(m)}"' for m in cfg.wake.stop_models) + "]",
        f"stop_threshold = {cfg.wake.stop_threshold}",
        "",
    ]

    for s in cfg.snippets:
        lines.append("[[snippet]]")
        lines.append(f'trigger        = "{_escape_toml_str(s.trigger)}"')
        lines.append(f'body           = "{_escape_toml_str(s.body)}"')
        lines.append(f'match          = "{esc(s.match)}"')
        lines.append(f'case_sensitive = {"true" if s.case_sensitive else "false"}')
        lines.append("")

    for r in cfg.replacements:
        lines.append("[[replacement]]")
        lines.append(f'to         = "{_escape_toml_str(r.to)}"')
        froms = ", ".join(f'"{_escape_toml_str(x)}"' for x in r.from_)
        lines.append(f"from       = [{froms}]")
        lines.append(f'whole_word = {"true" if r.whole_word else "false"}')
        lines.append(f'sounds     = "{_escape_toml_str(r.sounds)}"')
        lines.append(f'phonetic   = {"true" if r.phonetic else "false"}')
        lines.append("")

    for vc in cfg.voice_commands:
        lines.append("[[voice_command]]")
        lines.append(f'phrase = "{_escape_toml_str(vc.phrase)}"')
        lines.append(f'action = "{esc(vc.action)}"')
        lines.append(f'value  = "{_escape_toml_str(vc.value)}"')
        lines.append("")

    for c in cfg.cleaners:
        lines.append("[[cleaner]]")
        lines.append(f'type = "{esc(c.type)}"')
        lines.append("")
    # Atomic write so a crash mid-save can't leave a truncated config.toml that
    # fails to parse on the next start. A UNIQUE mkstemp name (not a fixed
    # .toml.tmp) + a process-wide lock so concurrent writers don't truncate each
    # other's temp file or hit "file in use" on os.replace under Windows.
    data = "\n".join(lines)
    with _save_lock:
        fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".toml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, CONFIG_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def update_config(mutate) -> Config:
    """Read-modify-write: load a FRESH config, apply `mutate(cfg)`, save.

    The only safe way to persist a partial change. Config has many independent
    writers (Settings form, pill position, tray toggles, auto-learned
    vocabulary) — saving a long-lived snapshot silently reverts everyone
    else's writes. Callers that own only a couple of fields must go through
    here instead of save_config(их_старый_снапшот)."""
    cfg = load_config()
    mutate(cfg)
    save_config(cfg)
    return cfg


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(_DEFAULT, encoding="utf-8")

    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        # Corrupt / truncated / unparseable TOML (crash mid-save, bad hand-edit).
        # Don't let the app vanish silently at startup — preserve the bad file
        # for inspection and fall back to defaults.
        logger.error(f"config.toml unreadable ({e}); backing up → config.toml.corrupt, using defaults")
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH.with_suffix(".toml.corrupt"))
        except Exception:
            pass
        CONFIG_PATH.write_text(_DEFAULT, encoding="utf-8")
        return Config()

    try:
        return _map_config(data)
    except Exception as e:
        # A malformed section (wrong shape / type that slips past _pick) must not
        # kill startup. All Config fields have safe defaults, so fall back rather
        # than vanish. (_pick already neutralises the common scalar-section case.)
        logger.error(f"config.toml has a bad section ({e}); using defaults")
        return Config()


def _map_config(data: dict) -> Config:
    cfg = Config()
    if "hotkey" in data:
        cfg.hotkey = HotkeyConfig(**_pick(data["hotkey"], HotkeyConfig))
    if "stt" in data:
        cfg.stt = SttConfig(**_pick(data["stt"], SttConfig))
    if "output" in data:
        cfg.output = OutputConfig(**_pick(data["output"], OutputConfig))
    if "continuous" in data:
        cfg.continuous = ContinuousConfig(**_pick(data["continuous"], ContinuousConfig))
    if "audio" in data:
        cfg.audio = AudioConfig(**_pick(data["audio"], AudioConfig))
    if "whisper_mode" in data:
        cfg.whisper_mode = WhisperModeConfig(**_pick(data["whisper_mode"], WhisperModeConfig))
    if "history" in data:
        cfg.history = HistoryConfig(**_pick(data["history"], HistoryConfig))
    if "sounds" in data:
        cfg.sounds = SoundsConfig(**_pick(data["sounds"], SoundsConfig))
    if "vocabulary" in data:
        words = data["vocabulary"].get("words", [])
        blacklist = data["vocabulary"].get("blacklist", [])
        cfg.vocabulary = VocabularyConfig(
            words=[str(w) for w in words if w],
            blacklist=[str(w) for w in blacklist if w],
        )
    if "snippet" in data:
        cfg.snippets = [SnippetConfig(**_pick(s, SnippetConfig)) for s in data["snippet"]]
    if "replacement" in data:
        cfg.replacements = [
            ReplacementConfig(
                to=str(r.get("to", "")),
                from_=[str(x) for x in r.get("from", []) or []],
                whole_word=bool(r.get("whole_word", True)),
                sounds=str(r.get("sounds", "")),
                phonetic=bool(r.get("phonetic", False)),
            )
            for r in data["replacement"]
        ]
    if "ui" in data:
        cfg.ui = UiConfig(**_pick(data["ui"], UiConfig))
    if "widget" in data:
        cfg.widget = WidgetConfig(**_pick(data["widget"], WidgetConfig))
    if "api" in data:
        cfg.api = ApiConfig(**_pick(data["api"], ApiConfig))
    if "voice_command" in data:
        cfg.voice_commands = [
            VoiceCommandConfig(**_pick(v, VoiceCommandConfig))
            for v in data["voice_command"]
        ]
    if not cfg.voice_commands:
        from voice_commands import default_commands
        cfg.voice_commands = [VoiceCommandConfig(**c) for c in default_commands()]
    if "wake" in data:
        cfg.wake = WakeConfig(**_pick(data["wake"], WakeConfig))
    if "cleaner" in data:
        cfg.cleaners = [CleanerConfig(**_pick(c, CleanerConfig)) for c in data["cleaner"]]
    # NB: TALKER_API_KEY env override is applied at runtime in
    # build_cleaner_chain (cleaner.py), NOT injected into Config here — injecting
    # it would leak the secret into config.toml on the next save_config().

    return cfg
