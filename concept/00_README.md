# Concept folder — R&D-архив идей Talker

> **Это исследовательский архив, не обещание roadmap.** Здесь — ТЗ и идеи фич
> разной степени готовности: часть реализована, часть осталась черновиками, а
> часть относится к ранним версиям Talker (включая LLM-очистку, позже убранную
> ради полной локальности). Истина — в коде, а не в этих документах.

Каждый файл здесь — это **техническое задание на одну фичу**. Цель: к моменту начала кодинга мне (или будущему мне) не нужно было повторно исследовать, как сделать, — только применить.

## Структура каждого файла

- **Категория / приоритет** — куда фича попадает в ранжировке.
- **Зачем (user value)** — что юзер получает.
- **Как (технический подход)** — конкретные библиотеки, API, алгоритм.
- **Архитектура** — какие файлы Talker трогаем, что добавляем.
- **Зависимости** — новые пакеты, модели, разрешения.
- **Edge cases** — что может пойти не так.
- **Acceptance criteria** — когда считаем готовым.
- **Сложность** — оценка в часах и LOC.
- **Готовность концепта** — насколько проработан этот документ.

## Готовность концепта — шкала

| Уровень | Значит |
|---|---|
| 🟢 **High** | Все вопросы продуманы, можно сразу начинать кодить по описанию. |
| 🟡 **Medium** | Нужны небольшие уточнения по ходу (1–2 экспериментальных коммита). |
| 🟠 **Research** | Требует прототипа / бенчмарка перед основной работой. |

## Карта приоритетов

**Tier 1 — главные UX-сдвиги (большая отдача):**
- `01_streaming_partial_inserts.md` — текст появляется по мере речи
- `02_command_mode.md` — голосом править выделенный текст
- `04_uia_direct_injection.md` — вставка без буфера, фикс промахов курсора
- `03_parakeet_backend.md` — модель в 10× быстрее Whisper

**Tier 2 — высокая отдача / низкая стоимость:**
- `05_custom_vocabulary.md` — словарь имён и терминов
- `06_auto_learning_dictionary.md` — словарь учится сам
- `07_per_app_modes.md` — разный cleanup-промпт под Slack/email/код
- `08_voice_snippets.md` — голосовое расширение текста
- `09_ten_vad.md` — точнее WebRTC VAD

**Tier 3 — точечные улучшения:**
- `10_punctuation_fallback.md` — пунктуация локально без LLM
- `11_srt_export.md` — субтитры
- `12_explorer_file_mode.md` — транскрибировать файл из проводника
- `13_youtube_transcribe.md` — URL → текст
- `14_local_http_api.md` — REST/WS для интеграций
- `15_auto_delete_history.md` — privacy / housekeeping
- `16_whisper_mode.md` — режим тихой речи
- `17_voice_commands_inline.md` — "удали последнее слово", "новый абзац"
- `18_cursor_aware_formatting.md` — регистр и пробел у курсора
- `19_course_correction.md` — «нет, я имел в виду…»

**Tier 4 — расширение use-case (опционально):**
- `20_system_audio_loopback.md` — meeting-mode через WASAPI
- `21_wake_word.md` — "Эй, Talker"

**Предложено — точность распознавания / «чтобы не путались слова» (черновики, не реализовано):**
- `22_replacement_dictionary.md` — детерминированная замена после STT (`клод → Claude`) 🔥
- `23_glossary_in_cleanup_prompt.md` — словарь терминов в промпт LLM-очистки (омофоны) 🔥
- `24_number_normalization_itn.md` — числа/даты цифрами («двадцать пять процентов» → «25 %») 🔥
- `25_foreground_context_prompt.md` — текст на экране как контекст для Whisper 🔥
- `26_phonetic_dictionary_match.md` — ловить искажения по звучанию, без перечисления вариантов 👍
- `27_ru_en_code_switching.md` — латиница для IT-терминов в русской речи 👍
- `28_history_vocab_mining.md` — авто-предложение слов в словарь из истории 👍

> Связка **22 + 23 + 24** — самый дешёвый и заметный набор; 22/26/27/28 делят одно хранилище `[[replacement]]`.

**Из исследования (`research/2026-05-29_what-to-add-next.md`, не реализовано):**
- `29_gigaam_russian_stt.md` — GigaAM как STT для русского: ~8.4% WER vs ~16% Whisper 🔥
- `30_screen_context_ocr.md` — screen-context через локальный OCR (усиливает 25) 🔥
- `31_meeting_mode_diarization.md` — митинг-режим + локальная диаризация «кто говорил» 👍
- `32_voice_to_agent_mcp.md` — голос → действия через MCP (поверх Command Mode) 👍
- `33_paste_fallback_recovery.md` — restore буфера, «вставить последний транскрипт», per-app вставка 🔥
- `34_low_latency_decoding.md` — спекулятивный декод + post-stream refinement 👍

> Папка `research/` — полные отчёты deep-research харнесса (источники, проверка фактов). Шорт-лист отдачи: **29 → 33 → 30 → 34 → 32 → 31**.

**Снимок ранней версии (историческое — сверяйтесь с кодом):**
- `35_local_cleanup_gpu_faithguard.md` — этап, когда очистка шла через локальную
  LLM; тогда же добавлены **faithguard** (сторож верности) и **profanity**
  (маскировка мата). LLM-очистка позже убрана ради локальности;
  faithguard/profanity остались в коде.

## Как этим пользоваться

1. Выбираем фичу → читаем файл → если 🟢, начинаем кодить.
2. По ходу работы — короткие отметки в файле «что пошло не так» (в конце, секция Notes).
3. После завершения — переименовываем `NN_*.md` → `NN_*_DONE.md`.

## Внедрено в коде

✅ **01 Streaming partial inserts** — `_BgJob` с `on_chunk` callback. При `output.streaming = true` каждые ~3 сек декодированный фрагмент вставляется через injector. На отпускании клавиши, если LLM-cleanup изменил текст — Backspace × N + повторная вставка финального текста.
✅ **02 Command Mode** — `main.py:_on_command_press/_process_command`, `cleaner.py` принимает `system_prompt=`. Хоткей + системный промпт настраиваются в Settings.
✅ **03 Parakeet V3** — `parakeet_engine.py` (NeMo wrapper, ленивый import), `transcriber.py` с выбором движка. Опция «Движок» в Settings. Без установки NeMo выдаёт понятную ошибку.
✅ **04 UIA direct injection** — `injector.py` с каскадом UIA → SendInput Unicode → clipboard+Ctrl+V. Защита от password-полей через UIA. Метод выбирается в Settings (`auto` по умолчанию).
✅ **05 Custom Vocabulary** — `vocabulary.py`, передаётся как `initial_prompt` в Whisper, UI в Settings.
✅ **06 Auto-learning dictionary** — кнопка «✎ Поправить» в `PasteFallbackBubble`, `vocabulary.extract_learnable` извлекает имена/термины через diff, добавляются в словарь и применяются к Whisper мгновенно.
✅ **07 Per-app modes** — `modes.py` (foreground detection + matching + watcher), `ModeConfig` в config, JSON-редактор в Settings, индикатор `Talker · slack` в FlowBar idle.
✅ **08 Voice Snippets** — `snippets.py`, exact (fuzzy) / prefix / anywhere. Exact пропускает LLM cleanup. UI в Settings.
✅ **09 TEN-VAD** — `recorder.py:_make_vad`, auto-fallback на webrtcvad, выбор в Settings.
✅ **10 Punctuation fallback** — `punctuation.py` (heavy backend через `deepmultilingualpunctuation` + heuristic-fallback), `PunctuationCleaner` авто-вставляется в `build_cleaner_chain` перед NoopCleaner. Чекбокс в Settings.
✅ **11 SRT/VTT/JSON export** — `exporters.py`, расширения в History Export dialog.
✅ **12 File mode + Explorer** — `file_mode.py` CLI + GUI прогресс + registry, кнопки в Settings.
✅ **13 YouTube URL transcribe** — `url_transcribe.py` (lazy yt-dlp), `UrlTranscribeWindow` в `ui.py`, пункт в tray menu.
✅ **14 Local HTTP API** — `api_server.py` (lazy FastAPI/uvicorn), token auth с авто-генерацией, 127.0.0.1 bind, endpoints: /health /history /transcribe /clean /vocabulary. UI секция в Settings.
✅ **15 Auto-delete history** — `HistoryManager._prune` с retention_days, on_quit_clear.
✅ **16 Whisper Mode** — `main.py:_toggle_whisper_mode`, gain в `Recorder`/`ContinuousListener`, tray-чекбокс.
✅ **17 Inline voice commands** — `voice_commands.py`, marker-префикс «talker» (опц. tail-standalone), действия `insert` / `key`, 19 встроенных, CRUD через TOML.
✅ **18 Cursor-aware formatting** — `cursor_format.py` читает контекст у курсора через UIA и фиксит регистр/пробел; fallback на capitalize-first без UIA.
✅ **19 Course correction (Backtrack)** — `backtrack.py`, эвристика на ru/en паттерны до LLM cleanup.
✅ **20 System audio loopback** — `loopback_recorder.py` (WASAPI через pyaudiowpatch); опция `audio.source = mic/system` в Settings, Recorder делегирует.
✅ **21 Wake word** — `wake_word.py` через openwakeword; на trigger запускается continuous-сессия на N сек.
✅ **UI font scaling** — `ui.py:_UiScale/_f/_s`, кнопки A−/A+ в History, поле в Settings, дефолт ×1.3 (раньше был 2.0, по запросу уменьшен).

## Доработки сверх исходных концептов

Эти штуки появились по итеративным запросам, документации в отдельных файлах нет — спецификация фактически совпадает с реализацией.

**🎚 Audio ducker (приглушение во время записи)**
- `audio_ducker.py` с двумя бэкендами: `master` (системный output volume через raw COM `IAudioEndpointVolume`) и `sessions` (per-app через pycaw `ISimpleAudioVolume`). По умолчанию `master` — приглушает ВСЁ (включая системные звуки), уровень `duck_level = 0.0` = full mute.
- Конфиг: `[audio] duck_other_apps`, `duck_level`, `duck_mode`. UI чекбокс + поле уровня в Settings.
- Вызывается в `_on_press` / `_start_continuous` / `_on_command_press`; restore в соответствующих stop-функциях.

**🎙 Виджет-pill (FlowBar) — настраиваемый компактный**
- `WidgetConfig`: `scale` (масштаб ×UI scale), `opacity`, `show_listening_label`, `show_glow`, `pos_x/pos_y` (запоминание позиции после drag через debounced `_persist_position`).
- Минимальная отрисовка: микрофонный глиф в idle, waveform в recording/listening, spinner в processing/loading.
- Без текстов «Talker», «Слушаю» и т.д. (опционально включаемых).
- Двойной клик → History; одиночный — только drag; ПКМ → расширенное меню.

**🎛 ControlBubble (отдельные ✕/✓ для записи)**
- Отдельный Toplevel слева от pill, появляется при state=recording.
- Кнопки ✕ (отмена записи без вставки) и ✓ (стоп + обработать) с большим gap между ними.
- Размер уменьшен ×40%, alpha 0.75, gap 60 px от pill.

**💬 Bubble «копировать» — умная**
- `output.bubble_mode = "on_failure" | "always" | "off"`.
- В режиме `on_failure` показывается **только** если injection не сработал ИЛИ если юзер активно печатал в момент попытки вставки (см. typing-guard ниже).
- Позиция: посередине внизу экрана.
- Auto-hide через **7 с с обратным отсчётом** в meta-лейбле.
- Hover приостанавливает таймер.
- Alpha 0.75 (на 20% прозрачнее предыдущей 0.95).
- Кнопка «✎ Поправить» открывает inline-editor; правки feed'ятся в auto-learning vocabulary.

**⌨ Typing-guard (не вставляем, когда юзер печатает)**
- В `_on_key` записываем timestamp последней «обычной» клавиши (не модификатор / не PTT / не continuous combo).
- В `_paste`: если прошло < 1.5 сек с последнего нажатия — возвращаем `"skipped_typing"`. Bubble показывается вместо вставки.

**🔁 Single-instance + force-exit**
- При старте `_kill_other_talker_instances()` через `psutil` ищет другие python с нашим cwd + main.py в cmdline → `p.kill()`. Новый запуск всегда супрессит старый.
- В `_quit`: после нормального teardown — `threading.Timer(1.5, os._exit)` гарантированно убивает процесс если что-то держит (keyboard hook, uvicorn, pyaudio).

**🖥 DPI awareness**
- `ctypes.windll.shcore.SetProcessDpiAwareness(2)` на старте — крупные иконки трея и pill чётче на high-DPI экранах.

**📊 Continuous = single_shot (по умолчанию)**
- `continuous.mode = "single_shot"`: ctrl+alt+space теперь стартует ОДНУ длинную запись через обычный `Recorder` (не ContinuousListener), вставляет один блок при остановке. Waveform пляшет от живого RMS.
- Старый VAD-режим оставлен как `mode = "vad_segments"` (опция).
- Кнопка ✓ корректно завершает single_shot continuous.

**🔇 Шумодав adaptive**
- `audio.nr_mode = "non_stationary"` (default) — `noisereduce` адаптивно ловит меняющийся шум (фен, машины, кондей). `nr_strength = 0.85` — баланс между чисткой и сохранением согласных.

**🧠 Consistency check (2-й проход LLM)**
- `output.consistency_check = false` (default). Если включить — после основного cleanup идёт второй проход через cleaner-chain с отдельным промптом (`cleaner.CONSISTENCY_PROMPT`): чинит логические нестыковки и ослышки распознавания по смыслу.
- Чекбокс в Settings → «Выходные данные».

**⏱ Long-form тексты (до 120 минут)**
- `MAX_RECORDING_SEC = 7200`.
- `_BgJob` адаптивный: `_INTERVAL_SHORT = 3` для < 5 мин, `_INTERVAL_LONG = 15` для > 5 мин. CPU не на 100% всё время.
- `transcriber.py`: для аудио > 60 с отключается `condition_on_previous_text` (защита от Whisper-loop'ов). `compression_ratio_threshold = 2.0` (был 2.4) — жёстче режет повторы.
- `_process`: bg_job результат используется только для записей > 20 с; короткие всегда full re-decode (контекст важнее).
- Injector: текст > 500 символов принудительно через clipboard (SendInput по символу не масштабируется).

**🚀 OpenRouter Quick Setup в Settings**
- Синий блок «Быстрый старт OpenRouter (бесплатно)» с пошаговой инструкцией и кнопкой «Открыть openrouter.ai/keys» (открывает webbrowser).
- Поле API Key имеет кнопку «📋 Вставить» — берёт из буфера обмена.

**📦 Loading-toast (есть, но не используется)**
- `LoadingWindow` класс в `ui.py` остался в коде, по запросу юзера сейчас никуда не подключён — индикация «занят» полностью через спиннер в самом pill.

**🪟 Modal Settings/History**
- Окна центрируются на экране при открытии, cap по размеру 80-85% от экрана.
- Кнопки `A+` / `A−` в History header — мгновенный font scale без перезагрузки окна (rebuild widgets in-place).
- Поле «Масштаб шрифта» в Settings + кнопки `+`/`−`/`×1.0` (instant apply, in-place rebuild).

**🧰 Hotkey detection**
- `keyboard.hook(...)` + `keyboard.add_hotkey(...)` в свежей версии `keyboard` конфликтуют — `add_hotkey` молча перестаёт работать.
- Перешли на ручную детекцию ctrl+alt+space внутри того же `_on_key`: отслеживаем модификаторы (`_mods_held`), при space+ctrl+alt → `_toggle_continuous()`. Флаг `_cont_combo_armed` гарантирует ровно один trigger per press.
