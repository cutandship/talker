"""Part of the Talker UI package — see ui/__init__.py for the public surface."""
from __future__ import annotations

import ctypes
import logging
import math
import random
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

import customtkinter as ctk
import pyperclip
import sounddevice as sd

from config import (
    CleanerConfig, Config, VocabularyConfig,
    load_config, save_config, update_config,
)
from history_mgr import HistoryEntry, HistoryManager
import search_index

from . import common
from .common import (
    _s, _f, _FONT_LABEL, _FONT_DIM, _UiScale, _UiScale_persist,
    _apply_theme, _resolve_mode, _resolve_fonts,
    _enable_entry_clipboard, _rounded_corners, _hide_from_taskbar,
    _get_mic_devices, _autostart_get, _autostart_set,
    _PopupMenu, _STATE_COLORS, _STATE_LABELS,
    _TRANSPARENT, _RED, _IDLE_DOT, _TEAL, _TEAL_SOFT, _ORANGE, _BLUE, _ERR,
    _CARD_BG, _HINT_FG, _SEP_FG,
)

class LoadingWindow:
    _BG     = "#1a1a1a"
    _BORDER = "#2c2c2c"
    _FG     = "#e8e8e8"
    _DIM    = "#888"
    _ACCENT = "#4a8fff"

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._label: tk.Label | None = None
        self._sub: tk.Label | None = None
        self._spin = 0
        self._tick_id: str | None = None

    def show(self, title: str = "Загрузка…", subtitle: str = "") -> None:
        if self._win is None or not self._win.winfo_exists():
            self._build()
        self._label.configure(text=title)
        self._sub.configure(text=subtitle)
        self._position()
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        if self._tick_id is None:
            self._tick()

    def hide(self) -> None:
        if self._win and self._win.winfo_exists():
            try: self._win.withdraw()
            except Exception: logger.debug("hide: suppressed", exc_info=True)
        if self._tick_id is not None:
            try: self._root.after_cancel(self._tick_id)
            except Exception: logger.debug("hide: suppressed", exc_info=True)
            self._tick_id = None

    def _build(self) -> None:
        w = tk.Toplevel(self._root)
        self._win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.95)
        w.configure(bg=self._BORDER)
        w.withdraw()

        body = tk.Frame(w, bg=self._BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        inner = tk.Frame(body, bg=self._BG)
        inner.pack(padx=_s(28), pady=_s(20))

        # Spinner
        size = _s(44)
        self._canvas = tk.Canvas(inner, width=size, height=size,
                                  bg=self._BG, highlightthickness=0)
        self._canvas.pack(pady=(0, _s(10)))

        self._label = tk.Label(inner, text="Загрузка Talker…",
                                font=_f("Segoe UI", 14, "bold"),
                                bg=self._BG, fg=self._FG)
        self._label.pack()
        self._sub = tk.Label(inner, text="",
                              font=_f("Segoe UI", 10),
                              bg=self._BG, fg=self._DIM)
        self._sub.pack(pady=(_s(4), 0))

        _rounded_corners(w)

    def _position(self) -> None:
        # Bottom-right corner so it never sits in the middle of the user's
        # workspace. Stays clear of the FlowBar pill (which is also bottom-right
        # by default) by being higher up.
        self._win.update_idletasks()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x = sw - w - _s(24)
        y = sh - h - _s(180)   # well above the taskbar and the pill
        x = max(8, x)
        y = max(8, y)
        self._win.geometry(f"{w}x{h}+{x}+{y}")

    def _tick(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            self._tick_id = None
            return
        if not self._win.winfo_viewable():
            self._tick_id = None
            return
        c = self._canvas
        c.delete("all")
        size = int(c["width"])
        cx = cy = size // 2
        self._spin = (self._spin + 12) % 360
        # 12-segment spinner with fading alpha
        n = 12
        r_in = size // 4
        r_out = size // 2 - _s(2)
        width = max(2, _s(3))
        bg_r, bg_g, bg_b = 0x1a, 0x1a, 0x1a
        ac_r, ac_g, ac_b = int(self._ACCENT[1:3], 16), int(self._ACCENT[3:5], 16), int(self._ACCENT[5:7], 16)
        import math
        for i in range(n):
            angle = math.radians(self._spin + i * (360 / n))
            alpha = (i + 1) / n
            x1 = cx + r_in * math.cos(angle)
            y1 = cy + r_in * math.sin(angle)
            x2 = cx + r_out * math.cos(angle)
            y2 = cy + r_out * math.sin(angle)
            r = int(ac_r * alpha + bg_r * (1 - alpha))
            g = int(ac_g * alpha + bg_g * (1 - alpha))
            b = int(ac_b * alpha + bg_b * (1 - alpha))
            c.create_line(x1, y1, x2, y2,
                          fill=f"#{r:02x}{g:02x}{b:02x}",
                          width=width, capstyle="round")
        self._tick_id = self._root.after(45, self._tick)


# ══════════════════════════════════════════════════════════════════════════════
# ControlBubble – separate window with ✕ Cancel / ✓ Confirm during recording.
# Lives next to the FlowBar pill so the pill itself stays a lightweight,
# always-fast animated widget.
# ══════════════════════════════════════════════════════════════════════════════



class PasteFallbackBubble:
    """
    Tiny auto-dismissing notification shown after a dictation, anchored to the
    FlowBar pill («на месте, где была диктовка»). It does NOT show the dictated
    text — just a ✓ and a one-line status, like a system toast.

    On a paste FAILURE (or a typing-skip) the text is put on the clipboard, so
    the line truthfully reads «Текст скопирован в буфер обмена». Fades in, holds
    ~3 s, fades out; click dismisses; never steals focus.

    NOTE: the old in-panel «Поправить»/«Причесать»/edit actions are gone (user
    request). on_correction is still accepted for API compatibility.
    """

    _BG       = "#1b1b1b"
    _BORDER   = "#2e2e2e"
    _TEXT     = "#f0f0f0"
    _ACCENT   = "#1fb8a6"
    _ALPHA    = 0.9          # semi-transparent — a light notification, not a wall

    # One-line message keyed by WHY the panel popped (no dictated text shown).
    _MSG = {
        "failed": "Текст скопирован в буфер обмена",
        "typing": "Текст скопирован в буфер обмена",
        "always": "Скопировано в буфер обмена",
    }

    # Font anchored to ONE knob: _FONT_PT × UI-scale ÷ _FONT_DIV (1.5× smaller
    # than the app's ramp), so the toast stays compact and predictable.
    _FONT_PT  = 12
    _FONT_DIV = 1.5

    @staticmethod
    def _font(pt: int, weight: str | None = None) -> tuple:
        px = max(7, int(round(pt * _UiScale.value / PasteFallbackBubble._FONT_DIV)))
        return (common._FONT_FAMILY, px, weight) if weight else (common._FONT_FAMILY, px)

    def __init__(self, root: tk.Tk, anchor_xy: Callable[[], tuple[int, int]],
                 anchor_size: "Callable[[], tuple[int, int]] | None" = None,
                 auto_hide_ms: int = 3_000,
                 on_correction: Callable[[str, str], None] | None = None) -> None:
        self._root = root
        self._anchor_xy = anchor_xy
        self._anchor_size = anchor_size
        self._auto_hide_ms = auto_hide_ms
        # Kept for API compatibility; this minimal toast no longer surfaces it.
        self._on_correction = on_correction

        self._win: tk.Toplevel | None = None
        self._lbl: tk.Label | None = None
        self._hide_after: str | None = None
        self._fade_after: str | None = None
        self._current_text = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self, text: str, reason: str = "failed", meta: str = "") -> None:
        """Pop the toast. `reason` ∈ {failed, typing, always} picks the line and
        whether we auto-copy. `text` is used only for the clipboard, never shown.
        Call from the Tk thread."""
        if not text:
            return
        self._current_text = text
        if self._win is None or not self._win.winfo_exists():
            self._build()

        # On a real paste failure (or typing-skip) put the text on the clipboard
        # so «Ctrl + V» just works. Don't clobber it in «always» mode.
        if reason in ("failed", "typing"):
            try:
                pyperclip.copy(text)
            except Exception:
                logger.debug("show: suppressed", exc_info=True)

        self._lbl.configure(text=meta or self._MSG.get(reason, self._MSG["failed"]))
        self._position()
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._fade_in()
        self._arm_autohide()

    def hide(self) -> None:
        if self._win and self._win.winfo_exists():
            self._cancel_timers()
            self._win.withdraw()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        w = tk.Toplevel(self._root)
        self._win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.0)
        w.configure(bg=self._BORDER)
        w.withdraw()

        outer = tk.Frame(w, bg=self._BORDER, bd=0)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        row = tk.Frame(outer, bg=self._BG)
        row.pack(fill="both", expand=True)

        pad = _s(11)
        d = _s(18)
        badge = tk.Canvas(row, width=d, height=d, bg=self._BG, highlightthickness=0)
        badge.create_oval(d * 0.07, d * 0.07, d * 0.93, d * 0.93,
                          fill=self._ACCENT, outline="")
        badge.create_line(d * 0.29, d * 0.50, d * 0.46, d * 0.68, d * 0.71,
                          d * 0.32, fill="#08312c", width=max(2, int(d * 0.11)),
                          capstyle="round", joinstyle="round")
        badge.pack(side="left", padx=(pad, _s(8)), pady=pad)

        self._lbl = tk.Label(row, text="", font=self._font(self._FONT_PT, "bold"),
                             bg=self._BG, fg=self._TEXT, anchor="w")
        self._lbl.pack(side="left", padx=(0, pad), pady=pad)

        # Click anywhere → dismiss early.
        for wdg in (w, outer, row, self._lbl):
            wdg.bind("<Button-1>", lambda e: self.hide())

        _rounded_corners(w)

    # ── Position / fade / timers ────────────────────────────────────────────────

    def _position(self) -> None:
        """Centre the toast just ABOVE the FlowBar pill (where the dictation was);
        drop it below if the pill sits too near the top edge."""
        self._win.update_idletasks()
        ww = self._win.winfo_reqwidth()
        wh = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        try:
            ax, ay = self._anchor_xy()
        except Exception:
            ax, ay = (sw - ww) // 2, sh - wh - _s(120)
        aw = ah = 0
        if self._anchor_size:
            try:
                aw, ah = self._anchor_size()
            except Exception:
                aw = ah = 0
        gap = _s(10)
        x = ax + aw // 2 - ww // 2          # centred over the pill
        y = ay - wh - gap                   # just above it
        if y < 8:                           # pill near the top → place below
            y = ay + ah + gap
        x = max(8, min(x, sw - ww - 8))
        y = max(8, min(y, sh - wh - 8))
        self._win.geometry(f"{ww}x{wh}+{x}+{y}")

    def _fade_in(self) -> None:
        try:
            self._win.attributes("-alpha", 0.0)
        except Exception:
            pass

        def step(a: float) -> None:
            if not (self._win and self._win.winfo_exists()):
                return
            try:
                self._win.attributes("-alpha", min(a, self._ALPHA))
            except Exception:
                return
            if a < self._ALPHA:
                self._win.after(12, lambda: step(a + 0.20))

        step(0.0)

    def _arm_autohide(self) -> None:
        self._cancel_timers()
        self._hide_after = self._win.after(self._auto_hide_ms, self._fade_out)

    def _fade_out(self) -> None:
        def step(a: float) -> None:
            if not (self._win and self._win.winfo_exists()):
                return
            if a <= 0.0:
                self.hide()
                return
            try:
                self._win.attributes("-alpha", a)
            except Exception:
                return
            self._fade_after = self._win.after(12, lambda: step(a - 0.12))

        step(self._ALPHA)

    def _cancel_timers(self) -> None:
        for attr in ("_hide_after", "_fade_after"):
            a = getattr(self, attr, None)
            if a:
                try:
                    self._win.after_cancel(a)
                except Exception:
                    logger.debug("_cancel_timers: suppressed", exc_info=True)
                setattr(self, attr, None)


class CancelUndoToast:
    """Small toast shown after ✕ cancel: a countdown bar (default 4 s) plus a
    «Вернуть» button. If the button is clicked before the bar empties, the
    just-cancelled recording is restored (transcribed + inserted); otherwise it
    is discarded for good. Nothing reaches history until/unless restored."""

    _BG = "#1a1a1a"
    _BORDER = "#2c2c2c"
    _TEXT = "#f0f0f0"
    _ACCENT = "#00d4aa"

    def __init__(self, root: tk.Tk, anchor_xy: Callable[[], tuple[int, int]],
                 on_undo: Callable[[], None], seconds: float = 4.0) -> None:
        self._root = root
        self._anchor_xy = anchor_xy
        self._on_undo = on_undo
        self._seconds = seconds
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._after: str | None = None
        self._t0: float = 0.0
        self._barw = 1

    def _build(self) -> None:
        w = tk.Toplevel(self._root)
        self._win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try: w.attributes("-alpha", 0.97)
        except Exception: logger.debug("_build: suppressed", exc_info=True)
        w.configure(bg=self._BORDER)
        body = tk.Frame(w, bg=self._BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        # Compact toast: bypass the type-ramp (caption=10) so the font can drop
        # below the global 7px floor — this widget is intentionally tiny.
        _tsz = max(8, int(round(8 * _UiScale.value)))
        _tf = (common._FONT_FAMILY, _tsz)
        _tfb = (common._FONT_FAMILY, _tsz, "bold")
        row = tk.Frame(body, bg=self._BG)
        row.pack(fill="x", padx=_s(4), pady=(_s(2), _s(1)))
        tk.Label(row, text="Отменено", bg=self._BG, fg=self._TEXT,
                 font=_tf).pack(side="left")
        btn = tk.Label(row, text="↶ Вернуть", bg="#10463c", fg=self._ACCENT,
                       font=_tfb, cursor="hand2")
        btn.pack(side="right", ipadx=_s(3), ipady=_s(1))
        btn.bind("<Button-1>", lambda e: self._fire())
        btn.bind("<Enter>", lambda e: btn.configure(bg="#15604f"))
        btn.bind("<Leave>", lambda e: btn.configure(bg="#10463c"))

        self._barw = _s(64)
        self._canvas = tk.Canvas(body, width=self._barw, height=_s(2),
                                 bg=self._BG, highlightthickness=0)
        self._canvas.pack(fill="x", padx=_s(4), pady=(_s(1), _s(3)))
        _rounded_corners(w)

    def prebuild(self) -> None:
        """Create the Toplevel up-front (hidden) so the FIRST real show() does
        not pay the window-creation + DWM rounded-corners cost with a visible
        stutter. Safe to call once at startup; show() reuses the window."""
        if self._win is None or not self._win.winfo_exists():
            self._build()
            try: self._win.withdraw()
            except Exception: logger.debug("prebuild: suppressed", exc_info=True)

    def show(self) -> None:
        import time
        if self._win is None or not self._win.winfo_exists():
            self._build()
        self._position()
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._t0 = time.monotonic()
        if self._after:
            try: self._win.after_cancel(self._after)
            except Exception: logger.debug("show: suppressed", exc_info=True)
        self._tick()

    def _tick(self) -> None:
        import time
        if self._win is None or not self._win.winfo_exists():
            return
        frac = 1.0 - min(1.0, (time.monotonic() - self._t0) / self._seconds)
        c = self._canvas
        c.delete("all")
        c.create_rectangle(0, 0, self._barw, _s(5), fill="#333", outline="")
        if frac > 0:
            c.create_rectangle(0, 0, int(self._barw * frac), _s(5),
                               fill=self._ACCENT, outline="")
        if frac <= 0:
            self.hide()
            return
        self._after = self._win.after(50, self._tick)

    def _fire(self) -> None:
        self.hide()
        try:
            self._on_undo()
        except Exception:
            logger.exception("CancelUndoToast: on_undo failed")

    def hide(self) -> None:
        if self._after:
            try: self._win.after_cancel(self._after)
            except Exception: logger.debug("hide: suppressed", exc_info=True)
            self._after = None
        if self._win and self._win.winfo_exists():
            self._win.withdraw()

    def _position(self) -> None:
        ax, ay = self._anchor_xy()
        self._win.update_idletasks()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x = max(8, min(ax, sw - w - 8))
        y = ay - h - _s(8)            # above the pill …
        if y < 8:
            y = ay + _s(44)           # … or below if there's no room
        y = max(8, min(y, sh - h - 8))
        self._win.geometry(f"+{x}+{y}")


class ClipboardToast:
    """Tiny self-dismissing «✓ Скопировано в буфер обмена» toast, shown after an
    auto-copy (output.copy_to_clipboard) when the dictated text reached the field
    directly — so no fallback bubble pops, but the user still gets a quick, fading
    confirmation that Ctrl+V is armed. Holds ~1.1 s, then fades out fast. Reuses
    the pill anchor so it appears just above the capsule."""

    _BG = "#1a1a1a"
    _BORDER = "#2c2c2c"
    _ACCENT = "#00d4aa"
    _ALPHA = 0.97
    _HOLD_MS = 1100          # fully visible …
    _STEP_MS = 50            # … then fade out in _STEP_MS ticks

    def __init__(self, root: tk.Tk, anchor_xy: Callable[[], tuple[int, int]],
                 text: str = "Скопировано в буфер обмена",
                 on_hidden: Callable[[], None] | None = None) -> None:
        self._root = root
        self._anchor_xy = anchor_xy
        self._text = text
        self._on_hidden = on_hidden
        self._win: tk.Toplevel | None = None
        self._after: str | None = None

    def _build(self) -> None:
        w = tk.Toplevel(self._root)
        self._win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self._BORDER)
        body = tk.Frame(w, bg=self._BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        _tsz = max(8, int(round(8 * _UiScale.value)))
        tk.Label(body, text=f"✓ {self._text}", bg=self._BG, fg=self._ACCENT,
                 font=(common._FONT_FAMILY, _tsz, "bold")).pack(
                     padx=_s(10), pady=(_s(3), _s(4)))
        _rounded_corners(w)

    def prebuild(self) -> None:
        """Create the Toplevel up-front (hidden) so the first show() doesn't pay
        the window-creation + rounded-corners cost with a visible stutter."""
        if self._win is None or not self._win.winfo_exists():
            self._build()
            try: self._win.withdraw()
            except Exception: logger.debug("prebuild: suppressed", exc_info=True)

    def show(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            self._build()
        if self._after:
            try: self._win.after_cancel(self._after)
            except Exception: logger.debug("show: suppressed", exc_info=True)
            self._after = None
        self._position()
        try: self._win.attributes("-alpha", self._ALPHA)
        except Exception: logger.debug("show alpha: suppressed", exc_info=True)
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._after = self._win.after(self._HOLD_MS, lambda: self._fade(self._ALPHA))

    def _fade(self, alpha: float) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        alpha -= 0.16                     # ~6 ticks ≈ 300 ms fade
        if alpha <= 0:
            self.hide()
            return
        try: self._win.attributes("-alpha", alpha)
        except Exception: logger.debug("_fade: suppressed", exc_info=True)
        self._after = self._win.after(self._STEP_MS, lambda: self._fade(alpha))

    def hide(self) -> None:
        if self._after:
            try: self._win.after_cancel(self._after)
            except Exception: logger.debug("hide: suppressed", exc_info=True)
            self._after = None
        if self._win and self._win.winfo_exists():
            self._win.withdraw()
        # A topmost window appearing then vanishing can shuffle the Windows
        # topmost z-order and leave the pill buried behind the active window.
        # Let the owner re-assert the pill on top once we're gone.
        if self._on_hidden:
            try: self._on_hidden()
            except Exception: logger.debug("on_hidden: suppressed", exc_info=True)

    def _position(self) -> None:
        ax, ay = self._anchor_xy()
        self._win.update_idletasks()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x = max(8, min(ax, sw - w - 8))
        y = ay - h - _s(8)            # above the pill …
        if y < 8:
            y = ay + _s(44)           # … or below if there's no room
        y = max(8, min(y, sh - h - 8))
        self._win.geometry(f"+{x}+{y}")


# ══════════════════════════════════════════════════════════════════════════════
# HistoryWindow – main panel with dictation history
# ══════════════════════════════════════════════════════════════════════════════

class OnboardingTip:
    """One-time first-run tip anchored to the pill: как начать диктовать.
    Приложение стартует молча в трей — без этой подсказки пользователь должен
    догадываться про PTT-клавишу. «Понятно» сохраняет ui.onboarding_shown
    (через update_config — merge-safe) и больше никогда не показывается."""

    def __init__(self, root: tk.Tk, anchor_xy, anchor_size,
                 hotkey_label: str = "Right Alt") -> None:
        self._anchor_xy = anchor_xy
        self._anchor_size = anchor_size
        win = ctk.CTkToplevel(root)
        self._win = win
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            logger.debug("onboarding topmost failed", exc_info=True)
        body = ctk.CTkFrame(win, fg_color=("#ffffff", "#1d1d1d"),
                            corner_radius=12, border_width=1,
                            border_color=("#c9c9c9", "#3a3a3a"))
        body.pack(fill="both", expand=True)
        ctk.CTkLabel(body, text="Это Talker 👋",
                     font=_f("Segoe UI", 13, "bold"),
                     anchor="w").pack(fill="x", padx=_s(14), pady=(_s(10), 0))
        ctk.CTkLabel(
            body,
            text=(f"Зажми {hotkey_label} и говори — отпусти, и текст появится "
                  "там, где стоит курсор.\n"
                  "Клик по капсуле — диктовка без рук, правый клик — меню."),
            font=_f("Segoe UI", 11), justify="left", anchor="w",
            wraplength=_s(330)).pack(fill="x", padx=_s(14),
                                     pady=(_s(4), _s(8)))
        ctk.CTkButton(body, text="Понятно", width=_s(110), height=_s(30),
                      command=self._dismiss).pack(anchor="e", padx=_s(12),
                                                  pady=(0, _s(10)))
        win.after(0, self._place)

    def _place(self) -> None:
        try:
            self._win.update_idletasks()
            w = self._win.winfo_reqwidth()
            h = self._win.winfo_reqheight()
            ax, ay = self._anchor_xy()
            aw, ah = self._anchor_size()
            x = max(8, min(ax + aw // 2 - w // 2,
                           self._win.winfo_screenwidth() - w - 8))
            y = ay - h - _s(14)
            if y < 8:                       # пилюля у верхнего края → под ней
                y = ay + ah + _s(14)
            self._win.geometry(f"+{x}+{y}")
            self._win.deiconify()
            self._win.lift()
        except Exception:
            logger.debug("onboarding place failed", exc_info=True)
            self._dismiss()

    def _dismiss(self) -> None:
        try:
            update_config(lambda c: setattr(c.ui, "onboarding_shown", True))
        except Exception:
            logger.exception("persist onboarding_shown failed")
        try:
            self._win.destroy()
        except Exception:
            logger.debug("onboarding destroy failed", exc_info=True)


