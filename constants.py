# -*- coding: utf-8 -*-
"""Shared low-level constants.

Kept dependency-free (imports nothing from the project) so any module can
import it without risking an import cycle.
"""
from __future__ import annotations

# Audio sample rate the whole pipeline runs at. The STT models (faster-whisper,
# GigaAM) and the VAD all require 16 kHz mono, so this is genuinely global —
# not a per-recorder tunable. Previously duplicated across recorder.py,
# loopback_recorder.py, transcriber.py, gigaam_engine.py and wake_word.py.
SAMPLE_RATE = 16_000

# Версия приложения (показывается в веб-окне и README)
APP_VERSION = "0.1.0"
