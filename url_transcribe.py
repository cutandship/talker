"""Transcribe audio from any URL supported by yt-dlp (1000+ sites).

Lazy import: yt-dlp is only required when this is actually invoked. Talker
itself doesn't depend on it.

See concept/13_youtube_transcribe.md.
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_supported_url(s: str) -> bool:
    """Cheap heuristic for the UI. yt-dlp is what actually decides whether
    a URL can be downloaded."""
    return bool(s and _URL_RE.match(s.strip()))


def _cleanup_partials(target: Path) -> None:
    """Remove yt-dlp's leftover partial files (.part/.ytdl) so a failed download
    doesn't accumulate junk in the shared temp dir."""
    for pat in ("*.part", "*.ytdl"):
        for p in target.glob(pat):
            try:
                p.unlink()
            except Exception:
                pass


def download_audio(url: str, dest_dir: Path | None = None,
                   on_progress: Callable[[float, str], None] | None = None
                   ) -> Path:
    """Download audio-only stream of `url` and return the resulting file path.

    Uses yt-dlp's `bestaudio/best` selector. We deliberately skip ffmpeg
    post-processing (libav inside faster-whisper handles webm/opus/m4a
    natively), so users don't need ffmpeg installed.

    `on_progress(fraction, label)` is called from yt-dlp's progress hook —
    fraction ∈ [0,1] when known, else 0.0. Label is a short status string.

    Raises RuntimeError with a friendly message if yt-dlp isn't installed.
    """
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp не установлен. Чтобы транскрибировать URL:\n"
            "    pip install yt-dlp\n"
            "и перезапусти Talker."
        ) from e

    target = dest_dir or Path(tempfile.gettempdir()) / "talker_url"
    target.mkdir(parents=True, exist_ok=True)

    captured_path: dict[str, str] = {}

    def _hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            if on_progress:
                try:
                    on_progress(frac, "Скачиваю аудио…")
                except Exception:
                    pass
        elif status == "finished":
            captured_path["path"] = d.get("filename", "")
            if on_progress:
                try:
                    on_progress(1.0, "Скачано")
                except Exception:
                    pass

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(target / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        # Don't try to convert to mp3 — saves the ffmpeg dependency.
        "postprocessors": [],
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            _cleanup_partials(target)
            raise RuntimeError(f"yt-dlp не смог скачать: {e}") from e

    # A successful "finished" hook gives us the real path even when extract_info
    # returns None (yt-dlp couldn't build full info but the file IS on disk).
    # So only require `info` for the fallback paths that actually dereference it.
    if captured_path.get("path"):
        result = Path(captured_path["path"])
    elif info is not None:
        # Fallback to deriving the filename from info
        result = Path(ydl.prepare_filename(info))
    else:
        raise RuntimeError("yt-dlp не вернул информацию о видео")

    if not result.exists():
        # yt-dlp sometimes renames extensions during merge; pick newest in dir.
        glob_id = info.get("id", "*") if info else "*"
        candidates = sorted(target.glob(f"{glob_id}*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise RuntimeError("Файл скачался, но не найден на диске")
        result = candidates[0]

    logger.info(f"Downloaded audio: {result} ({result.stat().st_size // 1024} KB)")
    return result


def transcribe_url(url: str, output_path: Path, fmt: str = "txt",
                   on_progress: Callable[[float, str], None] | None = None,
                   cleanup_temp: bool = True) -> Path:
    """Full download → transcribe → write pipeline. Returns `output_path`.

    Phases:
      0.0–0.5: downloading via yt-dlp
      0.5–1.0: transcription via faster-whisper (using file_mode)
    """
    # Phase 1: download
    def _dl_progress(frac: float, label: str) -> None:
        if on_progress:
            on_progress(frac * 0.5, label)

    audio_path = download_audio(url, on_progress=_dl_progress)

    # Phase 2: transcribe via file_mode helper
    from config import load_config
    from file_mode import _transcribe, _write_output

    cfg = load_config()

    def _tx_progress(done_sec: float, total_sec: float, text: str) -> None:
        if on_progress and total_sec > 0:
            frac = 0.5 + 0.5 * (done_sec / total_sec)
            on_progress(min(0.99, frac),
                        f"Транскрибирую {done_sec:.0f}/{total_sec:.0f}с")

    segs = _transcribe(audio_path, cfg, on_progress=_tx_progress)
    _write_output(segs, output_path, fmt)

    if cleanup_temp:
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(f"Could not delete temp {audio_path}")

    if on_progress:
        on_progress(1.0, f"Готово: {output_path.name}")
    return output_path
