# -*- coding: utf-8 -*-
"""Генерация синтетических сэмплов для тренировки русских wake/stop-моделей
openWakeWord — нативно на Windows (официальный piper-путь — Linux-only).

Голоса:
  - Silero TTS v4_ru (aidar, baya, kseniya, xenia, eugene) — локально, GPU;
    разнообразие через SSML prosody (rate × pitch).
  - edge-tts (ru-RU-SvetlanaNeural, ru-RU-DmitryNeural) — нейроголоса
    Microsoft, сетевые; кэшируются, чтобы не долбить API.

Каждый базовый рендер размножается случайным time-stretch (0.85–1.15) до
нужного количества. Негативы: рукописные «почти-фразы» (near-miss) + обычные
русские предложения из ../_corpus/clean.txt.

Раскладка под train.py:  out/{model}/{positive_train,positive_test,
negative_train,negative_test}/*.wav  (16 kHz mono int16)

Запуск (venv):  venv\\Scripts\\python.exe gen_samples.py [model ...]
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import random
import sys
from pathlib import Path

import numpy as np
import scipy.io.wavfile
import scipy.signal

HERE = Path(__file__).parent
OUT = HERE / "out"
CACHE = HERE / "tts_cache"
CORPUS = HERE.parent / "_corpus" / "clean.txt"
SR = 16_000

random.seed(36)

# ── Фразы ─────────────────────────────────────────────────────────────────────
# positive: варианты написания = варианты произношения.
# adversarial: near-miss, на которых модель НЕ должна срабатывать.
MODELS = {
    "ey_talker": {
        "positive": ["эй талкер", "эй, талкер", "эй толкер", "хэй талкер"],
        "adversarial": [
            "эй", "талкер", "толкер", "токарь", "трекер", "хакер", "спикер",
            "эй ты", "эй слушай", "эй брат", "эй погоди", "эй смотри",
            "талант", "толк", "тальк", "талмуд", "эй доктор", "это талант",
            "эй парень", "эй привет", "докер", "паркер", "тикер",
        ],
    },
    "stop_stop": {
        "positive": ["стоп стоп", "стоп-стоп", "стоп, стоп"],
        "adversarial": [
            "стоп", "стол", "сток", "стон", "сноп", "стопка", "штопор",
            "топ топ", "хлоп хлоп", "стой стой", "топот", "автостоп",
            "стоп кран", "стоп машина", "стоп игра", "нон-стоп", "стоп снято",
            "сто процентов", "стоп слово",
            "стоп да",            # команда отправки — НЕ простой стоп
        ],
    },
    "talker_stop": {
        "positive": ["талкер стоп", "талкер, стоп", "толкер стоп"],
        "adversarial": [
            "талкер старт", "талкер", "стоп", "доктор стоп", "трекер стоп",
            "токарь стой", "толкование", "паркер стоп", "талкер привет",
            "автостоп", "стоп кран", "талкер запиши", "стоп машина",
        ],
    },
    # «стоп-да» = закончить диктовку И нажать Enter (отправить). Коротко и
    # артикулируется легко; «да» фонетически далеко от «стоп» → модель не
    # путается со «стоп-стоп». Главный негатив — восклицание «стоп, да ладно».
    "stop_da": {
        "positive": ["стоп да", "стоп, да", "стоп-да"],
        "adversarial": [
            "стоп", "стоп стоп", "да", "да да", "да да да", "ну да",
            "да ладно", "стоп да ладно", "стой да", "сток да", "стоп дай",
            "стоп два", "стоп там", "стоп да нет", "вот да",
        ],
    },
}

N_TRAIN = 4000          # позитивов в train (и столько же негативов)
N_TEST = 400
CORPUS_SHARE = 0.4      # доля «обычной речи» среди негативов

SILERO_SPEAKERS = ["aidar", "baya", "kseniya", "xenia", "eugene"]
SILERO_RATES = ["slow", "medium", "fast"]
SILERO_PITCHES = ["low", "medium", "high"]
EDGE_VOICES = ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]
EDGE_RATES = ["-20%", "+0%", "+20%"]
EDGE_PITCHES = ["-20Hz", "+0Hz", "+20Hz"]

# ── TTS-движки ────────────────────────────────────────────────────────────────

_silero = None


def silero_tts(text: str, speaker: str, rate: str, pitch: str) -> np.ndarray:
    global _silero
    import torch
    if _silero is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model, _ = torch.hub.load("snakers4/silero-models", "silero_tts",
                                  language="ru", speaker="v4_ru",
                                  trust_repo=True)
        model.to(device)
        _silero = model
        print(f"  silero loaded on {device}")
    ssml = (f'<speak><prosody rate="{rate}" pitch="{pitch}">{text}'
            f"</prosody></speak>")
    audio = _silero.apply_tts(ssml_text=ssml, speaker=speaker,
                              sample_rate=48000)
    x = audio.detach().cpu().numpy().astype(np.float32)
    return scipy.signal.resample_poly(x, 1, 3)        # 48k → 16k


def edge_tts_render(text: str, voice: str, rate: str, pitch: str) -> np.ndarray:
    import edge_tts

    async def _run() -> bytes:
        c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buf = b""
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        return buf

    mp3 = asyncio.run(_run())
    import av
    out = []
    with av.open(io.BytesIO(mp3)) as cont:
        stream = cont.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for frame in cont.decode(stream):
            for rf in resampler.resample(frame):
                out.append(rf.to_ndarray().reshape(-1))
    pcm = np.concatenate(out).astype(np.float32) / 32768.0
    return pcm


# ── Утилиты ───────────────────────────────────────────────────────────────────

def trim_silence(x: np.ndarray, thr: float = 0.01, pad_ms: int = 60) -> np.ndarray:
    """Срезать тишину по краям (Silero щедро паддит), оставив небольшой зазор."""
    if x.size == 0:
        return x
    energy = np.abs(x)
    idx = np.where(energy > thr * max(1e-6, float(energy.max())))[0]
    if idx.size == 0:
        return x
    pad = SR * pad_ms // 1000
    lo, hi = max(0, idx[0] - pad), min(len(x), idx[-1] + pad)
    return x[lo:hi]


def time_stretch(x: np.ndarray, factor: float) -> np.ndarray:
    """Наивный стретч ресемплингом (меняет и темп, и тон — это и нужно для
    разнообразия)."""
    n = max(1, int(round(len(x) / factor)))
    t_old = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    t_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(t_new, t_old, x).astype(np.float32)


def save_wav(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1.0, 1.0)
    scipy.io.wavfile.write(str(path), SR, (x * 32767).astype(np.int16))


def cached_render(engine: str, text: str, voice: str, rate: str,
                  pitch: str) -> "np.ndarray | None":
    """Базовый рендер с дисковым кэшем (edge-tts — сетевой, бережём API)."""
    CACHE.mkdir(exist_ok=True)
    key = hashlib.md5(f"{engine}|{text}|{voice}|{rate}|{pitch}".encode()).hexdigest()
    f = CACHE / f"{key}.wav"
    if f.exists():
        sr, data = scipy.io.wavfile.read(str(f))
        return data.astype(np.float32) / 32768.0
    try:
        if engine == "silero":
            x = silero_tts(text, voice, rate, pitch)
        else:
            x = edge_tts_render(text, voice, rate, pitch)
    except Exception as e:
        print(f"  ! render failed ({engine}/{voice}/{rate}/{pitch}): {e}")
        return None
    x = trim_silence(x)
    if len(x) < SR // 10:                     # < 100 мс — брак
        return None
    save_wav(f, x)
    return x


def corpus_sentences(limit: int = 400) -> list[str]:
    """Обычные русские предложения (негативы «фоновая речь»)."""
    if not CORPUS.exists():
        return []
    sents: list[str] = []
    for line in CORPUS.read_text(encoding="utf-8", errors="ignore").splitlines():
        for s in line.replace("!", ".").replace("?", ".").split("."):
            s = s.strip()
            if 12 <= len(s) <= 90:
                sents.append(s)
    random.shuffle(sents)
    return sents[:limit]


def base_renders(texts: list[str], tag: str) -> list[np.ndarray]:
    """Все базовые рендеры для набора текстов: silero (все комбо) + edge."""
    out: list[np.ndarray] = []
    total = (len(texts) * len(SILERO_SPEAKERS) * len(SILERO_RATES)
             * len(SILERO_PITCHES))
    done = 0
    for text in texts:
        for spk in SILERO_SPEAKERS:
            for rate in SILERO_RATES:
                for pitch in SILERO_PITCHES:
                    x = cached_render("silero", text, spk, rate, pitch)
                    if x is not None:
                        out.append(x)
                    done += 1
        for voice in EDGE_VOICES:
            for rate in EDGE_RATES:
                for pitch in EDGE_PITCHES:
                    x = cached_render("edge", text, voice, rate, pitch)
                    if x is not None:
                        out.append(x)
        print(f"  [{tag}] {done}/{total} silero-рендеров, всего баз: {len(out)}")
    return out


def fill_dir(dst: Path, bases: list[np.ndarray], n: int) -> None:
    """Размножить базовые рендеры случайным стретчем до n файлов."""
    dst.mkdir(parents=True, exist_ok=True)
    have = len(list(dst.glob("*.wav")))
    if have >= n * 0.95:
        print(f"  {dst.name}: уже {have} файлов — пропуск")
        return
    i = have
    while i < n:
        x = random.choice(bases)
        f = random.uniform(0.85, 1.15)
        save_wav(dst / f"{i:06d}.wav", time_stretch(x, f))
        i += 1
    print(f"  {dst.name}: {i} файлов")


def generate_model(name: str) -> None:
    spec = MODELS[name]
    print(f"\n=== {name} ===")
    pos = base_renders(spec["positive"], f"{name}/pos")
    if not pos:
        raise RuntimeError("ни одного позитивного рендера — TTS не работает")
    neg_texts = list(spec["adversarial"])
    n_corpus = int(len(neg_texts) * CORPUS_SHARE / (1 - CORPUS_SHARE)) + 20
    extra = corpus_sentences(n_corpus)
    # Корпусные предложения дороги в рендере (длинные) — по 1 голосу на штуку.
    neg = base_renders(neg_texts, f"{name}/neg")
    for k, s in enumerate(extra):
        eng = "silero"
        spk = random.choice(SILERO_SPEAKERS)
        x = cached_render(eng, s, spk, "medium", "medium")
        if x is not None:
            neg.append(x)
        if k % 50 == 0:
            print(f"  [{name}/corpus] {k}/{len(extra)}")
    root = OUT / name
    fill_dir(root / "positive_train", pos, N_TRAIN)
    fill_dir(root / "positive_test", pos, N_TEST)
    fill_dir(root / "negative_train", neg, N_TRAIN)
    fill_dir(root / "negative_test", neg, N_TEST)


if __name__ == "__main__":
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    targets = sys.argv[1:] or list(MODELS)
    for t in targets:
        generate_model(t)
    print("\nGEN DONE")
