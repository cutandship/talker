# -*- coding: utf-8 -*-
"""Voice-gated hands-free dictation (patch 2, фича 1). Чистая логика над потоком
STT-чанков continuous-режима — без аудио и tkinter.

Поток: «талкер старт» открывает сессию → текст копится в буфер → «талкер стоп»
закрывает и отдаёт накопленный текст с флагом submit. До «старт» ничего не
копится (hands-free контракт). «ввод» в любой момент сессии взводит флаг submit
(после вставки жмётся Enter).

VoiceTriggers делает детект/вырезание кодовых слов; здесь — только буферизация.
Самотест: python voice_gate.py
"""
from __future__ import annotations

from voice_triggers import VoiceTriggers


class VoiceGate:
    def __init__(self, start_phrases=None, stop_phrases=None, fuzzy_thr=0.82) -> None:
        # fuzzy=True: ASR коверкает «стоп-стоп» («стап стап», «штоп»…), поэтому
        # короткую реплику (≤3 слов) без точного совпадения сверяем нечётко —
        # иначе стоп срабатывает не с первого раза. fuzzy_thr регулируется в
        # настройках (чувствительность «стоп-стоп»).
        self._vt = VoiceTriggers(start_phrases, stop_phrases, fuzzy=True,
                                 fuzzy_thr=fuzzy_thr)
        self._buf = ""

    @property
    def active(self) -> bool:
        return self._vt.active

    def reset(self) -> None:
        self._vt.reset()
        self._buf = ""

    def begin(self) -> None:
        """Открыть сессию программно — БЕЗ произнесённого «талкер старт». Нужно,
        когда диктовку уже запустило wake-слово (Hey Jarvis): копим с первого же
        фрагмента, а завершает стоп-фраза («джарвис/талкер стоп»)."""
        self._vt.active = True
        self._vt.polish_pending = False
        self._vt.submit_pending = False
        self._buf = ""

    def arm_submit(self) -> None:
        """Взвести submit ПРОГРАММНО — аудио-модель «ввод-ввод» услышала
        команду раньше, чем текст дошёл до feed(). Ближайший flush()/стоп
        вернёт submit=True (вставить и нажать Enter)."""
        self._vt.submit_pending = True

    def flush(self):
        """Принудительно финализировать буфер — для НЕголосового стопа (Ctrl+Alt+
        Space toggle / кнопка ✓ / таймаут сессии). Возвращает тот же dict, что и
        «стоп», либо None если копить нечего. Сбрасывает состояние."""
        out = None
        if self._buf:
            out = {"text": self._buf,
                   "submit": bool(getattr(self._vt, "submit_pending", False))}
        self._buf = ""
        self._vt.reset()
        return out

    def feed(self, chunk: str):
        """Скормить один распознанный фрагмент. Возвращает dict, когда «стоп»
        завершил АКТИВНУЮ сессию (текст может быть и пустым — сессию всё равно
        надо закрыть: убрать «Слушаю», вернуть wake):
            {"text": str, "submit": bool}
        Иначе None (копим между старт/стоп, либо простой до «старт»). На None
        вызывающий НЕ вставляет ничего — момент вставки определяет гейт.

        Регрессия, которую это чинит: «Hey Jarvis» (в т.ч. ложный) → сразу
        «стоп-стоп» при пустом буфере. Раньше feed возвращал None («нечего
        вставлять»), main не закрывал сессию — пилюля висела в «Слушаю», а
        ПОВТОРНЫЙ «стоп-стоп» падал в уже неактивный гейт и вставлял мусорный
        хвост распознавания."""
        was_active = self._vt.active
        r = self._vt.feed(chunk or "")
        if r["started"]:
            self._buf = ""
        # Копим только внутри сессии (или с момента «старт» в этом же чанке).
        # Хвост вокруг «стопа» НЕактивной сессии — мусор, его не буферизуем.
        if r["text"] and (was_active or r["started"]):
            self._buf = (self._buf + " " + r["text"]).strip()
        if r["stopped"]:
            # Сессия считается «своей», если была активна ДО чанка или
            # стартовала в этом же чанке («талкер старт … стоп» одной фразой).
            if not (was_active or r["started"]):
                self._buf = ""
                return None              # бродячий «стоп» без сессии — игнор
            out = {"text": self._buf, "submit": bool(r["submit"])}
            self._buf = ""
            return out                   # text может быть "" — сессия закрыта
        return None


# ── Самотест: python voice_gate.py ────────────────────────────────────────────
if __name__ == "__main__":
    # 1) старт … стоп в разных чанках
    g = VoiceGate()
    assert g.feed("талкер старт") is None
    assert g.feed("привет мир") is None
    out = g.feed("стоп стоп")
    assert out and out["text"] == "привет мир", out

    # 2) всё в одном чанке
    g.reset()
    out = g.feed("талкер старт быстрый текст стоп стоп")
    assert out and out["text"] == "быстрый текст", out

    # 3) ввод → флаг submit
    g.reset()
    g.feed("талкер старт письмо")
    out = g.feed("стоп стоп ввод")
    assert out and out["submit"] and out["text"] == "письмо", out

    # 4) речь до «старт» отбрасывается
    g.reset()
    assert g.feed("это до старта") is None
    assert g._buf == "", g._buf

    # 5) «стоп» без «старт» → ничего
    g.reset()
    assert g.feed("стоп стоп") is None

    # 6) регрессия: стоп при ПУСТОМ буфере активной сессии всё равно закрывает
    #    её (возвращает dict с text="") — иначе пилюля висит в «Слушаю»
    g.reset()
    g.begin()
    out = g.feed("стоп стоп")
    assert out is not None and out["text"] == "", out

    # 7) мусор вокруг бродячего стопа НЕ буферизуется и не вставляется
    g.reset()
    assert g.feed("какой-то хвост стоп стоп") is None
    assert g._buf == "", g._buf

    print("voice_gate: все проверки пройдены OK")
