"""
Build Talker into a portable one-folder executable.
Run: python build.py
Output: dist/Talker/  — copy this folder anywhere, run Talker.exe

Bundles the GigaAM v3 model (default engine) so it works fully offline on first
launch. Whisper stays on-demand (downloads when the user switches engine) to keep
the folder ~2 GB instead of ~3.6 GB.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# HF repo of the default GigaAM v3 model (onnx_asr downloads it from here).
_GIGAAM_REPO_DIR = "models--istupakov--gigaam-v3-onnx"
# onnx_asr model id == the bundled plain-folder name under <dist>/models/.
_GIGAAM_MODEL_NAME = "gigaam-v3-e2e-rnnt"


def find_package_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"Package '{name}' not found — run: pip install -r requirements.txt")
    return Path(spec.origin).parent


def _hf_hub() -> Path:
    hf_home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return hf_home / "hub"


def _dir_size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576


def bundle_gigaam(dist: Path) -> None:
    """Ship GigaAM v3 as a PLAIN folder of real files in <dist>/models/<name>.

    gigaam_engine loads it OFFLINE via onnx_asr.load_model(name, path=...), so we
    avoid the HF cache's snapshot SYMLINKS — those don't survive a plain copy/zip
    to an end-user Windows box and silently trigger a full re-download.
    """
    src_repo = _hf_hub() / _GIGAAM_REPO_DIR
    ref_file = src_repo / "refs" / "main"
    ref = ref_file.read_text().strip() if ref_file.exists() else None
    snap = (src_repo / "snapshots" / ref) if ref else None
    if not (snap and snap.exists()):
        print(f"\n[!] GigaAM snapshot not found under {src_repo}.")
        print("    Launch Talker once with engine=gigaam so it downloads, then re-run build.py.")
        return
    needed = ["config.json",
              "v3_e2e_rnnt_encoder.int8.onnx", "v3_e2e_rnnt_decoder.int8.onnx",
              "v3_e2e_rnnt_joint.int8.onnx", "v3_e2e_rnnt_vocab.txt"]
    dest = dist / "models" / _GIGAAM_MODEL_NAME
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in needed:
        s = snap / name
        if not s.exists():
            print(f"[!] missing {name} in snapshot — aborting model bundle")
            shutil.rmtree(dest)
            return
        shutil.copyfile(s, dest / name)   # copyfile follows symlinks → real bytes
    print(f"GigaAM bundled (plain folder, {_dir_size_mb(dest):.0f} MB): {dest}")


def main() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        print("Installing PyInstaller…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    ctk_dir = find_package_dir("customtkinter")

    icon = HERE / "icon.ico"
    args = [
        sys.executable, "-m", "PyInstaller",
        # --noupx: UPX fails on a few win32 .pyd and isn't worth the trouble.
        # No --clean: reuse the binary cache so rebuilds are fast (delete build/
        # manually for a from-scratch build).
        "--noconfirm", "--onedir", "--noconsole", "--noupx",
        "--name", "Talker",
        "--icon", str(icon) if icon.exists() else "NONE",
        # Override broken contrib hooks (webrtcvad → webrtcvad-wheels metadata).
        "--additional-hooks-dir", str(HERE / "_pyi_hooks"),

        # ── Исключаем тяжёлое, что рантайм не использует ──
        # gigaam (дефолт) = onnxruntime; whisper = ctranslate2. torch нужен ТОЛЬКО
        # для whisper device="auto" (GPU-проба) и обёрнут в try/except ImportError
        # → без torch whisper просто идёт на CPU. Экономит ~2 ГБ.
        "--exclude-module", "torch",
        "--exclude-module", "torchvision",
        "--exclude-module", "torchaudio",
        "--exclude-module", "transformers",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tensorboard",
        "--exclude-module", "nemo",

        # ── Bundled data (shipped files the app reads at runtime) ──
        "--add-data", f"{ctk_dir};customtkinter",
        "--add-data", f"{HERE / 'assets'};assets",          # шрифт + звуки диктовки
        "--add-data", f"{HERE / 'web_ui.html'};.",          # веб-UI страница

        # ── Core STT / audio / inject ──
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "onnx_asr",        # GigaAM (движок по умолчанию)
        "--collect-all", "onnxruntime",
        "--collect-all", "sounddevice",
        "--collect-binaries", "sounddevice",
        "--hidden-import", "webrtcvad",
        "--hidden-import", "noisereduce",
        "--hidden-import", "keyboard",
        "--hidden-import", "pyperclip",
        "--hidden-import", "pystray._win32",

        # ── Wake word «Hey Jarvis» (модели лежат внутри пакета) ──
        "--collect-all", "openwakeword",

        # ── Дакер + медиа-гард (COM через pycaw/comtypes — капризно к PyInstaller) ──
        "--collect-all", "comtypes",
        "--collect-submodules", "pycaw",
        "--hidden-import", "pycaw",
        "--hidden-import", "psutil",

        # ── Веб-UI сервер (uvicorn/fastapi — куча динамических импортов) ──
        "--collect-all", "uvicorn",
        "--collect-submodules", "fastapi",
        "--collect-submodules", "starlette",
        "--hidden-import", "fastapi",
        "--hidden-import", "anyio",
        "--hidden-import", "multipart",     # python-multipart (загрузка файла в /transcribe)

        # ── UI-пакет + Tk-фолбэк ──
        "--collect-submodules", "ui",
        "--hidden-import", "customtkinter",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "httpx",

        str(HERE / "main.py"),
    ]

    print("Running PyInstaller… (несколько минут)")
    subprocess.check_call(args, cwd=HERE)

    dist = HERE / "dist" / "Talker"
    # MIT требует включать LICENSE во все копии; README кладём рядом для контактов.
    for fname in ("LICENSE", "README.md"):
        src = HERE / fname
        if src.exists():
            shutil.copy2(src, dist / fname)
    # Lite по умолчанию: модель НЕ бандлится — скачается при первом запуске (~250 МБ,
    # прогресс в трее), дистрибутив компактный. `python build.py --bundle-model` —
    # тяжёлая офлайн-сборка с зашитой моделью (~+1 ГБ).
    if "--bundle-model" in sys.argv:
        bundle_gigaam(dist)
    else:
        print("\n[lite] Модель не забандлена — GigaAM v3 скачается при первом запуске.")

    # NB: ASCII only — the Windows console codepage (cp1251) can't encode ✓/… .
    print(f"\n[OK] Done! Portable folder: {dist}  (~{_dir_size_mb(dist):.0f} MB)")
    print("  Copy the entire 'Talker' folder anywhere and run Talker.exe")


if __name__ == "__main__":
    main()
