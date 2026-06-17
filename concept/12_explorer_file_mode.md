# 12. File mode + Explorer context menu

> Talker умеет принимать аудио/видео файл (drag-and-drop, "Открыть с помощью", контекстное меню Explorer) и выдавать транскрипт в выбранный формат.

**Категория:** Tier 3 — расширяет use case с диктовки до универсального транскрайбера.
**Готовность концепта:** 🟢 High.

---

## Зачем

Сейчас Talker — push-to-talk и continuous. Юзер не может:
- Транскрибировать готовый mp3/mp4 (например, скачанный подкаст).
- Передать видеолекцию для конспекта.
- Из Explorer сделать right-click → «Транскрибировать».

Эти юзкейсы покрывают MacWhisper, Aiko, Buzz. Простая добавка — большой охват.

---

## Технический подход

### Поддерживаемые форматы

faster-whisper принимает `numpy ndarray` либо **путь к файлу**. Внутри для файлов использует `av` (libav). Поддерживает: WAV, MP3, M4A, FLAC, OGG, OPUS, MP4 (audio track), WebM, MKV.

### CLI mode

Talker запускается с аргументом — переходит в file-mode:

```
python main.py --transcribe "C:\path\to\file.mp3" --output "C:\path\to\file.srt" --format srt
```

Или без `--output` — выдаёт в stdout / открывает Save As диалог в GUI.

### GUI mode для одного файла

Если файл передан, но без `--output`:
1. Открывается стандартное окно с прогрессом транскрипции.
2. По завершении — диалог "Сохранить как" с выбором формата (Text / SRT / VTT / JSON).
3. Возможность скопировать результат в clipboard напрямую.

### Окно прогресса

```
┌─ Транскрибирую: lecture.mp4 ───────────────── ✕ ─┐
│                                                    │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░  62%               │
│                                                    │
│  Длительность: 45 мин, обработано: 28 мин          │
│  Время до конца: ~3 мин                            │
│                                                    │
│  Превью последнего сегмента:                       │
│  "…и в конце концов мы пришли к выводу что…"      │
│                                                    │
│  [⏸ Пауза]  [⏹ Отменить]                          │
└────────────────────────────────────────────────────┘
```

### Explorer context menu

Windows регистрация через registry:

```reg
[HKEY_CURRENT_USER\Software\Classes\*\shell\TalkerTranscribe]
@="Транскрибировать с Talker"
"Icon"="C:\\Path\\To\\Talker.exe,0"

[HKEY_CURRENT_USER\Software\Classes\*\shell\TalkerTranscribe\command]
@="\"C:\\Path\\To\\Talker.exe\" --transcribe \"%1\""
```

Регистрировать только для аудио/видео расширений (`.mp3`, `.wav`, `.mp4`, …):

```reg
[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.mp3\shell\TalkerTranscribe]
...
```

Кнопка «Зарегистрировать в Explorer» в Settings. Снятие — кнопка «Убрать из Explorer».

### Send To

Альтернатива: создать ярлык в `%APPDATA%\Microsoft\Windows\SendTo\Talker.lnk`. Юзер тогда видит "Send To → Talker" по правому клику. Проще регистрации, но менее заметно.

### Drag-and-drop в FlowBar

Если FlowBar поддерживает drop (tkinter с `tkdnd`) — киндек файла на pill стартует транскрипцию. Бонус, не обязательно.

---

## Архитектура

**Новые модули:**
- `file_mode.py`:
  - `FileTranscriber` — обёртка вокруг Whisper для file input + прогресс.
  - `register_explorer_menu()` / `unregister_explorer_menu()`.
- `tools/file_mode_ui.py` — отдельное окно прогресса.

**Изменения:**
- `main.py`:
  - В начале — парсинг `sys.argv`. Если есть `--transcribe` → file-mode, иначе обычный tray.
- `ui.py`:
  - `FileTranscriberWindow` — UI с прогрессом.
- `exporters.py` (концепт 11).

---

## Зависимости

Никаких новых. faster-whisper уже умеет читать файлы через av.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Файл не существует / битый | Понятная ошибка в окне. |
| Файл > 500 MB | Грузится в RAM может быть проблема — faster-whisper читает потоково, должно быть ОК. Sanity test. |
| Talker уже запущен (tray), юзер тыкнул "Транскрибировать" в Explorer | IPC: новый экземпляр шлёт named pipe сообщение существующему → тот открывает FileTranscriberWindow. Если pipe нет — стартует второй экземпляр (deprecated, лучше IPC). |
| Файл video — извлечение только аудио | av делает это автоматически. |
| Юзер хочет отменить на 90% | Останов прогресса через flag. faster-whisper не имеет cancel-API; делаем через прерывание потока (как в текущем `_active = False` для BgJob). |
| Юзер закрыл progress window | Транскрипция фоном продолжается, результат — в clipboard / уведомление. |

---

## Acceptance criteria

- `python main.py --transcribe file.mp3` без UI выдаёт `file.txt` (или указанный формат).
- В Settings можно зарегистрировать Explorer context menu и убрать его.
- После регистрации в Explorer на mp3 видно «Транскрибировать с Talker».
- Прогресс отображается в UI с превью последнего сегмента.
- Поддерживается отмена.

---

## Сложность

- ~6–8 часов, ~400 LOC.
- 2 часа — CLI + progress callback из faster-whisper.
- 2 часа — Explorer registration (registry).
- 2–3 часа — UI окно прогресса.
- 1 час — IPC для существующего инстанса.

---

## Открытые вопросы

- Batch файлов? — да, простой queue. Bonus.
- Watch-folder (концепт MacWhisper)? — отдельный концепт в будущем, не критично.
- Поддержка subtitles в видеофайле (vtt/srt-tracks)? — out of scope.

---

## Источники

- [Buzz](https://github.com/chidiwilliams/buzz) — Python file-mode whisper transcriber, reference UI.
- [MacWhisper file mode](https://goodsnooze.gumroad.com/l/macwhisper)
- [Windows registry shell extensions](https://learn.microsoft.com/en-us/windows/win32/shell/context-menu-handlers)
