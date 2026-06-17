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

class _ListEditor(ctk.CTkFrame):
    """Editable list of records rendered as cards (one row each).

    columns: list of dicts, each:
        {"key": str,
         "kind": "entry" | "combo" | "text",
         "width"?: int, "values"?: list[str], "placeholder"?: str}
    A "text" column renders as a full-width multi-line box beneath the inline
    fields (used for snippet bodies / mode prompts).
    `get_rows()` returns a list of {key: value} dicts (entries/combos stripped).
    """

    def __init__(self, parent, columns: list[dict], rows: list[dict],
                 add_label: str = "+ Добавить", on_change=None) -> None:
        super().__init__(parent, fg_color="transparent")
        self._columns = columns
        self._cards: list[dict] = []
        # on_change подключается ПОСЛЕ начального наполнения, чтобы постройка
        # формы не считалась правкой (dirty-индикатор «Сохранить ●»).
        self._on_change = None
        self._host = ctk.CTkFrame(self, fg_color="transparent")
        self._host.pack(fill="x")
        self._placeholder = ctk.CTkLabel(
            self._host, text="Пока пусто — нажми кнопку ниже, чтобы добавить.",
            text_color=_HINT_FG, font=_f("Segoe UI", 10), anchor="w")
        for r in rows:
            self._add_card(r)
        self._sync_placeholder()
        self._on_change = on_change
        ctk.CTkButton(self, text=add_label, height=_s(30),
                      fg_color="gray30", hover_color="gray25",
                      command=lambda: self._add_card({})).pack(anchor="w",
                                                               pady=(_s(6), 0))

    def _notify_change(self, *_a) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                logger.debug("list editor on_change failed", exc_info=True)

    def set_rows(self, rows: list[dict]) -> None:
        """Replace all rows (без срабатывания on_change — это программная
        синхронизация, не правка пользователя)."""
        cb, self._on_change = self._on_change, None
        try:
            for c in list(self._cards):
                self._del_card(c["frame"])
            for r in rows:
                self._add_card(r)
        finally:
            self._on_change = cb

    def _sync_placeholder(self) -> None:
        if self._cards:
            self._placeholder.pack_forget()
        else:
            self._placeholder.pack(anchor="w", pady=_s(6))

    def _add_card(self, values: dict) -> None:
        card = ctk.CTkFrame(self._host, fg_color=_CARD_BG, corner_radius=6)
        card.pack(fill="x", pady=_s(3))
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=_s(6), pady=_s(4))
        refs: dict = {}
        text_cols: list[dict] = []
        for col in self._columns:
            kind = col.get("kind", "entry")
            key = col["key"]
            if kind == "text":
                text_cols.append(col)
                continue
            w = _s(col.get("width", 160))
            if kind == "combo":
                vals = col.get("values", [""])
                var = tk.StringVar(value=str(values.get(key, vals[0])))
                # Combos are short, fixed-choice → keep a fixed width.
                ctk.CTkOptionMenu(top, variable=var, values=vals,
                                  width=w).pack(side="left", padx=(0, _s(6)))
            else:
                var = tk.StringVar(value=str(values.get(key, "")))
                # Entries flex to share the row width, so a card never grows
                # wider than the viewport (which at high UI scale would inflate
                # the whole window). `width` acts as a minimum.
                ctk.CTkEntry(top, textvariable=var, width=w,
                             placeholder_text=col.get("placeholder", "")
                             ).pack(side="left", fill="x", expand=True,
                                    padx=(0, _s(6)))
            try:
                var.trace_add("write", self._notify_change)
            except Exception:
                logger.debug("list editor trace failed", exc_info=True)
            refs[key] = ("var", var)
        ctk.CTkButton(top, text="✕", width=_s(32), height=_s(28),
                      fg_color="#7a2a2a", hover_color="#9a3030",
                      command=lambda c=card: self._del_card(c)).pack(side="right")
        for col in text_cols:
            key = col["key"]
            # Height is per-column (long prompts need more room than short
            # snippet bodies); CTkTextbox word-wraps and scrolls past that.
            box = ctk.CTkTextbox(card, height=_s(col.get("h", 90)),
                                 font=_f("Consolas", 10), wrap="word")
            box.pack(fill="x", padx=_s(6), pady=(0, _s(6)))
            box.insert("1.0", str(values.get(key, "")))
            refs[key] = ("text", box)
        self._cards.append({"frame": card, "refs": refs})
        self._sync_placeholder()
        self._notify_change()

    def _del_card(self, card) -> None:
        self._cards = [c for c in self._cards if c["frame"] is not card]
        try: card.destroy()
        except Exception: logger.debug("card destroy failed", exc_info=True)
        self._sync_placeholder()
        self._notify_change()

    def get_rows(self) -> list[dict]:
        out: list[dict] = []
        for c in self._cards:
            row: dict = {}
            for key, (kind, widget) in c["refs"].items():
                if kind == "text":
                    row[key] = widget.get("1.0", "end").rstrip("\n")
                else:
                    row[key] = widget.get().strip()
            out.append(row)
        return out


# ══════════════════════════════════════════════════════════════════════════════
# SettingsWindow
# ══════════════════════════════════════════════════════════════════════════════

class SettingsWindow:
    _open_win: ctk.CTkToplevel | None = None
    _last_page: str | None = None       # remember tab across font-scale reopen
    _instance: "SettingsWindow | None" = None

    @classmethod
    def open(cls, root: tk.Tk, on_save: Callable[[Config], None],
             on_widget_preview: "Callable[[Config], None] | None" = None,
             player=None, mic_monitor=None,
             hook_pause=None, hook_resume=None) -> None:
        inst = cls._instance
        if inst is not None and inst._win is not None and inst._win.winfo_exists():
            # Reuse the already-built window — instant re-show, no 3 s rebuild
            # and no flicker (like Explorer). The form keeps its last state; for
            # the normal open→edit→Save→close flow that already matches config.
            if mic_monitor is not None:
                inst._mic_monitor = mic_monitor
            if hook_pause is not None:
                inst._hook_pause = hook_pause
            if hook_resume is not None:
                inst._hook_resume = hook_resume
            inst._prebuilding = False
            inst._reveal()
            try: inst._win.focus_force()
            except Exception: logger.debug("open: suppressed", exc_info=True)
            return
        inst = cls(root, on_save, on_widget_preview, player, mic_monitor,
                   hook_pause, hook_resume)
        cls._open_win = inst._win
        cls._instance = inst

    @classmethod
    def prebuild(cls, root: tk.Tk, on_save: Callable[[Config], None],
                 on_widget_preview: "Callable[[Config], None] | None" = None,
             player=None, mic_monitor=None,
             hook_pause=None, hook_resume=None) -> None:
        """Build the (heavy ~3 s) window now but keep it HIDDEN, so the first
        real open() is instant. Called shortly after launch while idle."""
        inst = cls._instance
        if inst is not None and inst._win is not None and inst._win.winfo_exists():
            return
        inst = cls(root, on_save, on_widget_preview, player, mic_monitor,
                   hook_pause, hook_resume)
        inst._prebuilding = True   # so the queued after(45,_reveal) no-ops (stays hidden)
        cls._open_win = inst._win
        cls._instance = inst

    # provider name → {url, models}
    def __init__(self, root: tk.Tk, on_save: Callable[[Config], None],
                 on_widget_preview: "Callable[[Config], None] | None" = None,
             player=None, mic_monitor=None,
             hook_pause=None, hook_resume=None) -> None:
        self._on_save = on_save
        self._on_widget_preview = on_widget_preview
        self._player = player
        self._mic_monitor = mic_monitor   # live mic-level probe for «test mic» meter
        # Глобальные клавиатурные хуки на паузу/обратно — для захвата новой
        # PTT-клавиши (иначе нажатие будущего хоткея запустит запись).
        self._hook_pause = hook_pause
        self._hook_resume = hook_resume
        self._hotkey_capturing = False
        self._mic_meter_on = False
        self._revealed = False            # True only while shown on-screen
        self._cfg = load_config()

        win = ctk.CTkToplevel(root)
        self._win = win
        win.withdraw()                        # hide during CTk's titlebar dance
        win.transient(root)                   # owned by root → no taskbar button
        win.title("Talker — Настройки")
        win.resizable(True, True)             # user can resize freely
        # X button hides (not destroys) so the next open is instant — see open().
        win.protocol("WM_DELETE_WINDOW", self._safe_destroy)
        # Keep the window at its geometry instead of ballooning to fit all
        # content (pages are scrollable; CTkToplevel otherwise auto-grows).
        win.pack_propagate(False)
        self._size_window(win)                # centered on-screen geometry
        self._build(win)
        self._install_dirty_traces()
        win.update_idletasks()
        # Restore the tab the user was last on.
        if SettingsWindow._last_page in self._pages:
            self._show_page(SettingsWindow._last_page)
        win.update_idletasks()
        self._onscreen_geom = win.geometry()  # remember where it should appear
        # Park VISIBLE but OFF-SCREEN: a CTkScrollableFrame built while the window
        # is withdrawn renders with an unsized canvas and SMEARS/ghosts on scroll.
        # Mapping it off-screen gives the canvases real dimensions (clean scroll),
        # with no -alpha (no layering) and pre-rendered content (no white flash on
        # reveal — reveal is just an on-screen move).
        self._park_offscreen()
        win.after(45, self._reveal)

    def _park_offscreen(self) -> None:
        """Map the (already-built) window below the screen — rendered but unseen."""
        try:
            w = self._win
            w.geometry(f"+{w.winfo_x()}+{w.winfo_screenheight() + 400}")
            w.deiconify()
            w.update_idletasks()
        except Exception:
            logger.debug("settings park offscreen failed", exc_info=True)

    def _refresh_external_fields(self) -> None:
        """Re-sync form fields that have writers OUTSIDE this window — окно
        строится при prebuild и живёт долго, а «показывать окошко» тогглится
        с пилюли, словарь пополняет автообучение. Без пересинхронизации
        «Сохранить» записал бы их устаревшими. Не помечает форму dirty."""
        try:
            fresh = load_config()
        except Exception:
            logger.debug("settings refresh: config load failed", exc_info=True)
            return
        if getattr(self, "_dirty", False):
            return        # не затирать несохранённые правки пользователя
        self._loading_form = True
        try:
            var = getattr(self, "_bubble_var", None)
            if var is not None and bool(var.get()) != fresh.output.show_bubble:
                var.set(fresh.output.show_bubble)
            ed = getattr(self, "_vocab_editor", None)
            if ed is not None:
                disk = [w for w in fresh.vocabulary.words]
                shown = [r.get("word", "").strip()
                         for r in ed.get_rows() if r.get("word", "").strip()]
                if disk != shown:
                    ed.set_rows([{"word": w} for w in disk])
        except Exception:
            logger.debug("settings refresh failed", exc_info=True)
        finally:
            self._loading_form = False

    def _reveal(self) -> None:
        """Slide the (pre-rendered, off-screen) window on-screen — instant, no
        flash. No-op while prebuilding (stays parked off-screen until opened)."""
        if getattr(self, "_prebuilding", False):
            return
        if not (self._win and self._win.winfo_exists()):
            return
        self._refresh_external_fields()
        try:
            self._win.deiconify()                      # ensure mapped
            self._win.geometry(self._onscreen_geom)    # move on-screen (rendered)
            self._win.lift()
            # NO modal grab(): a grab steals ALL app events, so the record pill
            # (and the History window) stop receiving hover/clicks while Settings
            # is open («на кнопку записи навожу — не работает»). Just take focus so
            # typing lands in the form; the pill stays live.
            self._win.focus_force()
        except Exception:
            logger.debug("_reveal: suppressed", exc_info=True)
        self._revealed = True
        self._update_mic_meter()    # start the live meter if we land on «Аудио»

    def _size_window(self, win, recenter: bool = True) -> None:
        """Size the window for the current UI scale. Size grows *gently* with the
        font (≈half-rate) and is capped to the screen; the user can still drag it
        larger (maxsize = the monitor)."""
        scr_w = win.winfo_screenwidth()
        scr_h = win.winfo_screenheight()
        try: wsc = ctk.ScalingTracker.get_window_scaling(win)
        except Exception: wsc = 1.0
        log_w, log_h = int(scr_w / wsc), int(scr_h / wsc)
        gentle = 1.0 + (_UiScale.value - 1.0) * 0.5
        tw = min(int(log_w * 0.97), int(1663 * gentle))    # wide default (was 792 → 1109 → 1663); cap near full screen
        th = min(int(log_h * 0.88), int(624 * gentle))     # +20% vs the old 520
        win.maxsize(log_w, log_h)             # resizable up to the screen edge
        # Floor wide enough that the content pane (window − nav − scrollbar −
        # padding) still fits the widest fixed control row, so labels/fields never
        # clip when the user drags the window narrow. Capped to tw on tiny screens.
        win.minsize(min(_s(640), tw), min(_s(380), th))
        if recenter:
            x = max(8, (log_w - tw) // 2)
            y = max(8, (log_h - th) // 2)
            win.geometry(f"{tw}x{th}+{x}+{y}")
        else:
            win.geometry(f"{tw}x{th}")        # keep current position

    # Plain-language labels for the device combo (value ↔ what the user reads).
    _DEVICE_LABELS = {
        "cpu":  "Процессор",
        "cuda": "Видеокарта NVIDIA",
        "auto": "Авто (само)",
    }

    def _device_value(self) -> str:
        """Map the friendly device label back to the config value."""
        rev = {v: k for k, v in self._DEVICE_LABELS.items()}
        return rev.get(self._device_var.get(), "cpu")

    # Human labels for the remaining dropdowns — same pattern as _DEVICE_LABELS:
    # the combo shows the label, config stores the code. Reverse via _rev_label.
    _ENGINE_LABELS = {
        "gigaam":  "Русский — быстрый",
        "whisper": "Любой язык",
    }
    _LANG_LABELS = {
        "":   "Авто",
        "ru": "Русский",
        "en": "English",
        "de": "Deutsch",
        "fr": "Français",
        "es": "Español",
        "it": "Italiano",
        "zh": "中文",
        "ja": "日本語",
    }
    _INJ_LABELS = {
        "auto":      "Автоматически",
        "uia":       "Через поле ввода",
        "sendinput": "Печатать по буквам",
        "clipboard": "Через буфер обмена",
    }
    _SOURCE_LABELS = {
        "mic":    "Микрофон",
        "system": "Звук с компьютера",
    }
    _THEME_LABELS = {
        "dark":   "Тёмная",
        "light":  "Светлая",
        "system": "Как в Windows",
    }
    _ACTION_LABELS = {
        "insert": "Вставить текст",
        "key":    "Нажать клавишу",
    }

    @staticmethod
    def _rev_label(labels: dict, shown: str, default: str) -> str:
        """Map a shown human label back to its config code."""
        return {v: k for k, v in labels.items()}.get(shown, default)

    def _engine_value(self) -> str:
        return self._rev_label(self._ENGINE_LABELS, self._engine_var.get(), "gigaam")

    def _lang_value(self) -> str:
        return self._rev_label(self._LANG_LABELS, self._lang_var.get(), "")

    # Sensitivity dials (1=строже … 5=ловит легче) → stored threshold value.
    # «Hey Jarvis» = wake.threshold (выше = строже). «стоп-стоп» = wake.stop_fuzzy
    # (выше = строже совпадение). Level 1 строже → большее значение.
    _WAKE_LEVELS = {1: 0.85, 2: 0.75, 3: 0.65, 4: 0.55, 5: 0.45}
    _STOP_LEVELS = {1: 0.92, 2: 0.87, 3: 0.82, 4: 0.77, 5: 0.72}

    @staticmethod
    def _nearest_level(value, table: dict) -> int:
        """Config value → nearest 1–5 dial level."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 3
        return min(table, key=lambda k: abs(table[k] - v))

    @staticmethod
    def _level_value(shown: str, table: dict, default: float) -> float:
        """Dial level (1–5) → config threshold value."""
        try:
            return table.get(int(round(float(shown))), default)
        except (TypeError, ValueError):
            return default

    # ── STT engine → model list ──────────────────────────────────────────────
    # One fixed best model per engine — no model picker in the UI (the engine
    # dropdown is the only choice). Whisper → large-v3-turbo (точный, многояз.,
    # потокобезопасный). GigaAM → v3-e2e-rnnt (русский, со своей пунктуацией).
    _FIXED_MODEL = {
        "whisper": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "gigaam":  "gigaam-v3-e2e-rnnt",
    }

    # Цвет активной вкладки в боковой навигации (бирюзовый акцент).
    _NAV_SEL = "#0d9488"

    def _current_engine_model(self) -> str:
        """Model saved for the currently-selected engine (each engine keeps its
        own model in config)."""
        e = (self._engine_value() if hasattr(self, "_engine_var")
             else self._cfg.stt.engine)
        return self._FIXED_MODEL.get(e, self._cfg.stt.model)

    # Pretty display names for the hotkey tokens (keyboard-lib name → label).
    _KEYCAP_NAMES = {
        "ctrl": "Ctrl", "control": "Ctrl", "left ctrl": "Ctrl",
        "right ctrl": "Right Ctrl", "alt": "Alt", "left alt": "Alt",
        "right alt": "Right Alt", "alt gr": "AltGr", "shift": "Shift",
        "left shift": "Shift", "right shift": "Right Shift",
        "windows": "Win", "win": "Win", "left windows": "Win",
        "right windows": "Win", "cmd": "Cmd", "space": "Space",
        "spacebar": "Space", "enter": "Enter", "return": "Enter",
        "tab": "Tab", "esc": "Esc", "escape": "Esc", "backspace": "⌫",
        "caps lock": "Caps", "capslock": "Caps", "delete": "Del",
        "page up": "PgUp", "page down": "PgDn", "insert": "Ins",
        "up": "↑", "down": "↓", "left": "←", "right": "→", "menu": "Menu",
    }

    def _keycap_pretty(self, token: str) -> str:
        t = token.strip().lower()
        if t in self._KEYCAP_NAMES:
            return self._KEYCAP_NAMES[t]
        if len(t) <= 2:           # single letters, digits, f1… → upper
            return t.upper()
        return t.title()

    def _hotkey_pretty(self, raw: str) -> str:
        """Format a hotkey string ('right alt', 'ctrl+alt+space') as readable
        text ('Right Alt', 'Ctrl + Alt + Space') for the informational display."""
        toks = [p.strip() for p in (raw or "").split("+") if p.strip()]
        return " + ".join(self._keycap_pretty(t) for t in toks) or "не задано"

    # ── Hotkey capture («Изменить» → нажми клавишу) ──────────────────────────
    # Tk keysym → имя клавиши в библиотеке keyboard (PTT — одиночная клавиша;
    # модификаторы сами по себе — легитимные PTT, как дефолтный Right Alt).
    _TK2KB = {
        "alt_r": "right alt", "alt_l": "left alt",
        "control_r": "right ctrl", "control_l": "left ctrl",
        "shift_r": "right shift", "shift_l": "left shift",
        "win_r": "right windows", "win_l": "left windows",
        "super_r": "right windows", "super_l": "left windows",
        "iso_level3_shift": "alt gr",
        "return": "enter", "prior": "page up", "next": "page down",
        "caps_lock": "caps lock", "num_lock": "num lock",
        "scroll_lock": "scroll lock", "app": "menu",
    }

    def _begin_hotkey_capture(self) -> None:
        if self._hotkey_capturing:
            return
        self._hotkey_capturing = True
        self._pending_capture = None
        # Глобальные хуки на паузу: иначе нажатие будущей PTT-клавиши прямо
        # сейчас запустит запись.
        if self._hook_pause is not None:
            try:
                self._hook_pause()
            except Exception:
                logger.exception("hook pause failed")
        try:
            self._hotkey_btn_fg = self._hotkey_btn.cget("fg_color")
            self._hotkey_btn.configure(text="Нажми клавишу…",
                                       fg_color="#a8702a")
        except Exception:
            logger.debug("capture button style failed", exc_info=True)
        try:
            self._hotkey_lbl.configure(text="…  (Esc — отмена)")
        except Exception:
            logger.debug("capture label failed", exc_info=True)
        self._win.bind("<KeyPress>", self._on_capture_key)
        try:
            self._win.focus_force()
        except Exception:
            logger.debug("capture focus failed", exc_info=True)

    def _on_capture_key(self, e) -> str:
        ks = (e.keysym or "").lower()
        logger.info(f"hotkey capture: keysym={ks!r}")
        if ks == "escape":
            self._end_hotkey_capture(cancelled=True)
            return "break"
        # AltGr (= Right Alt on many RU/EU layouts) emits a PHANTOM Left Ctrl an
        # instant BEFORE the real key. Grabbing it would bind «left ctrl» and Right
        # Alt would silently stop working. So defer a lone Left Ctrl: if Right Alt /
        # level3-shift follows within a tick it wins; a genuine Left Ctrl finalises
        # after the short delay.
        if ks == "control_l":
            self._pending_capture = self._win.after(
                220, lambda: self._finalize_capture("left ctrl"))
            return "break"
        name = self._TK2KB.get(ks)
        if name is None:
            if len(ks) == 1 or (ks.startswith("f") and ks[1:].isdigit()):
                name = ks
            else:
                name = ks.replace("_", " ")
        self._finalize_capture(name)
        return "break"

    def _finalize_capture(self, name: str) -> None:
        """Commit the captured key. Supersedes a deferred Left-Ctrl (AltGr case)."""
        pend = getattr(self, "_pending_capture", None)
        if pend is not None:
            try: self._win.after_cancel(pend)
            except Exception: pass
            self._pending_capture = None
        if not self._hotkey_capturing:
            return
        self._key_var.set(name)
        logger.info(f"hotkey capture: bound {name!r}")
        self._end_hotkey_capture(cancelled=False)
        if len(name) == 1:
            self._toast("Буква как PTT мешает обычной печати — лучше "
                        "Right Alt / Right Ctrl / F-клавиша", ok=False)

    def _end_hotkey_capture(self, cancelled: bool) -> None:
        if not self._hotkey_capturing:
            return
        self._hotkey_capturing = False
        pend = getattr(self, "_pending_capture", None)
        if pend is not None:
            try: self._win.after_cancel(pend)
            except Exception: pass
            self._pending_capture = None
        try:
            self._win.unbind("<KeyPress>")
        except Exception:
            logger.debug("capture unbind failed", exc_info=True)
        # Вернуть хуки как было: новая клавиша применится после «Сохранить»
        # (on_save → _register_hooks с новым конфигом).
        if self._hook_resume is not None:
            try:
                self._hook_resume()
            except Exception:
                logger.exception("hook resume failed")
        try:
            self._hotkey_btn.configure(
                text="Изменить",
                fg_color=getattr(self, "_hotkey_btn_fg", None) or
                ctk.ThemeManager.theme["CTkButton"]["fg_color"])
        except Exception:
            logger.debug("capture button restore failed", exc_info=True)
        try:
            self._hotkey_lbl.configure(
                text=self._hotkey_pretty(self._key_var.get()))
        except Exception:
            logger.debug("capture label restore failed", exc_info=True)

    def _on_engine_change(self, _choice: str = "") -> None:
        """Engine switched. GigaAM has one fixed model. Whisper keeps whatever
        size the «Скорость↔Качество» slider holds (default large-v3-turbo).
        The slider is shown only for Whisper (GigaAM has nothing to tune)."""
        e = self._engine_value()
        if e == "gigaam":
            self._cfg.stt.gigaam_model = self._FIXED_MODEL["gigaam"]
        else:
            self._cfg.stt.model = self._FIXED_MODEL["whisper"]
        self._cfg.stt.engine = e
        self._sync_engine_locks()

    # Engines whose runtime (onnxruntime) is NOT thread-safe: a background
    # pre-decode/stream running concurrently with the final transcription
    # crashes the process natively (0xc0000005). Streaming insert relies on that
    # concurrent decode, so it must stay OFF for them.
    _UNSAFE_PARALLEL_ENGINES = ("gigaam",)

    # Engines locked to a single language (no language picker). GigaAM is RU-only.
    _FIXED_LANGUAGE = {"gigaam": "ru"}

    def _sync_engine_locks(self) -> None:
        """Sync per-engine UI locks when the engine changes:
        - streaming checkbox: greyed off for non-thread-safe (onnx) engines;
        - language dropdown: locked to the engine's fixed language (GigaAM=ru),
          enabled with the full list for multilingual Whisper.
        Belt-and-suspenders: main.py also ignores streaming for unsafe engines."""
        e = self._engine_value()
        chk = getattr(self, "_streaming_chk", None)
        if chk is not None:
            try:
                if e in self._UNSAFE_PARALLEL_ENGINES:
                    self._streaming_var.set(False)
                    chk.configure(state="disabled")
                else:
                    chk.configure(state="normal")
            except Exception:
                logger.debug("_sync_engine_locks: suppressed", exc_info=True)
        lang_cb = getattr(self, "_lang_cb", None)
        if lang_cb is not None:
            try:
                fixed = self._FIXED_LANGUAGE.get(e)
                if fixed:
                    self._lang_var.set(self._LANG_LABELS.get(fixed, fixed))
                    lang_cb.configure(state="disabled")
                else:
                    lang_cb.configure(state="normal")
            except Exception:
                logger.debug("_sync_engine_locks: suppressed", exc_info=True)

    def _page(self, title: str, keywords: str = ""):
        """Return (creating if needed) the scrollable content frame for a
        sidebar category. Sections build their widgets into this frame."""
        p = self._pages.get(title)
        if p:
            return p["frame"]
        outer = ctk.CTkScrollableFrame(self._content)
        outer.grid(row=0, column=0, sticky="nsew")
        # Padded inner frame → every section gets a consistent left/right margin
        # so fields never touch the window edge or the scrollbar.
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=(_s(14), _s(18)), pady=_s(2))
        btn = ctk.CTkButton(
            self._nav, text=title, anchor="w", height=_s(34),
            fg_color="transparent", hover_color=("#cfcfcf", "#2d2d2d"),
            text_color=("#1a1a1a", "#e8e8e8"),
            command=lambda t=title: self._show_page(t),
        )
        btn.pack(fill="x", pady=1)
        self._pages[title] = {"frame": inner, "outer": outer, "btn": btn,
                              "kw": keywords}
        self._page_order.append(title)
        if self._active_page is None:
            self._show_page(title)
        return inner

    def _show_page(self, title: str) -> None:
        p = self._pages.get(title)
        if not p:
            return
        # Show exactly one page. tkraise() on stacked CTkScrollableFrames does
        # not reliably restack, so explicitly grid the active one and
        # grid_remove the rest (grid_remove keeps the cell config for re-show).
        for t, pp in self._pages.items():
            if t == title:
                pp["outer"].grid()
            else:
                pp["outer"].grid_remove()
            pp["btn"].configure(
                fg_color=(self._NAV_SEL if t == title else "transparent"))
        self._active_page = title
        SettingsWindow._last_page = title
        # Live mic meter runs only on the «Аудио» tab → start/stop on switch.
        self._update_mic_meter()
        # Re-wrap hints for the freshly shown page once its layout settles.
        try:
            self._win.after(10, self._apply_wrap)
        except Exception:
            logger.debug("_show_page: suppressed", exc_info=True)

    def _on_ctrl_mousewheel(self, event) -> "str | None":
        """Ctrl+wheel → scroll the active page. customtkinter binds only plain
        «<MouseWheel>», so with Ctrl held the page would otherwise not scroll."""
        p = self._pages.get(self._active_page)
        if p:
            canvas = getattr(p["outer"], "_parent_canvas", None)
            if canvas is not None:
                try:
                    canvas.yview_scroll(-1 * int(event.delta / 120), "units")
                    return "break"
                except Exception:
                    pass
        return None

    def _filter_nav(self, _evt=None) -> None:
        q = self._nav_search.get().strip()
        self._ensure_search_index()
        for t in self._page_order:
            try: self._pages[t]["btn"].pack_forget()
            except Exception: pass
        matched = [t for t in self._page_order
                   if search_index.matches(q, self._search_blobs.get(t, ""))]
        for t in matched:
            self._pages[t]["btn"].pack(fill="x", pady=1)
        # Chrome-like: if the page you're viewing fell out of the results, jump
        # to the first match so the relevant options show up immediately.
        if q and matched and self._active_page not in matched:
            self._show_page(matched[0])

    def _ensure_search_index(self) -> None:
        """Build (once, lazily) a normalized search blob per page = title +
        keywords + the text of every option/label/button/dropdown on that page.
        This is what makes the nav search 'global' — it matches INSIDE options,
        not just the category name."""
        if getattr(self, "_search_blobs", None) is not None:
            return
        self._search_blobs = {}
        for t in self._page_order:
            parts = [t, self._pages[t].get("kw", "")]
            try:
                self._collect_texts(self._pages[t]["frame"], parts)
            except Exception:
                logger.debug("search index walk failed", exc_info=True)
            self._search_blobs[t] = search_index.normalize(" ".join(parts))

    def _collect_texts(self, widget, out: list) -> None:
        """Recursively gather user-visible text from a page's widget tree."""
        for c in widget.winfo_children():
            for opt in ("text", "placeholder_text"):
                try:
                    v = c.cget(opt)
                    if isinstance(v, str) and v.strip():
                        out.append(v)
                except Exception:
                    pass
            try:
                vals = c.cget("values")
                if isinstance(vals, (list, tuple)):
                    out.extend(str(v) for v in vals)
            except Exception:
                pass
            self._collect_texts(c, out)

    def _autowrap_hints(self) -> None:
        """Collect long labels and keep them wrapped to the viewport width — fixes
        hint text running off the right edge at large UI scale. Wrapping to the
        label's own width fails (a long unwrapped CTkLabel inflates the frame and
        its reqwidth ignores wraplength), so we key off the right pane width."""
        labels: list = []
        checks: list = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ctk.CTkCheckBox):
                    # CTkCheckBox text has no wraplength of its own; _apply_wrap
                    # wraps its internal label so long options fit too.
                    checks.append(c)
                elif isinstance(c, ctk.CTkLabel):
                    try:
                        txt = c.cget("text")
                    except Exception:
                        txt = ""
                    # Hints AND section headers (≥25 chars). Skip inline row
                    # labels (packed side left/right next to a field) — wrapping
                    # those to 2 lines beside a single-line combo looks broken.
                    if len(txt) > 24:
                        side = ""
                        try: side = (c.pack_info() or {}).get("side", "")
                        except Exception: side = ""
                        if side not in ("left", "right"):
                            try: c.configure(justify="left")
                            except Exception: pass
                            labels.append(c)
                walk(c)

        try:
            walk(self._content)
        except Exception:
            logger.debug("autowrap walk failed", exc_info=True)
            labels, checks = [], []
        self._hint_labels = labels
        self._hint_checks = checks
        self._content.bind("<Configure>", lambda _e: self._apply_wrap())
        self._win.after(60, self._apply_wrap)
        self._win.after(240, self._apply_wrap)

    def _apply_wrap(self) -> None:
        try:
            w = self._content.winfo_width()
        except Exception:
            return
        if w <= 1:
            return
        # winfo_width() is in physical px, but CTkLabel.configure(wraplength=…)
        # multiplies by CTk's widget-scaling (DPI). Divide it out to get the
        # viewport width in the same logical units wraplength uses.
        try:
            sc = ctk.ScalingTracker.get_widget_scaling(self._content)
        except Exception:
            sc = 1.0
        # Reserve the REAL chrome between the viewport edge and the text, in
        # logical units: the page inner-frame L/R padding (_s(14)+_s(18), see
        # _page), the scrollable frame's scrollbar, and the label's own padding
        # + a safety gap (_s(34)). A flat percentage (was 0.88) left only a few
        # px once those were subtracted, so the last word clipped at the edge.
        avail = w / max(sc, 0.1)
        wrap = max(_s(120), int(avail) - _s(14) - _s(18) - _s(34))
        for lw in getattr(self, "_hint_labels", ()):
            try:
                if abs((lw.cget("wraplength") or 0) - wrap) > 2:
                    lw.configure(wraplength=wrap)
            except Exception:
                pass
        # Checkboxes: their internal tk.Label wraps in PHYSICAL px (CTk does NOT
        # rescale it), and the box+gap eats ~40 logical px before the text
        # starts — subtract both so long option labels fit too.
        cwrap = max(int(_s(120) * sc), int((wrap - 40) * max(sc, 0.1)))
        for cb in getattr(self, "_hint_checks", ()):
            tl = getattr(cb, "_text_label", None)
            if tl is None:
                continue
            # NB: tl is a raw tk.Label; cget("wraplength") returns a Tcl_Obj
            # («'0'»), not an int — arithmetic on it raises, so coerce via str.
            try:
                cur = int(str(tl.cget("wraplength")) or 0)
            except Exception:
                cur = 0
            if abs(cur - cwrap) > 2:
                try: tl.configure(wraplength=cwrap)
                except Exception: pass

    def _toast(self, msg: str, ok: bool = True) -> None:
        """Transient status line in the bottom bar (auto-clears)."""
        try:
            self._toast_lbl.configure(text=msg,
                                      text_color=(_TEAL if ok else "#ff5050"))
            self._toast_lbl.after(3500,
                                  lambda: self._toast_lbl.configure(text=""))
        except Exception:
            logger.debug("settings toast failed", exc_info=True)

    # ── Dirty tracking: «●» на кнопке, пока есть несохранённые правки ─────────

    def _install_dirty_traces(self) -> None:
        """Mark the form dirty on any edit: every tk.Variable on the instance
        gets a write-trace (row editors report через свой on_change). Зовётся
        после _build — программные set'ы во время постройки не считаются."""
        self._dirty = False
        self._loading_form = False
        for name, val in list(self.__dict__.items()):
            if isinstance(val, tk.Variable):
                try:
                    val.trace_add("write", self._mark_dirty)
                except Exception:
                    logger.debug("dirty trace failed for %s", name,
                                 exc_info=True)

    def _mark_dirty(self, *_a) -> None:
        if getattr(self, "_loading_form", False) or getattr(self, "_dirty", False):
            return
        self._dirty = True
        try:
            self._apply_btn.configure(text="●  Сохранить")
        except Exception:
            logger.debug("dirty button update failed", exc_info=True)

    def _mark_clean(self) -> None:
        self._dirty = False
        try:
            self._apply_btn.configure(text="Сохранить")
        except Exception:
            logger.debug("clean button update failed", exc_info=True)

    def _on_escape(self, _e=None):
        """Esc: во время захвата хоткея — отменить захват, иначе закрыть окно
        (с вопросом про несохранённые правки в _safe_destroy)."""
        if getattr(self, "_hotkey_capturing", False):
            self._end_hotkey_capture(cancelled=True)
            return "break"
        self._safe_destroy()
        return "break"

    def _on_theme_change(self, _choice: str = "") -> None:
        _apply_theme(self._theme_var.get())

    # Sidebar categories, in display order: (title, search keywords).
    # Пять вкладок: три простые (для всех) + Интерфейс + «Дополнительно»
    # (всё для энтузиастов одним списком). keywords объединены для поиска.
    _PAGE_DEFS = [
        ("Основное",        "горячая клавиша hotkey тихий режим whisper шёпот "
                            "распознавание речи stt движок модель gigaam язык "
                            "устройство cuda gpu паразиты филлеры "
                            "управление голосом hands-free hey jarvis старт стоп "
                            "стоп-стоп ввод чувствительность"),
        ("Аудио",           "микрофон mic источник система loopback нормализация шум noise ducker приглушать"),
        ("Интерфейс",       "тема theme светлая тёмная dark light шрифт масштаб виджет pill "
                            "прозрачность glow автозапуск autostart startup explorer проводник"),
        ("Текст и вставка", "замены replace числа itn пунктуация "
                            "мат profanity вставка sendinput uia "
                            "буфер clipboard стриминг streaming форматирование окошко"),
        ("Голос",           "голосовые команды voice история history звуки sounds"),
    ]

    def _build(self, win: ctk.CTkToplevel) -> None:
        self._pages: dict = {}
        self._page_order: list[str] = []
        self._active_page: str | None = None

        # customtkinter's CTkScrollableFrame binds only «<MouseWheel>», so holding
        # Ctrl turns the event into «<Control-MouseWheel>» and the page stops
        # scrolling. Bind that too → Ctrl+wheel scrolls the active page like a
        # plain wheel (instead of doing nothing).
        win.bind_all("<Control-MouseWheel>", self._on_ctrl_mousewheel, add="+")
        # Esc закрывает окно (или отменяет захват хоткея); Ctrl+S сохраняет.
        # Plain bind (не add) — при rebuild не плодит дубликатов.
        win.bind("<Escape>", self._on_escape)
        win.bind("<Control-s>", lambda _e: (self._save(), "break")[1])
        win.bind("<Control-S>", lambda _e: (self._save(), "break")[1])

        # NB: the Save button + toast live at the bottom of the left sidebar
        # (built below), not in a separate bottom bar — keeps the content area
        # taller and the button out of the way.

        # ── Body: left nav (search + categories) + right content stack ───────
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True)
        nav_wrap = ctk.CTkFrame(body, width=_s(160), fg_color=_CARD_BG,
                                corner_radius=0)
        nav_wrap.pack(side="left", fill="y")
        nav_wrap.pack_propagate(False)
        self._nav_search = ctk.CTkEntry(nav_wrap, placeholder_text="🔍 Поиск…")
        self._nav_search.pack(fill="x", padx=_s(8), pady=_s(8))
        self._nav_search.bind("<KeyRelease>", self._filter_nav)
        _enable_entry_clipboard(self._nav_search)
        # «Сохранить» + toast pinned to the bottom of the sidebar (under «Аудио»).
        # Packed before the list so the scrollable list fills only the space
        # above them. While the form has unsaved edits the button shows «●».
        self._apply_btn = ctk.CTkButton(nav_wrap, text="Сохранить",
                                        height=_s(38), command=self._save)
        self._apply_btn.pack(side="bottom", fill="x", padx=_s(8),
                             pady=(_s(4), _s(10)))
        self._toast_lbl = ctk.CTkLabel(nav_wrap, text="", font=_f("Segoe UI", 9),
                                       anchor="w", justify="left",
                                       wraplength=_s(160))
        self._toast_lbl.pack(side="bottom", fill="x", padx=_s(8), pady=(0, _s(2)))
        self._nav = ctk.CTkScrollableFrame(nav_wrap, fg_color="transparent")
        self._nav.pack(fill="both", expand=True, padx=_s(4), pady=(0, _s(6)))
        self._content = ctk.CTkFrame(body, fg_color="transparent")
        self._content.pack(side="left", fill="both", expand=True)
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)
        # Don't let a tall/wide page resize the content area (pages scroll).
        self._content.grid_propagate(False)
        self._content.pack_propagate(False)

        # Pre-create pages so the sidebar order is fixed regardless of the
        # order sections are built below.
        for title, kw in self._PAGE_DEFS:
            self._page(title, kw)

        self._build_page_main(self._page("Основное"))

        # ── Текст и вставка ───────────────────────────────────────────────────
        self._build_page_text_io(self._page("Текст и вставка"))

        # ── Audio ─────────────────────────────────────────────────────────────
        self._build_page_audio(self._page("Аудио"))

        # ── Голос, шаблоны, прочее ────────────────────────────────────────────
        self._build_page_voice_misc(self._page("Голос"))

        # ── Интерфейс ─────────────────────────────────────────────────────────
        self._build_page_interface(self._page("Интерфейс"))

        # Long hints/headers wrap to available width instead of overflowing.
        self._autowrap_hints()

    def _build_page_main(self, sf) -> None:
        # (Слайдер «Скорость↔Качество» удалён: GigaAM v3 + Whisper large —
        # две фиксированные модели, регулировать размер нечем.)

        # ── Горячая клавиша ──────────────────────────────────────────────────
        self._hdr(sf, "Горячая клавиша")
        self._key_var = tk.StringVar(value=self._cfg.hotkey.key)
        r = ctk.CTkFrame(sf, fg_color="transparent")
        r.pack(fill="x", pady=3)
        ctk.CTkLabel(r, text="Клавиша:", width=_s(110), anchor="w").pack(side="left")
        self._hotkey_btn = ctk.CTkButton(r, text="Изменить", width=_s(120),
                                         height=_s(30),
                                         command=self._begin_hotkey_capture)
        self._hotkey_btn.pack(side="right", padx=(_s(8), 0))
        self._hotkey_lbl = ctk.CTkLabel(
            r, text=self._hotkey_pretty(self._key_var.get()),
            font=_f("Segoe UI", 12, "bold"), anchor="w", justify="left",
            wraplength=_s(300))
        self._hotkey_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            sf,
            text="Эта клавиша запускает запись. «Изменить» → нажми новую "
                 "клавишу (Esc — отмена). Лучше всего — клавиша-модификатор "
                 "(Right Alt, Right Ctrl) или F-клавиша: они не мешают печати.",
            text_color=_HINT_FG, font=_FONT_DIM(), anchor="w",
            justify="left", wraplength=_s(540)).pack(fill="x", pady=(0, _s(6)))

        # Example combination as plain text (informational; wraps if needed).
        ex = ctk.CTkFrame(sf, fg_color="transparent")
        ex.pack(fill="x", pady=(0, 0))
        ctk.CTkLabel(ex, text="Например:", width=_s(110), anchor="w",
                     text_color=_HINT_FG, font=_FONT_DIM()).pack(side="left")
        ctk.CTkLabel(ex, text="Ctrl + Alt + Space",
                     font=_f("Segoe UI", 11, "bold"), anchor="w", justify="left",
                     wraplength=_s(380)).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            sf,
            text="Сочетание Ctrl + Alt + Space нажимается целиком — удерживай "
                 "все три клавиши, пока говоришь, и отпусти, когда закончил. "
                 "Сочетание из нескольких клавиш не сработает случайно, в "
                 "отличие от одиночной.",
            text_color=_HINT_FG, font=_FONT_DIM(), anchor="w",
            justify="left", wraplength=_s(540)).pack(fill="x", pady=(_s(2), 4))

        # «Режим» (hold/toggle) selector removed from the UI — keep a hidden
        # holder so save preserves the configured mode (default «hold»).
        self._mode_var = tk.StringVar(value=self._cfg.hotkey.mode)

        # ── Распознавание речи (вкладка «Основное») ───────────────────────────
        sf = self._page("Основное")
        self._hdr(sf, "Распознавание речи")
        _LW = _s(115)            # shared label column width

        # Engine
        r3 = ctk.CTkFrame(sf, fg_color="transparent")
        r3.pack(fill="x", pady=_s(5))
        ctk.CTkLabel(r3, text="Модель:", width=_LW, anchor="w").pack(side="left")
        self._engine_var = tk.StringVar(
            value=self._ENGINE_LABELS.get(self._cfg.stt.engine, "Русский — быстрый"))
        ctk.CTkComboBox(r3, variable=self._engine_var,
                        values=list(self._ENGINE_LABELS.values()),
                        command=self._on_engine_change,
                        width=_s(190)).pack(side="left")
        ctk.CTkLabel(
            sf,
            text="Какая нейросеть превращает речь в текст. "
                 "«Любой язык» (Whisper) — универсальный и точный. "
                 "«Русский — быстрый» (GigaAM) — только русский, но шустрее.",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", pady=(0, _s(8)))

        # No model picker — each engine uses one fixed model (_FIXED_MODEL).
        # Keep _model_var as a hidden holder so other code can read it.
        self._model_var = tk.StringVar(value=self._current_engine_model())

        # Language
        r3l = ctk.CTkFrame(sf, fg_color="transparent")
        r3l.pack(fill="x", pady=_s(5))
        ctk.CTkLabel(r3l, text="Язык:", width=_LW, anchor="w").pack(side="left")
        self._lang_var = tk.StringVar(
            value=self._LANG_LABELS.get(self._cfg.stt.language or "", "Авто"))
        self._lang_cb = ctk.CTkComboBox(
            r3l, variable=self._lang_var,
            values=list(self._LANG_LABELS.values()),
            width=_s(150))
        self._lang_cb.pack(side="left")
        ctk.CTkLabel(sf, text="«Русский — быстрый» понимает только русский; «Любой язык» — любой",
                     text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(540)).pack(fill="x", pady=(_s(2), 0))

        # Device (plain-language labels; the real value is stored on save)
        r3b = ctk.CTkFrame(sf, fg_color="transparent")
        r3b.pack(fill="x", pady=(_s(8), _s(5)))
        ctk.CTkLabel(r3b, text="Устройство:", width=_LW, anchor="w").pack(side="left")
        self._device_var = tk.StringVar(
            value=self._DEVICE_LABELS.get(self._cfg.stt.device, "Процессор"))
        ctk.CTkComboBox(r3b, variable=self._device_var,
                        values=list(self._DEVICE_LABELS.values()),
                        width=_s(200)).pack(side="left")
        ctk.CTkLabel(
            sf,
            text="На чём считать. Процессор — работает на любом компьютере, но "
                 "медленно. Видеокарта NVIDIA — если она есть, распознаёт в 5–10 раз "
                 "быстрее. Авто — пусть Talker выберет сам.",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", pady=(0, _s(8)))

        # Compute precision (compute_type) and CPU-thread count are AUTO now —
        # no UI knobs for them. Precision: int8 on CPU / float16 on GPU (picked
        # in transcriber.py). Threads: 0 = physical-core count.

        # ── Управление голосом (старт/стоп без рук) ──────────────────────────
        self._hdr(sf, "Управление голосом  (старт/стоп без рук)")
        self._voice_gate_var = tk.BooleanVar(value=self._cfg.output.voice_gate)
        ctk.CTkCheckBox(sf, text="Голосовой старт/стоп",
                        variable=self._voice_gate_var).pack(anchor="w", pady=(_s(6), _s(1)))
        ctk.CTkLabel(sf, text="Старт — скажи «Hey Jarvis» (Talker начнёт слушать) и "
                              "диктуй. Конец — «стоп-стоп». «ввод-ввод» — закончить и "
                              "сразу нажать Enter (отправить).",
                     text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(540)).pack(
                         fill="x", padx=(_s(26), 0), pady=(0, _s(4)))
        ctk.CTkLabel(sf, text="Чувствительность: левее — строже (реже ложные "
                              "срабатывания), правее — ловит легче.",
                     text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(540)).pack(
                         fill="x", padx=(_s(26), 0), pady=(_s(2), 0))
        self._wake_sens_var = tk.StringVar(
            value=str(self._nearest_level(self._cfg.wake.threshold, self._WAKE_LEVELS)))
        self._scale_slider(sf, "«Hey Jarvis»:", self._wake_sens_var,
                           1, 5, 4, None, fmt="{:.0f}")
        self._stop_sens_var = tk.StringVar(
            value=str(self._nearest_level(self._cfg.wake.stop_fuzzy, self._STOP_LEVELS)))
        self._scale_slider(sf, "«стоп-стоп»:", self._stop_sens_var,
                           1, 5, 4, None, fmt="{:.0f}")

        # ── Continuous VAD — скрыто из UI (разумные дефолты в коде/конфиге) ────
        # Крутилки тишины/чувствительности/детектора речи — экспертная внутрянка
        # детектора пауз; в обычном single_shot-режиме не нужны. Держим как
        # скрытые носители, чтобы save/валидация работали, не показывая юзеру.
        sf = self._page("Основное")
        self._silence_var = tk.StringVar(value=str(self._cfg.continuous.silence_secs))
        self._vad_var = tk.StringVar(value=str(self._cfg.continuous.vad_aggressiveness))
        self._vad_engine_var = tk.StringVar(value=self._cfg.continuous.vad_engine)

    def _build_page_text_io(self, sf) -> None:
        # Short checkbox label + wrapped plain-language explanation underneath
        # (CTkCheckBox text doesn't wrap, so long labels would run off-screen).
        def _chk(label, hint, var, indent=0):
            cb = ctk.CTkCheckBox(sf, text=label, variable=var)
            cb.pack(anchor="w", pady=(_s(6), _s(1)), padx=(_s(indent), 0))
            if hint:
                ctk.CTkLabel(sf, text=hint, text_color=_HINT_FG,
                             font=_f("Segoe UI", 9), anchor="w", justify="left",
                             wraplength=_s(540)).pack(
                    fill="x", padx=(_s(indent + 26), 0), pady=(0, _s(4)))
            return cb

        # ── Замены ───────────────────────────────────────────────────────────
        self._hdr(sf, "Замены  (как надо → что слышится)")
        ctk.CTkLabel(
            sf, text="Слева — как писать правильно; справа — что могло послышаться "
                     "(через запятую). Например: Claude → клод, клауд, клот.",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", pady=(0, _s(4)))
        self._repl_editor = _ListEditor(
            sf,
            columns=[
                {"key": "to",   "kind": "entry", "width": 120,
                 "placeholder": "как надо"},
                {"key": "from", "kind": "entry", "width": 160,
                 "placeholder": "что слышится (через запятую)"},
            ],
            rows=[{"to": r.to, "from": ", ".join(r.from_)}
                  for r in self._cfg.replacements if r.to],
            add_label="+ Замена",
            on_change=self._mark_dirty,
        )
        self._repl_editor.pack(fill="x", pady=(_s(4), 0))

        # ── Словарь (имена и термины) ────────────────────────────────────────
        self._hdr(sf, "Словарь  (имена, бренды, термины)")
        ctk.CTkLabel(
            sf, text="Слова, которые Talker должен узнавать в речи. Подсказываются "
                     "движку распознавания и пополняются сами, когда правишь "
                     "результат через «Поправить» в окошке после диктовки.",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", pady=(0, _s(4)))
        self._vocab_editor = _ListEditor(
            sf,
            columns=[{"key": "word", "kind": "entry", "width": 240,
                      "placeholder": "слово или фраза"}],
            rows=[{"word": w} for w in self._cfg.vocabulary.words],
            add_label="+ Слово",
            on_change=self._mark_dirty,
        )
        self._vocab_editor.pack(fill="x", pady=(_s(4), 0))

        # ── Обработка текста (галки) ─────────────────────────────────────────
        self._hdr(sf, "Обработка текста")
        self._remove_fillers_var = tk.BooleanVar(value=self._cfg.output.remove_fillers)
        ctk.CTkCheckBox(sf, text="Убирать слова-паразиты («ну», «короче», «э-э»)",
                        variable=self._remove_fillers_var).pack(anchor="w", pady=_s(3))
        ctk.CTkLabel(sf, text="Вырезает «ну / короче / типа» и звуки-заминки "
                              "«э-э / эм / мм» из готового списка — ничего не "
                              "выдумывает. Спорные слова («вот», «значит») не трогает.",
                     text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(540)).pack(fill="x", pady=(0, _s(4)))
        self._numfmt_var = tk.BooleanVar(value=self._cfg.output.number_format)
        ctk.CTkCheckBox(sf, text="Числа писать цифрами — 25 %, 2026",
                        variable=self._numfmt_var).pack(anchor="w", pady=_s(3))
        self._mask_prof_var = tk.BooleanVar(value=self._cfg.output.mask_profanity)
        ctk.CTkCheckBox(sf, text="Маскировать мат — «хуй» → «х*й»",
                        variable=self._mask_prof_var).pack(anchor="w", pady=_s(3))
        # ── Вставка ──────────────────────────────────────────────────────────
        self._hdr(sf, "Вставка текста")
        inj_row = ctk.CTkFrame(sf, fg_color="transparent")
        inj_row.pack(fill="x", pady=_s(5))
        ctk.CTkLabel(inj_row, text="Способ вставки:", width=_s(170),
                     anchor="w").pack(side="left")
        self._inj_var = tk.StringVar(
            value=self._INJ_LABELS.get(self._cfg.output.injection_mode, "Автоматически"))
        ctk.CTkComboBox(inj_row, variable=self._inj_var,
                        values=list(self._INJ_LABELS.values()),
                        width=_s(195)).pack(side="left")
        ctk.CTkLabel(
            sf,
            text="Как Talker помещает текст туда, где стоит курсор. Оставь "
                 "«Автоматически» — он сам подберёт рабочий способ. Меняй, только "
                 "если текст не вставляется.",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", padx=_s(2), pady=(0, _s(8)))
        self._copy_clip_var = tk.BooleanVar(value=self._cfg.output.copy_to_clipboard)
        _chk("Копировать текст в буфер обмена",
             "Надиктованный текст остаётся в буфере обмена — можно вставить его "
             "вручную через Ctrl+V.", self._copy_clip_var)
        self._restore_var = tk.BooleanVar(value=self._cfg.output.restore_clipboard)
        _chk("Сохранять мой буфер обмена",
             "Talker иногда вставляет через копирование. С этой галочкой он "
             "вернёт в буфер то, что там лежало до диктовки — твоё «скопировано» "
             "не потеряется.", self._restore_var)
        self._bubble_var = tk.BooleanVar(value=self._cfg.output.show_bubble)
        _chk("Показывать окошко с кнопкой «Копировать»",
             "Если текст не вставился в поле сам — скопируешь его из этого "
             "окошка вручную.", self._bubble_var)
        # Lock the language dropdown for single-language engines (GigaAM = ru).
        self._sync_engine_locks()
        self._smart_format_var = tk.BooleanVar(value=self._cfg.output.smart_format)
        _chk("Поправлять заглавную букву и пробел",
             "Talker сам сделает первую букву заглавной и добавит пробел перед "
             "вставкой. Срабатывает не везде — некоторые приложения (например, "
             "на базе Chrome) это игнорируют.", self._smart_format_var)

    def _build_page_voice_misc(self, sf) -> None:
        def _chk(label, hint, var, indent=0):
            cb = ctk.CTkCheckBox(sf, text=label, variable=var)
            cb.pack(anchor="w", pady=(_s(6), _s(1)), padx=(_s(indent), 0))
            if hint:
                ctk.CTkLabel(sf, text=hint, text_color=_HINT_FG,
                             font=_f("Segoe UI", 9), anchor="w", justify="left",
                             wraplength=_s(540)).pack(
                    fill="x", padx=(_s(indent + 26), 0), pady=(0, _s(4)))
            return cb

        # ── Голосовые команды (галка-включатель + список) ────────────────────
        self._hdr(sf, "Голосовые команды  (фраза → действие)")
        self._vc_enabled_var = tk.BooleanVar(value=self._cfg.output.voice_commands)
        _chk("Включить голосовые команды",
             "«talker новый абзац», «talker удали последнее слово», «talker "
             "отправь» и т.п. — выполняют действие, а не вставляют этот текст.",
             self._vc_enabled_var)
        ctk.CTkLabel(
            sf,
            text="Произнеси «talker <фраза>». «Вставить текст» — печатает значение "
                 "(\\n = перенос строки); «Нажать клавишу» — жмёт клавиши "
                 "(enter, tab, esc, ctrl+backspace).",
            text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(540),
        ).pack(fill="x", padx=2)
        self._vc_editor = _ListEditor(
            sf,
            columns=[
                {"key": "phrase", "kind": "entry", "width": 90,
                 "placeholder": "фраза"},
                {"key": "action", "kind": "combo", "width": 130,
                 "values": list(self._ACTION_LABELS.values())},
                {"key": "value", "kind": "entry", "width": 90,
                 "placeholder": r"значение: \n · enter · ctrl+backspace"},
            ],
            rows=[{"phrase": v.phrase,
                   "action": self._ACTION_LABELS.get(v.action, "Вставить текст"),
                   "value": v.value.replace("\n", "\\n")}
                  for v in self._cfg.voice_commands],
            add_label="+ Команда",
            on_change=self._mark_dirty,
        )
        self._vc_editor.pack(fill="x", pady=(4, 0))

        # ── История ──────────────────────────────────────────────────────────
        self._hdr(sf, "История")
        h1 = ctk.CTkFrame(sf, fg_color="transparent")
        h1.pack(fill="x", pady=3)
        ctk.CTkLabel(h1, text="Максимум записей:", width=_s(180), anchor="w").pack(side="left")
        self._hist_max_var = tk.StringVar(value=str(self._cfg.history.max_entries))
        ctk.CTkEntry(h1, textvariable=self._hist_max_var, width=_s(80)).pack(side="left")

        h2 = ctk.CTkFrame(sf, fg_color="transparent")
        h2.pack(fill="x", pady=3)
        ctk.CTkLabel(h2, text="Хранить (дней):", width=_s(180), anchor="w").pack(side="left")
        self._hist_days_var = tk.StringVar(value=str(self._cfg.history.retention_days))
        ctk.CTkEntry(h2, textvariable=self._hist_days_var, width=_s(80)).pack(side="left")
        ctk.CTkLabel(h2, text="0 = бессрочно",
                     text_color="#666", font=_f("Segoe UI", 9)).pack(side="left", padx=(8, 0))

        self._hist_clear_var = tk.BooleanVar(value=self._cfg.history.on_quit_clear)
        ctk.CTkCheckBox(sf, text="Очищать историю при выходе",
                        variable=self._hist_clear_var).pack(anchor="w", pady=3)

        # ── Звуки диктовки (концепт 36, часть A) ─────────────────────────────
        self._hdr(sf, "Звуки диктовки")
        try:
            from sound_settings_panel import SoundSettings
            # Persist ONLY the sounds section (merge-safe): writing the whole
            # stale self._cfg here затирало бы поля чужих писателей.
            SoundSettings(sf, player=self._player,
                          get_cfg=lambda: self._cfg.sounds,
                          on_change=lambda: update_config(
                              lambda c: setattr(c, "sounds", self._cfg.sounds))
                          ).pack(fill="x", pady=(_s(4), 0))
        except Exception:
            logger.exception("Sound settings panel failed")

    def _hdr(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=_f("Segoe UI", 13),
                     anchor="w").pack(fill="x", pady=(_s(12), _s(1)))
        ctk.CTkFrame(parent, height=max(1, _s(1)),
                     fg_color=_SEP_FG).pack(fill="x", pady=(0, _s(5)))

    # ── Font scale buttons (in-place: rebuild widgets without closing window) ──

    def _bump_font_scale(self, delta: float) -> None:
        self._set_font_scale(round(_UiScale.value + delta, 2))

    def _bump_widget_scale(self, delta: float) -> None:
        """Pill size, live. Independent of the font scale; persisted alongside
        the rest of the form without the heavy save / model-reload path."""
        try:
            cur = float(self._cfg.widget.scale)
        except (TypeError, ValueError):
            cur = 1.0
        new = round(max(0.3, min(3.0, cur + delta)), 2)
        if abs(new - cur) < 0.005:
            return
        self._cfg.widget.scale = new
        # Keep the Интерфейс-page entry and the header readout in sync.
        # Программная синхронизация — не пользовательская правка, поэтому
        # глушим dirty-трейс на время set().
        self._loading_form = True
        try:
            if getattr(self, "_widget_scale_var", None) is not None:
                self._widget_scale_var.set(str(new))
        except Exception:
            logger.debug("widget scale var sync failed", exc_info=True)
        finally:
            self._loading_form = False
        try:
            self._widget_size_lbl.configure(text=f"Виджет ×{new:.1f}")
        except Exception:
            logger.debug("_bump_widget_scale: suppressed", exc_info=True)
        # Live preview on the running pill (cheap — no model reload / hook churn).
        if self._on_widget_preview:
            try:
                self._on_widget_preview(self._cfg)
            except Exception:
                logger.debug("widget preview failed", exc_info=True)
        # Persist ONLY the widget scale (read-modify-write on a fresh config):
        # other form edits stay unsaved until «Сохранить», и ничего устаревшего
        # не затирает поля чужих писателей (позиция пилюли, словарь, тогглы).
        try:
            update_config(lambda c: setattr(c.widget, "scale", new))
        except Exception:
            logger.debug("persist widget scale failed", exc_info=True)

    def _set_font_scale(self, new_scale: float) -> None:
        new_scale = max(0.5, min(4.0, new_scale))
        if abs(new_scale - _UiScale.value) < 0.005:
            return
        _UiScale.set(new_scale)
        self._cfg.ui.font_scale = new_scale
        # Persist ONLY the scale itself (merge-safe). The rest of the form is
        # snapshotted in memory below so the rebuild keeps unsaved edits —
        # without writing them to disk behind the user's back.
        try:
            update_config(lambda c: setattr(c.ui, "font_scale", new_scale))
        except Exception:
            logger.exception("Could not persist font_scale")
        try:
            self._snapshot_form_into_cfg()
        except Exception:
            logger.debug("snapshot before rebuild failed", exc_info=True)
        self._rebuild_in_place()

    def _rebuild_in_place(self) -> None:
        """Re-render the settings content at the current UI scale *without*
        closing the window — clear its children and run _build() again. Renders
        from the in-memory self._cfg (callers snapshot the form into it first),
        so unsaved edits survive; the dirty flag survives with them."""
        win = self._win
        was_dirty = getattr(self, "_dirty", False)
        try:
            for child in list(win.winfo_children()):
                try: child.destroy()
                except Exception: logger.debug("_rebuild_in_place: suppressed", exc_info=True)
            self._size_window(win, recenter=False)   # resize, keep position
            self._build(win)
            win.update_idletasks()
            if SettingsWindow._last_page in self._pages:
                self._show_page(SettingsWindow._last_page)
            self._install_dirty_traces()
            if was_dirty:
                self._mark_dirty()
        except Exception:
            logger.exception("In-place rebuild failed")

    def _snapshot_form_into_cfg(self) -> None:
        """Pull current form values into the in-memory self._cfg so a rebuild
        keeps them. NO disk write — unsaved edits stay unsaved until the user
        presses «Сохранить»."""
        try:
            self._apply_form_to_cfg(self._cfg)
        except Exception:
            logger.debug("form snapshot failed (partial form?)", exc_info=True)

    # ── Row-editor → config collectors (shared by _save and _snapshot) ──────────

    def _collect_replacements(self) -> list:
        from config import ReplacementConfig
        prev = {r.to: r for r in self._cfg.replacements}
        out = []
        for row in self._repl_editor.get_rows():
            to = row.get("to", "").strip()
            froms = [x.strip() for x in row.get("from", "").split(",") if x.strip()]
            if to and froms:
                old = prev.get(to)
                out.append(ReplacementConfig(
                    to=to, from_=froms,
                    sounds=(old.sounds if old else ""),
                    phonetic=(old.phonetic if old else False)))
        return out

    def _collect_voice_commands(self) -> list:
        from config import VoiceCommandConfig
        out = []
        for row in self._vc_editor.get_rows():
            phrase = row.get("phrase", "").strip()
            action = self._rev_label(self._ACTION_LABELS, row.get("action", ""), "insert")
            value = row.get("value", "").replace("\\n", "\n")
            if phrase and value:
                out.append(VoiceCommandConfig(phrase=phrase, action=action,
                                              value=value))
        return out

    # ── 5-stop scale sliders (заменяют поля ввода масштабов) ───────────────────

    def _preview_widget_live(self) -> None:
        """Push the current widget slider values (size + opacity) to the running
        pill so the change is visible immediately."""
        try: self._cfg.widget.scale = float(self._widget_scale_var.get())
        except (TypeError, ValueError): pass
        try: self._cfg.widget.opacity = float(self._widget_opacity_var.get())
        except (TypeError, ValueError): pass
        if self._on_widget_preview:
            try: self._on_widget_preview(self._cfg)
            except Exception: logger.debug("widget preview failed", exc_info=True)

    def _font_slider_apply(self, val: float) -> None:
        """Font scale rebuilds the whole window, so debounce it while dragging —
        apply once, shortly after the slider settles."""
        try:
            if getattr(self, "_font_apply_after", None):
                self._win.after_cancel(self._font_apply_after)
        except Exception: logger.debug("_font_slider_apply: suppressed", exc_info=True)
        self._font_apply_after = self._win.after(300,
                                                 lambda: self._set_font_scale(val))

    def _scale_slider(self, parent, label: str, var: tk.StringVar,
                      lo: float, hi: float, steps: int, on_change,
                      fmt: str = "{:.1f}") -> None:
        """A snap slider with `steps`+1 stops that replaces a numeric entry. Keeps
        `var` (the StringVar read by _save/_validate) in sync, shows the picked
        value, and calls on_change(value) live. The starting value is snapped to
        the nearest stop."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=_s(5))
        ctk.CTkLabel(row, text=label, width=_s(160), anchor="w").pack(side="left")
        step_sz = (hi - lo) / steps
        try: cur = float(var.get())
        except (TypeError, ValueError): cur = lo
        cur = max(lo, min(hi, cur))
        cur = round(round((cur - lo) / step_sz) * step_sz + lo, 2)   # snap to a stop
        var.set(f"{cur:g}")
        val_lbl = ctk.CTkLabel(row, text=fmt.format(cur), width=_s(48),
                               anchor="e", font=_f("Segoe UI", 13, "bold"))

        def _on(v):
            val = round(round((float(v) - lo) / step_sz) * step_sz + lo, 2)
            var.set(f"{val:g}")
            try: val_lbl.configure(text=fmt.format(val))
            except Exception: pass
            if on_change:
                try: on_change(val)
                except Exception: logger.debug("scale slider failed", exc_info=True)

        sl = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=steps,
                           command=_on, width=_s(190))
        sl.set(cur)
        sl.pack(side="left", padx=(0, _s(10)))
        val_lbl.pack(side="left")

    def _build_page_interface(self, sf) -> None:
        self._hdr(sf, "Интерфейс")
        # Theme — applies live so the result is visible immediately.
        th_row = ctk.CTkFrame(sf, fg_color="transparent")
        th_row.pack(fill="x", pady=3)
        ctk.CTkLabel(th_row, text="Тема:", width=_s(90), anchor="w").pack(side="left")
        self._theme_var = tk.StringVar(value=getattr(self._cfg.ui, "theme", "dark"))
        # Radio buttons instead of a segmented control: the segmented button's
        # fixed width clipped the labels («dark/light/system») at larger UI
        # scales. Radios space naturally and never overlap their text.
        for _tval in ("dark", "light", "system"):
            ctk.CTkRadioButton(
                th_row, text=self._THEME_LABELS[_tval], value=_tval,
                variable=self._theme_var, command=self._on_theme_change,
            ).pack(side="left", padx=(0, _s(12)))
        ctk.CTkLabel(sf, text="Тёмная / Светлая / Как в Windows. Применяется сразу.",
                     text_color=_HINT_FG, font=_f("Segoe UI", 9), anchor="w"
                     ).pack(fill="x", padx=2, pady=(0, _s(4)))

        self._font_scale_var = tk.StringVar(value=str(self._cfg.ui.font_scale))
        self._scale_slider(sf, "Масштаб шрифта:", self._font_scale_var,
                           1.0, 3.0, 4, self._font_slider_apply)
        ctk.CTkLabel(sf, text="Размер всего текста в окне настроек. Применяется сразу.",
                     text_color="#888", font=_f("Segoe UI", 9),
                     anchor="w", justify="left", wraplength=_s(560)
                     ).pack(fill="x", padx=2)

        # ── Кнопка на экране (pill) ───────────────────────────────────────────
        self._hdr(sf, "Кнопка на экране")

        self._widget_scale_var = tk.StringVar(value=str(self._cfg.widget.scale))
        self._scale_slider(sf, "Размер:", self._widget_scale_var,
                           0.5, 2.5, 4, lambda v: self._preview_widget_live())

        self._widget_opacity_var = tk.StringVar(value=str(self._cfg.widget.opacity))
        self._scale_slider(sf, "Прозрачность:", self._widget_opacity_var,
                           0.2, 1.0, 4, lambda v: self._preview_widget_live())

        self._widget_label_var = tk.BooleanVar(
            value=self._cfg.widget.show_listening_label)
        ctk.CTkCheckBox(sf, text="Показывать «Слушаю», пока идёт запись",
                        variable=self._widget_label_var).pack(anchor="w", pady=3)

        self._widget_glow_var = tk.BooleanVar(value=self._cfg.widget.show_glow)
        ctk.CTkCheckBox(sf, text="Подсветка вокруг виджета",
                        variable=self._widget_glow_var).pack(anchor="w", pady=3)

        ctk.CTkLabel(sf, text="Размер виджета не зависит от масштаба шрифта. "
                              "Двигай ползунок — применяется сразу (живой предпросмотр).",
                     text_color="#666", font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(560)
                     ).pack(fill="x", padx=2)

        # ── Приложение ────────────────────────────────────────────────────────
        sf = self._page("Интерфейс")
        self._hdr(sf, "Приложение")
        self._autostart_var = tk.BooleanVar(value=_autostart_get())
        ctk.CTkCheckBox(sf, text="Запускать при старте Windows",
                        variable=self._autostart_var).pack(anchor="w", pady=4)
        # Save button now lives in the always-visible bottom bar (see _build).

    def _build_page_audio(self, sf) -> None:
        self._hdr(sf, "Аудио")
        mic_row = ctk.CTkFrame(sf, fg_color="transparent")
        mic_row.pack(fill="x", pady=3)
        ctk.CTkLabel(mic_row, text="Микрофон:", width=_s(110), anchor="w").pack(side="left")
        self._mic_devices = _get_mic_devices()
        mic_names = [name for _, name in self._mic_devices]
        current_idx = self._cfg.audio.mic_index
        current_name = next(
            (name for idx, name in self._mic_devices if idx == current_idx),
            mic_names[0],
        )
        self._mic_var = tk.StringVar(value=current_name)
        # Device names get long — let the combo fill the row so it never spills
        # past the window on a narrow/low-DPI screen.
        ctk.CTkComboBox(mic_row, variable=self._mic_var, values=mic_names,
                        command=self._on_mic_device_change
                        ).pack(side="left", fill="x", expand=True)

        # Live mic-level meter — talk and the bar should jump, so you can verify
        # the chosen mic actually works without leaving Settings. Active only
        # while this tab is on-screen (it briefly pauses «Hey Jarvis» to free
        # the mic — two streams on one device fight and one reads silence).
        meter_row = ctk.CTkFrame(sf, fg_color="transparent")
        meter_row.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(meter_row, text="Уровень:", width=_s(110),
                     anchor="w").pack(side="left")
        self._mic_meter_bar = ctk.CTkProgressBar(meter_row, height=_s(14),
                                                 corner_radius=_s(7))
        self._mic_meter_bar.pack(side="left", fill="x", expand=True)
        self._mic_meter_bar.set(0.0)
        try:
            self._mic_meter_bar.configure(progress_color="#00d4aa")  # teal, как пилюля
        except Exception:
            logger.debug("_build_page_audio: suppressed", exc_info=True)
        ctk.CTkLabel(
            sf, text="Скажи что-нибудь — полоска должна прыгать. Пока открыта "
                     "эта вкладка, распознавание по «Hey Jarvis» на паузе.",
            text_color="#666", font=_f("Segoe UI", 9), anchor="w",
            justify="left", wraplength=_s(480)
        ).pack(fill="x", padx=(_s(112), 0), pady=(0, _s(4)))

        src_row = ctk.CTkFrame(sf, fg_color="transparent")
        src_row.pack(fill="x", pady=3)
        ctk.CTkLabel(src_row, text="Источник:", width=_s(110), anchor="w").pack(side="left")
        self._source_var = tk.StringVar(
            value=self._SOURCE_LABELS.get(self._cfg.audio.source, "Микрофон"))
        ctk.CTkComboBox(src_row, variable=self._source_var,
                        values=list(self._SOURCE_LABELS.values()),
                        width=_s(180)).pack(side="left")
        ctk.CTkLabel(sf,
                     text="«Микрофон» — обычная запись голоса. «Звук с компьютера» — "
                          "записать то, что слышно в колонках (например, собеседника "
                          "в онлайн-звонке).",
                     text_color="#666", font=_f("Segoe UI", 9), anchor="w",
                     wraplength=_s(540), justify="left"
                     ).pack(fill="x", pady=(_s(2), 0))

        self._norm_var = tk.BooleanVar(value=self._cfg.audio.normalize)
        ctk.CTkCheckBox(sf, text="Выравнивать громкость записи (рекомендуется)",
                        variable=self._norm_var).pack(anchor="w", pady=3)
        self._nr_var = tk.BooleanVar(value=self._cfg.audio.noise_reduction)
        ctk.CTkCheckBox(sf, text="Шумоподавление (чуть медленнее)",
                        variable=self._nr_var).pack(anchor="w", pady=3)

        # Audio ducker
        self._duck_var = tk.BooleanVar(value=self._cfg.audio.duck_other_apps)
        try:
            from audio_ducker import AudioDucker
            duck_ok = AudioDucker().is_available()
        except Exception:
            duck_ok = False
        duck_text = ("Приглушать другие приложения во время записи" if duck_ok
                     else "Приглушать другие приложения  (недоступно на этом ПК)")
        ctk.CTkCheckBox(sf, text=duck_text,
                        variable=self._duck_var).pack(anchor="w", pady=3)

        self._duck_level_var = tk.StringVar(value=str(self._cfg.audio.duck_level))
        self._scale_slider(sf, "Громкость фона:", self._duck_level_var,
                           0.0, 1.0, 5, None, fmt="{:.1f}")
        ctk.CTkLabel(sf, text="Насколько глушить другие приложения: 0 — полная "
                              "тишина, 1 — без изменений.",
                     text_color="#666", font=_f("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=_s(540)).pack(fill="x", padx=_s(2))

    # ── Live mic-test meter ─────────────────────────────────────────────────────
    def _selected_mic_index(self) -> int:
        name = self._mic_var.get()
        for idx, nm in getattr(self, "_mic_devices", []):
            if nm == name:
                return idx
        return -1

    def _on_mic_device_change(self, _name=None) -> None:
        # Re-point the live probe at the newly chosen device (only if running).
        if self._mic_meter_on and self._mic_monitor is not None:
            try:
                self._mic_monitor.start(self._selected_mic_index())
            except Exception:
                logger.debug("mic monitor re-point failed", exc_info=True)

    def _mic_monitor_start(self) -> None:
        if self._mic_monitor is None or self._mic_meter_on:
            return
        try:
            self._mic_monitor.start(self._selected_mic_index())
        except Exception:
            logger.debug("mic monitor start failed", exc_info=True)
            return
        self._mic_meter_on = True
        self._mic_meter_tick()

    def _mic_monitor_stop(self) -> None:
        if not self._mic_meter_on:
            return
        self._mic_meter_on = False
        if self._mic_monitor is not None:
            try: self._mic_monitor.stop()
            except Exception: logger.debug("mic monitor stop failed", exc_info=True)
        bar = getattr(self, "_mic_meter_bar", None)
        if bar is not None:
            try: bar.set(0.0)
            except Exception: logger.debug("_mic_monitor_stop: suppressed", exc_info=True)

    def _mic_meter_tick(self) -> None:
        if not self._mic_meter_on:
            return
        bar = getattr(self, "_mic_meter_bar", None)
        if bar is not None and self._mic_monitor is not None:
            try:
                rms = float(self._mic_monitor.current_rms)
                # Perceptual (sqrt) mapping like the pill waveform — quiet and
                # loud speech both register; clamp to the bar's 0..1 range.
                lvl = max(0.0, min(1.0, math.sqrt(max(0.0, rms)) * 3.2))
                bar.set(lvl)
            except Exception:
                pass
        try:
            self._win.after(50, self._mic_meter_tick)
        except Exception:
            self._mic_meter_on = False

    def _update_mic_meter(self) -> None:
        """Run the meter ONLY while the Audio tab is the visible, revealed page —
        so wake is paused just for the test, not the whole settings session."""
        ok = False
        try:
            ok = bool(self._win and self._win.winfo_exists()
                      and getattr(self, "_revealed", False)
                      and self._active_page == "Аудио")
        except Exception:
            ok = False
        if ok:
            self._mic_monitor_start()
        else:
            self._mic_monitor_stop()

    def _validate(self) -> list:
        """Return human-readable problems with numeric fields (empty = OK)."""
        errs: list = []

        def _num(var, label, lo, hi, integer=False):
            raw = var.get().strip()
            try:
                v = int(raw) if integer else float(raw)
            except ValueError:
                errs.append(f"{label}: «{raw}» — не число")
                return
            if not (lo <= v <= hi):
                errs.append(f"{label}: {v} вне диапазона {lo}–{hi}")

        if not self._key_var.get().strip():
            errs.append("Горячая клавиша не задана")
        _num(self._silence_var, "Тишина (сек)", 0.1, 30.0)
        _num(self._duck_level_var, "Уровень приглушения", 0.0, 1.0)
        _num(self._hist_max_var, "Максимум записей", 0, 1_000_000, integer=True)
        _num(self._hist_days_var, "Хранить (дней)", 0, 100_000, integer=True)
        _num(self._font_scale_var, "Масштаб шрифта", 0.5, 4.0)
        _num(self._widget_scale_var, "Размер виджета", 0.3, 3.0)
        _num(self._widget_opacity_var, "Прозрачность виджета", 0.1, 1.0)
        return errs

    def _safe_destroy(self) -> None:
        # Hide by parking OFF-SCREEN (stay mapped → canvas keeps its size → no
        # ghosting on the next open; transient → no taskbar button). Keep the
        # built window in memory so the next open is instant. Release modal grab.
        if getattr(self, "_hotkey_capturing", False):
            self._end_hotkey_capture(cancelled=True)
        discard = False
        if getattr(self, "_dirty", False) and self._revealed:
            import tkinter.messagebox as mb
            try:
                if mb.askyesno("Talker — Настройки",
                               "Есть несохранённые изменения. Сохранить?",
                               parent=self._win):
                    self._save()      # сам закроет окно после тоста
                    return
                discard = True
            except Exception:
                logger.debug("unsaved-changes prompt failed", exc_info=True)
        self._revealed = False
        self._mic_monitor_stop()      # close probe + resume wake (mic freed)
        try: self._win.grab_release()
        except Exception: logger.debug("grab release failed", exc_info=True)
        try: self._onscreen_geom = self._win.geometry()   # remember position/size
        except Exception: logger.debug("geometry save failed", exc_info=True)
        self._park_offscreen()
        if discard:
            # «Нет» — отбросить правки: перечитать конфиг и перестроить форму
            # начисто (уже спрятанной), чтобы при следующем открытии не висели
            # фантомные несохранённые значения.
            self._mark_clean()
            self._cfg = load_config()
            try:
                self._win.after(120, self._rebuild_in_place)
            except Exception:
                logger.debug("discard rebuild schedule failed", exc_info=True)

    def _apply_form_to_cfg(self, cfg: Config) -> None:
        """Write every form-owned field into `cfg`; everything the form does
        NOT own (pill position, vocabulary blacklist, snippets, whisper_mode,
        api, wake.enabled, …) stays untouched. Callers pick the base: _save
        applies onto a FRESH load_config() (merge — other writers' changes
        survive), the font-scale rebuild applies onto the in-memory snapshot."""
        cfg.hotkey.key = self._key_var.get().strip()
        cfg.hotkey.mode = self._mode_var.get()
        cfg.stt.language = self._lang_value()
        # GigaAM is RU-only — force language (dropdown is disabled for it).
        engine_val = self._engine_value()
        if engine_val in self._FIXED_LANGUAGE:
            cfg.stt.language = self._FIXED_LANGUAGE[engine_val]
        cfg.stt.device = self._device_value()
        if engine_val in ("whisper", "gigaam"):
            cfg.stt.engine = engine_val
        # Model → correct per-engine slot.
        m = self._FIXED_MODEL.get(engine_val, "")
        if engine_val == "gigaam":
            cfg.stt.gigaam_model = m or cfg.stt.gigaam_model
        elif m:
            cfg.stt.model = m

        cfg.output.restore_clipboard = self._restore_var.get()
        cfg.output.copy_to_clipboard = self._copy_clip_var.get()
        cfg.output.show_bubble = self._bubble_var.get()
        cfg.output.smart_format = self._smart_format_var.get()
        cfg.output.voice_commands = self._vc_enabled_var.get()
        cfg.output.injection_mode = self._rev_label(
            self._INJ_LABELS, self._inj_var.get(), "auto")
        cfg.output.number_format = self._numfmt_var.get()
        cfg.output.mask_profanity = self._mask_prof_var.get()
        # LLM cleanup (embedded gemma + cloud api/ollama) removed. GigaAM v3
        # punctuates and the script filler-stripper handles «э-э / ну».
        cfg.output.remove_fillers = self._remove_fillers_var.get()
        cfg.cleaners = [CleanerConfig(type="noop")]
        if hasattr(self, "_voice_gate_var"):
            cfg.output.voice_gate = self._voice_gate_var.get()
        if hasattr(self, "_wake_sens_var"):
            cfg.wake.threshold = self._level_value(
                self._wake_sens_var.get(), self._WAKE_LEVELS, 0.55)
            cfg.wake.stop_fuzzy = self._level_value(
                self._stop_sens_var.get(), self._STOP_LEVELS, 0.82)

        # Row editors. (Snippets kept as-loaded: the editor was removed.)
        cfg.replacements = self._collect_replacements()
        cfg.voice_commands = self._collect_voice_commands()
        if getattr(self, "_vocab_editor", None) is not None:
            words: list[str] = []
            seen: set[str] = set()
            for row in self._vocab_editor.get_rows():
                w = row.get("word", "").strip()
                if w and w.lower() not in seen:
                    seen.add(w.lower())
                    words.append(w)
            cfg.vocabulary.words = words

        cfg.audio.normalize = self._norm_var.get()
        cfg.audio.noise_reduction = self._nr_var.get()
        # Resolve selected mic name → index
        mic_name = self._mic_var.get()
        cfg.audio.mic_index = next(
            (idx for idx, name in self._mic_devices if name == mic_name), -1
        )
        cfg.audio.source = self._rev_label(
            self._SOURCE_LABELS, self._source_var.get(), "mic")
        cfg.audio.duck_other_apps = self._duck_var.get()
        try:
            cfg.audio.duck_level = max(0.0, min(1.0,
                                                float(self._duck_level_var.get())))
        except ValueError:
            pass
        try:
            cfg.continuous.silence_secs = float(self._silence_var.get())
        except ValueError:
            pass
        try:
            cfg.continuous.vad_aggressiveness = int(self._vad_var.get())
        except ValueError:
            pass
        vad_engine = self._vad_engine_var.get()
        if vad_engine in ("auto", "ten", "webrtc"):
            cfg.continuous.vad_engine = vad_engine

        # History
        try:
            cfg.history.max_entries = max(0, int(self._hist_max_var.get()))
        except ValueError:
            pass
        try:
            cfg.history.retention_days = max(0, int(self._hist_days_var.get()))
        except ValueError:
            pass
        cfg.history.on_quit_clear = self._hist_clear_var.get()

        # Theme. (font_scale намеренно НЕ здесь: слайдер применяет и сохраняет
        # его сам, живьём; History A−/A+ пишет его со своей стороны — затирать
        # их значение устаревшим из формы нельзя.)
        theme = self._theme_var.get()
        if theme in ("dark", "light", "system"):
            cfg.ui.theme = theme

        # Sounds — the panel edits self._cfg.sounds in place (persisting each
        # change itself); carry the live object so nothing is lost on merge.
        cfg.sounds = self._cfg.sounds

        # Widget appearance
        try:
            cfg.widget.scale = max(0.3, min(3.0,
                                            float(self._widget_scale_var.get())))
        except ValueError:
            pass
        try:
            cfg.widget.opacity = max(0.1, min(1.0,
                                              float(self._widget_opacity_var.get())))
        except ValueError:
            pass
        cfg.widget.show_listening_label = self._widget_label_var.get()
        cfg.widget.show_glow = self._widget_glow_var.get()

    def _save(self) -> None:
        # Validate numeric fields up front — abort with a clear message instead
        # of silently clamping/ignoring bad input.
        errs = self._validate()
        if errs:
            extra = f"  (+ ещё {len(errs) - 1})" if len(errs) > 1 else ""
            self._toast("⚠ " + errs[0] + extra, ok=False)
            return

        # MERGE, не overwrite: окно строится при prebuild и живёт скрытым
        # часами, а конфиг тем временем пишут другие — позиция пилюли,
        # автовыученный словарь, тогглы из трея/пилюли. Применяем поля формы
        # на СВЕЖИЙ конфиг с диска — чужие изменения переживают «Сохранить».
        base = load_config()
        self._apply_form_to_cfg(base)
        self._cfg = base

        _autostart_set(self._autostart_var.get())
        save_config(base)
        try:
            self._on_save(base)
        except Exception:
            logger.exception("on_save callback failed")
            self._toast("⚠ Сохранено, но применить не удалось — см. лог", ok=False)
            return
        self._mark_clean()
        # Brief success feedback, then close.
        self._toast("✓ Сохранено и применено", ok=True)
        self._win.after(800, self._safe_destroy)
