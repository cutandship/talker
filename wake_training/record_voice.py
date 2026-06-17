# -*- coding: utf-8 -*-
"""Запись своего голоса для дообучения wake-моделей.

Простой скрипт: говорит, какую фразу сказать, записывает с микрофона, сохраняет
в out/<модель>/positive_train/ (туда же, где синтетические примеры). Дописывает
реальные примеры к синтетическим — это и есть domain adaptation (см.
docs/КАК_ДООБУЧИТЬ.md).

Запуск (из папки wake_training):
    venv\\Scripts\\python.exe record_voice.py ey_talker

Аргумент — какую модель дозаписываем:
    ey_talker | talker_stop | stop_stop | stop_da

Скажешь фразу N раз (скрипт скажет когда). Чем больше и разнообразнее
(громко/тихо, быстро/медленно, ближе/дальше от микрофона) — тем лучше.
Рекомендуется 30-50 повторов.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import scipy.io.wavfile
import sounddevice as sd

HERE = Path(__file__).parent
SR = 16_000
DUR = 2.0                       # секунд на одну запись
N_DEFAULT = 30                  # сколько повторов по умолчанию

PHRASES = {
    "ey_talker":   "Эй, Талкер",
    "talker_stop": "Талкер, стоп",
    "stop_stop":   "Стоп-стоп",
    "stop_da":     "Стоп, да",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in PHRASES:
        print("Использование: python record_voice.py <модель>")
        print("Модели:", ", ".join(PHRASES))
        return 1
    model = sys.argv[1]
    phrase = PHRASES[model]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else N_DEFAULT

    out_dir = HERE / "out" / model / "positive_train"
    out_dir.mkdir(parents=True, exist_ok=True)
    # нумеруем с «real_», чтобы не затирать синтетические (000000.wav и т.п.)
    existing = len(list(out_dir.glob("real_*.wav")))

    print("=" * 56)
    print(f"  Запись фразы:  «{phrase}»")
    print(f"  Повторов:      {n}")
    print(f"  Папка:         {out_dir}")
    print("=" * 56)
    print("\nКак записывать хорошо:")
    print("  • говори ЕСТЕСТВЕННО, как в жизни")
    print("  • меняй: громче/тише, быстрее/медленнее, ближе/дальше")
    print("  • можно с лёгким фоновым шумом (так даже реалистичнее)")
    print("\nНажми Enter, когда готов начать...")
    input()

    saved = 0
    for i in range(n):
        idx = existing + i
        print(f"\n[{i+1}/{n}]  Скажи: «{phrase}»")
        for c in (3, 2, 1):
            print(f"   запись через {c}...", end="\r")
            time.sleep(0.6)
        print("   🎤 ГОВОРИ!        ")
        rec = sd.rec(int(DUR * SR), samplerate=SR, channels=1, dtype="float32")
        sd.wait()
        audio = rec.reshape(-1)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.01:
            print("   ⚠️  тихо/тишина — пропускаю, повтори громче")
            continue
        # нормализуем пик к 0.9, режем в int16
        audio = audio / peak * 0.9
        path = out_dir / f"real_{idx:05d}.wav"
        scipy.io.wavfile.write(str(path), SR, (audio * 32767).astype(np.int16))
        saved += 1
        print(f"   ✓ сохранено ({peak:.2f} громкость)            ")

    print(f"\nГотово: записано {saved} реальных примеров «{phrase}».")
    print("Теперь дообучи модель — см. docs/КАК_ДООБУЧИТЬ.md, шаг 4.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
