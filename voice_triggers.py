"""Кодовые слова диктовки (patch 2): старт / стоп / «причеши» / «ввод».

Флоу (решения 2026-06-03):
  «эй талкер» (wake, есть) → «талкер старт» → диктовка («Глебу, …» в начале =
  роль) → в конце «причеши, талкер стоп, ввод».
- «талкер старт» → начать, «талкер стоп» → закончить.
- «причеши» → перед вставкой прогнать текст через polish (флаг polish).
- «ввод» → после вставки нажать Enter (флаг submit) — отправить сообщение.

Чистая логика над текстом распознавания (без аудио).
Закалка: boundary_only — «старт» только в начале реплики, «стоп» только в конце.
Командные слова («причеши»/«ввод»), стоящие ПОСЛЕ стопа, НЕ ломают границу: при
проверке они маскируются (считаются «не-контентом»). fuzzy ловит ослышки на
коротких репликах. Свёртка lower()+ё→е длину не меняет → позиции совпадают.
"""
from __future__ import annotations

import difflib
import re

DEFAULT_START = ["талкер старт", "talker start", "толкер старт"]
# Единственная стоп-фраза hands-free — семейство «стоп-стоп» и её ослышки.
# «талкер стоп» и «джарвис стоп» убраны: GigaAM ненадёжно распознаёт имена
# («джарвис» коверкался десятком способов), а «стоп» — простое частотное слово,
# которое распознаётся стабильно. Разделитель не важен — _norm() сворачивает
# «стоп-стоп» / «стоп.стоп» / «стоп стоп» в одно (matchится через \W+), так что
# тут перечислены в основном слитные формы и звуковые ослышки.
STOP_STOPSTOP = [
    "стоп-стоп", "стоп стоп", "стопстоп",
    "стоп", "стоп-стоп-стоп", "стоп стоп стоп", "стопстопстоп",
    # п→б (оглушение на конце)
    "стоб", "стоб стоб", "стоб стоп", "стоп стоб", "стобстоб",
    # о→а (GigaAM часто слышит «а»)
    "стап", "стап-стап", "стап стап", "стапстап", "стап стоп", "стоп стап",
    # с→ш / прочие ослышки
    "штоп", "штоп штоп", "стопь",
]
DEFAULT_STOP = list(STOP_STOPSTOP)
DEFAULT_POLISH = ["причеши", "причешите", "причесать", "polish"]
DEFAULT_SUBMIT = ["ввод", "энтер", "enter"]
# «стоп-да» = стоп + Enter одной фразой: закончить диктовку И отправить
# (нажать Enter), не говоря отдельно «стоп-стоп … ввод». Детектится в scan()
# ДО маскировки одиночного «ввод» и ДО обычного стоп-рана (иначе «стоп» съел
# бы первую половину). «стоп-да» — основная фраза (коротко, легко говорится,
# «да» фонетически далеко от «стоп»); «талкер отправь» и «ввод-ввод» —
# синонимы.
DEFAULT_STOP_SUBMIT = ["стоп да", "стоп-да", "стопда", "стоб да", "стап да",
                       "талкер отправь", "толкер отправь", "талкер отправить",
                       "ввод ввод", "ввод-ввод", "вводввод", "ввод ввод ввод",
                       "ввот ввот", "ввод ввот", "ввот ввод"]


def build_stop_phrases() -> list[str]:
    """Стоп-фразы hands-free: только семейство «стоп-стоп» (см. STOP_STOPSTOP).
    Разделитель между словами не важен — regex ловит «стоп-стоп» / «стоп.стоп» /
    «стоп стоп» одинаково."""
    return list(STOP_STOPSTOP)


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", s)).strip()


def _fold(s: str) -> str:                       # длину сохраняет (1:1 по символам)
    return s.lower().replace("ё", "е")


def _only_pad(s: str) -> bool:                  # нет букв/цифр (или пусто)
    return re.search(r"\w", s) is None


def _compile(phrases: list[str]) -> re.Pattern:
    # Trailing «[.,!?…-]*» eats the punctuation the ASR glues onto a command word
    # («Талкер стоп.» / «Стоп-.» → вырезаем вместе с точкой/дефисом, чтобы в
    # тексте не оставалась «лишняя точка»-сирота).
    tail = r"\b[.,!?;:…)»”’\"'\-—–]*"
    alts = [r"\b" + r"\W+".join(re.escape(w) for w in _norm(p).split()) + tail
            for p in phrases if _norm(p)]
    return re.compile("|".join(alts), re.IGNORECASE) if alts else re.compile(r"(?!x)x")


def _compile_run(phrases: list[str]) -> "re.Pattern | None":
    """Матч МАКСИМАЛЬНОГО хвостового «забега» стоп-слов, прижатого к концу строки.
    Ловит «Стоп-Стоп.», «стоп стоп стоп», «талкер стоп ввод» (ввод замаскирован)
    целиком — там, где одиночное «стоп» жадно съело бы лишь первый кусок и оставило
    хвост («Стоп-») в тексте. Между словами — любая пунктуация/дефис, в конце —
    висячая пунктуация до \\Z. Порядок альтернатив не важен: (?:\\W+grp)* добирает
    следующие стоп-слова сам."""
    alts = [r"\b" + r"\W+".join(re.escape(w) for w in _norm(p).split()) + r"\b"
            for p in phrases if _norm(p)]
    if not alts:
        return None
    grp = "(?:" + "|".join(alts) + ")"
    return re.compile(grp + r"(?:\W+" + grp + r")*\W*\Z", re.IGNORECASE)


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    spans = sorted(spans)
    out, last = [], 0
    for s, e in spans:
        if s > last:
            out.append(text[last:s])
        last = max(last, e)
    out.append(text[last:])
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _fuzzy(norm: str, phrases_norm: list[str], thr: float) -> bool:
    return any(difflib.SequenceMatcher(None, norm, p).ratio() >= thr for p in phrases_norm)


class VoiceTriggers:
    def __init__(self, start_phrases: list[str] | None = None,
                 stop_phrases: list[str] | None = None,
                 polish_phrases: list[str] | None = None,
                 submit_phrases: list[str] | None = None,
                 stop_submit_phrases: list[str] | None = None, *,
                 boundary_only: bool = True, fuzzy: bool = False,
                 fuzzy_thr: float = 0.82) -> None:
        self.start_phrases = list(start_phrases or DEFAULT_START)
        self.stop_phrases = list(stop_phrases or DEFAULT_STOP)
        self.polish_phrases = list(polish_phrases or DEFAULT_POLISH)
        self.submit_phrases = list(submit_phrases or DEFAULT_SUBMIT)
        self.stop_submit_phrases = list(stop_submit_phrases or DEFAULT_STOP_SUBMIT)
        self.boundary_only = boundary_only
        self.fuzzy = fuzzy
        self.fuzzy_thr = fuzzy_thr
        self._re_start = _compile(self.start_phrases)
        self._re_stop = _compile(self.stop_phrases)
        self._re_stop_run = _compile_run(self.stop_phrases)
        self._re_stop_submit_run = _compile_run(self.stop_submit_phrases)
        self._re_polish = _compile(self.polish_phrases)
        self._re_submit = _compile(self.submit_phrases)
        self._starts_norm = [_norm(p) for p in self.start_phrases]
        self._stops_norm = [_norm(p) for p in self.stop_phrases]
        self.active = False
        self.polish_pending = False
        self.submit_pending = False

    def scan(self, text: str) -> tuple[list[str], str]:
        """→ (events, cleaned). events: 'start'/'stop'/'polish'/'submit' по позиции.
        Командные слова маскируются при проверке границы старт/стоп (чтобы «стоп-
        стоп ввод» считался стопом-в-конце). «ввод-ввод» = стоп + submit сразу."""
        folded = _fold(text)
        chars = list(folded)
        # «ввод-ввод» (удвоенное) = стоп + submit одной фразой. Ловим на сыром
        # folded ДО маскировки одиночного «ввод» ниже, затем гасим спан, чтобы
        # submit-/stop-регексы его не пересчитали.
        ss_hits: list[tuple[int, int, str]] = []
        if self.boundary_only and self._re_stop_submit_run is not None:
            m = self._re_stop_submit_run.search(folded)
            if m is not None:
                ss_hits.append((m.start(), m.end(), "stop"))
                ss_hits.append((m.start(), m.end(), "submit"))
                for i in range(m.start(), m.end()):
                    chars[i] = " "
        base = "".join(chars)                    # folded с погашенным «ввод-ввод»
        cmd_hits: list[tuple[int, int, str]] = []
        for rex, kind in ((self._re_polish, "polish"), (self._re_submit, "submit")):
            for m in rex.finditer(base):
                cmd_hits.append((m.start(), m.end(), kind))
                for i in range(m.start(), m.end()):
                    chars[i] = " "
        content = "".join(chars)                 # текст без командных слов (для границ)

        hits = ss_hits + cmd_hits
        for m in self._re_start.finditer(base):
            if not self.boundary_only or _only_pad(content[:m.start()]):
                hits.append((m.start(), m.end(), "start"))
        if self.boundary_only:
            # Хвостовой «забег» стоп-слов целиком (см. _compile_run): «Стоп-Стоп.»,
            # «стоп стоп стоп» — чтобы в тексте не оставался ведущий «Стоп-».
            if self._re_stop_run is not None:
                mr = self._re_stop_run.search(content)
                if mr is not None:
                    hits.append((mr.start(), mr.end(), "stop"))
        else:
            for m in self._re_stop.finditer(base):
                hits.append((m.start(), m.end(), "stop"))
        hits.sort()

        if not any(h[2] in ("start", "stop") for h in hits) and self.fuzzy:
            norm = _norm(text)
            if 0 < len(norm.split()) <= 3:
                if _fuzzy(norm, self._stops_norm, self.fuzzy_thr):
                    return ["stop"], ""
                if _fuzzy(norm, self._starts_norm, self.fuzzy_thr):
                    return ["start"], ""

        events = [h[2] for h in hits]
        cleaned = _strip_spans(text, [(s, e) for s, e, _ in hits])
        return events, cleaned

    def feed(self, text: str) -> dict:
        """Потоковый помощник. → {started, stopped, active, polish, submit, text}.
        polish/submit читать на стопе: polish — причесать перед вставкой, submit —
        нажать Enter после вставки."""
        events, cleaned = self.scan(text)
        started = stopped = False
        for ev in events:
            if ev == "start":
                self.active, started = True, True
                self.polish_pending = self.submit_pending = False
            elif ev == "stop":
                self.active, stopped = False, True
            elif ev == "polish":
                self.polish_pending = True
            elif ev == "submit":
                self.submit_pending = True
        give = self.active or stopped
        return {"started": started, "stopped": stopped, "active": self.active,
                "polish": self.polish_pending, "submit": self.submit_pending,
                "text": cleaned if give else ""}

    def reset(self) -> None:
        self.active = False
        self.polish_pending = self.submit_pending = False


# ── Самотест: python voice_triggers.py ───────────────────────────────────────
if __name__ == "__main__":
    vt = VoiceTriggers()
    assert vt.scan("талкер старт привет мир") == (["start"], "привет мир")
    assert vt.scan("привет стоп стоп") == (["stop"], "привет")
    assert vt.scan("талкер старт текст стоп стоп") == (["start", "stop"], "текст")
    assert vt.scan("обычный текст без команд") == ([], "обычный текст без команд")
    assert vt.scan("это стоп стоп для примера") == ([], "это стоп стоп для примера")
    # команды после стопа не ломают границу:
    assert vt.scan("текст причеши стоп стоп") == (["polish", "stop"], "текст")
    assert vt.scan("стоп стоп ввод") == (["stop", "submit"], "")
    assert vt.scan("текст причеши стоп стоп ввод") == (["polish", "stop", "submit"], "текст")
    # «ввод-ввод» = стоп + Enter одной фразой; одиночный «ввод» — только submit:
    assert vt.scan("письмо ввод ввод") == (["stop", "submit"], "письмо")
    assert vt.scan("текст ввод") == (["submit"], "текст")

    vt.reset()
    assert vt.feed("талкер старт пишем письмо")["text"] == "пишем письмо"
    r = vt.feed("готово причеши стоп стоп ввод")
    assert r["stopped"] and r["polish"] and r["submit"] and r["text"] == "готово", r
    print("voice_triggers: все проверки пройдены OK")
