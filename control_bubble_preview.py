"""Временный ТЕСТ-КЛОН: ховер ✕/✓ + звуки (концепт 36, части A и D). Standalone.

НЕ трогает основной код Talker. Запуск без основной программы:
    python control_bubble_preview.py

Что внутри (всё на одном canvas — надёжнее плавающих окон):
- мок «пилюли» + бабла ✕/✓;
- 4 варианта ховера НА ВЫБОР: Baseline / Ring / Ring+зона / Полный;
- звук НА ВЫБОР: тема soft / crisp / minimal / выкл + кнопки теста сигналов;
- ▶ Старт / ■ Стоп — проигрывают earcon и переключают пилюлю.
Цель: пощупать наведение и звук, выбрать вариант. Потом подключим в ui.py.
"""
from __future__ import annotations

import math
import tkinter as tk

try:
    import sounds                      # переиспользуем earcon-движок
    _HAVE_SOUNDS = True
    PRESET_OPTS = ["выкл", *sounds.PRESETS.keys()]
except Exception:
    _HAVE_SOUNDS = False
    PRESET_OPTS = ["выкл"]

# ── Палитра (как в основном UI) ──────────────────────────────────────────────
BG        = "#141414"
PANEL     = "#1b1b1b"
PILL_BG   = "#23262b"
TEAL      = "#14b8a6"
TEXT      = "#f0f0f0"
DIM       = "#9a9a9a"
X_FILL    = "#3a2020"; X_GLYPH = "#ff7070"; X_RING = "#ff5a5a"; X_GHOST = "#b06a6a"
V_FILL    = "#1f8a4c"; V_GLYPH = "#ffffff"; V_RING = "#2fd39a"

# ── Варианты ховера ──────────────────────────────────────────────────────────
VARIANTS = {
    "Baseline":  dict(ring=False, scale=False, pad=False, label=False, asym=False),
    "Ring":      dict(ring=True,  scale=True,  pad=False, label=False, asym=False),
    "Ring+зона": dict(ring=True,  scale=True,  pad=True,  label=False, asym=False),
    "Полный":    dict(ring=True,  scale=True,  pad=True,  label=True,  asym=True),
}
VARIANT_ORDER = ["Baseline", "Ring", "Ring+зона", "Полный"]


def _hex(c):  # "#rrggbb" -> (r,g,b)
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _mix(a, b, t):
    ar, ag, ab = _hex(a); br, bg_, bb = _hex(b)
    return f"#{int(ar+(br-ar)*t):02x}{int(ag+(bg_-ag)*t):02x}{int(ab+(bb-ab)*t):02x}"


class Preview:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Talker — тест ✕/✓ + звуки (концепт 36)")
        self.root.configure(bg=BG)
        self.root.geometry("760x480")

        self.variant_name = "Полный"
        self.variant = VARIANTS[self.variant_name]
        self.layout = "По бокам"       # выбор пользователя: ✕ слева · ✓ справа
        self.recording = True          # ✕/✓ видны всегда — это тестер
        self._hover = None             # 'cancel' | 'confirm' | None
        self._press = None
        self._scale = {"cancel": 1.0, "confirm": 1.0}
        self._geom = {}                # последняя геометрия кнопок для hit-теста
        self._tick_n = 0
        self._flash = None             # (text, ttl)

        self._snd = None
        if _HAVE_SOUNDS:
            self._snd = sounds.SoundPlayer(enabled=True, volume=0.9)

        self._build_controls()
        self.c = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        for ev, fn in (("<Motion>", self._motion), ("<Leave>", self._leave),
                       ("<ButtonPress-1>", self._down), ("<ButtonRelease-1>", self._up)):
            self.c.bind(ev, fn)

        self._tick()

    # ── Панель управления ────────────────────────────────────────────────────
    def _build_controls(self) -> None:
        p = tk.Frame(self.root, bg=PANEL)
        p.pack(fill="x", side="top")

        r1 = tk.Frame(p, bg=PANEL); r1.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(r1, text="Ховер ✕/✓:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._var_v = tk.StringVar(value=self.variant_name)
        for name in VARIANT_ORDER:
            tk.Radiobutton(r1, text=name, value=name, variable=self._var_v,
                           command=self._on_variant, bg=PANEL, fg=TEXT,
                           selectcolor="#333", activebackground=PANEL,
                           activeforeground=TEAL, font=("Segoe UI", 10),
                           highlightthickness=0, bd=0).pack(side="left", padx=4)

        r1b = tk.Frame(p, bg=PANEL); r1b.pack(fill="x", padx=12, pady=2)
        tk.Label(r1b, text="Расположение:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._var_l = tk.StringVar(value=self.layout)
        for name in ("В строку", "По бокам"):
            tk.Radiobutton(r1b, text=name, value=name, variable=self._var_l,
                           command=self._on_layout, bg=PANEL, fg=TEXT,
                           selectcolor="#333", activebackground=PANEL,
                           activeforeground=TEAL, font=("Segoe UI", 10),
                           highlightthickness=0, bd=0).pack(side="left", padx=4)

        r2 = tk.Frame(p, bg=PANEL); r2.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(r2, text="Звук (пресет):", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._var_t = tk.StringVar(value=PRESET_OPTS[1] if len(PRESET_OPTS) > 1 else "выкл")
        om = tk.OptionMenu(r2, self._var_t, *PRESET_OPTS, command=self._on_preset)
        om.configure(bg="#2a2a2a", fg=TEXT, activebackground="#333",
                     activeforeground=TEAL, highlightthickness=0, bd=0,
                     font=("Segoe UI", 10), width=9)
        om["menu"].configure(bg="#2a2a2a", fg=TEXT)
        om.pack(side="left", padx=6)
        for kind, txt in (("start", "▶ start"), ("stop", "■ stop"),
                          ("pre_stop", "• pre_stop"), ("empty", "∅ empty")):
            tk.Button(r2, text=txt, command=lambda k=kind: self._play(k),
                      bg="#2a2a2a", fg=TEXT, activebackground="#3a3a3a",
                      activeforeground=TEAL, font=("Segoe UI", 9), bd=0,
                      padx=8, pady=2).pack(side="left", padx=3)
        tk.Label(r2, text="детально 5×4 — в sound_settings_panel.py", bg=PANEL,
                 fg=DIM, font=("Segoe UI", 8)).pack(side="left", padx=8)

        r3 = tk.Frame(p, bg=PANEL); r3.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(r3, text="▶ Старт записи", command=self._sim_start,
                  bg="#143d33", fg=TEAL, activebackground="#1c5246",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=10, pady=3).pack(side="left")
        tk.Button(r3, text="■ Стоп", command=self._sim_stop,
                  bg="#2a2a2a", fg=TEXT, activebackground="#3a3a3a",
                  font=("Segoe UI", 10, "bold"), bd=0, padx=10, pady=3).pack(side="left", padx=6)
        tk.Label(r3, text="наводи на ✕/✓ · кликай · меняй вариант и тему",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=10)
        if not _HAVE_SOUNDS:
            tk.Label(r3, text="(sounds.py не найден — звук выкл)", bg=PANEL,
                     fg="#cc6666", font=("Segoe UI", 9)).pack(side="left")

    # ── Колбэки ──────────────────────────────────────────────────────────────
    def _on_variant(self):
        self.variant_name = self._var_v.get()
        self.variant = VARIANTS[self.variant_name]

    def _on_layout(self):
        self.layout = self._var_l.get()

    def _on_preset(self, val):
        if not self._snd:
            return
        if val == "выкл":
            self._snd.update(enabled=False)
        else:
            self._snd.update(enabled=True, selection=sounds.PRESETS.get(val, {}))

    def _play(self, kind):
        if self._snd:
            self._snd.play(kind)

    def _sim_start(self):
        self.recording = True
        self._flash = ("запись…", 30)
        self._play("start")

    def _sim_stop(self):
        self.recording = False
        self._flash = ("стоп", 25)
        self._play("stop")

    # ── Мышь ─────────────────────────────────────────────────────────────────
    def _hit(self, x, y):
        pad = 14 if self.variant["pad"] else 0
        for name in ("confirm", "cancel"):
            g = self._geom.get(name)
            if not g:
                continue
            cx, cy, r = g
            if abs(x - cx) <= r + pad and abs(y - cy) <= r + pad:
                return name
        return None

    def _motion(self, e):
        self._hover = self._hit(e.x, e.y)
        self.c.configure(cursor="hand2" if self._hover else "")

    def _leave(self, _e):
        self._hover = None

    def _down(self, e):
        self._press = self._hit(e.x, e.y)

    def _up(self, e):
        hit = self._hit(e.x, e.y)
        if hit and hit == self._press:
            if hit == "confirm":
                self._flash = ("Готово ✓", 30); self._play("stop"); self.recording = False
            else:
                self._flash = ("Отменено", 30)   # ✕ — без звука; в реале есть тост «Вернуть»
                self.recording = False
        self._press = None

    # ── Анимация ─────────────────────────────────────────────────────────────
    def _tick(self):
        self._tick_n += 1
        for name in ("cancel", "confirm"):
            tgt = 0.94 if self._press == name else (1.10 if self._hover == name else 1.0)
            self._scale[name] += (tgt - self._scale[name]) * 0.3
        if self._flash:
            t, ttl = self._flash
            self._flash = (t, ttl - 1) if ttl > 1 else None
        self._draw()
        self.root.after(16, self._tick)

    # ── Рисование ────────────────────────────────────────────────────────────
    def _capsule(self, x, y, w, h, fill):
        r = h // 2
        self.c.create_oval(x, y, x + 2 * r, y + h, fill=fill, outline="")
        self.c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="")
        self.c.create_oval(x + w - 2 * r, y, x + w, y + h, fill=fill, outline="")

    def _draw(self):
        c = self.c; c.delete("all")
        W = c.winfo_width() or 760
        H = c.winfo_height() or 380
        cy = H // 2 + 14

        PH, PW = 66, 184                 # пилюля чуть крупнее/длиннее
        rb = PH * 0.44                   # базовый радиус кнопки (меньше высоты пилюли)
        asym = self.variant["asym"]
        r_cancel = rb * (0.90 if asym else 1.0)
        r_confirm = rb * (1.12 if asym else 1.0)
        g_pb = PH * 0.55                 # зазор пилюля↔кнопка
        g_bb = PH * 0.66                 # зазор ✕↔✓ (пошире — меньше промахов)

        if self.layout == "По бокам":
            total = 2 * r_cancel + g_pb + PW + g_pb + 2 * r_confirm
            left = int((W - total) / 2)
            cx_cancel = left + r_cancel
            pill_x = int(cx_cancel + r_cancel + g_pb)
            cx_confirm = pill_x + PW + g_pb + r_confirm
        else:                            # «В строку»: пилюля, затем ✕ ✓
            total = PW + g_pb + 2 * r_cancel + g_bb + 2 * r_confirm
            pill_x = int((W - total) / 2)
            cx_cancel = pill_x + PW + g_pb + r_cancel
            cx_confirm = cx_cancel + r_cancel + g_bb + r_confirm

        # Пилюля — это «твоя» пилюля (с живой волной)
        py = cy - PH // 2
        self._capsule(pill_x, py, PW, PH, PILL_BG)
        if self.recording:
            self._waveform(pill_x, cy, PW, PH)
        else:
            c.create_oval(pill_x + 26, cy - 6, pill_x + 38, cy + 6, fill=TEAL, outline="")
            c.create_text(pill_x + PW // 2 + 8, cy, text="Talker",
                          fill=DIM, font=("Segoe UI", 12))

        self._geom["cancel"] = (cx_cancel, cy, r_cancel)
        self._geom["confirm"] = (cx_confirm, cy, r_confirm)
        self._button("cancel", cx_cancel, cy, r_cancel,
                     X_FILL, X_GLYPH, X_RING, "✕", "Отмена")
        self._button("confirm", cx_confirm, cy, r_confirm,
                     V_FILL, V_GLYPH, V_RING, "✓", "Готово")

        if self._flash:
            c.create_text(pill_x + PW // 2, py - 18, text=self._flash[0],
                          fill=TEAL, font=("Segoe UI", 11, "bold"))
        c.create_text(W // 2, 22,
                      text=f"вариант: {self.variant_name}   ·   {self.layout}",
                      fill=DIM, font=("Segoe UI", 10))

    def _waveform(self, x0, cy, w, h):
        n = 16
        margin = h // 2 + 6
        avail = w - 2 * margin
        step = avail / n
        for i in range(n):
            lvl = (0.35 + 0.5 * abs(math.sin(self._tick_n * 0.12 + i * 0.6)))
            bh = int(lvl * (h * 0.5))
            bx = int(x0 + margin + i * step + step * 0.2)
            self.c.create_oval(bx, cy - bh // 2, bx + max(3, int(step * 0.5)),
                               cy + bh // 2, fill=TEAL, outline="")

    def _button(self, name, cx, cy, r, fill, glyph_col, ring_col, glyph, label):
        c = self.c
        v = self.variant
        hover = self._hover == name
        press = self._press == name
        s = self._scale[name] if v["scale"] else 1.0
        rd = r * s                             # масштабируем ВСЮ кнопку (не только глиф)
        ghost = v["asym"] and name == "cancel" and not hover and not press

        # Фон кнопки
        if ghost:
            c.create_oval(cx - rd, cy - rd, cx + rd, cy + rd, outline="#5a3a3a", width=1)
            glyph_col = X_GHOST
        else:
            f = fill
            if hover and not v["ring"]:        # Baseline: подсветка только фоном
                f = _mix(fill, "#ffffff", 0.18)
            elif hover:
                f = _mix(fill, "#ffffff", 0.10)
            if press:
                f = _mix(fill, "#000000", 0.12)
            c.create_oval(cx - rd, cy - rd, cx + rd, cy + rd, fill=f, outline="")

        # Кольцо на ховере (вариант Ring и выше)
        if v["ring"] and (hover or press):
            for k in (2, 1):                   # лёгкое двойное кольцо = мягкий glow
                rr = rd + 3 + k * 2
                col = _mix(BG, ring_col, 0.5 if k == 1 else 0.28)
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=col, width=2)
            c.create_oval(cx - rd - 2, cy - rd - 2, cx + rd + 2, cy + rd + 2,
                          outline=ring_col, width=2)

        # Глиф (масштабируется вместе с кнопкой)
        gs = max(10, int(r * 0.92 * s))
        c.create_text(cx, cy, text=glyph, fill=glyph_col,
                      font=("Segoe UI", gs, "bold"))

        # Подпись под кнопкой (вариант «Полный», на ховере)
        if v["label"] and (hover or press):
            c.create_text(cx, cy + rd + 16, text=label, fill="#cfcfcf",
                          font=("Segoe UI", 11))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("Тест-клон ✕/✓ + звуки. Меняй «Ховер ✕/✓» и «Звук», наводи на кнопки.")
    Preview().run()
