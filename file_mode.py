"""File-mode entry point: transcribe an audio/video file without the tray.

Usage:
  python main.py --transcribe FILE [--output OUT] [--format srt|txt|vtt|json]

Also registers an Explorer context menu entry ("Транскрибировать с Talker")
so the user can right-click any audio/video file and invoke this flow.

See concept/12_explorer_file_mode.md.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from config import load_config

logger = logging.getLogger(__name__)

# Extensions we register and accept. faster-whisper handles anything libav can
# decode, but we only register Explorer entries for common media types.
SUPPORTED_EXTS = (
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac",
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
)


# ── CLI flow ──────────────────────────────────────────────────────────────────

def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="Talker file-mode")
    parser.add_argument("--transcribe", required=True, help="input audio/video file")
    parser.add_argument("--output", default=None,
                        help="output path; if omitted, sits next to input with chosen ext")
    parser.add_argument("--format", default=None,
                        choices=["txt", "srt", "vtt", "json"],
                        help="output format; default is inferred from --output extension or 'txt'")
    parser.add_argument("--silent", action="store_true",
                        help="no UI progress window")
    args = parser.parse_args(argv)

    input_path = Path(args.transcribe).expanduser().resolve()
    if not input_path.exists():
        print(f"File not found: {input_path}", file=sys.stderr)
        return 2

    fmt = args.format
    if fmt is None and args.output:
        ext = args.output.rsplit(".", 1)[-1].lower() if "." in args.output else "txt"
        fmt = ext if ext in ("txt", "srt", "vtt", "json") else "txt"
    fmt = fmt or "txt"

    output_path = (Path(args.output).expanduser().resolve()
                   if args.output
                   else input_path.with_suffix("." + fmt))

    if args.silent:
        return _run_headless(input_path, output_path, fmt)
    return _run_with_window(input_path, output_path, fmt)


def _run_headless(input_path: Path, output_path: Path, fmt: str) -> int:
    cfg = load_config()
    _setup_logging()
    logger.info(f"File-mode: {input_path} -> {output_path} ({fmt})")
    try:
        segs = _transcribe(input_path, cfg, on_progress=None)
        _write_output(segs, output_path, fmt)
        print(f"Saved: {output_path}")
        return 0
    except Exception as e:
        logger.exception("File-mode failed")
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _run_with_window(input_path: Path, output_path: Path, fmt: str) -> int:
    cfg = load_config()
    _setup_logging()
    # UI scale honored
    from ui import _UiScale, _f, _s
    _UiScale.set(cfg.ui.font_scale)

    root = ctk.CTk()
    root.title(f"Talker — {input_path.name}")
    root.geometry(f"{_s(520)}x{_s(280)}")

    status_var = tk.StringVar(value="Загрузка модели…")
    progress_var = tk.DoubleVar(value=0.0)
    preview_var = tk.StringVar(value="")

    ctk.CTkLabel(root, textvariable=status_var,
                 font=_f("Segoe UI", 12, "bold")).pack(pady=_s(10), padx=_s(16), anchor="w")
    pb = ctk.CTkProgressBar(root, height=_s(14))
    pb.pack(fill="x", padx=_s(16), pady=_s(4))
    pb.set(0.0)

    ctk.CTkLabel(root, textvariable=preview_var, anchor="w", justify="left",
                 wraplength=_s(480), text_color="#aaa",
                 font=_f("Segoe UI", 10)).pack(
        fill="both", expand=True, padx=_s(16), pady=_s(10))

    result = {"code": 1}

    def on_progress(done: float, total: float, text: str) -> None:
        pct = (done / total) if total > 0 else 0.0
        # on_progress fires on the worker thread; Tk vars/widgets must only be
        # touched on the GUI thread or Tcl corrupts/crashes. Marshal via after().
        root.after(0, lambda: progress_var.set(pct))
        root.after(0, lambda: pb.set(pct))
        root.after(0, lambda: status_var.set(
            f"Обработано {done:.0f} / {total:.0f} сек ({pct*100:.0f}%)"))
        if text:
            root.after(0, lambda t=text: preview_var.set("… " + t[-300:]))

    def worker():
        try:
            root.after(0, lambda: status_var.set("Транскрибирую…"))
            segs = _transcribe(input_path, cfg, on_progress=on_progress)
            _write_output(segs, output_path, fmt)
            root.after(0, lambda: status_var.set(f"✓ Готово: {output_path}"))
            root.after(0, lambda: pb.set(1.0))
            result["code"] = 0
            root.after(2500, root.destroy)
        except Exception as e:
            logger.exception("File-mode UI worker failed")
            root.after(0, lambda: status_var.set(f"✗ Ошибка: {e}"))
            # Close after a longer delay than the success path (so the user can
            # read the error) — otherwise mainloop() blocks forever and the
            # process becomes a zombie. result["code"] stays 1.
            root.after(10000, root.destroy)

    import threading
    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    return result["code"]


# ── Transcription ────────────────────────────────────────────────────────────

def _transcribe(input_path: Path, cfg, on_progress=None) -> list[dict]:
    """Run faster-whisper on the file, returning a list of segment dicts.

    `on_progress(done_sec, total_sec, latest_text)` is called as each segment
    finishes so the UI can show a progress bar and live preview.
    """
    from transcriber import Transcriber
    transcriber = Transcriber(
        model_size=cfg.stt.model,
        language=cfg.stt.language or None,
        normalize=False,                # whisper handles file audio
        noise_reduction=False,
        device=cfg.stt.device,
        compute_type=cfg.stt.compute_type,
        cpu_threads=cfg.stt.cpu_threads,
        vocabulary=cfg.vocabulary.words,
    )

    # We bypass Transcriber.transcribe() because it's tuned for numpy arrays;
    # here we want segment-by-segment streaming so progress is live.
    from vocabulary import build_initial_prompt
    initial_prompt = build_initial_prompt(cfg.vocabulary.words, cfg.stt.language) or None

    segments, info = transcriber.model.transcribe(
        str(input_path),
        language=cfg.stt.language or None,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 1000},
        initial_prompt=initial_prompt,
        beam_size=5,
        # Files are long, single-source — context + fallback help accuracy.
        temperature=(0.0, 0.2, 0.4),
        condition_on_previous_text=True,
    )
    total = float(getattr(info, "duration", 0.0) or 0.0)

    out: list[dict] = []
    for seg in segments:
        out.append({
            "start": float(seg.start),
            "end":   float(seg.end),
            "text":  seg.text.strip(),
        })
        if on_progress:
            try:
                on_progress(float(seg.end), total, seg.text)
            except Exception:
                pass
    return out


def _write_output(segs: list[dict], output_path: Path, fmt: str) -> None:
    import exporters
    if fmt == "srt":
        content = exporters.to_srt(segs)
    elif fmt == "vtt":
        content = exporters.to_vtt(segs)
    elif fmt == "json":
        import json
        content = json.dumps({"segments": segs}, ensure_ascii=False, indent=2)
    else:
        content = "\n".join(s["text"] for s in segs if s["text"])
    output_path.write_text(content, encoding="utf-8")


# ── Logging ─────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    if logging.getLogger().handlers:
        return
    here = Path(__file__).parent
    logging.basicConfig(
        filename=str(here / "talker.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8",
    )


# ── Explorer context menu (Windows registry) ─────────────────────────────────

_MENU_VERB = "TalkerTranscribe"
_MENU_LABEL = "Транскрибировать с Talker"


def _exe_command() -> str:
    """Command string for the registry — quotes path so spaces work."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --transcribe "%1"'
    # When running from source: pythonw.exe + main.py
    main_py = Path(__file__).parent / "main.py"
    py = Path(sys.executable).with_name("pythonw.exe")
    if not py.exists():
        py = Path(sys.executable)
    return f'"{py}" "{main_py}" --transcribe "%1"'


def register_explorer_menu() -> tuple[bool, str]:
    """Register the right-click entry for every extension in SUPPORTED_EXTS.
    Returns (success, message)."""
    import winreg
    cmd = _exe_command()
    try:
        for ext in SUPPORTED_EXTS:
            base = rf"SystemFileAssociations\{ext}\shell\{_MENU_VERB}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{base}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _MENU_LABEL)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{base}\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
        return True, f"Зарегистрировано для {len(SUPPORTED_EXTS)} типов файлов."
    except Exception as e:
        logger.exception("Explorer menu registration failed")
        return False, f"Ошибка: {e}"


def unregister_explorer_menu() -> tuple[bool, str]:
    import winreg
    removed = 0
    for ext in SUPPORTED_EXTS:
        base = rf"Software\Classes\SystemFileAssociations\{ext}\shell\{_MENU_VERB}"
        try:
            # Delete command subkey first
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + r"\command")
            except FileNotFoundError:
                pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)
                removed += 1
            except FileNotFoundError:
                pass
        except Exception as e:
            logger.warning(f"Could not remove {base}: {e}")
    return True, f"Удалено: {removed} типов."


def is_explorer_menu_registered() -> bool:
    """True if at least one supported extension has our verb."""
    import winreg
    for ext in SUPPORTED_EXTS:
        base = rf"Software\Classes\SystemFileAssociations\{ext}\shell\{_MENU_VERB}"
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_READ).Close()
            return True
        except OSError:
            continue
    return False
