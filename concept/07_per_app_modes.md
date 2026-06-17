# 07. Per-app modes — разный cleanup-промпт в зависимости от активного приложения

> Talker определяет, в каком приложении сейчас фокус (Slack / Gmail / Cursor / Telegram), и применяет **разный** cleanup-промпт и параметры. Slack-casual, email-formal, code-aware.

**Категория:** Tier 2 — multi-fold-возврат от одной фичи.
**Готовность концепта:** 🟢 High.

---

## Зачем

Сейчас cleanup-промпт у Talker один: «убери "ну, эээ", расставь пунктуацию, абзацы». Но:

- В Slack хочется **коротко и casually**, без излишней формальности и абзацев.
- В письме (Outlook/Gmail) хочется **полный шаблон**: "Здравствуйте, ...".
- В коде (VS Code, Cursor) хочется **сохранить технические термины** без auto-correct и **не добавлять пунктуацию** в неправильных местах.
- В Cursor chat хочется **structured prompts** с markdown.

Per-app modes автоматически переключают behaviour.

---

## Технический подход

### Определение активного приложения

```python
import ctypes
from ctypes import wintypes

def get_foreground_info() -> dict:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    
    # Process name
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_name = _get_process_name(pid.value)   # via psutil or QueryFullProcessImageName
    
    # Window class
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    
    # Window title
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    
    # URL if browser — extract via UIA from address bar
    url = _extract_browser_url(hwnd) if process_name in BROWSERS else None
    
    return {
        "process": process_name,
        "class": cls.value,
        "title": title.value,
        "url": url,
    }
```

### Mode matching

`config.toml`:

```toml
[[mode]]
name = "slack"
match_process = ["slack.exe"]
prompt = """
Ты редактируешь короткое сообщение для Slack-чата.
Делай casually, без приветствий, без абзацев.
Сохраняй смайлики, упоминания @user, ссылки.
"""
beam_size = 5

[[mode]]
name = "code"
match_process = ["Code.exe", "cursor.exe", "windsurf.exe", "pycharm.exe"]
prompt = """
Это диктовка для редактирования кода или комментария к коду.
Сохраняй имена переменных camelCase / snake_case без изменений.
Не добавляй пунктуацию там, где её не было.
Технические термины (API, JSON, async) пиши как есть.
"""

[[mode]]
name = "email"
match_process = ["OUTLOOK.EXE", "thunderbird.exe"]
match_url_contains = ["mail.google.com", "outlook.live.com"]
prompt = """
Это письмо. Используй формальный, но дружелюбный тон.
Начинай с приветствия, заканчивай подписью если уместно.
Разбивай на абзацы по смыслу.
"""

[[mode]]
name = "default"
prompt = """(существующий cleanup промпт)"""
```

Matching: процесс → класс окна → URL (для browsers) → default. Первый match выигрывает.

### UI

В Настройках новая секция «Режимы по приложениям»:

```
┌─ Режимы ───────────────────────────────────────────────┐
│ ┌─ slack            slack.exe              [Edit][Del] │
│ │ ┌─ code            Code.exe, cursor.exe   [Edit][Del] │
│ │ ┌─ email           OUTLOOK.EXE, gmail...   [Edit][Del] │
│ │ ┌─ default                                  [Edit]    │
│ + Добавить режим                                        │
└────────────────────────────────────────────────────────┘
```

При Edit — диалог с полями name, matchers, prompt (textarea), beam_size, опциональная модель (если хочется на code-режиме сильнее модель).

### Индикация в FlowBar

В FlowBar показываем имя активного режима маленькой меткой: `Talker · slack`. Юзер всегда видит, что применится.

---

## Архитектура

**Новые модули:**
- `modes.py`:
  - `Mode` dataclass
  - `ModeMatcher` — пробегает по правилам, возвращает match.
  - `ForegroundWatcher` — фоновый поток, обновляет current_mode каждые 500 мс.

**Изменения:**
- `config.py`:
  - `ModeConfig` dataclass.
  - `Config.modes: list[ModeConfig]`.
  - Save/load.
- `main.py`:
  - В `_process` берём `current_mode = mode_matcher.current()`, передаём `mode.prompt` в cleaner.
  - При запуске стартуем `ForegroundWatcher`.
- `cleaner.py`:
  - `CleanerChain.clean(text, system_prompt=None)` — принимает override промпт.
- `ui.py`:
  - Секция «Режимы», вложенный диалог редактирования.
  - FlowBar — небольшая подпись с именем режима.

---

## Зависимости

```
psutil>=5.9       # для process name
```

(можно обойтись без psutil — через `OpenProcess + QueryFullProcessImageNameW`, но psutil чище).

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Активное окно меняется во время речи | Берём режим, **активный в момент отпускания клавиши**. Не в момент нажатия. |
| Несколько правил матчатся (slack + default) | Первый по порядку — slack. Default всегда последний и без `match_*` (catch-all). |
| Юзер удалил все режимы | Используется встроенный hardcoded default. |
| Browser URL не извлекается | Падаем на match_process / класс. |
| Активное окно — сам Talker (Settings или History) | Используем default (или mode `talker`, если есть). |
| Foreground watcher лагает на тяжёлой системе | Получаем устаревший режим. Можно делать lookup on-demand в `_process`, без watcher'а. **Решение:** watcher держится для FlowBar-индикатора, но в `_process` всегда re-fetch. |

---

## Acceptance criteria

- Из коробки идут 3 режима: slack, code, email + default.
- FlowBar показывает текущий режим.
- Меняешь фокус с VS Code на Slack — индикатор обновляется в течение 500 мс.
- Диктуешь в Slack — текст коротенький, без приветствий. В Outlook — с приветствием и подписью.
- Можно добавить свой режим.

---

## Сложность

- ~5–6 часов, ~350 LOC.
- Большая часть — UI редактор.
- Foreground detection — 30 LOC.

---

## Открытые вопросы

- Mode-specific модели (например, code-режим использует large-v3, slack — small)? Это сильно усложняет (нужно держать N моделей в памяти). v2.
- Mode-specific хоткеи? Например push-to-talk в code-режиме — это всегда команда «вставь docstring». Сложно. v2.
- URL извлечение из browser address bar — стабильность UIA? — варьируется по браузерам. Тестировать на Chrome / Firefox / Edge / Arc.

---

## Источники

- [Wispr Flow Context Awareness](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness)
- [Superwhisper Modes](https://superwhisper.com/docs/modes)
- [Win32 GetForegroundWindow / GetWindowThreadProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow)
- [psutil for process inspection](https://psutil.readthedocs.io/en/latest/)
