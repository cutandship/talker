from __future__ import annotations

import ctypes
import logging
import math
import random
import sys
import threading
import tkinter as tk
import winreg
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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Fonts ───────────────────────────────────────────────────────────────────
# Bundled Inter (loaded private-to-process), with graceful fallback to the
# native Win11 "Segoe UI Variable Text" and finally plain "Segoe UI". Every
# call site passes "Segoe UI"/"Consolas"; _f() remaps those to the resolved
# families, so the whole UI re-fonts from this single place.
_FONT_FAMILY = "Segoe UI"   # resolved by _resolve_fonts(root) once a root exists
_MONO_FAMILY = "Consolas"
# When frozen by PyInstaller the font is unpacked under sys._MEIPASS.
_ASSET_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_INTER_PATH = _ASSET_BASE / "assets" / "fonts" / "Inter-Variable.ttf"


def _register_bundled_fonts() -> None:
    """AddFontResourceEx the bundled Inter so GDI/Tk can use it without a
    system install. Safe to call once at import."""
    try:
        if _INTER_PATH.exists():
            FR_PRIVATE = 0x10
            n = ctypes.windll.gdi32.AddFontResourceExW(str(_INTER_PATH),
                                                       FR_PRIVATE, 0)
            logger.info("Inter font registered (%s faces)", n)
    except Exception:
        logger.debug("Inter font registration failed", exc_info=True)


def _resolve_fonts(root) -> None:
    """Pick the best available UI family now that a Tk root exists. Uses
    Font.actual() so private (FR_PRIVATE) Inter is detected even if it is not
    enumerated by families()."""
    global _FONT_FAMILY
    import tkinter.font as tkfont

    def usable(fam: str) -> bool:
        try:
            return tkfont.Font(root=root, family=fam, size=12
                               ).actual("family").lower() == fam.lower()
        except Exception:
            return False

    for cand in ("Inter", "Inter Variable", "Inter Display",
                 "Segoe UI Variable Text", "Segoe UI"):
        if usable(cand):
            _FONT_FAMILY = cand
            break
    logger.info("UI font family resolved: %s", _FONT_FAMILY)

    # Make CTk's *default* font (used by widgets created without an explicit
    # font=, e.g. plain labels and combobox text) match our family and body
    # ramp step, so everything shares one font + size. CTkFont multiplies size
    # by widget-scaling (DPI), while _f() returns a raw tuple that does not — so
    # divide it out to land at the same pixel size as _f("Segoe UI", body).
    try:
        wsc = ctk.ScalingTracker.get_widget_scaling(root)
    except Exception:
        wsc = 1.0
    try:
        th = ctk.ThemeManager.theme
        th["CTkFont"]["family"] = _FONT_FAMILY
        th["CTkFont"]["size"] = max(8, int(round(
            _TYPE_STEPS[1] * _UiScale.value / max(wsc, 0.1))))
    except Exception:
        logger.debug("default CTkFont tweak failed", exc_info=True)


def _apply_accent(light: str, dark: str, hover_light: str, hover_dark: str) -> None:
    """Recolor CTk's default widgets to a custom accent. Must run before any
    widget is created (CTk reads ThemeManager at construction time)."""
    th = ctk.ThemeManager.theme
    acc = [light, dark]
    hov = [hover_light, hover_dark]

    def _set(widget: str, **kv) -> None:
        if widget in th:
            th[widget].update(kv)

    # White text on the teal accent — the theme default (#DCE4EE light-blue) is
    # too low-contrast on this mid-teal and reads washed-out/pinkish.
    _set("CTkButton", fg_color=acc, hover_color=hov, text_color=["#ffffff", "#ffffff"])
    _set("CTkSegmentedButton", selected_color=acc, selected_hover_color=hov,
         text_color=["#ffffff", "#ffffff"])
    _set("CTkCheckBox", fg_color=acc, hover_color=hov)
    _set("CTkRadioButton", fg_color=acc, hover_color=hov)
    _set("CTkSwitch", progress_color=acc)
    # Neutral (not accent) arrow button so it blends into the field instead of
    # a bright teal block whose square corners overhang the rounded field.
    _set("CTkComboBox", button_color=("#c4c4c4", "#3a3a3a"),
         button_hover_color=("#b2b2b2", "#484848"), border_width=0)
    _set("CTkOptionMenu", fg_color=acc, button_color=hov, button_hover_color=hov,
         text_color=["#ffffff", "#ffffff"])
    _set("CTkSlider", progress_color=acc, button_color=acc, button_hover_color=hov)
    _set("CTkProgressBar", progress_color=acc)


_register_bundled_fonts()
# Accent — teal (user choice). (light, dark) pairs.
_ACCENT      = "#14b8a6"
_ACCENT_HOV  = "#0d9488"
_apply_accent("#0e9384", "#14b8a6", "#0b7c6f", "#0d9488")


# ── Global UI scale ────────────────────────────────────────────────────────────
# All fonts and many widget sizes go through `_f()` / `_s()` so the user can
# crank everything up via Settings → "Размер интерфейса" without code changes.
# Default 2.0 (matches user request to double the baseline).

class _UiScale:
    value: float = 2.0
    _listeners: list[Callable[[float], None]] = []

    @classmethod
    def set(cls, v: float) -> None:
        v = max(0.5, min(4.0, float(v)))
        if abs(v - cls.value) < 0.01:
            return
        cls.value = v
        # NOTE: we deliberately don't call ctk.set_widget_scaling — that
        # would double-scale on top of our _f()/_s() multipliers. Our own
        # functions are the single source of truth.
        for cb in list(cls._listeners):
            try: cb(v)
            except Exception: logger.exception("UI scale listener failed")

    @classmethod
    def on_change(cls, cb: Callable[[float], None]) -> None:
        cls._listeners.append(cb)


# ── Type scale ───────────────────────────────────────────────────────────────
# Design-system lesson (Refactoring UI / Material / NNGroup): use a *small,
# fixed* set of sizes on one ratio instead of many ad-hoc ones. Every call site
# passes some legacy size; _snap_size() collapses them onto a 4-step ramp
# (~1.25 ratio) so the whole UI shares exactly four text sizes → visual
# uniformity. Hierarchy then comes from weight + color, not size sprawl.
_TYPE_STEPS = (10, 12, 15, 19)   # caption · body · heading · title


def _snap_size(size: int) -> int:
    if size <= 9:
        return _TYPE_STEPS[0]    # 8,9  → caption
    if size <= 12:
        return _TYPE_STEPS[1]    # 10–12 → body
    if size <= 14:
        return _TYPE_STEPS[2]    # 13,14 → heading
    return _TYPE_STEPS[3]        # 15+  → title


def _f(family: str, size: int, weight: str | None = None) -> tuple:
    """Build a font tuple: snap to the type ramp, then scale by the UI scale.

    Sizes < ~7 px render fuzzy in tk; clamp to 7 minimum.
    """
    scaled = max(7, int(round(_snap_size(size) * _UiScale.value)))
    # Remap the two literal families used across call sites to the resolved
    # bundled/native fonts, so the whole UI re-fonts from one place.
    if family == "Segoe UI":
        family = _FONT_FAMILY
    elif family == "Consolas":
        family = _MONO_FAMILY
    if weight:
        return (family, scaled, weight)
    return (family, scaled)


def _s(px: int) -> int:
    """Scale a pixel-ish value (paddings, widget sizes) by UI scale, then snap
    to a 4 px grid (8pt-grid lesson) so spacing keeps a consistent rhythm."""
    scaled = px * _UiScale.value
    grid = int(round(scaled / 4.0)) * 4
    return max(2, grid)


def _UiScale_persist(delta: float) -> None:
    """Bump UI scale by `delta`, persist to config.toml. If the Settings window
    is open, rebuild it in-place; otherwise the new value applies next open."""
    new_scale = max(0.5, min(4.0, round(_UiScale.value + delta, 2)))
    if abs(new_scale - _UiScale.value) < 0.005:
        return
    _UiScale.set(new_scale)
    try:
        from config import load_config, save_config
        cfg = load_config()
        cfg.ui.font_scale = new_scale
        save_config(cfg)
        logger.info(f"UI scale → {new_scale}")
    except Exception:
        logger.exception("Could not persist font_scale via FlowBar menu")
    # In-place rebuild of an open Settings window. Lazy import: common is
    # the bottom of the ui-package layering, settings sits on top of it.
    from .settings import SettingsWindow
    sw = SettingsWindow._open_win
    if sw and sw.winfo_exists():
        try:
            inst = SettingsWindow._instance
            if inst:
                inst._set_font_scale(new_scale)
        except Exception:
            logger.exception("In-place rebuild from FlowBar failed")

# ── Autostart helpers ──────────────────────────────────────────────────────────

_AUTOSTART_NAME = "Talker"
_AUTOSTART_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_get() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0,
                            winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, _AUTOSTART_NAME)
            return True
    except OSError:
        return False


def _autostart_set(enable: bool) -> None:
    if getattr(sys, "frozen", False):
        exe_cmd = f'"{sys.executable}"'
    else:
        main_py = Path(__file__).parent / "main.py"
        exe_cmd = f'"{sys.executable}" "{main_py}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _AUTOSTART_NAME, 0, winreg.REG_SZ, exe_cmd)
            else:
                try:
                    winreg.DeleteValue(k, _AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


# ── Mic device helpers ─────────────────────────────────────────────────────────

def _get_mic_devices() -> list[tuple[int, str]]:
    """Returns [(index, name), …] with -1 = system default at index 0."""
    result: list[tuple[int, str]] = [(-1, "Системный по умолчанию")]
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                result.append((i, d["name"]))
    except Exception:
        logger.debug("_get_mic_devices: suppressed", exc_info=True)
    return result

# ── Palette ────────────────────────────────────────────────────────────────────
# Theme-aware: the FlowBar canvas is hand-drawn, so it can't use CTk's
# automatic light/dark colors — we keep its palette in module globals and
# swap them via `_apply_theme()`. Accent colors (red/teal/orange/blue) read
# fine on both backgrounds, so only the neutrals flip.
_TRANSPARENT = "#010101"      # exact color treated as transparent by Windows
_RED         = "#ff4040"
_IDLE_DOT    = "#9a9a9a"        # muted gray for the idle pill dot (→ red on hover)
_TEAL        = "#00d4aa"
_TEAL_SOFT   = "#2c8a78"       # muted teal for the idle mic — present, not loud
_ORANGE      = "#ff9500"
_BLUE        = "#4a8fff"
_ERR         = "#ff5500"

# Neutrals — reassigned by _apply_theme(). Defaults = dark.
_BG          = "#141414"
_PILL_BG     = "#1e1e1e"      # pill fill (slightly lighter = more glass feel)
_TEXT        = "#f0f0f0"
_DIM         = "#888888"
_GRAY        = "#555555"

_PALETTE_DARK  = dict(BG="#141414", PILL_BG="#1e1e1e", TEXT="#f0f0f0",
                      DIM="#888888", GRAY="#555555")
_PALETTE_LIGHT = dict(BG="#eaeaea", PILL_BG="#f4f4f4", TEXT="#1a1a1a",
                      DIM="#666666", GRAY="#b0b0b0")

# (light, dark) tuples for CTk widgets in Settings/History — CTk picks the
# right one for the active appearance mode automatically.
_CARD_BG  = ("#e4e4e4", "#1a1a1a")
_HINT_FG  = ("gray40", "#888888")
_SEP_FG   = ("#c8c8c8", "#444444")


def _resolve_mode(theme: str) -> str:
    """Map a stored theme to the actual light/dark the canvas should paint."""
    if theme == "system":
        try:
            import darkdetect  # optional
            return "light" if (darkdetect.theme() or "Dark").lower() == "light" else "dark"
        except Exception:
            try:
                return ctk.get_appearance_mode().lower()
            except Exception:
                return "dark"
    return theme if theme in ("light", "dark") else "dark"


def _apply_theme(theme: str) -> None:
    """Set CTk appearance mode and swap the hand-drawn FlowBar palette."""
    global _BG, _PILL_BG, _TEXT, _DIM, _GRAY
    try:
        ctk.set_appearance_mode("system" if theme == "system" else theme)
    except Exception:
        logger.exception("set_appearance_mode failed")
    pal = _PALETTE_LIGHT if _resolve_mode(theme) == "light" else _PALETTE_DARK
    _BG, _PILL_BG, _TEXT = pal["BG"], pal["PILL_BG"], pal["TEXT"]
    _DIM, _GRAY = pal["DIM"], pal["GRAY"]

def _FONT_LABEL():  return _f("Segoe UI", 11, "bold")
def _FONT_DIM():    return _f("Segoe UI", 9)


def _enable_entry_clipboard(entry) -> None:
    """Make Ctrl+C/V/X/A work in a CTkEntry on ANY keyboard layout. On the RU
    layout Ctrl+С is a Cyrillic keysym, so Tk's default Latin-keysym clipboard
    bindings never fire. Dispatch on the Windows virtual keycode (the physical
    key, layout-independent) instead, and add a real select-all for Ctrl+A
    (Tk Entry has none by default on Windows)."""
    tkent = getattr(entry, "_entry", entry)   # underlying tkinter.Entry

    def _on(e):
        if not (e.state & 0x0004):             # Control held?
            return None
        kc = e.keycode
        try:
            if kc == 65:                       # A → select all
                tkent.select_range(0, "end"); tkent.icursor("end"); return "break"
            if kc == 67:                       # C → copy
                tkent.event_generate("<<Copy>>"); return "break"
            if kc == 86:                       # V → paste
                tkent.event_generate("<<Paste>>"); return "break"
            if kc == 88:                       # X → cut
                tkent.event_generate("<<Cut>>"); return "break"
        except Exception:
            return None
        return None

    try:
        tkent.bind("<KeyPress>", _on, add="+")
    except Exception:
        pass

# Color per app state
_STATE_COLORS = {
    "loading":    _BLUE,
    "idle":       _RED,        # red mic; hover halo matches the mic colour
    "recording":  _RED,
    "processing": _ORANGE,
    "listening":  _TEAL,       # teal — как акцент интерфейса
    "error":      _ERR,
}
_STATE_LABELS = {
    "loading":    "Загрузка…",
    "idle":       "Talker",
    "recording":  "",
    "processing": "Обработка…",
    "listening":  "Слушаю",
    "error":      "Ошибка",
}


def _rounded_corners(win: tk.Toplevel, *, borderless: bool = False) -> None:
    """Apply Windows 11 native rounded corners to a frameless popup.

    borderless=True does the OPPOSITE — it skips every DWM call. Reason: opting a
    window into DWM's modern frame *at all* (even just to round corners, and even
    with border COLOR_NONE / DONOTROUND / NCRENDERING DISABLED) makes DWM paint a
    soft drop shadow around the WINDOW rect. The pill window hugs the capsule with
    only a few px of padding, so that shadow reads as a gray "обводка сзади" round
    the pill on a light desktop (measured ~21/255 dip at the edge; pixel-clean the
    moment the DWM call is gone). The pill is colorkey-transparent and paints its
    own rounded capsule on the canvas, so it needs no DWM rounding anyway — we
    leave the window untouched → no shadow, no border line, crisp transparent edge.
    """
    if borderless:
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWM_WINDOW_CORNER_PREFERENCE_ROUND = 2
        v = ctypes.c_int(DWM_WINDOW_CORNER_PREFERENCE_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(v), ctypes.sizeof(v),
        )
    except Exception:
        logger.debug("_rounded_corners: suppressed", exc_info=True)


def _set_noactivate(win: tk.Toplevel) -> None:
    """WS_EX_NOACTIVATE: клики и перетаскивание НЕ активируют это окно —
    фокус остаётся в приложении, куда пользователь диктует. Для пилюли и
    кнопок ✕/✓ это обязательное свойство: клик по ним не должен переключать
    активное окно (Tk-события мыши приходят и без активации)."""
    try:
        win.update_idletasks()
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
    except Exception:
        logger.debug("_set_noactivate failed", exc_info=True)


def _hide_from_taskbar(win: tk.Toplevel) -> None:
    """Keep a Toplevel out of the taskbar (and Alt+Tab) by making it a Win32
    tool window: set WS_EX_TOOLWINDOW, clear WS_EX_APPWINDOW on the real
    top-level HWND.

    Why not transient()/owner: that alone does NOT reliably suppress the taskbar
    button across CTk's title-bar dance + the off-screen prebuild deiconify, so
    the «Talker — История» button leaked onto the taskbar at startup even though
    the user never opened History. Call this while the window is still withdrawn
    so the button never appears in the first place (Windows decides taskbar
    membership at show time from the current ex-style).
    """
    try:
        win.update_idletasks()
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                         (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    except Exception:
        logger.debug("_hide_from_taskbar: suppressed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# _PopupMenu – modern dark floating context menu
# ══════════════════════════════════════════════════════════════════════════════

class _PopupMenu:
    _MENU_BG  = "#252525"
    _MENU_HOV = "#3a3a3a"
    _MENU_FG  = "#ffffff"
    _MENU_SEP = "#444444"
    @staticmethod
    def _FONT():
        # ~1/3 of the old size. The menu used _f("Segoe UI", 14) = snapped 15 ×
        # UI-scale = 30 px at the user's ×2 interface scale — too big for a
        # context menu. Scale a 5 px base directly (the type-ramp's 10 px floor ×
        # scale can't go small enough) and clamp to a readable minimum.
        size = max(9, int(round(5 * _UiScale.value)))
        return (_FONT_FAMILY, size, "bold")

    def __init__(self, parent: tk.Misc, items: list, x: int, y: int) -> None:
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=self._MENU_BG)

        for item in items:
            if item is None:
                tk.Frame(self._win, bg=self._MENU_SEP, height=_s(2)).pack(
                    fill="x", padx=_s(10), pady=_s(3))
            else:
                label, cmd = item
                row = tk.Label(
                    self._win, text=label, font=self._FONT(),
                    bg=self._MENU_BG, fg=self._MENU_FG,
                    anchor="w", padx=_s(10), pady=_s(5), cursor="hand2",
                )
                row.pack(fill="x")
                row.bind("<Enter>", lambda e, w=row: w.configure(bg=self._MENU_HOV))
                row.bind("<Leave>", lambda e, w=row: w.configure(bg=self._MENU_BG))
                row.bind("<Button-1>", lambda e, c=cmd: self._invoke(c))

        self._win.update_idletasks()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        self._win.geometry(f"{w}x{h}+{min(x, sw-w-8)}+{min(y, sh-h-8)}")
        _rounded_corners(self._win)
        self._win.bind("<FocusOut>", lambda e: self._dismiss())
        self._win.bind("<Escape>", lambda e: self._dismiss())
        self._win.focus_set()

    def _invoke(self, cmd) -> None:
        self._dismiss()
        self._win.after(10, cmd)

    def _dismiss(self) -> None:
        try:
            self._win.destroy()
        except Exception:
            logger.debug("_dismiss: suppressed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# FlowBar – floating animated pill
# ══════════════════════════════════════════════════════════════════════════════

