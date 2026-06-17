# 25. Контекст активного окна в initial_prompt

> Перед распознаванием Talker читает текст, который уже есть у курсора (черновик, сообщение, на которое отвечаешь), и подмешивает его в `initial_prompt` Whisper. Модель «знает тему разговора» → меньше промахов на близких по звучанию словах. Сильнее любого ручного словаря.

**Категория:** Tier 1/2 — высокая отдача, но read-back через UIA капризный.
**Эффект:** 🔥 круто.
**Готовность концепта:** 🟡 Medium — требует осторожной обработки пустого/мусорного контекста.

---

## Зачем

Whisper выбирает между омофонами по контексту предыдущего сегмента (`condition_on_previous_text=True`, [transcriber.py:132](../transcriber.py)). Но в начале реплики контекста нет — а он буквально лежит на экране: текст письма, на которое отвечаешь; ветка чата; черновик в поле ввода.

Если скормить этот текст как `initial_prompt`, Whisper «настроится на тему»: в письме про аренду «сдача» не станет «сдачей в магазине», в треде про Kubernetes «под» распознается как «pod» и т.п. Это динамический контекст, которого ручной словарь (05) дать не может.

---

## Технический подход

### Источник контекста

Уже есть `cursor_format.read_caret_context()` — читает текст вокруг каретки в сфокусированном контроле через UIA (используется в `_paste`, [main.py:881](../main.py)). Плюс заголовок окна из `get_foreground_info().title` ([modes.py:70](../modes.py)).

```python
def gather_context() -> str:
    parts = []
    try:
        ctx = read_caret_context()          # текст у курсора (черновик/тред)
        if ctx and ctx.text:
            parts.append(ctx.text)
    except Exception:
        pass
    try:
        info = get_foreground_info()
        if info.title:
            parts.append(info.title)        # тема письма / название чата
    except Exception:
        pass
    return " ".join(parts)
```

### Сборка prompt

Контекст + словарь, с урезкой под бюджет 224 токена (словарь — приоритетнее, контекст добивает остаток). Расширяем `build_initial_prompt` ([vocabulary.py:25](../vocabulary.py)):

```python
def build_initial_prompt(words, language, context: str = "") -> str:
    base = _words_clause(words, language)          # как сейчас
    ctx = _truncate(context, max_chars=400)        # хвост, ближайший к курсору
    return (ctx + " " + base).strip() if ctx else base
```

Берём **последние** N символов контекста (ближе к курсору — релевантнее), чистим от мусора (множественные переводы строк, нечитаемое).

### Где вызывается

В `_process` ([main.py:797](../main.py)), прямо перед `transcribe`, кладём контекст в мутабельный атрибут (как уже сделано с `vocabulary` / `no_speech_threshold`, [transcriber.py:50](../transcriber.py)):

```python
if self.config.stt.context_priming:
    self.transcriber.context = gather_context()
raw = self.transcriber.transcribe(audio)
```

`transcribe` ([transcriber.py:111](../transcriber.py)) собирает `initial_prompt = build_initial_prompt(self.vocabulary, self.language, self.context)`.

> Контекст читаем **в начале** `_process`, до любой вставки, пока фокус ещё на исходном поле.

---

## Архитектура

**Изменения:**
- `vocabulary.py` — параметр `context` в `build_initial_prompt`, урезка.
- `transcriber.py` — атрибут `self.context: str = ""`, проброс в `build_initial_prompt`.
- `main.py` — `gather_context()` (можно в `cursor_format.py`), установка `transcriber.context` в `_process`.
- `config.py` — `SttConfig.context_priming: bool = True` ([config.py:95](../config.py)).
- `ui.py` — чекбокс «Учитывать текст на экране (контекст)».

**Только для Whisper.** Parakeet ([parakeet_engine.py](../parakeet_engine.py)) `initial_prompt` не принимает — для него фича no-op.

---

## Зависимости

Нет — UIA уже задействован в `injector.py` / `cursor_format.py`.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Поле пустое / UIA ничего не вернул | `context=""`, поведение как сейчас. |
| Поле с паролем | UIA-защита в `injector.py` (концепт 04) уже не читает password-контролы; для контекста — то же. **Важно для приватности.** |
| Контекст на другом языке | Whisper микширует нормально; язык реплики не навязываем. |
| Огромный текст (вся статья) | Берём только хвост ~400 симв.; словарь приоритетнее в бюджете токенов. |
| Контекст «зациклил» Whisper (повтор) | `compression_ratio_threshold` + temperature fallback ([transcriber.py:126](../transcriber.py)) уже страхуют. |
| Continuous mode | Контекст читаем один раз на старте сессии, не на каждом сегменте (дорого). |

---

## Acceptance criteria

- При ответе в треде про конкретную тему слова из этой темы распознаются точнее (smoke: омофон в контексте).
- Пустой/недоступный контекст не ломает распознавание.
- Password-поля не читаются.
- Фича отключается флагом.

---

## Сложность

- ~3–4 часа, ~120 LOC.
- Риск — в качестве UIA read-back на разных приложениях; нужен тест на Chrome/Slack/Outlook.

---

## Открытые вопросы

- Что приоритетнее в бюджете токенов — словарь или контекст? → словарь (стабильная польза), контекст добивает остаток.
- Брать выделенный текст (selection) как сильный сигнал «вот к чему реплика»? — да, если есть selection, он важнее caret-контекста.

---

## Источники

- [Whisper prompting guide (OpenAI cookbook)](https://cookbook.openai.com/examples/whisper_prompting_guide)
- Связано: концепт 04 (UIA injection), 18 (cursor-aware formatting), 05 (словарь).
