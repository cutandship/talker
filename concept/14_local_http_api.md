# 14. Local HTTP / WebSocket API

> Talker открывает локальный endpoint на 127.0.0.1:NNNN. Другие тулы (Raycast, vim plugin, browser extension, скрипты) могут триггерить транскрипцию или получать live partials.

**Категория:** Tier 3 — для power-users и интеграций.
**Готовность концепта:** 🟢 High.

---

## Зачем

- Skрипт на Python хочет транскрибировать аудио → POST файл, получить текст.
- Vim plugin хочет диктовать в insert-mode → WebSocket subscribe на partials.
- Raycast/PowerToys: хоткей "транскрибируй последние 5 секунд из буфера обмена".
- Voice автоматизация Home Assistant.

Это превращает Talker из закрытого tray-app в **локальный сервис**.

---

## Технический подход

### Stack

**FastAPI + uvicorn** — стандарт для лёгких локальных API. Async, OpenAPI docs из коробки.

Alternative: `aiohttp` (легче), но FastAPI удобнее со swagger.

### Endpoints

```
GET  /health                    → {"status": "ok", "model": "small", "version": "0.5"}
GET  /history?limit=10          → list of HistoryEntry
POST /transcribe                → multipart audio file → text
POST /transcribe/raw            → raw PCM bytes → text
POST /clean                     → {text, prompt?} → cleaned text (LLM cleanup chain)
POST /command                   → {selection, command} → result text
WS   /stream                    → live audio stream → partial + final transcripts
POST /dictate/start             → программно стартовать push-to-talk
POST /dictate/stop              → программно остановить
GET  /modes                     → list of per-app modes (концепт 07)
POST /vocabulary                → add words to dictionary (концепт 05)
```

### Auth

Локальный API, но всё равно нужна минимальная защита (другие приложения могут увидеть открытый порт):

- Token-based: при первом запуске Talker генерит `~/.talker/api_token`. Все запросы должны иметь `Authorization: Bearer <token>`.
- Bind на `127.0.0.1` only — недоступно извне.

Минимальный код:

```python
from fastapi import FastAPI, Depends, HTTPException, Header

app = FastAPI()

def verify_token(authorization: str = Header(None)):
    expected = read_token_file()
    if authorization != f"Bearer {expected}":
        raise HTTPException(401)

@app.post("/transcribe", dependencies=[Depends(verify_token)])
async def transcribe(file: UploadFile):
    ...
```

### WebSocket /stream

```python
@app.websocket("/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    
    streamer = StreamingTranscriber(...)
    
    @streamer.on_partial
    async def push_partial(text):
        await ws.send_json({"type": "partial", "text": text})
    
    @streamer.on_final
    async def push_final(text):
        await ws.send_json({"type": "final", "text": text})
    
    while True:
        chunk = await ws.receive_bytes()
        if not chunk:
            break
        streamer.feed(chunk)
```

Это позволяет внешним клиентам реализовывать диктовку в их любимом редакторе.

### Запуск

API сервер стартует в отдельном потоке при старте Talker, если в конфиге `[api] enabled = true`.

```python
def start_api_server(app, host="127.0.0.1", port=7869):
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
```

### UI

В Settings → секция «Local API»:

```
[ ] Включить локальный API
    URL: http://127.0.0.1:7869
    Token: ******** [Скопировать] [Перегенерировать]
    Документация: http://127.0.0.1:7869/docs
```

---

## Архитектура

**Новые модули:**
- `api/server.py` — FastAPI app, эндпоинты.
- `api/auth.py` — token management.

**Изменения:**
- `main.py`:
  - При старте — запуск API сервера если включён.
  - API получает ссылку на `App` (через DI) для доступа к транскрайберу, cleaner, history.
- `config.py`:
  - `ApiConfig { enabled: bool, port: int }`.
- `ui.py`:
  - Секция в Settings.

---

## Зависимости

```
fastapi>=0.110
uvicorn[standard]>=0.27
python-multipart>=0.0.9    # для UploadFile
```

~10 MB к exe.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Порт занят | На старте — попробовать +1, +2, +3 до 10 раз. В UI показать актуальный порт. |
| Несколько клиентов одновременно | Транскрайбер — singleton, можно ставить запросы в очередь. Concurrent requests — sequential processing. |
| WebSocket клиент отвалился во время стрима | Streamer останавливается на этом ws, ресурсы освобождаются. |
| API триггерит dictate/start, но юзер уже зажал хоткей | Конкурентный запрос игнорируется, в API ответ 409 Conflict. |
| Token утёк (например файл прочитан другим приложением) | Кнопка «Перегенерировать» в UI. |

---

## Acceptance criteria

- `curl -H "Authorization: Bearer $TOKEN" http://localhost:7869/health` → 200 OK.
- POST audio file → транскрипт в response.
- WebSocket клиент получает partials и финал.
- OpenAPI docs доступны на `/docs`.
- При отключении в Settings — сервер останавливается без перезапуска приложения.

---

## Сложность

- ~6–8 часов, ~400 LOC.
- 2 часа — base endpoints.
- 2 часа — WebSocket стриминг (зависит от концепта 01).
- 1 час — auth.
- 1 час — UI настройки.

---

## Открытые вопросы

- mTLS вместо bearer token? — overkill для local-only.
- OpenAI-compatible API (`/v1/audio/transcriptions`)? — да, очень удобно для существующих SDK. Включить в v1.
- CORS для browser extensions? — да, для `127.0.0.1` / `localhost` whitelist.

---

## Источники

- [FastAPI](https://fastapi.tiangolo.com/)
- [WhisperLive REST/WS API design](https://github.com/collabora/WhisperLive)
- [whisper.cpp HTTP server](https://github.com/ggerganov/whisper.cpp/tree/master/examples/server)
- [OpenAI audio API spec](https://platform.openai.com/docs/api-reference/audio)
