"""Direct text-injection cascade for Windows.

Bypasses the clipboard when possible. Tries (1) UIA → (2) SendInput Unicode →
(3) clipboard + Ctrl+V. The last resort matches the old behaviour, so this
module is strictly an upgrade.

Why bother?
- Clipboard + Ctrl+V is fragile in RDP, sandboxed apps, password fields,
  fullscreen games. SendInput Unicode works in ~95% of native and Electron
  apps without touching the user's clipboard.
- Clipboard managers (Ditto, ClipboardFusion) ingest every copy — annoying
  when Talker fires N times a day.
- Some apps (Slack/Discord) intercept Ctrl+V to upload images if the
  clipboard contained one.

See concept/04_uia_direct_injection.md.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Literal

import keyboard
import pyperclip

logger = logging.getLogger(__name__)

Method = Literal["uia", "sendinput", "clipboard", "none"]


# ── ctypes plumbing for SendInput Unicode ────────────────────────────────────

INPUT_KEYBOARD       = 1
KEYEVENTF_KEYUP      = 0x0002
KEYEVENTF_UNICODE    = 0x0004
VK_BACK              = 0x08
VK_RETURN            = 0x0D
VK_SHIFT             = 0x10


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wintypes.LONG),
        ("dy",          wintypes.LONG),
        ("mouseData",   wintypes.DWORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg",    wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype  = wintypes.UINT


def _kbd_event(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags,
                         time=0, dwExtraInfo=None)
    return inp


def _send_inputs(events: list[_INPUT]) -> int:
    n = len(events)
    if n == 0:
        return 0
    # Send in small batches with a tiny yield between, instead of one burst of
    # hundreds of synthetic events. A big burst floods the global keyboard
    # hook's ctypes callback and can crash it natively (0xc0000005 in
    # _ctypes.pyd). Batching keeps SendInput but eases that pressure.
    sent = 0
    _BATCH = 16
    for i in range(0, n, _BATCH):
        chunk = events[i:i + _BATCH]
        arr = (_INPUT * len(chunk))(*chunk)
        sent += _user32.SendInput(len(chunk), arr, ctypes.sizeof(_INPUT))
        if i + _BATCH < n:
            time.sleep(0.001)
    return sent


def send_unicode(text: str) -> bool:
    """Inject text via KEYEVENTF_UNICODE — doesn't touch clipboard, doesn't
    depend on keyboard layout. Works in 95%+ of apps including Chromium."""
    events: list[_INPUT] = []
    for ch in text:
        # Newline → Shift+Enter, NOT a Unicode U+000A. In chat apps (Slack,
        # Telegram, Discord, Teams) a bare Enter/U+000A SENDS the message;
        # Shift+Enter inserts a real line break without sending. In plain
        # editors Shift+Enter is also just a line break. So multi-line lists
        # paste safely everywhere. (\r is ignored — handled by the \n.)
        if ch == "\n":
            events.append(_kbd_event(vk=VK_SHIFT))
            events.append(_kbd_event(vk=VK_RETURN))
            events.append(_kbd_event(vk=VK_RETURN, flags=KEYEVENTF_KEYUP))
            events.append(_kbd_event(vk=VK_SHIFT, flags=KEYEVENTF_KEYUP))
            continue
        if ch == "\r":
            continue
        code = ord(ch)
        if code > 0xFFFF:
            # encode as UTF-16 surrogate pair (for emoji etc.)
            code -= 0x10000
            hi = 0xD800 + (code >> 10)
            lo = 0xDC00 + (code & 0x3FF)
            for surrog in (hi, lo):
                events.append(_kbd_event(scan=surrog, flags=KEYEVENTF_UNICODE))
                events.append(_kbd_event(scan=surrog, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        else:
            events.append(_kbd_event(scan=code, flags=KEYEVENTF_UNICODE))
            events.append(_kbd_event(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    sent = _send_inputs(events)
    return sent == len(events)


def send_backspace(n: int) -> None:
    """N backspace keystrokes — used to roll back streaming partials when LLM
    rewrites the text on commit. Uses real virtual-key BACK, not Unicode."""
    if n <= 0:
        return
    events: list[_INPUT] = []
    for _ in range(n):
        events.append(_kbd_event(vk=VK_BACK))
        events.append(_kbd_event(vk=VK_BACK, flags=KEYEVENTF_KEYUP))
    _send_inputs(events)


# ── Optional UIA backend ─────────────────────────────────────────────────────
# uiautomation (PyPI) is a lightweight pure-Python wrapper over the same DLL
# we'd talk to via comtypes. Only used if installed; ImportError silently
# disables this rung of the cascade.

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
        logger.info("UIA backend available (uiautomation lib loaded)")
    except ImportError:
        logger.info("uiautomation not installed — UIA backend disabled")
    return _uia_module


def _try_uia(text: str) -> bool:
    auto = _get_uia()
    if auto is None:
        return False
    try:
        focused = auto.GetFocusedControl()
        if focused is None:
            return False
        # Refuse password fields for safety.
        try:
            if focused.IsPassword:
                logger.warning("UIA: focused control is a password field, refusing")
                return False
        except Exception:
            pass
        if focused.IsValuePatternAvailable():
            vp = focused.GetValuePattern()
            current = vp.Value or ""
            vp.SetValue(current + text)
            return True
    except Exception as e:
        logger.debug(f"UIA injection error: {e}")
    return False


# ── Clipboard fallback ───────────────────────────────────────────────────────

class _ClipboardRestorer:
    """Single pending clipboard-restore timer. Without cancelling, two pastes
    within the delay window stack timers that race to write the clipboard —
    the older one can clobber the newer paste's content (or restore a stale
    value)."""

    DELAY = 4.0           # long enough for the user to paste into a 2nd field

    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def schedule(self, prev: str, delay: float | None = None) -> None:
        """Restore `prev` to the clipboard after `delay`s, cancelling any
        still-pending restore first so rapid successive pastes don't fight."""
        def _do_restore() -> None:
            with self._lock:
                self._timer = None
            try:
                pyperclip.copy(prev)
            except Exception:
                logger.debug("clipboard restore failed", exc_info=True)

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(delay or self.DELAY, _do_restore)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


_restorer = _ClipboardRestorer()


def _schedule_clipboard_restore(prev: str, delay: float = _ClipboardRestorer.DELAY) -> None:
    _restorer.schedule(prev, delay)


def cancel_clipboard_restore() -> None:
    """Cancel a pending clipboard restore (see _ClipboardRestorer.schedule).
    Called when injected text is deliberately LEFT on the clipboard — e.g. the
    result toast told the user «Текст скопирован в буфер обмена» and re-copied it,
    so the scheduled restore must NOT overwrite it with the old clipboard."""
    _restorer.cancel()


def _via_clipboard(text: str, restore: bool = True) -> bool:
    prev = None
    if restore:
        try:
            prev = pyperclip.paste()
        except Exception:
            pass
    try:
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        if prev is not None:
            _schedule_clipboard_restore(prev)
        return True
    except Exception as e:
        logger.warning(f"Clipboard inject failed: {e}")
        return False


# ── Public API ───────────────────────────────────────────────────────────────

# Above this length, SendInput-by-character becomes painfully slow (each
# char is two SendInput events; 2000 chars = 4000 events ≈ visibly chunky).
# For long text we go straight to clipboard+Ctrl+V which is O(1).
_LONG_TEXT_CHARS = 500


def inject(text: str, mode: str = "auto",
           restore_clipboard: bool = True) -> Method:
    """Insert `text` at the current focus.

    `mode`:
      - "auto":      uia → sendinput → clipboard (preferred default)
      - "uia":       UIA only; if unavailable, return "none"
      - "sendinput": KEYEVENTF_UNICODE only
      - "clipboard": old clipboard+Ctrl+V behaviour

    Returns the method that succeeded (or "none" on failure).
    """
    if not text:
        return "none"

    if mode == "clipboard":
        return "clipboard" if _via_clipboard(text, restore_clipboard) else "none"
    if mode == "sendinput":
        return "sendinput" if send_unicode(text) else "none"
    if mode == "uia":
        return "uia" if _try_uia(text) else "none"

    # Multi-line text MUST go through sendinput: it turns each "\n" into
    # Shift+Enter (a line break that doesn't SEND in chat apps). UIA and
    # clipboard paste a raw newline, which submits the message in Slack/Telegram/
    # etc. So for any text containing a newline, sendinput is the only safe path.
    if "\n" in text:
        if send_unicode(text):
            return "sendinput"
        # fall back to clipboard only if sendinput failed outright
        if _via_clipboard(text, restore_clipboard):
            return "clipboard"
        return "none"

    # auto cascade — long single-line text bypasses sendinput (clipboard is O(1))
    if len(text) > _LONG_TEXT_CHARS:
        logger.info(f"Long text ({len(text)} chars) — using clipboard inject")
        if _via_clipboard(text, restore_clipboard):
            return "clipboard"
        return "none"

    if _try_uia(text):
        return "uia"
    if send_unicode(text):
        return "sendinput"
    if _via_clipboard(text, restore_clipboard):
        return "clipboard"
    return "none"
