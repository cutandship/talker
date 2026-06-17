# Third-party licenses

Talker itself is **MIT** (see [LICENSE](LICENSE)). It bundles, uses, or builds on
the components below — each keeps its own license.

## Bundled in this repository

| Component | What | License |
|-----------|------|---------|
| [Inter](https://rsms.me/inter/) | UI font (`assets/fonts/Inter-Variable.ttf`) | SIL Open Font License 1.1 — see [`assets/fonts/OFL.txt`](assets/fonts/OFL.txt) |
| [openWakeWord](https://github.com/dscripka/openWakeWord) (© 2022 David Scripka) | modified training scripts in `wake_training/openwakeword_patches/` | Apache-2.0 — see [`LICENSE`](wake_training/openwakeword_patches/LICENSE) + [`NOTICE`](wake_training/openwakeword_patches/NOTICE) |

### ⚠️ Wake-word feature models — NonCommercial
The trained wake models in `wake_training/out/final/*.onnx` run on openWakeWord's
shared feature models (`melspectrogram.onnx`, `embedding_model.onnx`, downloaded
by `wake_training/download_features.py`). Per openWakeWord, those pre-trained
feature components are **CC BY-NC-SA 4.0 (NonCommercial)** because of their
training-data terms — while openWakeWord's *code* is Apache-2.0. This
NonCommercial restriction applies to the wake-word feature stack only, **not** to
Talker's own MIT-licensed code. Review it before any commercial use of wake-word.

## Speech-to-text models (downloaded at runtime, not bundled)

| Model | License |
|-------|---------|
| [GigaAM v3](https://github.com/salute-developers/GigaAM) — default RU STT | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |

## Python dependencies (installed via pip — see `requirements.txt`)

All core dependencies are permissively licensed (MIT / BSD / Apache-2.0 / MPL-2.0)
and none impose copyleft on Talker: faster-whisper, onnx-asr, onnxruntime,
huggingface_hub, sounddevice, numpy, Pillow, pyperclip, psutil, tqdm, pystray,
fastapi, uvicorn, httpx, customtkinter, webrtcvad-wheels, noisereduce, keyboard.
Optional VAD backend [TEN-VAD](https://github.com/TEN-framework) is Apache-2.0.

---
*Not legal advice. If you redistribute Talker or use it commercially, verify each
upstream license yourself.*
