# 05. Custom Vocabulary — словарь имён и терминов

> Юзер задаёт список слов / имён / терминов / брендов; они подаются Whisper'у как `initial_prompt`, что снижает WER на доменной лексике на 40–60%.

**Категория:** Tier 2 — easy win, особенно важный для русского.
**Готовность концепта:** 🟢 High.

---

## Зачем

Whisper хорошо понимает обычную речь, но **систематически промахивается** на:
- Именах собственных, особенно нерусских ("Anthropic" → "энтропик", "Claude" → "клод/клауд").
- Брендах и продуктах ("Talker", "Wispr", "Cursor").
- Технических терминах ("kubernetes", "transformer", "attention").
- Аббревиатурах ("STT", "TTS", "UIA").

Параметр `initial_prompt` Whisper-а **уже встроен** в faster-whisper и принимает строку до 224 токенов. Whisper использует её как "контекст предыдущего сегмента" — слова из неё **активируются** в beam search.

Research arxiv 2410.18363 (2024): −40-60% WER на доменной лексике без файнтюна.

---

## Технический подход

### UI

В Настройках новая секция «Словарь»:

```
┌─ Словарь ──────────────────────────────────────────────┐
│ Имена и термины, которые модель должна узнавать:       │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Anthropic                                            │ │
│ │ Claude                                               │ │
│ │ Talker                                               │ │
│ │ Wispr                                                │ │
│ │ Cursor                                               │ │
│ │ Александр Иванович                                   │ │
│ │ kubernetes                                           │ │
│ └─────────────────────────────────────────────────────┘ │
│ + Добавить                                              │
│                                                         │
│ ℹ Сохранено: 7 слов. Лимит ≈ 50–80 коротких слов.      │
└────────────────────────────────────────────────────────┘
```

### Хранение

`config.toml`:

```toml
[vocabulary]
words = [
    "Anthropic",
    "Claude",
    "Talker",
    "Wispr",
    "kubernetes",
]
```

В `config.py`:

```python
@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)
```

### Композиция initial_prompt

Из списка слов собираем prompt:

```python
def build_initial_prompt(words: list[str], language: str | None) -> str:
    if not words:
        return ""
    # Whisper лучше понимает "natural" предложения, чем bare keyword list.
    # Конструкция типа: "Discussion mentioned X, Y, Z." 
    sample = ", ".join(words)
    if language == "ru":
        return f"В разговоре упоминаются: {sample}."
    return f"The discussion mentions: {sample}."
```

Передаём в `model.transcribe(audio, initial_prompt=prompt, ...)`.

### Лимит токенов

224 токенов = примерно 50–80 коротких слов (с учётом, что русские слова часто токенизируются как 2–3 BPE-токена).

При превышении — обрезаем по приоритету:
- Recently used (если есть auto-learning) — впереди.
- Manually added — впереди обычных.
- Длинные редко-встречающиеся — обрезаем первыми.

Логируем "vocabulary truncated to N words for prompt size".

### Интеграция с Continuous mode

В Continuous (VAD-сегменты) — `initial_prompt` отдаём на каждом сегменте. Это слегка ухудшает производительность (Whisper обрабатывает prompt каждый раз), но эффект на качество того стоит.

---

## Архитектура

**Изменения:**
- `config.py` — `VocabularyConfig`, save/load, parse `[vocabulary]` секции.
- `transcriber.py`:
  - `Transcriber.__init__` принимает `vocabulary: list[str]`.
  - Метод `_build_initial_prompt()` (internal).
  - В `transcribe()` пробрасываем `initial_prompt=self._build_initial_prompt()`.
- `ui.py`:
  - Новая секция «Словарь» в Settings.
  - Listbox + поле ввода + кнопки Add/Remove.
- `main.py`:
  - `_load_model()` пробрасывает `cfg.vocabulary.words` в Transcriber.
  - `_on_settings_saved` — при изменении словаря модель **не** перегружаем (prompt применяется на каждом transcribe), просто обновляем `transcriber.vocabulary = ...`.

---

## Зависимости

Никаких новых. Параметр `initial_prompt` уже поддерживается faster-whisper.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Пустой словарь | Не передаём `initial_prompt` (или передаём `""`), Whisper работает как раньше. |
| Слово в словаре противоречит языку | Это **ОК** — Whisper нормально микширует, особенно на code-switching фразах "посмотри код в Cursor". |
| Юзер добавил очень много слов | Обрезаем по лимиту. UI показывает счётчик. |
| Опечатка в слове ("Antropic" вместо "Anthropic") | Whisper будет насаждать опечатку. Подсказка в UI: "пишите слова правильно". |
| Юзер добавляет регулярные слова ("привет") | Не вредит, но и не помогает. UI может подсказать: "обычные слова добавлять не нужно". |
| Punctuation в слове ("github.com") | Передаём как есть, Whisper нормально обрабатывает. |

---

## Acceptance criteria

- В Настройках можно добавить/удалить слова.
- При следующей диктовке упомянутые слова распознаются заметно точнее (smoke test: имя "Anthropic" в чистой речи).
- Без словаря поведение не меняется.
- Изменение словаря **не** требует перезагрузки модели (мгновенно).
- При длинном словаре в логе видно, что prompt усечён.

---

## Сложность

- ~3–4 часа работы, ~150 LOC.
- В основном UI listbox + save/load.

---

## Открытые вопросы

- Группировать слова по категориям (имена / тех-термины / бренды)? — нет, не нужно. Whisper не различает.
- Импорт из текстового файла одной кнопкой? — Yes, простой "Import from .txt" — bonus.
- Авто-предложение слов из истории? — это уже концепт **06 auto-learning dictionary**.

---

## Источники

- [Whisper prompting guide (OpenAI cookbook)](https://cookbook.openai.com/examples/whisper_prompting_guide)
- [Contextual Biasing without Fine-Tuning (arxiv 2410.18363)](https://arxiv.org/abs/2410.18363)
- [faster-whisper transcribe args](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- [Prompt Engineering in Whisper (Medium)](https://medium.com/axinc-ai/prompt-engineering-in-whisper-6bb18003562d)
