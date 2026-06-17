<!-- LANG: English -->
**English** · [Русский](BENCHMARKS.ru.md)

# Talker — Engineering Benchmarks

Talker is a **local, privacy-first Russian voice-dictation tool for Windows**. It runs
speech-to-text entirely on-device (no cloud), so recognition quality is engineered, not
outsourced. This document records the *data-driven* decisions behind that quality. Every
number below is reproducible — the scripts live in [`tests/`](tests/).

**STT engine:** `gigaam-v3-e2e-rnnt` (Sber GigaAM, Russian-only, runs locally via
`onnxruntime`, **punctuates and capitalizes natively**). All benchmarks run on CPU.

---

## TL;DR

| Question | Finding | Decision |
|---|---|---|
| Do we need a punctuation model? | GigaAM's **native** punctuation = **81.7%** comma F1; adding RUPunct on top **drops it to 68.1%** | **No.** Native is better. |
| Is the brand/term dictionary healthy? | It was tuned on GigaAM **v2**; production is **v3**, which mishears differently → **5/16** hit | **Re-snapshotted** on v3 → 93 rules / 158 variants |
| Can a dictionary fix every acronym? | Most resolve fine; some (**ИИ→"и"**, **IT→"эти"**) are destroyed by the ASR | **No.** Hard ceiling — documented |

The throughline: we **measure before we integrate**. Twice, the measurement overturned the
"obvious" move.

---

## 1. Punctuation: do we actually need a punctuation model?

### Context
Punctuation is the most-cited weak spot of dictation tools built on Whisper (it chunks audio
in 30 s windows and drops punctuation across boundaries). The usual fix is a second-pass
language model — which is exactly what risks **rewriting the user's words**. So: does Talker
need a punctuation/cleanup model at all?

### Method
Two experiments, scored against a gold Russian corpus (Leipzig news+wiki, 12k sentences),
word-aligned with `difflib` so ASR word-errors don't pollute the punctuation metric.

1. **Restoration** ([`run_punct_bench.py`](tests/run_punct_bench.py)): strip punctuation from
   gold → restore. Compares the current heuristic fallback vs **RUPunct_small** (a BERT
   token-classification model, MIT). *10 000 sentences.*
2. **Production reality** ([`punct_gigaam_bench.py`](tests/punct_gigaam_bench.py)): gold →
   local TTS (Windows SAPI, ru-RU) → **real GigaAM v3 transcription** → score its **native**
   punctuation vs **re-punctuating the same words with RUPunct**. *250 sentences.*

### Results

**Restoration vs the bare heuristic** (10k) — RUPunct looks great here:

| Metric | Heuristic (current RU fallback) | RUPunct_small |
|---|---|---|
| **Comma F1** | 0.0% | **76.2%** |
| Capitalization | 92.0% | 96.8% |
| Exact match | 16.2% | 37.5% |

**But against the real production engine** (250) — the picture flips:

| Metric | **GigaAM-native** | GigaAM → RUPunct |
|---|---|---|
| **Comma F1** | **81.7%** | 68.1% |
| Comma precision | 80.9% | 74.5% |
| Comma recall | **82.5%** | 62.6% |
| Terminal mark type | 98.0% | 97.6% |
| Capitalization | 97.9% | 97.5% |
| Exact match | 29.6% | 24.4% |

### Takeaway
The first benchmark is **misleading on its own**: RUPunct beats a heuristic that places *no*
internal commas — but production is not the heuristic, it's GigaAM, whose native punctuation
is strong (81.7% comma F1). Re-punctuating GigaAM's output with RUPunct **loses recall and
over-segments** ("…государства**.** право…"). So **we do not ship a punctuation model** on the
GigaAM path — it would cost quality *and* a ~2 GB `torch`/ONNX dependency. RUPunct stays
available only for the optional Whisper engine, whose native punctuation is weaker.

> **Punctuation is a competitive advantage here, not a gap** — it comes from the on-device
> model, with no cloud LLM "polishing" that could alter your words.

### Reproduce
```bash
python tests/punct_gigaam_bench.py --n 250   # native vs RUPunct on real GigaAM output
python tests/run_punct_bench.py --n 10000    # restoration: heuristic vs RUPunct
```

---

## 2. Keeping a term dictionary in sync with the ASR model

### Context
GigaAM is Russian-only and emits Cyrillic, so it can't produce "Claude" or "Microsoft" from
speech — it returns `клод`, `майкрософт`. Talker fixes this **deterministically after STT**
([`replacements.py`](replacements.py)): a curated map of Cyrillic mishears → canonical spelling.
Because it's a post-STT find-replace (not a generative model), it **cannot invent or reorder
words** — the same fidelity guarantee as the rest of the pipeline.

The catch: those mishears are **model-specific**. The dictionary was collected on GigaAM
**v2**, but production moved to **v3**.

### Method
An empirical probe ([`probe_gigaam.py`](tests/probe_gigaam.py)): each canonical term
([`dict_terms.py`](tests/dict_terms.py), 178 terms) → `edge-tts` (2 voices × 2 carrier
phrases) → 16 kHz mono → **GigaAM v3** → the real Cyrillic mishear. Each candidate is then
classified:
- **safe** → add to the dictionary;
- **homonym** (a frequent Russian word — frequency-filtered) → **excluded** (would break normal
  speech: `Grok→игрок`, `Git→гид`, `ChatGPT→чат`);
- **unrecoverable** (collapsed/truncated to 1–2 letters) → separate bucket;
- **already canonical** (v3 emitted Latin itself) → no rule needed.

### Results
On a 16-term sanity sample, the **v2 dictionary caught only 5/16** — not because of coverage
gaps, but because **v3 hears differently**:

| Term | v2 dictionary expects | What **v3** actually emits |
|---|---|---|
| Gemini | джемини / гемини | **джимней** |
| Docker | докер | **дакр** |
| Copilot | капайлот | **капайд** |
| GitHub | гитхаб | **гиток** |
| Kubernetes | кубернетис | **губернитис** |
| OpenAI | опен эй ай | **опеной** |

After re-snapshotting on v3 and merging the safe forms, the production dictionary grew to
**93 rules / 158 variants**, with homonyms excluded by a frequency filter plus manual audit.

### Takeaway
**A replacement dictionary silently rots when the ASR model is upgraded.** Treating it as
"set once" is a trap; it needs re-probing per model version. The probe makes that a
repeatable 15-minute job instead of guesswork.

> **Caveat (honesty):** TTS pronunciation ≠ a human voice, so the exact Cyrillic forms are
> *candidates* pending a live-voice check, not ground truth.

### Reproduce
```bash
python tests/probe_gigaam.py --full   # 178 terms → real v3 mishears + classification
```

---

## 3. The acronym ceiling

### Context
Spelled-out acronyms (ИИ, API, URL, SQL) are a known STT pain. We probed how GigaAM v3 renders
them across pronunciations ([`probe_acronyms.py`](tests/probe_acronyms.py)).

### Results
- **Most are fine.** Spelled letter-by-letter, v3 emits the correct Latin canon itself:
  `HTML`, `CSS`, `USB`, `CPU`, `GPU`, `PDF`, `VPN`. No rule needed.
- **A few map cleanly** (added to the dictionary): `урл→URL`, `эскуэль→SQL`, `сипью→CPU`,
  `пдеф→PDF`.
- **Two are unrecoverable** — the ASR destroys the information:
  - **ИИ → "и"** (collapses to the conjunction "and", in every pronunciation);
  - **IT → "эти"** (collides with the common word "these").

### Takeaway
**A post-STT dictionary cannot restore information the ASR has already thrown away.** You can't
map `и → ИИ` or `эти → IT` without breaking ordinary Russian. The honest fixes are upstream
(STT-level biasing — which GigaAM doesn't support, Whisper does) or behavioural (say
"искусственный интеллект", which transcribes perfectly). Knowing *where* the ceiling is
prevents wasted dictionary work.

### Reproduce
```bash
python tests/probe_acronyms.py
```

---

## Caveats

- **TTS is a proxy for human speech.** SAPI/edge-tts pronounce punctuation with clean prosodic
  pauses and read foreign words their own way, so absolute scores are optimistic vs real users.
  The *comparisons* (native vs RUPunct; v2 vs v3) hold because both sides see identical inputs,
  but exact dictionary forms still need a **live-voice pass**.
- **One corpus** (Leipzig RU news+wiki) — formal prose, not chat/code dictation.
- Single language pair (RU→canonical EN brands).

## Reproducing everything
```bash
# Python 3.11, CPU. Core deps: onnx-asr onnxruntime (GigaAM), edge-tts, pydub (+ ffmpeg).
# Optional: transformers torch (RUPunct), wordfreq (homonym filter).
python tests/punct_gigaam_bench.py --n 250
python tests/run_punct_bench.py --n 10000
python tests/probe_gigaam.py --full
python tests/probe_acronyms.py
```
Artifacts (`*_summary.md`, `dict_v3_report.md`, …) are written next to each script in `tests/`.
