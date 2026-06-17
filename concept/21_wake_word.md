# 21. Wake-word activation

> Вместо хоткея — голосовая активация: «Эй, Talker» → начинается запись.

**Категория:** Tier 4 — для случаев, когда руки заняты. Спорная фича — false positives раздражают.
**Готовность концепта:** 🟡 Medium — выбор библиотеки требует прототипа.

---

## Зачем

Push-to-talk быстр, но требует:
- Рук на клавиатуре.
- Хоткея, который не конфликтует с другими.
- В fullscreen-играх / RDP может пропускаться.

Wake word: «Эй, Talker» → начинается push-to-talk. Удобно за рулём, в кухне, на кросс-апп workflow.

Trade-off: false positives. Каждый раз когда юзер говорит что-то похожее → запись. Это **раздражает**, поэтому wake word у Wispr / Superwhisper — опция, не default.

---

## Технический подход

### Библиотеки

**(a) openWakeWord** (David Scripka, Apache 2.0):
- Open-source, бесплатный.
- Pre-trained модели: "alexa", "hey jarvis", "computer", "ok google".
- **Custom wake word** через обучение на синтетических TTS-сэмплах + опционально real recordings.
- Лёгкий — ONNX, ~30 MB.
- Чувствителен к качеству custom models — нужно ~50+ сэмплов для нормального качества.

**(b) Picovoice Porcupine**:
- Платный для commercial, free tier для personal use (требует API key).
- Высочайшее качество, custom wake word за 10 минут.
- Лицензионные ограничения для open-source проектов — спорно.

**(c) Snowboy** — deprecated, не рекомендую.

**(d) Vosk small** в always-listening mode + match на ключевое слово:
- Простое решение, ~50 MB.
- Качество ниже purpose-built wake word.
- Жрёт CPU постоянно.

**Выбор:** **openWakeWord** с pre-trained "hey jarvis" → ребрендим в "hey talker" обучая custom model по их рецепту. v1: pre-trained "hey jarvis", v2: custom "эй талкер".

### Архитектура

Фоновый поток слушает микрофон **постоянно** (когда wake-mode включён):

```python
class WakeWordListener:
    def __init__(self, on_wake: Callable, model_path: str):
        from openwakeword.model import Model
        self._model = Model(wakeword_models=[model_path])
        self._on_wake = on_wake
        self._stream = None
    
    def start(self):
        self._stream = sd.InputStream(
            samplerate=16_000,
            channels=1,
            blocksize=1280,   # 80ms — openWakeWord stride
            dtype="int16",
            callback=self._cb,
        )
        self._stream.start()
    
    def _cb(self, indata, frames, time, status):
        prediction = self._model.predict(indata.flatten())
        for wake_name, score in prediction.items():
            if score > 0.5:   # configurable
                self._on_wake()
                # debounce — игнорировать следующие N секунд
                ...
```

При обнаружении wake → старт recorder + bg_job + `_set_state(RECORDING)`. Юзер далее говорит → завершение через silence detection или повторный wake.

### Конфликты с continuous mode

Они оба требуют listening always. Wake word работает на **каждом сэмпле**, continuous VAD — на сегментах. Можно объединить:

- Если wake-mode on: WakeWordListener слушает.
- При wake → continuous mode стартует.
- Continuous пишет до тишины > N сек → стоп, обратно в wake-listening.

### Чувствительность

В Settings:
- Слайдер «Wake threshold» (0.3 = чувствительно / много FP, 0.7 = строго / редкие FP).
- Cooldown: после wake event N секунд игнорируем повторные.

### Звуковая обратная связь

Юзер не видит экран, нужна аудио-индикация:
- При wake → короткий beep (Hz=1200, 80ms).
- Хотя юзер просил тишину! Конфликт. **Решение:** в wake mode — звуки явно opt-in, дефолт off, юзер сам решает (без них в hands-free нет понимания «услышал/нет»).

---

## Архитектура

**Новые модули:**
- `wake_word.py`:
  - `WakeWordListener` (см. выше).
  - Утилита для загрузки моделей.

**Изменения:**
- `main.py`:
  - В `__init__` — опциональный `WakeWordListener`.
  - При `cfg.wake.enabled = True` — старт listener'а.
- `config.py:WakeConfig` — enabled, model_path, threshold, cooldown.
- `ui.py:SettingsWindow` — секция «Голосовая активация».

---

## Зависимости

```
openwakeword>=0.6
onnxruntime>=1.18
```

~50 MB модели + runtime.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Юзер постоянно ругается дома, false positives | Настроить threshold выше. Bottleneck — качество модели. |
| Wake триггерится во время музыки / TV | openWakeWord слабее на noisy environments. Документировать ограничение. |
| Custom wake word не обучен / некачественный | Falls back на pre-trained "hey jarvis". Юзер выбирает в UI. |
| Конфликт wake + push-to-talk хоткей | Не конфликтуют — оба триггерят одну функцию. PTT всегда работает, wake — опционально. |
| Latency обнаружения wake (~300-500 мс) | После wake → запись стартует, но первые 300мс речи **уже сказаны**. **Решение:** keep rolling buffer (~1 сек) до wake event, prepend к записи. |
| Privacy concern: always listening | Документация: модель работает локально, аудио не покидает машину. Чекбокс в Settings явный. |

---

## Acceptance criteria

- Включил wake mode, сказал «hey jarvis» → запись стартовала.
- False positives < 1 per hour при threshold 0.5.
- Latency wake → начало записи < 500 мс (включая prepended buffer).
- Без wake mode CPU не растёт.
- Custom wake-word можно загрузить (пользователь обучил отдельно).

---

## Сложность

- ~6–8 часов, ~350 LOC.
- 2 часа — openWakeWord integration + prototype.
- 2 часа — rolling buffer + state machine с continuous.
- 2 часа — UI.
- 2 часа — обучение custom модели для "эй талкер" (offline-ная работа, можно не v1).

---

## Открытые вопросы

- Стоит ли вообще делать? False-positive raate реально раздражает. **Решение:** Tier 4, делаем только если есть пользовательский запрос.
- Multi-language wake words? openWakeWord — agnostic, custom model нужно обучать. Бонус.
- Cross-platform Linux/Mac — out of scope.

---

## Источники

- [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- [Custom wake word training tutorial](https://github.com/dscripka/openWakeWord/blob/main/docs/custom_models.md)
- [Picovoice Porcupine](https://picovoice.ai/platform/porcupine/)
- [RealtimeSTT wake word integration](https://github.com/KoljaB/RealtimeSTT#wakeword-recipes)
