"""Детерминированная коррекция терминов после STT (концепт 22).

Применяется в _pre_clean_pipeline ПОСЛЕ сниппетов и ДО backtrack/LLM, чтобы
канонические написания попали и в очистку, и в историю. В отличие от
initial_prompt (vocabulary.py) срабатывает всегда и не ест токены.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def compile_rules(rules) -> list[tuple[re.Pattern, str]]:
    """rules: list[ReplacementConfig]. Компилируем один раз при load/save."""
    compiled: list[tuple[re.Pattern, str]] = []
    for r in rules:
        variants = sorted((v for v in (r.from_ or []) if v), key=len, reverse=True)
        if not r.to or not variants:
            continue
        alt = "|".join(re.escape(v) for v in variants)
        # \b ненадёжен на стыке кириллица/латиница — lookaround по \w.
        body = rf"(?<!\w)(?:{alt})(?!\w)" if r.whole_word else rf"(?:{alt})"
        compiled.append((re.compile(body, re.IGNORECASE), r.to))
    return compiled


def apply_replacements(text: str, compiled) -> str:
    if not text or not compiled:
        return text
    n = 0
    for pat, repl in compiled:
        text, k = pat.subn(lambda m, repl=repl: _preserve_case(m.group(0), repl), text)
        n += k
    if n:
        logger.info(f"Replacements: {n} substitution(s)")
    return text


def _preserve_case(src: str, repl: str) -> str:
    # «КЛОД» (крик) → upper канона; иначе канон как задан.
    if src.isupper() and not repl.isupper():
        return repl.upper()
    return repl


# ── 26. Фонетический матч (опционально, per-rule) ────────────────────────────────
# Консервативно: терминальное оглушение + мягкий/твёрдый знак + ё/й + схлопывание
# двойных, и ТОЧНОЕ равенство фонокодов (без edit-distance — оно ловит ложные пары
# вроде «гид»/«гит»; fuzzy — путь v2 после бенчмарка на talker.log).
_DEVOICE = {"д": "т", "б": "п", "в": "ф", "г": "к", "з": "с", "ж": "ш"}
_PHON_MIN_LEN = 4
_CYR_WORD = re.compile(r"[А-Яа-яЁё]+")


def phonkey(w: str) -> str:
    w = w.lower().replace("ь", "").replace("ъ", "").replace("ё", "е").replace("й", "и")
    if w and w[-1] in _DEVOICE:               # терминальное оглушение: клод→клот
        w = w[:-1] + _DEVOICE[w[-1]]
    w = re.sub(r"(.)\1+", r"\1", w)           # двойные согласные → одинарные
    return w


def compile_phonetic(rules, min_len: int = _PHON_MIN_LEN) -> dict:
    """rules: list[ReplacementConfig]. → {phonkey: to} только для phonetic-правил."""
    out: dict[str, str] = {}
    for r in rules:
        if not getattr(r, "phonetic", False):
            continue
        src = (getattr(r, "sounds", "") or (r.from_[0] if r.from_ else "")).strip()
        if src and len(src) >= min_len and r.to:
            out.setdefault(phonkey(src), r.to)
    return out


def apply_phonetic(text: str, phon: dict, min_len: int = _PHON_MIN_LEN) -> str:
    if not text or not phon:
        return text

    def repl(m: "re.Match") -> str:
        w = m.group(0)
        if len(w) < min_len:
            return w
        to = phon.get(phonkey(w))
        return _preserve_case(w, to) if to else w

    return _CYR_WORD.sub(repl, text)


# ── 27. Стартовый набор IT-терминов (код-switching RU→EN) ────────────────────────
def default_replacements() -> list[dict]:
    """Плоские dict-ы (как default_commands) — config.py строит ReplacementConfig.
    Многословные/специфичные правила идут РАНЬШЕ общих ("гитхаб" до "гит")."""
    return [
        {"to": "GitHub",       "from_": ["гитхаб", "гит хаб"]},
        {"to": "git",          "from_": ["гит"]},
        {"to": "commit",       "from_": ["коммит"]},
        {"to": "merge",        "from_": ["мёрж", "мерж"]},
        {"to": "pull request", "from_": ["пулреквест", "пул реквест"]},
        {"to": "deploy",       "from_": ["деплой"]},
        {"to": "Docker",       "from_": ["докер"]},
        {"to": "Kubernetes",   "from_": ["кубернетес", "кубернетис"]},
        {"to": "React",        "from_": ["реакт"]},
        {"to": "API",          "from_": ["апи"]},
        {"to": "JSON",         "from_": ["джейсон"]},
        {"to": "frontend",     "from_": ["фронтенд", "фронт энд"]},
        {"to": "backend",      "from_": ["бэкенд", "бэк энд"]},
        # ── Бренды, продукты, общеизвестные названия (RU-произношение → канон).
        # GigaAM — русскоязычная и латиницу не выдаёт, поэтому это ЕДИНСТВЕННЫЙ
        # способ получить «Microsoft» вместо «майкрософт». Многословные/длинные —
        # раньше коротких, whole_word по умолчанию (не заденет «гуглить» и т.п.).
        {"to": "ChatGPT",      "from_": ["чат джипити", "чат гпт", "чатгпт", "чатджипити"]},
        {"to": "OpenAI",       "from_": ["опен эй ай", "опенэйай", "опен ай"]},
        {"to": "JavaScript",   "from_": ["джава скрипт", "джаваскрипт", "ява скрипт"]},
        {"to": "TypeScript",   "from_": ["тайп скрипт", "тайпскрипт"]},
        {"to": "Microsoft",    "from_": ["майкрософт"]},
        {"to": "Google",       "from_": ["гугл", "гугле"]},
        {"to": "Gemini",       "from_": ["джемини", "гемини"]},
        {"to": "Gemma",        "from_": ["гемма", "джемма"]},
        {"to": "Claude",       "from_": ["клод"]},
        {"to": "Anthropic",    "from_": ["антропик", "энтропик"]},
        {"to": "Apple",        "from_": ["эпл", "эппл"]},
        {"to": "Windows",      "from_": ["виндоус", "виндовс", "винда", "винды"]},
        {"to": "Linux",        "from_": ["линукс"]},
        {"to": "Ubuntu",       "from_": ["убунту"]},
        {"to": "Python",       "from_": ["питон", "пайтон"]},
        {"to": "YouTube",      "from_": ["ютуб", "ютьюб"]},
        {"to": "Telegram",     "from_": ["телеграм", "телеграмм"]},
        {"to": "WhatsApp",     "from_": ["вотсап", "ватсап", "вацап"]},
        {"to": "NVIDIA",       "from_": ["энвидиа", "нвидиа", "энвидия"]},
        {"to": "iPhone",       "from_": ["айфон"]},
        {"to": "Android",      "from_": ["андроид"]},
        {"to": "Excel",        "from_": ["эксель"]},
        {"to": "PowerPoint",   "from_": ["пауэр поинт", "поверпоинт", "пауэрпоинт"]},
        # ── AI-ассистенты, модели и инструменты. Кириллические формы СНЯТЫ
        # эмпирически с GigaAM v2 (см. _gigaam_probe): часть звучит не как
        # «правильная» транслитерация — Copilot→капайлот, Slack→слек,
        # Discord→дискорт (оглушение), Obsidian→абсидиан, PyTorch→пайтерч,
        # LangChain→лэнгчин — плюс добавлены варианты живого произношения.
        # НЕ включены омонимы русских слов (сломали бы обычную речь): Cursor→
        # «курсор», Notion→«наушник», Redis→«редис» (овощ) — заводить вручную.
        {"to": "Jarvis",       "from_": ["джарвис", "жарвис"]},
        {"to": "Siri",         "from_": ["сири"]},
        {"to": "Alexa",        "from_": ["алекса"]},
        {"to": "Copilot",      "from_": ["капайлот", "копайлот", "ко пайлот"]},
        {"to": "Midjourney",   "from_": ["миджорни", "мидджорни", "мид джорни"]},
        {"to": "Perplexity",   "from_": ["перплексити"]},
        {"to": "Grok",         "from_": ["грок"]},
        {"to": "DeepSeek",     "from_": ["дипсик", "дип сик"]},
        {"to": "Figma",        "from_": ["фигма"]},
        {"to": "Slack",        "from_": ["слэк", "слек"]},
        {"to": "Discord",      "from_": ["дискорд", "дискорт"]},
        {"to": "Obsidian",     "from_": ["обсидиан", "абсидиан"]},
        {"to": "Postman",      "from_": ["постман"]},
        {"to": "Django",       "from_": ["джанго"]},
        {"to": "PyTorch",      "from_": ["пайторч", "пайтерч", "пай торч"]},
        {"to": "LangChain",    "from_": ["лэнгчейн", "лэнгчин", "ленгчейн"]},
        {"to": "PostgreSQL",   "from_": ["постгрес", "постгрэс"]},

        # ── v3-зонд (tests/probe_gigaam.py): формы под GigaAM v3-e2e-rnnt. ──
        # Сняты TTS→GigaAM v3 (v2-формы выше слышались иначе). Омонимы
        # (Grok→игрок, Git→гид, ChatGPT→чат…) ИСКЛЮЧЕНЫ. Дубль `to` ок —
        # apply_replacements прогоняет все правила.
        {"to": "Gemini", "from_": ["джимней", "джимрнай", "джимрней"]},
        {"to": "Copilot", "from_": ["капайд", "капайорд"]},
        {"to": "Llama", "from_": ["ламр", "ламу"]},
        {"to": "Mistral", "from_": ["мистрал"]},
        {"to": "Midjourney", "from_": ["миджерни"]},
        {"to": "Sora", "from_": ["сору"]},
        {"to": "Whisper", "from_": ["уиспе", "уиспер", "успер"]},
        {"to": "Gemma", "from_": ["гима", "гимы"]},
        {"to": "Qwen", "from_": ["курн"]},
        {"to": "Cursor", "from_": ["керса", "кирса", "кирсер"]},
        {"to": "Windsurf", "from_": ["уайнцер", "уйнцер", "уэнсер", "уэнцер"]},
        {"to": "Suno", "from_": ["суно"]},
        {"to": "Replit", "from_": ["реплит"]},
        {"to": "Notebook LM", "from_": ["ноутбук ле"]},
        {"to": "OpenAI", "from_": ["опеной", "опены"]},
        {"to": "DeepMind", "from_": ["дипмент"]},
        {"to": "Stability AI", "from_": ["стабелиты эй яй", "стабелиты эйя"]},
        {"to": "Cohere", "from_": ["кахир"]},
        {"to": "Scale AI", "from_": ["скилл и"]},
        # Mistral AI пропущен: «мистрал эй ай» съедается правилом «мистрал»→Mistral.
        {"to": "Together AI", "from_": ["тргет эй"]},
        {"to": "Tesla", "from_": ["тесла"]},
        {"to": "Intel", "from_": ["интел"]},
        {"to": "AMD", "from_": ["ампт"]},
        {"to": "Qualcomm", "from_": ["квалком"]},
        {"to": "Oracle", "from_": ["оракл"]},
        {"to": "Adobe", "from_": ["адок", "адоп", "адот"]},
        {"to": "Uber", "from_": ["убер"]},
        {"to": "GitHub", "from_": ["гиток"]},   # «гит оп» съедается правилом «гит»→git
        {"to": "GitLab", "from_": ["гидлеп"]},
        {"to": "Docker", "from_": ["дакр", "дакру"]},
        {"to": "Kubernetes", "from_": ["губернитис", "кубернитис"]},
        {"to": "Jira", "from_": ["джер", "джерро"]},
        {"to": "Jenkins", "from_": ["дженкинс"]},
        {"to": "Figma", "from_": ["фигмы"]},
        {"to": "Notion", "from_": ["нашен", "нашон"]},
        {"to": "Slack", "from_": ["слэг"]},

        # ── Акронимы (tests/probe_acronyms.py на v3): ТОЛЬКО формы, где v3 даёт
        # стабильный не-канон. Большинство акронимов v3 пишет латиницей сам
        # (HTML/CSS/USB/CPU…). ИИ→«и» и IT→«эти» НЕвосстановимы (коллизии).
        {"to": "URL", "from_": ["урл"]},
        {"to": "SQL", "from_": ["эскуэль"]},
        {"to": "CPU", "from_": ["сипью"]},
        {"to": "PDF", "from_": ["пдеф"]},

        # ── Латиница (Whisper выдаёт английский) + доп. омонимы AI ───────────
        # Матчинг регистронезависимый: «Dipsik» ловится формой «dipsik».
        # ChatGPT — РАНЬШE GPT, иначе «chat gpt» распадётся на «chat GPT».
        {"to": "ChatGPT",  "from_": ["chat gpt", "чат жпт"]},
        {"to": "DeepSeek", "from_": ["dipsik", "dip sik", "dipseek", "dip seek",
                                     "deepsik", "deep sik", "deepseek", "дипсек",
                                     "дип сек", "дипсиг"]},
        {"to": "Claude",   "from_": ["клауд", "клот", "клода", "claud", "claude"]},
        {"to": "GPT",      "from_": ["гпт", "джипити", "джи пи ти", "жпт", "gpt"]},
        {"to": "API",      "from_": ["эй пи ай", "а пи ай", "api"]},
    ]
