# -*- coding: utf-8 -*-
"""Чистые звенья текстового пайплайна: числа (itn), паразиты (filler),
замены (replacements), faithguard."""
import faithguard
import filler
import itn
from config import ReplacementConfig
from replacements import apply_replacements, compile_rules, default_replacements


# ── ITN: числа прописью → цифры ──────────────────────────────────────────────

def test_itn_percent():
    assert itn.normalize("двадцать пять процентов", "ru") == "25 %"


def test_itn_plain_text_passthrough():
    text = "в две тысячи двадцать шестом году"
    assert itn.normalize(text, "ru") == text


# ── Слова-паразиты: режет только из фиксированного списка ────────────────────

def test_filler_strips_known():
    assert filler.strip_fillers("ну я короче пошёл э-э домой") == "Я пошёл домой"


def test_filler_keeps_ambiguous():
    # «вот»/«значит» — спорные, их трогать нельзя.
    assert filler.strip_fillers("вот значит так и было") == "вот значит так и было"


# ── Замены: пользовательские + встроенный IT-словарь ─────────────────────────

def _rules(*cfgs):
    return compile_rules(list(cfgs))


def test_replacement_basic():
    rules = _rules(ReplacementConfig(to="Claude", from_=["клод", "клауд"]))
    assert apply_replacements("я спросил клод о погоде", rules) == \
        "я спросил Claude о погоде"


def test_builtin_dictionary():
    rules = compile_rules([
        ReplacementConfig(
            to=d.get("to", ""), from_=list(d.get("from_", [])),
            whole_word=d.get("whole_word", True),
            phonetic=d.get("phonetic", False), sounds=d.get("sounds", ""))
        for d in default_replacements()
    ])
    assert "Microsoft" in apply_replacements("открой майкрософт ворд", rules)


# ── Faithguard: LLM-чистка не должна выдумывать и терять слова ───────────────

def test_faithguard_identical_ok():
    ok, violations = faithguard.verify_faithful("привет как дела",
                                                "привет как дела")
    assert ok and not violations


def test_faithguard_catches_invention():
    ok, violations = faithguard.verify_faithful(
        "привет как дела", "привет как дела дорогой")
    assert not ok
    assert any("дорогой" in v for v in violations)


def test_faithguard_allows_stutter_removal():
    ok, _ = faithguard.verify_faithful("привет привет как дела",
                                       "привет как дела")
    assert ok
