# -*- coding: utf-8 -*-
"""Шим вместо пакета acoustics (он тянет matplotlib+pandas ~20 МБ, которые
не пролезают через задушенный канал). openwakeword/data.py использует из
него РОВНО одно: acoustics.generator.noise(N, color=...) — цветной шум.
Кладём в site-packages venv крошечный совместимый модуль."""
import sys
from pathlib import Path

SITE = (Path(__file__).parent / "venv" / "Lib" / "site-packages" / "acoustics")
SITE.mkdir(parents=True, exist_ok=True)

(SITE / "__init__.py").write_text(
    '"""Mini-shim for the `acoustics` package (only generator.noise).\n'
    'The real package drags matplotlib+pandas; openWakeWord needs just\n'
    'colored-noise generation. See make_acoustics_shim.py in wake_training."""\n'
    "from . import generator  # noqa: F401\n",
    encoding="utf-8")

(SITE / "generator.py").write_text('''\
"""Colored noise generator, API-compatible subset of acoustics.generator."""
import numpy as np

_EXP = {"white": 0.0, "pink": 0.5, "red": 1.0, "brown": 1.0,
        "blue": -0.5, "violet": -1.0, "purple": -1.0}


def noise(N, color="white", state=None):
    rng = state if state is not None else np.random
    white = rng.standard_normal(int(N)) if hasattr(rng, "standard_normal") \\
        else rng.randn(int(N))
    e = _EXP.get(str(color).lower(), 0.0)
    if e == 0.0:
        x = white.astype(np.float64)
    else:
        spec = np.fft.rfft(white)
        f = np.fft.rfftfreq(int(N))
        f[0] = f[1] if len(f) > 1 else 1.0
        spec = spec / (f ** e)
        x = np.fft.irfft(spec, n=int(N))
    std = x.std() or 1.0
    return (x / std).astype(np.float64)
''', encoding="utf-8")

print("acoustics shim written to", SITE)
sys.exit(0)
