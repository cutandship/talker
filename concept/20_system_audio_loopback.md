# 20. System audio loopback (meeting mode)

> Talker умеет писать **системный звук** через WASAPI loopback — то, что играет в наушниках. Транскрибирует Zoom/Teams/Meet без виртуального аудио-кабеля.

**Категория:** Tier 4 — расширение в meeting-режим, новая аудитория.
**Готовность концепта:** 🟡 Medium — нужен PyAudioWPatch и осторожный mic+system mixing.

---

## Зачем

Сейчас Talker слушает только microphone. Митинг в Zoom: микрофон ловит только твою речь, голос собеседника — нет.

Решения:
- Виртуальный аудио-кабель (VB-Cable) — каждому юзеру отдельно настраивать.
- Loopback (WASAPI / PulseAudio) — нативно в OS, без сторонних драйверов.
- Loopback = снять то, что играет в speakers, и считать input.

Это превращает Talker в **митинг-транскрайбер** с минимальными усилиями.

---

## Технический подход

### Stack

**PyAudioWPatch** — fork pyaudio с поддержкой WASAPI loopback на Windows. MIT, активно.

```python
import pyaudiowpatch as pyaudio

with pyaudio.PyAudio() as pa:
    # Список loopback устройств
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice", False):
            print(dev["name"])  # "Speakers (Realtek...) [Loopback]"
    
    # Stream от default output (как input!)
    default_speaker = pa.get_default_wasapi_loopback()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=default_speaker["maxInputChannels"],
        rate=int(default_speaker["defaultSampleRate"]),
        frames_per_buffer=1024,
        input=True,
        input_device_index=default_speaker["index"],
    )
```

### Modes

В Settings → новый режим записи:

- `mic_only` — текущий дефолт.
- `system_only` — только loopback.
- `mic+system` — оба, миксованные (для записи собственной речи + собеседника).

В режиме `mic+system` — два sd.InputStream, samples суммируются. Нужно нормализовать gain, иначе один заглушает другой.

### Speaker diarization (бонус)

Если есть `mic+system` — мы знаем, что mic = «я», system = «остальные». Это **дешёвая диарика** без pyannote: помечаем сегменты по источнику.

Output:

```
[Я] Спасибо за обзор, давайте продолжим.
[Собеседник] Согласен, переходим к следующему пункту.
```

Это огромная победа над честной diarization, которая тяжёлая и неточная.

### Continuous-режим по дефолту

Митинг — long-form. Push-to-talk бесполезен. По умолчанию для meeting mode включается continuous (VAD).

### Output

Митинговый транскрипт обычно нужен **в файл**, не вставка в активное окно. UI:

```
┌─ Митинг идёт ──────────────────────────── ✕ ─┐
│                                                │
│  🔴 REC  00:12:34                              │
│  Источник: Mic + System (loopback)             │
│  Активных собеседников: 2                      │
│                                                │
│  Превью:                                       │
│  [Собеседник] ...это интересный подход...     │
│  [Я] согласен                                  │
│                                                │
│  [⏸ Пауза]  [⏹ Остановить и сохранить]       │
│                                                │
│  Сохранить в: meeting_2026-05-28.srt           │
└────────────────────────────────────────────────┘
```

---

## Архитектура

**Новые модули:**
- `loopback_recorder.py`:
  - `LoopbackRecorder` — открывает WASAPI loopback stream.
  - `MixedRecorder` — миксует mic + loopback, помечает сэмплы источником.
- `meeting_mode.py`:
  - `MeetingSession` — orchestrates continuous transcription + diarization metadata + file output.

**Изменения:**
- `ui.py` — окно MeetingWindow.
- Tray menu: «Запись митинга».
- `config.py:MeetingConfig` — defaults.

---

## Зависимости

```
pyaudiowpatch>=0.2.12
```

~5 MB. Только Windows (что нам подходит — Talker only-Windows).

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Loopback недоступен (старая Windows) | Понятная ошибка, fallback на mic only. |
| WASAPI exclusive mode у другого приложения | Loopback всё равно работает (shared mode). |
| Sample rate loopback != 16 kHz | Resample через scipy / librosa перед Whisper. Лучше librosa (быстрее на короткие чанки). |
| Mic и system на разных sample rates | Resample обоих в 16 kHz, mix. |
| Эхо: микрофон ловит звук из speakers | Loopback canceling. **Не пытаемся** делать AEC — слишком сложно. Документируем: используйте наушники для best results. |
| Митинг 2+ часа | Размер файла ~700 MB (16k mono 16bit × 2 часа = ~1 GB). Стримим в файл по кускам, не держим в RAM. |
| Юзер забыл выключить, ушёл спать | Watchdog: если сегментов 0 за 30 мин — пауза. |

---

## Acceptance criteria

- В Settings есть выбор источника: mic / system / mic+system.
- При `system` режиме Zoom-звонок транскрибируется (собеседник слышен).
- В `mic+system` источник пометок [Я] / [Собеседник] в output.
- Поддерживаются основные форматы output (SRT, JSON, текст).
- Митинг можно паузить и возобновлять.

---

## Сложность

- ~10–15 часов, ~700 LOC.
- Самая большая фича из всех (после streaming).
- Нужно много testing'а на разных setup'ах.

---

## Открытые вопросы

- Honest speaker diarization через pyannote для случаев, когда mic+system нельзя разделить (Discord/Teams с одного канала)? — отдельный концепт. Тяжёлая зависимость, ~1.5 GB.
- Auto-summary митинга через LLM в конце? — отдельный концепт, легко поверх existing cleaner chain.
- Записывать аудио параллельно с транскриптом? — Опция. Полезно для re-transcription с другой моделью.

---

## Источники

- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)
- [MacWhisper meeting mode](https://macwhisper.helpscoutdocs.com/article/30-record-meetings)
- [WASAPI loopback docs (MS)](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
