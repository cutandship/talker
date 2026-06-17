# 08. Voice Snippets — расширение коротких фраз в большие шаблоны

> Произносишь короткий триггер ("моя подпись", "адрес офиса", "статус короткий") → Talker подставляет полный шаблон.

**Категория:** Tier 2 — заменяет TextExpander для часто повторяемых фраз.
**Готовность концепта:** 🟢 High.

---

## Зачем

Юзер диктует одни и те же длинные фразы каждый день:

- Email-подпись (5–10 строк)
- Адрес офиса / реквизиты
- Шаблон стендапа («вчера X, сегодня Y, блокеров нет»)
- Юридический disclaimer
- Шаблон ответа коллегам / клиентам

Snippets: говоришь короткий триггер, в поле появляется полный шаблон. Не нужно каждый раз диктовать одно и то же.

---

## Технический подход

### Хранение

`snippets.json` (отдельно от config, т.к. может быть большим):

```json
[
  {
    "trigger": "моя подпись",
    "body": "С уважением,\nИван Иванов\nivan@example.com",
    "match": "prefix",
    "case_sensitive": false
  },
  {
    "trigger": "статус короткий",
    "body": "Вчера: ...\nСегодня: ...\nБлокеров нет.",
    "match": "exact",
    "case_sensitive": false
  }
]
```

### Matching modes

- **`exact`** — весь финальный текст ровно равен триггеру → заменяется на body. Самый частый кейс.
- **`prefix`** — текст начинается с триггера, остальное — параметр. Триггер `"подпись для"` + диктовка `"подпись для Ивана"` → body с интерполяцией `{name}=Ивана`.
- **`anywhere`** — заменить **только** триггер внутри текста (in-place). Например триггер `"мой имейл"` → `"ivan@example.com"`.

### Pipeline

После Whisper транскрипции, **до** LLM cleanup:

```python
def apply_snippets(raw: str, snippets: list[Snippet]) -> tuple[str, bool]:
    """Returns (text, was_snippet_used)."""
    norm = raw.strip().lower()
    for s in snippets:
        if s.match == "exact" and norm == s.trigger.lower():
            return s.body, True
        if s.match == "prefix" and norm.startswith(s.trigger.lower()):
            param = raw[len(s.trigger):].strip()
            return s.body.replace("{param}", param), True
    # anywhere matches — apply all
    text = raw
    used = False
    for s in snippets:
        if s.match == "anywhere":
            new = _replace_case_insensitive(text, s.trigger, s.body)
            if new != text:
                text = new
                used = True
    return text, used
```

### Skip LLM при exact match

Если сработал `exact` snippet — **пропускаем LLM cleanup**, потому что body уже идеален (написан юзером руками). Это экономит 500ms–2s.

### UI

Новое окно «Снippets» (или вкладка в Настройках):

```
┌─ Снippets ─────────────────────────────────────────────┐
│                                                         │
│ ┌────────────────────────────────────────────────────┐ │
│ │ моя подпись                          [exact]   [✎] │ │
│ │ ┃ С уважением, Иван Иванов...                      │ │
│ │ ────────────────────────────────────────────────── │ │
│ │ статус                                [prefix]  [✎] │ │
│ │ ┃ Вчера: {param}                                    │ │
│ └────────────────────────────────────────────────────┘ │
│ + Создать                                              │
└────────────────────────────────────────────────────────┘
```

### Импорт/экспорт

Кнопка «Импорт из TextExpander / Espanso» — парсит их YAML / SQLite.

---

## Архитектура

**Новые модули:**
- `snippets.py`:
  - `Snippet` dataclass
  - `SnippetStore` — load/save snippets.json
  - `apply_snippets(text, snippets)`

**Изменения:**
- `main.py`:
  - В `_process` после транскрипции: `text, used = apply_snippets(raw, self.snippets)`. Если used и match==exact — пропускаем cleanup.
- `ui.py`:
  - Окно SnippetsWindow с CRUD UI.
  - Триггер из tray menu «Снippets».
- `config.py`:
  - Путь к snippets.json (рядом с config.toml).

---

## Зависимости

Никаких новых.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Триггер совпадает с обычной фразой (юзер реально хотел сказать "моя подпись") | Юзер сам выбирает уникальные триггеры. Можно префиксировать "сниппет ..." для безопасности. |
| Параметр в prefix-режиме длинный | OK, без ограничения. {param} просто вставляется. |
| Несколько snippets совпали | exact > prefix > anywhere. Внутри одной категории — первый по порядку. |
| Юзер использует snippet trigger как часть длинной диктовки | exact не сработает (не равно), prefix может — но обычно понятно из контекста. |
| Body содержит markdown / эмодзи / unicode | Передаётся как есть в clipboard, injector сам разберётся. |
| Whisper плохо распознал триггер ("моя подпись" → "моя подписи") | Lemmatization русского — overkill. Лучше fuzzy matching через `difflib.SequenceMatcher.ratio()` >= 0.85. |

---

## Acceptance criteria

- В UI можно создать/отредактировать/удалить сниппет.
- Произношу триггер → вставляется body без cleanup.
- Prefix-сниппет с {param} интерполирует параметр.
- Из коробки идут 2–3 примера сниппетов (можно удалить).
- Если ни один не сработал — поведение Talker не меняется.

---

## Сложность

- ~4–5 часов, ~250 LOC.
- В основном UI CRUD.
- Сам apply — 30 LOC.

---

## Открытые вопросы

- Голосовое редактирование snippets ("создай новый сниппет: триггер X, тело Y") — bonus в v2.
- Multi-параметрические snippets (`{name}`, `{date}`, `{time}`) — да, базовая интерполяция: `{date}` = today, `{time}` = now. Уж очень полезно.
- Sync snippets между машинами — out of scope.

---

## Источники

- [Wispr Flow Snippets](https://docs.wisprflow.ai/articles/5784437944-create-and-use-snippets)
- [Espanso (open-source text expander)](https://espanso.org/)
- [TextExpander snippets concept](https://textexpander.com/)
