# -*- coding: utf-8 -*-
"""Ночной оркестратор тренировки wake/stop-моделей. Запуск ОСНОВНЫМ питоном:
    python run_night.py
Этапы (каждый идемпотентен, упавший этап не валит остальные модели):
    1) prepare_data (venv)  — RIR + AudioSet + resources
    2) gen_samples  (venv)  — синтетика Silero/edge-tts
    3) train.py --augment_clips --train_model (venv) ×3 модели
    4) сбор .onnx в out/final/ + REPORT.md
Пока скрипт жив, системе запрещено засыпать (SetThreadExecutionState — без
изменения настроек электропитания). Логи: logs/<этап>.log
"""
from __future__ import annotations

import ctypes
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
VENV_PY = HERE / "venv" / "Scripts" / "python.exe"
LOGS = HERE / "logs"
FINAL = HERE / "out" / "final"
MODELS = ["ey_talker", "stop_stop", "talker_stop", "stop_da"]

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(on: bool) -> None:
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def run(name: str, args: list[str], cwd: Path = HERE) -> bool:
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"{name}.log"
    print(f"[{datetime.datetime.now():%H:%M:%S}] >>> {name}: {' '.join(map(str, args))}")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n===== {datetime.datetime.now()} =====\n")
        f.flush()
        p = subprocess.run([str(a) for a in args], cwd=str(cwd),
                           stdout=f, stderr=subprocess.STDOUT)
    ok = p.returncode == 0
    print(f"[{datetime.datetime.now():%H:%M:%S}] <<< {name}: "
          f"{'OK' if ok else f'FAIL (code {p.returncode}) — см. logs/{name}.log'}")
    return ok


def tail(path: Path, n: int = 25) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8",
                                        errors="ignore").splitlines()[-n:])
    except OSError:
        return "(нет лога)"


def main() -> int:
    # Single-instance lock: два параллельных run_night затирают друг другу
    # feature-файлы (гонка, из-за которой тренировка падала). Если лок занят
    # живым процессом — выходим, не мешаем.
    lock = HERE / "run_night.lock"
    if lock.exists():
        try:
            old = int(lock.read_text())
            import ctypes as _c
            h = _c.windll.kernel32.OpenProcess(0x1000, False, old)
            if h:
                _c.windll.kernel32.CloseHandle(h)
                print(f"run_night уже работает (pid {old}) — выходим")
                return 0
        except Exception:
            pass
    import os as _os
    lock.write_text(str(_os.getpid()))

    keep_awake(True)
    results: dict[str, str] = {}
    try:
        if not run("prepare_data", [VENV_PY, HERE / "prepare_data.py"]):
            print("prepare_data упал — тренировка без аугментационных данных "
                  "бессмысленна, стоп.")
            return 1
        if not run("gen_samples", [VENV_PY, HERE / "gen_samples.py"]):
            print("gen_samples упал — стоп.")
            return 1

        FINAL.mkdir(parents=True, exist_ok=True)
        for m in MODELS:
            # Idempotent per-model: if the final .onnx already exists, skip —
            # otherwise a guard restart would re-train finished models forever
            # and never reach the last one.
            if (FINAL / f"{m}.onnx").exists():
                results[m] = "OK (cached)"
                continue
            ok = run(f"train_{m}", [
                VENV_PY, HERE / "openWakeWord" / "openwakeword" / "train.py",
                "--training_config", HERE / "configs" / f"{m}.yml",
                "--augment_clips", "--train_model",
            ])
            # Success = the .onnx exists, regardless of train.py's exit code.
            # The optional post-export tflite step can fail (no onnx_tf) AFTER
            # the model is already saved — that must NOT mark the model failed.
            onnx = HERE / "out" / f"{m}.onnx"
            alt = HERE / "out" / m / f"{m}.onnx"
            src = onnx if onnx.exists() else (alt if alt.exists() else None)
            if src is not None:
                shutil.copy2(src, FINAL / f"{m}.onnx")
                results[m] = "OK" if ok else "OK (tflite skipped)"
            else:
                results[m] = "FAIL"

        report = [f"# Отчёт тренировки {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]
        for m in MODELS:
            report.append(f"## {m} — {results.get(m, '?')}")
            f = FINAL / f"{m}.onnx"
            if f.exists():
                report.append(f"- модель: `{f}` ({f.stat().st_size // 1024} KB)")
            report.append("- хвост лога тренировки:")
            report.append("```")
            report.append(tail(LOGS / f"train_{m}.log"))
            report.append("```")
            report.append("")
        (HERE / "out" / "REPORT.md").write_text("\n".join(report),
                                                encoding="utf-8")
        print("\n".join(f"{m}: {s}" for m, s in results.items()))
        print(f"Отчёт: {HERE / 'out' / 'REPORT.md'}")
        return 0 if all(v == "OK" for v in results.values()) else 2
    finally:
        keep_awake(False)
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
