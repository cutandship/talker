# -*- coding: utf-8 -*-
"""Эмпирический зонд: как GigaAM v3 РЕАЛЬНО слышит англоязычные термины/бренды,
с учётом «слышит по-разному» (много прочтений) и фильтром омонимов.

Пайплайн на термин:
    канон → НЕСКОЛЬКО RU-карьеров × НЕСКОЛЬКО голосов (edge-tts)
          → ffmpeg/pydub → 16 кГц mono → GigaAM v3-e2e-rnnt
          → снять карьер-префикс → множество кириллических вариантов.

Классификация каждого варианта:
    already   — GigaAM сам выдал канон/латиницу (правило не нужно);
    unrecover — схлопнут/обрезан до 1–2 букв или союза (ИИ→«и»): словарём не чинится;
    collision — частотное русское слово (wordfreq): мапить НЕЛЬЗЯ (сломает речь);
    safe      — годный мисхир → в from_ правила замены.

Старые ~70 вариантов в replacements.py снимались на v2 — здесь пересъём под v3.

Запуск:
    python tests/probe_gigaam.py                 # 16 sample-терминов, 2 голоса
    python tests/probe_gigaam.py --full          # все 178 из dict_terms.py
    python tests/probe_gigaam.py --voices 1       # быстрее

Артефакты в tests/:
    dict_v3_rules.json     — обновлённый default_replacements (canon → safe-варианты)
    dict_v3_report.md      — по каждому термину: все варианты + класс + freq
    dict_v3_collisions.md  — омонимы (исключены, на ручной разбор)
    dict_v3_unrecover.md   — необратимые (ИИ/ChatGPT/NVIDIA) под STT-биасинг
    gigaam_probe.json      — сырые выходы
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# карьеры: префикс из стабильных слов + термин В КОНЦЕ (хвост не слипнется).
CARRIERS = [
    ("я установил", "я установил {term}"),
    ("открой",      "открой {term}"),
]
VOICES = ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]

SAMPLE = [
    "Claude", "ChatGPT", "OpenAI", "Anthropic", "Gemini", "Copilot",
    "Microsoft", "GitHub", "Python", "Docker", "Kubernetes", "React",
    "NVIDIA", "Linux", "Telegram", "ИИ",
]

COLLISION_ZIPF = 3.0          # ≥ этого = частотное рус. слово = омоним
_MIN_SAFE_LEN = 3
_CONJ = {"и", "а", "но", "в", "о", "у", "к", "с", "я", "от", "до", "по", "за",
         "из", "на", "не", "то", "же", "ли", "бы"}

_PUNCT = re.compile(r"[.,!?;:«»\"'()\-–—…]")
_HAS_CYR = re.compile(r"[а-яё]")
_HAS_LAT = re.compile(r"[a-z]")


def _norm(s: str) -> str:
    s = _PUNCT.sub(" ", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


# ── омоним-чекер: wordfreq, иначе корпус-частоты ─────────────────────────────
def load_freq():
    try:
        from wordfreq import zipf_frequency
        return ("wordfreq", lambda w: zipf_frequency(w, "ru"))
    except Exception:
        import collections
        txt = (ROOT / "_corpus" / "clean.txt").read_text(encoding="utf-8").lower()
        cnt = collections.Counter(re.findall(r"[а-яё]{2,}", txt))
        total = sum(cnt.values()) or 1
        import math

        def zipf_like(w):           # грубый zipf из корпуса (фолбэк)
            c = cnt.get(w, 0)
            if not c:
                return 0.0
            return math.log10(c / total * 1e9)
        return ("corpus", zipf_like)


async def _synth(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice).save(str(path))


def _load_pcm16k(mp3):
    import numpy as np
    from pydub import AudioSegment
    seg = AudioSegment.from_file(mp3).set_frame_rate(16000).set_channels(1).set_sample_width(2)
    return np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0


def _strip(prefix, full_norm):
    pw, words = prefix.split(), full_norm.split()
    i = 0
    for p in pw:
        if i < len(words) and words[i] == p:
            i += 1
        else:
            break
    return " ".join(words[i:]).strip()


def classify(term, variant, freq):
    """→ (bucket, zipf). bucket ∈ already|unrecover|collision|safe."""
    t = term.lower()
    v = variant.strip()
    if not v:
        return "unrecover", 0.0
    # уже канон: латиница или точное совпадение с каноном
    if v == t or v.replace(" ", "") == t.replace(" ", ""):
        return "already", 0.0
    if _HAS_LAT.search(v) and not _HAS_CYR.search(v):
        return "already", 0.0          # GigaAM сам выдал латиницу
    # необратимо: 1–2 буквы или чистый союз/предлог
    if len(v) < _MIN_SAFE_LEN or v in _CONJ:
        return "unrecover", 0.0
    z = freq(v)
    if z >= COLLISION_ZIPF:
        return "collision", z
    return "safe", z


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--voices", type=int, default=2)
    ap.add_argument("--terms", type=str, default="")
    args = ap.parse_args()

    if args.terms:
        terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    elif args.full:
        from dict_terms import all_terms
        terms = all_terms()
    else:
        terms = SAMPLE
    voices = VOICES[: max(1, min(args.voices, len(VOICES)))]
    carriers = CARRIERS

    freq_kind, freq = load_freq()
    print(f"GigaAM-зонд: {len(terms)} терминов × {len(voices)} голос × "
          f"{len(carriers)} карьер · омонимы: {freq_kind}", flush=True)

    from gigaam_engine import GigaamEngine
    print("гружу GigaAM v3-e2e-rnnt…", flush=True)
    eng = GigaamEngine(model_name="gigaam-v3-e2e-rnnt", quantization="int8")
    eng.warmup()
    print("модель готова, синтез+распознавание…", flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="ttsprobe_"))
    raw = {}
    classified = {}        # term → {variant: (bucket, zipf)}
    t0 = time.time()
    for ti, term in enumerate(terms):
        variants = {}
        outs = {}
        for vi, voice in enumerate(voices):
            for ci, (prefix, tmpl) in enumerate(carriers):
                mp3 = tmp / f"{ti}_{vi}_{ci}.mp3"
                try:
                    asyncio.run(_synth(tmpl.format(term=term), voice, mp3))
                    full = eng.transcribe(_load_pcm16k(mp3))
                except Exception as e:
                    outs[f"{voice}|{prefix}"] = f"<ERR {e}>"
                    continue
                outs[f"{voice}|{prefix}"] = full
                var = _strip(prefix, _norm(full))
                if var:
                    b, z = classify(term, var, freq)
                    # один вариант — берём «лучший» класс (safe важнее collision)
                    variants[var] = (b, round(z, 2))
        raw[term] = outs
        classified[term] = variants
        safe = [v for v, (b, _) in variants.items() if b == "safe"]
        print(f"  [{ti+1}/{len(terms)}] {term:16} safe={safe} all={list(variants)}",
              flush=True)

    dt = time.time() - t0
    print(f"\nготово за {dt:.0f}s", flush=True)

    # ── разложить по корзинам ──
    safe_map, collisions, unrecover, already = {}, {}, {}, {}
    for term, vs in classified.items():
        for v, (b, z) in vs.items():
            if b == "safe":
                safe_map.setdefault(term, []).append(v)
            elif b == "collision":
                collisions.setdefault(term, []).append((v, z))
            elif b == "unrecover":
                unrecover.setdefault(term, []).append(v)
            else:
                already.setdefault(term, []).append(v)

    # ── слить с текущим словарём проекта ──
    from replacements import default_replacements
    existing = {d["to"]: list(d.get("from_", [])) for d in default_replacements()}
    merged = dict(existing)
    new_terms, grown = 0, 0
    for term, sv in safe_map.items():
        cur = merged.get(term, [])
        before = len(cur)
        for v in sv:
            if v.lower() not in {c.lower() for c in cur}:
                cur.append(v)
        merged[term] = cur
        if term not in existing:
            new_terms += 1
        elif len(cur) > before:
            grown += 1
    rules = [{"to": t, "from_": f} for t, f in merged.items() if f]

    HERE.mkdir(exist_ok=True)
    (HERE / "gigaam_probe.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "dict_v3_rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

    def w(name, lines):
        (HERE / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    rep = [f"# Пересъём словаря на GigaAM v3\n",
           f"Терминов: {len(terms)} · {len(voices)} голос × {len(carriers)} карьер · "
           f"омоним-фильтр: {freq_kind} (zipf≥{COLLISION_ZIPF})\n",
           f"Новых терминов: **{new_terms}** · дополнено вариантов: **{grown}**\n",
           "| Канон | safe-варианты (v3) | омонимы (искл.) | необратимо | уже канон |",
           "|---|---|---|---|---|"]
    for term in terms:
        sv = safe_map.get(term, [])
        co = [f"{v}({z})" for v, z in collisions.get(term, [])]
        un = unrecover.get(term, [])
        al = already.get(term, [])
        rep.append(f"| {term} | {', '.join(sv) or '—'} | {', '.join(co) or '—'} "
                   f"| {', '.join(un) or '—'} | {', '.join(al) or '—'} |")
    w("dict_v3_report.md", rep)

    col = ["# Омонимы — НЕ добавлять в авто-замены (сломают обычную речь)\n",
           "| Канон | вариант | zipf | почему опасно |", "|---|---|---|---|"]
    for term, lst in collisions.items():
        for v, z in lst:
            col.append(f"| {term} | {v} | {z} | частотное русское слово |")
    w("dict_v3_collisions.md", col)

    un = ["# Необратимые — словарём не чинятся (STT уничтожил инфо)\n",
          "Чинить на уровне STT (Whisper initial_prompt-биасинг) или контекстом.\n",
          "| Канон | что осталось от слова |", "|---|---|"]
    for term, lst in unrecover.items():
        un.append(f"| {term} | {', '.join(lst)} |")
    w("dict_v3_unrecover.md", un)

    print(f"\nновых терминов: {new_terms}, дополнено: {grown}, "
          f"омонимов: {sum(len(v) for v in collisions.values())}, "
          f"необратимых: {len(unrecover)}")
    print("артефакты: tests/dict_v3_rules.json / dict_v3_report.md / "
          "dict_v3_collisions.md / dict_v3_unrecover.md")


if __name__ == "__main__":
    main()
