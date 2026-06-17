# -*- coding: utf-8 -*-
"""Анимированный лого-сплэш «Talker / Jarvis» — СОХРАНЁННЫЙ ДИЗАЙН (standalone).

Это дизайн загрузочного экрана, который недолго жил в приложении (StartupSplash):
дышащий бирюзовый диск с расходящимися «звуковыми волнами» (намёк на голосовую
диктовку), под ним «Talker» и подзаголовок. Из старта приложения он убран (висел
по центру и мог зависнуть до подъёма mainloop), но дизайн может быть интереснее
текущего стандартного вида — поэтому сохранён здесь отдельно, самодостаточным и
без зависимостей от ui.py.

Запуск демо:  python startup_splash_demo.py
Переиспользование: импортируй SplashLogo и встрой canvas-рендер куда нужно
(в т.ч. как альтернативный «вид Jarvis» для пилюли/загрузки).
"""
from __future__ import annotations

import math
import tkinter as tk


class SplashLogo:
    """Self-contained animated logo. Draws onto any tk.Canvas via render(canvas,
    phase) — no app dependencies, pixel sizes are literal."""

    BG = "#161616"
    BORDER = "#2c2c2c"
    FG = "#f0f0f0"
    DIM = "#7a7a7a"
    ACCENT = "#00d4aa"
    _AC = (0x00, 0xd4, 0xaa)
    _BGC = (0x16, 0x16, 0x16)

    @staticmethod
    def _mix(fg: tuple, bg: tuple, a: float) -> str:
        """Blend fg over bg by alpha a∈[0,1] → hex (tk.Canvas has no alpha)."""
        a = max(0.0, min(1.0, a))
        return "#%02x%02x%02x" % (
            int(fg[0] * a + bg[0] * (1 - a)),
            int(fg[1] * a + bg[1] * (1 - a)),
            int(fg[2] * a + bg[2] * (1 - a)))

    @classmethod
    def render(cls, canvas: tk.Canvas, phase: float) -> None:
        """One animation frame. `phase` grows by ~0.10 each tick."""
        canvas.delete("all")
        size = int(canvas["width"])
        cx = cy = size / 2
        r_max = size / 2 - 3

        # Sound-waves: concentric rings rippling outward, fading as they grow.
        rings = 3
        for i in range(rings):
            t = (phase / (2 * math.pi) + i / rings) % 1.0
            rr = r_max * (0.30 + 0.70 * t)
            a = (1.0 - t) * 0.55
            if a <= 0.02:
                continue
            canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                               outline=cls._mix(cls._AC, cls._BGC, a), width=2)

        # Breathing core disc — the «logo».
        pulse = 0.5 + 0.5 * math.sin(phase * 1.6)
        cr = r_max * (0.26 + 0.06 * pulse)
        canvas.create_oval(cx - cr, cy - cr, cx + cr, cy + cr,
                           fill=cls.ACCENT, outline="")
        # Soft top highlight for a hint of depth.
        hr = cr * 0.5
        canvas.create_oval(cx - hr, cy - hr * 1.4, cx + hr, cy + hr * 0.6,
                           fill=cls._mix((255, 255, 255), cls._AC, 0.28), outline="")


class SplashDemo:
    """A centered, rounded, frameless window showing the animated logo + title —
    exactly the look the app's StartupSplash had, preserved standalone."""

    def __init__(self, title: str = "Talker", subtitle: str = "Загрузка модели…",
                 canvas_px: int = 160) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try: self.root.attributes("-alpha", 0.0)
        except Exception: pass
        self.root.configure(bg=SplashLogo.BORDER)

        body = tk.Frame(self.root, bg=SplashLogo.BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = tk.Frame(body, bg=SplashLogo.BG)
        inner.pack(padx=64, pady=48)

        self.canvas = tk.Canvas(inner, width=canvas_px, height=canvas_px,
                                bg=SplashLogo.BG, highlightthickness=0)
        self.canvas.pack(pady=(0, 22))
        tk.Label(inner, text=title, font=("Segoe UI", 30, "bold"),
                 bg=SplashLogo.BG, fg=SplashLogo.FG).pack()
        tk.Label(inner, text=subtitle, font=("Segoe UI", 16),
                 bg=SplashLogo.BG, fg=SplashLogo.DIM).pack(pady=(8, 0))

        self._phase = 0.0
        self.root.after(0, self._center)
        self.root.after(0, self._fade_in)
        self._tick()

    def _center(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")

    def _fade_in(self, a: float = 0.0) -> None:
        a = min(0.97, a + 0.10)
        try: self.root.attributes("-alpha", a)
        except Exception: pass
        if a < 0.97:
            self.root.after(16, lambda: self._fade_in(a))

    def _tick(self) -> None:
        self._phase += 0.10
        SplashLogo.render(self.canvas, self._phase)
        self.root.after(33, self._tick)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    print("Демо лого-сплэша «Talker». Esc или закрыть окно — выход.")
    demo = SplashDemo()
    demo.root.bind("<Escape>", lambda e: demo.root.destroy())
    demo.run()
