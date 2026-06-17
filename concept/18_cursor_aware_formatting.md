# 18. Cursor-aware formatting

> Перед вставкой Talker читает несколько символов до курсора через UIA. Фиксит регистр (если предложение не закончено — lower case первое слово), пробел (вставить или нет в зависимости от того, что перед).

**Категория:** Tier 3 — устраняет мелкие, но раздражающие косяки.
**Готовность концепта:** 🟡 Medium — зависит от концепта 04 (UIA).

---

## Зачем

Сейчас юзер набирает: «Я думаю, ___» (курсор после запятой и пробела), нажимает PTT, говорит «нам нужно сделать это». Talker вставляет «Нам нужно сделать это.» с заглавной буквы → получается «Я думаю, Нам нужно сделать это.».

Что хочется: видеть, что слева запятая + пробел → строчная буква, не вставлять лишний пробел.

Случаи:
- После `.`, `!`, `?` — заглавная. Возможно пробел.
- После `,`, `:`, `;` — строчная. Пробел.
- После `«`, `(` — строчная без пробела.
- После пробела — как есть.
- В пустом поле — заглавная.

---

## Технический подход

### Чтение контекста через UIA

```python
def read_caret_context(chars_before: int = 50) -> str | None:
    """Returns last N chars before caret in focused element, or None."""
    uia = get_uia()
    focused = uia.GetFocusedElement()
    if not focused:
        return None
    try:
        text_pattern = focused.GetCurrentPattern(UIA_TextPatternId)
        if not text_pattern:
            return None
        # Caret range
        selection = text_pattern.GetSelection()
        if selection.Length == 0:
            return None
        caret_range = selection.GetElement(0)
        # Move start back by N characters
        caret_range.MoveEndpointByUnit(
            TextPatternRangeEndpoint_Start,
            TextUnit_Character,
            -chars_before
        )
        return caret_range.GetText(chars_before)
    except COMError:
        return None
```

UIA не везде есть — fallback на clipboard hack:
- Ctrl+Shift+Home сохранил бы выделение → читать → undo. Деструктивно, **не делаем**.

Если UIA не дал контекст — используем дефолтную логику (заглавная если в начале, пробел перед).

### Logic

```python
def adjust_for_context(text: str, ctx: str | None) -> str:
    if not ctx:
        return _capitalize_first(text)  # дефолт: с заглавной
    
    last_char = ctx[-1] if ctx else ""
    
    # Need leading space?
    if ctx and not last_char.isspace() and last_char not in "([«\"":
        text = " " + text
    
    # Capitalization
    if last_char in "" or last_char in ".!?" or _ends_paragraph(ctx):
        text = _capitalize_first(text)
    elif last_char in ",;:":
        text = _lowercase_first(text)
    # otherwise leave as-is
    
    return text
```

### Где вызывается

В `_process`, после cleanup, **до** injector.inject:

```python
text = cleaned
ctx = read_caret_context()
text = adjust_for_context(text, ctx)
injector.inject(text)
```

### Где НЕ применяется

- В Command Mode (концепт 02) — выделение заменяется целиком, контекст не релевантен.
- В streaming (концепт 01) — partial обновляется постоянно, контекст применить только к первому partial.
- В snippet-режиме (концепт 08) — body уже готов, не трогаем.

Опция в Settings: «Умная капитализация и пробелы» (дефолт on).

---

## Архитектура

**Новые модули:** нет. Логика в `injector.py` (концепт 04).

**Изменения:**
- `injector.py`:
  - `inject(text, smart_format=True)` — расширенная сигнатура.
  - Внутри читает context, применяет adjust.
- `main.py:_paste` уже зовёт `injector.inject(text)`.
- `config.py:OutputConfig.smart_format: bool = True`.

---

## Зависимости

UIA (концепт 04). Без неё фича сильно ограничена — только дефолтная капитализация.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| UIA не работает | Применяется дефолтная логика (capitalize first, добавить пробел если в поле что-то есть → неизвестно). |
| Контекст содержит markdown / code | "**жирный** ___" → последний символ `пробел`, как обычно. ОК. |
| Начало кавычки "...сказал: «___" | Strip leading space, lowercase first. |
| Юзер пишет URL | После `.` (точка в URL) логика думает «конец предложения» → большая буква. Эвристика: если в `ctx` нет пробела между точкой и предыдущим текстом → возможно URL → не капитализируем. |
| Русский и английский | Логика языко-независимая (Unicode `.upper()` / `.lower()`). |
| Acronyms (USA) | Не трогаем uppercase слова. |

---

## Acceptance criteria

- В Notepad после "Я думаю, " — диктовка вставляется со строчной, без двойного пробела.
- В пустом поле — с заглавной.
- После `.` в обычном тексте — с заглавной.
- В Cursor IDE (Electron) — фолбэк работает (там UIA ограничена), не хуже текущего.

---

## Сложность

- ~3–4 часа, ~150 LOC.
- 1 час — UIA reading.
- 1 час — logic + URL/exception cases.
- 1 час — testing на разных приложениях.

---

## Открытые вопросы

- Замена цифр (нумерация списков): после "1. " — лучше lowercase? Дефолт: capitalize. Опционально.
- В коде (mode=code) — отключать smart_format автоматически? Да.

---

## Источники

- [UIA TextPattern docs](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomationtextpattern)
- [Wispr Flow Smart Formatting](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack)
