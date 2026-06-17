# -*- coding: utf-8 -*-
"""Заглушка вместо piper-sample-generator (Linux-only из-за piper-phonemize).

train.py безусловно импортирует generate_samples даже в режимах
--augment_clips/--train_model. Сэмплы мы генерируем сами (gen_samples.py,
Silero + edge-tts — нативно на Windows и с бОльшим разнообразием русских
голосов), поэтому piper не нужен. Если кто-то всё же позовёт --generate_clips —
упадём с понятным сообщением.
"""


def generate_samples(*args, **kwargs):
    raise RuntimeError(
        "piper недоступен на Windows. Сэмплы генерирует gen_samples.py "
        "(Silero + edge-tts) — запусти его вместо --generate_clips.")
