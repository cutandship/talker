# -*- coding: utf-8 -*-
"""Hands-free гейт («Hey Jarvis … стоп-стоп»): буферизация и закрытие сессии."""
from voice_gate import VoiceGate
from voice_triggers import build_stop_phrases


def _gate():
    return VoiceGate(stop_phrases=build_stop_phrases(), fuzzy_thr=0.82)


def test_normal_flow():
    g = _gate()
    g.begin()
    assert g.feed("привет мир") is None          # копим
    out = g.feed("Стоп-стоп.")
    assert out and out["text"] == "привет мир"
    assert not g.active


def test_stop_with_empty_buffer_closes_session():
    """Регрессия: ложный wake → сразу «стоп-стоп» при пустом буфере. Сессия
    обязана закрыться (dict с text=""), а не молча зависнуть в «Слушаю»."""
    g = _gate()
    g.begin()
    out = g.feed("Стоп-стоп.")
    assert out is not None
    assert out["text"] == ""
    assert not g.active


def test_stray_stop_ignored_and_no_junk():
    """«стоп» без сессии — игнор; мусор вокруг него не буферизуется и не
    может вставиться следующим стопом."""
    g = _gate()
    assert g.feed("какой-то хвост из фильма стоп стоп") is None
    g.begin()
    assert g.feed("реальный текст") is None
    out = g.feed("стоп стоп")
    assert out and out["text"] == "реальный текст"   # без «хвоста из фильма»


def test_submit_flag():
    g = _gate()
    g.begin()
    g.feed("письмо готово")
    out = g.feed("ввод-ввод")
    assert out and out["submit"] and out["text"] == "письмо готово"


def test_start_and_stop_in_one_chunk():
    g = VoiceGate()                      # дефолтные старт-фразы («талкер старт»)
    out = g.feed("талкер старт быстрый текст стоп стоп")
    assert out and out["text"] == "быстрый текст"


def test_stop_da_text_trigger():
    """«стоп-да» текстом = стоп + submit одной фразой (основная команда)."""
    g = VoiceGate()
    g.begin()
    g.feed("созвон завтра в десять")
    out = g.feed("Стоп, да.")
    assert out and out["submit"] is True
    assert out["text"] == "созвон завтра в десять"


def test_stop_stop_does_not_submit():
    """Обычный «стоп-стоп» НЕ жмёт Enter."""
    g = VoiceGate()
    g.begin()
    g.feed("черновик письма")
    out = g.feed("стоп-стоп")
    assert out and out["submit"] is False


def test_talker_otprav_synonym():
    """«талкер отправь» — синоним отправки."""
    g = VoiceGate()
    g.begin()
    g.feed("текст")
    out = g.feed("талкер отправь")
    assert out and out["submit"] is True


def test_arm_submit_for_audio_vvod():
    """Аудио-модель «ввод-ввод» взводит submit программно; flush (нетекстовый
    стоп через _stop_continuous) возвращает submit=True."""
    g = _gate()
    g.begin()
    g.feed("отправь это сообщение")
    g.arm_submit()
    out = g.flush()
    assert out and out["submit"] is True
    assert out["text"] == "отправь это сообщение"
