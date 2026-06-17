# 19. Course Correction (Backtrack)

> Если в речи есть «стоп, нет, я имел в виду…» — отбрасываем предыдущую фразу, оставляем только корректную версию.

**Категория:** Tier 3 — мелочь, но сильно влияет на ощущение «модель понимает».
**Готовность концепта:** 🟡 Medium.

---

## Зачем

Сейчас если юзер говорит:

> «Встреча в четверг в три часа... нет, погоди, в пятницу в три часа.»

Talker вставит весь raw поток, и юзер должен **сам** удалить «в четверг в три часа... нет, погоди». LLM cleanup частично это лечит, но непредсказуемо.

Wispr показывает: distinct backtrack-фича снижает usability friction. Юзер диктует **естественно**, без необходимости держать в голове "не оговориться".

---

## Технический подход

### Подходы

**(a) LLM-only:** доверяем cleanup промпту. Уже частично работает, но непредсказуемо.

**(b) Heuristic pre-pass:** обнаруживаем паттерны "стоп / нет / погоди / я имею в виду" и режем текст до маркера.

**(c) Two-pass LLM:** specific prompt для backtrack detection до общего cleanup.

**Выбор:** **(b) heuristic + (a) LLM как backup**. Эвристика быстро ловит явные случаи, LLM добивает нюансы.

### Эвристика

Паттерны (русский):
- `(стоп|нет|погоди|подожди|я имею в виду|я хотел сказать|то есть)[\s,.\-]+`
- Действие: считаем эту фразу + всё, что в **этом же предложении до неё**, "отменённым".

Алгоритм:

```python
BACKTRACK_PATTERNS_RU = [
    r"\b(стоп,?\s+нет)\b",
    r"\b(нет,?\s+погоди)\b",
    r"\b(я\s+имел[аи]?\s+в\s+виду)\b",
    r"\b(я\s+хотел[аи]?\s+сказать)\b",
    r"\b(то\s+есть)\b",   # weaker — sometimes legit
]

def apply_backtrack(text: str, lang: str) -> str:
    sentences = _split_sentences(text)
    result = []
    for sent in sentences:
        # find latest backtrack marker
        last_marker_pos = -1
        for pat in BACKTRACK_PATTERNS_RU:
            for m in re.finditer(pat, sent, re.IGNORECASE):
                last_marker_pos = max(last_marker_pos, m.end())
        if last_marker_pos >= 0:
            # drop everything before the last marker
            sent = sent[last_marker_pos:].strip()
        if sent:
            result.append(sent)
    return " ".join(result)
```

Place in pipeline: **до** LLM cleanup, **после** snippets.

### English patterns

```python
BACKTRACK_PATTERNS_EN = [
    r"\b(scratch that)\b",
    r"\b(wait,?\s+no)\b",
    r"\b(I mean)\b",
    r"\b(actually,?\s+no)\b",
    r"\b(let me rephrase)\b",
]
```

### LLM augmentation

В system-prompt cleanup добавляем явное указание:

```
Если в тексте есть фразы коррекции (стоп / нет / я имею в виду / то есть),
оставь только финальную версию, отбросив всё, что отменено.

Пример:
Вход:  «Встреча в четверг в три. Нет, погоди, в пятницу в три.»
Выход: «Встреча в пятницу в три.»
```

Это страховка на случаи, когда heuristic не сработал.

---

## Архитектура

**Новые модули:**
- `backtrack.py`:
  - `apply_backtrack(text, lang) -> str`
  - Список паттернов по языкам.

**Изменения:**
- `main.py:_process` — между snippets и cleanup: `text = apply_backtrack(text, cfg.stt.language)`.
- `cleaner.py` — обогащённый system prompt с backtrack-инструкцией.
- `config.py:CleanerConfig.backtrack_enabled: bool = True`.

---

## Зависимости

Никаких новых.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| «Я имел в виду» внутри легитимной фразы ("Когда я сказал X, я имел в виду Y") | Эвристика отрежет до маркера и оставит "Y". В этом случае **это правильно** — Y и есть то, что хотел юзер. |
| «То есть» как обычная конструкция (объяснение) | False positive: "Алгоритм — то есть он работает так..." → отрежет начало. **Лечение:** маркер «то есть» — opt-in, не дефолт. |
| Несколько маркеров подряд («нет, стоп, я имею в виду…») | Берём самый правый — отрезаем по нему. |
| Маркер на стыке предложений («..., стоп, давай отдельно.») | Эвристика работает в рамках одного предложения. Если маркер в начале нового — оставляем как есть. |
| Юзер диктует мета-текст про сам backtrack ("когда я говорю 'я имею в виду' это значит коррекция") | Эвристика отрежет. Рекомендуем отключить опцию для таких сессий. |

---

## Acceptance criteria

- «Встреча в четверг. Нет, погоди, в пятницу.» → «В пятницу.» (после backtrack + LLM).
- При выключенной опции — backtrack не применяется.
- Можно добавить свои маркеры через config.

---

## Сложность

- ~3 часа, ~150 LOC.
- 1 час — паттерны + sentence split.
- 1 час — интеграция в pipeline.
- 1 час — promp tuning.

---

## Открытые вопросы

- Авто-определение языка для выбора patterns или использовать `cfg.stt.language`? Дефолт — конфиг.
- Логировать, когда heuristic срабатывал — для tuning'а. Да, в `talker.log`.

---

## Источники

- [Wispr Flow Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack)
- Регулярные выражения по русскому — наработки nerd-dictation адаптируем.
