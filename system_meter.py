"""Пиковый уровень СИСТЕМНОГО вывода (что сейчас играет в колонках), 0..1.

Используется медиа-гардом hands-free: если колонки громко играют (фильм,
музыка), а сигнал в микрофоне слабый — это утечка звука колонок в микрофон,
не голос пользователя, и сегмент не надо вставлять.

NB: comtypes/pycaw — COM-объекты живут на потоке создания. Зовите это ТОЛЬКО
с выделенного аудио-COM-потока (в Talker — поток дакера, см. App._duck_worker);
вызов с Tk-потока воспроизводит известный 0xc0000005 (см. crash.log).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def system_output_peak() -> "float | None":
    """Текущий пик устройства вывода по умолчанию, 0.0..1.0; None если
    замер недоступен (нет pycaw, нет устройства, эксклюзивный режим).

    ВАЖНО: COM-объекты pycaw попадают в ссылочные циклы. Если их соберёт GC на
    ДРУГОМ потоке, чем тот, что их создал, Release() — кросс-апартментный вызов →
    нативный 0xc0000005 (краш процесса). Поэтому в finally рвём ссылки и
    собираем мусор ПРЯМО ЗДЕСЬ, на потоке-владельце (ср. audio_ducker.
    _release_com_cycles). Зовите ТОЛЬКО с выделенного COM-потока."""
    dev = raw = iface = meter = None
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        dev = AudioUtilities.GetSpeakers()
        # pycaw version drift: older GetSpeakers() returns a raw IMMDevice (has
        # .Activate); newer wraps it in an AudioDevice whose raw COM pointer is
        # ._dev. Without this fallback .Activate threw on every call → peak was
        # always None → the media-guard (wake + segments) silently never fired.
        raw = dev if hasattr(dev, "Activate") else getattr(dev, "_dev", None)
        if raw is None:
            return None
        iface = raw.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        # QueryInterface, NOT cast(): cast aliases the SAME COM pointer without an
        # AddRef, so iface + meter each Release it → double-free → 0xc0000005
        # (the access-violation crash during the ducker's GC). QI returns an
        # independently ref-counted pointer, so the two Releases balance.
        meter = iface.QueryInterface(IAudioMeterInformation)
        return float(meter.GetPeakValue())
    except Exception:
        logger.debug("system_output_peak failed", exc_info=True)
        return None
    finally:
        meter = iface = raw = dev = None     # drop refs so the cycle is collectable
        try:
            import gc
            gc.collect()                     # Release on THIS thread, not later elsewhere
        except Exception:
            pass
