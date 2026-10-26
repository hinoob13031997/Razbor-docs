"""Сверка названия файла с содержимым, расшифровка документа простыми словами, описки внутри документа.

Для проверяющего программа отвечает на три вопроса:
  1. Что это за документ на самом деле (одна-две фразы, без жаргона).
  2. Сходится ли с этим название файла: вид документа, дата, сторона, номера, фамилия.
  3. Как назвать файл правильно, если не сходится.

Названия пишут люди, поэтому ошибки бывают разные: другой вид документа, перепутанная сторона,
опечатка в слове или в номере, переставленные цифры, день и месяц наоборот, дата другого документа,
чужая фамилия, название «Скан_001». Каждый вид ошибки сообщается отдельной понятной фразой.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from .textutil import find_dates, fmt_money

if TYPE_CHECKING:  # pragma: no cover
    from .analyze import Analysis

# ---------------------------------------------------------------- общие мелочи

_LOOK = str.maketrans({"a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о",
                       "p": "р", "t": "т", "x": "х", "y": "у", "i": "і"})
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
MONTH_STEMS = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("мая", 5), ("май", 5), ("июн", 6), ("июл", 7),
               ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]
MONTH_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
             "октября", "ноября", "декабря"]


def _lower(s: str) -> str:
    return s.lower().replace("ё", "е")


def _fix_word(w: str) -> str:
    """Латинские двойники внутри русского слова («oпределение» с латинской o) -> русские буквы."""
    lw = w.lower()
    if re.search(r"[а-яё]", lw) and re.search(r"[a-z]", lw):
        return lw.translate(_LOOK)
    return lw


def _month_of(word: str) -> int | None:
    w = _lower(word)
    for stem, n in MONTH_STEMS:
        if w.startswith(stem):
            return n
    return None


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _key(s: str) -> str:
    """Номер для сравнения: без знаков, заглавными, латиница -> кириллица."""
    t = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", str(s)).upper().replace("Ё", "Е")
    return t.translate(str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))


def _d(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


# ---------------------------------------------------------------- виды документов по названию

FSSP_KEYS = {"fssp_start", "fssp_end", "fssp_refuse", "fssp_stop", "fssp_levy", "fssp_limit", "fssp_act", "fssp_other"}

# (регулярное выражение по названию, допустимые виды, как назвать в сообщении)
CUES: list[tuple[str, set, str]] = [
    (r"отмен\w*\W+(?:судебн\w*\W+)?приказ", {"order_cancel"}, "определение об отмене судебного приказа"),
    (r"приказ", {"order", "order_application", "order_cancel"}, "судебный приказ"),
    (r"исполнительн\w*\W+лист|(?<![а-я])ил(?![а-я])", {"writ"}, "исполнительный лист"),
    (r"правопреем|замен[аеыуи]\w*\W+(?:сторон|взыскател|истц|ответчик|должник|заявител)|(?<![а-я])замен[аеыуи](?![а-я])",
     {"succession"}, "определение о замене стороны (правопреемство)"),
    (r"определени", {"ruling", "order_cancel", "succession", "bankr"}, "определение суда"),
    (r"решени", {"decision", "bankr"}, "решение суда"),
    (r"постановлени", FSSP_KEYS, "постановление судебного пристава"),
    (r"возбужд", {"fssp_start", "fssp_refuse", "order"}, "о возбуждении исполнительного производства"),
    (r"оконч", {"fssp_end"}, "об окончании исполнительного производства"),
    (r"прекращени", {"fssp_stop"}, "о прекращении исполнительного производства"),
    (r"обращени\w*\W+взыскани|удержани", {"fssp_levy"}, "об обращении взыскания"),
    (r"арест|запрет|ограничени\w*\W+(?:на\W+)?выезд", {"fssp_limit"}, "об ограничениях / аресте"),
    (r"цесси|уступк", {"cession", "cession_notice", "succession"}, "договор уступки (цессии)"),
    (r"кредитн|займ|(?<![а-я])кд(?![а-я])", {"credit", "calc", "claim_letter", "claim", "decision", "order"}, "кредитный договор / займ"),
    (r"договор", {"credit", "cession", "cession_notice", "questionnaire", "calc", "claim_letter", "claim",
                  "decision", "order", "succession"}, "договор"),
    (r"анкет", {"questionnaire"}, "анкета"),
    (r"смерт", {"death_cert", "estate"}, "свидетельство о смерти"),
    (r"наследств|нотариус|нотариальн", {"estate", "death_cert"}, "наследственное дело / нотариус"),
    (r"банкрот|несостоятельн", {"bankr"}, "банкротство"),
    (r"претензи|уведомлени\w*\W+о\W+задолж", {"claim_letter"}, "претензия"),
    (r"расчет|справк\w*\W+о\W+(?:размер\w*\W+)?задолж|выписк", {"calc"}, "расчёт / справка о задолженности"),
    (r"исков|(?<![а-я])иск(?![а-я])", {"claim"}, "исковое заявление"),
    (r"паспорт", {"passport"}, "паспорт"),
]
_CUES = [(re.compile(rx), allowed, label) for rx, allowed, label in CUES]

# слова для исправления опечаток в названии: слово из названия -> правильное
VOCAB = ["определение", "решение", "постановление", "приказ", "судебный", "исполнительный", "исполнительного",
         "производства", "возбуждении", "окончании", "прекращении", "правопреемстве", "правопреемство",
         "замене", "стороны", "взыскателя", "должника", "истца", "ответчика", "уступки", "цессии",
         "свидетельство", "смерти", "банкротстве", "банкротстве", "претензия", "расчет", "справка", "анкета",
         "договор", "кредитный", "займа", "наследственное", "нотариус", "требования", "заявление",
         "отмене", "обращении", "взыскания", "задолженности", "исковое", "выписка", "копия", "окончание",
         "возбуждение", "прекращение", "замена", "обращение", "уступка", "цессия", "претензии", "расчета",
         "расчёт", "справки", "свидетельства", "наследства", "банкротстве", "банкротство", "заявления"]
_VOCAB_SET = set(VOCAB)

FILLER = {"копия", "копии", "скан", "сканы", "сканирование", "документ", "документы", "файл", "новый", "новое",
          "образец", "оригинал", "приложение", "материалы", "материал", "дело", "дела", "гражданское", "номер",
          "итог", "итоговый", "финал", "final", "doc", "docs", "scan", "img", "image", "pdf", "photo", "фото",
          "картинка", "версия", "от", "по", "на", "для", "или", "что", "это", "его", "ее", "их", "при", "без",
          "под", "над", "про", "год", "года", "номера", "суд", "суда", "судебный", "судебного", "договора",
          "договору", "отношении", "стороне", "стороны", "лица", "лицо", "дата", "дате", "иск", "иска",
          "исполнительное", "исполнительного", "производство", "производства", "ип", "сп", "рф", "гпк", "ооо",
          "оао", "пао", "зао", "мкк", "мфк", "пко", "банк", "уид", "лист", "листа", "лист", "стр", "страница",
          "страницы", "часть", "ч", "и", "в", "с", "к", "о", "об", "а"}
_SURNAMELIKE = re.compile(r"(?:ов|ев|ёв|ин|ын|ский|цкий|ской|ова|ева|ёва|ина|ына|ская|цкая|енко|юк|ук|ич|"
                          r"ову|еву|ину|овой|евой|иной|овна|евна|ичу|овым|евым|иным)$", re.I)

GENERIC_NAME = re.compile(
    r"^(?:img|dsc|scan|doc|docs|document|file|photo|image|picture|pic|scanner|скан\w*|документ\w*|файл\w*|"
    r"новый\w*|изображение|фото\w*|без\W*названия|безымянный|копия|сканирование)[\W_\d]*(?:копия)?[\W_\d]*$", re.I)


# ---------------------------------------------------------------- разбор названия

@dataclass
class NameDate:
    day: int | None
    month: int | None
    year: int | None
    text: str

    def full(self) -> date | None:
        if self.day and self.month and self.year:
            try:
                return date(self.year, self.month, self.day)
            except ValueError:
                return None
        return None

    def show(self) -> str:
        if self.day and self.month and self.year:
            return f"{self.day:02d}.{self.month:02d}.{self.year}"
        if self.month and self.year:
            return f"{self.month:02d}.{self.year}"
        return str(self.year or "")


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 and n <= 40 else n + 1900 if n < 100 else n


def _valid(d: int, m: int, y: int) -> bool:
    try:
        date(y, m, d)
        return 1990 <= y <= 2100
    except ValueError:
        return False


def parse_dates(base: str) -> tuple[list[NameDate], str]:
    """Даты из названия в любой записи; возвращает (даты, название с вырезанными датами)."""
    found: list[tuple[int, int, NameDate]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(m: re.Match, nd: NameDate) -> None:
        if free(m.start(), m.end()):
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), nd))

    for m in re.finditer(r"(?<!\d)(\d{4})[.\-_/ ](\d{1,2})[.\-_/ ](\d{1,2})(?!\d)", base):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(d, mo, y):
            add(m, NameDate(d, mo, y, m.group(0)))
    for m in re.finditer(r"(?<!\d)(\d{1,2})[.\-_/ ](\d{1,2})[.\-_/ ](\d{4}|\d{2})(?!\d)", base):
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if _valid(d, mo, y):
            add(m, NameDate(d, mo, y, m.group(0)))
    for m in re.finditer(r"(?<!\d)(\d{1,2})\W{0,3}([а-яёА-ЯЁ]{3,9})\W{0,3}(\d{4}|\d{2})?(?:\W{0,2}(?:г|года|год))?(?![\dа-яё])", base):
        mo = _month_of(m.group(2))
        if mo and 1 <= int(m.group(1)) <= 31:
            y = _year(m.group(3)) if m.group(3) else None
            add(m, NameDate(int(m.group(1)), mo, y, m.group(0)))
    for m in re.finditer(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", base):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(d, mo, y):
            add(m, NameDate(d, mo, y, m.group(0)))
    for m in re.finditer(r"(?<!\d)(\d{2})(\d{2})(\d{4}|\d{2})(?!\d)", base):
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if _valid(d, mo, y):
            add(m, NameDate(d, mo, y, m.group(0)))
    for m in re.finditer(r"(?<![\d.])(\d{1,2})[.\-_/](\d{4})(?![\d])", base):
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1990 <= y <= 2100:
            add(m, NameDate(None, mo, y, m.group(0)))
    for m in re.finditer(r"(?<![\dа-яё])([а-яё]{3,9})\W{0,3}(\d{4})(?!\d)", base, re.I):
        mo = _month_of(m.group(1))
        if mo and 1990 <= int(m.group(2)) <= 2100:
            add(m, NameDate(None, mo, int(m.group(2)), m.group(0)))
    found.sort(key=lambda t: t[0])
    rest = base
    for a, b, _ in sorted(found, key=lambda t: -t[0]):
        rest = rest[:a] + " " + rest[b:]
    return [nd for _, _, nd in found], rest


@dataclass
class NameClaims:
    base: str
    dates: list[NameDate] = field(default_factory=list)
    cases: list[tuple[str, str, int]] = field(default_factory=list)   # (префикс, номер, год)
    ips: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)                    # похожие на номера договоров
    text: str = ""                                                      # нижний регистр, с исправленными опечатками
    words: list[str] = field(default_factory=list)                      # прочие слова (возможно, фамилии)
    raw_words: list[str] = field(default_factory=list)
    typos: list[tuple[str, str]] = field(default_factory=list)          # (как написано, как правильно)
    side: str = ""                                                       # debtor | creditor | ""
    upper: bool = False
    generic: bool = False


def repair_typos(tokens: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    out, typos = [], []
    for t in tokens:
        w = _fix_word(t)
        if len(w) >= 6 and w not in _VOCAB_SET and w not in FILLER:
            best = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.82)
            if best and abs(len(best[0]) - len(w)) <= 2:
                typos.append((t, best[0]))
                w = best[0]
        out.append(w)
    return out, typos


def parse_name(filename: str) -> NameClaims:
    base = os.path.splitext(os.path.basename(filename))[0]
    c = NameClaims(base=base)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", base)
    c.upper = bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.8
    c.generic = GENERIC_NAME.match(base.strip()) is not None
    c.dates, rest = parse_dates(base)
    # номера ИП и дел
    for m in re.finditer(r"(?<!\d)(\d{3,8})[-_/ ](\d{2})[-_/ ](\d{4,6})(?:[-_ ]?(?:ИП|СД|IP))?(?!\d)", rest, re.I):
        c.ips.append(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"(?<![\dA-Za-zА-Яа-я])(А\d{2}|\d{1,2}[а-я]?)[-_ /](\d{1,6})[-_ /](\d{4}|\d{2})(?!\d)", rest):
        c.cases.append((m.group(1).upper(), m.group(2), _year(m.group(3))))
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"(?<![A-Za-zА-Яа-я0-9])([A-Za-zА-Яа-я]{1,6}[-_]?\d{3,}[\w\-]*|\d{6,})(?![A-Za-zА-Яа-я0-9])", rest):
        if not c.generic and not re.match(r"(?i)(?:scan|img|dsc|doc|file|photo|image|pic|page|скан|фото|файл|стр)", m.group(1)):
            c.numbers.append(m.group(1))
        rest = rest.replace(m.group(0), " ")
    for m in re.finditer(r"(?<![\d\-/.])((?:19|20)\d{2})(?![\d\-/])", rest):
        c.dates.append(NameDate(None, None, int(m.group(1)), m.group(0)))
        rest = rest.replace(m.group(0), " ", 1)
    toks = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", rest)
    fixed, c.typos = repair_typos(toks)
    c.text = " ".join(fixed)
    c.raw_words = toks
    for orig, w in zip(toks, fixed):
        if len(w) >= 4 and w not in _VOCAB_SET and w not in FILLER and not any(rx.search(w) for rx, _, _ in _CUES):
            c.words.append(orig)
    has_d = re.search(r"должник|ответчик|заемщик|заёмщик", c.text) is not None
    has_c = re.search(r"взыскател|истц|истец|кредитор", c.text) is not None
    c.side = "debtor" if has_d and not has_c else "creditor" if has_c and not has_d else ""
    return c


# ---------------------------------------------------------------- результат сверки

@dataclass
class Row:
    what: str
    name: str
    doc: str
    state: str   # ok | typo | bad | info

    def to_dict(self) -> dict:
        return {"what": self.what, "name": self.name, "doc": self.doc, "state": self.state}


@dataclass
class NameCheck:
    verdict: str = "unknown"   # ok | typo | wrong | generic | unknown
    title: str = ""
    problems: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    suggest: str = ""

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "title": self.title, "problems": self.problems,
                "rows": [r.to_dict() for r in self.rows], "suggest": self.suggest}


SIDE_RU = {"debtor": "должник", "creditor": "взыскатель (истец)"}
ROLE_HUMAN = {"истец": "взыскатель (истец)", "взыскатель": "взыскатель", "заявитель": "взыскатель (заявитель)",
              "ответчик": "должник (ответчик)", "должник": "должник", "сторона": "сторона"}
ROLE_HUMAN_GEN = {"истец": "взыскателя (истца)", "взыскатель": "взыскателя", "заявитель": "взыскателя (заявителя)",
                  "ответчик": "должника (ответчика)", "должник": "должника", "сторона": "стороны"}


def _doc_side(a: "Analysis") -> tuple[str, str]:
    """Кого заменили в документе: (debtor|creditor|'', слово для людей)."""
    h = a.fields.get("Кого заменили")
    if h is None:
        return "", ""
    role = (h.note or "").split(",")[0].strip()
    if role in ("истец", "взыскатель", "заявитель"):
        return "creditor", role
    if role in ("ответчик", "должник"):
        return "debtor", role
    return "", role


def _fv(a: "Analysis", name: str):
    h = a.fields.get(name)
    return h.value if h is not None else None


def check_name(filename: str, a: "Analysis") -> NameCheck:
    nc = NameCheck()
    if a is None or a.doc_key == "unknown" or a.chars < 30:
        nc.verdict, nc.title = "unknown", "Не удалось сверить: вид документа не определён"
        c0 = parse_name(filename)
        nc.suggest = ""
        if c0.dates or c0.cases or c0.ips:
            nc.rows.append(Row("Название", os.path.basename(filename), "—", "info"))
        return nc
    c = parse_name(filename)
    bad = typo = 0
    comparable = 0

    def row(what, name, doc, state, problem=""):
        nonlocal bad, typo
        nc.rows.append(Row(what, name, doc, state))
        if state == "bad":
            bad += 1
        elif state == "typo":
            typo += 1
        if problem:
            nc.problems.append(problem)

    # --- 1. вид документа
    cues = [(rx, allowed, label) for rx, allowed, label in _CUES if rx.search(c.text)]
    # «приказ» внутри «отмене приказа» не считаем отдельным признаком
    if any(l.startswith("определение об отмене") for _, _, l in cues):
        cues = [x for x in cues if not (x[2] == "судебный приказ")]
    if cues:
        comparable += 1
        fails = [x for x in cues if a.doc_key not in x[1]]
        shown = ", ".join(sorted({x[2] for x in cues}, key=len)[:2])
        if not fails:
            row("Вид документа", shown, a.doc_label, "ok")
        else:
            claimed = "; ".join(x[2] for x in fails)
            row("Вид документа", claimed, a.doc_label, "bad",
                f"В названии указано «{claimed}», а по содержанию это «{a.doc_label}».")
    else:
        row("Вид документа", "в названии не указан", a.doc_label, "info")

    # --- 2. опечатки в словах названия
    for orig, right in c.typos:
        row("Слово в названии", orig, right, "typo", f"В названии слово «{orig}» написано с ошибкой (правильно «{right}»).")

    # --- 3. дата
    doc_date = _fv(a, "Дата документа")
    doc_dates = list(getattr(a, "dates", []) or [])
    for nd in c.dates:
        comparable += 1
        full = nd.full()
        shown = nd.show()
        if full is not None and isinstance(doc_date, date):
            if full == doc_date:
                row("Дата", shown, _d(doc_date), "ok")
                continue
            label = next((lab for iso, lab in doc_dates if iso == full.isoformat() and lab), "")
            same_text = any(iso == full.isoformat() for iso, _ in doc_dates)
            if full.month == doc_date.day and full.day == doc_date.month and full.year == doc_date.year and full.day <= 12 and full.month <= 12:
                row("Дата", shown, _d(doc_date), "typo",
                    f"В названии дата {shown}, в документе {_d(doc_date)}: похоже, перепутаны день и месяц.")
            elif (full.year == doc_date.year and
                  sum(x != y for x, y in zip(full.strftime("%d%m"), doc_date.strftime("%d%m"))) == 1):
                row("Дата", shown, _d(doc_date), "typo",
                    f"В названии дата {shown}, в документе {_d(doc_date)}: отличается одной цифрой — возможно, опечатка.")
            elif same_text:
                what = f" — это {label}" if label else " — это другая дата из текста документа"
                row("Дата", shown, _d(doc_date), "bad",
                    f"В названии дата {shown}{what}, а сам документ составлен {_d(doc_date)}.")
            else:
                row("Дата", shown, _d(doc_date), "bad",
                    f"В названии дата {shown}, а документ составлен {_d(doc_date)}.")
        elif full is None and isinstance(doc_date, date):
            if nd.year == doc_date.year and (nd.month is None or nd.month == doc_date.month):
                row("Дата", shown, _d(doc_date), "ok")
            else:
                row("Дата", shown, _d(doc_date), "bad",
                    f"В названии указано {shown}, а документ составлен {_d(doc_date)}.")
        else:
            row("Дата", shown, "в документе не найдена", "info")

    # --- 4. сторона (для документов о замене стороны)
    if a.doc_key == "succession" and c.side:
        comparable += 1
        side, word = _doc_side(a)
        if side:
            if side == c.side:
                row("Сторона", SIDE_RU[c.side], ROLE_HUMAN.get(word, word), "ok")
            else:
                other = "должник остался прежним" if side == "creditor" else "взыскатель остался прежним"
                row("Сторона", SIDE_RU[c.side], ROLE_HUMAN.get(word, word), "bad",
                    f"В названии указано «на стороне {'должника' if c.side == 'debtor' else 'взыскателя'}», "
                    f"а в документе заменён {ROLE_HUMAN.get(word, word)}: {other}.")
        else:
            row("Сторона", SIDE_RU[c.side], "в документе не определена", "info")

    # --- 5. номера дела, ИП
    ids = a.ids or {}
    doc_cases = list(ids.get("cases", []))
    for pre, no, yr in c.cases:
        comparable += 1
        want = f"{pre}-{no}/{yr}"
        res = _compare_case((pre, no, yr), doc_cases)
        shown_doc = ", ".join(doc_cases[:2]) if doc_cases else "в документе не найден"
        if res == "ok":
            row("Номер дела", want, shown_doc, "ok")
        elif res == "typo":
            row("Номер дела", want, shown_doc, "typo",
                f"В названии номер дела {want}, в документе {doc_cases[0]}: похоже на опечатку (цифры).")
        elif doc_cases:
            row("Номер дела", want, shown_doc, "bad", f"В названии номер дела {want}, а в документе {shown_doc}.")
        else:
            row("Номер дела", want, shown_doc, "info")
    doc_ips = list(ids.get("ips", []))
    for ip in c.ips:
        comparable += 1
        res = _compare_plain(ip, doc_ips)
        shown_doc = ", ".join(doc_ips[:2]) if doc_ips else "в документе не найден"
        if res == "ok":
            row("Номер ИП", ip, shown_doc, "ok")
        elif res == "typo":
            row("Номер ИП", ip, shown_doc, "typo", f"В названии номер ИП {ip}, в документе {doc_ips[0]}: похоже на опечатку.")
        elif doc_ips:
            row("Номер ИП", ip, shown_doc, "bad", f"В названии номер ИП {ip}, а в документе {shown_doc}.")
        else:
            row("Номер ИП", ip, shown_doc, "info")
    pool = [x for k in ("contracts", "tokens", "writs", "cases", "ips") for x in ids.get(k, [])]
    for num in c.numbers:
        comparable += 1
        res = _compare_plain(num, pool)
        if res == "ok":
            row("Номер", num, "найден в документе", "ok")
        elif res == "typo":
            near = _nearest(num, pool)
            row("Номер", num, near, "typo", f"В названии номер {num}, в документе {near}: похоже на опечатку.")
        else:
            row("Номер", num, "в документе нет", "bad", f"Номера {num} из названия в документе нет.")

    # --- 6. фамилии и другие слова из названия
    stems = getattr(a, "stems", None) or frozenset()
    if stems:
        for w in c.words:
            lw = _fix_word(w).replace("ё", "е")
            variants = {v for v in (lw, lw[:-1], lw[:-2]) if len(v) >= 4}
            if variants & stems:
                comparable += 1
                row("Слово", w, "есть в документе", "ok")
                continue
            surname = bool(_SURNAMELIKE.search(lw)) and len(lw) >= 5
            close = difflib.get_close_matches(lw, list(stems), n=1, cutoff=0.8)
            if not surname and close and len(lw) >= 5 and (w[:1].isupper() and not c.upper):
                comparable += 1
                row("Фамилия / слово", w, close[0], "typo",
                    f"Слово «{w}» из названия в документе написано иначе («{close[0]}»): возможна опечатка.")
                continue
            if not surname:
                if len([r for r in nc.rows if r.what == "Слово"]) < 3 and (w[:1].isupper() or c.upper):
                    row("Слово", w, "в документе не найдено", "info")
                continue
            comparable += 1
            if close:
                row("Фамилия / слово", w, close[0], "typo", f"Слово «{w}» из названия в документе написано иначе («{close[0]}»): возможна опечатка.")
            else:
                row("Фамилия / слово", w, "в документе нет", "bad",
                    f"Слова «{w}» из названия в документе нет: возможно, название от другого документа или другого человека.")

    # --- итог
    generic = c.generic or (not cues and not c.dates and not c.cases and not c.ips and not c.numbers and
                            len(c.words) == 0)
    nc.suggest = suggest_name(filename, a, c)
    if generic:
        nc.verdict, nc.title = "generic", "Название не говорит, что это за документ"
        nc.problems.insert(0, "В названии нет вида документа, даты или номера.")
    elif bad:
        nc.verdict, nc.title = "wrong", "Название не сходится с содержимым"
    elif typo:
        nc.verdict, nc.title = "typo", "В названии возможна опечатка"
    elif comparable:
        nc.verdict, nc.title = "ok", "Название сходится с содержимым"
    else:
        nc.verdict, nc.title = "generic", "Название не говорит, что это за документ"
        nc.problems.insert(0, "В названии нет вида документа, даты или номера.")
    if nc.verdict == "ok":
        nc.suggest = ""
    return nc


def _compare_case(want: tuple[str, str, int], doc_cases: list[str]) -> str:
    for dc in doc_cases:
        m = re.match(r"^(А\d{2}|\d{1,2}[а-я]?)[-/](\d{1,6})[/-](\d{4}|\d{2})", dc.replace(" ", ""), re.I)
        if not m:
            continue
        pre, no, yr = m.group(1).upper(), m.group(2), _year(m.group(3))
        if (pre, no, yr) == want:
            return "ok"
    for dc in doc_cases:
        m = re.match(r"^(А\d{2}|\d{1,2}[а-я]?)[-/](\d{1,6})[/-](\d{4}|\d{2})", dc.replace(" ", ""), re.I)
        if m:
            k1 = f"{want[0]}{want[1]}{want[2]}"
            k2 = f"{m.group(1).upper()}{m.group(2)}{_year(m.group(3))}"
            if len(k1) == len(k2) and _lev(k1, k2) <= 1 or sorted(k1) == sorted(k2) and _lev(k1, k2) <= 2:
                return "typo"
    return "bad"


def _compare_plain(num: str, pool: list[str]) -> str:
    k = re.sub(r"(?:ИП|СД)$", "", _key(num))
    keys = [re.sub(r"(?:ИП|СД)$", "", _key(x)) for x in pool]
    if k in keys or any(k and (k in kk or kk in k) and min(len(k), len(kk)) >= 6 for kk in keys if kk):
        return "ok"
    if len(k) >= 5:
        for kk in keys:
            if len(kk) == len(k) and (_lev(k, kk) <= 1 or (sorted(k) == sorted(kk) and _lev(k, kk) <= 2)):
                return "typo"
    return "bad"


def _nearest(num: str, pool: list[str]) -> str:
    k = _key(num)
    best = min(pool, key=lambda x: _lev(k, _key(x))) if pool else ""
    return best


# ---------------------------------------------------------------- как назвать правильно

SHORT = {
    "order": "Судебный приказ", "order_cancel": "Определение об отмене судебного приказа",
    "decision": "Решение суда", "ruling": "Определение суда", "writ": "Исполнительный лист",
    "claim": "Исковое заявление", "order_application": "Заявление о выдаче судебного приказа",
    "fssp_start": "Постановление о возбуждении ИП", "fssp_end": "Постановление об окончании ИП",
    "fssp_refuse": "Постановление об отказе в возбуждении ИП", "fssp_stop": "Постановление о прекращении ИП",
    "fssp_levy": "Постановление об обращении взыскания", "fssp_limit": "Постановление об ограничениях",
    "fssp_act": "Акт о невозможности взыскания", "fssp_other": "Документ ФССП",
    "cession": "Договор уступки прав", "cession_notice": "Уведомление об уступке", "credit": "Кредитный договор",
    "questionnaire": "Анкета", "death_cert": "Свидетельство о смерти", "estate": "Наследственное дело, нотариус",
    "bankr": "Решение о банкротстве", "claim_letter": "Претензия", "calc": "Расчёт задолженности",
    "passport": "Паспорт",
}
ROLE_GEN = {"истец": "истца", "взыскатель": "взыскателя", "заявитель": "заявителя", "ответчик": "ответчика",
            "должник": "должника", "сторона": "стороны"}


def short_title(a: "Analysis") -> str:
    if a.doc_key == "succession":
        h = a.fields.get("Кого заменили")
        role = (h.note or "").split(",")[0].strip() if h is not None else "сторона"
        gen = ROLE_HUMAN_GEN.get(role, "стороны").split(" (")[0]
        return f"Определение о замене {gen} (правопреемство)"
    if a.doc_key == "ruling" and a.title:
        return f"Определение {a.title}"[:90]
    return SHORT.get(a.doc_key, a.doc_label or "Документ")


def suggest_name(filename: str, a: "Analysis", c: NameClaims | None = None) -> str:
    """Название по содержимому, в стиле исходного (ЗАГЛАВНЫЕ_С_ПОДЧЁРКИВАНИЯМИ или обычное)."""
    ext = os.path.splitext(filename)[1]
    c = c or parse_name(filename)
    parts = [short_title(a)]
    dd = _fv(a, "Дата документа")
    if isinstance(dd, date):
        if re.search(r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{2}(?!\d)", c.base):
            parts.append(f"от {dd.day}.{dd.month}.{dd.year % 100:02d}")
        else:
            parts.append(f"от {_d(dd)}")
    ids = a.ids or {}
    ip = _fv(a, "Номер ИП")
    case = _fv(a, "Номер дела")
    if ip and a.group == "ФССП":
        parts.append(str(ip) if str(ip).upper().endswith(("ИП", "СД")) else f"ИП {ip}")
    elif case:
        parts.append(f"дело {case}")
    name = " ".join(str(p) for p in parts)
    name = _BAD_CHARS.sub("-", name).strip(" .-")
    if "_" in c.base and " " not in c.base:
        name = name.replace(" ", "_")
    if c.upper:
        name = name.upper()
    return name[:140] + ext


# ---------------------------------------------------------------- расшифровка простыми словами

def _m(v) -> str:
    return fmt_money(v) if isinstance(v, Decimal) else str(v)


def _court(a: "Analysis") -> str:
    s = _fv(a, "Суд")
    return f" ({s})" if s else ""


def describe(a: "Analysis") -> str:
    """Одна-две фразы: что это за документ и что в нём главное. Только то, что найдено."""
    if a is None or a.doc_key == "unknown":
        return "Вид документа не определён. Откройте файл и проверьте вручную."
    k = a.doc_key
    dd = _fv(a, "Дата документа")
    when = f" от {_d(dd)}" if isinstance(dd, date) else ""
    case = _fv(a, "Номер дела")
    case_s = f" по делу № {case}" if case else ""
    fio = _fv(a, "ФИО")
    cred = _fv(a, "Взыскатель / кредитор")
    amount = _fv(a, "Сумма основная")
    fee = _fv(a, "Госпошлина")
    court = _court(a)
    out: list[str] = []

    if k == "succession":
        old, new = _fv(a, "Кого заменили"), _fv(a, "На кого заменили")
        side, word = _doc_side(a)
        who = ROLE_HUMAN_GEN.get(word, "стороны")
        out.append(f"Определение суда{court}{when}{case_s}: суд заменил {who}"
                   + (f" {old} на {new}" if old and new else f" — новая сторона: {new}" if new else "")
                   + " (правопреемство).")
        cd = _fv(a, "Дата уступки")
        if isinstance(cd, date):
            out.append(f"Основание — договор уступки права требования от {_d(cd)}.")
        if side == "creditor":
            out.append("Должник не менялся.")
        elif side == "debtor":
            out.append("Взыскатель не менялся.")
        if fio:
            out.append(f"Должник: {fio}.")
        if amount:
            out.append(f"Сумма иска: {_m(amount)} руб.")
    elif k == "order":
        out.append(f"Судебный приказ{when}{case_s}{court}: взыскание долга." + (f" Должник: {fio}." if fio else "") +
                   (f" Взыскатель: {cred}." if cred else "") + (f" Сумма: {_m(amount)} руб." if amount else ""))
        if fee:
            out.append(f"Госпошлина {_m(fee)} руб.")
    elif k == "order_cancel":
        out.append(f"Определение об отмене судебного приказа{when}{case_s}{court}. Приказ больше не действует, "
                   f"взыскателю разъяснено право подать иск.")
    elif k == "decision":
        out.append(f"Решение суда{court}{when}{case_s}: взыскание долга." + (f" Должник: {fio}." if fio else "") +
                   (f" Взыскатель: {cred}." if cred else "") + (f" Сумма: {_m(amount)} руб." if amount else ""))
        v = _fv(a, "Вступил в силу")
        if isinstance(v, date):
            out.append(f"Вступило в силу {_d(v)}.")
        if fee:
            out.append(f"Госпошлина {_m(fee)} руб.")
    elif k == "ruling":
        out.append(f"Определение суда{court}{when}{case_s}." + (f" {a.title[:1].upper() + a.title[1:]}." if a.title else ""))
    elif k == "writ":
        wr = _fv(a, "Исп. лист (серия, №)")
        out.append(f"Исполнительный лист{(' ' + str(wr)) if wr else ''}{case_s}{when}." +
                   (f" Должник: {fio}." if fio else "") + (f" Сумма: {_m(amount)} руб." if amount else ""))
    elif k.startswith("fssp"):
        ip = _fv(a, "Номер ИП")
        what = SHORT.get(k, "Постановление судебного пристава")
        out.append(f"{what}{(' № ' + str(ip)) if ip else ''}{when}." +
                   (f" Должник: {fio}." if fio else ""))
        if k == "fssp_start" and amount:
            out.append(f"Предмет исполнения: {_m(amount)} руб.")
        if k == "fssp_end":
            b = _fv(a, "Основание окончания")
            if b:
                out.append(f"Основание: {b}.")
            if amount:
                out.append(f"Остаток долга: {_m(amount)} руб.")
            sb = _fv(a, "Исполнительский сбор")
            if sb:
                out.append(f"Исполнительский сбор {_m(sb)} руб. — это не долг по договору, в сумму долга он не входит.")
    elif k in ("credit",):
        no = _fv(a, "Номер договора")
        out.append(f"Кредитный договор / договор займа{(' № ' + str(no)) if no else ''}{when}" +
                   (f", заёмщик {fio}" if fio else "") +
                   (f", сумма {_m(_fv(a, 'Сумма кредита / займа'))} руб." if _fv(a, "Сумма кредита / займа") else "."))
    elif k in ("cession", "cession_notice"):
        no = _fv(a, "Номер договора")
        ced = _fv(a, "Цедент")
        out.append(f"{'Договор уступки прав' if k == 'cession' else 'Уведомление об уступке права требования'}"
                   f"{(' № ' + str(no)) if no else ''}{when}" + (f", прежний кредитор (цедент) {ced}" if ced else "") + ".")
    elif k == "death_cert":
        ds = _fv(a, "Дата смерти")
        out.append("Свидетельство о смерти" + (f": {fio}" if fio else "") +
                   (f", дата смерти {_d(ds)}" if isinstance(ds, date) else "") + ".")
    elif k == "estate":
        ds = _fv(a, "Дата смерти")
        out.append("Ответ нотариуса / документ наследственного дела" + (f"{when}" if when else "") +
                   (f", дата смерти {_d(ds)}" if isinstance(ds, date) else "") + ".")
    elif k == "bankr":
        out.append(f"Судебный акт о банкротстве{when}{case_s}" + (f": {fio}" if fio else "") + ".")
    elif k == "claim_letter":
        out.append(f"Претензия / требование о погашении долга{when}" + (f" на {_m(amount)} руб." if amount else "."))
    elif k == "calc":
        out.append(f"Расчёт или справка о задолженности{when}" + (f" на {_m(amount)} руб." if amount else "."))
    else:
        out.append(f"{a.doc_label}{when}{case_s}.")
    return " ".join(x.strip() for x in out if x).replace(" .", ".").replace("..", ".")


# ---------------------------------------------------------------- описки внутри самого документа

TITLE_WORD = [
    (re.compile(r"^[\W_]*(ОПРЕДЕЛЕНИЕ|Определение)\b", re.M), "определение", "определил"),
    (re.compile(r"^[\W_]*(РЕШЕНИЕ|Решение|ЗАОЧНОЕ\s+РЕШЕНИЕ|Заочное\s+решение)\b", re.M), "решение", "решил"),
    (re.compile(r"^[\W_]*(ПОСТАНОВЛЕНИЕ|Постановление)\b", re.M), "постановление", "постановил"),
    (re.compile(r"^[\W_]*(СУДЕБНЫЙ\s+ПРИКАЗ|Судебный\s+приказ)\b", re.M), "приказ", "приказал"),
]
OPERATIVE_WORD = {"определение": "определил", "решение": "решил", "постановление": "постановил", "приказ": "приказал"}
OPERATIVE = {"определение": re.compile(r"(?i)\bопределил\w*\s*:?"), "решение": re.compile(r"(?i)\bрешил\w*\s*:?"),
             "постановление": re.compile(r"(?i)\bпостановил\w*\s*:?"), "приказ": re.compile(r"(?i)\bприказал\w*\s*:?")}


def _inn_ok(inn: str) -> bool:
    d = [int(x) for x in inn]
    if len(d) == 10:
        w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(a * b for a, b in zip(w, d[:9])) % 11 % 10 == d[9]
    if len(d) == 12:
        w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        return (sum(a * b for a, b in zip(w1, d[:10])) % 11 % 10 == d[10] and
                sum(a * b for a, b in zip(w2, d[:11])) % 11 % 10 == d[11])
    return True


def internal_issues(text: str, head: str, a: "Analysis", today: date | None = None) -> list[str]:
    """Возможные описки и противоречия внутри документа. Это подсказки проверяющему, а не приговор."""
    today = today or date.today()
    out: list[str] = []
    # заголовок и резолютивная часть
    for rx, title, _ in TITLE_WORD:
        if rx.search(head):
            for t2, orx in OPERATIVE.items():
                if t2 != title and orx.search(text) and not OPERATIVE[title].search(text):
                    out.append(f"В заголовке «{title}», а резолютивная часть начинается со слова «{OPERATIVE_WORD[t2]}» "
                               f"— возможна описка в заголовке. По содержанию: {a.doc_label.lower()}.")
            break
    dd = _fv(a, "Дата документа")
    if isinstance(dd, date):
        if dd > today:
            out.append(f"Дата документа {_d(dd)} позже сегодняшней — возможна описка в дате.")
        if dd.year < 1995:
            out.append(f"Дата документа {_d(dd)} слишком ранняя — возможна описка в годе.")
    case = _fv(a, "Номер дела")
    if case and isinstance(dd, date):
        m = re.search(r"/(\d{4})(?!\d)|[-/](\d{2})$", str(case))
        yr = int(m.group(1)) if m and m.group(1) else (2000 + int(m.group(2)) if m and m.group(2) else None)
        if yr and dd.year < yr:
            out.append(f"Номер дела {case} относится к {yr} году, а документ датирован {dd.year} — "
                       f"возможна описка в номере или в дате.")
    ip = _fv(a, "Номер ИП")
    if ip and isinstance(dd, date):
        m = re.match(r"\d+/(\d{2})/", str(ip))
        if m:
            yr = 2000 + int(m.group(1))
            if dd.year < yr or (a.doc_key == "fssp_start" and dd.year - yr > 1):
                out.append(f"Номер ИП {ip} содержит год {yr}, а постановление датировано {dd.year} — возможна описка.")
    eff = _fv(a, "Вступил в силу")
    if isinstance(eff, date) and isinstance(dd, date) and eff < dd and a.doc_key in ("decision", "order"):
        out.append(f"Дата вступления в силу {_d(eff)} раньше даты документа {_d(dd)} — возможна описка.")
    b = _fv(a, "Дата рождения")
    if isinstance(b, date) and (b.year < 1900 or b > today):
        out.append(f"Дата рождения {_d(b)} выглядит неверно — возможна описка.")
    if isinstance(b, date) and isinstance(dd, date) and (dd.year - b.year) < 18 and a.doc_key not in ("death_cert",):
        out.append(f"По датам должнику на момент документа меньше 18 лет ({_d(b)} и {_d(dd)}) — проверьте даты.")
    inn = _fv(a, "ИНН")
    if inn and re.fullmatch(r"\d{10}|\d{12}", str(inn)) and not _inn_ok(str(inn)):
        out.append(f"ИНН {inn} не проходит проверку контрольных цифр — возможна описка или ошибка распознавания.")
    return out


def word_stems(text: str) -> frozenset:
    """Первые пять букв каждого слова документа (для поиска фамилии из названия в любом падеже)."""
    out = set()
    for w in re.findall(r"[а-яё]{4,}", _lower(text)):
        out.add(w)
        if len(w) >= 5:
            out.add(w[:-1])
        if len(w) >= 6:
            out.add(w[:-2])
    return frozenset(out)


def date_index(text: str, a: "Analysis") -> list[tuple[str, str]]:
    """Все даты текста с подписью, чем они являются: «дата договора уступки», «дата вступления в силу»…"""
    labels = {}
    for name, lab in (("Дата уступки", "дата договора уступки"), ("Вступил в силу", "дата вступления в силу"),
                      ("Дата рождения", "дата рождения"), ("Дата смерти", "дата смерти")):
        v = _fv(a, name)
        if isinstance(v, date):
            labels[v.isoformat()] = lab
    out, seen = [], set()
    for d, _s, _e in find_dates(text):
        iso = d.isoformat()
        if iso not in seen:
            seen.add(iso)
            out.append((iso, labels.get(iso, "")))
    return out
