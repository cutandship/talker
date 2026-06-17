# -*- coding: utf-8 -*-
"""Large objective prompt benchmark on gemma-3-4b (GPU).

Reference corpus = clean RU sentences (gold punctuation/casing).
Input = ASR-degraded form. We score each prompt's output against the gold ref:

  PunctF1 : token-aligned punctuation restoration F1 (. , ! ? : ;)
  WERc    : word error rate of CONTENT (both sides normalized: lower, no punct,
            digits->words) vs ref — measures distortion/loss/insertion. Lower=better.
  fillKeep: fraction of injected fillers still present (lower=better cleaner)
  capOK   : output starts capitalized; termOK: ends with . ! ?

Composite (higher=better):
  score = 0.45*PunctF1 + 0.35*(1-WERc_clip) + 0.20*(1-fillKeep_on_filler_lines)
WERc_clip = min(WERc, 1.0). Filler term only averaged over lines that had fillers.

Usage:
  python promptbench_big.py stage1 <N>      # all prompts x N texts -> ranking
  python promptbench_big.py stage2 <N> P1 P2 P3   # named prompts x N (final)
Writes _big_report.txt / _big_raw.jsonl.
"""
from __future__ import annotations
import sys, time, re, json, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))   # import cleaner/local_llm
sys.stdout.reconfigure(encoding="utf-8")

import jiwer
from razdel import tokenize as rz_tokenize
from degrade import degrade, FILLERS, _digits_to_words
from cleaner import _strip_wrapping
import local_llm

M4B = str(Path(__file__).parent.parent / "models" / "gemma-3-4b-it-Q4_K_M.gguf")

# ── prompt candidates ─────────────────────────────────────────────────────────
PROMPTS = {
 "F2_current":
  "Обработай распознанную речь по правилам: 1) первая буква предложения "
  "заглавная; 2) расставь запятые и точки, при перечислении и счёте ставь "
  "запятые между словами; 3) удали слова-паразиты (ну, вот, короче, типа, "
  "значит, как бы, это самое); 4) числа и счёт оставь словами, форму слов "
  "не меняй и не переводи. Верни только текст:",
 "G1_concise":
  "Восстанови в распознанной речи знаки препинания и заглавные буквы, удали "
  "слова-паразиты (ну, вот, короче, типа, значит, как бы, это самое, в общем, "
  "походу, блин). Числа оставь словами, остальные слова и их форму не меняй. "
  "Верни только текст:",
 "G2_rules6":
  "Ты редактор расшифровки речи. Правила: 1) заглавная в начале предложений и "
  "в именах; 2) запятые, точки, тире где нужно; 3) при счёте и перечислении — "
  "запятые между словами; 4) удали паразиты: ну, вот, короче, типа, значит, "
  "как бы, это самое, в общем, походу, блин, слушай; 5) числа словами; "
  "6) не меняй, не переводи и не добавляй слова. Только итоговый текст:",
 "G3_preserve_first":
  "Расставь знаки препинания и заглавные буквы в распознанной речи. Сохрани "
  "все слова дословно в той же форме и порядке, числа оставь словами, ничего "
  "не добавляй. Затем удали только слова-паразиты: ну, вот, короче, типа, "
  "значит, как бы, это самое. Верни только текст:",
 "G4_punct_focus":
  "Восстанови пунктуацию (запятые, точки, вопросы, двоеточия) и заглавные "
  "буквы в распознанной речи; при перечислении ставь запятые. Убери слова-"
  "паразиты (ну, вот, короче, типа, значит, как бы, это самое). Слова и их "
  "форму сохрани, числа оставь словами. Только текст:",
 "G5_natural":
  "Преврати распознанную речь в грамотно оформленный текст: правильная "
  "пунктуация и заглавные буквы, без слов-паразитов (ну, вот, короче, типа, "
  "значит, как бы, это самое, в общем). Не искажай слова, не переводи термины, "
  "числа оставь словами. Верни только текст:",
 "G6_strict_fidelity":
  "Задача: пунктуация и регистр для распознанной речи. Поставь запятые, точки, "
  "вопросительные знаки и заглавные буквы; при счёте/перечислении ставь "
  "запятые. Удали паразиты (ну, вот, короче, типа, значит, как бы, это самое). "
  "ЗАПРЕЩЕНО: менять слова, их форму и порядок, переводить, превращать числа "
  "в цифры, добавлять что-либо. Верни только текст:",
 "G7_editor":
  "Отредактируй расшифровку устной речи: расставь знаки препинания и заглавные "
  "буквы, при перечислении — запятые, удали слова-паразиты (ну, вот, короче, "
  "типа, значит, как бы, это самое, походу, блин). Имена, термины и числа "
  "(словами) сохрани без изменений, форму слов не меняй. Только результат:",
 "H1_g7_midfiller":
  "Отредактируй расшифровку устной речи: расставь знаки препинания и заглавные "
  "буквы, при перечислении — запятые. Удали слова-паразиты (ну, вот, короче, "
  "типа, значит, как бы, это самое, походу, блин, слушай, понимаешь) — в том "
  "числе когда они стоят в середине или конце фразы и мешают смыслу. Имена, "
  "термины и числа (словами) сохрани без изменений, форму слов не меняй. "
  "Только результат:",
 "H2_g7_names":
  "Отредактируй расшифровку устной речи: 1) расставь запятые, точки, "
  "вопросительные знаки; 2) заглавные буквы в начале предложений, именах, "
  "названиях и аббревиатурах; 3) при перечислении — запятые; 4) удали слова-"
  "паразиты в любом месте фразы (ну, вот, короче, типа, значит, как бы, это "
  "самое, походу, блин, слушай). Имена, термины и числа словами сохрани, форму "
  "слов не меняй, ничего не добавляй. Только результат:",
 "H3_g7_strict":
  "Отредактируй расшифровку устной речи: расставь знаки препинания и заглавные "
  "буквы (включая имена и названия), при перечислении — запятые, удали слова-"
  "паразиты (ну, вот, короче, типа, значит, как бы, это самое, походу, блин, "
  "слушай, понимаешь) где бы они ни стояли. НЕ меняй форму слов, НЕ переставляй "
  "слова, НЕ заменяй синонимами, НЕ переводи, числа оставь словами. "
  "Только результат:",
 "H4_g7_plus_dup":
  "Отредактируй расшифровку устной речи: расставь знаки препинания и заглавные "
  "буквы (в том числе в именах и названиях), при перечислении — запятые, удали "
  "слова-паразиты (ну, вот, короче, типа, значит, как бы, это самое, походу, "
  "блин, слушай) в любом месте фразы, убери случайные повторы слов. Имена, "
  "термины и числа словами сохрани, форму и порядок слов не меняй. "
  "Только результат:",
}

# ── metrics ───────────────────────────────────────────────────────────────────
_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)
_PUNCT_SET = set(".,!?:;")

def _norm_content(s: str) -> str:
    """Lowercase word stream, no punctuation, digits->words — for WER of content.
    Digits are spelled out on BOTH ref and hyp so that the model keeping numbers
    as words (correct, per our prompt) is not penalised against a gold ref that
    stored them as digits."""
    s = _digits_to_words(s)           # 1969 -> "одна тысяча девятьсот..."
    s = s.lower().replace("ё", "е")
    return " ".join(_WORD.findall(s))

def werc(ref: str, hyp: str) -> float:
    r = _norm_content(ref); h = _norm_content(hyp)
    if not r:
        return 0.0
    try:
        return jiwer.wer(r, h)
    except Exception:
        return 1.0

def _word_punct_pairs(s: str):
    """Return list of (word_lower, trailing_punct_or_'') walking the string."""
    toks = list(rz_tokenize(s))
    pairs = []
    for i, t in enumerate(toks):
        txt = t.text
        if _WORD.fullmatch(txt.replace("ё", "е").lower()):
            # look ahead for punctuation tokens immediately following
            p = ""
            j = i + 1
            # razdel splits punct as separate tokens; peek next non-space token
            # We reconstruct by checking the raw char after this token end.
            pairs.append([txt.lower().replace("ё", "е"), p])
    return pairs

def punct_after(s: str):
    """Map each content word (by order) -> the punctuation char that follows it
    (first of . , ! ? : ; or '')."""
    out = []
    # walk chars; collect words and the punctuation right after each word
    cur = ""
    pend_word = None
    res = []
    i = 0
    s2 = s.replace("ё", "е")
    words = []
    for m in re.finditer(r"[а-яa-z0-9]+|[.,!?:;]", s2.lower()):
        tok = m.group(0)
        if tok in _PUNCT_SET:
            if words:
                # attach to last word if not already set
                if res and res[-1][1] == "":
                    res[-1][1] = tok
        else:
            words.append(tok)
            res.append([tok, ""])
    return res  # list of [word, punct]

def punct_f1(ref: str, hyp: str):
    """Align hyp words to ref words; compare punctuation marks. Returns (P,R,F1).
    Only positions where words match are counted (so WER noise doesn't corrupt
    punctuation scoring)."""
    R = punct_after(ref)
    H = punct_after(hyp)
    # align by simple word LCS on the word sequences
    rw = [w for w, _ in R]; hw = [w for w, _ in H]
    # DP LCS with backpointers
    n, m = len(rw), len(hw)
    if n == 0 or m == 0:
        return 0.0, 0.0, 0.0
    dp = [[0]*(m+1) for _ in range(n+1)]
    for i in range(n-1, -1, -1):
        for j in range(m-1, -1, -1):
            dp[i][j] = dp[i+1][j+1]+1 if rw[i] == hw[j] else max(dp[i+1][j], dp[i][j+1])
    # walk to collect matched index pairs
    i = j = 0; matched = []
    while i < n and j < m:
        if rw[i] == hw[j]:
            matched.append((i, j)); i += 1; j += 1
        elif dp[i+1][j] >= dp[i][j+1]:
            i += 1
        else:
            j += 1
    tp = fp = fn = 0
    for ri, hj in matched:
        rp = R[ri][1]; hp = H[hj][1]
        if rp and hp:
            if rp == hp: tp += 1
            else: fp += 1; fn += 1     # wrong mark = both a false pos and neg
        elif hp and not rp:
            fp += 1
        elif rp and not hp:
            fn += 1
    P = tp/(tp+fp) if (tp+fp) else 0.0
    Rr = tp/(tp+fn) if (tp+fn) else 0.0
    F = 2*P*Rr/(P+Rr) if (P+Rr) else 0.0
    return P, Rr, F

def _count_fillers(s: str) -> int:
    h = " " + _norm_content(s) + " "
    cnt = 0
    for f in FILLERS:
        fn = f.replace("ё", "е")
        cnt += len(re.findall(r"(?<![а-я])"+re.escape(fn)+r"(?![а-я])", h))
    return cnt

def filler_keep(hyp: str, n_filler: int, ref: str) -> float | None:
    """Fraction of INJECTED filler tokens still present in hyp. Subtracts the
    fillers naturally present in the gold ref (e.g. legit «значит»/«вот»), so a
    word the model rightly keeps because it belongs to the sentence isn't
    counted as a failure to clean."""
    if n_filler <= 0:
        return None
    natural = _count_fillers(ref)
    injected_left = max(0, _count_fillers(hyp) - natural)
    return min(1.0, injected_left / n_filler)

def score_one(ref, hyp, n_filler, asr):
    P, R, F = punct_f1(ref, hyp)
    w = werc(ref, hyp)
    fk = filler_keep(hyp, n_filler, ref)
    cap = bool(hyp[:1].isupper())
    term = hyp[-1:] in (".", "!", "?", "…")
    return dict(P=P, R=R, F=F, wer=w, fillKeep=fk, cap=cap, term=term)

def composite(rows):
    F = sum(r["F"] for r in rows)/len(rows)
    w = sum(min(1.0, r["wer"]) for r in rows)/len(rows)
    fk = [r["fillKeep"] for r in rows if r["fillKeep"] is not None]
    fkm = sum(fk)/len(fk) if fk else 0.0
    sc = 0.45*F + 0.35*(1-w) + 0.20*(1-fkm)
    return sc, F, w, fkm

# ── runner ────────────────────────────────────────────────────────────────────
def load_items(n):
    refs = (Path(__file__).parent / "clean.txt").read_text(encoding="utf-8").splitlines()
    items = [degrade(refs[i], i) for i in range(min(n, len(refs)))]
    return items

def run(prompts: dict, items: list, tag: str):
    local_llm.LocalLlm._instance = None
    llm = local_llm.LocalLlm.get(M4B)
    print("backend:", llm.backend, "| items:", len(items), "| prompts:", len(prompts))
    chat = lambda p, t: llm.chat(p, t, temperature=0.0)
    chat(next(iter(prompts.values())), "привет")  # warmup

    rawf = open(Path(__file__).parent / f"_big_raw_{tag}.jsonl", "w", encoding="utf-8")
    agg = {name: [] for name in prompts}
    t0 = time.time()
    for k, name in enumerate(prompts):
        p = prompts[name]; tp = time.time()
        for it in items:
            out = _strip_wrapping(chat(p, it["asr"]))
            sc = score_one(it["ref"], out, it["n_filler"], it["asr"])
            agg[name].append(sc)
            rawf.write(json.dumps({"prompt":name,"idx":it["idx"],"asr":it["asr"],
                "ref":it["ref"],"out":out,**{k:round(v,3) if isinstance(v,float) else v
                for k,v in sc.items() if k!='fillKeep'},
                "fillKeep": (round(sc["fillKeep"],3) if sc["fillKeep"] is not None else None),
                "n_filler":it["n_filler"]}, ensure_ascii=False)+"\n")
        print(f"  [{k+1}/{len(prompts)}] {name:20} {time.time()-tp:5.0f}s")
    rawf.close()
    print(f"total {time.time()-t0:.0f}s")

    lines = ["\n"+"="*96, f"OBJECTIVE PROMPT BENCH ({tag}, gemma-3-4b, {len(items)} texts)", "="*96]
    lines.append(f"{'prompt':20}{'SCORE':>7}{'PunctF1':>8}{'WERc':>7}{'fillKeep':>9}"
                 f"{'cap':>5}{'term':>6}")
    ranked = sorted(prompts, key=lambda n: -composite(agg[n])[0])
    for name in ranked:
        rows = agg[name]; sc,F,w,fk = composite(rows)
        cap = sum(r["cap"] for r in rows)/len(rows)
        term = sum(r["term"] for r in rows)/len(rows)
        lines.append(f"{name:20}{sc:7.3f}{F:8.3f}{w:7.3f}{fk:9.0%}{cap:5.0%}{term:6.0%}")
    rep = "\n".join(lines); print(rep)
    (Path(__file__).parent / f"_big_report_{tag}.txt").write_text(rep, encoding="utf-8")
    return ranked

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    if mode == "stage1":
        run(PROMPTS, load_items(n), f"s1_{n}")
    else:
        names = sys.argv[3:]
        sub = {k: PROMPTS[k] for k in names}
        run(sub, load_items(n), f"s2_{n}")

if __name__ == "__main__":
    main()
