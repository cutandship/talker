# -*- coding: utf-8 -*-
"""Переклассификация мисхиров зонда БЕЗ повторного прогона (работает на
сохранённом tests/gigaam_probe.json). Чинит две беды corpus-фильтра:

  1. Латиница: v3 иногда сам выдаёт канон (microsoft), иногда латиничный бред
     (spisks=SpaceX). Раньше обе → «уже канон». Теперь: латиница==канон → already;
     латиница≠канон → mishear (в safe, его тоже надо мапить).
  2. Омонимы: вместо «есть ли слово в корпусе» (ловит редкие имена типа «клод»
     как коллизию, и прозёвывает «чат», которого в корпусе нет) — порог по ЧАСТОТЕ
     в корпусе (count≥COMMON_MIN) + блок-лист частых разговорных/IT-слов.

Если установлен wordfreq — используем его (точнее); иначе корпус+блок-лист.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

COMMON_MIN = 12          # count в корпусе ≥ этого = частое слово = омоним
COLLISION_ZIPF = 3.0     # если есть wordfreq
_MIN_SAFE_LEN = 3
_CONJ = {"и", "а", "но", "в", "о", "у", "к", "с", "я", "от", "до", "по", "за",
         "из", "на", "не", "то", "же", "ли", "бы", "об", "со", "во"}
# Частые русские слова, на которые мапить НЕЛЬЗЯ (сломают речь). Корпус 12k мал
# и частоты ненадёжны, поэтому ведём явный список — настоящие омонимы из выхода
# зонда + общеупотребимые. wordfreq, когда встанет, заменит это автоматикой.
_BLOCKLIST = {
    # реальные коллизии, выявленные зондом (мисхир == обычное слово):
    "игрок", "оператор", "гид", "дали", "раной", "рана", "курсор", "редис",
    "наушник", "грок", "сора", "сор", "дак", "мак", "метла",
    # IT-разговорное / общее:
    "чат", "бот", "код", "сайт", "файл", "пост", "лайк", "чек", "хост", "кэш",
    "бан", "спам", "лог", "тег", "клик", "дай", "облако", "поток", "ядро",
    "сеть", "окно", "папка", "ссылка", "запрос", "ответ", "почта", "диск",
    "память", "образ", "среда", "ветка", "сборка", "поиск", "доступ",
}

_PUNCT = re.compile(r"[.,!?;:«»\"'()\-–—…]")
_HAS_CYR = re.compile(r"[а-яё]")
_HAS_LAT = re.compile(r"[a-z]")

CARRIERS_PREFIX = ["я установил", "открой"]


def _norm(s: str) -> str:
    s = _PUNCT.sub(" ", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _strip(prefix: str, full_norm: str) -> str:
    pw, words = prefix.split(), full_norm.split()
    i = 0
    for p in pw:
        if i < len(words) and words[i] == p:
            i += 1
        else:
            break
    return " ".join(words[i:]).strip()


# ── частотный чекер ──
def load_freq():
    try:
        from wordfreq import zipf_frequency
        return "wordfreq", (lambda w: zipf_frequency(w, "ru") >= COLLISION_ZIPF), \
               (lambda w: round(zipf_frequency(w, "ru"), 2))
    except Exception:
        txt = (ROOT / "_corpus" / "clean.txt").read_text(encoding="utf-8").lower()
        cnt = collections.Counter(re.findall(r"[а-яё]{2,}", txt))
        return "corpus+blocklist", \
            (lambda w: cnt.get(w, 0) >= COMMON_MIN or w in _BLOCKLIST), \
            (lambda w: cnt.get(w, 0))


def classify(term, variant, is_common, score):
    t = term.lower().strip()
    v = variant.strip()
    if not v:
        return "empty", None
    same = v.replace(" ", "") == t.replace(" ", "")
    if same:
        return "already", None
    if _HAS_LAT.search(v) and not _HAS_CYR.search(v):
        return "latin_mishear", None      # латиница ≠ канон → мисхир (мапим)
    if len(v) < _MIN_SAFE_LEN or v in _CONJ:
        return "unrecover", None
    if is_common(v):
        return "collision", score(v)
    return "safe", None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raw = json.loads((HERE / "gigaam_probe.json").read_text(encoding="utf-8"))
    kind, is_common, score = load_freq()
    print(f"переклассификация · фильтр: {kind} · терминов: {len(raw)}")

    buckets = {}   # term → {bucket: [variants]}
    for term, outs in raw.items():
        seen = {}
        for key, full in outs.items():
            if not isinstance(full, str) or full.startswith("<ERR"):
                continue
            prefix = key.split("|", 1)[1] if "|" in key else ""
            var = _strip(prefix, _norm(full))
            if not var:
                continue
            b, sc = classify(term, var, is_common, score)
            # приоритет класса: safe>latin_mishear>collision>unrecover>already>empty
            rank = {"safe": 5, "latin_mishear": 4, "collision": 3,
                    "unrecover": 2, "already": 1, "empty": 0}
            if var not in seen or rank[b] > rank[seen[var][0]]:
                seen[var] = (b, sc)
        bk = {}
        for var, (b, sc) in seen.items():
            bk.setdefault(b, []).append(var if sc is None else f"{var}({sc})")
        buckets[term] = bk

    # ── слить safe + latin_mishear в словарь ──
    from replacements import default_replacements
    existing = {d["to"]: list(d.get("from_", [])) for d in default_replacements()}
    merged = dict(existing)
    new_terms = grown = 0
    for term, bk in buckets.items():
        add = bk.get("safe", []) + bk.get("latin_mishear", [])
        if not add:
            continue
        cur = merged.get(term, [])
        before = len(cur)
        low = {c.lower() for c in cur}
        for v in add:
            if v.lower() not in low:
                cur.append(v); low.add(v.lower())
        merged[term] = cur
        if term not in existing:
            new_terms += 1
        elif len(cur) > before:
            grown += 1
    rules = [{"to": t, "from_": f} for t, f in merged.items() if f]
    (HERE / "dict_v3_rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── отчёты ──
    def w(name, lines):
        (HERE / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    n_coll = sum(len(bk.get("collision", [])) for bk in buckets.values())
    n_safe = sum(len(bk.get("safe", [])) + len(bk.get("latin_mishear", []))
                 for bk in buckets.values())
    rep = [f"# Пересъём словаря на GigaAM v3 (переклассифицировано)\n",
           f"Фильтр омонимов: **{kind}** · новых терминов: **{new_terms}** · "
           f"дополнено: **{grown}** · safe-вариантов: {n_safe} · омонимов: {n_coll}\n",
           "| Канон | safe (мапим) | латиница-мисхир | омонимы (искл.) | уже канон |",
           "|---|---|---|---|---|"]
    for term, bk in buckets.items():
        rep.append(f"| {term} | {', '.join(bk.get('safe', [])) or '—'} "
                   f"| {', '.join(bk.get('latin_mishear', [])) or '—'} "
                   f"| {', '.join(bk.get('collision', [])) or '—'} "
                   f"| {', '.join(bk.get('already', [])) or '—'} |")
    w("dict_v3_report.md", rep)

    col = ["# Омонимы — НЕ добавлять (сломают обычную речь)\n",
           f"Фильтр: {kind}. Порог: count≥{COMMON_MIN} или блок-лист.\n",
           "| Канон | вариант(score) |", "|---|---|"]
    for term, bk in buckets.items():
        if bk.get("collision"):
            col.append(f"| {term} | {', '.join(bk['collision'])} |")
    w("dict_v3_collisions.md", col)

    print(f"новых: {new_terms}, дополнено: {grown}, safe: {n_safe}, омонимов: {n_coll}")
    print("примеры коллизий:", end=" ")
    shown = 0
    for term, bk in buckets.items():
        if bk.get("collision") and shown < 12:
            print(f"{term}={bk['collision']}", end="  "); shown += 1
    print("\nартефакты: tests/dict_v3_rules.json, dict_v3_report.md, dict_v3_collisions.md")


if __name__ == "__main__":
    main()
