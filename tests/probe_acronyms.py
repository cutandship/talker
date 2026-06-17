# -*- coding: utf-8 -*-
"""Акроним-зонд: как GigaAM v3 слышит акронимы в РАЗНЫХ произношениях.

Для каждого канона пробуем несколько форм произношения (латиница + кириллическая
«апи/ии/сми»), синтез edge-tts → GigaAM v3 → смотрим выход. Цель: понять, какая
форма мапится в словарь, а какая необратима (схлопнута/обрезана).

Запуск: python tests/probe_acronyms.py
Артефакт: tests/acronyms_probe.md
"""
from __future__ import annotations
import asyncio, re, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

VOICE = "ru-RU-SvetlanaNeural"
CARRIER = "я использую {f}"
PREFIX = "я использую"

# канон → варианты произношения (как реально говорят)
ACR = {
    "ИИ":   ["ИИ", "и и", "ии"],
    "API":  ["API", "апи", "эй пи ай"],
    "СМИ":  ["СМИ", "сми"],
    "URL":  ["URL", "урл", "ю эр эл"],
    "SQL":  ["SQL", "эс кю эль", "сиквел"],
    "HTML": ["HTML", "эйч ти эм эль"],
    "CSS":  ["CSS", "си эс эс"],
    "JSON": ["JSON", "джейсон"],
    "CPU":  ["CPU", "цэ пэ у", "си пи ю"],
    "GPU":  ["GPU", "джи пи ю"],
    "PDF":  ["PDF", "пэ дэ эф"],
    "USB":  ["USB", "ю эс би"],
    "IT":   ["IT", "айти"],
    "ML":   ["ML", "эм эль"],
    "VPN":  ["VPN", "вэ пэ эн"],
    "США":  ["США", "сша"],
}

_PUNCT = re.compile(r"[.,!?;:«»\"'()\-–—…]")


def _norm(s): return re.sub(r"\s+", " ", _PUNCT.sub(" ", s or "")).strip().lower()


def _strip(fn):
    pw, w = PREFIX.split(), fn.split(); i = 0
    for p in pw:
        if i < len(w) and w[i] == p: i += 1
        else: break
    return " ".join(w[i:]).strip()


async def _synth(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(path))


def _pcm(mp3):
    import numpy as np
    from pydub import AudioSegment
    seg = AudioSegment.from_file(mp3).set_frame_rate(16000).set_channels(1).set_sample_width(2)
    return np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0


def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    from gigaam_engine import GigaamEngine
    print("гружу GigaAM v3…", flush=True)
    eng = GigaamEngine(model_name="gigaam-v3-e2e-rnnt", quantization="int8")
    eng.warmup()
    print("готово, гоняю акронимы…", flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="acr_"))
    rows = ["# Акронимы на GigaAM v3 — что выдаёт по произношениям\n",
            "| Канон | произношение | выход GigaAM | вердикт |", "|---|---|---|---|"]
    results = {}
    n = 0
    for canon, forms in ACR.items():
        results[canon] = []
        for f in forms:
            mp3 = tmp / f"{n}.mp3"; n += 1
            try:
                asyncio.run(_synth(CARRIER.format(f=f), mp3))
                out = eng.transcribe(_pcm(mp3))
            except Exception as e:
                out = f"<ERR {e}>"
            heard = _strip(_norm(out))
            # вердикт
            cl = canon.lower()
            if heard == cl or heard.replace(" ", "") == cl:
                verdict = "✅ уже канон"
            elif not heard or len(heard.replace(" ", "")) < 2:
                verdict = "❌ схлопнут"
            elif re.search(r"[a-z]", heard):
                verdict = "🔡 латиница"
            else:
                verdict = "🔤 мапить"
            results[canon].append((f, heard, verdict))
            rows.append(f"| {canon} | {f} | {heard or '∅'} | {verdict} |")
            print(f"  {canon:5} «{f}» → «{heard}»  {verdict}", flush=True)

    (HERE / "acronyms_probe.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("\nартефакт: tests/acronyms_probe.md")


if __name__ == "__main__":
    main()
