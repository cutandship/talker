# 01. Streaming partial inserts

> Текст появляется в активном поле **по мере речи**, не после отпускания клавиши.

**Категория:** Tier 1 — фундаментальный UX-сдвиг. Главное отличие Wispr Flow / AquaVoice / OpenAI Realtime от Talker.
**Готовность концепта:** 🟡 Medium — общая архитектура ясна, но требует прототипа для подбора латентности.

---

## Зачем

Сейчас юзер зажимает клавишу, говорит 10 секунд, отпускает — и **ждёт** ещё 1–3 секунды, пока хвост декодируется. Воспринимается как "программа тормозит".

Со стримингом: ты говоришь — текст уже **в поле**. Перцепция latency = 0.

Дополнительно: видя текст по ходу, юзер сам может **остановиться раньше**, если уже сказал что хотел.

---

## Технический подход

### Архитектурный выбор: своё решение vs RealtimeSTT vs WhisperLive

**RealtimeSTT** (github.com/KoljaB/RealtimeSTT, MIT) — наиболее зрелый Python-проект. Drop-in `AudioToTextRecorder` с callback на partial + final segments. Под капотом — Silero VAD + faster-whisper, периодический re-decode скользящего окна.

**WhisperLive** (collabora, Apache 2.0) — клиент-сервер на WebSocket, ориентирован на длинные сессии и мульти-клиента. Overkill для tray-app.

**Своё** — переписать `_BgJob` под streaming: каждые 300 мс брать accumulating window последних N секунд, декодировать с малым beam, выдавать partial. Каждые ~2 сек — финализировать стабильную часть.

**Выбор:** **RealtimeSTT**. Сэкономит ~500 LOC и пару недель ловли edge cases (детекция конца речи, dedup partial vs final, merge сегментов).

### Алгоритм вставки partial → final

Whisper по природе нон-стриминговый: каждый декод даёт **полный пересчёт** окна. Между partial-ами текст может **меняться** (Whisper переоценил сегмент).

Стратегия (как у Wispr):

1. Держим `committed_text` — то, что уже точно вставлено в поле.
2. Каждый partial: декодируем последние ~5 секунд, получаем `candidate_text`.
3. Находим **самый длинный общий префикс** между `committed` и `candidate`. Это "стабильная" часть.
4. Если стабильная часть длиннее `committed` — **довставляем разницу**.
5. Если в `candidate` обнаружили, что хвост `committed` оказался неверным — **удалить** через `Backspace × N`, потом довставить корректное.
6. При паузе ≥ 800 мс — финализируем сегмент, очищаем буфер.

### Вставка без поломки фокуса

Долбить Ctrl+V каждые 300 мс — плохо: clipboard перетирается, paste-листенеры в Slack/Discord звуковые. Лучше — посимвольный ввод через `keyboard.write(text, delay=0)` или Win32 `SendInput` с Unicode (`KEYEVENTF_UNICODE`).

Backspace для коррекций — `keyboard.send('backspace')` × N.

### Параметры

| Параметр | Дефолт | Зачем |
|---|---|---|
| `streaming_partial_interval_ms` | 300 | Как часто пересчитывать partial. Меньше = плавнее, больше CPU. |
| `streaming_window_sec` | 5.0 | Какой хвост аудио передаём в Whisper. |
| `streaming_commit_silence_ms` | 800 | Через сколько тишины фиксируем сегмент. |
| `streaming_min_partial_len` | 3 | Не вставлять partial короче N символов (избегаем мерцания на междометиях). |

---

## Архитектура

**Новые модули:**
- `streamer.py` — обёртка над RealtimeSTT, отдаёт events `(partial|final, text)` через callback.

**Изменения:**
- `main.py` — новый State.STREAMING, `_on_press` → `streamer.start()` если режим стриминга включён.
- `config.py` — `StreamingConfig` (включено/выключено, параметры).
- `ui.py` — FlowBar получает индикатор "стримим" (вертикальная градиентная полоса). `SettingsWindow` — чекбокс "Стриминг (текст по ходу речи)".

**Что НЕ ломаем:**
- Старый push-to-talk остаётся как опция (`streaming=false`).
- Cleanup-цепочка работает на финальном тексте, не на partial. То есть partial идёт сырой, в конце всё переписывается через LLM. Это компромисс — но иначе LLM-латентность убивает streaming.

---

## Зависимости

```
RealtimeSTT>=0.3
```

Подтянет: faster-whisper (уже есть), silero-vad (через torch).

⚠️ RealtimeSTT тянет `torch` — это +2GB к billed exe. Альтернатива: вынести стриминг в плагин, грузить по требованию.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Фокус ушёл во время речи | Partial вставляется в новое окно (фича Wispr). Бубль может показать прежний контекст. |
| Whisper переписывает уже вставленные слова | Backspace + reinsert. Юзер увидит "мерцание" — приемлемо. |
| Юзер набирает руками во время streaming | Streamer не знает об этом, продолжит вставлять. Опционально: пауза streaming на keyboard event. |
| Сетевые / GPU-лаги вызывают drift partial | Если partial отстаёт > 2 сек — пропускаем, ждём финал. |
| LLM cleanup в конце меняет финальный текст | Стираем весь сессионный output (Backspace × len), вставляем cleaned. |

---

## Acceptance criteria

- При включённом стриминге первое слово появляется в поле **≤ 500 мс** после начала речи.
- Partial → final коррекция не оставляет лишних символов.
- При отключённом стриминге поведение Talker не меняется.
- Можно переключаться без перезапуска приложения.

---

## Сложность

- ~6–10 часов работы, ~300 LOC.
- Прототип: 2 часа на RealtimeSTT integration + ручное тестирование.
- Полировка commit/rollback логики: 4 часа.
- UI/настройки: 1 час.

---

## Открытые вопросы

- Как взаимодействовать со стримингом + LLM cleanup? Варианты:
  - (a) Streaming → сырой текст, в конце LLM рерайтит всё (текущий план).
  - (b) Streaming → текст сразу финальный, без LLM (быстрее, грязнее).
  - (c) Streaming → каждые N секунд LLM cleanup на стабильной части (сложно, дорого).
  - Дефолт: (a). В Настройках — переключатель.

---

## Источники

- [RealtimeSTT GitHub](https://github.com/KoljaB/RealtimeSTT)
- [WhisperLive](https://github.com/collabora/WhisperLive)
- [Whisper Streaming (ufal)](https://github.com/ufal/whisper_streaming)
- [Wispr Flow Smart Formatting & Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack)
