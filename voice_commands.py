"""Inline voice commands — phrases like «новый абзац», «удали последнее слово»,
«отправь» get recognized as *actions* instead of being inserted as text.

Disambiguation: by default a command is recognised only when prefixed with a
**marker word** ("talker" / "команда"). Optionally the user can opt into
"standalone in tail" — a command alone at the end of an utterance counts even
without a marker. Marker-only is the safer default.

See concept/17_voice_commands_inline.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VoiceCommandAction:
    """A single action to execute after the text is inserted.

    kind:
      - "insert" → injector.inject(value)
      - "key"    → keyboard.send(value)
    """
    kind: str
    value: str


@dataclass
class VoiceCommand:
    phrase: str = ""
    action: str = "insert"
    value: str = ""


# Marker words that license a command. Users can add their own via config; we
# accept both Russian and English baselines.
DEFAULT_MARKERS = ("talker", "талкер", "команда")


def default_commands() -> list[dict]:
    """Seed commands written as plain dicts so config.py doesn't import us."""
    return [
        # Insertions
        {"phrase": "новый абзац",           "action": "insert", "value": "\n\n"},
        {"phrase": "new paragraph",         "action": "insert", "value": "\n\n"},
        {"phrase": "новая строка",          "action": "insert", "value": "\n"},
        {"phrase": "new line",              "action": "insert", "value": "\n"},
        {"phrase": "точка",                 "action": "insert", "value": "."},
        {"phrase": "period",                "action": "insert", "value": "."},
        {"phrase": "запятая",               "action": "insert", "value": ","},
        {"phrase": "comma",                 "action": "insert", "value": ","},
        {"phrase": "вопросительный знак",   "action": "insert", "value": "?"},
        {"phrase": "question mark",         "action": "insert", "value": "?"},

        # Key actions
        {"phrase": "отправь",               "action": "key",    "value": "enter"},
        {"phrase": "send it",               "action": "key",    "value": "enter"},
        {"phrase": "энтер",                 "action": "key",    "value": "enter"},
        {"phrase": "удали последнее слово", "action": "key",    "value": "ctrl+backspace"},
        {"phrase": "delete last word",      "action": "key",    "value": "ctrl+backspace"},
        {"phrase": "табуляция",             "action": "key",    "value": "tab"},
        {"phrase": "tab",                   "action": "key",    "value": "tab"},
        {"phrase": "esc",                   "action": "key",    "value": "esc"},
        {"phrase": "отмена",                "action": "key",    "value": "esc"},
    ]


def extract_commands(text: str, commands: list[VoiceCommand],
                     markers: tuple[str, ...] = DEFAULT_MARKERS,
                     allow_standalone_tail: bool = False
                     ) -> tuple[str, list[VoiceCommandAction]]:
    """Strip command phrases from `text` and return (cleaned_text, actions).

    Two recognition modes:
      1. Marker-prefixed: any "<marker> <phrase>" anywhere → command.
      2. Tail standalone (opt-in): the whole text *equals* a phrase, or text
         ends with ".<phrase>" → command.
    """
    if not text or not commands:
        return text, []

    actions: list[VoiceCommandAction] = []
    norm = text.strip().rstrip(".,!?:;").strip()

    # --- 2) Tail standalone ----------------------------------------------------
    if allow_standalone_tail:
        for cmd in commands:
            ph = cmd.phrase.strip().lower()
            if not ph:
                continue
            if norm.lower() == ph:
                actions.append(VoiceCommandAction(cmd.action, cmd.value))
                return "", actions

    # --- 1) Marker-prefixed ----------------------------------------------------
    # Build one big pattern: (marker_alt)(\s+)(phrase_alt) — case-insensitive.
    marker_alt = "|".join(re.escape(m) for m in markers if m)
    if not marker_alt:
        return text, actions

    # Sort phrases longest-first so "удали последнее слово" beats "удали".
    sorted_cmds = sorted(commands, key=lambda c: -len(c.phrase))
    phrases_alt = "|".join(re.escape(c.phrase) for c in sorted_cmds if c.phrase)
    if not phrases_alt:
        return text, actions

    pattern = re.compile(
        rf"\b({marker_alt})[\s,]+({phrases_alt})\b",
        re.IGNORECASE,
    )

    def _on_match(m: re.Match) -> str:
        matched_phrase = m.group(2).lower()
        for c in sorted_cmds:
            if c.phrase.lower() == matched_phrase:
                actions.append(VoiceCommandAction(c.action, c.value))
                break
        return ""    # remove command from the text we'll insert

    new_text = pattern.sub(_on_match, text)
    # Tidy up double spaces left behind
    new_text = re.sub(r"\s{2,}", " ", new_text).strip()
    if actions:
        logger.info(f"Voice commands extracted: {[(a.kind, a.value) for a in actions]}")
    return new_text, actions


def execute_actions(actions: list[VoiceCommandAction]) -> None:
    """Run extracted actions after the cleaned text has been inserted."""
    if not actions:
        return
    import keyboard
    from injector import inject

    for a in actions:
        try:
            if a.kind == "insert":
                inject(a.value, mode="sendinput", restore_clipboard=False)
            elif a.kind == "key":
                keyboard.send(a.value)
            else:
                logger.warning(f"Unknown voice command action: {a.kind}")
        except Exception:
            logger.exception(f"Voice command failed: {a}")
