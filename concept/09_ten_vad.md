# 09. Замена WebRTC VAD на TEN-VAD

> В Continuous-режиме сейчас используется webrtcvad (GMM 2014 г). TEN-VAD (2025, Apache 2.0) — DNN, точнее, ниже латентность, меньше памяти.

**Категория:** Tier 2 — easy win, чувствуется в Continuous-режиме.
**Готовность концепта:** 🟢 High.

---

## Зачем

`webrtcvad-wheels` сейчас в зависимостях. WebRTC VAD — лёгкий (158 KB), быстрый, но **GMM-based**, FAR/FRR хуже современных DNN.

TEN-VAD (TEN-framework/ten-vad) — Apache 2.0, DNN-based:
- Lower computational complexity than Silero VAD.
- Higher precision than both WebRTC и Silero.
- Lower end-to-end latency.

Picovoice 2026 comparison: TEN-VAD = best in class по latency × accuracy.

В Continuous-режиме это прямо чувствуется — реже отрабатывает на междометиях, реже режет конец фразы.

---

## Технический подход

### Установка

```
pip install ten-vad
```

### API ten-vad

```python
from ten_vad import TenVad

vad = TenVad(
    sample_rate=16_000,
    hop_size=160,        # 10 ms frames
    threshold=0.5,
)

# Per-frame probability
probability, is_speech = vad.process(audio_chunk_int16)
```

vs текущий:
```python
import webrtcvad
vad = webrtcvad.Vad(aggressiveness=1)
is_speech = vad.is_speech(pcm_int16_30ms, 16_000)
```

### Различия в обработке

- WebRTC: frames по 10/20/30 мс, bool out.
- TEN-VAD: frames по 16 мс (256 samples при 16 кГц), float probability + bool.

Это значит наш `_FRAME_MS = 30` нужно изменить на 16 (или подобрать).

### Архитектурное место

В `recorder.py:ContinuousListener`:

```python
def __init__(self, ..., vad_engine: str = "ten"):
    if vad_engine == "ten":
        from ten_vad import TenVad
        self._vad = TenVad(sample_rate=16000, hop_size=256, threshold=...)
        self._FRAME_SAMPLES = 256
    else:
        import webrtcvad
        self._vad = webrtcvad.Vad(aggressiveness)
        self._FRAME_SAMPLES = 480   # 30ms

    self._classify = self._classify_ten if vad_engine == "ten" else self._classify_webrtc

def _classify_ten(self, chunk: np.ndarray) -> bool:
    pcm = (chunk * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
    prob, is_speech = self._vad.process(pcm)
    return is_speech

def _classify_webrtc(self, chunk: np.ndarray) -> bool:
    pcm = (chunk * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
    try:
        return self._vad.is_speech(pcm, self._SAMPLE_RATE)
    except Exception:
        return False
```

### Маппинг `aggressiveness` (0..3) → `threshold`

WebRTC aggressiveness 0..3 — четыре пресета. TEN-VAD — непрерывный threshold 0..1.

Соответствие (эмпирически, нужно подкрутить):

| webrtcvad | ten-vad threshold |
|---|---|
| 0 (мягкий) | 0.30 |
| 1 (умеренный) | 0.45 |
| 2 (жёсткий) | 0.60 |
| 3 (очень жёсткий) | 0.75 |

В UI оставляем те же 0..3 для совместимости, маппим в threshold.

### Конфиг

```toml
[continuous]
silence_secs       = 1.2
vad_aggressiveness = 1
vad_engine         = "ten"     # "ten" | "webrtc"
```

`vad_engine` опционально — дефолт "ten", если установлено, иначе "webrtc".

### Fallback

Если `import ten_vad` падает (пользователь не доустановил) — автоматический fallback на webrtcvad с предупреждением в логе.

---

## Архитектура

**Изменения:**
- `recorder.py:ContinuousListener` — параметризованный backend (см. выше).
- `config.py:ContinuousConfig.vad_engine`.
- `ui.py:SettingsWindow` — combobox «VAD engine: ten / webrtc».
- `requirements.txt` — `ten-vad>=1.0` (если есть на PyPI; иначе fork URL).

---

## Зависимости

```
ten-vad>=1.0   # https://github.com/TEN-framework/ten-vad
```

Проверить: на момент работы — pip install ten-vad существует? Если нет — install из git.

Размер: ~20 MB (ONNX модель + runtime).

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| ten-vad не установлен | Auto-fallback на webrtcvad, лог warning. |
| Threshold подобран плохо для конкретного юзера | UI оставляет ту же шкалу 0..3, юзер крутит — ощутимо влияет. |
| Frame size 16 ms vs 30 ms | Меняем `_FRAME_SAMPLES` соответственно, ring buffer pre-speech подкручиваем (вместо 10 фреймов × 30мс = 300мс держим 19 × 16мс ≈ 300мс). |
| Очень тихое окружение | TEN-VAD реже даёт false positives на нём → юзер думает «не записывает». Слегка снижаем threshold для quiet-mode (концепт 16). |

---

## Acceptance criteria

- В Continuous-режиме при `vad_engine=ten` сегменты режутся аккуратнее, чем при webrtc.
- Smoke test: тихие междометия «эээ» не запускают сегмент в обоих, но TEN заметно лучше на быстрой речи.
- Без установленного ten-vad приложение работает на webrtcvad.

---

## Сложность

- ~2–3 часа, ~100 LOC.
- В основном refactoring `ContinuousListener` под параметризованный backend.

---

## Открытые вопросы

- Доступна ли Silero VAD как третий вариант? Да, через `silero-vad` (torch hub). Тянет torch, не стоит ради третьего backend'а. Skip.
- Бенчмарк TEN vs WebRTC на нашем сценарии (русская диктовка): сделать после интеграции, отчёт в концепте.

---

## Источники

- [TEN-VAD GitHub](https://github.com/TEN-framework/ten-vad)
- [TEN-VAD HuggingFace](https://huggingface.co/TEN-framework/ten-vad)
- [Picovoice VAD comparison 2026](https://picovoice.ai/blog/best-voice-activity-detection-vad/)
- [webrtcvad-wheels](https://pypi.org/project/webrtcvad-wheels/)
