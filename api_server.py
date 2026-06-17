"""Local-only HTTP API for external integrations.

Lazy import — FastAPI/uvicorn are pulled in only when the API is enabled. Talker
itself doesn't depend on them. The server binds to 127.0.0.1 only and requires
a bearer token (auto-generated, stored next to config).

Endpoints (v1):
    GET  /health                       — status + model info (token required)
    GET  /history?limit=N              — history dump
    POST /transcribe       (multipart) — audio file → text
    POST /transcribe_json  (json)      — {"text": ...}  no-op echo (placeholder)
    POST /clean            (json)      — {"text": ..., "system_prompt": ...}
                                          → run cleanup chain
    POST /vocabulary       (json)      — {"words": [...]} append to vocab

See concept/14_local_http_api.md.
"""
from __future__ import annotations

import io
import logging
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import App

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).parent / ".api_token"
DEFAULT_PORT = 7869
# Bound the history dump so a client can't pull an unbounded response at once;
# limit<=0 still means "everything", capped here.
_HISTORY_HARD_CAP = 10_000
# Cap uploads so a huge POST can't exhaust RAM/disk. Generous — this is a local
# 127.0.0.1 API and a long meeting recording is legitimate.
_MAX_UPLOAD_BYTES = 500 * (1 << 20)   # 500 MB


async def _read_capped(file, cap: int) -> "bytes | None":
    """Read an UploadFile in 1 MB chunks, returning None as soon as it exceeds
    `cap` bytes — so an oversized upload is rejected without buffering all of it."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _ensure_token() -> str:
    """Read or create the API bearer token file."""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    try:
        TOKEN_FILE.write_text(tok, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist API token: {e}")
    return tok


def regenerate_token() -> str:
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


def read_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


# ── Server thread ────────────────────────────────────────────────────────────

class ApiServer:
    """Wraps uvicorn so we can start/stop it from the App lifecycle."""

    def __init__(self, app: "App", host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._uvicorn = None
        self._thread: threading.Thread | None = None
        self.actual_port: int | None = None

    def start(self) -> None:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Локальный API требует fastapi + uvicorn. Установи:\n"
                "    pip install fastapi 'uvicorn[standard]' python-multipart\n"
                "и включи API ещё раз в Настройках."
            ) from e

        from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
        import uvicorn as _uv

        # `from __future__ import annotations` стрингифицирует аннотацию
        # `file: UploadFile`; FastAPI разрешает её по globals модуля, а импорт
        # локальный — без этого /transcribe падает 500. Прокидываем в globals.
        globals()["UploadFile"] = UploadFile

        token = _ensure_token()

        def verify(authorization: str | None = Header(default=None)) -> None:
            expected = f"Bearer {token}"
            if not authorization or authorization != expected:
                raise HTTPException(status_code=401, detail="invalid token")

        api = FastAPI(title="Talker local API", version="1")

        @api.get("/health", dependencies=[Depends(verify)])
        def _health():
            cfg = self._app.config
            return {
                "status": "ok",
                "engine": cfg.stt.engine,
                "model": cfg.stt.model,
                "language": cfg.stt.language,
                "vocabulary_size": len(cfg.vocabulary.words),
                "whisper_mode": self._app._whisper_mode,
            }

        @api.get("/history", dependencies=[Depends(verify)])
        def _history(limit: int = 100):
            entries = self._app.history.entries()
            if limit > 0:
                entries = entries[-min(limit, _HISTORY_HARD_CAP):]
            return {"entries": entries, "count": len(entries)}

        # multipart-загрузка требует python-multipart; без него отключаем
        # ТОЛЬКО этот эндпоинт, а не весь сервер (на нём теперь живут и
        # веб-окна Настроек/Истории).
        try:
            @api.post("/transcribe", dependencies=[Depends(verify)])
            async def _transcribe(file: UploadFile = File(...)):
                if self._app.transcriber is None:
                    raise HTTPException(503, "transcriber not ready")
                data = await _read_capped(file, _MAX_UPLOAD_BYTES)
                if data is None:
                    raise HTTPException(
                        413, f"file too large (max {_MAX_UPLOAD_BYTES // (1 << 20)} MB)")
                text = _decode_and_transcribe(self._app, data, file.filename or "audio")
                return {"text": text}
        except RuntimeError as e:
            logger.warning(f"/transcribe disabled (need python-multipart): {e}")

        class CleanReq(BaseModel):
            text: str

        @api.post("/clean", dependencies=[Depends(verify)])
        def _clean(req: CleanReq):
            # CleanerChain.clean takes only `text` now (the LLM/system_prompt
            # path was removed); passing system_prompt= raised TypeError → 500.
            result, ok = self._app.cleaner_chain.clean(req.text)
            return {"text": result, "cleaned": ok}

        class VocabReq(BaseModel):
            words: list[str]

        @api.post("/vocabulary", dependencies=[Depends(verify)])
        def _add_vocab(req: VocabReq):
            from vocabulary import normalize_words
            existing = {w.lower() for w in self._app.config.vocabulary.words}
            added = [w for w in normalize_words(req.words)
                     if w.lower() not in existing]
            self._app.config.vocabulary.words.extend(added)
            from config import save_config
            save_config(self._app.config)
            self._app._apply_runtime_overrides()
            return {"added": added, "total": len(self._app.config.vocabulary.words)}

        # ── Веб-окна «Настройки»/«История» (web_ui.py + web_ui.html) ──
        try:
            from web_ui import register_ui_routes
            register_ui_routes(api, self._app, token)
        except Exception:
            logger.exception("web UI routes failed to register")

        # ── Run uvicorn in a background thread, find a free port ──
        port = self._choose_port(self._port)
        self.actual_port = port
        # log_config=None: НЕ давать uvicorn настраивать своё логирование.
        # Его дефолтный formatter дёргает sys.stdout.isatty(), а под pythonw
        # stdout = None → «Unable to configure formatter 'default'» и сервер
        # не поднимается вовсе. Логи uvicorn идут в root → talker.log.
        cfg = _uv.Config(api, host=self._host, port=port,
                         log_level="warning", access_log=False,
                         log_config=None)
        server = _uv.Server(cfg)
        self._uvicorn = server

        def _run():
            try:
                server.run()
            except Exception:
                logger.exception("uvicorn crashed")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        logger.info(f"API server started on http://{self._host}:{port}")

    def stop(self) -> None:
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
            logger.info("API server stop requested")

    @staticmethod
    def _choose_port(preferred: int) -> int:
        import socket
        for p in range(preferred, preferred + 10):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        return preferred   # let uvicorn fail loudly


def _decode_and_transcribe(app: "App", data: bytes, filename: str) -> str:
    """Decode uploaded audio to 16 kHz mono PCM and run the LOADED engine.

    Goes through decode_audio (PyAV) + Transcriber.transcribe so it works for
    BOTH Whisper and GigaAM. The old path called app.transcriber.model.transcribe
    directly, which is None on the GigaAM engine (the production default) and
    crashed with AttributeError.
    """
    import io
    from faster_whisper.audio import decode_audio
    from constants import SAMPLE_RATE
    audio = decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE)
    return (app.transcriber.transcribe(audio) or "").strip()
