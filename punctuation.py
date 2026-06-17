"""Local punctuation-and-capitalization restoration.

Used as a Cleaner-chain entry positioned *after* LLM cleaners but *before*
NoopCleaner. When all LLM cleaners fail (no internet, daily limit, model
down), this restores at least basic punctuation locally so the output is
still readable.

Backends, tried in order:
    1. deepmultilingualpunctuation (Oliver Guhr) — EN/DE/FR/IT, ~250 MB.
    2. minimal heuristic fallback (capitalize first letter, ensure final dot).

To enable the heavy backend on the user's side:
    pip install deepmultilingualpunctuation

Talker doesn't require this package. See concept/10_punctuation_fallback.md.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── Backend probing (lazy) ────────────────────────────────────────────────────

_backend = None        # one of: "deepmultilingualpunctuation", "heuristic"
_model = None          # loaded model instance (only for the deep backend)


def _load_backend() -> str:
    """Pick the best available backend; load the model. Returns backend name."""
    global _backend, _model
    if _backend is not None:
        return _backend

    try:
        from deepmultilingualpunctuation import PunctuationModel
        # No language= parameter; multilingual by default.
        _model = PunctuationModel()
        _backend = "deepmultilingualpunctuation"
        logger.info("Punctuation backend: deepmultilingualpunctuation")
        return _backend
    except Exception as e:
        logger.info(f"deepmultilingualpunctuation unavailable: {e}")

    _backend = "heuristic"
    logger.info("Punctuation backend: heuristic (capitalize + trailing dot)")
    return _backend


# ── Public API ────────────────────────────────────────────────────────────────

def restore(text: str) -> str:
    """Apply punctuation/capitalization restoration. Falls back to a cheap
    heuristic if no heavy backend is available, so it's safe to call anywhere.
    """
    text = (text or "").strip()
    if not text:
        return text

    backend = _load_backend()

    if backend == "deepmultilingualpunctuation":
        try:
            return _model.restore_punctuation(text).strip()
        except Exception:
            logger.exception("deepmultilingualpunctuation failed; using heuristic")

    return _heuristic(text)


def _heuristic(text: str) -> str:
    """Minimal restoration: capitalize sentence starts, add trailing `.`
    if the text ends without terminal punctuation. Cheap, language-agnostic,
    no models. Never makes the output worse than raw STT.
    """
    text = text.strip()
    if not text:
        return text

    # Capitalize first letter of the whole string
    text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()

    # Capitalize after .!? followed by space
    def _cap(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()
    text = re.sub(r"([.!?]\s+)([a-zа-яё])", _cap, text)

    # Ensure terminal punctuation
    if text and text[-1] not in ".!?…":
        text = text + "."

    return text


def is_heavy_backend_available() -> bool:
    """Used by UI to label whether the deep model is installed."""
    try:
        import deepmultilingualpunctuation  # noqa: F401
        return True
    except ImportError:
        return False
