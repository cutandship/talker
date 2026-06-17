"""Cursor-aware text adjustment before injection.

Reads ~50 chars before the caret via UIA (when available) and adjusts the
text we're about to insert:
  - lower-case first letter if preceded by ",;:" (continuation of sentence)
  - capitalize first letter if preceded by ".!?" or beginning of input
  - prepend a space if the previous char isn't whitespace / opener

Falls back to capitalize-first-letter when UIA can't read context.

See concept/18_cursor_aware_formatting.md.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_uia_module = None
_uia_tried = False


def _get_uia():
    global _uia_module, _uia_tried
    if _uia_tried:
        return _uia_module
    _uia_tried = True
    try:
        import uiautomation as auto
        _uia_module = auto
    except ImportError:
        pass
    return _uia_module


def read_caret_context(chars_before: int = 50) -> str | None:
    """Best-effort read of the N chars preceding the caret in the focused
    editable control. Returns None on any failure."""
    auto = _get_uia()
    if auto is None:
        return None
    try:
        focused = auto.GetFocusedControl()
        if focused is None:
            return None
        # Try TextPattern: it's the only UIA pattern that exposes the caret.
        if not focused.IsTextPatternAvailable():
            return None
        tp = focused.GetTextPattern()
        sel = tp.GetSelection()
        if not sel:
            return None
        caret_range = sel[0]
        # Move the start back by N characters; faulty controls just return
        # whatever they can.
        caret_range.MoveEndpointByUnit(
            auto.TextPatternRangeEndpoint.Start,
            auto.TextUnit.Character,
            -chars_before,
        )
        text = caret_range.GetText(chars_before)
        return text if text else None
    except Exception as e:
        logger.debug(f"UIA context read failed: {e}")
        return None


def adjust_for_context(text: str, ctx: str | None) -> str:
    """Apply capitalization / spacing tweaks against the context preceding
    the caret. Returns the adjusted text.

    If `ctx` is None (UIA unavailable) we default to:
      - capitalize first letter
      - leave spacing alone (caller picked the right text)
    """
    if not text:
        return text

    if ctx is None:
        return _capitalize_first(text)

    # Last non-whitespace char of context
    last = ""
    for ch in reversed(ctx):
        if not ch.isspace():
            last = ch
            break

    # Spacing — only add a leading space if needed
    needs_space = False
    if ctx and not ctx.endswith((" ", "\t", "\n", "(", "[", "«", "\"", "'")):
        needs_space = True

    # Capitalization decision
    if not last:
        # Empty / whitespace-only context — start of doc
        text = _capitalize_first(text)
    elif last in ".!?…":
        text = _capitalize_first(text)
    elif last in ",;:":
        text = _lowercase_first(text)
    # else: leave as-is (mid-sentence)

    if needs_space and not text.startswith((" ", "\n")):
        text = " " + text

    return text


def _capitalize_first(s: str) -> str:
    return s[0].upper() + s[1:] if len(s) > 1 else s.upper()


def _lowercase_first(s: str) -> str:
    return s[0].lower() + s[1:] if len(s) > 1 else s.lower()


# gather_context() (концепты 25/30 — контекст-прайминг через UIA/OCR) удалён
# при заморозке: фича валила COM-крашем и была отключена. read_caret_context и
# adjust_for_context выше остаются — их использует smart_format (концепт 18).
