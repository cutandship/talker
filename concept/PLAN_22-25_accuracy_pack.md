# План-сборка: 22 + 23 + 24 + 25 — «accuracy pack»

> Единый план для реализации **в один присест**. Четыре фичи делят одни и те же файлы (`config.py`, `vocabulary.py`, `cleaner.py`, `transcriber.py`, `main.py`, `ui.py`), поэтому делать их по отдельности — значит трогать одни места по 2–3 раза и ловить конфликты. Здесь все правки собраны по файлам, в порядке применения, с точными якорями и готовым кодом. **Код ещё не внедрён** — это staging.

Входящие ТЗ: [22](22_replacement_dictionary.md) · [23](23_glossary_in_cleanup_prompt.md) · [24](24_number_normalization_itn.md) · [25](25_foreground_context_prompt.md).

---

## 0. Что входит в пакет (и что нет)

| № | Фича | В пакете |
|---|---|---|
| 22 | Словарь-замена после STT | ✅ полностью (детерминированный, работает всегда) |
| 23 | Глоссарий в промпт очистки | ✅ полностью |
| 24 | ITN (числа цифрами) | ✅ путь A (промпт LLM) **+ мини путь B** (`itn.py`) — числа работают даже без LLM |
| 25 | Контекст экрана в `initial_prompt` | ✅ полностью (только движок whisper) |

### Зависимость от LLM (решено: добавляем локальный ITN)

Очистку текста в Talker делает **LLM** (языковая модель — в интернете через API или локально). Сейчас в `config.toml` стоит `noop` ([config.toml:205](../config.toml)) — «очистка-пустышка»: текст от Whisper идёт в поле как есть, модель не подключена. Что это значит для фич:

- **23 (глоссарий)** — на `noop` **не работает**: некому передать инструкцию про канонические написания. Включится автоматически, как только в Настройках добавишь любой LLM-чистильщик (есть бесплатные — OpenRouter free / локальный Ollama).
- **24 (числа)** — теперь работает **и без LLM**: добавляем локальный модуль `itn.py` (§6b), который сам переводит числительные в цифры по правилам. Если LLM включён — он делает основное, `itn.py` дочищает остаток.
- **22 и 25** — от LLM **не зависят, работают всегда** (22 — обычная замена внутри Talker, 25 — подсказка для Whisper).

---

## 1. Общая схема изменений

```
STT (25: context → initial_prompt)
      │  raw
      ▼
_pre_clean_pipeline:  voice cmds → snippets → [22 replacements] → backtrack
      │  text_in
      ▼
cleaner_chain.clean(text_in, system_prompt=mode, extra_instructions=[23 glossary]+[24A ITN])
      │  text
      ▼
_paste
```

**Новый модуль:** `replacements.py` (22).
**Трогаем:** `config.py`, `vocabulary.py`, `cleaner.py`, `transcriber.py`, `cursor_format.py`, `main.py`, `ui.py`.

---

## 2. Консолидированная схема конфига (всё, что добавляется)

```toml
[stt]
context_priming = true        # 25 — учитывать текст на экране

[output]
glossary_in_prompt = true     # 23 — словарь в промпт LLM
number_format      = true     # 24 — числа цифрами (нужен LLM-чистильщик)

# 22 — словарь-замена (массив таблиц, как [[snippet]])
[[replacement]]
to         = "Claude"
from       = ["клод", "клауд", "клот"]
whole_word = true
```

> **Gotcha (важно):** новое поле в `OutputConfig`/`SttConfig` нужно добавить в **трёх** местах, иначе оно молча не сохранится: (1) dataclass в `config.py`, (2) writer в `save_config`, (3) reader в `ui.py:_save`. Прецедент уже есть — `consistency_check` выставляется в [ui.py:2923](../ui.py), но отсутствует в `OutputConfig` и в `save_config`, т.е. не персистится. Не повторяем эту ошибку.

---

## 3. `replacements.py` — новый модуль (22), готовый к созданию

```python
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
```

---

## 4. `config.py` — правки

**4.1** В `SttConfig` ([config.py:95](../config.py)) добавить поле:
```python
    context_priming: bool = True   # 25 — текст на экране как контекст Whisper
```

**4.2** В `OutputConfig` ([config.py:106](../config.py)) добавить:
```python
    glossary_in_prompt: bool = True   # 23
    number_format: bool = True        # 24 (нужен LLM-чистильщик)
```
> `load_config` для `[stt]`/`[output]` использует `_pick` ([config.py:283](../config.py)) — новые поля подхватятся автоматически при наличии в TOML, иначе дефолт. Парсить отдельно не нужно.

**4.3** Новый dataclass рядом со `SnippetConfig` ([config.py:182](../config.py)):
```python
@dataclass
class ReplacementConfig:
    to: str = ""
    from_: list[str] = field(default_factory=list)   # TOML-ключ "from"
    whole_word: bool = True
```

**4.4** В `Config` ([config.py:260](../config.py)) добавить поле:
```python
    replacements: list[ReplacementConfig] = field(default_factory=list)
```

**4.5** В `save_config` — после блока сниппетов ([config.py:387](../config.py)) добавить writer:
```python
    for r in cfg.replacements:
        lines.append("[[replacement]]")
        lines.append(f'to         = "{_escape_toml_str(r.to)}"')
        froms = ", ".join(f'"{_escape_toml_str(x)}"' for x in r.from_)
        lines.append(f"from       = [{froms}]")
        lines.append(f'whole_word = {"true" if r.whole_word else "false"}')
        lines.append("")
```

**4.6** В `load_config` — рядом с парсингом `snippet` ([config.py:455](../config.py)) добавить (нельзя `_pick`, т.к. `from`→`from_`):
```python
    if "replacement" in data:
        cfg.replacements = [
            ReplacementConfig(
                to=str(r.get("to", "")),
                from_=[str(x) for x in r.get("from", []) or []],
                whole_word=bool(r.get("whole_word", True)),
            )
            for r in data["replacement"]
        ]
```

**4.7** (опц.) В `_DEFAULT` ([config.py:9](../config.py)) дописать закомментированный пример `[[replacement]]` рядом со сниппетами — для самодокументируемости.

---

## 5. `vocabulary.py` — правки

**5.1** Расширить `build_initial_prompt` ([vocabulary.py:25](../vocabulary.py)) — добавить контекст (25):
```python
def build_initial_prompt(words: list[str], language: str | None,
                         context: str = "") -> str:
    base = _words_clause(words, language)          # текущая логика → вынести в helper
    ctx = (context or "").strip()
    if ctx:
        ctx = ctx[-400:]                            # хвост у курсора — релевантнее
        return (ctx + " " + base).strip() if base else ctx
    return base
```
> Текущее тело (строки 31–47) переименовать во внутренний `_words_clause(words, language)` без изменения логики; обрезку по `_MAX_WORDS_PROMPT` оставить там.

**5.2** Новая функция — глоссарий для промпта очистки (23):
```python
def glossary_suffix(words: list[str]) -> str:
    words = [w.strip() for w in words if w and w.strip()]
    if not words:
        return ""
    if len(words) > _MAX_WORDS_PROMPT:
        words = words[:_MAX_WORDS_PROMPT]
    return ("\n\nКанонические написания терминов и имён "
            "(используй ровно так, исправляй искажения по смыслу): "
            + ", ".join(words) + ".")
```

---

## 6. `cleaner.py` — правки (23 + 24A)

**6.1** Константа ITN рядом с `_SYSTEM_PROMPT` ([cleaner.py:12](../cleaner.py)):
```python
ITN_CLAUSE = (
    "\n\n- числа, проценты, даты, время и денежные суммы записывай цифрами "
    "(«двадцать пять процентов» → «25 %», «две тысячи двадцать шестой год» → "
    "«2026 год»); телефоны и номера оставляй как произнесено."
)
```

**6.2** Параметр `extra_instructions` во всех `clean()`. Паттерн (для `ApiCleaner`/`OllamaCleaner`/`LocalLlmCleaner`):
```python
def clean(self, text, system_prompt=None, extra_instructions: str = "") -> str:
    sys = (system_prompt or _SYSTEM_PROMPT) + extra_instructions
    ...  # вместо (system_prompt or _SYSTEM_PROMPT)
```
`NoopCleaner` / `PunctuationCleaner` — просто принимают `extra_instructions` и игнорируют.

**6.3** `CleanerChain.clean` ([cleaner.py:137](../cleaner.py)) — добавить параметр и пробросить:
```python
def clean(self, text, system_prompt=None, extra_instructions: str = "") -> tuple[str, bool]:
    ...
        result = cleaner.clean(text, system_prompt=system_prompt,
                               extra_instructions=extra_instructions)
```

---

## 6b. `itn.py` — новый модуль: числа цифрами без LLM (24, путь B)

> Переводит числительные прописью в цифры по правилам, без модели. Кардиналы (ноль…999 999) и проценты — надёжно. Годы/время/порядковые — НЕ здесь (см. §13), чтобы не плодить регрессии. Вызывается в `_process` после очистки (§9.7).

```python
"""Локальная нормализация чисел для русского (концепт 24, путь B).

Числительные прописью → цифры, без LLM. Применяется к финальному тексту.
Надёжно: кардиналы и проценты. Порядковые (годы) намеренно пропускаются.
"""
from __future__ import annotations
import re

_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
    "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000}
_ALL = list(_UNITS) + list(_SCALES)


def _to_int(tokens: list[str]) -> int:
    total = cur = 0
    for t in tokens:
        if t in _UNITS:
            cur += _UNITS[t]
        elif t in _SCALES:
            cur = (cur or 1) * _SCALES[t]
            total += cur
            cur = 0
    return total + cur


_RUN = re.compile(
    r"\b(?:" + "|".join(sorted(_ALL, key=len, reverse=True)) +
    r")(?:\s+(?:" + "|".join(_ALL) + r"))*\b", re.IGNORECASE)
# Порядковое слово сразу после числа («двадцать шестой год») — НЕ трогаем.
_ORD_AHEAD = re.compile(r"\s+[а-яё]+(?:ый|ой|ий|ого|ом|ому|ых|ые|ая|ое)\b", re.IGNORECASE)
_PCT = re.compile(r"(\d+)\s+процент(?:ов|а)?", re.IGNORECASE)


def normalize(text: str, language: str = "ru") -> str:
    if not text or not (language or "").lower().startswith("ru"):
        return text

    def _repl(m: re.Match) -> str:
        if _ORD_AHEAD.match(m.string, m.end()):     # за числом порядковое → пропуск
            return m.group(0)
        return str(_to_int(m.group(0).lower().split()))

    text = _RUN.sub(_repl, text)            # «двадцать пять» → «25»
    text = _PCT.sub(r"\1 %", text)          # «25 процентов» → «25 %»
    return text
```

> Гард `_ORD_AHEAD`: если сразу за числительным идёт порядковое слово (год), фразу не трогаем целиком — иначе «две тысячи двадцать шестой» превратилось бы в «2020 шестой». Перед расширением на годы/время — прогон на `talker.log`.

---

## 7. `transcriber.py` — правки (25)

**7.1** В `__init__` ([transcriber.py:50](../transcriber.py)) рядом с `self.vocabulary`:
```python
        self.context: str = ""    # 25 — подмешивается в initial_prompt
```

**7.2** В `transcribe` ([transcriber.py:111](../transcriber.py)):
```python
        initial_prompt = build_initial_prompt(
            self.vocabulary, self.language, self.context) or None
```

---

## 8. `cursor_format.py` — правка (25)

Добавить сборщик контекста (переиспользует существующий `read_caret_context` + `modes.get_foreground_info`):
```python
def gather_context() -> str:
    parts: list[str] = []
    try:
        ctx = read_caret_context()
        if ctx and getattr(ctx, "text", ""):
            parts.append(ctx.text)
    except Exception:
        pass
    try:
        from modes import get_foreground_info
        title = get_foreground_info().title
        if title:
            parts.append(title)
    except Exception:
        pass
    return " ".join(parts).strip()
```
> Проверить отсутствие циклического импорта: `modes` тянет только stdlib/ctypes — импорт внутри функции безопасен.

---

## 9. `main.py` — правки

**9.1 Импорты** (рядом с [main.py:96–97](../main.py)):
```python
from replacements import compile_rules, apply_replacements
from vocabulary import glossary_suffix
from cleaner import ITN_CLAUSE
```

**9.2 Новый метод** — пересборка рантайм-артефактов (не зависит от transcriber, поэтому отдельно от `_apply_runtime_overrides`, который рано выходит при `transcriber is None`, [main.py:413](../main.py)):
```python
def _rebuild_text_aids(self) -> None:
    """Пересобрать правила-замены и доп-инструкции для очистки. Дёшево;
    звать при старте и из _on_settings_saved."""
    self._replacement_rules = compile_rules(self.config.replacements)
    extra = ""
    if self.config.output.glossary_in_prompt:
        extra += glossary_suffix(self.config.vocabulary.words)
    if self.config.output.number_format:
        extra += ITN_CLAUSE
    self._extra_instructions = extra
```

**9.3 Вызвать** `_rebuild_text_aids()`:
- в `__init__` — сразу после первого `self.cleaner_chain = build_cleaner_chain(...)` (инициализировать `self._replacement_rules = []`, `self._extra_instructions = ""` до этого на всякий случай);
- в конце `_on_settings_saved` ([main.py:1232](../main.py)), рядом с `_apply_runtime_overrides()`.

**9.4 Замены в пайплайне** — `_pre_clean_pipeline` ([main.py:980](../main.py)), между snippet-return и backtrack:
```python
        text, exact = apply_snippets(text, self._build_snippet_objects())
        if exact:
            return text, True, actions
        # 2.5) Словарь-замена (22): канонизируем ДО backtrack и LLM
        text = apply_replacements(text, self._replacement_rules)
        # 3) Backtrack
        if self.config.output.backtrack:
            text = apply_backtrack(text, self.config.stt.language)
```

**9.5 Контекст перед транскрипцией** — в `_process` ([main.py:797](../main.py)), перед `raw = self.transcriber.transcribe(audio)`:
```python
            if (self.config.stt.context_priming
                    and self.config.stt.engine == "whisper"):
                try:
                    from cursor_format import gather_context
                    self.transcriber.context = gather_context()
                except Exception:
                    self.transcriber.context = ""
```
> Действует на полном декоде (≤20 с). Длинный bg_job-путь (>20 с, [main.py:793](../main.py)) декодирует чанки заранее, без контекста — это ок, отметить в Notes.

**9.6 Проброс доп-инструкций в очистку:**
- `_process` ([main.py:813](../main.py)):
```python
                text, cleaned = self.cleaner_chain.clean(
                    text_in, system_prompt=mode_prompt,
                    extra_instructions=self._extra_instructions)
```
- `_process_command` ([main.py:1117](../main.py)):
```python
            text, cleaned = self.cleaner_chain.clean(
                user_msg, system_prompt=sys_prompt,
                extra_instructions=self._extra_instructions)
```

**9.7 Локальная нормализация чисел (24, путь B)** — в `_process` ([main.py:816](../main.py)), сразу после блока очистки, до вставки:
```python
            if self.config.output.number_format:
                from itn import normalize
                text = normalize(text, self.config.stt.language)
```
> Один флаг `number_format` управляет обоими путями. Включены оба — LLM конвертит основное, `itn.normalize` дочищает; только `noop` — работает один `itn.py`.

---

## 10. `ui.py` — правки

**10.1 Редактор замен (22)** — после vocab-textbox ([ui.py:2066](../ui.py)):
```python
self._hdr(sf, "Замены  (коррекция: канон = варианты, по одному правилу на строку)")
ctk.CTkLabel(sf, text="Например:  Claude = клод, клауд, клот",
             text_color="#666", font=_f("Segoe UI", 9), anchor="w").pack(fill="x", padx=2)
self._repl_text = ctk.CTkTextbox(sf, height=80, font=_f("Consolas", 10))
self._repl_text.pack(fill="x", pady=(4, 0))
self._repl_text.insert("1.0", "\n".join(
    f"{r.to} = {', '.join(r.from_)}" for r in self._cfg.replacements))
```

**10.2 Чекбоксы (23/24/25)** — рядом со «Словарь»:
```python
self._glossary_var = tk.BooleanVar(value=self._cfg.output.glossary_in_prompt)
ctk.CTkCheckBox(sf, text="Словарь в промпт очистки (омофоны)",
                variable=self._glossary_var).pack(anchor="w", pady=2)
self._numfmt_var = tk.BooleanVar(value=self._cfg.output.number_format)
ctk.CTkCheckBox(sf, text="Числа цифрами — 25 %, 2026 (нужен LLM-чистильщик)",
                variable=self._numfmt_var).pack(anchor="w", pady=2)
self._context_var = tk.BooleanVar(value=self._cfg.stt.context_priming)
ctk.CTkCheckBox(sf, text="Учитывать текст на экране (контекст для Whisper)",
                variable=self._context_var).pack(anchor="w", pady=2)
```

**10.3 В `_save`** ([ui.py:2903](../ui.py)) — рядом с другими `output`:
```python
self._cfg.output.glossary_in_prompt = self._glossary_var.get()
self._cfg.output.number_format = self._numfmt_var.get()
self._cfg.stt.context_priming = self._context_var.get()
# Замены: парсим "to = a, b"
repls = []
for line in self._repl_text.get("1.0", "end").splitlines():
    if "=" not in line:
        continue
    to, _, rest = line.partition("=")
    froms = [x.strip() for x in rest.split(",") if x.strip()]
    if to.strip() and froms:
        repls.append(ReplacementConfig(to=to.strip(), from_=froms))
self._cfg.replacements = repls
```
> Добавить импорт `ReplacementConfig` в `ui.py` (там, где импортируются прочие config-классы).

---

## 11. Порядок применения (чек-лист одного присеста)

1. [ ] `replacements.py` — создать (§3).
2. [ ] `itn.py` — создать (§6b).
3. [ ] `config.py` — поля + dataclass + save/load (§4).
4. [ ] `vocabulary.py` — `build_initial_prompt(context=)` + `glossary_suffix` (§5).
5. [ ] `cleaner.py` — `ITN_CLAUSE` + `extra_instructions` по цепочке (§6).
6. [ ] `transcriber.py` — `self.context` (§7).
7. [ ] `cursor_format.py` — `gather_context` (§8).
8. [ ] `main.py` — импорты, `_rebuild_text_aids`, вызовы, пайплайн (22-замена + 24B-itn), проброс (§9).
9. [ ] `ui.py` — секция замен, 3 чекбокса, `_save` (§10).
10. [ ] Smoke-тесты (§12).

Логически независимые куски: **22** (1,2-часть,4.4–4.6,9.4,10.1/10.3-замены) и **25** (4.1,5.1,7,8,9.5,10.2/10.3-context) можно даже коммитить отдельно от **23+24** (4.2,5.2,6,9.2/9.6,10.2/10.3-флаги). Но раз делаем вместе — порядок выше самый бесконфликтный.

---

## 12. Smoke-тесты

| Фича | Проверка | Ожидаемо |
|---|---|---|
| 22 | Правило `Claude = клод`; сказать «открой клод» | в поле «открой Claude», 10/10 |
| 22 | Сказать «кодекс чести» при правиле `код=...` | «кодекс» не тронут (whole_word) |
| 23 | LLM включён, в словаре «кампания»; «запусти кампанию» | «кампанию», не «компанию» |
| 24 | флаг on; «выросли на двадцать процентов» | «выросли на 20 %» (работает и на `noop` через `itn.py`) |
| 24 | «в две тысячи двадцать шестом году» | НЕ ломаем в «2020 …» — порядковые пропускаются (годы — путь B v2) |
| 25 | Ответ в треде про конкретную тему | термин темы распознан точнее; пустой контекст не ломает |
| все | Сохранить Настройки, перезапустить | поля сохранились в `config.toml` |

---

## 13. Вне пакета (следующая итерация)

- **24 путь B v2** — годы, время, порядковые («две тысячи двадцать шестой» → «2026»). Мини-версия (кардиналы + проценты) уже в пакете; полнота требует бенчмарка на `talker.log`.
- **26/27/28** — фонетика, IT-набор, майнинг истории. 26/27/28 переиспользуют хранилище `[[replacement]]` из этого пакета.

---

## 14. Notes (заполнять при реализации)

- …
