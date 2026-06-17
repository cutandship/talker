from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, TypedDict

logger = logging.getLogger(__name__)

HISTORY_PATH = Path(__file__).parent / "history.json"
DEFAULT_MAX_ENTRIES = 1000


class HistoryEntry(TypedDict, total=False):
    timestamp: str   # ISO 8601
    text: str        # что реально вставили (с форматированием, если было вкл)
    raw: str         # текст ДО голосового форматирования (для тоггла в истории);
                     # отсутствует у старых записей → трактуется как == text


class HistoryManager:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES,
                 retention_days: int = 0) -> None:
        self.max_entries = max_entries
        self.retention_days = retention_days
        self._entries: list[HistoryEntry] = []
        self._callbacks: list[Callable[[HistoryEntry | None], None]] = []
        # Serializes file writes + entry mutation: history is written from both
        # the GUI thread and the uvicorn API thread (vocab add → save). RLock so
        # append()/update_text() can call _save() while already holding it.
        self._lock = threading.RLock()
        self._load()
        self._prune()

    def set_policy(self, max_entries: int, retention_days: int) -> None:
        """Update retention policy; immediately prunes."""
        with self._lock:
            self.max_entries = max_entries
            self.retention_days = retention_days
            if self._prune():
                self._save()

    def _load(self) -> None:
        try:
            if HISTORY_PATH.exists():
                raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    # Keep only well-formed entries — a stray non-dict element
                    # (corrupt/old file) would later crash append()/export_text()/
                    # _prune on e["text"]/e["timestamp"].
                    self._entries = [
                        e for e in raw
                        if isinstance(e, dict) and "text" in e and "timestamp" in e
                    ]
        except Exception as e:
            logger.warning(f"History load failed: {e}")

    def _save(self) -> None:
        try:
            with self._lock:
                # Atomic write with a UNIQUE temp file: a fixed tmp name races
                # when two threads (GUI + API) save at once — on Windows the
                # second os.replace hits "file in use". mkstemp gives each writer
                # its own tmp in the same dir, keeping os.replace atomic.
                data = json.dumps(self._entries, ensure_ascii=False, indent=2)
                fd, tmp = tempfile.mkstemp(
                    dir=str(HISTORY_PATH.parent), suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(data)
                    os.replace(tmp, HISTORY_PATH)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        except Exception as e:
            logger.warning(f"History save failed: {e}")

    def _prune(self) -> bool:
        """Returns True if anything was removed."""
        before = len(self._entries)

        # Retention by days
        if self.retention_days and self.retention_days > 0:
            cutoff = datetime.now() - timedelta(days=self.retention_days)
            kept: list[HistoryEntry] = []
            for e in self._entries:
                try:
                    ts = datetime.fromisoformat(e["timestamp"])
                    if ts >= cutoff:
                        kept.append(e)
                except Exception:
                    # Malformed timestamps stay — we'd rather keep than drop.
                    kept.append(e)
            self._entries = kept

        # Hard cap by count (newest kept)
        if self.max_entries > 0 and len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

        removed = before - len(self._entries)
        if removed:
            logger.info(f"History pruned: removed {removed} entries")
        return removed > 0

    def append(self, text: str, raw: str | None = None) -> HistoryEntry:
        """Append an entry. `text` is what was inserted; `raw` is the text
        before voice-formatting (defaults to text when no formatting applied)."""
        entry: HistoryEntry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "text": text,
        }
        if raw is not None and raw != text:
            entry["raw"] = raw
        with self._lock:
            self._entries.append(entry)
            self._prune()
            self._save()
        for cb in self._callbacks:
            try:
                cb(entry)
            except Exception:
                pass
        return entry

    def update_text(self, timestamp: str, new_text: str) -> bool:
        """Replace the displayed `text` of the entry with this timestamp (used
        by the history UI's format/unformat toggle). Returns True if found."""
        with self._lock:
            for e in self._entries:
                if e.get("timestamp") == timestamp:
                    e["text"] = new_text
                    self._save()
                    return True
        return False

    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._save()
        for cb in self._callbacks:
            try:
                cb(None)   # None = full clear signal
            except Exception:
                pass

    def export_text(self) -> str:
        # Snapshot under the lock (like entries()) for a consistent view — a
        # concurrent append/_prune during dictation could otherwise make the
        # export skip/duplicate a row. (CPython list iteration doesn't crash on
        # concurrent mutation, so this is consistency, not a crash fix.)
        with self._lock:
            snapshot = list(self._entries)
        lines = []
        for e in snapshot:
            try:
                ts = datetime.fromisoformat(e["timestamp"]).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts = e["timestamp"]
            lines.append(f"[{ts}]\n{e['text']}")
        return "\n\n".join(lines)

    def on_new(self, cb: Callable[[HistoryEntry | None], None]) -> None:
        self._callbacks.append(cb)

    def off_new(self, cb: Callable[[HistoryEntry | None], None]) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)
