# 17. Inline voice commands

> Внутри потока диктовки слова-команды («новый абзац», «удали последнее», «с большой буквы», «отправь») распознаются как **команды**, а не вставляются как текст.

**Категория:** Tier 3 — мелкие, но удобные действия. Есть у Superwhisper, Talon.
**Готовность концепта:** 🟡 Medium — требует продуманного списка команд и тестов на ложные срабатывания.

---

## Зачем

Часто нужно:
- Сделать абзац: сейчас юзер договаривает фразу, отпускает, набирает Enter руками.
- Удалить только что произнесённое (опечатка / передумал).
- Поставить большую букву / точку в конкретном месте.
- В чате после диктовки нажать Enter (отправить).

Голосовая команда выполняет действие **сразу**, без касания клавиатуры.

---

## Технический подход

### Список команд (русский)

| Фраза | Действие |
|---|---|
| «новый абзац» | `\n\n` |
| «новая строка» | `\n` |
| «точка» | `.` |
| «запятая» | `,` |
| «вопрос» / «вопросительный знак» | `?` |
| «удали последнее слово» | Backspace до пробела |
| «удали последнее предложение» | Backspace до `.` |
| «с большой буквы» | Применить к следующему слову |
| «отправь» / «энтер» | `Enter` |
| «отмена» / «забудь» | Очистить всё, что вставлено в эту сессию |
| «выдели последнее предложение» | Shift+Home (или UIA) |

### English equivalents

| Phrase | Action |
|---|---|
| "new paragraph" | `\n\n` |
| "new line" | `\n` |
| "period" / "dot" | `.` |
| "comma" | `,` |
| "delete last word" | Backspace × N |
| "send it" / "enter" | Enter |
| "cancel" | Clear session |

### Pipeline

После Whisper транскрипции, **до** LLM cleanup, **до** snippets:

```python
def extract_commands(text: str, lang: str) -> tuple[str, list[Action]]:
    """
    Split text into (cleaned_text, [actions]) where actions execute after insert.
    Recognizes command phrases case-insensitively.
    """
    pattern = _build_command_pattern(lang)   # alternation of all triggers
    parts = []
    actions = []
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            parts.append(text[last:m.start()])
        cmd = m.group(0).lower()
        actions.append(_command_to_action(cmd))
        last = m.end()
    if last < len(text):
        parts.append(text[last:])
    return " ".join(parts).strip(), actions
```

После cleanup и вставки текста — executing actions in order:

```python
for action in actions:
    if action.kind == "insert_text":
        injector.inject(action.text)
    elif action.kind == "key":
        keyboard.send(action.key)
    elif action.kind == "backspace_until":
        # пересчитываем сколько backspace'ов
        ...
```

### Disambiguation

Самый сложный момент — фраза может быть **частью реальной речи**, а не командой.

- "Я думаю, **новый абзац** должен быть здесь" — это диктовка про абзацы, не команда!
- "Удалите **последнее слово** из этого предложения" — обсуждение, не команда.

Heuristics:
1. **Слово-маркер впереди:** распознавать только если перед командой есть `"команда"` / `"talker"`:
   - "talker новый абзац" → команда.
   - "новый абзац" → обычный текст.
   - Минимизирует false positives, но требует от юзера явного маркера.
2. **Standalone:** команда распознаётся только если она **на конце** диктовки и одиночная:
   - "...вот текст. новый абзац." → команда.
   - "новый абзац должен быть здесь" → текст.
3. **Гибрид:** маркер опционален; standalone-команда в конце — без маркера; в середине — только с маркером.

**Дефолт v1:** требовать маркер `"talker ..."` или явный модификатор. Безопаснее.

### Конфиг (расширяемый словарь)

```toml
[[voice_command]]
phrase = "новый абзац"
action = "insert"
value  = "\n\n"

[[voice_command]]
phrase = "удали последнее слово"
action = "key"
value  = "ctrl+backspace"

[[voice_command]]
phrase = "отправь"
action = "key"
value  = "enter"
```

UI в Settings даёт CRUD по этому списку.

---

## Архитектура

**Новые модули:**
- `voice_commands.py`:
  - `VoiceCommand` dataclass
  - `extract_commands(text, commands, marker)` 
  - `execute_actions(actions, injector)`

**Изменения:**
- `main.py:_process` — после транскрипции, до cleanup: `text, actions = extract_commands(raw, ...)`. Затем cleanup на text, потом execute actions.
- `config.py:VoiceCommandsConfig` — список команд + marker.
- `ui.py:SettingsWindow` — секция «Голосовые команды».

---

## Зависимости

Никаких новых.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Фраза-команда без marker, но в конце | Если опт-ин `standalone_in_tail = True` — команда. Иначе — текст. |
| Multi-language utterance | Использовать `cfg.stt.language` или auto-detect first command pattern. |
| Команда + параметр («удали последние 3 слова») | v2. Сначала только фиксированные команды. |
| Команда внутри snippet trigger | Сниппеты обрабатываются после commands. Конфликт unlikely. |
| Слово созвучно команде, но не оно («Талкер, новый Абраам» — имя) | Жёсткие границы (`\bновый абзац\b`), case-insensitive. Имена редко в command-list. |

---

## Acceptance criteria

- Произношу «talker новый абзац» — вставляется `\n\n`.
- Произношу «удали последнее слово» (с маркером) — Backspace до предыдущего пробела.
- Без маркера команда не срабатывает (или срабатывает только standalone в tail-режиме).
- Можно добавить свою команду в UI.

---

## Сложность

- ~4–5 часов, ~250 LOC.
- 2 часа — pattern matching + actions.
- 2 часа — UI CRUD.
- 1 час — disambiguation tuning.

---

## Открытые вопросы

- Marker слово — какое? «talker» (имя продукта), «команда» (явно), что-то ещё? Юзер выбирает в Settings, дефолт — оба.
- Backspace-actions при наличии UIA — лучше через replace selection, чем счёт символов. Зависит от концепта 04.
- Конфликт с Command Mode (концепт 02) — Command Mode это отдельный хоткей, inline команды — внутри обычной диктовки. Не конфликтуют.

---

## Источники

- [Superwhisper voice commands](https://superwhisper.com/docs/modes/super)
- [Talon Voice basic usage](https://talon.wiki/Basic%20Usage/basic_usage/)
- [nerd-dictation command grammar](https://github.com/ideasman42/nerd-dictation)
