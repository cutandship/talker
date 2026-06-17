# Исследование: что ещё добавить в Talker

> Отчёт сгенерирован deep-research харнессом (fan-out веб-поиск → выкачка → состязательная проверка фактов → синтез). Дата: 2026-05-29.

**Метод и охват:** 6 углов поиска → 27 источников выкачано → 128 claims извлечено → топ-25 проверено состязательно (3 голоса на claim, нужно ≥2 опровержения чтобы убить) → **23 подтверждено, 2 отклонено**.

**Вопрос:** какие новые функции/улучшения добавить в Talker (Windows, CPU-локальный, русский-первый, push-to-talk → STT → LLM-очистка → вставка), по 4 направлениям: гэпы vs конкуренты, свежие STT-движки, новые сценарии, UX потока ввода.

Производные ТЗ: **concept/29–34** (см. таблицу в конце).

---

## 1. Краткое резюме

Главная находка для русскоязычного сценария — **добавление STT-движка GigaAM v2/v3**: лучшая открытая модель для русского (средний WER ~8.4% против ~16–25% у Whisper-large-v3 на тех же бенчмарках), MIT-лицензия, CPU-инференс через сверхлёгкую `onnx-asr` (только numpy + onnxruntime, без PyTorch/Transformers/FFmpeg) — идеально под локальную CPU-архитектуру Talker. Из конкурентов главные перенимаемые killer-фичи: **screen-context через OCR** (VoiceInk), **встроенный meeting-режим с локальной диаризацией** (OpenWhispr), **voice-to-agent поверх диктовки** (OpenWhispr, OpenAI MCP-паттерн) и зрелые **fallback-механики вставки** (Wispr Flow). По латентности/точности — два «бесплатных» приёма: **спекулятивный декод Whisper** (~2x без потери WER) и **post-stream refinement** (быстрые partials + точный финал вторым проходом).

---

## 2. Выводы (findings)

### F1. GigaAM v2/v3 как русский STT-движок (через onnx-asr) → ТЗ 29
**Суть.** GigaAM — Conformer-foundation модель для русского (~220–240M параметров), CTC и RNN-T в версиях v1/v2/v3, MIT, предобучена на 50 000+ часов; v3 (ноябрь 2025) — end-to-end CTC/RNN-T с пунктуацией и ITN. По независимому бенчмарку Alphacephei (автор Vosk) **GigaAM2 CTC+LM = 8.42% среднего WER — лучший открытый результат**, обгоняя RNN-T (8.64%), Whisper-large-v3 (16.21%), v3-turbo (16.84%), NeMo Parakeet TDT V3 (16.02%), Canary V2 (20.24%). На AudioBooks: GigaAM2 3.4 vs Whisper 5.8; на Ru Librispeech 4.4 vs 9.5. E2E GigaAM-v3 выигрывает у Whisper-large-v3 side-by-side 70:30 (LLM-as-judge).
**Ценность.** ~2–3x меньше ошибок на русском (основной язык), меньше работы LLM-очистке.
**Подход.** `onnx-asr` (istupakov) нативно грузит GigaAM v2/v3 (CTC/RNN-T/E2E) + Vosk/Zipformer; зависимости только numpy+onnxruntime; int8 ~670 МБ диска / ~2 ГБ RAM; Win/Linux/Mac, x86/ARM. Веса `istupakov/gigaam-v2-onnx`, `gigaam-v3-onnx`. Добавить как backend рядом с whisper/parakeet, авто-выбор для ru.
**Сложность** средняя · **отдача** высокая · **confidence** high.
**Caveat.** Только русский (Whisper мультиязычен) → оставить Whisper/Parakeet fallback. Цифра 70:30 вендорская (Sber), но превосходство по WER подтверждено независимо.
**Источники:** github.com/salute-developers/GigaAM · github.com/istupakov/onnx-asr · alphacephei.com/nsh/2025/04/18/russian-models.html

### F2. Screen-context через локальный OCR → ТЗ 30 (усиливает концепт 25)
**Суть.** VoiceInk «Context Aware»: разовый скриншот → on-device OCR → текст подаётся в AI-этап, повышая точность/релевантность, особенно для терминов/имён на экране. Есть и Clipboard Context.
**Ценность.** Сильная реализация уже запланированного «контекста экрана» (концепт 25): лучше распознаёт имена/термины/ники, видимые на экране, без облака.
**Подход.** Скриншот активного окна → локальный OCR (Windows.Media.Ocr через WinRT, либо RapidOCR/Tesseract) → текст в `initial_prompt` STT и/или в промпт очистки.
**Сложность** средняя · **отдача** высокая · **confidence** high.
**Источник:** github.com/Beingpax/VoiceInk

### F3. Митинг-режим с локальной диаризацией → ТЗ 31
**Суть.** OpenWhispr: авто-детект Zoom/Teams/Meet/Webex + живая on-device диаризация по voice-fingerprint без облака. Модели `pyannote-segmentation-3.0` + `CAM++` (3D-Speaker), ONNX; отпечатки в SQLite; live-метки каждую ~1 с после ≥1.6 с речи + офлайн batch-уточнение.
**Ценность.** У Talker уже есть WASAPI loopback — не хватает авто-детекта приложений-звонков и разметки «кто что сказал». Превращает запись митинга в полноценные заметки.
**Подход.** Детект процессов + активность микрофона; диаризация через ONNX-версии моделей (укладывается в onnxruntime-стек из F1); SQLite для fingerprint; «live + batch».
**Сложность** высокая · **отдача** средняя-высокая · **confidence** high.
**Caveat.** Слаба при перекрытии речи, cold-start, репликах <0.8 с.
**Источник:** github.com/openwhispr/openwhispr

### F4. Voice-to-agent: диктовка → действия через MCP → ТЗ 32
**Суть.** Референс OpenAI Cookbook: голос → STT → Planner-агент → вызовы инструментов через **MCP** → (опц.) TTS. OpenWhispr уже делает на практике: named assistant поверх GPT/Claude/Gemini/локальных, авто-различение «команда агенту vs диктовка», вырезание имени агента из текста.
**Ценность.** Расширение Command Mode/inline-команд: голосом не только править текст, но и запускать действия. Сильно расширяет применение без слома основного flow.
**Подход.** Цепочка STT→Planner-LLM→MCP-инструменты поверх существующей LLM-цепочки; детект режима «команда vs диктовка».
**Сложность** высокая · **отдача** средняя-высокая · **confidence** high.
**Caveat.** Накопительная латентность ~0.8–2 с; «безопасность» MCP — цель, не гарантия (tool-poisoning/injection — нужны свои consent/authorization).
**Источники:** cookbook.openai.com/examples/partners/mcp_powered_voice_agents · github.com/openwhispr/openwhispr · modelcontextprotocol.io

### F5. Зрелые fallback-механики вставки → ТЗ 33
**Суть.** Wispr Flow (прямой ориентир Talker) на Win/Mac: вставка через буфер с **сохранением/восстановлением** прежнего содержимого (~500 мс после успеха; при отмене — не восстанавливает). При сбое авто-вставки — **«вставить последний транскрипт»** (Alt+Shift+Z, дефолт) и **скретчпад** (Win+Alt+S, opt-in), текст не теряется. Документирован список приложений, ломающих clipboard-вставку: Citrix, RDP, виртуалки, терминалы (cmd/PowerShell/Wezterm), EMR, **Outlook classic**.
**Ценность.** У Talker есть каскад UIA→SendInput→clipboard и защита password-полей. Не хватает: (а) гарантированного restore буфера, (б) «вставить последний транскрипт»/скретчпада как сети безопасности, (в) спец-обработки проблемных приложений (терминалы → Ctrl+Shift+V; Outlook classic → задержка 1–2 с).
**Подход.** Save/restore clipboard вокруг Ctrl+V; хоткей «paste last transcript» из истории; persistent-скретчпад; per-app таблица (класс окна → метод/задержка).
**Сложность** низкая-средняя · **отдача** высокая (ежедневный поток) · **confidence** high.
**Источник:** docs.wisprflow.ai/articles/7971211038

### F6. Латентность/точность «бесплатно»: спекулятивный декод + post-stream refinement → ТЗ 34
**Суть.**
- **Спекулятивный декод Whisper:** ~2x ускорение (EN large-v2 73→33 с, 2.2x; NL 117→62 с, 1.9x) при **математически идентичном выводе** main-модели — WER не меняется (EN 3.5→3.5, NL 12.8→12.8) при greedy/низкой температуре (режим Talker). Draft: distil-whisper/whisper-tiny.
- **Post-Stream Refinement** (Azure): двухпроходно — второй проход параллельно стримингу, partials быстрые, финал заменяется точным по полному аудиоконтексту. Развязывает «латентность vs точность».
**Ценность.** У Talker уже есть streaming partial inserts — эти приёмы ускоряют декод и повышают точность финала без новых модулей.
**Подход.** Спек-декод: draft-модель в Whisper-ветке (HF Transformers). Post-stream refinement как паттерн: эмитить partials, затем 2-м проходом по полному сегменту получать точный финал и заменять.
**Сложность** средняя · **отдача** средняя-высокая · **confidence** high.
**Caveat.** Бенчмарки спек-декода на GPU (T4) — выигрыш на CPU надо подтвердить; несовместим с large-v3 (ок для medium/large-v2). Post-stream refinement у Azure — preview, monolingual; берём как паттерн, не API.
**Источники:** huggingface.co/blog/whisper-speculative-decoding · techcommunity.microsoft.com (post-stream-refinement) · arXiv 2312.09463

### F7. Валидация курса (опенсорс-ориентиры) — без отдельного ТЗ
- **Handy** (MIT, Tauri/Rust, офлайн) поддерживает **Parakeet V3 как CPU-модель с авто-детектом языка** + Whisper Small/Medium/Turbo/Large (~5x real-time) — те же движки, что у Talker; «accessibility не за paywall».
- **Aqua Voice** — **inline LLM-очистка в реальном времени** (Streaming, ~850 мс) — подтверждает курс Talker «STT + LLM-очистка», а не голая транскрипция.
**Источники:** github.com/cjpais/Handy · aquavoice.com

---

## 3. Приоритизированный шорт-лист

1. **GigaAM через onnx-asr** (F1/ТЗ29) — лучший ROI для русского.
2. **Fallback-вставки** (F5/ТЗ33) — дёшево, высокая ежедневная отдача.
3. **Screen-context OCR** (F2/ТЗ30) — усилить запланированный «контекст экрана».
4. **Спек-декод + refinement** (F6/ТЗ34) — сперва замер на CPU.
5. **Voice-to-agent/MCP** (F4/ТЗ32) — после стабилизации STT/вставки.
6. **Митинг-режим/диаризация** (F3/ТЗ31) — самое трудозатратное, под спрос.

---

## 4. Отклонено проверкой (для прозрачности)

- ❌ **0-3** «Parakeet TDT V3 даёт 6.9/5.3 WER на русском» — опровергнуто; по Alphacephei Parakeet TDT V3 = 16.02% среднего WER (слабее GigaAM/Vosk). Источник: alphacephei.com/nsh/2025/04/18/russian-models.html
- ❌ **1-2** «Whispering поддерживает 3 локальных движка (Whisper.cpp + Parakeet via transcribe-rs + Moonshine)» — не подтвердилось. Источник: github.com/braden-w/whispering

---

## 5. Открытые вопросы

- Реальная скорость GigaAM int8 (RTF) и RAM в фоне на целевом CPU — нужен замер.
- Качество встроенной пунктуации/ITN GigaAM-v3 e2e vs текущая LLM-очистка — можно ли частично заменить LLM-этап на русском?
- Даёт ли спек-декод выигрыш на CPU (а не GPU)?
- Совместимость pyannote-seg-3.0 + CAM++ с чисто-onnxruntime стеком без PyTorch.
- Лицензии/размеры конкретных ONNX-весов GigaAM-v3-e2e для распространения с приложением.

---

## 6. Источники (27, по углам)

**Конкуренты:** techcrunch.com/2026/05/02/the-best-ai-powered-dictation-apps-of-2025 (secondary) · github.com/Beingpax/VoiceInk (primary) · tryvoiceink.com/best-superwhisper-alternatives · getvoibe.com/resources/voiceink-vs-wispr-flow · beingpax.medium.com/superwhisper-alternatives-2025 · wisprflow.ai/post/wispr-flow-vs-voiceink-2025
**STT-движки:** github.com/salute-developers/GigaAM (primary) · github.com/istupakov/onnx-asr (primary) · alphacephei.com/nsh/2025/04/18/russian-models.html (primary) · huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/discussions/3 (forum) · shunyalabs.ai/blog/benchmarking-top-open-source-speech-recognition-models · github.com/k2-fsa/sherpa-onnx/issues/3619 (forum)
**Voice-to-agent/сценарии:** cookbook.openai.com (mcp_powered_voice_agents) (primary) · github.com/openwhispr/openwhispr (primary) · github.com/cjpais/Handy (primary) · aquavoice.com (primary) · mindwiredai.com (voicebox)
**UX потока ввода:** docs.wisprflow.ai/articles/7971211038 (primary) · gladia.io/blog/measuring-latency-in-stt (secondary) · techcommunity.microsoft.com (post-stream-refinement) (primary) · huggingface.co/blog/whisper-speculative-decoding (primary)
**Опенсорс-практика:** github.com/braden-w/whispering · github.com/savbell/whisper-writer
**Биасинг/очистка для русского:** huggingface.co/kontur-ai/sbert_punc_case_ru · arxiv.org/html/2502.11572v1 · arxiv.org/pdf/2306.01942 · github.com/OpenNMT/CTranslate2/pull/1789

**Статистика:** 6 углов · 27 источников · 128 claims · 25 проверено · 23 подтверждено · 2 отклонено · 4 URL-дубля · 5 budget-dropped.
