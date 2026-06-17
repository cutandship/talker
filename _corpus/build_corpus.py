# -*- coding: utf-8 -*-
"""Build a clean RU reference corpus from Leipzig news+wiki sentences.
Filters junk, dedups, keeps a spread of lengths. Output: clean.txt (one
reference sentence per line, with correct punctuation/casing = gold standard).
"""
from __future__ import annotations
import re, sys, random
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent
SOURCES = [
    HERE / "rus_news_2020_100K" / "rus_news_2020_100K-sentences.txt",
    HERE / "rus_wikipedia_2021_100K" / "rus_wikipedia_2021_100K-sentences.txt",
]
N_TARGET = 12000   # collect a bit extra; degraded set trims to 10k usable
random.seed(1234)

CYR = re.compile(r"[а-яё]", re.I)

def clean_line(s: str) -> str | None:
    # strip leading "id\t"
    if "\t" in s:
        s = s.split("\t", 1)[1]
    s = s.strip()
    # reject if too short/long or junk-heavy
    if not (20 <= len(s) <= 220):
        return None
    # must be mostly Cyrillic prose
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return None
    cyr = sum(1 for c in letters if CYR.match(c))
    if cyr / len(letters) < 0.85:        # mostly Russian
        return None
    # must end with sentence punctuation
    if s[-1] not in ".!?":
        return None
    # reject lines dominated by digits/symbols/quotes-tables
    digits = sum(1 for c in s if c.isdigit())
    if digits / len(s) > 0.15:
        return None
    if s.count("«") + s.count("»") + s.count('"') > 4:
        return None
    if any(b in s for b in ("|", "—", "•", "№", "://", "@")):
        return None
    # collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # need at least 4 words (so punctuation restoration is non-trivial)
    if len(s.split()) < 4:
        return None
    return s

def main():
    seen = set()
    out = []
    for src in SOURCES:
        if not src.exists():
            print("MISSING:", src); continue
        n_src = 0
        for raw in src.read_text(encoding="utf-8", errors="replace").splitlines():
            c = clean_line(raw)
            if not c:
                continue
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            n_src += 1
        print(f"{src.parent.name}: {n_src} kept")
    random.shuffle(out)
    out = out[:N_TARGET]
    # length spread report
    lens = [len(x.split()) for x in out]
    lens.sort()
    print(f"\nTOTAL kept: {len(out)}")
    print(f"words/sent: min={lens[0]} p25={lens[len(lens)//4]} "
          f"median={lens[len(lens)//2]} p75={lens[3*len(lens)//4]} max={lens[-1]}")
    (HERE / "clean.txt").write_text("\n".join(out), encoding="utf-8")
    print("wrote clean.txt")

if __name__ == "__main__":
    main()
