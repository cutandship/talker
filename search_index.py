"""Forgiving "global" search for the Settings window.

Three pieces the UI wires together:

  1. normalize()  — Chrome-like query cleanup: lowercase, ё→е, strip
     punctuation, collapse spaces, and an EN⇄RU keyboard-layout fix so a query
     typed on the wrong layout ("ntvf" → "тема") still matches.

  2. SYNONYM groups — a small dictionary so a near-word the user knows ("звук",
     "хоткей", "оформление") finds the page that only contains the real term
     ("аудио", "горячая клавиша", "тема").

  3. matches()     — token search across a page's whole text blob (title +
     keywords + EVERY option label harvested from the page), with synonym
     expansion and a 1-edit typo tolerance ("микрафон" → "микрофон").

The UI builds one normalized blob per page (see SettingsWindow._ensure_search_index)
and calls matches(query, blob) for each page on every keystroke.
"""
from __future__ import annotations

import functools
import re

# ── 1. Normalization ─────────────────────────────────────────────────────────

# ЙЦУКЕН ↔ QWERTY: same physical key. Lets a query typed on the wrong layout
# still match (e.g. user means "тема" but types it on the EN layout → "ntvf").
_RU_BY_EN = {
    "q": "й", "w": "ц", "e": "у", "r": "к", "t": "е", "y": "н", "u": "г",
    "i": "ш", "o": "щ", "p": "з", "a": "ф", "s": "ы", "d": "в", "f": "а",
    "g": "п", "h": "р", "j": "о", "k": "л", "l": "д", "z": "я", "x": "ч",
    "c": "с", "v": "м", "b": "и", "n": "т", "m": "ь",
}
_EN_BY_RU = {v: k for k, v in _RU_BY_EN.items()}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, fold ё→е, drop punctuation, collapse whitespace."""
    s = (s or "").lower().replace("ё", "е")
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _layout_variants(tok: str) -> set[str]:
    """The token plus its EN→RU / RU→EN keyboard-layout twin (when applicable)."""
    out = {tok}
    if tok and all(ch in _RU_BY_EN for ch in tok):          # all-latin
        out.add("".join(_RU_BY_EN[ch] for ch in tok))
    if tok and all(ch in _EN_BY_RU for ch in tok):          # all-cyrillic
        out.add("".join(_EN_BY_RU[ch] for ch in tok))
    return out


# ── 2. Synonym dictionary ────────────────────────────────────────────────────
# Any word in a group expands to every other word in the group (bidirectional),
# so the query and the page text meet in the middle.
_SYNONYM_GROUPS = [
    {"аудио", "звук", "audio", "sound", "громкость", "динамик", "колонки"},
    {"микрофон", "mic", "микро", "вход", "input"},
    {"горячая", "клавиша", "клавиши", "hotkey", "хоткей", "шорткат", "сочетание", "кнопка"},
    {"тема", "theme", "оформление", "цвет", "цвета", "светлая", "темная", "dark", "light"},
    {"распознавание", "stt", "asr", "транскрипция", "recognition", "speech", "движок", "engine"},
    {"форматирование", "очистка", "cleanup", "llm", "ai", "ии", "нейросеть", "пунктуация"},
    {"провайдер", "openrouter", "ollama", "api", "ключ", "key", "token", "токен"},
    {"словарь", "vocabulary", "глоссарий", "термины", "слова", "лексикон"},
    {"замены", "замена", "replace", "автозамена", "правки"},
    {"числа", "itn", "цифры", "номера"},
    {"контекст", "ocr", "экран", "screen"},
    {"вывод", "вставка", "paste", "clipboard", "буфер", "sendinput", "uia"},
    {"стриминг", "streaming", "потоково"},
    {"история", "history", "журнал", "записи", "лог"},
    {"интерфейс", "ui", "виджет", "widget", "плашка", "pill", "прозрачность", "opacity", "масштаб", "шрифт", "glow", "свечение"},
    {"автозапуск", "autostart", "startup", "автостарт", "запуск"},
    {"шум", "noise", "шумоподавление", "denoise", "подавление"},
    {"приглушать", "приглушение", "ducker", "duck", "затихание"},
    {"команды", "voice", "голосовые", "command", "голос"},
    {"шаблоны", "сниппеты", "snippet", "заготовки", "фразы"},
    {"режимы", "modes", "приложения", "per-app", "slack", "code", "email", "промпт"},
    {"wake", "активация", "jarvis", "пробуждение", "hands-free"},
    {"язык", "language", "lang", "русский", "english", "английский"},
    {"модель", "model", "веса", "small", "medium", "large", "gigaam", "whisper"},
    {"устройство", "device", "cpu", "gpu", "cuda", "видеокарта", "процессор"},
    {"скорость", "качество", "пресет", "preset", "быстрее", "точнее", "баланс", "производительность"},
    {"непрерывный", "continuous", "руки", "hands-free", "диктовка"},
    {"тишина", "vad", "пауза", "silence"},
    {"шёпот", "шепот", "whisper", "тихо"},
]

_SYN: dict[str, set[str]] = {}
for _grp in _SYNONYM_GROUPS:
    for _w in _grp:
        _SYN.setdefault(_w, set()).update(_grp)


@functools.lru_cache(maxsize=512)
def _expand(tok: str) -> frozenset[str]:
    """Token → {token, layout-twins, all synonyms of any of those}.

    Memoized: the same query token recurs across every page and keystroke,
    and _SYN is built once at import (immutable), so the result is stable."""
    out = _layout_variants(tok)
    for v in list(out):
        out |= _SYN.get(v, set())
    return frozenset(out)


# ── 3. Fuzzy matching ────────────────────────────────────────────────────────

def _within_one_edit(a: str, b: str) -> bool:
    """True if Levenshtein(a, b) <= 1 (one insert/delete/substitute)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:                      # ensure la <= lb
        a, b, la, lb = b, a, lb, la
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            if la == lb:             # substitution
                i += 1
                j += 1
            else:                    # insertion in the longer string
                j += 1
    return True


def matches(query: str, blob: str) -> bool:
    """Does `query` match this page? `blob` is the page's already-normalized
    text (title + keywords + harvested option labels). Empty query → True.

    All query words must match (AND), each via: synonym/layout-expanded
    substring, or a 1-edit typo against an individual blob word.
    """
    qtokens = normalize(query).split()
    if not qtokens:
        return True
    blob_tokens = blob.split()
    for qt in qtokens:
        cands = _expand(qt)
        hit = any(len(c) >= 2 and c in blob for c in cands)
        if not hit and len(qt) >= 4:
            hit = any(len(bt) >= 4 and _within_one_edit(qt, bt)
                      for bt in blob_tokens)
        if not hit:
            return False
    return True
