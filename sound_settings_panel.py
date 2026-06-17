"""Блок настроек звука для меню (концепт 36, часть A) — готов к интеграции.

Выбор варианта на КАЖДОЕ событие (5 на каждое) + пресеты-комбинации + громкость
+ кнопка ▶ для прослушивания. Сделан на customtkinter — как остальной Settings.

ИНТЕГРАЦИЯ В ui.py (одна вставка в SettingsWindow, основной код не трогаем сейчас):
    from sound_settings_panel import SoundSettings
    SoundSettings(
        parent_frame,
        player=app._snd,                       # SoundPlayer из main.py
        get_cfg=lambda: app.config.sounds,     # объект [sounds] из config
        on_change=lambda: save_config(app.config),
    ).pack(fill="x", padx=12, pady=8)

Самостоятельный демо-запуск:  python sound_settings_panel.py
"""
from __future__ import annotations

import customtkinter as ctk

import sounds


class SoundSettings(ctk.CTkFrame):
    def __init__(self, master, player=None, get_cfg=None, on_change=None, **kw):
        super().__init__(master, **kw)
        self.player = player or sounds.SoundPlayer()
        self.get_cfg = get_cfg
        self.on_change = on_change or (lambda: None)
        self._ev_vars: dict[str, ctk.StringVar] = {}
        self._build()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        ctk.CTkLabel(self, text="Звуки диктовки",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(2, 6))

        head = ctk.CTkFrame(self, fg_color="transparent"); head.pack(fill="x")
        self.var_enabled = ctk.BooleanVar(value=self.player.enabled)
        ctk.CTkSwitch(head, text="Включить", variable=self.var_enabled,
                      command=self._toggle).pack(side="left")
        ctk.CTkLabel(head, text="Громкость").pack(side="left", padx=(16, 6))
        self.var_vol = ctk.DoubleVar(value=self.player.volume)
        ctk.CTkSlider(head, from_=0.0, to=1.0, variable=self.var_vol,
                      command=self._vol, width=160).pack(side="left")

        prow = ctk.CTkFrame(self, fg_color="transparent"); prow.pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(prow, text="Пресет").pack(side="left")
        self.var_preset = ctk.StringVar(value="—")
        ctk.CTkOptionMenu(prow, width=140, variable=self.var_preset,
                          values=["—", *sounds.PRESETS.keys()],
                          command=self._preset).pack(side="left", padx=8)
        ctk.CTkLabel(prow, text="— или собери комбинацию ниже",
                     text_color=("gray40", "#888")).pack(side="left", padx=4)

        # По строке на событие: подпись · выпадашка (5 вариантов) · ▶
        grid = ctk.CTkFrame(self, fg_color="transparent"); grid.pack(fill="x", pady=4)
        for ev in sounds.EVENTS:
            row = ctk.CTkFrame(grid, fg_color="transparent"); row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=sounds.EVENT_LABELS[ev], width=150,
                         anchor="w").pack(side="left")
            var = ctk.StringVar(value=self.player.selection[ev])
            self._ev_vars[ev] = var
            ctk.CTkOptionMenu(row, width=170, variable=var,
                              values=sounds.variant_names(ev),
                              command=lambda name, e=ev: self._pick(e, name)
                              ).pack(side="left", padx=8)
            ctk.CTkButton(row, text="▶", width=40,
                          command=lambda e=ev: self.player.play(e)).pack(side="left")

    # ── Логика ──────────────────────────────────────────────────────────────
    def _persist(self) -> None:
        cfg = self.get_cfg() if self.get_cfg else None
        if cfg is not None:
            try:
                cfg.enabled = self.player.enabled
                cfg.volume = self.player.volume
                for ev in sounds.EVENTS:
                    setattr(cfg, ev, self.player.selection[ev])
            except Exception:
                pass
        self.on_change()

    def _toggle(self):
        self.player.update(enabled=self.var_enabled.get()); self._persist()

    def _vol(self, _=None):
        self.player.update(volume=float(self.var_vol.get())); self._persist()

    def _pick(self, ev, name):
        self.player.set_variant(ev, name)
        self.var_preset.set("—")          # ручной выбор сбрасывает пресет
        self.player.play(ev)              # сразу слышно
        self._persist()

    def _preset(self, name):
        if name in sounds.PRESETS:
            self.player.update(selection=sounds.PRESETS[name])
            for ev, var in self._ev_vars.items():
                var.set(self.player.selection[ev])
            self._persist()


# ── Демо ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = ctk.CTk()
    app.title("Talker — настройки звука (демо блока для меню)")
    app.geometry("520x340")
    SoundSettings(app, player=sounds.SoundPlayer(enabled=True, volume=0.9)
                  ).pack(fill="both", expand=True, padx=16, pady=16)
    app.mainloop()
