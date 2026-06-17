"""Интерактивный превью центрования пилюли (концепт 36, часть B) — standalone.

НЕ трогает основной код. Гоняет ровно ту же логику из widget_position.py,
что пойдёт в FlowBar, но на «живой» пилюле, которую можно таскать по экрану.

Запуск:  python widget_position_preview.py
- стартует по центру снизу (дефолт bottom-center);
- тащи мышью — на отпускании прилипает к ближайшей зоне (иначе остаётся free);
- ПРОБЕЛ — переключить idle⇄recording (показывает «рост от центра», не съезжает);
- S — вкл/выкл snap;  R — вернуть в bottom-center;  ESC — выход.
Подпись на пилюле показывает текущий anchor и режим.
"""
from __future__ import annotations

import tkinter as tk

import widget_position as wp

W_IDLE, W_ACTIVE, H = 150, 320, 56     # как у FlowBar (idle/active ширина, высота)


class Preview:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()

        self.anchor = wp.DEFAULT_ANCHOR    # 'bottom-center'
        self.off = (0, 0)
        self.snap = True
        self.active = False                # idle vs recording
        self.w = W_IDLE
        self._drag = None

        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#14b8a6")
        self.canvas = tk.Canvas(self.win, bg="#14b8a6", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.label = tk.Label(self.win, bg="#14b8a6", fg="white",
                              font=("Segoe UI", 10, "bold"))
        self.label.place(relx=0.5, rely=0.5, anchor="center")

        for ev, fn in (("<ButtonPress-1>", self._press),
                       ("<B1-Motion>", self._move),
                       ("<ButtonRelease-1>", self._release)):
            self.win.bind(ev, fn)
            self.canvas.bind(ev, fn)
        self.root.bind_all("<space>", self._toggle_active)
        self.root.bind_all("<KeyPress-s>", self._toggle_snap)
        self.root.bind_all("<KeyPress-r>", self._reset)
        self.root.bind_all("<Escape>", lambda e: self.root.destroy())

        self._place_to_anchor()

    # ── позиционирование через widget_position ──────────────────────────────
    def _full(self) -> tuple[int, int]:
        return self.w, H

    def _place_to_anchor(self) -> None:
        x, y = wp.anchor_to_xy(self.anchor, self.w, H, self.sw, self.sh,
                               off_x=self.off[0], off_y=self.off[1])
        self.win.geometry(f"{self.w}x{H}+{x}+{y}")
        self._redraw()

    def _redraw(self) -> None:
        self.win.geometry(f"{self.w}x{H}+{self.win.winfo_x()}+{self.win.winfo_y()}")
        self.label.configure(
            text=f"{self.anchor}   ·   {'REC' if self.active else 'idle'}   "
                 f"·   snap {'on' if self.snap else 'off'}")

    # ── drag ────────────────────────────────────────────────────────────────
    def _press(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _move(self, e):
        if self._drag:
            x = e.x_root - self._drag[0]
            y = e.y_root - self._drag[1]
            self.win.geometry(f"+{x}+{y}")

    def _release(self, e):
        self._drag = None
        x, y = self.win.winfo_x(), self.win.winfo_y()
        self.anchor, ox, oy = wp.resolve_drop(x, y, self.w, H, self.sw, self.sh,
                                              snap=self.snap)
        self.off = (ox, oy)
        self._place_to_anchor()

    # ── режимы ──────────────────────────────────────────────────────────────
    def _toggle_active(self, _e=None):
        """idle⇄recording: ширина меняется, но ЦЕНТР держим фиксированным —
        для центральных якорей пилюля не съезжает (то, чего нет сейчас в FlowBar)."""
        cx = self.win.winfo_x() + self.w / 2
        self.active = not self.active
        self.w = W_ACTIVE if self.active else W_IDLE
        new_x = int(cx - self.w / 2)
        new_x, _ = wp.clamp_to_visible(new_x, self.win.winfo_y(), self.w, H,
                                       self.sw, self.sh)
        self.win.geometry(f"{self.w}x{H}+{new_x}+{self.win.winfo_y()}")
        self._redraw()

    def _toggle_snap(self, _e=None):
        self.snap = not self.snap
        self._redraw()

    def _reset(self, _e=None):
        self.anchor, self.off = wp.DEFAULT_ANCHOR, (0, 0)
        self._place_to_anchor()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("Превью центрования: тащи пилюлю · ПРОБЕЛ idle/rec · S snap · R reset · ESC выход")
    Preview().run()
