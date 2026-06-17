# 22. Словарь-замена — детерминированная коррекция после STT

> Карта «слышу → пишу», которая применяется к сырому тексту Whisper **всегда** и без лимита токенов. Чинит систематические промахи (`клод → Claude`, `энтропик → Anthropic`), которые `initial_prompt` (концепт 05) ловит лишь статистически.

**Категория:** Tier 2 — самый дешёвый способ убрать «путаницу слов». Прямое продолжение 05.
**Эффект:** 🔥 круто.
**Готовность концепта:** 🟢 High.

---

## Зачем

Концепт 05 (`custom_vocabulary`) кормит Whisper'у `initial_prompt` — это **статистическое** смещение beam search. У него два ограничения:

1. Whisper *может* учесть слово, а может и проигнорировать — гарантии нет.
2. Жёсткий потолок `_MAX_WORDS_PROMPT = 60` ([vocabulary.py:22](../vocabulary.py)) / 224 токена. Полноценный доменный словарь туда не влезает.

Нужен **детерминированный** слой поверх STT: для известных промахов — «всегда заменяй X на Y». Это покрывает 90% жалоб «путает слова» при нулевом риске и нулевой стоимости.

Типовые правила:

| Слышу | Пишу |
|---|---|
| клод, клауд, клот | Claude |
| энтропик, антропик | Anthropic |
| кубернетес, кубернетис | kubernetes |
| эй-пи-ай, апишка | API |
| гитхаб | GitHub |

---

## Технический подход

### Хранение — `[[replacement]]` (как `[[snippet]]`)

Массив таблиц в `config.toml`, консистентно с существующими `[[snippet]]` / `[[voice_command]]` / `[[mode]]`:

```toml
[[replacement]]
to         = "Claude"
from       = ["клод", "клауд", "клот"]
whole_word = true                       # по умолчанию true

[[replacement]]
to         = "API"
from       = ["эй-пи-ай", "апи"]
whole_word = true
```

`config.py` — новый dataclass рядом со `SnippetConfig` ([config.py:182](../config.py)):

```python
@dataclass
class ReplacementConfig:
    to: str = ""
    from_: list[str] = field(default_factory=list)   # TOML key "from"
    whole_word: bool = True
```

> `from` — зарезервированное слово Python, поэтому в dataclass поле `from_`, а при парсинге маппим `m.get("from", [])`. Сохранение/загрузка — по образцу `cfg.snippets` ([config.py:387](../config.py), [config.py:455](../config.py)).

### Модуль `replacements.py`

```python
import re

def compile_rules(rules: list[ReplacementConfig]) -> list[tuple[re.Pattern, str]]:
    compiled = []
    for r in rules:
        if not r.to or not r.from_:
            continue
        variants = sorted((v for v in r.from_ if v), key=len, reverse=True)
        alt = "|".join(re.escape(v) for v in variants)
        # \b ненадёжен на стыке кириллицы/латиницы — используем lookaround по \w.
        body = rf"(?<!\w)(?:{alt})(?!\w)" if r.whole_word else rf"(?:{alt})"
        compiled.append((re.compile(body, re.IGNORECASE), r.to))
    return compiled

def apply_replacements(text: str, compiled) -> str:
    for pat, repl in compiled:
        text = pat.sub(lambda m, repl=repl: _preserve_case(m.group(0), repl), text)
    return text

def _preserve_case(src: str, repl: str) -> str:
    # «КЛОД» → «CLAUDE», «Клод» оставляем как канон (repl уже в нужном регистре).
    if src.isupper() and not repl.isupper():
        return repl.upper()
    return repl
```

Компиляцию делаем один раз при загрузке/сохранении конфига, не на каждый вызов.

### Где в пайплайне

В `_pre_clean_pipeline` ([main.py:967](../main.py)) — **после snippets, до backtrack**:

```python
# 2.5) Replacement dictionary — канонизируем термины ДО LLM и backtrack
text = apply_replacements(text, self._replacement_rules)
```

Порядок важен: канон попадает и в LLM-очистку (та не «переисправит» обратно), и в историю. Применяется мгновенно — модель не трогаем (как и словарь, см. `_apply_runtime_overrides` [main.py:403](../main.py)).

---

## Архитектура

**Новый модуль:** `replacements.py` (~50 LOC).

**Изменения:**
- `config.py` — `ReplacementConfig`, парсинг секции `replacement`, запись в `save_config`, поле `Config.replacements`.
- `main.py` — `self._replacement_rules = compile_rules(cfg.replacements)`, перекомпиляция в `_on_settings_saved` ([main.py:1164](../main.py)); вызов в `_pre_clean_pipeline`.
- `ui.py` — секция в `SettingsWindow` рядом со «Словарь» ([ui.py:2056](../ui.py)). Простейший формат textbox: одна строка `Claude = клод, клауд, клот`; парсим `to = from, from`.

---

## Зависимости

Никаких новых — `re` из stdlib.

---

## Edge cases

| Сценарий | Поведение |
|---|---|
| Вариант — подстрока обычного слова («код» внутри «кодекс») | `whole_word=true` + lookaround `(?<!\w)(?!\w)` не даёт ложных срабатываний. |
| Замена ломает уже верное слово | Правила точечные, заводит их пользователь. Логируем счётчик замен на реплику. |
| Регистр в речи разный («КЛОД» криком) | `_preserve_case`: всё-капс → `repl.upper()`, иначе канон как есть. |
| Один `from` в двух правилах | Первое в списке выигрывает (правила применяются по порядку). UI может предупредить о дубле. |
| Пересечение со сниппетом `anywhere` | Сниппеты ([snippets.py:73](../snippets.py)) идут раньше; это ок, разные задачи (расширение vs коррекция). |
| Пустой `to` или `from` | Правило пропускается при компиляции. |

---

## Acceptance criteria

- В Настройках можно завести правило «канон = варианты».
- После диктовки «посмотри код в клод» в поле появляется «Claude» детерминированно (10/10 раз).
- Замена не срабатывает внутри других слов при `whole_word=true`.
- Изменение правил не требует перезагрузки модели.
- LLM-очистка не откатывает канон обратно.

---

## Сложность

- ~2–3 часа, ~120 LOC.
- Основное — UI-секция и парсинг формата `to = from, from`.

---

## Открытые вопросы

- Перечислять варианты руками или ловить близкие по звучанию автоматически? → автоматику выносим в концепт **26 (фонетический матч)**, он переиспользует это хранилище.
- Подсасывать `to`-значения в `initial_prompt` Whisper (двойной эффект)? — да, бесплатно: канонические написания полезны и как биас (см. концепт 23).

---

## Источники

- [Python `re` Unicode word boundaries](https://docs.python.org/3/library/re.html)
- Связано: концепт 05 (custom vocabulary), 23 (глоссарий в промпт), 26 (фонетика).
