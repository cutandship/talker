"""Звуковые earcon'ы диктовки (концепт 36, часть A).

Модель: 4 СОБЫТИЯ × 5 ВАРИАНТОВ каждое → пользователь собирает «комбинацию»
(свой вариант на старт, на стоп, на pre-stop, на пустой результат). Плюс
готовые ПРЕСЕТЫ-комбинации для быстрого выбора.

ПОЧЕМУ ИГРАЕМ ИЗ ФАЙЛА (важно)
------------------------------
На машине пользователя `winsound.PlaySound(SND_MEMORY)` (звук из памяти) —
молчал, а `SND_FILENAME` (звук из .wav-файла) — играл. Поэтому выбранный
вариант СИНТЕЗИРУЕТСЯ (numpy) и РЕНДЕРИТСЯ в кэш-файл `assets/sounds/cache/`,
а играется через `SND_FILENAME | SND_ASYNC`. Синтез = ноль ассетов и любые
комбинации; файловый проигрыш = надёжная слышимость.

Свой звук: положить `assets/sounds/custom/<event>.wav` — он перебивает синтез.

КОНТРАКТ С DUCKER'ОМ (соблюдает main.py)
----------------------------------------
play start → ducker.duck() → record    |    record stop → ducker.restore() → play stop
Иначе earcon будет приглушён. pre_stop — только в mic-режиме (не в loopback).

Зависимости: numpy (есть), winsound (stdlib, Windows). Ничего нового.
"""
from __future__ import annotations

import io
import logging
import sys
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    import winsound  # stdlib, Windows-only
    _HAVE_WINSOUND = True
except Exception:                       # pragma: no cover - не-Windows
    _HAVE_WINSOUND = False

_ASSET_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_SR = 44_100
_FADE_MS = 2.0


# ── Синтез-примитивы ─────────────────────────────────────────────────────────

def _glide(f0: float, f1: float, dur_ms: float, sr: int = _SR) -> np.ndarray:
    """Фазо-непрерывный тон со скольжением частоты f0→f1 (без щелчков)."""
    n = max(1, int(sr * dur_ms / 1000.0))
    freq = np.linspace(f0, f1, n)
    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    return np.sin(phase)


def _adsr(n: int, sr: int, attack_ms: float, decay_ms: float,
          sustain: float, release_ms: float) -> np.ndarray:
    a = min(n, int(sr * attack_ms / 1000.0))
    d = min(n - a, int(sr * decay_ms / 1000.0))
    r = min(n - a - d, int(sr * release_ms / 1000.0))
    s = max(0, n - a - d - r)
    env = np.concatenate([
        np.linspace(0.0, 1.0, a, endpoint=False) if a else np.array([]),
        np.linspace(1.0, sustain, d, endpoint=False) if d else np.array([]),
        np.full(s, sustain),
        np.linspace(sustain, 0.0, r) if r else np.array([]),
    ])
    if env.size < n:
        env = np.pad(env, (0, n - env.size), constant_values=0.0)
    return env[:n]


def _edge_fade(x: np.ndarray, sr: int = _SR, ms: float = _FADE_MS) -> np.ndarray:
    k = min(len(x) // 2, int(sr * ms / 1000.0))
    if k <= 0:
        return x
    ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, k)))
    x[:k] *= ramp
    x[-k:] *= ramp[::-1]
    return x


def _mix_to_wav_bytes(mono: np.ndarray, volume: float, sr: int = _SR) -> bytes:
    """numpy float (-1..1) → WAV-образ в памяти (16-bit PCM mono)."""
    mono = _edge_fade(np.asarray(mono, dtype=np.float64))
    peak = float(np.max(np.abs(mono))) or 1.0
    mono = (mono / peak) * float(np.clip(volume, 0.0, 1.0))
    pcm = np.clip(mono * 32767.0, -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ── Строительные блоки для вариантов ─────────────────────────────────────────

def _tone(f0, f1, dur, a=10, d=35, sus=0.85, rel=None):
    rel = dur * 0.35 if rel is None else rel
    s = _glide(f0, f1, dur)
    return s * _adsr(len(s), _SR, a, d, sus, rel)


def _blip(f, dur):                       # короткий перкуссивный
    s = _glide(f, f, dur)
    return s * _adsr(len(s), _SR, 3, 18, 0.0, dur * 0.6)


def _seq(parts, gap_ms):                 # склейка с паузами (двойные сигналы)
    gap = np.zeros(int(_SR * gap_ms / 1000.0))
    out = []
    for i, p in enumerate(parts):
        if i:
            out.append(gap)
        out.append(p)
    return np.concatenate(out)


# ── ПАЛИТРА: 5 вариантов на каждое событие ───────────────────────────────────
# (имя, builder→numpy). Имя видит пользователь в меню.

PALETTE = {
    "start": [
        ("Восходящий",   lambda: _tone(520, 880, 130)),
        ("Двойной вверх", lambda: _seq([_blip(620, 70), _blip(880, 90)], 40)),
        ("Мягкая капля", lambda: _tone(840, 600, 150, a=4, d=60, sus=0.5)),
        ("Чирп",         lambda: _tone(660, 1040, 110, a=2, d=30, sus=0.8)),
        ("Тук",          lambda: _blip(780, 80)),
    ],
    "stop": [
        ("Нисходящий",   lambda: _tone(820, 520, 150)),
        ("Двойной вниз", lambda: _seq([_blip(820, 80), _blip(560, 100)], 40)),
        ("Мягкий",       lambda: _tone(560, 440, 170, a=6, d=70, sus=0.5)),
        ("Чёткий",       lambda: _tone(990, 620, 120, a=2, d=30, sus=0.8)),
        ("Тук",          lambda: _blip(560, 90)),
    ],
    "pre_stop": [
        ("Тик",          lambda: _blip(700, 60)),
        ("Высокий тик",  lambda: _blip(900, 50)),
        ("Двойной тик",  lambda: _seq([_blip(740, 40), _blip(740, 40)], 50)),
        ("Низкий тик",   lambda: _blip(500, 60)),
        ("Клик",         lambda: _blip(1100, 35)),
    ],
    "empty": [
        ("Два низких",      lambda: _seq([_blip(400, 70), _blip(400, 70)], 60)),
        ("Нисходящий мягкий", lambda: _tone(520, 360, 200, a=6, d=90, sus=0.5)),
        ("Бузз",            lambda: _tone(300, 300, 180, a=4, d=20, sus=0.7)),
        ("Тихий двойной",   lambda: _seq([_blip(440, 60), _blip(440, 60)], 70)),
        ("Минор",           lambda: _seq([_blip(520, 90), _blip(415, 120)], 50)),
    ],
}
EVENTS = ("start", "stop", "pre_stop", "empty")
EVENT_LABELS = {
    "start":    "Старт записи",
    "stop":     "Стоп записи",
    "pre_stop": "Перед остановкой",
    "empty":    "Пустой результат",
}
DEFAULT_SELECTION = {k: PALETTE[k][0][0] for k in EVENTS}   # первый вариант каждого

# Готовые комбинации (быстрый выбор в меню)
PRESETS = {
    "Мягкий":  {"start": "Восходящий", "stop": "Нисходящий", "pre_stop": "Тик", "empty": "Два низких"},
    "Чёткий":  {"start": "Чирп", "stop": "Чёткий", "pre_stop": "Высокий тик", "empty": "Минор"},
    "Тихий":   {"start": "Мягкая капля", "stop": "Мягкий", "pre_stop": "Низкий тик", "empty": "Нисходящий мягкий"},
    "Минимал": {"start": "Тук", "stop": "Тук", "pre_stop": "Клик", "empty": "Тихий двойной"},
}


def variant_names(event: str) -> list[str]:
    return [n for n, _ in PALETTE.get(event, [])]


def _variant_index(event: str, name: str) -> int:
    names = variant_names(event)
    return names.index(name) if name in names else 0


def _build(event: str, name: str) -> np.ndarray:
    return PALETTE[event][_variant_index(event, name)][1]()


# ── Плеер ───────────────────────────────────────────────────────────────────

class SoundPlayer:
    """Проигрыватель earcon'ов с выбором варианта на каждое событие.

        snd = SoundPlayer(enabled=True, volume=0.9,
                          selection={"start": "Чирп", "stop": "Мягкий"})
        snd.start(); snd.stop()                  # точки вызова — в main.py
        snd.set_variant("empty", "Минор")        # из меню настроек
    """

    def __init__(self, enabled: bool = True, volume: float = 0.9,
                 selection: dict | None = None,
                 cache_dir: str | None = None, custom_dir: str | None = None) -> None:
        self.enabled = bool(enabled)
        self.volume = float(volume)
        self.selection = dict(DEFAULT_SELECTION)
        if selection:
            self.selection.update({k: v for k, v in selection.items() if k in EVENTS})
        base = _ASSET_BASE / "assets" / "sounds"
        self._cache = Path(cache_dir) if cache_dir else base / "cache"
        self._custom = Path(custom_dir) if custom_dir else base / "custom"

    @classmethod
    def from_config(cls, cfg) -> "SoundPlayer":
        """Из объекта вида cfg.sounds с полями enabled/volume/start/stop/pre_stop/empty."""
        s = getattr(cfg, "sounds", cfg)
        sel = {k: getattr(s, k, DEFAULT_SELECTION[k]) for k in EVENTS}
        return cls(enabled=bool(getattr(s, "enabled", True)),
                   volume=float(getattr(s, "volume", 0.9)), selection=sel)

    # ── настройка из меню ───────────────────────────────────────────────────
    def set_variant(self, event: str, name: str) -> None:
        if event in EVENTS and name in variant_names(event):
            self.selection[event] = name

    def update(self, *, enabled=None, volume=None, selection=None) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if volume is not None:
            self.volume = float(volume)
        if selection:
            self.selection.update({k: v for k, v in selection.items() if k in EVENTS})

    # ── проигрывание ────────────────────────────────────────────────────────
    def _vtag(self) -> int:                       # квантуем громкость до шагов 5%
        return int(round(self.volume * 20)) * 5   # → не плодим кэш-файлы

    def _custom_path(self, event: str) -> Path | None:
        p = self._custom / f"{event}.wav"
        return p if p.exists() else None

    def _render_path(self, event: str) -> Path:
        name = self.selection.get(event, DEFAULT_SELECTION[event])
        idx = _variant_index(event, name)
        self._cache.mkdir(parents=True, exist_ok=True)
        p = self._cache / f"{event}_{idx}_v{self._vtag()}.wav"
        if not p.exists():
            p.write_bytes(_mix_to_wav_bytes(_build(event, name), self.volume))
        return p

    def play(self, event: str) -> None:
        """Проиграть выбранный вариант события. Тихо ничего не делает, если
        выключено / нет winsound; ошибки → лог warning (не молча)."""
        if not self.enabled or not _HAVE_WINSOUND or event not in EVENTS:
            return
        try:
            p = self._custom_path(event) or self._render_path(event)
            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            logger.warning("SoundPlayer.play(%s) failed", event, exc_info=True)

    # Обёртки — точки вызова в main.py
    def start(self):    self.play("start")     # ДО ducker.duck()
    def stop(self):     self.play("stop")      # ПОСЛЕ ducker.restore()
    def pre_stop(self): self.play("pre_stop")  # только mic-режим
    def empty(self):    self.play("empty")


# ── Демо/проверка ───────────────────────────────────────────────────────────
#   python sounds.py            — список палитры (4×5)
#   python sounds.py dump       — рендер всех вариантов в _sound_samples/<event>_<вариант>.wav
#   python sounds.py play       — проиграть дефолтную комбинацию (через файлы)
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    logging.basicConfig(level=logging.WARNING)

    if cmd == "dump":
        out = _ASSET_BASE / "_sound_samples"
        out.mkdir(exist_ok=True)
        for ev in EVENTS:
            for i, (name, make) in enumerate(PALETTE[ev]):
                (out / f"{ev}_{i}.wav").write_bytes(_mix_to_wav_bytes(make(), 0.9))
        print("Рендер 20 вариантов в:", out)
        raise SystemExit

    for ev in EVENTS:
        print(f"\n{ev} ({EVENT_LABELS[ev]}):")
        for i, (name, make) in enumerate(PALETTE[ev]):
            ms = len(make()) / _SR * 1000.0
            print(f"  {i}. {name:18} {ms:5.0f} ms")

    if cmd == "play":
        if not _HAVE_WINSOUND:
            print("\nwinsound недоступен на этой платформе"); raise SystemExit
        import time
        p = SoundPlayer()
        print("\nпроигрываю дефолтную комбинацию (из файлов)...")
        for ev in EVENTS:
            print("  ", ev, "→", p.selection[ev]); p.play(ev); time.sleep(1.0)
        time.sleep(0.3)
