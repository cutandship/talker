# 29. GigaAM v2/v3 — лучший STT-движок для русского (через onnx-asr)

> Добавить GigaAM как третий STT-движок рядом с whisper/parakeet. По независимому бенчмарку — лучшая открытая модель для русского (~8.4% WER против ~16% у Whisper-large-v3), MIT, CPU-инференс через сверхлёгкую `onnx-asr`.

**Категория:** STT-движок — самый высокий ROI для основного языка.
**Эффект:** 🔥 высокая отдача.
**Готовность концепта:** 🟡 Medium (нужен замер скорости/RAM на целевом CPU).
**Источник идеи:** [research/2026-05-29](research/2026-05-29_what-to-add-next.md) F1.

---

## Зачем

Текущие движки на русском проигрывают. По бенчмарку Alphacephei (автор Vosk) средний WER:
- **GigaAM2 CTC+LM — 8.42%** (лучший открытый)
- Whisper-large-v3 — 16.21%, v3-turbo — 16.84%
- **Parakeet TDT V3 — 16.02%** (текущий опциональный движок Talker)
- faster-whisper medium (текущий дефолт) — ещё хуже large-v3.

На AudioBooks: GigaAM 3.4 против Whisper 5.8. Это ~2–3x меньше ошибок на основном языке продукта → меньше правок руками и меньше работы LLM-очистке.

---

## Технический подход

### Библиотека onnx-asr (рекомендуется)

`onnx-asr` (istupakov) нативно грузит GigaAM v2/v3 (CTC, RNN-T, E2E с пунктуацией+ITN). Зависимости — **только** `numpy` + `onnxruntime` (без PyTorch/Transformers/FFmpeg), встроенное чтение WAV и ресемплинг. Это идеально ложится на CPU-локальную архитектуру (в отличие от Parakeet, который тянет тяжёлый NeMo).

```python
import onnx_asr
model = onnx_asr.load_model("gigaam-v2-ctc")   # или gigaam-v3-e2e-rnnt
text = model.recognize(audio_16k_mono_float32)
```

- Готовые int8-веса: `istupakov/gigaam-v2-onnx`, `gigaam-v3-onnx`. ~670 МБ диска / ~2 ГБ RAM.
- Win/Linux/Mac, x86/ARM, через onnxruntime (CPU; при наличии — CUDA/DirectML провайдер).

### Интеграция как движок

Повторяем паттерн Parakeet ([parakeet_engine.py](../parakeet_engine.py) — ленивый wrapper): новый `gigaam_engine.py`, выбор в `Transcriber` ([transcriber.py:64](../transcriber.py) — там уже `if engine == "parakeet": ...`).

```python
# transcriber.py
elif engine == "gigaam":
    from gigaam_engine import GigaamEngine
    self._gigaam = GigaamEngine(model_name=gigaam_model)
    self.model = None
```

`GigaamEngine` принимает float32 моно 16 кГц (как уже отдаёт recorder) и возвращает текст. `initial_prompt`/biasing у GigaAM нет — словарь (концепт 05) на этом движке не применяется (учесть в UI).

---

## Архитектура

- `gigaam_engine.py` — новый (ленивый импорт `onnx_asr`, как `parakeet_engine.py`).
- `transcriber.py` — ветка `engine == "gigaam"` в `__init__` ([transcriber.py:64](../transcriber.py)) и в `transcribe` ([transcriber.py:120](../transcriber.py)).
- `config.py` — `SttConfig.engine` уже строка ([config.py:101](../config.py)); добавить значение `"gigaam"` + `gigaam_model: str = "gigaam-v2-ctc"`; запись в save/load.
- `ui.py` — в выпадашке «Движок» добавить пункт GigaAM (ru-only).
- `requirements.txt` — `onnx-asr`, `onnxruntime`.

---

## Зависимости

`onnx-asr`, `onnxruntime`. Веса скачиваются с HuggingFace при первом запуске (как whisper-кэш).

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Язык не русский | GigaAM только ru → авто-fallback на whisper/parakeet по `stt.language`. UI предупреждает. |
| Нет initial_prompt | Словарь (05) и контекст (25) на GigaAM не действуют; глоссарий в очистке (23) и замена (22) — действуют (они после STT). |
| Долгая речь (>30 с) | GigaAM long-form — через внешний VAD (у Talker VAD уже есть). |
| onnxruntime не установлен | Понятная ошибка + fallback на whisper (как сейчас с NeMo). |
| Память | int8 ~2 ГБ RAM — проверить на минимальном целевом железе. |

---

## Acceptance criteria

- Выбор «GigaAM» в Settings; на русской диктовке WER заметно ниже whisper medium (smoke на 10 фразах с именами/терминами).
- Без onnxruntime — понятная ошибка, не падаем.
- Не-русский язык корректно уходит на fallback-движок.

---

## Сложность

- ~5–8 часов, ~150 LOC (новый engine + ветки + UI + requirements). Основное — формат аудио и кэш моделей.

---

## Открытые вопросы

- RTF и RAM int8 на целевом CPU — замер до коммита (🟠).
- GigaAM-v3 e2e уже даёт пунктуацию+ITN — можно ли на русском частично выключать LLM-очистку и снижать латентность?
- Лицензии/размеры конкретных v3-e2e весов для распространения с приложением.

---

## Источники

- [GigaAM (Sber, MIT)](https://github.com/salute-developers/GigaAM)
- [onnx-asr](https://github.com/istupakov/onnx-asr)
- [Бенчмарк русских моделей (Alphacephei)](https://alphacephei.com/nsh/2025/04/18/russian-models.html)
- Связано: концепт 03 (Parakeet backend), 05 (словарь — не действует на GigaAM).
