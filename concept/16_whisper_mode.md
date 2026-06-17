# 16. Whisper Mode (тихая речь)

> Режим для тихой / шёпотной речи в офисе, библиотеке, ночью. Подкручивает gain микрофона, чувствительность VAD, нормализацию.

**Категория:** Tier 3 — comfort, нишевая, но юзеры Wispr Flow её просят.
**Готовность концепта:** 🟢 High.

---

## Зачем

Тихая речь:
- В open-office при коллегах рядом.
- В библиотеке / кофейне.
- Ночью, когда домашние спят.
- Когда болит горло.

Дефолтные настройки Whisper и VAD ориентированы на нормальную громкость. На шёпоте:
- VAD пропускает начало (низкая энергия).
- Whisper иногда "разрешает" не-речь как речь, путается.
- Normalize вытягивает шум вместе с речью.

Whisper Mode — пресет настроек, переключаемый одной кнопкой.

---

## Технический подход

### Настройки в режиме

При активации Whisper Mode применяются:

| Параметр | Default | Whisper Mode |
|---|---|---|
| `audio.normalize` | true | true (агрессивнее, target_rms = 0.12) |
| `audio.noise_reduction` | false | **true** (включаем noisereduce) |
| `audio.mic_gain` (новый) | 1.0 | 2.5 |
| `continuous.vad_aggressiveness` | 1 | **0** (более мягкий) |
| `continuous.silence_secs` | 1.2 | 1.8 (больше тишины разрешено) |
| Transcribe `no_speech_threshold` | 0.6 | 0.4 (более терпимо) |

`mic_gain` — программное усиление перед нормализацией:

```python
audio = audio * mic_gain
audio = np.clip(audio, -1.0, 1.0)   # защита от клиппинга
```

### Активация

- Хоткей: `Ctrl+Alt+W` (или конфигурируемый).
- Или из tray menu чекбокс «🤫 Whisper Mode».
- Или из FlowBar — long-press на pill открывает context, там переключатель.

### Индикация

В FlowBar — иконка 🤫 рядом с лейблом. Цветовая схема чуть приглушённая (десатурация).

### Auto-detect (опция)

В реальности юзер сам знает, когда шёпотом говорит. Auto-detect через RMS-анализ возможен (если средний RMS низкий), но рискует false-positives. Дефолт — manual toggle.

---

## Архитектура

**Новые модули:** нет.

**Изменения:**
- `config.py`:
  - `AudioConfig.mic_gain: float = 1.0`.
  - `Config.whisper_mode_enabled: bool = False` (state).
- `recorder.py`:
  - В `Recorder.start()` принимать `gain`. В callback применять `gain * indata`.
- `transcriber.py`:
  - `no_speech_threshold` параметром.
- `main.py`:
  - `_toggle_whisper_mode()` — переключает флаг, обновляет recorder/transcriber параметры.
  - Хоткей `whisper_mode_hotkey` в hooks.
- `ui.py`:
  - В Settings → блок «Whisper Mode»: чекбокс + хоткей + override-параметры (advanced).

---

## Зависимости

`noisereduce` — уже в requirements.txt. Просто включаем.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Юзер забыл выключить, обычная речь | Громкость x2.5 → audio клиппится → Whisper плохо распознаёт. Auto-disable при detected RMS > threshold? Простой watchdog. |
| Whisper Mode + continuous | VAD threshold ниже → больше сегментов, больше шума. Документировать. |
| noisereduce заметно лагает (+1–2 сек) | Документировано в Settings. Юзер согласился, переходя в whisper mode. |

---

## Acceptance criteria

- Хоткей переключает режим, иконка в FlowBar меняется.
- При тестировании шёпотом распознавание заметно лучше.
- При обычной речи в whisper mode качество не катастрофически хуже.
- Все override-параметры можно подкрутить в advanced settings.

---

## Сложность

- ~3 часа, ~120 LOC.
- В основном — параметризация существующих компонентов.

---

## Открытые вопросы

- Wispr использует **отдельную обученную модель** для шёпота. У нас её нет, мы крутим параметры. Это хуже, но дешевле.
- Auto-detect — не делаем в v1, добавим если будет фидбек.

---

## Источники

- [Wispr Flow Whisper Mode](https://wisprflow.ai/features) (упоминание)
- [noisereduce library](https://github.com/timsainb/noisereduce)
- [Whisper no_speech_threshold tuning](https://github.com/SYSTRAN/faster-whisper/issues/100)
