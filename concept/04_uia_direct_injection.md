# 04. Прямая вставка текста через UI Automation (без буфера обмена)

> Вместо `clipboard + Ctrl+V` использовать Windows UI Automation для прямой вставки текста в активный focused element. Bubble остаётся как safety net.

**Категория:** Tier 1 — убирает основную причину промахов курсора, на которую юзер жаловался.
**Готовность концепта:** 🟡 Medium — стратегия ясна, нужен прототип per-app для оценки покрытия.

---

## Зачем

Сейчас вставка идёт через:
1. `pyperclip.copy(text)` — перетирает буфер
2. `time.sleep(0.05)` — гонка с медленными приложениями
3. `keyboard.send("ctrl+v")` — не работает в RDP, песочницах, password-полях, многих native dialogs

Проблемы:
- Если фокус ушёл — текст ушёл «куда-то» (бубль помогает, но требует руки).
- Clipboard managers (Ditto, ClipboardFusion) ловят каждую копию.
- Antivirus может видеть автоматический Ctrl+V как подозрительный.
- В некоторых приложениях Ctrl+V вставляет картинку (если буфер был картинкой и наш `copy()` не успел).

UIA-вставка решает: текст идёт прямо в element, **не трогая clipboard**.

---

## Технический подход

### Стек

**comtypes** + **UIAutomationCore** напрямую (без `pywinauto` — последний громоздкий, тянет дереву). Альтернатива — `uiautomation` (pip), удобнее, но та же база.

### Алгоритм

```python
def insert_via_uia(text: str) -> bool:
    """Returns True if inserted; False if no suitable element."""
    import comtypes.client
    import comtypes
    
    uia = comtypes.client.CreateObject("UIAutomationCore.CUIAutomation8")
    focused = uia.GetFocusedElement()
    if not focused:
        return False
    
    # Проверка: это editable text?
    try:
        # UIA TextPattern (если есть) — вставка через caret
        text_pattern = focused.GetCurrentPattern(UIA_TextPatternId)
        if text_pattern:
            # Получаем range у caret, заменяем selection / вставляем
            ... 
            return True
    except COMError:
        pass
    
    try:
        # ValuePattern (для simple text fields)
        value_pattern = focused.GetCurrentPattern(UIA_ValuePatternId)
        if value_pattern:
            current = value_pattern.CurrentValue
            value_pattern.SetValue(current + text)   # append
            return True
    except COMError:
        pass
    
    # Последний fallback — Unicode SendInput
    return send_unicode_via_sendinput(text)
```

### SendInput с KEYEVENTF_UNICODE

Когда UIA не работает (Chromium-апликации часто отказывают через ValuePattern, рисуют свой caret), используем низкоуровневый `SendInput`:

```python
def send_unicode_via_sendinput(text: str) -> bool:
    for ch in text:
        # KEYEVENTF_UNICODE — keyDown + keyUp с wScan=ord(ch)
        send_input([
            INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE)),
            INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)),
        ])
    return True
```

Это работает в 95% приложений, включая Chromium/Electron. Не требует clipboard.

### Каскад методов

```
1. UIA TextPattern (best — caret-aware, replace-selection support)
       ↓ fails
2. UIA ValuePattern (works on basic edit controls)
       ↓ fails
3. SendInput Unicode (universal, character-by-character)
       ↓ user said "skip injection"
4. Clipboard + Ctrl+V (old behaviour, last resort)
```

При **любой** успешной вставке — bubble всё равно показываем (если включён), потому что юзер может захотеть второе место.

### Производительность

- UIA-вызов: 5–30 мс.
- SendInput на 200 символов: ~50 мс (с задержкой 0 между событиями).
- Сейчас clipboard + sleep(0.05) + Ctrl+V: ~80 мс.

Скорее всего **быстрее** текущего.

---

## Архитектура

**Новые модули:**
- `injector.py` — каскад методов, фасад `inject(text: str, fallback_clipboard: bool = True) -> InjectionResult`.

**Изменения:**
- `main.py`:
  - `_paste(text)` → `injector.inject(text)`.
  - Логирование результата (какой метод сработал) для отладки покрытия.
- `config.py` — `OutputConfig.injection_mode: "auto" | "uia" | "sendinput" | "clipboard"`.
- `ui.py` — Настройки → блок «Метод вставки», по дефолту auto.

---

## Зависимости

```
comtypes>=1.4
```

UIAutomationCore.dll — системная DLL, есть на Windows 7+.

Не тянет ничего тяжёлого. ~5MB.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Element не editable (read-only label) | UIA отказывает, SendInput тоже не сработает. Bubble остаётся. |
| Password field | UIA блокируется ОС. SendInput работает, но это **security risk** — мы автоматически вводим пароль-look-alike. **Решение:** детектим password field через `IsPasswordCurrent`, отказываемся вставлять, показываем bubble с предупреждением. |
| Электронная таблица (Excel) | UIA Excel специфичен. Через SendInput работает (Excel принимает текст в активную ячейку). |
| Терминалы (cmd, PowerShell, Windows Terminal) | UIA ограничено. SendInput Unicode не всегда правильно интерпретируется shell (кириллица особенно). Фолбэк на clipboard. |
| Игры в фуллскрине | DirectInput игнорирует SendInput; clipboard тоже бесполезен. Bubble — единственный путь. |
| Юзер набирает руками во время injection | Гонка. SendInput может «врезаться» в набор. Минимизировать длительность batch (≤50ms). |
| Юникод-символ ∉ BMP (эмодзи, древние иероглифы) | KEYEVENTF_UNICODE с одним wScan не покрывает surrogate pairs. Шлём два события с high/low surrogate. |

---

## Acceptance criteria

- В дефолтных приложениях (Chrome, Slack, Discord, VS Code, Notepad, Notion, Telegram Desktop, Word, Outlook) вставка работает без clipboard. Логи показывают, какой метод сработал.
- В password-полях вставка **блокируется**, bubble показывает предупреждение.
- При неудаче всех методов автоматический fallback на clipboard + Ctrl+V — поведение не хуже текущего.
- Сборка отчёта: `injector_stats.log` — какой метод какой % успеха.

---

## Сложность

- ~6–10 часов работы, ~400 LOC.
- Большая часть — testing на разных приложениях.
- comtypes-pattern-handling — boilerplate, легко.

---

## Открытые вопросы

- Caret-position-aware вставка (заменить выделение, а не append) — стоит ли? Для Command Mode (концепт 02) — **да**. Через `TextPattern.GetSelection()` + `Replace`.
- Кэшировать UIA-result для скорости? Юзер обычно не меняет фокус между речью и вставкой — но если меняет, кэш плохой.

---

## Источники

- [UI Automation Overview (MSDN)](https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32)
- [comtypes UIA recipes](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
- [SendInput + KEYEVENTF_UNICODE](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
- Wispr Flow → accessibility-based injection (упоминание в их docs, нет публичной реализации)
