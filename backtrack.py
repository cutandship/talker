"""Course-correction heuristic: when the user says "стоп, нет, я имел в виду…"
mid-utterance, drop the retracted part and keep only the corrected version.

Runs *before* LLM cleanup as a cheap pre-pass — patterns are deliberately
narrow to minimise false positives. The LLM cleanup prompt also gets a hint
about retractions so it can catch edge cases this pass misses.

See concept/19_course_correction.md.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Russian backtrack markers. We list them in *priority order* — when the same
# sentence contains multiple, the latest one wins (we cut to the right of it).
_RU_PATTERNS = [
    r"\bстоп,?\s+нет\b",
    r"\bнет,?\s+погоди\b",
    r"\bя\s+имел[аи]?\s+в\s+виду\b",
    r"\bя\s+хотел[аи]?\s+сказать\b",
    r"\bпогоди[,\s]+я\s+имел",
    # "то есть" is also a legitimate explanatory phrase — keep it OFF by
    # default to avoid false positives. Users can add it via [[backtrack]] if
    # their speaking style matches.
]

_EN_PATTERNS = [
    r"\bscratch that\b",
    r"\bwait,?\s+no\b",
    r"\bactually,?\s+no\b",
    r"\bI mean\b",
    r"\blet me rephrase\b",
]


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def apply_backtrack(text: str, language: str | None = None,
                    extra_patterns: list[str] | None = None) -> str:
    """Return text with retracted segments removed. Idempotent."""
    if not text:
        return text
    patterns = list(_pick_patterns(language))
    if extra_patterns:
        patterns.extend(extra_patterns)
    if not patterns:
        return text

    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    sentences = _SENT_SPLIT_RE.split(text)
    out: list[str] = []
    for sent in sentences:
        # Find the *last* match anywhere in this sentence
        latest_end = -1
        for pat in compiled:
            for m in pat.finditer(sent):
                if m.end() > latest_end:
                    latest_end = m.end()
        if latest_end >= 0:
            after = sent[latest_end:].lstrip(" ,.;:—-")
            # Capitalize first letter so the surviving fragment looks right
            if after:
                after = after[0].upper() + after[1:] if len(after) > 1 else after.upper()
                out.append(after)
                logger.info(f"Backtrack: dropped {latest_end} chars before "
                            f"marker in segment of {len(sent)}")
            # else: marker was at end with nothing after — drop the whole sentence
        else:
            out.append(sent)
    return " ".join(s for s in out if s).strip()


def _pick_patterns(language: str | None):
    lang = (language or "").lower()
    if lang.startswith("ru"):
        return _RU_PATTERNS
    if lang.startswith("en"):
        return _EN_PATTERNS
    # auto / unknown — combine
    return _RU_PATTERNS + _EN_PATTERNS
