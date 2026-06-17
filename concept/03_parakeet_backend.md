# 03. NVIDIA Parakeet V3 как альтернатива Whisper

> Опционально использовать Parakeet TDT V3 вместо faster-whisper. 10× быстрее, 600M параметров vs 800M у large-v3, 25 языков (включая русский).

**Категория:** Tier 1 — решает жалобу юзера «сильные модели медленные».
**Готовность концепта:** 🟠 Research — нужен прототип и бенчмарк на русском перед интеграцией.

---

## Зачем

Юзер хочет качество medium/large, но без их латентности. Parakeet V3:

- 10× faster чем Whisper на one-shot транскрипции (по NVIDIA-бенчмаркам).
- 600M параметров — меньше памяти, меньше потребления батареи.
- TDT (Token-and-Duration Transducer) архитектура — поддерживает **streaming натив**, в отличие от Whisper (Whisper нужно re-decode'ить окно).
- Поддерживает 25 языков включая ru.

Trade-off: Whisper лучше на редких языках и узкой лексике; Parakeet быстрее на типичной речи. Для русской диктовки в LLM-чаты — Parakeet **скорее всего** выигрывает, но нужен бенчмарк.

---

## Технический подход

### Варианты интеграции

**(a) NVIDIA NeMo toolkit** — официальный путь. Тяжёлый: ~2GB зависимостей (PyTorch + NeMo). Документация есть, API стабилен.

**(b) ONNX runtime + сконвертированная Parakeet** — лёгкая интеграция, ~150MB. Нет официальной ONNX-конвертации, но в сообществе есть скрипты.

**(c) HuggingFace transformers** — `AutoModelForSpeechSeq2Seq`. Не идеально, но работает. Тяжелее ONNX, легче NeMo.

**Выбор:** прототип на (a) NeMo — самый рабочий. Если перформанс/размер критичны — мигрируем на ONNX.

### Архитектура переключения

`Transcriber` остаётся фасадом. Внутри — `_engine`:

```python
class _WhisperEngine:
    def transcribe(audio): ...

class _ParakeetEngine:
    def transcribe(audio): ...

class Transcriber:
    def __init__(self, engine: str, ...):
        if engine == "parakeet":
            self._engine = _ParakeetEngine(...)
        else:
            self._engine = _WhisperEngine(...)
```

### Конфиг

```toml
[stt]
engine = "whisper"         # "whisper" | "parakeet"
model  = "small"            # для whisper: tiny/.../large-v3; для parakeet: tdt-0.6b-v3
```

В Настройках — combobox «Движок», под ним появляется combobox «Модель» с правильным списком.

### Бенчмарк перед слиянием

Делаем `bench.py`:

1. Берём 20 русских аудио-сэмплов (твоя реальная диктовка через `history.json` + recordings, если их сохранять).
2. Прогоняем через Whisper-small/medium/large-v3 и Parakeet-v3.
3. Метрики: WER (если есть reference), wall time, RAM peak.
4. Решение по результатам.

---

## Архитектура

**Новые модули:**
- `transcriber_parakeet.py` — реализация `_ParakeetEngine`.

**Изменения:**
- `transcriber.py` — становится фасадом с выбором движка.
- `config.py` — `SttConfig.engine`, отдельный список моделей.
- `ui.py` — UI с динамическим списком моделей.

---

## Зависимости

```
nemo_toolkit[asr]>=2.0
```

⚠️ NeMo тянет PyTorch (+ CUDA libs если хотим GPU). Размер distribution: +2–3 GB.

**Решение:** Parakeet — **опциональная установка**. Не в `requirements.txt`, а через `[parakeet]` extra или отдельный install script. Talker без него работает на Whisper.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| NeMo не установлен, юзер выбрал Parakeet | Понятная ошибка в Settings: «pip install nemo_toolkit[asr]». |
| Parakeet на CPU без AVX512 | Может тормозить хуже Whisper. Бенчмарк → возможно отключаем для CPU-only машин. |
| Parakeet ест 4GB RAM | Honest disclaimer в UI. |
| Русский качество хуже ожиданий | Возвращаемся к Whisper как дефолту, Parakeet — расширенная опция. |
| Streaming-режим (концепт 01) с Parakeet | Это **большое преимущество** Parakeet TDT — нативный streaming. Стоит увязать с концептом 01. |

---

## Acceptance criteria

- В Настройках есть выбор движка (Whisper / Parakeet).
- При установленном NeMo Parakeet работает.
- Бенчмарк на 20 русских сэмплов: Parakeet быстрее Whisper-medium хотя бы в 2× при сопоставимом WER (±10%).
- Без NeMo приложение запускается, Whisper работает как раньше.

---

## Сложность

- Прототип + бенчмарк: **8–12 часов** (включая разбирательство с NeMo на Windows).
- Если бенчмарк показал «не стоит» — закрываем концепт. Иначе integration: ещё 4–6 часов.

---

## Открытые вопросы

- Поддерживается ли NeMo нормально на Windows? (Linux/Mac — да, Windows — местами проблематично с torch + CUDA.)
- ONNX-версия Parakeet появилась? Проверить на момент начала работы.
- Подходит ли Parakeet для длинных записей (>5 мин)? Whisper умеет, Parakeet — нужно проверить.

---

## Источники

- [Parakeet V3 vs Whisper benchmark](https://whispernotes.app/blog/parakeet-v3-default-mac-model)
- [NVIDIA Parakeet model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [NeMo ASR docs](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/asr/intro.html)
- [Northflank STT 2026 benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Ockham AI Whisper vs Parakeet 2](https://ockham.ai/articles/openai-whisper-vs-nvidia-parakeet-2-the-futur.html)
