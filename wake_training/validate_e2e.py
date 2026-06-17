# -*- coding: utf-8 -*-
"""Честная end-to-end валидация wake/stop-моделей: синтезируем реальное аудио
целевой фразы (Silero, голоса вне обучающего набора по возможности) и чужих
фраз, прогоняем через openwakeword.Model.predict (он сам считает мелспек+
эмбеддинг — тот же путь, что в продакшене Talker), берём пиковый score.

Модель считается рабочей, если СВОЯ фраза даёт высокий пик (>0.5), а чужие —
низкий. Печатает таблицу; код возврата 0 только если ВСЕ 4 прошли.
"""
import sys
import numpy as np

import gen_samples as g
from openwakeword.model import Model

FINAL = "out/final"
TESTS = {
    "ey_talker":   "эй талкер",
    "stop_stop":   "стоп стоп",
    "talker_stop": "талкер стоп",
    "stop_da":     "стоп да",
}
OTHERS = ["привет как дела", "что сегодня по плану", "открой браузер пожалуйста"]
SPEAKERS = ["aidar", "baya", "xenia", "kseniya"]


def peak_score(model, text, speaker):
    a = g.cached_render("silero", text, speaker, "medium", "medium")
    if a is None:
        return 0.0
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    model.reset()
    peak = 0.0
    for i in range(0, len(pcm) - 1280, 1280):
        s = model.predict(pcm[i:i + 1280])
        peak = max(peak, float(list(s.values())[0]))
    return peak


def main() -> int:
    print(f'{"модель":<13}{"своя фраза":>11}{"чужие":>9}{"вердикт":>10}')
    all_ok = True
    rows = []
    for name, phrase in TESTS.items():
        path = f"{FINAL}/{name}.onnx"
        try:
            m = Model(wakeword_models=[path], inference_framework="onnx")
        except Exception as e:
            print(f"{name:<13}  load FAIL: {e}")
            all_ok = False
            continue
        own = max(peak_score(m, phrase, spk) for spk in SPEAKERS[:3])
        other = max(peak_score(m, o, SPEAKERS[0]) for o in OTHERS)
        ok = own > 0.5 and own > other * 1.5
        all_ok = all_ok and ok
        rows.append((name, own, other, ok))
        print(f"{name:<13}{own:>11.3f}{other:>9.3f}{'OK' if ok else 'МЁРТВАЯ':>10}")
    print("E2E_RESULT:", "ALL_OK" if all_ok else "NOT_OK")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
