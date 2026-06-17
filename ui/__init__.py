"""Talker UI package.

Бывший монолитный ui.py (~4800 строк) распилен на модули:
  common    — шрифты, масштаб, темы/палитры, win32-хелперы, _PopupMenu
  flowbar   — плавающая пилюля (FlowBar) + бабл ✕/✓ (ControlBubble)
  bubbles   — LoadingWindow, PasteFallbackBubble, CancelUndoToast, ClipboardToast, OnboardingTip
  url_window— окно «Транскрибировать URL»
  history   — окно Истории
  settings  — окно Настроек + _ListEditor

Публичные имена реэкспортируются здесь — внешний код продолжает писать
`from ui import FlowBar, SettingsWindow, …` как раньше.
"""
from .common import (_UiScale, _apply_theme, _resolve_fonts, _resolve_mode,
                     _f, _s, _FONT_DIM, _FONT_LABEL)
from .flowbar import FlowBar, ControlBubble
from .bubbles import (LoadingWindow, PasteFallbackBubble, CancelUndoToast,
                      ClipboardToast, OnboardingTip)
from .url_window import UrlTranscribeWindow
from .history import HistoryWindow
from .settings import SettingsWindow
