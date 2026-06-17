# -*- coding: utf-8 -*-
"""Водопровод аудио-стопа (stop_word.py): кадры копятся в 80-мс блоки,
тишина не триггерит, close() останавливает поток. Детект реальной фразы
проверяется вживую (нужен голос), здесь — только механика."""
import time

import numpy as np
import pytest

try:
    from stop_word import StopWordWatcher
    import openwakeword  # noqa: F401
    HAVE_OWW = True
except Exception:
    HAVE_OWW = False

pytestmark = pytest.mark.skipif(not HAVE_OWW, reason="openwakeword не установлен")


def test_silence_does_not_fire_and_close_stops():
    fired = []
    w = StopWordWatcher(model_paths=["hey_jarvis"], on_stop=fired.append,
                        threshold=0.5)
    # ~2 секунды тишины кусками по 256 сэмплов (как отдаёт ten-vad листенер)
    for _ in range(125):
        w.feed(np.zeros(256, dtype=np.float32))
    time.sleep(1.0)            # дать воркеру дожевать очередь
    w.close()
    assert fired == []
    w._thread.join(timeout=2.0)
    assert not w._thread.is_alive()


def test_feed_after_close_is_noop():
    w = StopWordWatcher(model_paths=["hey_jarvis"], on_stop=lambda n: None)
    w.close()
    w.feed(np.zeros(256, dtype=np.float32))   # не должно бросать
