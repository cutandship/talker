# -*- coding: utf-8 -*-
"""Faithfulness guard for LLM cleanup of dictated speech.

Principle (user requirement): for dictation, FIDELITY beats prettiness.
Better to ship weak punctuation than to let the model invent, paraphrase,
reorder, or alter the user's words. This module is a deterministic checker —
it does NOT trust the model. It compares the model's output to the raw input
word-by-word and REJECTS the output if the model did anything beyond:

  • adding punctuation / capitalization
  • deleting known filler words (ну, вот, короче, типа, …)
  • writing numbers as words or digits (the two are treated as equivalent)

Any of the following makes the output UNFAITHFUL → reject → fall back to a
deterministic `safe_clean` (filler removal + capitalize + final dot, words
left exactly as dictated):

  • an inserted word that wasn't in the input  (hallucination)
  • a substituted / synonymised word           («пишет»→«сообщает»)
  • a changed word form                          («позвони»→«позвоните»)
  • reordered words                              (high-WER paraphrase)
  • a merged/garbled number                      («восемь девятьсот»→«89812»)

No network, no model — pure string logic. Safe to call on every cleanup.
"""
from __future__ import annotations

import difflib
import functools
import re

# ── filler vocabulary the model is ALLOWED to delete ──────────────────────────
# Single-token fillers. Kept conservative: only words that are parasitic in
# speech almost always. NOTE we intentionally allow deleting «вот»/«значит»
# (the prompt removes them) — losing an occasional legit one is a minor,
# non-hallucinatory cost, which the spec prefers over invention.
_FILLER_SINGLE = {
    "ну", "вот", "короче", "типа", "значит", "походу", "блин",
    "слушай", "понимаешь", "мол", "ээ", "эээ", "эм", "эмм", "мм", "ммм",
    "аа", "ааа", "эх",
}
# Multi-token filler phrases (matched as contiguous n-grams).
_FILLER_PHRASE = {
    ("как", "бы"),
    ("это", "самое"),
    ("в", "общем"),
    ("так", "сказать"),
    ("то", "есть"),
}
_PHRASE_BY_LEN = {}
for _p in _FILLER_PHRASE:
    _PHRASE_BY_LEN.setdefault(len(_p), set()).add(_p)
_MAX_PHRASE = max((len(p) for p in _FILLER_PHRASE), default=1)

_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)

# Optional digit↔word normalization so ITN (numbers as digits) and number-word
# output compare equal. Degrades gracefully if num2words is missing.
try:
    from num2words import num2words as _n2w

    @functools.lru_cache(maxsize=2048)
    def _digit_token_to_words(tok: str) -> tuple[str, ...]:
        if not tok.isdigit():
            return (tok,)
        try:
            return tuple(_WORD.findall(_n2w(int(tok), lang="ru").replace("ё", "е")))
        except Exception:
            return (tok,)
    _HAVE_N2W = True
except Exception:  # pragma: no cover
    def _digit_token_to_words(tok: str) -> tuple[str, ...]:
        return (tok,)
    _HAVE_N2W = False


def _toks(s: str) -> list[str]:
    """Normalize to a comparable word stream: lowercase, ё→е, strip punctuation,
    spell digit tokens out (so 25 == «двадцать пять»)."""
    s = (s or "").lower().replace("ё", "е")
    raw = _WORD.findall(s)
    out: list[str] = []
    for t in raw:
        if t.isdigit():
            out.extend(_digit_token_to_words(t))
        else:
            out.append(t)
    return out


def _strip_fillers(keys: list[str], surface: list[str] | None = None) -> list[str]:
    """Collapse filler singles/phrases out of a word run, returning the kept
    (non-filler) items. Matching is done on `keys`; kept items come from
    `surface` (defaults to `keys`) so a caller can match on normalized tokens
    while preserving the user's original surface forms (ё, capitalization)."""
    out = surface if surface is not None else keys
    kept: list[str] = []
    i, n = 0, len(keys)
    while i < n:
        matched = False
        for L in range(min(_MAX_PHRASE, n - i), 1, -1):
            if tuple(keys[i:i + L]) in _PHRASE_BY_LEN.get(L, ()):
                i += L
                matched = True
                break
        if matched:
            continue
        if keys[i] in _FILLER_SINGLE:
            i += 1
            continue
        kept.append(out[i])
        i += 1
    return kept


def _all_fillers(words: list[str]) -> bool:
    """True iff the whole `words` run is covered by filler singles/phrases."""
    return not _strip_fillers(words)


def verify_faithful(src: str, out: str) -> tuple[bool, list[str]]:
    """Compare cleanup output `out` against raw input `src`.

    Returns (ok, violations). ok=True means the output only added punctuation,
    capitalization, and/or removed filler words — nothing invented, changed,
    reordered, or number-garbled. `violations` lists human-readable reasons.
    """
    a = _toks(src)
    b = _toks(out)
    violations: list[str] = []

    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            chunk = a[i1:i2]
            # Collapsing an immediate stutter (the deleted run repeats the word
            # right before or after it) is NOT invention — ASR doubles words and
            # removing the dup is faithful. Allow it.
            is_stutter = (
                (i1 > 0 and all(w == a[i1 - 1] for w in chunk)) or
                (i2 < len(a) and all(w == a[i2] for w in chunk))
            )
            if is_stutter:
                continue
            if not _all_fillers(chunk):
                bad = [w for w in chunk]
                violations.append(f"removed: {' '.join(bad)}")
        elif tag == "insert":
            chunk = b[j1:j2]
            violations.append(f"added: {' '.join(chunk)}")
        elif tag == "replace":
            src_chunk = a[i1:i2]
            out_chunk = b[j1:j2]
            # Allow ONLY the case where the source side is fillers + a tail that
            # exactly equals the output side, in order. Anything else (synonym,
            # form change, reorder, number merge) is a violation.
            # Strip leading/trailing fillers from src side, then require equality.
            # Collapse filler runs anywhere in the src side, then require the
            # remainder to equal the out side exactly.
            cleaned = _strip_fillers(src_chunk)
            if cleaned != out_chunk:
                violations.append(
                    f"changed: «{' '.join(src_chunk)}» → «{' '.join(out_chunk)}»")

    return (len(violations) == 0, violations)


def safe_clean(src: str) -> str:
    """Deterministic, zero-hallucination cleanup used when the LLM output is
    rejected. Removes filler words by exact match, capitalizes the first
    letter, ensures a terminal dot. Never changes, reorders, or invents words.
    Works on the raw (lowercase, punctuation-light) ASR text."""
    s = (src or "").strip()
    if not s:
        return s
    # Tokenize preserving original surface forms (not the ё→е/digit-normalized
    # ones) so the user's words come through verbatim.
    raw = re.findall(r"[а-яёa-z0-9]+|[^\sа-яёa-z0-9]+", s, re.I)
    words = [t for t in raw if re.match(r"[а-яёa-z0-9]+", t, re.I)]
    # Drop filler singles / phrases: match on normalized keys (ё→е, lower) but
    # keep the original surface forms.
    low = [w.lower().replace("ё", "е") for w in words]
    kept = _strip_fillers(low, words)
    if not kept:
        kept = words  # never return empty — keep original if it was all fillers
    text = " ".join(kept)
    text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def guard(src: str, llm_out: str) -> tuple[str, bool, list[str]]:
    """Top-level: return (final_text, used_llm, violations).
    If llm_out is faithful → (llm_out, True, []).
    Else → (safe_clean(src), False, violations)."""
    ok, v = verify_faithful(src, llm_out)
    if ok:
        return llm_out, True, []
    return safe_clean(src), False, v
