"""Локальная нормализация чисел для русского (концепт 24, путь B).

Числительные прописью → цифры, без LLM. Применяется к финальному тексту.
Надёжно: кардиналы и проценты. Порядковые (годы) намеренно пропускаются.
"""
from __future__ import annotations
import re

_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
    "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000}
_ALL = list(_UNITS) + list(_SCALES)


def _run_to_text(tokens: list[str]) -> str:
    """Парсит run числительных в ОДНО или НЕСКОЛЬКО чисел. Подряд идущие
    одного/большего разряда — это счёт, а не сумма: «два три четыре пять» →
    «2 3 4 5»; составное «двадцать пять» → «25»; «две тысячи двадцать» → «2020»."""
    results: list[int] = []
    n = 0          # накопленная часть >= тысяч
    seg = 0        # текущий сегмент < 1000
    last = None    # последнее добавленное значение (для решения о разрыве счёта)
    started = False

    def flush() -> None:
        nonlocal n, seg, last, started
        if started:
            results.append(n + seg)
        n = 0
        seg = 0
        last = None
        started = False

    for t in tokens:
        if t in _SCALES:
            seg = (seg or 1) * _SCALES[t]
            n += seg
            seg = 0
            last = None
            started = True
            continue
        v = _UNITS[t]
        # «два три» (3>=2) или «пять двадцать» (20>=5) → не составное, новое число
        if started and last is not None and v >= last:
            flush()
        seg += v
        last = v
        started = True
    flush()
    return " ".join(str(x) for x in results)


_RUN = re.compile(
    r"\b(?:" + "|".join(sorted(_ALL, key=len, reverse=True)) +
    r")(?:\s+(?:" + "|".join(_ALL) + r"))*\b", re.IGNORECASE)
# Порядковое слово сразу после числа («двадцать шестой год») — НЕ трогаем.
_ORD_AHEAD = re.compile(r"\s+[а-яё]+(?:ый|ой|ий|ого|ом|ому|ых|ые|ая|ое)\b", re.IGNORECASE)
_PCT = re.compile(r"(\d+)\s+процент(?:ов|а)?", re.IGNORECASE)


def normalize(text: str, language: str = "ru") -> str:
    if not text:
        return text
    # "" (авто-определение языка) считаем ru-совместимым: ITN трогает только
    # русские числительные, на другом языке просто нечего менять. Явный не-ru — пропуск.
    lang = (language or "").lower()
    if lang and not lang.startswith("ru"):
        return text

    def _repl(m: "re.Match") -> str:
        if _ORD_AHEAD.match(m.string, m.end()):     # за числом порядковое → пропуск
            return m.group(0)
        return _run_to_text(m.group(0).lower().split())

    text = _RUN.sub(_repl, text)            # «двадцать пять» → «25»
    text = _PCT.sub(r"\1 %", text)          # «25 процентов» → «25 %»
    return text
