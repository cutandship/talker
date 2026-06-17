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

class HistoryWindow:
    _open_win: ctk.CTkToplevel | None = None
    _instance: "HistoryWindow | None" = None

    @classmethod
    def open(cls, root: tk.Tk, history: HistoryManager,
             on_settings: Callable,
             on_transcribe_file: "Callable | None" = None) -> None:
        inst = cls._instance
        if inst is not None and inst._win is not None and inst._win.winfo_exists():
            # Reuse the already-built window — instant (re)open, like Explorer,
            # instead of rebuilding ~1100 widgets (~0.9 s) every time.
            inst._prebuilding = False
            inst._reveal()
            try: inst._win.focus_force()
            except Exception: logger.debug("open: suppressed", exc_info=True)
            return
        inst = cls(root, history, on_settings, on_transcribe_file)
        cls._open_win = inst._win
        cls._instance = inst

    @classmethod
    def prebuild(cls, root: tk.Tk, history: HistoryManager,
                 on_settings: Callable,
                 on_transcribe_file: "Callable | None" = None) -> None:
        """Build the (heavy ~0.9 s) window now but keep it HIDDEN, so the first
        real open() is instant. Called shortly after launch while idle."""
        inst = cls._instance
        if inst is not None and inst._win is not None and inst._win.winfo_exists():
            return
        inst = cls(root, history, on_settings, on_transcribe_file)
        # Set before the queued after(45, _reveal) fires → it no-ops, stays hidden.
        inst._prebuilding = True
        cls._open_win = inst._win
        cls._instance = inst

    def __init__(self, root: tk.Tk, history: HistoryManager,
                 on_settings: Callable,
                 on_transcribe_file: "Callable | None" = None) -> None:
        self._history = history
        self._on_settings = on_settings
        self._on_transcribe_file = on_transcribe_file
        self._entry_frames: list[ctk.CTkFrame] = []
        self._pending_entries: list = []     # queue for chunked rendering
        self._search_after_id: str | None = None

        win = ctk.CTkToplevel(root)
        self._win = win
        win.withdraw()                        # hide during CTk's titlebar dance
        # No taskbar button (user: «убери вкладку на панели задач»). History is a
        # tray/tap-opened utility window — the startup prebuild used to leak a
        # «Talker — История» button onto the taskbar. _hide_from_taskbar (below,
        # while still withdrawn) makes it a tool window so the button never shows.
        win.title("Talker — История")
        win.resizable(True, True)
        self._size_window(win)
        win.protocol("WM_DELETE_WINDOW", self._on_close)
        win.bind("<Escape>", lambda _e: self._on_close())

        self._build(win)
        win.update_idletasks()
        # Make it a tool window WHILE STILL WITHDRAWN → no taskbar button ever,
        # incl. during the off-screen prebuild deiconify below. Re-apply once
        # after CTk's title-bar dance (it withdraws/deiconifies ~200 ms in).
        _hide_from_taskbar(win)
        win.after(400, lambda: _hide_from_taskbar(win))
        self._onscreen_geom = win.geometry()
        # Park VISIBLE but OFF-SCREEN so the scrollable list's canvas gets real
        # dimensions (built-while-withdrawn → unsized canvas → smears on scroll).
        # No -alpha (no layering), pre-rendered → reveal is an instant on-screen
        # move with no white flash.
        self._park_offscreen()
        win.after(10, self._populate)
        win.after(45, self._reveal)
        history.on_new(self._on_new_entry)

    def _park_offscreen(self) -> None:
        """Map the (already-built) window below the screen — rendered but unseen."""
        try:
            w = self._win
            # Transient while parked/rendering → NO taskbar button, so the brief
            # «Talker — История» button doesn't flash during prebuild. Detached
            # on real open (_reveal) so an opened window still gets its button.
            try: w.transient(w.master)
            except Exception: logger.debug("_park_offscreen: suppressed", exc_info=True)
            w.geometry(f"+{w.winfo_x()}+{w.winfo_screenheight() + 400}")
            w.deiconify()
            w.update_idletasks()
        except Exception:
            logger.debug("history park offscreen failed", exc_info=True)

    def _reveal(self) -> None:
        """Slide the (pre-rendered, off-screen) window on-screen — instant, no
        flash. No-op while prebuilding (stays parked off-screen until opened)."""
        if getattr(self, "_prebuilding", False):
            # Fully hide (no taskbar button) until really opened. The canvas
            # already got its real size from the off-screen render in __init__,
            # so a later deiconify won't ghost/smear.
            try: self._win.withdraw()
            except Exception: logger.debug("_reveal: suppressed", exc_info=True)
            return
        if not (self._win and self._win.winfo_exists()):
            return
        try:
            # Stay transient (set in _park_offscreen) → NO taskbar button at all
            # (user: «убери вкладку на панели задач»). Opens as a normal centered
            # window owned by root.
            self._win.deiconify()
            self._win.geometry(self._onscreen_geom)
            self._win.lift()
            # After deiconify the scrollable canvas needs a layout pass to regain
            # its real size; without it a re-populate renders into an unsized
            # canvas and the first row smears under the header.
            self._win.update_idletasks()
            # Pull in any dictations that arrived while we were hidden.
            if getattr(self, "_dirty", False):
                self._dirty = False
                self._win.after(0, self._populate)
            # Keep the newest (top) row visible — not scrolled under the header.
            self._win.after(0, self._scroll_to_top)
        except Exception:
            logger.debug("_reveal: suppressed", exc_info=True)

    def _scroll_to_top(self) -> None:
        """Reset the scrollable list to the top (newest entry)."""
        try:
            self._sf._parent_canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _on_close(self) -> None:
        # Withdraw (not destroy): removes the taskbar button and hides the
        # window, but keeps it built for an instant reopen. The canvas already
        # has its size from the initial off-screen render, so re-deiconify won't
        # ghost. on_new stays registered so the list keeps updating in the bg.
        try: self._onscreen_geom = self._win.geometry()
        except Exception: logger.debug("_on_close: suppressed", exc_info=True)
        try: self._win.withdraw()
        except Exception: logger.debug("_on_close: suppressed", exc_info=True)

    def _size_window(self, win, recenter: bool = True) -> None:
        scr_w = win.winfo_screenwidth()
        scr_h = win.winfo_screenheight()
        try: wsc = ctk.ScalingTracker.get_window_scaling(win)
        except Exception: wsc = 1.0
        log_w, log_h = int(scr_w / wsc), int(scr_h / wsc)
        gentle = 1.0 + (_UiScale.value - 1.0) * 0.5
        tw = min(int(log_w * 0.80), int(460 * gentle))
        th = min(int(log_h * 0.82), int(520 * gentle))
        win.minsize(min(_s(360), tw), min(_s(360), th))
        win.maxsize(log_w, log_h)
        if recenter:
            x = max(0, (log_w - tw) // 2)
            y = max(0, (log_h - th) // 2)
            win.geometry(f"{tw}x{th}+{x}+{y}")
        else:
            win.geometry(f"{tw}x{th}")

    def _bump_scale(self, delta: float) -> None:
        """Change the UI scale and re-render the window content *in place* —
        no close/reopen, so the window never flickers or 'disappears'."""
        new_scale = round(_UiScale.value + delta, 2)
        _UiScale.set(new_scale)
        try:
            cfg = load_config()
            cfg.ui.font_scale = new_scale
            save_config(cfg)
        except Exception:
            logger.exception("Could not persist font_scale")
        win = self._win
        try:
            self._entry_frames = []
            self._pending_entries = []
            for child in list(win.winfo_children()):
                try: child.destroy()
                except Exception: logger.debug("_bump_scale: suppressed", exc_info=True)
            self._size_window(win, recenter=False)
            self._build(win)
            win.after(10, self._populate)
        except Exception:
            logger.exception("History in-place rebuild failed")

    def _build(self, win: ctk.CTkToplevel) -> None:
        # ── Header ────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color=_CARD_BG, corner_radius=0, height=_s(60))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="История",
                     font=_f("Segoe UI", 15, "bold")).pack(side="left",
                                                           padx=(_s(14), _s(6)), pady=_s(8))
        # Entry count moved off the (now removed) bottom bar into the header.
        self._count_lbl = ctk.CTkLabel(hdr, text="", text_color=_HINT_FG,
                                       font=_f("Segoe UI", 10))
        self._count_lbl.pack(side="left", pady=_s(8))

        # Compact icon buttons so the title + all controls fit a narrow window.
        btn_cfg = dict(width=_s(38), height=_s(40), fg_color="transparent",
                       hover_color=("#d0d0d0", "#333333"), corner_radius=8,
                       font=_f("Segoe UI", 14, "bold"))
        ctk.CTkButton(hdr, text="⚙", **btn_cfg,
                      command=self._on_settings).pack(side="right", padx=_s(4), pady=_s(8))
        # 📁 Транскрибировать аудио/видео-файл → результат падает в эту Историю.
        if self._on_transcribe_file:
            ctk.CTkButton(hdr, text="📁", **btn_cfg,
                          command=self._on_transcribe_file).pack(side="right", padx=_s(2), pady=_s(8))
        ctk.CTkButton(hdr, text="↗", **btn_cfg,
                      command=self._export).pack(side="right", padx=_s(2), pady=_s(8))
        ctk.CTkButton(hdr, text="🗑", **btn_cfg,
                      command=self._clear).pack(side="right", padx=_s(2), pady=_s(8))
        # Font scale: quick A− / A+ (slightly bigger to match Settings header)
        ctk.CTkButton(hdr, text="A+", **btn_cfg,
                      command=lambda: self._bump_scale(+0.2)).pack(side="right", padx=_s(2), pady=_s(8))
        ctk.CTkButton(hdr, text="A−", **btn_cfg,
                      command=lambda: self._bump_scale(-0.2)).pack(side="right", padx=_s(2), pady=_s(8))
        # Copy ALL dictations to clipboard (moved here from the removed search row).
        ctk.CTkButton(hdr, text="⎘", **btn_cfg,
                      command=self._copy_all).pack(side="right", padx=_s(2), pady=_s(8))

        ctk.CTkFrame(win, fg_color=_SEP_FG, height=1).pack(fill="x")

        # Поиск — отдельной компактной строкой ПОД хедером: внутри хедера ему
        # тесно (заголовок + 7 кнопок), на крупном масштабе он выдавливал
        # кнопки за край окна. _populate уже умеет фильтровать и подсвечивать
        # по _search_var; Esc в поле — очистить.
        srow = ctk.CTkFrame(win, fg_color=_CARD_BG, corner_radius=0)
        srow.pack(fill="x")
        self._search_var = tk.StringVar()
        search = ctk.CTkEntry(srow, textvariable=self._search_var,
                              placeholder_text="🔍 Поиск по записям…",
                              height=_s(24), font=_f("Segoe UI", 10))
        search.pack(fill="x", padx=_s(10), pady=(_s(2), _s(6)))
        search.bind("<KeyRelease>", lambda _e: self._schedule_populate())
        search.bind("<Escape>", self._clear_search)
        _enable_entry_clipboard(search)
        ctk.CTkFrame(win, fg_color=_SEP_FG, height=1).pack(fill="x")

        # ── Scrollable entry list ──────────────────────────────────────────────
        # Цвета — парами (light, dark): окно обязано жить в обеих темах.
        self._sf = ctk.CTkScrollableFrame(win, fg_color=("#f3f3f3", "#0e0e0e"))
        self._sf.pack(fill="both", expand=True, padx=0, pady=0)
        # (No bottom bar — «Копировать всё» is in the search row, count in header.)

    _CHUNK_SIZE = 25         # entries built per idle tick
    _SEARCH_DEBOUNCE_MS = 180

    def _clear_search(self, _e=None):
        """Esc в поле поиска: очистить фильтр (и не дать Esc закрыть окно)."""
        if self._search_var.get():
            self._search_var.set("")
            self._populate()
            return "break"
        return None

    def _schedule_populate(self) -> None:
        """Debounce: wait for the user to stop typing before re-rendering."""
        if self._search_after_id:
            try: self._win.after_cancel(self._search_after_id)
            except Exception: pass
        self._search_after_id = self._win.after(self._SEARCH_DEBOUNCE_MS, self._populate)

    def _populate(self) -> None:
        self._search_after_id = None
        for w in self._sf.winfo_children():
            w.destroy()
        self._entry_frames.clear()

        query = getattr(self, "_search_var", None)
        q = query.get().strip().lower() if query else ""

        entries = self._history.entries()
        if q:
            entries = [e for e in entries if q in e["text"].lower()]

        if not entries:
            msg = ("Ничего не найдено." if q else
                   "Пока нет записей.\nЗажмите горячую клавишу и скажите что-нибудь.")
            ctk.CTkLabel(self._sf, text=msg, text_color=("#888888", "#555555"),
                         font=_f("Segoe UI", 12), justify="center").pack(expand=True, pady=60)
            self._pending_entries = []
        else:
            # Queue entries newest-first; render in chunks so the window stays
            # responsive even with thousands of entries.
            self._pending_entries = list(reversed(entries))
            self._render_state = {"prev_day": None, "highlight": q}
            self._win.after_idle(self._render_chunk)

        self._update_count()

    def _render_chunk(self) -> None:
        if not self._pending_entries or not self._win.winfo_exists():
            return
        batch = self._pending_entries[:self._CHUNK_SIZE]
        self._pending_entries = self._pending_entries[self._CHUNK_SIZE:]

        prev_day = self._render_state["prev_day"]
        highlight = self._render_state["highlight"]
        # Disable GC while building CTk widgets: creating/destroying CTk frames
        # releases comtypes COM objects in __del__, and a GC firing mid-build can
        # run that Release on a bad thread → native access violation (0xc0000005).
        import gc as _gc
        _gc_was = _gc.isenabled()
        _gc.disable()
        try:
            for entry in batch:
                try:
                    dt = datetime.fromisoformat(entry["timestamp"])
                    day = dt.strftime("%d %B %Y")
                    time_str = dt.strftime("%H:%M")
                except Exception:
                    day = "Сегодня"
                    time_str = "--:--"

                if day != prev_day:
                    self._add_day_separator(day)
                    prev_day = day

                self._add_entry_row(entry, time_str, highlight=highlight)
        finally:
            if _gc_was:
                _gc.enable()

        self._render_state["prev_day"] = prev_day
        if self._pending_entries:
            self._win.after(12, self._render_chunk)

    def _add_day_separator(self, day: str, before=None):
        row = ctk.CTkFrame(self._sf, fg_color="transparent")
        opts = dict(fill="x", padx=16, pady=(12, 4))
        if before is not None:
            opts["before"] = before
        row.pack(**opts)
        ctk.CTkLabel(row, text=day, font=_f("Segoe UI", 10, "bold"),
                     text_color=("#8a8a8a", "#444444")).pack(side="left")
        return row

    def _add_entry_row(self, entry: HistoryEntry, time_str: str, highlight: str = "",
                       before=None):
        row = ctk.CTkFrame(self._sf, fg_color=("#ffffff", "#1c1c1c"),
                           corner_radius=8)
        opts = dict(fill="x", padx=12, pady=3)
        if before is not None:
            opts["before"] = before
        row.pack(**opts)
        self._entry_frames.append(row)

        # Action buttons sit in a right-side gutter, stacked one above the other,
        # bigger and with a solid fill + border so they read clearly.
        btn_col = ctk.CTkFrame(row, fg_color="transparent")
        btn_col.pack(side="right", padx=(_s(4), _s(8)), pady=_s(8))

        content = ctk.CTkFrame(row, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True)

        top = ctk.CTkFrame(content, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(top, text=time_str, font=_f("Segoe UI", 9),
                     text_color=("#999999", "#666666"), width=36,
                     anchor="w").pack(side="left")

        hit = highlight and highlight in entry["text"].lower()
        text_color = (("#9a6b00", "#ffc844") if hit
                      else ("#1a1a1a", "#f0f0f0"))
        text_lbl = ctk.CTkLabel(
            content, text=entry["text"],
            font=_f("Segoe UI", 11), text_color=text_color,
            justify="left", anchor="w", wraplength=390,
        )
        text_lbl.pack(fill="x", padx=10, pady=(0, 8))

        # Bigger, scale-aware, distinct (solid bg + border) — was tiny 26×22.
        _BW, _BH = _s(38), _s(28)
        _BFONT = _f("Segoe UI", 14, "bold")
        _BFG, _BHOV = ("#ececec", "#343434"), ("#dcdcdc", "#484848")
        _BBORD = ("#c2c2c2", "#5a5a5a")
        _BTXT = ("#1f1f1f", "#ededed")
        copy_btn = ctk.CTkButton(
            btn_col, text="⎘", width=_BW, height=_BH, font=_BFONT,
            fg_color=_BFG, hover_color=_BHOV, text_color=_BTXT,
            border_width=max(1, _s(1)), border_color=_BBORD, corner_radius=_s(6),
            command=lambda: self._copy_one(text_lbl.cget("text")),
        )
        copy_btn.pack(side="top")
        return row

    def _on_new_entry(self, entry: HistoryEntry | None) -> None:
        """Called from HistoryManager on new dictation or clear. Re-render ONLY
        when the window is actually on-screen — the window stays mapped off-
        screen when "closed", so blindly repopulating on every dictation rebuilt
        the whole list (lots of CTk widgets) in the background and crashed via a
        comtypes COM __del__ race. When hidden we just mark the list dirty and
        refresh on next reveal. For the common case (new dictation, no filter,
        no trim) we cheaply prepend ONE row instead of rebuilding everything."""
        if not (self._win and self._win.winfo_exists()):
            return
        try:
            visible = bool(self._win.winfo_viewable()) and not getattr(self, "_prebuilding", False)
        except Exception:
            visible = False
        if not visible:
            self._dirty = True
            return
        if self._can_increment(entry):
            self._win.after(0, lambda e=entry: self._prepend_entry(e))
        else:
            self._win.after(0, self._populate)

    def _can_increment(self, entry: HistoryEntry | None) -> bool:
        """True only when the newest row can be SAFELY prepended without a full
        rebuild: a real new entry, no active search filter, no chunked render in
        flight, an already-rendered list, and no retention trim (which would
        need a row removed from the bottom)."""
        if entry is None:                          # clear signal → full rebuild
            return False
        query = getattr(self, "_search_var", None)
        if query and query.get().strip():          # active filter → full rebuild
            return False
        if self._pending_entries:                  # chunked render still running
            return False
        if not self._entry_frames:                 # empty list → let _populate seed it
            return False
        try:
            if len(self._history.entries()) >= self._history.max_entries:
                return False                       # retention may have trimmed bottom
        except Exception:
            return False
        return True

    def _prepend_entry(self, entry: HistoryEntry) -> None:
        """Insert the newest entry as one row at the top, adding a day separator
        if its day differs from the current top entry's day. Mirrors
        _render_chunk's GC guard; falls back to _populate on any anomaly."""
        if not (self._win and self._win.winfo_exists()):
            return
        children = self._sf.winfo_children()
        if not children:
            self._populate()
            return
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
            day = dt.strftime("%d %B %Y")
            time_str = dt.strftime("%H:%M")
        except Exception:
            day, time_str = "Сегодня", "--:--"
        # Day of the previous top entry (now second-newest in the data).
        prev_day = None
        try:
            ents = self._history.entries()
            if len(ents) >= 2:
                prev_day = datetime.fromisoformat(ents[-2]["timestamp"]).strftime("%d %B %Y")
        except Exception:
            prev_day = None
        import gc as _gc
        ok = True
        was = _gc.isenabled()
        _gc.disable()
        try:
            anchor = children[0]                   # current top separator
            if day != prev_day:
                # New day → fresh separator + row above everything.
                self._add_day_separator(day, before=anchor)
                self._add_entry_row(entry, time_str, before=anchor)
            else:
                # Same day → row right under the existing top separator.
                before = children[1] if len(children) >= 2 else None
                self._add_entry_row(entry, time_str, before=before)
        except Exception:
            logger.debug("History incremental prepend failed", exc_info=True)
            ok = False
        finally:
            if was:
                _gc.enable()
        if ok:
            self._update_count()
        else:
            self._populate()

    def _toggle_format(self, entry: HistoryEntry, lbl) -> None:
        """Switch this history entry between raw and voice-formatted text.
        Stores raw on the entry the first time so the toggle is reversible.
        Re-saves via HistoryManager and updates the on-screen label."""
        try:
            from text_format import apply_formatting
        except Exception:
            return
        cur = entry.get("text", "")
        raw = entry.get("raw", cur)        # старые записи: raw == text
        formatted = apply_formatting(raw)
        # Если сейчас показан формат (есть переносы и он == formatted) → к raw.
        if cur == formatted and formatted != raw:
            new = raw
        else:
            new = formatted
        if new == cur:
            return                          # нечего форматировать (нет команд)
        # сохранить raw на запись (для обратимости) и обновить text
        entry["raw"] = raw
        entry["text"] = new
        try:
            self._history.update_text(entry["timestamp"], new)
        except Exception:
            logger.debug("_toggle_format: suppressed", exc_info=True)
        try:
            lbl.configure(text=new)
        except Exception:
            logger.debug("_toggle_format: suppressed", exc_info=True)

    def _copy_one(self, text: str) -> None:
        pyperclip.copy(text)

    def _copy_all(self) -> None:
        pyperclip.copy(self._history.export_text())

    def _clear(self) -> None:
        self._history.clear()

    def _export(self) -> None:
        import tkinter.filedialog as fd
        import exporters

        path = fd.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[
                ("Text",    "*.txt"),
                ("SubRip",  "*.srt"),
                ("WebVTT",  "*.vtt"),
                ("JSON",    "*.json"),
                ("All",     "*.*"),
            ],
            title="Экспорт истории",
            initialfile="talker_history.txt",
        )
        if not path:
            return

        entries = self._history.entries()
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "txt"
        try:
            if ext == "srt":
                content = exporters.history_to_pseudo_srt(entries)
            elif ext == "vtt":
                # vtt has no timestamped history either — use a pseudo-stack
                # built from srt segments via shared time formatter.
                content = exporters.to_vtt([
                    {"start": i * 2.5, "end": i * 2.5 + 2.0,
                     "text": e.get("text", "")}
                    for i, e in enumerate(entries) if e.get("text", "").strip()
                ])
            elif ext == "json":
                content = exporters.to_json(entries)
            else:
                content = self._history.export_text()

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"History export failed: {e}")

    def _update_count(self) -> None:
        n = len(self._history.entries())
        self._count_lbl.configure(text=f"{n} записей" if n else "")


# ══════════════════════════════════════════════════════════════════════════════
# Row-based list editor — replaces raw JSON / pipe-delimited text editing
# ══════════════════════════════════════════════════════════════════════════════

