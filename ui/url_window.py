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

class UrlTranscribeWindow:
    """Lets the user paste a URL (YouTube etc.) and saves a transcript."""

    _open_win: ctk.CTkToplevel | None = None

    @classmethod
    def open(cls, root: tk.Tk) -> None:
        if cls._open_win and cls._open_win.winfo_exists():
            cls._open_win.lift()
            cls._open_win.focus_force()
            return
        inst = cls(root)
        cls._open_win = inst._win

    def __init__(self, root: tk.Tk) -> None:
        win = ctk.CTkToplevel(root)
        self._win = win
        win.title("Talker — Транскрибировать URL")
        win.geometry(f"{_s(540)}x{_s(280)}")
        win.minsize(_s(440), _s(220))
        win.protocol("WM_DELETE_WINDOW", self._on_close)
        win.bind("<Escape>", lambda _e: self._on_close())
        import threading
        self._cancelled = False
        self._cancel_evt = threading.Event()
        self._build()

    def _on_close(self) -> None:
        self._cancelled = True
        self._cancel_evt.set()      # tell the worker thread to stop touching Tk
        self._win.destroy()
        UrlTranscribeWindow._open_win = None

    def _build(self) -> None:
        body = ctk.CTkFrame(self._win, fg_color=("#f0f0f0", "#0f0f0f"))
        body.pack(fill="both", expand=True, padx=_s(12), pady=_s(12))

        ctk.CTkLabel(body, text="URL (YouTube / Vimeo / podcast / любой сайт, "
                                "который умеет yt-dlp):",
                     font=_f("Segoe UI", 10), anchor="w").pack(fill="x", pady=(2, 4))

        self._url_var = tk.StringVar()
        url_entry = ctk.CTkEntry(body, textvariable=self._url_var,
                                  placeholder_text="https://www.youtube.com/watch?v=…")
        url_entry.pack(fill="x", pady=(0, 8))
        url_entry.focus_set()

        fmt_row = ctk.CTkFrame(body, fg_color="transparent")
        fmt_row.pack(fill="x", pady=4)
        ctk.CTkLabel(fmt_row, text="Формат:", anchor="w",
                     width=_s(80)).pack(side="left")
        self._fmt_var = tk.StringVar(value="txt")
        for label, value in [("TXT", "txt"), ("SRT", "srt"),
                              ("VTT", "vtt"), ("JSON", "json")]:
            ctk.CTkRadioButton(fmt_row, text=label, variable=self._fmt_var,
                                value=value).pack(side="left", padx=_s(6))

        self._status_var = tk.StringVar(value="Введи URL и нажми «Старт».")
        ctk.CTkLabel(body, textvariable=self._status_var, anchor="w",
                     wraplength=_s(500), justify="left",
                     font=_f("Segoe UI", 10), text_color="#aaa"
                     ).pack(fill="x", pady=(10, 4))

        self._pb = ctk.CTkProgressBar(body, height=_s(12))
        self._pb.pack(fill="x", pady=4)
        self._pb.set(0.0)

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        self._start_btn = ctk.CTkButton(btn_row, text="Старт", width=_s(120),
                                         command=self._start)
        self._start_btn.pack(side="left")
        ctk.CTkButton(btn_row, text="Закрыть", width=_s(100),
                      fg_color="gray40", command=self._on_close
                      ).pack(side="right")

    def _start(self) -> None:
        import tkinter.filedialog as fd
        url = self._url_var.get().strip()
        try:
            from url_transcribe import is_supported_url, transcribe_url
        except Exception as e:
            self._status_var.set(f"✗ {e}")
            return
        if not is_supported_url(url):
            self._status_var.set("✗ Это не похоже на URL (нужен http/https).")
            return

        fmt = self._fmt_var.get()
        path = fd.asksaveasfilename(
            defaultextension="." + fmt,
            filetypes=[(fmt.upper(), "*." + fmt), ("All", "*.*")],
            title="Куда сохранить транскрипт",
            initialfile="url_transcript." + fmt,
        )
        if not path:
            return
        output_path = Path(path)
        self._start_btn.configure(state="disabled")
        self._status_var.set("Готовлюсь…")

        def _post(fn: Callable[[], None]) -> None:
            # Marshal a UI update onto the Tk thread. Never touch Tk from the
            # worker directly: the cancel Event (thread-safe) replaces the
            # cross-thread winfo_exists() check, and after() is guarded against
            # a window destroyed between the check and the call (TclError).
            if self._cancel_evt.is_set():
                return
            try:
                self._win.after(0, fn)
            except tk.TclError:
                pass

        def _progress(frac: float, label: str) -> None:
            f = max(0.0, min(1.0, frac))
            _post(lambda: (self._pb.set(f), self._status_var.set(label)))

        def _worker() -> None:
            try:
                transcribe_url(url, output_path, fmt, on_progress=_progress)
                _post(lambda: (self._status_var.set(f"✓ Сохранено: {output_path}"),
                               self._pb.set(1.0)))
            except Exception as e:
                logger.exception("URL transcribe failed")
                _post(lambda: self._status_var.set(f"✗ {e}"))
            finally:
                _post(lambda: self._start_btn.configure(state="normal"))

        import threading
        threading.Thread(target=_worker, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# PasteFallbackBubble – топ-most всплывашка для ручной вставки
# ══════════════════════════════════════════════════════════════════════════════

