# -*- coding: utf-8 -*-
"""Deterministic Russian profanity masking for dictation output.

Goal: soften strong language without dropping it — write «х*й» instead of the
raw word, so the meaning is preserved but the text is presentable. This is a
post-processing step run at the very END of the cleanup pipeline (after the LLM
and faithguard), so it never fights the model or the guard.

Why a script and not the LLM: a small model censors inconsistently — sometimes
misses, sometimes over-masks, sometimes paraphrases. A deterministic dictionary
of mat roots (with morphology) + a whitelist of look-alikes is predictable and
testable.

Masking style (default `vowels`): replace the vowels inside the matched word
with «*», keeping consonants and word edges → «х*й», «п*зда», «еб*ть». The user's
example «х*й» is exactly this. Alternative `edges` keeps first+last only.

The hard part is avoiding false positives. We DON'T match bare substrings —
each family is a curated regex that requires mat-specific morphology, and a
whitelist short-circuits known innocent look-alikes (худой, хутор, мудрый,
требовать, …).
"""
from __future__ import annotations

import re

# ── Innocent words that share letters with mat roots — never touch these ──────
# Checked first; if the whole token is here (case-insensitive, ё→е), skip it.
_WHITELIST = {
    # ху-: not хуй
    "худой", "худо", "худышка", "худеть", "похудеть", "схуднуть", "худший",
    "худо-бедно", "хутор", "хуторской", "хунта", "хунхуз", "духи", "ухо",
    "уху", "ухом", "хухрик", "хуан", "хуана", "чихуахуа", "хула", "хулить",
    "хулиган", "хулиганить", "хулиганский", "хухоль", "хунвейбин",
    "хулить", "хулил", "хулила", "хулят", "хуливший", "охулка",
    # -еб-: not ебать
    "требовать", "требование", "требовательный", "потребовать", "употреблять",
    "употребить", "потреблять", "погреб", "погребать", "погребение", "гребень",
    "гребать", "грести", "хлеб", "хлебать", "себе", "щебень", "щебетать",
    "жеребец", "жеребёнок", "теребить", "серебро", "серебряный", "ребус",
    "ребро", "ребус", "небеса", "требуха", "зебра", "лебедь", "тебе",
    # -бляд-/бл-: not блядь
    "блюдо", "блюдце", "наблюдать", "наблюдение", "обладать", "обладание",
    "заблуждение", "заблудиться", "блуждать", "блок", "благо", "бледный",
    "близко", "блин",  # «блин» — мягкое междометие, не маскируем
    # муд-: not мудак
    "мудрый", "мудрость", "мудрец", "премудрость", "изумруд", "изумрудный",
    "верблюд", "верблюжий", "амуд",
    # пизд-: almost no innocent look-alikes, but guard a few
    "капиздан",
    # other
    "сукно", "суккуб", "суккулент", "сукра",
}

# ── Mat families: each regex matches the WHOLE inflected word (anchored at word
# boundaries). Built to require mat-specific morphology, not bare roots. ───────
# We compile with IGNORECASE; ё is normalized to е before matching for the
# pattern, but masking is applied to the original token (ё preserved).
_PATTERNS = [
    # хуй family — require ху + mat continuation, block ху+д/т/н/л(а)/то
    r"(?:на|по|до|ни|о|за|пере|про)?ху(?:й|я|е|ё|ю|и)[а-я]*",
    # хули (эвфемизм-вопрос «какого хрена») — только точные формы, чтобы не
    # задеть хулить/хула/хулиган (они в whitelist, но подстрахуемся).
    r"(?:на|по)?хули",
    # пизд family
    r"(?:с|за|на|вы|до|подъ|про|о|от|по|раз|рас|при|у|из)?пизд[а-я]*",
    r"(?:рас|раз)пизд[яеи][а-я]*",
    r"пизд[её]ж[а-я]*",
    # ебать family (verb mat). ё-forms are almost always mat.
    r"(?:на|за|вы|у|разъ|разо|оту|отъ|ото|до|про|пере|подъ|съ|въ|объ|изъ|по)?[её]б(?:а|и|у|л|ё|н|щ|ы)[а-я]*",
    r"[а-я]{0,12}о[её]б[а-я]*",       # долбоёб, мудоёб, остоебенить
    r"[а-я]{0,12}[её]б(?:ан|ну|ло|нут|ись|лан|орь|ырь)[а-я]*",
    # блядь family
    r"бля(?:д[ьи]|дск|т[ьи]|)[а-я]*",
    r"\bбля\b",
    # мудак family
    r"муд(?:ак|ач|ил|охер|оз|о)[а-я]*",
    # пидор / пидар family
    r"пид(?:о|а)р[а-я]*",
    r"пидр[а-я]*",
    # залупа
    r"(?:за)?залуп[а-я]*",
    r"залуп[а-я]*",
    # уёбок / уеб
    r"у[её]б(?:ок|ищ|ан|лан)[а-я]*",
    # манда / мандавошка
    r"манд(?:а|ы|е|ой|авошк)[а-я]*",
    # гондон
    r"гондон[а-я]*",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
_TOKEN = re.compile(r"[а-яёА-ЯЁ]+")


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е")


def _is_mat(token: str) -> bool:
    n = _norm(token)
    if n in _WHITELIST:
        return False
    for rx in _COMPILED:
        m = rx.fullmatch(n)
        if m:
            return True
    return False


def _mask_token(token: str, style: str) -> str:
    if style == "edges":
        if len(token) <= 2:
            return token[0] + "*"
        return token[0] + "*" * (len(token) - 2) + token[-1]
    # default 'vowels': replace interior vowels with «*», keep word edges as-is
    chars = list(token)
    for i in range(1, len(chars) - 1):          # keep first & last char
        if chars[i] in _VOWELS:
            chars[i] = "*"
    # if no interior vowel got masked (e.g. «бля»), mask the middle char
    if "".join(chars) == token and len(token) >= 3:
        mid = len(token) // 2
        chars[mid] = "*"
    return "".join(chars)


def mask_profanity(text: str, style: str = "vowels") -> str:
    """Return text with Russian mat words masked («хуй» → «х*й»). Non-mat words
    are untouched. `style`: 'vowels' (default) or 'edges'."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        return _mask_token(tok, style) if _is_mat(tok) else tok

    return _TOKEN.sub(repl, text)


def contains_profanity(text: str) -> bool:
    """True if any token is detected as mat."""
    return any(_is_mat(m.group(0)) for m in _TOKEN.finditer(text or ""))
