"""Веб-версия окон «Настройки» и «История».

Зачем: Tk/customtkinter версии были переусложнены (пребилды, парковка за
экраном, GC-хаки, чанк-рендер) и всё равно мылились при перетаскивании окна
(Tk не перерисовывается на смену DPI) и тормозили на поиске (перестройка сотен
виджетов). Браузерный движок решает всё это бесплатно: идеальный per-monitor
DPI, мгновенный поиск фильтрацией массива, а само окно — один HTML-файл
(web_ui.html). Зависимостей ноль: страницу отдаёт уже существующий локальный
API-сервер (fastapi/uvicorn), открывается она отдельным окном Edge --app.

Безопасность: всё на 127.0.0.1; /ui/api/* требуют тот же bearer-токен, что и
остальной API (страница получает его один раз через ?token=, прячет в
localStorage и срезает из адреса).
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import App

logger = logging.getLogger(__name__)

HTML_PATH = Path(__file__).parent / "web_ui.html"

# Cap file-upload transcription: local single-user API, but a huge POST
# shouldn't be buffered into RAM unbounded. Generous — a long meeting is legit.
_MAX_UPLOAD_BYTES = 500 * (1 << 20)   # 500 MB

# Дублируют SettingsWindow._WAKE_LEVELS/_STOP_LEVELS (1=строже … 5=ловит легче).
WAKE_LEVELS = {1: 0.85, 2: 0.75, 3: 0.65, 4: 0.55, 5: 0.45}
STOP_LEVELS = {1: 0.92, 2: 0.87, 3: 0.82, 4: 0.77, 5: 0.72}

FIXED_MODEL = {
    "whisper": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "gigaam":  "gigaam-v3-e2e-rnnt",
}


def _nearest_level(value, table: dict) -> int:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 3
    return min(table, key=lambda k: abs(table[k] - v))


def register_ui_routes(api, app: "App", token: str) -> None:
    """Повесить /ui и /ui/api/* на уже созданное FastAPI-приложение."""
    from fastapi import Depends, File, Header, HTTPException, Query, UploadFile
    from fastapi.responses import HTMLResponse, PlainTextResponse, Response

    # `from __future__ import annotations` (вверху файла) превращает аннотацию
    # `file: UploadFile` в строку; FastAPI/pydantic разрешает её по globals
    # МОДУЛЯ, а UploadFile импортирован локально. Прокидываем в globals, иначе
    # /ui/api/transcribe_file падает 500 («UploadFile is not fully defined»).
    globals()["UploadFile"] = UploadFile

    def verify(authorization: str | None = Header(default=None),
               t: str | None = Query(default=None, alias="token")) -> None:
        # Токен либо в заголовке (fetch из JS), либо в query (?token=) — query
        # нужен навигационным загрузкам (экспорт файла), где заголовок не задать.
        if authorization == f"Bearer {token}" or t == token:
            return
        raise HTTPException(status_code=401, detail="invalid token")

    @api.get("/ui")
    def _ui_page():
        # Сам HTML без данных — токен нужен только его API-вызовам.
        try:
            return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))
        except OSError:
            return PlainTextResponse("web_ui.html not found", status_code=500)

    @api.get("/ui/api/state", dependencies=[Depends(verify)])
    def _state():
        from config import load_config
        import sounds
        cfg = load_config()
        try:
            from ui.common import _autostart_get
            autostart = bool(_autostart_get())
        except Exception:
            logger.debug("autostart probe failed", exc_info=True)
            autostart = False
        try:
            from ui.common import _get_mic_devices
            mics = [{"index": i, "name": n} for i, n in _get_mic_devices()]
        except Exception:
            logger.exception("mic list failed")
            mics = []
        palette = {ev: sounds.variant_names(ev) for ev in sounds.PALETTE}
        d = dataclasses.asdict(cfg)
        # Чувствительность голосового стопа — в уровнях 1..5, как в форме.
        d["_wake_level"] = _nearest_level(cfg.wake.threshold, WAKE_LEVELS)
        d["_stop_level"] = _nearest_level(cfg.wake.stop_fuzzy, STOP_LEVELS)
        try:
            from constants import APP_VERSION
        except ImportError:
            APP_VERSION = ""
        try:
            whisper = app.whisper_status()
        except Exception:
            whisper = {}
        return {"config": d, "mics": mics, "autostart": autostart,
                "sound_palette": palette, "version": APP_VERSION,
                "history_count": len(app.history.entries()),
                "whisper": whisper}

    @api.post("/ui/api/config", dependencies=[Depends(verify)])
    def _save(payload: dict):
        cfg = _apply_ui_config(app, payload)
        # Применение трогает Tk (пилюля) и перезапускает хуки — маршалим на
        # Tk-поток, uvicorn-поток для этого не годится.
        if app.root is not None:
            app.root.after(0, lambda: app._on_settings_saved(cfg))
        return {"ok": True}

    @api.post("/ui/api/download_whisper", dependencies=[Depends(verify)])
    def _download_whisper():
        """Кнопка «Скачать Whisper»: тянет модель в плоскую папку (без HF-симлинков)
        с прогрессом. Сама загрузка — в фоновом потоке; клиент опрашивает
        /ui/api/state (поле whisper) для процентов."""
        started = app.start_whisper_download()
        return {"ok": True, "started": started, "whisper": app.whisper_status()}

    @api.post("/ui/api/widget_preview", dependencies=[Depends(verify)])
    def _widget_preview(payload: dict):
        """Живой предпросмотр виджета (размер/прозрачность/подпись/подсветка)
        прямо при движении ползунка в /ui — БЕЗ записи на диск. Переиспользуем
        app._apply_widget_live (он сам маршалит на Tk-поток)."""
        from types import SimpleNamespace
        w = app.config.widget
        def _pick(key, attr, cast):
            try: return cast(payload[key])
            except Exception: return getattr(w, attr)
        ns = SimpleNamespace(
            scale=_pick("scale", "scale", float),
            opacity=_pick("opacity", "opacity", float),
            show_listening_label=_pick("label", "show_listening_label", bool),
            show_glow=_pick("glow", "show_glow", bool),
        )
        if app.root is not None:
            app._apply_widget_live(SimpleNamespace(widget=ns))
        return {"ok": True}

    @api.get("/ui/api/history", dependencies=[Depends(verify)])
    def _hist():
        return {"entries": app.history.entries()}

    @api.post("/ui/api/history/clear", dependencies=[Depends(verify)])
    def _hist_clear():
        app.history.clear()
        return {"ok": True}

    @api.get("/ui/api/history/export", dependencies=[Depends(verify)])
    def _hist_export(fmt: str = "txt"):
        import exporters
        entries = app.history.entries()
        if fmt == "srt":
            body, mime = exporters.history_to_pseudo_srt(entries), "text/plain"
        elif fmt == "vtt":
            body = exporters.to_vtt([
                {"start": i * 2.5, "end": i * 2.5 + 2.0, "text": e.get("text", "")}
                for i, e in enumerate(entries) if e.get("text", "").strip()])
            mime = "text/vtt"
        elif fmt == "json":
            body, mime = exporters.to_json(entries), "application/json"
        else:
            fmt, body, mime = "txt", app.history.export_text(), "text/plain"
        return Response(
            content=body, media_type=f"{mime}; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="talker_history.{fmt}"'})

    # Загрузка аудио/видео файла → текст. Тот же кросс-движковый путь, что и
    # «Транскрибировать аудиофайл» в трее (decode_audio → transcriber.transcribe),
    # поэтому работает и на GigaAM (у него model=None — путь model.transcribe не
    # годится). multipart требует python-multipart; без него гасим только этот
    # маршрут, а не все веб-окна.
    try:
        @api.post("/ui/api/transcribe_file", dependencies=[Depends(verify)])
        async def _transcribe_file(file: UploadFile = File(...)):
            if app.transcriber is None:
                raise HTTPException(503, "Модель ещё грузится — подожди пару секунд")
            data = bytearray()
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, f"Файл слишком большой (макс {_MAX_UPLOAD_BYTES >> 20} МБ)")
            if not data:
                raise HTTPException(400, "Пустой файл")
            # Декод + распознавание — блокирующие и тяжёлые: уводим в пул, чтобы
            # не вешать event loop (иначе подвисает поллинг уровня микрофона и т.п.).
            from fastapi.concurrency import run_in_threadpool
            text = await run_in_threadpool(
                _transcribe_upload, app, bytes(data), file.filename or "audio")
            return {"text": text}
    except RuntimeError as e:
        logger.warning(f"/ui/api/transcribe_file disabled (need python-multipart): {e}")

    @api.post("/ui/api/mictest", dependencies=[Depends(verify)])
    def _mictest(payload: dict):
        on = bool(payload.get("on"))
        try:
            if on:
                app._mic_monitor.start(int(payload.get("mic_index", -1)))
            else:
                app._mic_monitor.stop()
            return {"ok": True}
        except Exception as e:
            logger.exception("mic test toggle failed")
            raise HTTPException(500, str(e))

    @api.get("/ui/api/miclevel", dependencies=[Depends(verify)])
    def _miclevel():
        try:
            return {"rms": float(app._mic_monitor.current_rms)}
        except Exception:
            return {"rms": 0.0}

    @api.post("/ui/api/sound_test", dependencies=[Depends(verify)])
    def _sound_test(payload: dict):
        import sounds
        event = str(payload.get("event", ""))
        name = str(payload.get("name", ""))
        if event not in sounds.PALETTE:
            raise HTTPException(400, "unknown event")
        from config import load_config
        saved = getattr(load_config().sounds, event, "")
        try:
            app._snd.set_variant(event, name)
            app._snd.play(event)
        finally:
            try:
                app._snd.set_variant(event, saved)
            except Exception:
                logger.debug("sound variant restore failed", exc_info=True)
        return {"ok": True}

    @api.post("/ui/api/autostart", dependencies=[Depends(verify)])
    def _autostart(payload: dict):
        from ui.common import _autostart_set
        _autostart_set(bool(payload.get("enabled")))
        return {"ok": True}

    @api.post("/ui/api/hotkey_capture", dependencies=[Depends(verify)])
    def _hk_capture(payload: dict):
        # На время захвата новой PTT-клавиши в браузере глушим глобальные хуки,
        # иначе нажатие будущего хоткея тут же запустит запись.
        if bool(payload.get("active")):
            app._hooks_pause()
        else:
            app._register_hooks()
        return {"ok": True}

    logger.info("Web UI routes registered (/ui)")


def _transcribe_upload(app: "App", data: bytes, filename: str) -> str:
    """Декодировать загруженные байты (любой формат libav) в 16 кГц mono PCM и
    прогнать ЗАГРУЖЕННЫМ движком. Через decode_audio + transcriber.transcribe —
    работает и на Whisper, и на GigaAM. Результат кладём в Историю, как файл-режим
    в трее."""
    import io

    from faster_whisper.audio import decode_audio  # PyAV-декодер, без модели
    from constants import SAMPLE_RATE

    tr = app.transcriber
    if tr is None:
        raise RuntimeError("transcriber not ready")
    audio = decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE)
    text = (tr.transcribe(audio) or "").strip()
    if text:
        try:
            app.history.append(text)
        except Exception:
            logger.debug("transcribe_file: history append failed", exc_info=True)
    logger.info(f"Web UI: file transcribed ({filename}) → {len(text)} chars")
    return text


def _apply_ui_config(app: "App", p: dict) -> "object":
    """Поля формы → СВЕЖИЙ конфиг с диска (merge: чужие поля не трогаем),
    сохранить, вернуть cfg. Никаких слепых setattr из клиента — только
    известные поля с валидацией."""
    from config import (load_config, save_config, ReplacementConfig,
                        VoiceCommandConfig)

    cfg = load_config()

    def fnum(key, lo, hi, cur):
        try:
            return max(lo, min(hi, float(p[key])))
        except (KeyError, TypeError, ValueError):
            return cur

    def inum(key, lo, hi, cur):
        try:
            return max(lo, min(hi, int(p[key])))
        except (KeyError, TypeError, ValueError):
            return cur

    def fbool(key, cur):
        v = p.get(key)
        return bool(v) if isinstance(v, bool) else cur

    def fstr(key, allowed, cur):
        v = p.get(key)
        return v if isinstance(v, str) and v in allowed else cur

    key = p.get("hotkey_key")
    if isinstance(key, str) and key.strip():
        cfg.hotkey.key = key.strip()[:40]

    engine = fstr("engine", ("whisper", "gigaam"), cfg.stt.engine)
    cfg.stt.engine = engine
    if engine == "gigaam":
        cfg.stt.gigaam_model = FIXED_MODEL["gigaam"]
        cfg.stt.language = "ru"               # GigaAM — только русский
    else:
        cfg.stt.model = FIXED_MODEL["whisper"]
        lang = p.get("language")
        if isinstance(lang, str) and len(lang) <= 5:
            cfg.stt.language = lang
    cfg.stt.device = fstr("device", ("cpu", "cuda", "auto"), cfg.stt.device)

    o = cfg.output
    o.restore_clipboard = fbool("restore_clipboard", o.restore_clipboard)
    o.copy_to_clipboard = fbool("copy_to_clipboard", o.copy_to_clipboard)
    o.show_bubble = fbool("show_bubble", o.show_bubble)
    o.smart_format = fbool("smart_format", o.smart_format)
    o.voice_commands = fbool("voice_commands_enabled", o.voice_commands)
    o.voice_gate = fbool("voice_gate", o.voice_gate)
    o.injection_mode = fstr("injection_mode",
                            ("auto", "uia", "sendinput", "clipboard"),
                            o.injection_mode)
    o.number_format = fbool("number_format", o.number_format)
    o.mask_profanity = fbool("mask_profanity", o.mask_profanity)
    o.remove_fillers = fbool("remove_fillers", o.remove_fillers)

    lvl = inum("wake_level", 1, 5, _nearest_level(cfg.wake.threshold, WAKE_LEVELS))
    cfg.wake.threshold = WAKE_LEVELS[lvl]
    lvl = inum("stop_level", 1, 5, _nearest_level(cfg.wake.stop_fuzzy, STOP_LEVELS))
    cfg.wake.stop_fuzzy = STOP_LEVELS[lvl]

    a = cfg.audio
    a.mic_index = inum("mic_index", -1, 256, a.mic_index)
    a.source = fstr("source", ("mic", "system"), a.source)
    a.normalize = fbool("normalize", a.normalize)
    a.noise_reduction = fbool("noise_reduction", a.noise_reduction)
    a.duck_other_apps = fbool("duck_other_apps", a.duck_other_apps)
    a.duck_level = fnum("duck_level", 0.0, 1.0, a.duck_level)

    cfg.continuous.media_guard = fbool("media_guard", cfg.continuous.media_guard)

    h = cfg.history
    h.max_entries = inum("hist_max", 0, 1_000_000, h.max_entries)
    h.retention_days = inum("hist_days", 0, 100_000, h.retention_days)
    h.on_quit_clear = fbool("hist_clear_on_quit", h.on_quit_clear)

    cfg.ui.theme = fstr("theme", ("dark", "light", "system"), cfg.ui.theme)
    w = cfg.widget
    w.scale = fnum("widget_scale", 0.3, 3.0, w.scale)
    w.opacity = fnum("widget_opacity", 0.1, 1.0, w.opacity)
    w.show_listening_label = fbool("widget_label", w.show_listening_label)
    w.show_glow = fbool("widget_glow", w.show_glow)

    snd = p.get("sounds")
    if isinstance(snd, dict):
        import sounds as _sounds
        s = cfg.sounds
        s.enabled = bool(snd.get("enabled", s.enabled))
        try:
            s.volume = max(0.0, min(1.0, float(snd.get("volume", s.volume))))
        except (TypeError, ValueError):
            pass
        for ev in ("start", "stop", "pre_stop", "empty"):
            name = snd.get(ev)
            if isinstance(name, str) and name in _sounds.variant_names(ev):
                setattr(s, ev, name)

    repl = p.get("replacements")
    if isinstance(repl, list):
        prev = {r.to: r for r in cfg.replacements}
        out = []
        for r in repl[:500]:
            if not isinstance(r, dict):
                continue
            to = str(r.get("to", "")).strip()
            froms = [str(x).strip() for x in (r.get("from") or []) if str(x).strip()]
            if to and froms:
                old = prev.get(to)
                out.append(ReplacementConfig(
                    to=to, from_=froms,
                    sounds=(old.sounds if old else ""),
                    phonetic=(old.phonetic if old else False)))
        cfg.replacements = out

    vocab = p.get("vocabulary")
    if isinstance(vocab, list):
        seen: set[str] = set()
        words: list[str] = []
        for wd in vocab[:2000]:
            wd = str(wd).strip()
            if wd and wd.lower() not in seen:
                seen.add(wd.lower())
                words.append(wd)
        cfg.vocabulary.words = words

    vcs = p.get("voice_commands")
    if isinstance(vcs, list):
        out = []
        for v in vcs[:500]:
            if not isinstance(v, dict):
                continue
            phrase = str(v.get("phrase", "")).strip()
            action = v.get("action") if v.get("action") in ("insert", "key") else "insert"
            value = str(v.get("value", ""))
            if phrase and value:
                out.append(VoiceCommandConfig(phrase=phrase, action=action,
                                              value=value))
        cfg.voice_commands = out

    save_config(cfg)
    logger.info("Web UI: config saved")
    return cfg
