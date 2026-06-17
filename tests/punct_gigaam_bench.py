# -*- coding: utf-8 -*-
"""Пунктуация в РЕАЛЬНОМ потоке: эталон vs GigaAM-native vs GigaAM→RUPunct.

В отличие от run_punct_bench (там пунктуацию срезали с эталона и восстанавливали),
здесь честный продакшн-поток:
    эталон (с пунктуацией) → edge-tts → GigaAM v3-e2e → его РОДНАЯ пунктуация.

Две системы на ОДНИХ И ТЕХ ЖЕ распознанных словах:
    native : выход GigaAM как есть (его пунктуация + капитализация);
    rupunct: берём слова GigaAM, срезаем его пунктуацию/регистр, переразмечаем
             RUPunct_small.

Скоринг против эталона (по словам через difflib): запятые P/R/F1, концевой знак,
капитализация, exact. Показывает, СТОИТ ли переразмечать уже-пунктуированный
GigaAM-выход моделью, или его родная пунктуация уже хороша.

Запуск: python tests/punct_gigaam_bench.py --n 250
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from run_punct_bench import (score_one, aggregate, to_asr_input,  # noqa: E402
                             restore_model_batch, _WORD)
from probe_gigaam import _load_pcm16k  # noqa: E402  (синтез — локальный SAPI)

CORPUS = ROOT / "_corpus" / "clean.txt"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250)
    args = ap.parse_args()

    refs = [r.strip() for r in CORPUS.read_text(encoding="utf-8").splitlines() if r.strip()][: args.n]
    print(f"корпус: {len(refs)} эталонов", flush=True)

    from gigaam_engine import GigaamEngine
    print("гружу GigaAM v3-e2e-rnnt…", flush=True)
    eng = GigaamEngine(model_name="gigaam-v3-e2e-rnnt", quantization="int8")
    eng.warmup()
    print("синтез + распознавание (это медленная часть)…", flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="punctgiga_"))
    # ── локальный синтез всех фраз через Windows SAPI (Irina), без сети ──
    import subprocess
    listf = tmp / "refs.tsv"
    listf.write_text("\n".join(f"{i}\t{r}" for i, r in enumerate(refs)), encoding="utf-8")
    print("SAPI-синтез (Irina, локально)…", flush=True)
    ps = HERE / "sapi_synth.ps1"
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
         "-ListFile", str(listf), "-OutDir", str(tmp)],
        capture_output=True, text=True)
    print(f"  SAPI: {res.stdout.strip()[-60:]} {res.stderr.strip()[-160:]}", flush=True)

    print("распознавание GigaAM…", flush=True)
    pairs_rg = []   # (ref, giga_native)
    t0 = time.time()
    for i, ref in enumerate(refs):
        wav = tmp / f"{i}.wav"
        if not wav.exists():
            continue
        try:
            giga = eng.transcribe(_load_pcm16k(wav))
        except Exception as e:
            print(f"  [{i+1}] skip: {e}", flush=True)
            continue
        if giga and giga.strip():
            pairs_rg.append((ref, giga.strip()))
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(refs)}  ({time.time()-t0:.0f}s)", flush=True)

    print(f"распознано {len(pairs_rg)} за {time.time()-t0:.0f}s", flush=True)

    refs_ok = [r for r, _ in pairs_rg]
    native = [g for _, g in pairs_rg]
    # RUPunct поверх СЛОВ GigaAM (срезаем его пунктуацию → переразмечаем)
    print("RUPunct переразмечает выход GigaAM…", flush=True)
    rup = restore_model_batch([to_asr_input(g) for g in native])

    rows = []
    for i in range(len(refs_ok)):
        a = score_one(refs_ok[i], native[i])
        b = score_one(refs_ok[i], rup[i])
        row = {"idx": i, "ref": refs_ok[i], "native": native[i], "rupunct": rup[i]}
        for k, v in a.items():
            row[f"nat_{k}"] = v
        for k, v in b.items():
            row[f"rup_{k}"] = v
        rows.append(row)

    nat = aggregate(rows, "nat")
    rua = aggregate(rows, "rup")

    HERE.mkdir(exist_ok=True)
    with (HERE / "punct_giga_cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    summary = {"n": len(rows), "model": "gigaam-v3-e2e-rnnt + RUPunct_small",
               "native": nat, "rupunct": rua}
    (HERE / "punct_giga_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def pct(x): return f"{x*100:.1f}%"
    md = [f"# Пунктуация в потоке: GigaAM-native vs GigaAM→RUPunct\n",
          f"Кейсов: **{len(rows)}** · поток: эталон→SAPI(Irina)→GigaAM v3-e2e→(RUPunct)\n",
          "| Метрика | GigaAM-native | GigaAM→RUPunct |", "|---|---|---|",
          f"| **Запятые F1** | **{pct(nat['comma_f1'])}** | **{pct(rua['comma_f1'])}** |",
          f"| Запятые precision | {pct(nat['comma_precision'])} | {pct(rua['comma_precision'])} |",
          f"| Запятые recall | {pct(nat['comma_recall'])} | {pct(rua['comma_recall'])} |",
          f"| Концевой знак тип | {pct(nat['end_type_accuracy'])} | {pct(rua['end_type_accuracy'])} |",
          f"| Капитализация | {pct(nat['cap_accuracy'])} | {pct(rua['cap_accuracy'])} |",
          f"| Полное совпадение | {pct(nat['exact_match'])} | {pct(rua['exact_match'])} |"]
    (HERE / "punct_giga_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("\n========== ИТОГ ==========")
    print(f"{'метрика':24}{'native':>12}{'+RUPunct':>12}")
    for label, k in [("запятые F1", "comma_f1"), ("запятые precision", "comma_precision"),
                     ("запятые recall", "comma_recall"), ("концевой знак", "end_type_accuracy"),
                     ("капитализация", "cap_accuracy"), ("exact", "exact_match")]:
        print(f"{label:24}{pct(nat[k]):>12}{pct(rua[k]):>12}")
    print("\nпримеры:")
    for r in rows[:8]:
        print(f"\n  эталон : {r['ref']}")
        print(f"  native : {r['native']}")
        print(f"  rupunct: {r['rupunct']}")


if __name__ == "__main__":
    main()
