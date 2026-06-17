# 11. SRT / VTT / JSON Export

> Расшифровки из History и отдельные транскрипты файлов можно экспортировать с таймштампами в форматы для видеоредакторов и других тулов.

**Категория:** Tier 3 — easy win, расширяет аудиторию (юзеры субтитров).
**Готовность концепта:** 🟢 High.

---

## Зачем

Сейчас History → Export даёт plain text. Этого мало для:

- Субтитров к видео (нужен SRT/VTT).
- Импорта в DAW (SRT/JSON с word-level).
- Интеграции с другими тулами (JSON).
- Файловой транскрипции (концепт 12) — там SRT основной выход.

faster-whisper уже возвращает `segments` с `start`, `end`, опционально `words`. Просто оборачиваем в форматы.

---

## Технический подход

### Whisper segments → SRT

```python
def segments_to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _format_srt_time(seg.start)
        end = _format_srt_time(seg.end)
        lines.append(f"{i}\n{start} --> {end}\n{seg.text.strip()}\n")
    return "\n".join(lines)

def _format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
```

### VTT — то же самое, разделитель `.` вместо `,` + заголовок `WEBVTT`.

### JSON

```python
{
  "language": "ru",
  "duration": 123.45,
  "segments": [
    {"start": 0.0, "end": 2.3, "text": "Привет, как дела?",
     "words": [{"start": 0.0, "end": 0.5, "word": "Привет"}, ...]}
  ]
}
```

### History export

В Talker history хранится `[(timestamp, text)]` — без сегментов. Это значит для **history items** только plain text возможен.

Чтобы появились SRT-сегменты, нужно:
- При диктовке сохранять сегменты в history (изменение `HistoryEntry`).
- ИЛИ — SRT доступен только для file-mode (концепт 12), где у нас полные сегменты в памяти.

**Решение v1:** SRT/VTT/JSON только для file-mode. History — plain text (как сейчас).

**Решение v2:** Расширить `HistoryEntry`:

```python
class HistoryEntry(TypedDict):
    timestamp: str
    text: str
    segments: list[dict] | None   # optional, present if available
    audio_path: str | None        # if user opted in to keep audio
```

### UI

В HistoryWindow — кнопка «Экспорт» открывает диалог с выбором формата (Text / SRT / VTT / JSON). Если выбрано SRT/VTT/JSON и у entries нет сегментов — предупреждение «эти записи без таймштампов, будет plain text».

В File-mode (концепт 12) — после транскрипции диалог сохранения с выбором формата.

---

## Архитектура

**Новые модули:**
- `exporters.py` — функции `to_srt`, `to_vtt`, `to_json`, `to_text`.

**Изменения:**
- `history_mgr.py`:
  - Опционально хранить сегменты в HistoryEntry.
  - Метод `export(format: str) -> str`.
- `ui.py:HistoryWindow._export` — расширить с диалогом выбора формата.
- (для концепта 12) — в file-mode сохранение результата с выбором формата.

---

## Зависимости

Никаких новых.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| История без сегментов, экспорт SRT | Делаем "fake" SRT с одним блоком: `00:00:00 → 00:00:01 \n полный текст`. Это валидный SRT, но без таймштампов. Альтернатива — отказать с понятным сообщением. |
| Текст с переносами строк в text | В SRT — разрешено, оставляем как есть. |
| Очень длинная запись (10 минут) | Размер SRT ~50 KB. Не проблема. |
| Кириллица в file path | Пишем с `encoding="utf-8"`. |
| Сегменты с пустым text (Whisper иногда даёт) | Пропускаем. |

---

## Acceptance criteria

- В History → Export можно выбрать формат: Text / SRT / VTT / JSON.
- Полученный файл валиден (открывается в VLC для SRT, импортируется в DaVinci Resolve).
- Без сегментов в записях — корректный фолбэк.

---

## Сложность

- ~2 часа, ~100 LOC.
- Самая простая фича.

---

## Открытые вопросы

- Поддержать FCPXML для Final Cut Pro? — bonus, мелочь, +50 LOC. v2.
- Word-level timestamps — поддерживаются faster-whisper через `word_timestamps=True`, но **в 2× медленнее**. Опция в Settings для file-mode.

---

## Источники

- [SubRip (SRT) spec](https://en.wikipedia.org/wiki/SubRip)
- [WebVTT spec](https://www.w3.org/TR/webvtt1/)
- [faster-whisper word timestamps](https://github.com/SYSTRAN/faster-whisper#word-level-timestamps)
