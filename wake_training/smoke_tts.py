# -*- coding: utf-8 -*-
"""Смоук генератора: по одному реальному рендеру Silero (GPU) и edge-tts."""
import gen_samples as g

print("[silero] загрузка модели на GPU + рендер...")
x = g.cached_render("silero", "эй талкер", "aidar", "medium", "medium")
print("  silero:", None if x is None else f"{len(x)} samples, {len(x)/16000:.2f}s")

print("[edge] нейроголос Microsoft...")
y = g.cached_render("edge", "эй талкер", "ru-RU-DmitryNeural", "+0%", "+0Hz")
print("  edge:", None if y is None else f"{len(y)} samples, {len(y)/16000:.2f}s")

print("SMOKE", "OK" if (x is not None and y is not None) else "FAIL")
