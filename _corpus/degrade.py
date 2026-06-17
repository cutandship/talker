# -*- coding: utf-8 -*-
"""Degrade clean reference sentences into an ASR-like form, deterministically.
Full distortion set (user choice):
  - lowercase, strip punctuation
  - digits -> Russian number words (num2words)   [GigaAM/CTC выдаёт словами]
  - insert filler words into ~30% of sentences (we record which → ground truth)
  - random mishears/typos (й→и, тся→ться swaps, doubled words)  [light]

Each produces, per reference R:
  asr      : degraded input fed to the model
  ref      : the gold clean sentence (for punct/WER scoring)
  n_filler : how many filler tokens were injected (for filler-removal metric)
Deterministic via per-line seed so reruns are identical (resume-safe).
"""
from __future__ import annotations
import re, random
from num2words import num2words

FILLERS = ["ну", "вот", "короче", "типа", "значит", "как бы", "это самое",
           "в общем", "походу", "блин", "слушай", "понимаешь"]

_PUNCT = re.compile(r"[.,!?;:«»\"'()\-–—…]")

def _digits_to_words(s: str) -> str:
    def repl(m):
        num = m.group(0)
        try:
            # keep it simple: integer part only; ASR rarely emits decimals as digits
            val = int(num)
            return num2words(val, lang="ru")
        except Exception:
            return num
    # split decimals like 429,2 -> "429" "2" handled crudely: drop comma-decimals
    s = re.sub(r"(\d+),(\d+)", lambda m: m.group(1), s)  # 429,2 -> 429
    return re.sub(r"\d+", repl, s)

def _mishears(words: list[str], rng: random.Random) -> list[str]:
    """Light random ASR-style corruptions on a few tokens."""
    out = []
    for w in words:
        r = rng.random()
        if len(w) > 4 and r < 0.04:
            # swap й→и (common mishear)
            w = w.replace("й", "и", 1) if "й" in w else w
        if len(w) > 5 and r >= 0.04 and r < 0.06:
            # double the word (stutter)
            out.append(w)
        out.append(w)
    return out

def degrade(ref: str, idx: int) -> dict:
    rng = random.Random(1000 + idx)
    s = ref
    # digits -> words (do before lowercasing/stripping; keeps number-word form)
    s = _digits_to_words(s)
    # strip punctuation
    s = _PUNCT.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    words = s.split()
    # light mishears (every line, but low rate)
    words = _mishears(words, rng)
    # inject fillers into ~30% of lines
    n_filler = 0
    if rng.random() < 0.30 and len(words) >= 3:
        k = rng.choice([1, 1, 2, 2, 3])         # 1–3 fillers
        for _ in range(k):
            pos = rng.randint(0, len(words))
            f = rng.choice(FILLERS)
            # "это самое" / "как бы" / "в общем" are 2 tokens
            ftoks = f.split()
            words[pos:pos] = ftoks
            n_filler += len(ftoks)
    asr = " ".join(words)
    return {"idx": idx, "ref": ref, "asr": asr, "n_filler": n_filler}

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    from pathlib import Path
    refs = (Path(__file__).parent / "clean.txt").read_text(encoding="utf-8").splitlines()
    print("=== 6 примеров деградации ===")
    for i in range(6):
        d = degrade(refs[i], i)
        print(f"\nREF [{i}]: {d['ref']}")
        print(f"ASR    : {d['asr']}")
        print(f"fillers: {d['n_filler']}")
