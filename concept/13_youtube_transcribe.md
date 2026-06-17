# 13. YouTube URL Transcribe

> Вставляешь URL YouTube → Talker качает аудио и выдаёт транскрипт.

**Категория:** Tier 3 — простая, но очень утилитарная фича. Есть у MacWhisper.
**Готовность концепта:** 🟢 High. Зависит от концепта 12 (file-mode pipeline).

---

## Зачем

Случаи:
- Подкаст на YouTube → нужен текст для чтения / поиска.
- Лекция / вебинар → конспект.
- Туториал → пересказ ключевых моментов.

Альтернатива сейчас — скачать через сторонний downloader, потом дать Talker'у файл. С URL — один шаг.

---

## Технический подход

### Stack

**yt-dlp** (форк youtube-dl, активно поддерживается). Качает только audio track (намного меньше видео).

```python
import subprocess

def download_audio(url: str, target_dir: Path) -> Path:
    # Опции: extract audio, mp3 или opus, сохранить в temp
    out_template = str(target_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",                          # extract audio
        "--audio-format", "mp3",
        "--audio-quality", "5",        # средний bitrate, на STT нет смысла больше
        "-o", out_template,
        "--no-playlist",
        "--quiet",
        "--progress",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {proc.stderr}")
    return next(target_dir.glob("*.mp3"))
```

Опционально: yt-dlp как Python library (`from yt_dlp import YoutubeDL`) — без CLI, но добавляет зависимость.

### Поддерживаемые сайты

yt-dlp **из коробки** поддерживает 1000+ сайтов: YouTube, Vimeo, Twitter, TikTok, SoundCloud, BBC, podcasts, и т.д.

То есть фича называется "YouTube transcribe", но реально работает с любым yt-dlp-поддерживаемым URL.

### Pipeline

1. Юзер вставляет URL в input (новое окно "Транскрибировать URL" из tray меню).
2. Talker распознаёт это как URL и запускает yt-dlp download (с прогресс-баром).
3. Скачанный файл идёт в file-mode pipeline (концепт 12).
4. По завершении транскрипции — file удаляется.

### UI

```
┌─ Транскрибировать URL ────────────────────── ✕ ─┐
│                                                  │
│ URL: [https://www.youtube.com/watch?v=...     ]  │
│                                                  │
│ Формат: ⦿ SRT ⦾ TXT ⦾ VTT ⦾ JSON                │
│                                                  │
│ Сохранить в: [C:\Users\...\Downloads\    ] [...] │
│                                                  │
│  ▓▓▓▓▓░░░░░░░░░░░░  Скачиваю...   24%           │
│                                                  │
│  [Старт]   [Отмена]                              │
└──────────────────────────────────────────────────┘
```

### Длинные видео

YouTube-лекция 1 час → файл ~50 MB → транскрипция ~10 мин на small CPU.
Прогресс-бар показывает: скачивание → транскрипция. Для долгих — уведомление в системе по готовности.

---

## Архитектура

**Новые модули:**
- `url_transcribe.py`:
  - `download_audio(url, tmp_dir) -> Path`
  - `is_supported_url(url) -> bool`

**Изменения:**
- `file_mode.py` (концепт 12) — принимает URL так же, как файл.
- `ui.py`:
  - Новое окно `UrlTranscribeWindow` или встроено во FileTranscriberWindow.
- Tray menu: пункт «Транскрибировать URL…».

---

## Зависимости

```
yt-dlp>=2024.04
```

yt-dlp — pure Python, без C-зависимостей. ~15 MB. Активно обновляется (важно — YouTube ломает API раз в квартал).

Альтернатива: вызывать `yt-dlp` как CLI binary. Тогда юзер должен его установить. Хуже UX, проще нам.

**Решение:** включить yt-dlp как pip dependency.

### Не тянем ffmpeg

yt-dlp требует ffmpeg для **post-processing** (конвертация в mp3). Но faster-whisper через `av` умеет читать **что угодно**, что декодируется libav-ом — включая webm/opus, в котором YouTube отдаёт audio изначально.

Опция: качать original audio без re-encode (`--no-post-overwrites --extract-audio --audio-format best`). Файл — `.webm` или `.m4a`. faster-whisper их прочитает.

Это убирает зависимость от ffmpeg в системе.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| URL невалидный | Понятная ошибка "не похоже на URL поддерживаемого сервиса". |
| yt-dlp не может скачать (приватное / удалено / geo-block) | Ошибка от yt-dlp пробрасывается в UI. |
| Файл огромный (10-часовой стрим) | Предупреждение: «10 часов аудио ≈ 30 минут транскрипции». Юзер подтверждает. |
| Юзер закрыл окно во время скачивания | subprocess.kill. |
| YouTube изменили API → yt-dlp устарел | В UI кнопка «Обновить yt-dlp» → `pip install -U yt-dlp` в фоне. |
| Скачивание прошло, transcribe упал | Тимовый файл остаётся; юзер может его передать вручную. |

---

## Acceptance criteria

- Вставляю YouTube URL → через N минут получаю текст / SRT в указанной папке.
- Прогресс-бар показывает обе фазы (download + transcribe).
- Без ffmpeg в системе работает (на webm/m4a).
- Поддерживаются Vimeo, Twitter, основные сайты (smoke test ≥ 5).

---

## Сложность

- ~3–4 часа, ~200 LOC.
- В основном UI окно + интеграция yt-dlp.

---

## Открытые вопросы

- Кэшировать скачанные файлы? — нет, удаляем после транскрипции. Disk space важнее.
- Embed yt-dlp.exe в Talker дистрибутив (PyInstaller) — увеличит размер на ~15 MB, но независимо от системы. Скорее всего yes.
- Batch URL — несколько ссылок сразу? — Tier 4, v2.

---

## Источники

- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [MacWhisper YouTube transcription](https://macwhisper.helpscoutdocs.com/article/51-how-to-transcribe-youtube-videos)
