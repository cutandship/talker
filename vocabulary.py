"""Custom-vocabulary store for biasing Whisper toward user-specific names and terms.

Whisper accepts a free-form `initial_prompt` (≤224 BPE tokens) that biases the
decoder. Wrapping a comma-separated list of words in a natural-language sentence
(e.g. "В разговоре упоминаются: X, Y, Z.") works noticeably better than raw
keyword dumps.

References:
- OpenAI cookbook: https://cookbook.openai.com/examples/whisper_prompting_guide
- Contextual biasing arxiv 2410.18363 — −40-60% WER on domain vocab without
  fine-tuning.
"""
from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Rough cap to stay under Whisper's 224-token limit. Russian words tokenize
# heavier than English (2–3 BPE per word), so cap below the token limit.
_MAX_WORDS_PROMPT = 60


def build_initial_prompt(words: list[str], language: str | None,
                         context: str = "") -> str:
    """Compose an initial_prompt string from vocabulary words and (optionally)
    on-screen context (concept 25).

    Returns an empty string when there's nothing to bias on — callers can pass
    the result to faster-whisper unconditionally.
    """
    base = _words_clause(words, language)
    ctx = (context or "").strip()
    if ctx:
        # Tail nearest the caret is the most relevant; cap to protect the
        # 224-token budget (vocabulary takes priority over context).
        ctx = ctx[-400:].strip()
        return (ctx + " " + base).strip() if base else ctx
    return base


def _words_clause(words: list[str], language: str | None) -> str:
    if not words:
        return ""

    # Newest / most-used first (caller sorts), then cap to avoid blowing the
    # 224-token prompt budget.
    clean = [w.strip() for w in words if w and w.strip()]
    if len(clean) > _MAX_WORDS_PROMPT:
        logger.info(
            f"Vocabulary: truncating from {len(clean)} to {_MAX_WORDS_PROMPT} words "
            "for prompt budget"
        )
        clean = clean[:_MAX_WORDS_PROMPT]

    sample = ", ".join(clean)
    if (language or "").lower().startswith("ru"):
        return f"В разговоре упоминаются: {sample}."
    return f"The discussion mentions: {sample}."


def normalize_words(raw: Iterable[str]) -> list[str]:
    """Dedupe (case-insensitive) and strip — used by Settings save path."""
    seen: dict[str, str] = {}
    for w in raw:
        if not w:
            continue
        w = w.strip()
        if not w:
            continue
        key = w.lower()
        if key not in seen:
            seen[key] = w
    return list(seen.values())


# ── Auto-learning from user corrections (concept 06) ──────────────────────────

# Words too short / too common to be worth learning. Russian + English baseline.
_STOPWORDS = frozenset({
    # ru
    "и", "в", "не", "на", "что", "я", "с", "он", "она", "это", "как", "по",
    "из", "за", "у", "так", "о", "но", "к", "до", "же", "то", "от", "для",
    "вот", "был", "была", "было", "быть", "есть", "его", "её", "их", "мы",
    "вы", "ты", "там", "тут", "если", "или", "ли", "бы", "уже", "ещё", "еще",
    # en
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "to",
    "of", "in", "on", "at", "by", "for", "from", "with", "as", "it", "this",
    "that", "i", "you", "he", "she", "we", "they",
})


def extract_learnable(original: str, corrected: str) -> list[str]:
    """Diff two strings word-level; return words from `corrected` that look
    like proper nouns or technical terms (capitalized, latin scripts, mixed
    case, 3+ chars, non-stopword). Used to feed the auto-learning dictionary.
    """
    from difflib import SequenceMatcher

    orig_words = original.split()
    corr_words = corrected.split()
    sm = SequenceMatcher(a=orig_words, b=corr_words)

    candidates: list[str] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            for w in corr_words[j1:j2]:
                cleaned = w.strip(" .,;:!?\"'«»()[]{}…")
                if _looks_like_name(cleaned):
                    candidates.append(cleaned)
    # Dedupe preserving order
    seen: dict[str, None] = {}
    for w in candidates:
        seen.setdefault(w.lower(), w)
    out = []
    for key, _ in seen.items():
        for c in candidates:
            if c.lower() == key:
                out.append(c)
                break
    return normalize_words(out)


def _looks_like_name(w: str) -> bool:
    if not w or len(w) < 3:
        return False
    if w.lower() in _STOPWORDS:
        return False
    if any(ch.isdigit() for ch in w):
        return False
    # Names / brands / acronyms typically have either a capital letter (any
    # position) or are camelCase / contain a dot.
    has_capital = any(c.isupper() for c in w[1:]) or w[0].isupper()
    has_dot = "." in w
    return has_capital or has_dot
