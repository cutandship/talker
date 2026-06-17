# -*- coding: utf-8 -*-
"""Бенчмарк пунктуации: «сейчас» vs «сейчас + RUPunct_small».

Идея теста (изоляция задачи восстановления пунктуации):
  gold-предложение  →  убираем пунктуацию + lowercase (имитация сырого ASR)
                    →  восстанавливаем двумя способами и сравниваем с gold.

Две системы:
  current : punctuation.restore() из проекта (для RU = эвристика «заглавная +
            точка в конце», т.к. deepmultilingualpunctuation EN/DE/FR/IT и не
            ставит запятые) — это РЕАЛЬНОЕ поведение Talker сегодня для русского.
  model   : RUPunct/RUPunct_small (BERT token-classification, MIT) — кандидат
            в локальный RU-fallback (concept/10).

Метрики (против gold, выровнено по словам через difflib):
  - запятые: precision / recall / F1   (главная и самая трудная для RU)
  - концевой знак: present + type-correct (. vs ? vs !)
  - капитализация: пословная точность (ловит имена собственные в середине)
  - exact: доля предложений, восстановленных символ-в-символ как gold

Запуск:
    python tests/run_punct_bench.py --n 10        # быстрый прогон + 10 примеров
    python tests/run_punct_bench.py --n 10000     # полный прогон

Артефакты в tests/:
    punct_bench_cases.csv     — все кейсы: вход, оба выхода, поэкземплярные очки
    punct_bench_summary.json  — агрегированные метрики (машиночитаемо)
    punct_bench_summary.md    — та же сводка человекочитаемо
    punct_bench_samples.md    — N первых примеров красиво (вход/current/model/gold)
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))            # чтобы импортнуть punctuation.py проекта

import punctuation as proj_punct          # noqa: E402  «сейчас»

CORPUS = ROOT / "_corpus" / "clean.txt"

# ── деградация gold → ASR-вход (только пунктуация/регистр, без филлеров) ───────
_PUNCT = re.compile(r"[.,!?;:«»\"'()\[\]\-–—…]")


def to_asr_input(ref: str) -> str:
    """Имитация сырого STT-выхода: режем пунктуацию и регистр, схлопываем пробелы.
    Слова и их порядок НЕ трогаем — обе системы получают идентичный набор слов,
    значит выравнивание с gold честное."""
    s = _PUNCT.sub(" ", ref)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# ── система «сейчас» ──────────────────────────────────────────────────────────
def restore_current(text: str) -> str:
    return proj_punct.restore(text)


# ── система «модель» (RUPunct_small) ─────────────────────────────────────────
_classifier = None


def _load_model():
    global _classifier
    if _classifier is not None:
        return _classifier
    from transformers import pipeline, AutoTokenizer
    pt = "RUPunct/RUPunct_small"
    tk = AutoTokenizer.from_pretrained(pt, strip_accents=False, add_prefix_space=True)
    device = -1   # CPU: загрузка модели на GPU виснет на torch 2.12+cu130
    _classifier = pipeline("ner", model=pt, tokenizer=tk,
                           aggregation_strategy="first", device=device)
    return _classifier


def process_token(token, label):
    if label == "LOWER_O":
        return token
    if label == "LOWER_PERIOD":
        return token + "."
    if label == "LOWER_COMMA":
        return token + ","
    if label == "LOWER_QUESTION":
        return token + "?"
    if label == "LOWER_TIRE":
        return token + "—"
    if label == "LOWER_DVOETOCHIE":
        return token + ":"
    if label == "LOWER_VOSKL":
        return token + "!"
    if label == "LOWER_PERIODCOMMA":
        return token + ";"
    if label == "LOWER_DEFIS":
        return token + "-"
    if label == "LOWER_MNOGOTOCHIE":
        return token + "..."
    if label == "LOWER_QUESTIONVOSKL":
        return token + "?!"
    if label == "UPPER_O":
        return token.capitalize()
    if label == "UPPER_PERIOD":
        return token.capitalize() + "."
    if label == "UPPER_COMMA":
        return token.capitalize() + ","
    if label == "UPPER_QUESTION":
        return token.capitalize() + "?"
    if label == "UPPER_TIRE":
        return token.capitalize() + " —"
    if label == "UPPER_DVOETOCHIE":
        return token.capitalize() + ":"
    if label == "UPPER_VOSKL":
        return token.capitalize() + "!"
    if label == "UPPER_PERIODCOMMA":
        return token.capitalize() + ";"
    if label == "UPPER_DEFIS":
        return token.capitalize() + "-"
    if label == "UPPER_MNOGOTOCHIE":
        return token.capitalize() + "..."
    if label == "UPPER_QUESTIONVOSKL":
        return token.capitalize() + "?!"
    if label == "UPPER_TOTAL_O":
        return token.upper()
    if label == "UPPER_TOTAL_PERIOD":
        return token.upper() + "."
    if label == "UPPER_TOTAL_COMMA":
        return token.upper() + ","
    if label == "UPPER_TOTAL_QUESTION":
        return token.upper() + "?"
    if label == "UPPER_TOTAL_TIRE":
        return token.upper() + " —"
    if label == "UPPER_TOTAL_DVOETOCHIE":
        return token.upper() + ":"
    if label == "UPPER_TOTAL_VOSKL":
        return token.upper() + "!"
    if label == "UPPER_TOTAL_PERIODCOMMA":
        return token.upper() + ";"
    if label == "UPPER_TOTAL_DEFIS":
        return token.upper() + "-"
    if label == "UPPER_TOTAL_MNOGOTOCHIE":
        return token.upper() + "..."
    if label == "UPPER_TOTAL_QUESTIONVOSKL":
        return token.upper() + "?!"
    return token


def _clean_spaces(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.!?;:…])", r"\1", s)     # нет пробела перед знаком
    return s


def restore_model_batch(inputs: list[str], chunk: int = 500) -> list[str]:
    clf = _load_model()
    outs: list[str] = []
    n = len(inputs)
    for start in range(0, n, chunk):
        part = inputs[start:start + chunk]
        preds_batch = clf(part, batch_size=64)
        # при одиночном входе pipeline возвращает один список, нормализуем
        if part and isinstance(preds_batch, list) and preds_batch and isinstance(preds_batch[0], dict):
            preds_batch = [preds_batch]
        for preds in preds_batch:
            out = ""
            for item in preds:
                out += " " + process_token(item["word"].strip(), item["entity_group"])
            outs.append(_clean_spaces(out))
        print(f"  model: {min(start + chunk, n)}/{n}", flush=True)
    return outs


# ── метрики ───────────────────────────────────────────────────────────────────
_WORD = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+")
_TOKEN_TRAIL = re.compile(r"([А-Яа-яЁёA-Za-z0-9]+)([^\sА-Яа-яЁёA-Za-z0-9]*)")


def pairs(s: str):
    """[(word_norm, has_comma, end_mark, is_cap)] для строки.
    word_norm: lower + ё→е (для выравнивания). end_mark ∈ {'.','?','!',''}."""
    out = []
    for w, trail in _TOKEN_TRAIL.findall(s):
        has_comma = "," in trail
        if "?" in trail:
            end = "?"
        elif "!" in trail:
            end = "!"
        elif "." in trail or "…" in trail:
            end = "."
        else:
            end = ""
        out.append((w.lower().replace("ё", "е"), has_comma, end, w[:1].isupper()))
    return out


def score_one(ref: str, hyp: str) -> dict:
    rp, hp = pairs(ref), pairs(hyp)
    rw, hw = [p[0] for p in rp], [p[0] for p in hp]
    sm = difflib.SequenceMatcher(None, rw, hw, autojunk=False)

    comma_tp = comma_fp = comma_fn = 0
    cap_ok = cap_tot = 0
    aligned = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            # несравнимые слоты (почти не бывает — слова идентичны): запятые-gold = FN
            for k in range(i1, i2):
                if rp[k][1]:
                    comma_fn += 1
            for k in range(j1, j2):
                if hp[k][1]:
                    comma_fp += 1
            continue
        for k in range(i2 - i1):
            g, h = rp[i1 + k], hp[j1 + k]
            aligned += 1
            if g[1] and h[1]:
                comma_tp += 1
            elif h[1] and not g[1]:
                comma_fp += 1
            elif g[1] and not h[1]:
                comma_fn += 1
            cap_tot += 1
            if g[3] == h[3]:
                cap_ok += 1

    # концевой знак — по последнему токену
    g_end = rp[-1][2] if rp else ""
    h_end = hp[-1][2] if hp else ""
    end_present = 1 if h_end else 0
    end_type_ok = 1 if (h_end and h_end == g_end) else 0

    exact = 1 if _clean_spaces(ref) == _clean_spaces(hyp) else 0
    return {
        "comma_tp": comma_tp, "comma_fp": comma_fp, "comma_fn": comma_fn,
        "cap_ok": cap_ok, "cap_tot": cap_tot,
        "end_present": end_present, "end_type_ok": end_type_ok,
        "exact": exact, "g_has_end": 1 if g_end else 0,
    }


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def aggregate(rows: list[dict], key: str) -> dict:
    tp = sum(r[f"{key}_comma_tp"] for r in rows)
    fp = sum(r[f"{key}_comma_fp"] for r in rows)
    fn = sum(r[f"{key}_comma_fn"] for r in rows)
    p, r_, f = prf(tp, fp, fn)
    cap_ok = sum(r[f"{key}_cap_ok"] for r in rows)
    cap_tot = sum(r[f"{key}_cap_tot"] for r in rows)
    end_present = sum(r[f"{key}_end_present"] for r in rows)
    end_type_ok = sum(r[f"{key}_end_type_ok"] for r in rows)
    exact = sum(r[f"{key}_exact"] for r in rows)
    n = len(rows)
    return {
        "comma_precision": round(p, 4), "comma_recall": round(r_, 4),
        "comma_f1": round(f, 4), "comma_tp": tp, "comma_fp": fp, "comma_fn": fn,
        "cap_accuracy": round(cap_ok / cap_tot, 4) if cap_tot else 0.0,
        "end_present_rate": round(end_present / n, 4) if n else 0.0,
        "end_type_accuracy": round(end_type_ok / n, 4) if n else 0.0,
        "exact_match": round(exact / n, 4) if n else 0.0,
    }


# ── основной прогон ───────────────────────────────────────────────────────────
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # cp1251-консоль не печатает — / …
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="сколько предложений")
    ap.add_argument("--samples", type=int, default=10, help="сколько примеров в .md")
    args = ap.parse_args()

    refs = CORPUS.read_text(encoding="utf-8").splitlines()
    refs = [r.strip() for r in refs if r.strip()][: args.n]
    n = len(refs)
    print(f"корпус: {n} предложений (из {CORPUS})")

    inputs = [to_asr_input(r) for r in refs]

    t0 = time.time()
    print("current: восстанавливаю…")
    cur_out = [restore_current(x) for x in inputs]
    t_cur = time.time() - t0

    t0 = time.time()
    print("model: гружу RUPunct_small и восстанавливаю (первый раз — скачивание)…")
    mod_out = restore_model_batch(inputs)
    t_mod = time.time() - t0

    rows = []
    for i in range(n):
        cs = score_one(refs[i], cur_out[i])
        ms = score_one(refs[i], mod_out[i])
        row = {"idx": i, "n_words": len(_WORD.findall(refs[i])),
               "input": inputs[i], "ref": refs[i],
               "current": cur_out[i], "model": mod_out[i]}
        for k, v in cs.items():
            row[f"cur_{k}"] = v
        for k, v in ms.items():
            row[f"mod_{k}"] = v
        rows.append(row)

    cur_agg = aggregate(rows, "cur")
    mod_agg = aggregate(rows, "mod")

    # ── артефакты ──
    HERE.mkdir(exist_ok=True)
    # CSV — все кейсы
    csv_path = HERE / "punct_bench_cases.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = {
        "n_cases": n,
        "corpus": str(CORPUS),
        "model": "RUPunct/RUPunct_small",
        "current_backend": proj_punct._load_backend(),
        "timing_sec": {"current": round(t_cur, 2), "model": round(t_mod, 2)},
        "current": cur_agg,
        "model": mod_agg,
    }
    (HERE / "punct_bench_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # MD-сводка
    def pct(x):
        return f"{x * 100:.1f}%"
    md = []
    md.append(f"# Пунктуация: «сейчас» vs «сейчас + RUPunct_small»\n")
    md.append(f"Кейсов: **{n}** · корпус: `_corpus/clean.txt` · "
              f"current-backend: `{summary['current_backend']}` · "
              f"GPU-время модели: {t_mod:.1f}s\n")
    md.append("| Метрика | Сейчас | + RUPunct_small |")
    md.append("|---|---|---|")
    md.append(f"| Запятые — precision | {pct(cur_agg['comma_precision'])} | {pct(mod_agg['comma_precision'])} |")
    md.append(f"| Запятые — recall | {pct(cur_agg['comma_recall'])} | {pct(mod_agg['comma_recall'])} |")
    md.append(f"| **Запятые — F1** | **{pct(cur_agg['comma_f1'])}** | **{pct(mod_agg['comma_f1'])}** |")
    md.append(f"| Концевой знak — есть | {pct(cur_agg['end_present_rate'])} | {pct(mod_agg['end_present_rate'])} |")
    md.append(f"| Концевой знак — тип верный (. ? !) | {pct(cur_agg['end_type_accuracy'])} | {pct(mod_agg['end_type_accuracy'])} |")
    md.append(f"| Капитализация — точность | {pct(cur_agg['cap_accuracy'])} | {pct(mod_agg['cap_accuracy'])} |")
    md.append(f"| Полное совпадение с эталоном | {pct(cur_agg['exact_match'])} | {pct(mod_agg['exact_match'])} |")
    (HERE / "punct_bench_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # примеры
    smp = []
    smp.append(f"# Примеры ({min(args.samples, n)})\n")
    for i in range(min(args.samples, n)):
        r = rows[i]
        smp.append(f"### {i+1}")
        smp.append(f"- **вход**   : `{r['input']}`")
        smp.append(f"- **сейчас** : {r['current']}")
        smp.append(f"- **+модель** : {r['model']}")
        smp.append(f"- **эталон**  : {r['ref']}")
        smp.append("")
    (HERE / "punct_bench_samples.md").write_text("\n".join(smp) + "\n", encoding="utf-8")

    # ── консоль ──
    print("\n================  СВОДКА  ================")
    print(f"кейсов: {n}   current: {t_cur:.1f}s   model: {t_mod:.1f}s\n")
    print(f"{'метрика':32} {'сейчас':>10} {'+модель':>10}")
    print(f"{'запятые F1':32} {pct(cur_agg['comma_f1']):>10} {pct(mod_agg['comma_f1']):>10}")
    print(f"{'запятые precision':32} {pct(cur_agg['comma_precision']):>10} {pct(mod_agg['comma_precision']):>10}")
    print(f"{'запятые recall':32} {pct(cur_agg['comma_recall']):>10} {pct(mod_agg['comma_recall']):>10}")
    print(f"{'концевой знак тип верный':32} {pct(cur_agg['end_type_accuracy']):>10} {pct(mod_agg['end_type_accuracy']):>10}")
    print(f"{'капитализация':32} {pct(cur_agg['cap_accuracy']):>10} {pct(mod_agg['cap_accuracy']):>10}")
    print(f"{'полное совпадение':32} {pct(cur_agg['exact_match']):>10} {pct(mod_agg['exact_match']):>10}")
    print("\nартефакты:")
    for p in ["punct_bench_cases.csv", "punct_bench_summary.json",
              "punct_bench_summary.md", "punct_bench_samples.md"]:
        print(f"  tests/{p}")

    print("\n--------  10 ПРИМЕРОВ  --------")
    for i in range(min(args.samples, n)):
        r = rows[i]
        print(f"\n[{i+1}] вход   : {r['input']}")
        print(f"    сейчас : {r['current']}")
        print(f"    +модель: {r['model']}")
        print(f"    эталон : {r['ref']}")


if __name__ == "__main__":
    main()
