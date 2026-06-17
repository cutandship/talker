# 10. Punctuation Restoration Fallback (без LLM)

> Локальная модель ставит пунктуацию и капитализацию в raw Whisper-тексте, когда LLM cleanup недоступен (offline, лимит, ошибка сети).

**Категория:** Tier 3 — повышает качество fallback-режима.
**Готовность концепта:** 🟢 High.

---

## Зачем

Сейчас цепочка `CleanerChain` при отказе всех LLM возвращает **сырой текст**. Сырой Whisper в continuous-режиме часто без точек, без капитализации первого слова: `привет как дела сегодня хорошо`. Читать неудобно.

Локальная punctuation-restoration модель (~250 MB) добавляет `.,?:` и капитализацию **офлайн, без LLM**, за ~100мс.

Это становится **новым последним звеном** цепочки cleaners — после всех LLM, перед NoopCleaner.

---

## Технический подход

### Модель

**deepmultilingualpunctuation** (HuggingFace, MIT):
- `oliverguhr/fullstop-punctuation-multilang-large`
- Поддержка: EN, DE, FR, IT.
- Размер: ~ 235 MB (на основе xlm-roberta-base).

Для русского — `RUPunct/RUPunct_big` (HF):
- HuggingFace: `kontur-ai/sbert_punc_case_ru`
- ~150 MB
- Восстанавливает пунктуацию + регистр для русского.

Загружать модель по языку (если `cfg.stt.language == "ru"` — RUPunct; иначе multilingual).

### Pipeline

```python
class PunctuationCleaner(Cleaner):
    def __init__(self, lang: str = "ru"):
        from transformers import pipeline
        if lang == "ru":
            self._pipe = pipeline("token-classification",
                                  model="kontur-ai/sbert_punc_case_ru")
        else:
            self._pipe = pipeline("token-classification",
                                  model="oliverguhr/fullstop-punctuation-multilang-large")

    def clean(self, text: str) -> str:
        # Pipeline аннотирует токены тегами PERIOD, COMMA, QUESTION, etc.
        result = self._pipe(text)
        return _apply_punctuation_tags(text, result)
```

`_apply_punctuation_tags` — пробегается по токенам, добавляет соответствующий знак.

### Место в цепочке

Сейчас в `cleaner.py:build_cleaner_chain`:

```python
[ApiCleaner, OllamaCleaner, NoopCleaner]
```

Новая цепочка:

```python
[ApiCleaner, OllamaCleaner, PunctuationCleaner, NoopCleaner]
```

Если все LLM упали — PunctuationCleaner не упадёт (локальный), даст хоть какую-то пунктуацию.

В UI Settings — чекбокс «Локальная пунктуация при недоступности LLM» (дефолт on).

### Lazy loading

Модель 150–250 MB. Загружать **только при первом обращении**, не на старте.

```python
class PunctuationCleaner(Cleaner):
    def __init__(self, lang):
        self._lang = lang
        self._pipe = None  # lazy

    def clean(self, text):
        if self._pipe is None:
            self._load()
        return self._apply(text)
```

При первой работе будет латентность ~3-5 сек на load + 100мс на inference. После — 100мс.

### Размер билда

Если включаем в default — exe растёт на ~250 MB. Лучше: **опциональная установка**.

Talker без модели → fallback на NoopCleaner.
Talker с моделью → PunctuationCleaner в цепочке.

UI: в Настройках «Установить локальную пунктуацию» → запускает `download_model.py`, который тянет с HF и складывает рядом с exe.

---

## Архитектура

**Новые модули:**
- В `cleaner.py` добавить класс `PunctuationCleaner`.
- `tools/download_punct_model.py` — отдельный скрипт.

**Изменения:**
- `cleaner.py:build_cleaner_chain` — добавляет PunctuationCleaner перед NoopCleaner если модель установлена.
- `config.py:CleanerConfig` поддерживает `type = "punctuation"`.
- `ui.py:SettingsWindow` — чекбокс + кнопка «Скачать модель пунктуации».

---

## Зависимости

```
transformers>=4.40
torch>=2.0
```

⚠️ Тянет torch ~2 GB. Это **большой** довесок.

**Альтернатива:** ONNX-конвертация `RUPunct` (50–80 MB), runtime через `onnxruntime` (50 MB). Без torch.

В таком случае:

```
onnxruntime>=1.18
tokenizers>=0.15
```

**Решение:** v1 — на ONNX runtime. Torch не тянем.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Модель не скачана | Cleaner не активен, цепочка работает как раньше. |
| Модель упала (битый файл) | Пропускаем, идём в NoopCleaner. |
| Слишком длинный текст | Модели на base-roberta имеют лимит 512 токенов. Делим на сегменты по предложениям (по нашему же VAD-разделению) и обрабатываем кусками. |
| Текст уже с пунктуацией (LLM сработал) | PunctuationCleaner в цепочке после LLM — он не активируется (LLM вернул текст, цепочка остановилась). |
| Русский + английский mix | RUPunct модель плохо на mixed. Multilingual — лучше. Можно auto-detect language → switch model. Bonus. |

---

## Acceptance criteria

- При недоступности всех LLM cleaners текст получает пунктуацию и капитализацию через локальную модель.
- Первая работа — 3–5 сек (load), потом 100–200 мс.
- Без скачанной модели Talker работает как раньше.
- В Settings есть кнопка установки/удаления модели с прогресс-баром.

---

## Сложность

- ~5–7 часов, ~250 LOC.
- ~2 часа — ONNX-конвертация модели (если ещё не сделана).
- 3 часа — integration.
- 2 часа — UI download/install.

---

## Открытые вопросы

- Есть ли готовая ONNX-версия `RUPunct`? Проверить. Если нет — конвертация через `optimum.onnxruntime`. Не страшно.
- Какой precision модели? FP32 → 200 MB. INT8 → 60 MB, потеря качества минимальна.
- Auto-detect language vs хардкод по конфигу? Дефолт: брать `cfg.stt.language` → нужная модель.

---

## Источники

- [deepmultilingualpunctuation](https://github.com/oliverguhr/deepmultilingualpunctuation)
- [RUPunct on HuggingFace](https://huggingface.co/kontur-ai/sbert_punc_case_ru)
- [oliverguhr/fullstop-punctuation-multilang-large](https://huggingface.co/oliverguhr/fullstop-punctuation-multilang-large)
- [optimum.onnxruntime conversion](https://huggingface.co/docs/optimum/main/en/onnxruntime/overview)
