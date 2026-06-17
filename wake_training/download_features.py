# -*- coding: utf-8 -*-
"""Загрузка артефактов тренировки openWakeWord (resumable).

Порядок — от мелкого и критичного к гигантскому: под DPI-удушением мелочь
успевает проскочить в быстрое окно соединения, а 5-гигабайтный ACAV пусть
домалывается циклами сторожа (dl_loop.ps1) последним.
"""
import os
import urllib.request

from huggingface_hub import hf_hub_download

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

# [1] Мелочь с GitHub (он не душится) — модели для AudioFeatures.
URLS = [
    ("embedding_model.onnx",
     "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx"),
    ("melspectrogram.onnx",
     "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx"),
]
for name, url in URLS:
    dst = os.path.join(DATA, name)
    if not os.path.exists(dst):
        print(f"[1] {name}...")
        urllib.request.urlretrieve(url, dst)
    print("OK:", dst)

# [2] Валидация (~120 MB) — без неё train.py не стартует.
print("[2] validation features (~120 MB)...")
p = hf_hub_download(repo_id="davidscripka/openwakeword_features",
                    repo_type="dataset",
                    filename="validation_set_features.npy",
                    local_dir=DATA)
print("OK:", p)

# [3] ACAV100M (~5.4 GB) — негативы для тренировки; качается дольше всего.
print("[3] ACAV100M negative features (~5.4 GB)...")
p = hf_hub_download(repo_id="davidscripka/openwakeword_features",
                    repo_type="dataset",
                    filename="openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
                    local_dir=DATA)
print("OK:", p)

print("DOWNLOADS DONE")
