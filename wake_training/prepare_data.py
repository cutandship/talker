# -*- coding: utf-8 -*-
"""Подготовка данных аугментации (venv) — вариант под задушенный канал:
вместо HF-датасетов (MIT RIR + AudioSet) используем ОДИН архив RIRS_NOISES
с OpenSLR (качается ночным rir_loop.ps1 через curl -C -): в нём и
реверберации комнат (real RIRs), и точечные шумы (pointsource_noises) для
фоновой подмешки.

Запуск (venv): venv\\Scripts\\python.exe prepare_data.py
Повторный запуск безопасен — готовые куски пропускаются.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import numpy as np
import scipy.io.wavfile
import scipy.signal
import soundfile as sf

HERE = Path(__file__).parent
DATA = HERE / "data"
SR = 16_000

RIR_OUT = DATA / "rirs"
NOISE_OUT = DATA / "background_16k"
ZIP = DATA / "rirs_noises.zip"


def _to_16k_wav(src_bytes_path, dst: Path) -> bool:
    try:
        data, sr = sf.read(src_bytes_path, dtype="float32", always_2d=True)
    except Exception:
        return False
    mono = data.mean(axis=1)
    if sr != SR:
        g = int(np.gcd(int(sr), SR))
        mono = scipy.signal.resample_poly(mono, SR // g, int(sr) // g)
    if mono.size < SR // 10:
        return False
    scipy.io.wavfile.write(str(dst), SR,
                           (np.clip(mono, -1, 1) * 32767).astype(np.int16))
    return True


def synth_rirs(n: int = 400) -> None:
    """Локальная замена скачиваемым RIR: синтетика image-source методом
    (pyroomacoustics) — случайные комнаты, позиции источника и микрофона.
    Стандартная практика для аугментации wake-word моделей; не требует сети."""
    import numpy as np
    import pyroomacoustics as pra
    rng = np.random.default_rng(36)
    RIR_OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for i in range(n):
        dst = RIR_OUT / f"synth_{i:04d}.wav"
        if dst.exists():
            continue
        try:
            dims = [rng.uniform(2.5, 10.0), rng.uniform(2.5, 8.0),
                    rng.uniform(2.3, 4.0)]
            absorb = rng.uniform(0.15, 0.65)
            room = pra.ShoeBox(dims, fs=SR,
                               materials=pra.Material(absorb),
                               max_order=12)
            src = [rng.uniform(0.5, d - 0.5) for d in dims]
            mic = [rng.uniform(0.5, d - 0.5) for d in dims]
            room.add_source(src)
            room.add_microphone(mic)
            room.compute_rir()
            rir = np.asarray(room.rir[0][0], dtype=np.float32)
            peak = float(np.max(np.abs(rir))) or 1.0
            scipy.io.wavfile.write(str(dst), SR,
                                   (rir / peak * 32767 * 0.9).astype(np.int16))
            made += 1
        except Exception as e:
            print("  rir fail:", e)
    print(f"synth RIR: +{made} (всего {len(list(RIR_OUT.glob('*.wav')))})")


def synth_noises(n: int = 400, sec: float = 10.0) -> None:
    """Локальный шумовой банк: цветные шумы (белый/розовый/коричневый) с
    амплитудной модуляцией — имитация вентиляторов, дороги, гула."""
    import numpy as np
    rng = np.random.default_rng(37)
    NOISE_OUT.mkdir(parents=True, exist_ok=True)
    ln = int(SR * sec)
    made = 0
    for i in range(n):
        dst = NOISE_OUT / f"noise_{i:04d}.wav"
        if dst.exists():
            continue
        white = rng.standard_normal(ln).astype(np.float32)
        kind = i % 3
        if kind == 0:                       # белый
            x = white
        elif kind == 1:                     # розовый (≈ -3 дБ/окт)
            spec = np.fft.rfft(white)
            f = np.maximum(np.fft.rfftfreq(ln, 1 / SR), 1.0)
            x = np.fft.irfft(spec / np.sqrt(f), n=ln).astype(np.float32)
        else:                               # коричневый (интеграл белого)
            x = np.cumsum(white).astype(np.float32)
            x -= x.mean()
        # медленная амплитудная модуляция — «дыхание» фона
        t = np.linspace(0, sec, ln, dtype=np.float32)
        mod = 0.6 + 0.4 * np.sin(2 * np.pi * rng.uniform(0.05, 0.5) * t
                                 + rng.uniform(0, 6.28))
        x = x * mod
        x = x / (float(np.max(np.abs(x))) or 1.0) * rng.uniform(0.3, 0.9)
        scipy.io.wavfile.write(str(dst), SR, (x * 32767).astype(np.int16))
        made += 1
    print(f"synth noise: +{made} (всего {len(list(NOISE_OUT.glob('*.wav')))})")


def rirs_noises() -> None:
    have_rir = RIR_OUT.exists() and len(list(RIR_OUT.glob("*.wav"))) > 100
    have_noise = NOISE_OUT.exists() and len(list(NOISE_OUT.glob("*.wav"))) > 300
    if have_rir and have_noise:
        print("RIRS_NOISES: уже разложено")
        return
    # Битый/недокачанный zip (канал задушен) → синтетика. Проверяем, что файл
    # вообще открывается как zip, а не только существует.
    zip_ok = False
    if ZIP.exists() and ZIP.stat().st_size > 50 * (1 << 20):
        try:
            with zipfile.ZipFile(ZIP) as _z:
                zip_ok = _z.testzip() is None
        except Exception:
            zip_ok = False
    if not zip_ok:
        print("rirs_noises.zip нет/битый — локальная синтетика (RIR + шумы)")
        synth_rirs()
        synth_noises()
        return
    RIR_OUT.mkdir(parents=True, exist_ok=True)
    NOISE_OUT.mkdir(parents=True, exist_ok=True)
    n_rir = n_noise = 0
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".wav"):
                continue
            low = name.lower()
            if "real_rirs" in low or "/simulated_rirs/" in low and "smallroom" in low:
                kind, out = "rir", RIR_OUT
            elif "pointsource_noises" in low:
                kind, out = "noise", NOISE_OUT
            else:
                continue
            dst = out / (Path(name).stem + ".wav")
            if dst.exists():
                continue
            tmp = DATA / "_tmp_extract.wav"
            with z.open(name) as src, open(tmp, "wb") as t:
                shutil.copyfileobj(src, t)
            if _to_16k_wav(tmp, dst):
                if kind == "rir":
                    n_rir += 1
                else:
                    n_noise += 1
            tmp.unlink(missing_ok=True)
            if (n_rir + n_noise) % 200 == 0 and (n_rir + n_noise):
                print(f"  rir={n_rir} noise={n_noise}...")
    print(f"RIRS_NOISES готово: rir={n_rir}, noise={n_noise}")


def resources() -> None:
    """embedding/melspectrogram .onnx → в resources editable-установки."""
    dst = HERE / "openWakeWord" / "openwakeword" / "resources" / "models"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("embedding_model.onnx", "melspectrogram.onnx"):
        src = DATA / name
        if src.exists() and not (dst / name).exists():
            shutil.copy2(src, dst / name)
            print("resources:", name, "-> ok")


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    resources()
    rirs_noises()
    print("PREPARE DONE")
