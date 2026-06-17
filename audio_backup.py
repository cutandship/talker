# -*- coding: utf-8 -*-
"""Аварийный бэкап аудио — ТОЛЬКО при сбое распознавания.

Идея (выбор пользователя): при успешной расшифровке ничего на диск не пишем
(приватность). Но если STT вернул пусто или упал на длинной диктовке — сохраняем
сырое аудио в папку recovery/, чтобы сказанное не потерялось безвозвратно.

Чистка — ПО ВРЕМЕНИ (возрасту), а НЕ по сессиям. Это важно: если случится system
failure и приложение перезапустится, удаление «на старте новой сессии» стёрло бы
бэкап только что упавшего прогона. Поэтому файлы живут _RETENTION_DAYS дней (и
свежие — текущей и только что крашнувшейся сессии — всегда сохраняются), и лишь
потом устаревают. Высокий потолок по числу файлов страхует от разрастания.

Формат — обычный WAV (16 кГц, моно, PCM16), его откроет любой плеер; при желании
аудио можно перераспознать через file_mode (--transcribe).
"""
from __future__ import annotations

import logging
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

RECOVERY_DIR = Path(__file__).parent / "recovery"
_RETENTION_DAYS = 14       # удалять записи старше этого срока (по возрасту)
_MAX_FILES = 100           # потолок против разрастания (сносим самые старые)


def save_failed(audio: np.ndarray, sample_rate: int, reason: str = "") -> Path:
    """Записать аудио в recovery/ и подчистить старые по возрасту. Возвращает путь."""
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = f"_{reason}" if reason else ""
    # Uniquify so two failures in the same second don't overwrite each other.
    path = RECOVERY_DIR / f"{stamp}{tag}.wav"
    k = 1
    while path.exists():
        path = RECOVERY_DIR / f"{stamp}{tag}_{k}.wav"
        k += 1

    pcm = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")        # little-endian int16
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16.tobytes())

    cleanup_old()
    logger.info(
        f"Saved failed-dictation audio: {path.name} "
        f"({len(pcm) / sample_rate:.0f}s, reason={reason or 'n/a'})")
    return path


def cleanup_old(retention_days: float = _RETENTION_DAYS,
                max_files: int = _MAX_FILES) -> int:
    """Чистка по ВОЗРАСТУ (не по сессиям). Удаляет .wav старше retention_days,
    поэтому system failure + рестарт не сотрёт бэкап только что упавшего прогона:
    свежие файлы живут весь срок и лишь потом устаревают. Дополнительно держит не
    более max_files (сносит самые старые) — страховка от разрастания. Возвращает
    число удалённых. Вызывается и после сохранения, и периодически из main."""
    if not RECOVERY_DIR.exists():
        return 0
    deleted = 0
    try:
        items: list[tuple[Path, float]] = []
        for p in RECOVERY_DIR.glob("*.wav"):
            try:
                items.append((p, p.stat().st_mtime))
            except OSError:
                pass

        # 1) Возрастная чистка: всё старше cutoff — удаляем.
        cutoff = time.time() - retention_days * 86400.0
        survivors: list[tuple[Path, float]] = []
        for p, m in items:
            if m < cutoff:
                try:
                    p.unlink()
                    deleted += 1
                except OSError:
                    survivors.append((p, m))
            else:
                survivors.append((p, m))

        # 2) Потолок по числу: если всё ещё много — сносим самые старые.
        if len(survivors) > max_files:
            survivors.sort(key=lambda x: x[1])           # старые первыми
            for p, _m in survivors[:len(survivors) - max_files]:
                try:
                    p.unlink()
                    deleted += 1
                except OSError:
                    pass
    except Exception:
        logger.debug("recovery cleanup failed", exc_info=True)

    if deleted:
        logger.info(f"Recovery cleanup: removed {deleted} old file(s)")
    return deleted
