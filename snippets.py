"""Voice snippets — expand short trigger phrases into longer bodies.

After Whisper transcription, *before* LLM cleanup, raw text is matched against
snippet triggers. An `exact` match replaces the whole text and skips cleanup
(the body is already polished by the user). `prefix` matches let the user
parameterize: trigger "статус для" + dictation "статус для Ивана" → body with
`{param}` = "Ивана". `anywhere` matches do in-place substitutions inside
longer text (e.g. "мой имейл" → "user@example.com").

See concept/08_voice_snippets.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

MATCH_EXACT    = "exact"
MATCH_PREFIX   = "prefix"
MATCH_ANYWHERE = "anywhere"

# How close a dictation needs to be to a snippet trigger to count as an exact
# match — Whisper is imperfect, so we don't require character-perfect equality.
_EXACT_FUZZ = 0.88


@dataclass
class Snippet:
    trigger: str
    body: str
    match: str = MATCH_EXACT
    case_sensitive: bool = False


def apply_snippets(raw: str, snippets: list[Snippet]) -> tuple[str, bool]:
    """Returns (text, was_exact_match).

    When `was_exact_match` is True, callers should skip LLM cleanup — the body
    is user-written and shouldn't be paraphrased.
    """
    if not raw or not snippets:
        return raw, False

    norm = raw.strip()
    text_cmp = norm if any(s.case_sensitive for s in snippets) else norm.lower()

    # 1) Exact (fuzzy) — covers Whisper transcription noise
    for s in snippets:
        if s.match != MATCH_EXACT:
            continue
        trig = s.trigger if s.case_sensitive else s.trigger.lower()
        if _fuzzy_equal(text_cmp, trig):
            logger.info(f"Snippet exact match: {s.trigger!r}")
            return _interpolate(s.body, param=""), True

    # 2) Prefix — capture {param} after the trigger
    for s in snippets:
        if s.match != MATCH_PREFIX:
            continue
        trig = s.trigger if s.case_sensitive else s.trigger.lower()
        if text_cmp.startswith(trig):
            param = norm[len(s.trigger):].strip(" ,:.\t")
            logger.info(f"Snippet prefix match: {s.trigger!r}, param={param!r}")
            return _interpolate(s.body, param=param), True

    # 3) Anywhere — leave cleanup running afterwards (not "exact" match)
    text = norm
    used_anywhere = False
    for s in snippets:
        if s.match != MATCH_ANYWHERE:
            continue
        pattern = re.compile(re.escape(s.trigger),
                             0 if s.case_sensitive else re.IGNORECASE)
        new_text, n = pattern.subn(_interpolate(s.body, param=""), text)
        if n:
            text = new_text
            used_anywhere = True
            logger.info(f"Snippet anywhere match: {s.trigger!r}, {n} substitutions")
    return text, False  # anywhere doesn't skip cleanup


def _fuzzy_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= _EXACT_FUZZ


def _interpolate(body: str, *, param: str) -> str:
    """Replace {param}, {date}, {time} placeholders in snippet body."""
    now = datetime.now()
    return (body
            .replace("{param}", param)
            .replace("{date}", now.strftime("%Y-%m-%d"))
            .replace("{time}", now.strftime("%H:%M")))
