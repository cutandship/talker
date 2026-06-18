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
    _set_noactivate,
    _get_mic_devices, _autostart_get, _autostart_set,
    _PopupMenu, _STATE_COLORS, _STATE_LABELS,
    _TRANSPARENT, _RED, _IDLE_DOT, _TEAL, _TEAL_SOFT, _ORANGE, _BLUE, _ERR,
    _CARD_BG, _HINT_FG, _SEP_FG,
)

class FlowBar:
    """
    Wispr Flow–style floating pill bar.

    States are strings matching State.value ('idle', 'recording', …).
    Waveform bars are driven by the recorder's live RMS when recording.

    Lively touches:
    - Width animates with critically-damped lerp instead of snapping.
    - Recording state grows a soft outer glow (concentric pills with fading alpha).
    - Hover lifts the pill ~2 px and brightens the fill.
    - State changes fade alpha so transitions don't feel like a hard cut.
    """

    _BAR_N    = 24   # bars span almost the full pill width
    _FPS_HI   = 28   # fps when animating
    _FPS_LO   = 15   # fps when idle — smooth enough to not look "laggy" at rest

    # Geometry — read via properties so they respond to widget-scale changes.
    # _ws() multiplies base px by the user-tunable widget scale ONLY (NOT the
    # global font scale) — so cranking the Settings font size no longer bloats
    # the floating pill. Tune via Settings header «Виджет −/+» or Интерфейс→Размер.
    def _ws(self, base: int) -> int:
        return max(1, int(round(base * self._cfg_scale)))

    @property
    def _BAR_W(self):    return self._ws(3)
    @property
    def _BAR_GAP(self):  return self._ws(3)
    @property
    def _BAR_MAX(self):  return self._ws(42)   # waveform height (повыше — прыжки заметнее)
    @property
    def _W_IDLE(self):   return self._ws(112)    # compact mic pill (cumulative width tweaks)
    @property
    def _W_ACTIVE(self): return self._ws(283)    # wave + timer (−10% recording width)
    @property
    def _H(self):        return self._ws(44)   # −15% height (52→44)
    @property
    def _GLOW_PAD(self):
        # Empty padding around the pill inside its host window. With glow we need
        # room for the halo; without glow keep it tight so the window hugs the
        # pill (less visible colorkey fringe / "обрамление").
        return self._ws(14) if self._show_glow else self._ws(4)

    def __init__(
        self,
        root: tk.Tk,
        recorder,                          # Recorder instance for live RMS
        on_open: Callable,                 # open history window
        on_settings: Callable,
        on_quit: Callable,
        on_url: Callable | None = None,
        on_whisper_toggle: Callable | None = None,
        widget_cfg=None,                   # WidgetConfig — appearance options
        on_bubble_toggle: Callable | None = None,
        on_cancel: Callable | None = None,
        on_confirm: Callable | None = None,
        on_record: Callable | None = None,   # left-click pill → start/stop record
    ) -> None:
        self._root = root
        self._recorder = recorder
        # The object the waveform reads its live mic level from. Defaults to the
        # PTT recorder; main swaps it to the ContinuousListener during hands-free
        # (set_level_source) so the bars bounce with your voice there too.
        self._rms_source = recorder
        self._on_open = on_open
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_url = on_url
        self._on_whisper_toggle = on_whisper_toggle
        self._on_bubble_toggle = on_bubble_toggle
        self._on_cancel = on_cancel
        self._on_confirm = on_confirm
        self._on_record = on_record

        # Pull widget appearance from config (defaults are baked in too)
        self._cfg_scale       = float(getattr(widget_cfg, "scale", 0.5)) if widget_cfg else 0.5
        self._cfg_opacity     = float(getattr(widget_cfg, "opacity", 0.75)) if widget_cfg else 0.75
        self._show_listen_lbl = bool(getattr(widget_cfg, "show_listening_label", False)) if widget_cfg else False
        self._show_glow       = bool(getattr(widget_cfg, "show_glow", False)) if widget_cfg else False
        self._cfg_pos_x       = int(getattr(widget_cfg, "pos_x", -1)) if widget_cfg else -1
        self._cfg_pos_y       = int(getattr(widget_cfg, "pos_y", -1)) if widget_cfg else -1
        # Anchor-zone position model (concept 36-C).
        self._cfg_anchor      = str(getattr(widget_cfg, "anchor", "bottom-center")) if widget_cfg else "bottom-center"
        self._cfg_off_x       = int(getattr(widget_cfg, "off_x", 0)) if widget_cfg else 0
        self._cfg_off_y       = int(getattr(widget_cfg, "off_y", 0)) if widget_cfg else 0
        self._cfg_snap        = bool(getattr(widget_cfg, "snap", True)) if widget_cfg else True

        self._state = "loading"
        self._mode_label: str = ""    # small per-app mode badge ("slack", "code", …)
        self._drag_xy: tuple[int, int] | None = None
        self._rec_start: float | None = None
        self._user_hidden = False     # True once the user hides the pill (tray) —
                                      # keeps set_state from forcing it back on screen

        # Waveform animation state — bars are a scrolling real-RMS history.
        self._bar_h   = [0.0] * self._BAR_N
        self._bar_tgt = [0.0] * self._BAR_N
        self._tick_n  = 0
        self._grow_zone = "center"     # how the pill expands (set per recording)

        # Spinner / pulse
        self._spin_angle = 0.0
        self._pulse_phase = 0.0

        # Width / glow / hover animation state
        self._width_now    = float(self._W_IDLE)
        self._width_target = float(self._W_IDLE)
        self._glow         = 0.0   # 0..1, drives glow intensity
        self._glow_target  = 0.0
        self._lift         = 0.0   # 0..1, hover lift
        self._lift_target  = 0.0
        self._tick_after   = None  # id of the scheduled _tick (for wake-on-hover)
        self._alpha        = 0.0   # appear-fade

        # Tap-vs-drag detection + hover tooltip (discoverability)
        self._press_xy: tuple[int, int] | None = None
        self._press_t: float = 0.0
        self._tip: tk.Toplevel | None = None
        self._tip_after: str | None = None

        self._win = self._make_window()
        self._canvas = self._make_canvas()
        _rounded_corners(self._win, borderless=True)

        self._tick()   # start animation loop

    def _make_window(self) -> tk.Toplevel:
        w = tk.Toplevel(self._root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.0)              # fade in on first tick
        w.attributes("-transparentcolor", _TRANSPARENT)
        w.configure(bg=_TRANSPARENT)
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        full_w = self._W_IDLE + 2 * self._GLOW_PAD
        full_h = self._H + 2 * self._GLOW_PAD
        # Legacy absolute position (pos_x/y from an old drag) wins; otherwise
        # place by anchor zone (concept 36-C) — default bottom-center.
        if (self._cfg_pos_x >= 0 and self._cfg_pos_y >= 0
                and self._cfg_pos_x < sw - 20 and self._cfg_pos_y < sh - 20):
            x = max(0, min(self._cfg_pos_x, sw - full_w))
            y = max(0, min(self._cfg_pos_y, sh - full_h))
        else:
            import widget_position as _wp
            x, y = _wp.anchor_to_xy(self._cfg_anchor, full_w, full_h, sw, sh,
                                    off_x=self._cfg_off_x, off_y=self._cfg_off_y)
        w.geometry(f"{full_w}x{full_h}+{x}+{y}")
        # Клик/драг по пилюле не должен уводить фокус из окна, куда диктуют.
        _set_noactivate(w)
        logger.info(f"FlowBar placed: cfg(anchor={self._cfg_anchor} "
                    f"off=({self._cfg_off_x},{self._cfg_off_y}) "
                    f"pos=({self._cfg_pos_x},{self._cfg_pos_y})) "
                    f"screen={sw}x{sh} full={full_w}x{full_h} -> +{x}+{y}")
        # Left tap → record (start/stop); drag → reposition; right-click →
        # History. Settings/Quit live in the tray menu.
        bindings = [
            ("<ButtonPress-1>",   self._drag_start),
            ("<B1-Motion>",       self._drag_move),
            ("<ButtonRelease-1>", self._drag_end),
            ("<Button-3>",        lambda e: self._open_history_debounced()),
            ("<Enter>",           self._on_enter),
            ("<Leave>",           self._on_leave),
        ]
        for ev, fn in bindings:
            w.bind(ev, fn)
        return w

    def _make_canvas(self) -> tk.Canvas:
        full_w = self._W_ACTIVE + 2 * self._GLOW_PAD
        full_h = self._H + 2 * self._GLOW_PAD
        c = tk.Canvas(
            self._win,
            width=full_w, height=full_h,
            bg=_TRANSPARENT, highlightthickness=0,
        )
        c.pack(fill="both", expand=True)
        bindings = [
            ("<ButtonPress-1>",   self._drag_start),
            ("<B1-Motion>",       self._drag_move),
            ("<ButtonRelease-1>", self._drag_end),
            ("<Button-3>",        lambda e: self._open_history_debounced()),
            ("<Enter>",           self._on_enter),
            ("<Leave>",           self._on_leave),
        ]
        for ev, fn in bindings:
            c.bind(ev, fn)
        return c

    _last_open_ts: float = 0.0

    def _open_history_debounced(self) -> None:
        """Open History, guarding against rapid duplicate triggers (a real
        double-click fires two near-simultaneous events; a tap + the trailing
        <Double-Button-1> can also overlap)."""
        import time
        now = time.monotonic()
        if now - self._last_open_ts < 0.5:
            return
        self._last_open_ts = now
        self._on_open()

    def _on_double_click(self, _e: tk.Event) -> None:
        self._open_history_debounced()

    # Position of the pill's actual visible top-left on screen — accounts for
    # both glow_pad and the lift animation, so external windows can dock
    # exactly against the pill with zero visual seam.
    def anchor_xy(self) -> tuple[int, int]:
        lift = int(self._lift * 2)
        return (self._win.winfo_x() + self._GLOW_PAD,
                self._win.winfo_y() + self._GLOW_PAD - lift)

    def anchor_size(self) -> tuple[int, int]:
        """Current pill size in screen pixels."""
        return (int(round(self._width_now)), self._H)

    def _compute_grow_zone(self) -> str:
        """Which third of the screen the pill is in → how it should grow:
        'left' (anchor left edge, grow right), 'right' (anchor right edge, grow
        left), or 'center' (grow symmetrically). Keeps the expanding pill on
        screen instead of sliding off the left edge."""
        try:
            sw = self._win.winfo_screenwidth()
            cx = self._win.winfo_x() + self._win.winfo_width() / 2
            f = cx / max(1, sw)
        except Exception:
            return "center"
        if f < 0.34:
            return "left"
        if f > 0.66:
            return "right"
        return "center"

    def _ensure_control_bubble(self) -> None:
        if getattr(self, "_ctrl_bubble", None) is None and (
                self._on_cancel or self._on_confirm):
            self._ctrl_bubble = ControlBubble(
                self._root,
                anchor_xy=self.anchor_xy,
                anchor_size=self.anchor_size,
                on_cancel=self._on_cancel or (lambda: None),
                on_confirm=self._on_confirm or (lambda: None),
            )

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _drag_start(self, e: tk.Event) -> None:
        import time
        self._drag_xy = (e.x_root - self._win.winfo_x(), e.y_root - self._win.winfo_y())
        self._press_xy = (e.x_root, e.y_root)
        self._press_t = time.monotonic()
        self._hide_tip()

    def _drag_move(self, e: tk.Event) -> None:
        if self._drag_xy:
            x = e.x_root - self._drag_xy[0]
            y = e.y_root - self._drag_xy[1]
            self._win.geometry(f"+{x}+{y}")
            # Drag the control bubble along with the pill so they stay glued
            # together as one row.
            cb = getattr(self, "_ctrl_bubble", None)
            if cb and getattr(cb, "_buttons", None):
                try: cb._position()
                except Exception: pass

    def _drag_end(self, e: tk.Event) -> None:
        import time
        self._drag_xy = None
        # Tap (press+release, almost no movement) → start/stop recording; a real
        # drag (moved > 6 px) just repositions the pill. History is on right-click.
        moved = 999
        if self._press_xy is not None:
            dx = e.x_root - self._press_xy[0]
            dy = e.y_root - self._press_xy[1]
            moved = (dx * dx + dy * dy) ** 0.5
        self._press_xy = None
        if moved <= 6 and (time.monotonic() - self._press_t) < 0.4:
            if self._on_record:
                self._on_record()
            return
        # Persist new position so it survives a restart.
        self._save_position_debounced()

    _save_pos_after_id: str | None = None

    def _save_position_debounced(self) -> None:
        if self._save_pos_after_id:
            try: self._win.after_cancel(self._save_pos_after_id)
            except Exception: pass
        self._save_pos_after_id = self._win.after(400, self._persist_position)

    def save_position_now(self) -> None:
        """Persist the pill's CURRENT position IMMEDIATELY (call on app quit).
        The drag-save is debounced 400 ms, so a «подвинул и сразу закрыл» would
        otherwise lose the move — this flushes it so the pill always reopens
        exactly where it was when the program was closed."""
        if self._save_pos_after_id:
            try: self._win.after_cancel(self._save_pos_after_id)
            except Exception: logger.debug("save_position_now: suppressed", exc_info=True)
            self._save_pos_after_id = None
        try:
            self._persist_position()
        except Exception:
            logger.exception("save_position_now failed")

    def _persist_position(self) -> None:
        self._save_pos_after_id = None
        x = self._win.winfo_x()
        y = self._win.winfo_y()
        try:
            import widget_position as _wp
            from config import load_config, save_config
            fw, fh = self._win.winfo_width(), self._win.winfo_height()
            sw, sh = self._win.winfo_screenwidth(), self._win.winfo_screenheight()
            anchor, off_x, off_y = _wp.resolve_drop(x, y, fw, fh, sw, sh,
                                                    snap=self._cfg_snap)
            cfg = load_config()
            cfg.widget.anchor = anchor
            cfg.widget.off_x = off_x
            cfg.widget.off_y = off_y
            cfg.widget.pos_x = -1            # clear legacy so the anchor wins
            cfg.widget.pos_y = -1
            save_config(cfg)
            self._cfg_anchor, self._cfg_off_x, self._cfg_off_y = anchor, off_x, off_y
            self._cfg_pos_x = self._cfg_pos_y = -1
            logger.info(f"FlowBar position saved: anchor={anchor} off=({off_x},{off_y})")
        except Exception:
            logger.exception("Could not persist FlowBar position")

    def _ctx_menu(self, e: tk.Event) -> None:
        items = [
            ("📋  История",   self._on_open),
            ("⚙  Настройки",  self._on_settings),
            None,
            ("✕  Выход",      self._on_quit),
        ]
        _PopupMenu(self._win, items, e.x_root, e.y_root)

    # ── State updates ──────────────────────────────────────────────────────────

    def set_level_source(self, src) -> None:
        """Swap where the waveform reads the live mic level. Pass the active
        ContinuousListener during hands-free; pass None to revert to the PTT
        recorder. The object just needs a `current_rms` (or `current_rms_live`)."""
        self._rms_source = src or self._recorder

    def _cancel_bubble_after(self) -> None:
        """Cancel a queued ✕/✓ bubble show/hide so rapid state flips can't land
        a stale show after a hide (or vice versa)."""
        aid = getattr(self, "_bubble_after", None)
        if aid is not None:
            try: self._root.after_cancel(aid)
            except Exception: pass
            self._bubble_after = None

    def set_state(self, state: str) -> None:
        prev = self._state
        self._state = state
        # No hover lift while recording/listening (hover is idle-only) — clear it
        # in case the cursor was over the pill when recording started.
        if state in ("recording", "listening"):
            self._lift_target = 0.0
        # ✕/✓ control buttons in ALL active states — PTT recording AND hands-free
        # listening / per-segment processing — not just PTT («старая форма» везде).
        # Kept up through processing so they don't flicker between continuous
        # segments. In continuous ✓ = стоп+вставить, ✕ = отмена.
        if state in ("recording", "listening", "processing"):
            if state == "recording":
                import time
                self._rec_start = time.time()
            self._ensure_control_bubble()
            if getattr(self, "_ctrl_bubble", None):
                # Pill grows rightward, so dock the buttons on the right;
                # _position() falls back to the left if there's no room.
                self._ctrl_bubble._side = "right"
                # Cancel a pending hide first: a very short PTT tap goes
                # recording→idle in <50 ms, and without this the show (after 50)
                # would fire AFTER the hide (after 0) → ✕/✓ stuck on an idle pill.
                self._cancel_bubble_after()
                self._bubble_after = self._root.after(50, self._ctrl_bubble.show)
        else:
            self._rec_start = None
            if getattr(self, "_ctrl_bubble", None):
                self._cancel_bubble_after()
                self._bubble_after = self._root.after(0, self._ctrl_bubble.hide)

        # All active states use the WIDE form (waveform + ✕/✓), so the pill
        # doesn't shrink/flicker between continuous segments.
        if state in ("recording", "listening", "processing"):
            self._width_target = self._W_ACTIVE
        else:
            self._width_target = self._W_IDLE
        self._glow_target = 1.0 if state == "recording" else (
            0.6 if state in ("listening", "processing") else 0.0
        )
        if prev != state:
            # subtle "blink" cue on transition
            self._pulse_phase = 0.0
        if state in ("recording", "processing"):
            self._hide_tip()

        # Don't force a user-hidden pill back on screen on every state change.
        if not self._user_hidden:
            self._win.deiconify()

    def show(self) -> None:
        self._user_hidden = False
        self._win.deiconify()

    def hide(self) -> None:
        self._user_hidden = True
        self._hide_tip()
        self._win.withdraw()

    def set_topmost(self, on: bool) -> None:
        """Toggle always-on-top. Dropped while a Talker window (Settings/History)
        is open so the pill doesn't float over it — but it stays visible."""
        try:
            self._win.attributes("-topmost", bool(on))
        except Exception:
            logger.debug("set_topmost: suppressed", exc_info=True)

    def set_behind(self, behind: bool) -> None:
        """While a Settings/History window is open, drop the pill's always-on-top
        flag so it can't float over that window. We deliberately do NOT lower()
        the window: lower() buries the pill under *every* window on screen (the
        browser, the IDE, the desktop) and it appears to vanish entirely. Just
        dropping topmost is enough — an overlapping active window covers it, but
        anywhere it doesn't overlap the pill stays visible. Restored on close."""
        try:
            if behind:
                self._win.attributes("-topmost", False)
            else:
                self._win.attributes("-topmost", True)
                self._win.lift()
        except Exception:
            logger.debug("set_behind: suppressed", exc_info=True)

    def set_mode_label(self, label: str) -> None:
        """Update the small per-app mode badge ('slack', 'code', ...). Empty = hide."""
        # 'default' / empty — don't show the badge (it's not informative).
        self._mode_label = "" if label in ("", "default") else label

    def _on_enter(self, _e=None) -> None:
        # Hover lift ONLY in the compact idle pill — NOT while recording/listening
        # (the waveform pill shouldn't react to the cursor).
        if self._state not in ("recording", "listening"):
            self._lift_target = 1.0
            self._wake_tick()

    def _on_leave(self, _e=None) -> None:
        self._lift_target = 0.0
        self._wake_tick()

    def _wake_tick(self) -> None:
        """Start animating NOW instead of waiting up to 1/_FPS_LO s (~125 ms) for
        the next idle tick — that wait is what made hover feel laggy. Reschedule
        the pending tick to fire immediately (after(0)) rather than calling
        _tick() synchronously: _on_enter/_on_leave are bound on BOTH the window
        and the canvas, so a single hover fires twice — after(0) coalesces them
        into one tick instead of double-stepping the animation (visible jitter)."""
        if self._tick_after is not None:
            try: self._win.after_cancel(self._tick_after)
            except Exception: pass
        self._tick_after = self._win.after(0, self._tick)

    # ── Hover tooltip (teaches the gestures) ────────────────────────────────────

    def _show_tip(self) -> None:
        self._tip_after = None
        # Don't cover the waveform while the user is mid-dictation.
        if self._state in ("recording", "processing"):
            return
        if self._tip is not None:
            return
        try:
            tip = tk.Toplevel(self._win)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            try: tip.attributes("-alpha", 0.96)
            except Exception: pass
            frm = tk.Frame(tip, bg=common._PILL_BG, highlightthickness=1,
                           highlightbackground=common._GRAY)
            frm.pack(fill="both", expand=True)
            tk.Label(
                frm,
                text="Клик — история   ·   ПКМ — меню   ·   тащить — переместить",
                bg=common._PILL_BG, fg=common._TEXT, font=_f("Segoe UI", 9),
                padx=_s(10), pady=_s(5),
            ).pack()
            tip.update_idletasks()
            ax, ay = self.anchor_xy()
            aw, _ah = self.anchor_size()
            tw = tip.winfo_reqwidth()
            tx = int(ax + aw / 2 - tw / 2)
            ty = int(ay - tip.winfo_reqheight() - _s(8))
            tx = max(4, min(tx, self._win.winfo_screenwidth() - tw - 4))
            ty = max(4, ty)
            tip.geometry(f"+{tx}+{ty}")
            self._tip = tip
        except Exception:
            logger.debug("tooltip failed", exc_info=True)
            self._tip = None

    def _hide_tip(self) -> None:
        if self._tip_after is not None:
            try: self._win.after_cancel(self._tip_after)
            except Exception: pass
            self._tip_after = None
        if self._tip is not None:
            try: self._tip.destroy()
            except Exception: pass
            self._tip = None

    # ── Live config apply (no restart) ──────────────────────────────────────────

    def apply_widget_cfg(self, widget_cfg) -> None:
        """Apply appearance changes from Settings without restarting Talker:
        size, opacity, glow and the listening label all update in place."""
        try:
            self._cfg_scale       = float(getattr(widget_cfg, "scale", self._cfg_scale))
            self._cfg_opacity     = float(getattr(widget_cfg, "opacity", self._cfg_opacity))
            self._show_listen_lbl = bool(getattr(widget_cfg, "show_listening_label",
                                                  self._show_listen_lbl))
            self._show_glow       = bool(getattr(widget_cfg, "show_glow", self._show_glow))
        except Exception:
            logger.exception("apply_widget_cfg: bad values")
            return
        # Re-evaluate width targets at the new scale and force a redraw next tick.
        self._width_target = self._W_ACTIVE if self._state == "recording" else self._W_IDLE
        try:
            self._draw()
        except Exception:
            logger.debug("redraw after apply_widget_cfg failed", exc_info=True)

    # ── Animation loop ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._tick_n += 1
        active = self._state in ("recording", "listening", "processing", "loading")
        # Keep high FPS while anything is still animating — e.g. the pill
        # shrinking back after ✕ cancel happens in the *idle* state, and at the
        # low idle FPS that shrink looked choppy ("притормаживает").
        animating = (abs(self._width_now - self._width_target) > 0.5
                     or abs(self._glow - self._glow_target) > 0.02
                     or abs(self._lift - self._lift_target) > 0.02
                     or any(b > 0.02 for b in self._bar_h))
        fps = self._FPS_HI if (active or animating) else self._FPS_LO

        # Re-assert topmost ~1×/sec. Another app grabbing the foreground (e.g.
        # pasting into Notepad, opening a window) can sink the pill behind it, and
        # Tk won't re-apply a `-topmost` it believes is still set. Toggling forces
        # WS_EX_TOPMOST back on; overrideredirect → no activation / focus steal.
        if self._tick_n % max(1, fps) == 0 and not self._user_hidden:
            try:
                self._win.attributes("-topmost", False)
                self._win.attributes("-topmost", True)
            except Exception:
                logger.debug("topmost re-assert suppressed", exc_info=True)

        # Re-clamp on monitor/resolution change (~once a second): if the pill
        # ended up off-screen (display unplugged, resolution dropped), pull it
        # back to its anchor zone. Skipped while dragging.
        if self._drag_xy is None and self._tick_n % max(1, fps) == 0:
            try:
                import widget_position as _wp
                sw = self._win.winfo_screenwidth()
                sh = self._win.winfo_screenheight()
                wx, wy = self._win.winfo_x(), self._win.winfo_y()
                fw, fh = self._win.winfo_width(), self._win.winfo_height()
                if _wp.is_offscreen(wx, wy, fw, fh, sw, sh):
                    nx, ny = _wp.anchor_to_xy(self._cfg_anchor, fw, fh, sw, sh,
                                              off_x=self._cfg_off_x, off_y=self._cfg_off_y)
                    self._win.geometry(f"+{nx}+{ny}")
            except Exception:
                pass

        # Processing spins 2× faster than loading — a more energetic "working"
        # feel while audio is being transcribed.
        self._spin_angle  = (self._spin_angle +
                             (16 if self._state == "processing" else 8)) % 360
        self._pulse_phase = (self._pulse_phase + 0.11) % (2 * math.pi)

        # Width / glow / lift: critically-damped lerp toward target
        self._width_now += 0.22 * (self._width_target - self._width_now)
        self._glow      += 0.18 * (self._glow_target  - self._glow)
        self._lift      += 0.24 * (self._lift_target  - self._lift)

        # Resize host window so prior right edge follows the pill — keeps the
        # transparent area minimal so we don't eat clicks beside the pill.
        # Skip while dragging — drag_move owns geometry then.
        cur_full_w = self._win.winfo_width()
        target_full_w = int(round(self._width_now)) + 2 * self._GLOW_PAD
        # cur_full_w <= 1 → window not realized yet (the first tick runs in
        # __init__, before the geometry from _make_window has settled). Resizing
        # now would read winfo_x/y as 0 and slam the pill to +0+0 — «пилюля
        # появляется в верхнем левом углу». Wait until it's realized.
        if (self._drag_xy is None and cur_full_w > 1
                and abs(target_full_w - cur_full_w) >= 1):
            full_h = self._H + 2 * self._GLOW_PAD
            sw = self._win.winfo_screenwidth()
            old_x = self._win.winfo_x()
            # Grow from the CENTRE by default (pill expands symmetrically, not to
            # the right) — EXCEPT at the screen edges: a right-edge anchor keeps
            # its RIGHT edge (grows left), a left-edge anchor keeps its LEFT edge.
            import widget_position as _wp
            fx = _wp.ZONES.get(self._cfg_anchor, (0.5, 0.0))[0]   # 'free' → centre
            if fx >= 0.99:            # right edge → keep RIGHT edge (grow left)
                new_x = int(round(old_x + cur_full_w - target_full_w))
            elif fx <= 0.01:          # left edge → keep LEFT edge (grow right)
                new_x = old_x
            else:                     # centre / mid → keep CENTRE (symmetric)
                center = old_x + cur_full_w / 2
                new_x = int(round(center - target_full_w / 2))
            if new_x + target_full_w > sw:
                new_x = max(0, sw - target_full_w)
            new_x = max(0, new_x)
            self._win.geometry(f"{target_full_w}x{full_h}+{new_x}+{self._win.winfo_y()}")
            # When pill changes width, re-anchor the control bubble so it
            # stays glued to the pill's edge.
            cb = getattr(self, "_ctrl_bubble", None)
            if cb and getattr(cb, "_buttons", None):
                try: cb._position()
                except Exception: pass

        # Sample the desktop behind the pill ~twice a second and remember whether
        # it's light → adapt contrast in _draw. Hysteresis (0.45 / 0.58) avoids
        # flicker at borderline backgrounds.
        if self._tick_n % max(2, fps // 2) == 0:
            _lum = self._sample_bg_luminance()
            if _lum is not None:
                if _lum > 0.58:
                    self._bg_light = True
                elif _lum < 0.45:
                    self._bg_light = False

        # Idle: 30% more transparent than configured; on hover ramp up to the
        # full configured opacity (self._lift is the hover amount, 0..1). On a
        # LIGHT background bump opacity so the pill doesn't wash out.
        # While recording/listening → FULLY OPAQUE (no transparency) so the pill
        # reads clearly; otherwise the configured opacity (dimmer at idle,
        # ramping up on hover).
        if self._state in ("recording", "listening"):
            target_alpha = 1.0
        else:
            _op_boost = 1.28 if getattr(self, "_bg_light", False) else 1.0
            target_alpha = max(0.1, min(1.0,
                               self._cfg_opacity * (0.7 + 0.3 * self._lift) * _op_boost))
        if abs(self._alpha - target_alpha) > 0.01:
            step = 0.07 if self._alpha < target_alpha else -0.07
            self._alpha = max(0.0, min(1.0, self._alpha + step))
            if (step > 0 and self._alpha > target_alpha) or \
               (step < 0 and self._alpha < target_alpha):
                self._alpha = target_alpha
            try: self._win.attributes("-alpha", self._alpha)
            except Exception: pass

        if self._state in ("recording", "listening"):
            if self._tick_n % 2 == 0:
                # Real microphone level (not random): read live RMS and scroll
                # it through the bars so the waveform tracks the actual audio.
                try:
                    src = self._rms_source or self._recorder
                    rms = float(getattr(src, "current_rms_live",
                                        getattr(src, "current_rms", 0.0)))
                except Exception:
                    rms = 0.0
                # Perceptual mapping → quiet vs loud both visible. Exponent 0.6
                # (less compressive than sqrt's 0.5) widens the soft↔loud gap for
                # a more EXPRESSIVE wave; ×5.0 lets loud peaks slam the full height
                # while normal speech (rms ~0.02–0.05) still varies ~0.5–0.8 — not a
                # flat «wall» (which is what ×5.5 on sqrt gave: everything pinned).
                lvl = max(0.0, rms) ** 0.6 * 5.0
                # Голубой (listening, Ctrl+Alt+Space) прыгает на всю высоту, как
                # красный (recording, PTT) — раньше listening резался вдвое (0.5).
                cap = 1.0
                lvl = max(0.06, min(cap, lvl))
                # Shift history left, newest sample on the right.
                self._bar_tgt = self._bar_tgt[1:] + [lvl]
                if self._tick_n % 30 == 0:
                    logger.info(f"FlowBar wave: state={self._state} rms={rms:.4f} lvl={lvl:.2f}")
            for i in range(self._BAR_N):
                # Snappier follow (0.5 → 0.7) so bars «скачут» livelier.
                self._bar_h[i] += 0.7 * (self._bar_tgt[i] - self._bar_h[i])
        else:
            # Decay bars when not active
            for i in range(self._BAR_N):
                self._bar_h[i] *= 0.85

        self._draw()
        self._tick_after = self._win.after(1000 // fps, self._tick)

    def _sample_bg_luminance(self) -> "float | None":
        """Median luminance (0..1) of the desktop just OUTSIDE the pill window.
        Sampling a ring around the window (not over it) avoids reading the pill
        itself. Cheap GDI GetPixel; returns None if nothing valid was read."""
        try:
            w = self._win
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ww, hh = w.winfo_width(), w.winfo_height()
        except Exception:
            return None
        if ww <= 1 or hh <= 1:
            return None
        m = 6
        pts = []
        for fx in (0.25, 0.5, 0.75):
            px = int(x + ww * fx)
            pts.append((px, y - m)); pts.append((px, y + hh + m))
        for fy in (0.35, 0.65):
            py = int(y + hh * fy)
            pts.append((x - m, py)); pts.append((x + ww + m, py))
        try:
            import ctypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            gdi32.GetPixel.restype = ctypes.c_uint32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            hdc = user32.GetDC(0)
            if not hdc:
                return None
            lums = []
            try:
                for px, py in pts:
                    if px < 0 or py < 0 or px >= sw or py >= sh:
                        continue
                    cval = gdi32.GetPixel(hdc, int(px), int(py))
                    if cval == 0xFFFFFFFF:        # CLR_INVALID
                        continue
                    r = cval & 0xFF
                    g = (cval >> 8) & 0xFF
                    b = (cval >> 16) & 0xFF
                    lums.append((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0)
            finally:
                user32.ReleaseDC(0, hdc)
        except Exception:
            return None
        if not lums:
            return None
        lums.sort()
        return lums[len(lums) // 2]

    # ── Drawing ────────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")

        # Pill geometry inside the wider window (which has GLOW_PAD on each side).
        pill_w = int(self._width_now)
        pill_h = self._H
        ox = self._GLOW_PAD
        oy = self._GLOW_PAD - int(self._lift * 2)   # hover lift
        cx = ox + pill_w // 2
        cy = oy + pill_h // 2

        color = _STATE_COLORS.get(self._state, common._GRAY)

        # Glow under the pill in active states
        if self._glow > 0.01:
            self._draw_glow(c, ox, oy, pill_w, pill_h, color)

        # Hover highlight — a soft accent halo that fades IN only while the
        # cursor is over the pill (self._lift → 1). At rest it's invisible, so
        # the pill stays calm and frame-free; on hover it lights up clearly.
        if self._lift > 0.02:
            for i in range(3, 0, -1):
                # Half-thickness (ws(1) vs ws(2)) accent ring, in RED like the
                # button dot. Clamped to the window padding so the tighter
                # no-glow window doesn't clip it at the edge.
                hw = min(self._ws(1) * i, self._GLOW_PAD)
                halo = self._mix(common._PILL_BG, _RED, (0.6 / i) * self._lift)
                self._draw_pill(c, ox - hw, oy - hw,
                                pill_w + 2 * hw, pill_h + 2 * hw, halo)

        # Pill body — a gentle luminance lift over the background (lighter =
        # "raised"). On a LIGHT desktop the translucent dark pill washes out, so
        # switch to a darker body + a dark contrasting ring (set in _tick →
        # self._bg_light) to keep it clearly visible.
        if getattr(self, "_bg_light", False):
            ring = max(2, self._ws(2))
            self._draw_pill(c, ox - ring, oy - ring,
                            pill_w + 2 * ring, pill_h + 2 * ring, "#0d0d0d")
            base = self._mix(common._PILL_BG, "#000000", 0.45)
            pill_color = self._mix(base, "#ffffff", 0.05 + 0.18 * self._lift)
        else:
            base = self._mix(common._PILL_BG, "#ffffff", 0.06)
            pill_color = self._mix(base, "#ffffff", 0.06 + 0.20 * self._lift)
        self._draw_pill(c, ox, oy, pill_w, pill_h, pill_color)

        if self._state == "recording":
            # Waveform across the pill — cancel/confirm buttons live in a
            # separate floating control window, so the pill stays light and
            # responsive.
            self._draw_waveform(c, cx, cy, color)
        elif self._state == "listening":
            self._draw_waveform(c, cx, cy, color)
            if self._show_listen_lbl:
                self._draw_label(c, cx + self._ws(22), cy, "Слушаю", color)
        elif self._state == "loading":
            # STATIC dot — no animation during startup load. The model load
            # saturates the CPU, so any spinner just stutters; a steady dot reads
            # clean. Distinguished from idle by the loading colour (blue).
            self._draw_idle_dot(c, cx, cy, color)
        elif self._state == "processing":
            # In a hands-free session the mic stays open between segments, so keep
            # the WAVEFORM rather than flashing a spinner each segment (less glitchy
            # in Ctrl+Alt+Space). Otherwise (PTT transcribe) spinner.
            if self._rms_source is not self._recorder:
                self._draw_waveform(c, cx, cy, color)
            else:
                self._draw_spinner(c, cx, cy, color)
        elif self._state == "idle":
            # Simple dot: muted gray at rest, ramps to red as the cursor hovers
            # (self._lift is the hover amount, 0..1).
            self._draw_idle_dot(c, cx, cy, self._mix(_IDLE_DOT, _RED, self._lift))
        elif self._state == "error":
            self._draw_x(c, cx, cy, _ERR)

    def _draw_glow(self, c: tk.Canvas, x: int, y: int, w: int, h: int,
                   color: str) -> None:
        """Soft outer halo under the pill — 4 concentric pills, fading outward."""
        cr, cg, cb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        # Background (transparent color) — used to fade outward.
        br, bg_, bb = 0x01, 0x01, 0x01  # _TRANSPARENT
        layers = 5
        pulse = 0.78 + 0.22 * math.sin(self._pulse_phase * 2)
        for i in range(layers, 0, -1):
            pad = i * 3
            alpha = (1.0 - i / (layers + 1)) * self._glow * pulse * 0.55
            r = int(cr * alpha + br * (1 - alpha))
            g = int(cg * alpha + bg_ * (1 - alpha))
            b = int(cb * alpha + bb * (1 - alpha))
            col = f"#{r:02x}{g:02x}{b:02x}"
            self._draw_pill(c, x - pad, y - pad, w + 2 * pad, h + 2 * pad, col)

    def _draw_pill(self, c: tk.Canvas, x: int, y: int, w: int, h: int,
                   fill: str) -> None:
        """Rounded-rectangle fill at (x, y), size w × h. Squarer than a capsule
        (small corner radius instead of h/2) — the long straight edges show far
        less colorkey fringe than the old oval end-caps."""
        r = max(1, min(h // 4, w // 4))      # small corner radius
        # Cross of two rectangles + four corner discs = a rounded rectangle.
        c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="")
        c.create_rectangle(x, y + r, x + w, y + h - r, fill=fill, outline="")
        c.create_oval(x, y, x + 2 * r, y + 2 * r, fill=fill, outline="")
        c.create_oval(x + w - 2 * r, y, x + w, y + 2 * r, fill=fill, outline="")
        c.create_oval(x, y + h - 2 * r, x + 2 * r, y + h, fill=fill, outline="")
        c.create_oval(x + w - 2 * r, y + h - 2 * r, x + w, y + h, fill=fill, outline="")

    def _draw_waveform(self, c: tk.Canvas, cx: int, cy: int, color: str) -> None:
        # Span almost the full pill width: clear the rounded end-caps + a small
        # margin, then distribute the bars evenly across the rest.
        pill_w = int(self._width_now)
        margin = self._H // 2 + self._ws(3)
        avail = max(self._ws(16), pill_w - 2 * margin)
        avail = min(avail * 1.2, pill_w - 2 * self._ws(4))   # +20% шире, но внутри пилюли
        n = max(1, self._BAR_N)
        step = avail / n
        bar_w = max(2, int(step * 0.62))
        x0 = cx - avail / 2 + (step - bar_w) / 2
        for i, h_ratio in enumerate(self._bar_h):
            bh = max(self._ws(3), int(h_ratio * self._BAR_MAX))
            bx = int(round(x0 + i * step))
            y0 = cy - bh // 2
            y1 = cy + bh // 2
            # Rounded bar in the state colour (recording → red). Single oval per
            # bar = light capsule shape.
            c.create_oval(bx, y0, bx + bar_w, y1, fill=color, outline="")

    def _draw_timer(self, c: tk.Canvas, right_x: int, cy: int) -> None:
        if self._rec_start is None:
            return
        import time
        elapsed = int(time.time() - self._rec_start)
        mm, ss = divmod(elapsed, 60)
        c.create_text(right_x - 34, cy, text=f"{mm:02d}:{ss:02d}",
                      fill=common._DIM, font=_FONT_DIM(), anchor="center")

    def _draw_spinner(self, c: tk.Canvas, cx: int, cy: int, color: str) -> None:
        n = 10
        bg_r, bg_g, bg_b = 0x1e, 0x1e, 0x1e  # common._PILL_BG components
        cr, cg, cb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        r_in = self._ws(8)            # 2× larger loader while transcribing
        r_out = self._ws(16)
        width = max(1, self._ws(4))
        for i in range(n):
            angle = math.radians(self._spin_angle + i * (360 / n))
            alpha = (i + 1) / n
            x1 = cx + r_in  * math.cos(angle)
            y1 = cy + r_in  * math.sin(angle)
            x2 = cx + r_out * math.cos(angle)
            y2 = cy + r_out * math.sin(angle)
            r = int(cr * alpha + bg_r * (1 - alpha))
            g = int(cg * alpha + bg_g * (1 - alpha))
            b = int(cb * alpha + bg_b * (1 - alpha))
            c.create_line(x1, y1, x2, y2, fill=f"#{r:02x}{g:02x}{b:02x}", width=width,
                          capstyle="round")

    def _draw_dot(self, c: tk.Canvas, cx: int, cy: int, color: str) -> None:
        pulse = 0.72 + 0.28 * math.sin(self._pulse_phase)
        r = int(5 * pulse)
        # Soft halo around dot
        halo = 0.35 + 0.25 * pulse
        for k in (4, 2, 0):
            shade = self._mix(common._PILL_BG, color, halo * (1 - k / 6))
            rr = r + k
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=shade, outline="")
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    def _draw_idle_dot(self, c: tk.Canvas, cx: int, cy: int, color: str) -> None:
        """Plain filled circle for the idle pill (no pulse) — gray at rest,
        red on hover."""
        r = self._ws(14)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    def _draw_label(self, c: tk.Canvas, cx: int, cy: int, text: str, color: str) -> None:
        c.create_text(cx, cy + 1, text=text, fill=color,
                      font=_FONT_LABEL(), anchor="center")

    def _draw_mic(self, c: tk.Canvas, cx: int, cy: int, color: str,
                  outline: str = "", ow: int = 0) -> None:
        """Stylised microphone glyph — capsule head + small stem + base.
        Drawn ~2× the original size for better visibility on the idle pill.
        `outline`/`ow` add a thin contour line around each part."""
        w = self._ws(14)
        h = self._ws(20)
        sw = max(1, self._ws(2))          # stem half-width
        bw = max(1, self._ws(2))          # base half-thickness
        # Base (drawn first so the stem/capsule overlap hides its top seam)
        c.create_rectangle(cx - w + 2, cy + h - 1,
                            cx + w - 2, cy + h + bw,
                            fill=color, outline=outline, width=ow)
        # Stem
        c.create_rectangle(cx - sw, cy + h // 2 + 1,
                            cx + sw, cy + h - 1,
                            fill=color, outline=outline, width=ow)
        # Mic capsule (last → its outline sits cleanly on top)
        c.create_oval(cx - w, cy - h, cx + w, cy + h // 2,
                      fill=color, outline=outline, width=ow)

    def _draw_x(self, c: tk.Canvas, cx: int, cy: int, color: str) -> None:
        r = self._ws(7)
        w = self._ws(2)
        c.create_line(cx - r, cy - r, cx + r, cy + r,
                      fill=color, width=w, capstyle="round")
        c.create_line(cx - r, cy + r, cx + r, cy - r,
                      fill=color, width=w, capstyle="round")

    def _draw_btn_cancel(self, c: tk.Canvas, cx: int, cy: int) -> None:
        """Cancel button on the left of the recording pill — ✕ in a circle."""
        r = self._ws(10)
        # Circle background (tag-bound for click detection)
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill="#2a2a2a", outline="", tags="btn_cancel")
        w = max(1, self._ws(2))
        a = self._ws(5)
        c.create_line(cx - a, cy - a, cx + a, cy + a,
                      fill="#ff6060", width=w, capstyle="round", tags="btn_cancel")
        c.create_line(cx - a, cy + a, cx + a, cy - a,
                      fill="#ff6060", width=w, capstyle="round", tags="btn_cancel")

    def _draw_btn_confirm(self, c: tk.Canvas, cx: int, cy: int) -> None:
        """Confirm button on the right — ✓ in a filled circle. Stop recording
        and transcribe (same as releasing the hotkey)."""
        r = self._ws(10)
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill="#ffffff", outline="", tags="btn_confirm")
        w = max(1, self._ws(2))
        # Check mark: short stroke + long stroke
        a = self._ws(5)
        c.create_line(cx - a, cy + 0, cx - 1, cy + a - 1,
                      fill="#1f8a4c", width=w + 1, capstyle="round", tags="btn_confirm")
        c.create_line(cx - 1, cy + a - 1, cx + a + 1, cy - a,
                      fill="#1f8a4c", width=w + 1, capstyle="round", tags="btn_confirm")

    @staticmethod
    def _mix(c1: str, c2: str, t: float) -> str:
        """Linear-interpolate two #rrggbb colors. t=0 → c1, t=1 → c2."""
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"


# ══════════════════════════════════════════════════════════════════════════════
# LoadingWindow – centered toast with spinner while the STT model is loading.
# Shown on State.LOADING, hidden on every other state.
# ══════════════════════════════════════════════════════════════════════════════



class ControlBubble:
    _BG = "#1a1a1a"
    _BORDER = "#2c2c2c"
    # ✕ ghost/secondary · ✓ primary (concept 36-B colours from the reference).
    _XF = "#3a2020"; _XG = "#ff7070"; _XR = "#ff5a5a"; _XGH = "#b06a6a"
    _VF = "#1f8a4c"; _VG = "#ffffff"; _VR = "#2fd39a"

    def __init__(self, root: tk.Tk,
                 anchor_xy: Callable[[], tuple[int, int]],
                 anchor_size: Callable[[], tuple[int, int]],
                 on_cancel: Callable[[], None],
                 on_confirm: Callable[[], None]) -> None:
        self._root = root
        self._anchor_xy = anchor_xy
        self._anchor_size = anchor_size
        self._on_cancel = on_cancel
        self._on_confirm = on_confirm
        self._side = "right"          # fallback dock side for "В строку" (unused in split)
        # Split layout "По бокам": ✕ in its own window LEFT of the pill, ✓ RIGHT.
        # Each button is one borderless canvas window → they straddle the pill.
        self._buttons: dict = {}      # name → {win, canvas, side, r, fill, gcol, rcol, glyph, label, cb, W, H}
        self._hover = None            # 'cancel' | 'confirm' | None
        self._press = None
        self._scale = {"cancel": 1.0, "confirm": 1.0}
        self._tick_id = None
        self._built_h = None

    @staticmethod
    def _mix(a: str, b: str, t: float) -> str:
        ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        br, bg_, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
        return f"#{int(ar+(br-ar)*t):02x}{int(ag+(bg_-ag)*t):02x}{int(ab+(bb-ab)*t):02x}"

    def _metrics(self) -> tuple:
        """Per-button radii from the pill height. ✓ primary (larger), ✕ smaller."""
        ph = max(20, int(self._anchor_size()[1]))
        rb = ph * 0.44 * 1.15 * 1.15          # icons +15%, then another +15% vs pill
        rv = rb * 1.12                        # ✓ size
        rc = rv                               # ✕ SAME size as ✓ (user request)
        smax = 1.10
        pad = int(rv * (smax - 1.0)) + 6
        label_h = 22
        return rc, rv, pad, label_h, smax

    def _win_size(self, r, pad, label_h, smax) -> tuple[int, int]:
        return int(2 * r * smax + 2 * pad), int(2 * r * smax + label_h + pad)

    def _build(self) -> None:
        rc, rv, pad, label_h, smax = self._metrics()
        specs = [
            ("cancel",  "left",  rc, self._XF, self._XG, self._XR, "✕", "Отмена", self._on_cancel),
            ("confirm", "right", rv, self._VF, self._VG, self._VR, "✓", "Готово", self._on_confirm),
        ]
        self._buttons = {}
        for name, side, r, fill, gcol, rcol, glyph, label, cb in specs:
            w = tk.Toplevel(self._root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            # Colorkey transparency so ONLY the circle shows — no square backing
            # plate around it (user request). No _rounded_corners: the window is
            # invisible except the disc.
            w.attributes("-transparentcolor", _TRANSPARENT)
            w.configure(bg=_TRANSPARENT)
            w.withdraw()
            # Клик по ✕/✓ не активирует это окно — фокус остаётся там, куда
            # пойдёт вставка текста.
            _set_noactivate(w)
            W, H = self._win_size(r, pad, label_h, smax)
            c = tk.Canvas(w, width=W, height=H, bg=_TRANSPARENT, highlightthickness=0)
            c.pack(fill="both", expand=True)
            for ev, fn in (("<Motion>",         lambda e, n=name: self._motion(e, n)),
                           ("<Leave>",          lambda e, n=name: self._leave(n)),
                           ("<ButtonPress-1>",  lambda e, n=name: self._down(e, n)),
                           ("<ButtonRelease-1>",lambda e, n=name: self._up(e, n))):
                c.bind(ev, fn)
            self._buttons[name] = dict(win=w, canvas=c, side=side, r=r, fill=fill,
                                       gcol=gcol, rcol=rcol, glyph=glyph, label=label,
                                       cb=cb, W=W, H=H)

    def _draw(self) -> None:
        for name, b in self._buttons.items():
            if not b["win"].winfo_exists():
                continue
            c = b["canvas"]
            c.delete("all")
            cx = b["W"] // 2
            cy = b["H"] // 2                   # disc centred in its window
            self._draw_button(name, b, cx, cy)

    def _draw_button(self, name, b, cx, cy) -> None:
        c = b["canvas"]
        hover = self._hover == name
        press = self._press == name
        s = self._scale[name]
        r = b["r"]
        rd = r * s                            # scale the WHOLE button, not the glyph
        glyph_col = b["gcol"]
        # ✕ is always a filled disc now (no ghost) so it's visible at rest, not
        # only on hover (user request).
        f = b["fill"]
        if hover:
            f = self._mix(f, "#ffffff", 0.10)
        if press:
            f = self._mix(f, "#000000", 0.12)
        c.create_oval(cx - rd, cy - rd, cx + rd, cy + rd, fill=f, outline="")
        if hover or press:
            for k in (2, 1):
                rr = rd + 3 + k * 2
                col = self._mix(self._BG, b["rcol"], 0.5 if k == 1 else 0.28)
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=col, width=2)
            c.create_oval(cx - rd - 2, cy - rd - 2, cx + rd + 2, cy + rd + 2,
                          outline=b["rcol"], width=2)
        gs = max(9, int(r * 0.92 * s))
        c.create_text(cx, cy, text=b["glyph"], fill=glyph_col,
                      font=(common._FONT_FAMILY, -gs, "bold"))
        if hover or press:
            c.create_text(cx, cy + rd + 14, text=b["label"], fill="#cfcfcf",
                          font=(common._FONT_FAMILY, -11))

    def _over_disc(self, e, b) -> bool:
        """Cursor over the VISIBLE disc (not the whole transparent window), so
        the hover zone matches what's drawn (user request)."""
        cx = b["W"] // 2
        cy = b["H"] // 2                      # match _draw: disc centred in window
        rr = b["r"] + 4                       # small hit pad around the disc
        return (e.x - cx) ** 2 + (e.y - cy) ** 2 <= rr * rr

    def _motion(self, e, name):
        b = self._buttons.get(name)
        over = bool(b) and self._over_disc(e, b)
        if over:
            self._hover = name
        elif self._hover == name:
            self._hover = None
        if b:
            try: b["canvas"].configure(cursor="hand2" if over else "")
            except Exception: pass

    def _leave(self, name):
        if self._hover == name:
            self._hover = None

    def _down(self, e, name):
        b = self._buttons.get(name)
        self._press = name if (b and self._over_disc(e, b)) else None

    def _up(self, e, name):
        b = self._buttons.get(name)
        if self._press == name and b and self._over_disc(e, b):
            self._fire(b["cb"])
        self._press = None

    def _tick(self) -> None:
        for name in ("cancel", "confirm"):
            tgt = 0.94 if self._press == name else (1.10 if self._hover == name else 1.0)
            self._scale[name] += (tgt - self._scale[name]) * 0.3
        self._draw()
        alive = bool(self._buttons) and any(b["win"].winfo_exists()
                                            for b in self._buttons.values())
        self._tick_id = self._root.after(16, self._tick) if alive else None

    def _destroy_wins(self) -> None:
        for b in self._buttons.values():
            try:
                if b["win"].winfo_exists():
                    b["win"].destroy()
            except Exception: logger.debug("_destroy_wins: suppressed", exc_info=True)
        self._buttons = {}

    def show(self) -> None:
        # Rebuild only when the pill HEIGHT changed (widget-scale) so button
        # sizes track the pill; otherwise reuse the existing windows.
        cur_h = max(20, int(self._anchor_size()[1]))
        alive = bool(self._buttons) and all(b["win"].winfo_exists()
                                            for b in self._buttons.values())
        if not alive or self._built_h != cur_h:
            self._destroy_wins()
            self._build()
            self._built_h = cur_h
        self._position()
        for b in self._buttons.values():
            b["win"].deiconify(); b["win"].lift()
            b["win"].attributes("-topmost", True)
        if self._tick_id is None:
            self._tick()

    def hide(self) -> None:
        if self._tick_id is not None:
            try: self._root.after_cancel(self._tick_id)
            except Exception: logger.debug("hide: suppressed", exc_info=True)
            self._tick_id = None
        for b in self._buttons.values():
            try:
                if b["win"].winfo_exists():
                    b["win"].withdraw()
            except Exception: logger.debug("hide: suppressed", exc_info=True)

    def _position(self) -> None:
        # Default: ✕ LEFT of the pill, ✓ RIGHT. But near a screen edge, both
        # buttons go on the side that HAS room (side by side), so neither runs
        # off the monitor. Simple rule, no per-button flip clashes.
        bc = self._buttons.get("cancel")
        bv = self._buttons.get("confirm")
        if not (bc and bv):
            return
        ax, ay = self._anchor_xy()
        pill_w, pill_h = self._anchor_size()
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        gap = max(6, int(pill_h * 0.45))
        Wc, Wv = bc["W"], bv["W"]
        space_left = ax
        space_right = sw - (ax + pill_w)
        if space_left >= Wc + gap + 4 and space_right >= Wv + gap + 4:
            xc = ax - Wc - gap                       # ✕ left
            xv = ax + pill_w + gap                   # ✓ right
        elif space_right >= Wc + Wv + 2 * gap + 4:
            xv = ax + pill_w + gap                   # both right, ✓ nearer pill
            xc = xv + Wv + gap
        else:
            xc = ax - Wc - gap                       # both left
            xv = xc - Wv - gap
        for b, x in ((bc, xc), (bv, xv)):
            W, H = b["W"], b["H"]
            x = max(4, min(int(x), sw - W - 4))
            y = ay + (pill_h - H) // 2
            y = max(8, min(y, sh - H - 8))
            b["win"].geometry(f"{W}x{H}+{x}+{y}")

    def _fire(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            logger.exception("ControlBubble callback failed")


# ══════════════════════════════════════════════════════════════════════════════
# UrlTranscribeWindow – paste a URL → get a transcript
# ══════════════════════════════════════════════════════════════════════════════

