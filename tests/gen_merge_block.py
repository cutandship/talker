# -*- coding: utf-8 -*-
"""Сгенерировать БЕЗОПАСНЫЙ блок добавлений в default_replacements() из сырья
зонда (gigaam_probe.json). Только консервативные кандидаты:
  кириллица · длина ≥4 · не омоним (блок-лист) · не союз · не дубль существующего.
Латиничный мусор (spisks=SpaceX) и латиница-канон (microsoft) НЕ берём.
Пишет блок в tests/_v3_block.txt + статистику.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

_PUNCT = re.compile(r"[.,!?;:«»\"'()\-–—…]")
_CYR_ONLY = re.compile(r"^[а-яё ]+$")
_CONJ = {"и","а","но","в","о","у","к","с","я","от","до","по","за","из","на",
         "не","то","же","ли","бы","об","со","во"}
BLOCKLIST = {
    "игрок","оператор","гид","дали","раной","рана","курсор","редис","наушник",
    "сора","сор","дак","мак","метла","чат","бот","код","сайт","файл","пост",
    "лайк","чек","хост","кэш","бан","спам","лог","тег","клик","дай","облако",
    "поток","ядро","сеть","окно","папка","ссылка","запрос","ответ","почта",
    "диск","память","образ","среда","ветка","сборка","поиск","доступ","грок",
    # пойманы ручным аудитом (мелкий корпус прозевал — частые слова):
    "слайд","кран","мета","край","слой","грань","рана","дале",
}
CARRIERS_PREFIX = ["я установил", "открой"]


def _norm(s): return re.sub(r"\s+"," ",_PUNCT.sub(" ",s or "")).strip().lower()
def _strip(prefix, fn):
    pw, w = prefix.split(), fn.split(); i=0
    for p in pw:
        if i<len(w) and w[i]==p: i+=1
        else: break
    return " ".join(w[i:]).strip()


def good(v):
    return bool(_CYR_ONLY.match(v)) and len(v.replace(" ","")) >= 4 \
        and v not in _CONJ and v not in BLOCKLIST


raw = json.loads((HERE/"gigaam_probe.json").read_text(encoding="utf-8"))
from replacements import default_replacements
existing = {}
for d in default_replacements():
    existing.setdefault(d["to"], set()).update(x.lower() for x in d.get("from_",[]))

new_terms, extra_for_existing, lines = [], [], []
for term, outs in raw.items():
    cands = set()
    for key, full in outs.items():
        if not isinstance(full,str) or full.startswith("<ERR"): continue
        prefix = key.split("|",1)[1] if "|" in key else ""
        v = _strip(prefix, _norm(full))
        if v and good(v): cands.add(v)
    # убрать уже известные
    have = existing.get(term, set())
    fresh = sorted(c for c in cands if c.lower() not in have)
    if not fresh: continue
    arr = ", ".join(f'"{c}"' for c in fresh)
    lines.append(f'        {{"to": "{term}", "from_": [{arr}]}},')
    (new_terms if term not in existing else extra_for_existing).append(term)

block = ["",
    "        # ── v3-зонд (tests/probe_gigaam.py): формы под GigaAM v3-e2e-rnnt. ──",
    "        # Сняты TTS→GigaAM v3 (v2-формы выше слышались иначе). Омонимы",
    "        # (Grok→игрок, Git→гид, ChatGPT→чат…) ИСКЛЮЧЕНЫ. Дубль `to` ок —",
    "        # apply_replacements прогоняет все правила.",
] + lines
(HERE/"_v3_block.txt").write_text("\n".join(block)+"\n", encoding="utf-8")

print(f"новых терминов: {len(new_terms)} | доп.вариантов к существующим: {len(extra_for_existing)}")
print(f"строк-правил в блоке: {len(lines)}")
print("новые:", ", ".join(new_terms))
print("доп к существующим:", ", ".join(extra_for_existing))
