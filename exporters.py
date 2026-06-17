"""Export transcripts to SRT / VTT / JSON / TXT.

See concept/11_srt_export.md.
"""
from __future__ import annotations

import json
from typing import Any, Iterable


def to_text(entries: Iterable[dict]) -> str:
    """Plain history dump (matches HistoryManager.export_text)."""
    from datetime import datetime
    lines = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["timestamp"]).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = e.get("timestamp", "")
        lines.append(f"[{ts}]\n{e.get('text', '')}")
    return "\n\n".join(lines)


def to_json(entries: Iterable[dict]) -> str:
    """Pretty JSON of the full history list."""
    return json.dumps(list(entries), ensure_ascii=False, indent=2)


def to_srt(segments: list[dict]) -> str:
    """SubRip: 1-indexed blocks of `start --> end\\n text\\n`.

    Each segment dict must have `start`, `end` (seconds) and `text` keys.
    History entries without timestamped segments aren't suitable — callers
    should detect and fall back to to_text.
    """
    out: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _srt_time(float(seg["start"]))
        end = _srt_time(float(seg["end"]))
        text = str(seg.get("text", "")).strip()
        out.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(out)


def to_vtt(segments: list[dict]) -> str:
    """WebVTT: `WEBVTT` header + blocks with `.` decimal separator."""
    out = ["WEBVTT", ""]
    for seg in segments:
        start = _vtt_time(float(seg["start"]))
        end = _vtt_time(float(seg["end"]))
        text = str(seg.get("text", "")).strip()
        out.append(f"{start} --> {end}")
        out.append(text)
        out.append("")
    return "\n".join(out)


def history_to_pseudo_srt(entries: list[dict]) -> str:
    """Fallback when history has no segments — each entry becomes a 1-second
    block stacked at successive timestamps. Valid SRT, but timestamps are not
    real. Useful so users get *some* SRT output even from text-only history.
    """
    segs = []
    t = 0.0
    for e in entries:
        text = str(e.get("text", "")).strip()
        if not text:
            continue
        segs.append({"start": t, "end": t + 2.0, "text": text})
        t += 2.5
    return to_srt(segs)


# ── time formatters ──────────────────────────────────────────────────────────

def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")
