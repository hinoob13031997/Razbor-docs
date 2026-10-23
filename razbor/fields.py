"""Извлечение полей из текста документа.

Правила рассчитаны на типовые формулировки судебных актов, постановлений ФССП и
кредитных договоров. Результат всегда нужно сверять с исходным файлом: для этого у
каждого найденного значения запоминается страница и фрагмент текста.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .classify import G_CONTRACT, G_FSSP, HEAD_CHARS
from .textutil import find_dates, fmt_money, to_decimal


@dataclass
class Hit:
    value: object  # str | date | Decimal
    pos: int = 0
    end: int = 0
    note: str = ""  # пояснение: «по метке», «по максимуму»…


class Doc:
    """Текст документа с привязкой позиций к страницам."""

    def __init__(self, pages: list[str]):
        self.pages = pages
        self.starts: list[int] = []
        parts: list[str] = []
        pos = 0
        for p in pages:
            self.starts.append(pos)
            parts.append(p)
            pos += len(p) + 2
        self.text = "\n\n".join(parts)
        self.head = self.text[:HEAD_CHARS]

    def page_of(self, pos: int) -> int:
        return max(1, bisect.bisect_right(self.starts, pos))

    def snippet(self, pos: int, end: int, pad: int = 40) -> str:
        a = max(0, pos - pad)
        b = min(len(self.text), end + pad)
        s = self.text[a:b].replace("\n", " ")
        return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- ФИО

_TOK = r"(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?|[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?)"
_SP = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
_FIO = rf"({_TOK}{_SP}{_TOK}{_SP}{_TOK})"
_PATRONYMIC = re.compile(
    r"(?:[иы]ч[а-я]{0,3}|[ое]вн[аыеуо]й?|ичн[аыеуо]й?|оглы|кызы|гызы|угли|ичем|ичу|иче)$", re.I)
_NOT_NAME = re.compile(
    r"общество|акционерн|публичн|закрыт|открыт|банк|компани|микрофинанс|кредитн|потребительск|"
    r"управлен|российск|федераци|отдел|суд\b|судья|мирово|участок|район|област|республик|"
    r"город|ответственност|ограниченн|коллектор|агентств|отделени|службы|служба|страхов",
    re.I)


def looks_like_person(fio: str) -> bool:
    parts = re.split(r"\s+", fio.strip())
    if len(parts) != 3 or _NOT_NAME.search(fio):
        return False
    return bool(_PATRONYMIC.search(parts[2]))


def _fio_match(rx: re.Pattern, text: str, group: int = 1):
    for m in rx.finditer(text):
        fio = re.sub(r"\s+", " ", m.group(group)).strip()
        if looks_like_person(fio):
            return m, fio
    return None, None


_LABEL_PERSON = (
    r"(?i:должник\w*|заем[щц]ик\w*|заёмщик\w*|ответчик\w*|наследодател\w+|умерш\w+|"
    r"гражданин\w*|гражданк\w+|ф\.?\s*и\.?\s*о\.?(?:\s+должника)?)"
)
FIO_LABELED = re.compile(_LABEL_PERSON + r"\s*(?:\([^)\n]{0,40}\))?\s*[:\-–—]?\s*(?:гражданин\w*\s+)?" + _FIO)
FIO_DATIVE = re.compile(r"(?i:\bк|\bс|взыскать\s+с|с\s+должника)[ \t]+" + _FIO)
FIO_ANY = re.compile(_FIO)

BIRTH = re.compile(
    r"(?i:дата\s+рождения|д\.?\s*р\.?|родил(?:ся|ась)|г\.?\s*р\.?)[ \t:,\-]*(\d{1,2}[./]\d{1,2}[./]\d{4})")
BIRTH_AFTER = re.compile(r"(\d{1,2}[./]\d{1,2}[./]\d{4})[ \t]*(?i:г\.?\s*р\.?|года\s+рождения)")

# ---------------------------------------------------------------- номера

CASE_LABELED = re.compile(
    r"(?i:дел[оау]|иск|заявлени\w+)\s*(?:№|N|No\.?)\s*"
    r"((?:А|а)?\d{1,3}[а-я]?-\d{1,6}(?:/\d{2,4})?(?:-\d{1,3})?(?:\s*\(\d{1,2}\))?)")
CASE_ANY = re.compile(
    r"(?<![\w/])((?:А\d{2}|\d{1,2}[а-я]?)-\d{1,6}/\d{2,4}(?:-\d{1,3})?)(?![\w/])")
CASE_HASH = re.compile(r"(?:№|N)\s*((?:А\d{2}|\d{1,2}[а-я]?)-\d{1,6}/\d{2,4}(?:-\d{1,3})?)")
UID = re.compile(r"(?i:уид)[\s:№]*([0-9]{2}[A-ZА-Я]{2}\d{4}-\d{2}-\d{4}-\d{6}-\d{2})")
UID_ANY = re.compile(r"\b(\d{2}[A-Z]{2}\d{4}-\d{2}-\d{4}-\d{6}-\d{2})\b")

IP_LABELED = re.compile(
    r"(?i:исполнительн\w+\s+производств\w+|\bИП\b|\bдело\b)\s*(?:№|N|No\.?)?\s*[:\-]?\s*"
    r"(\d{1,8}/\d{2}/\d{2,6}(?:-(?:ИП|СД))?)")
IP_ANY = re.compile(r"(?<![\w/])(\d{3,8}/\d{2}/\d{5}-(?:ИП|СД))(?![\w/])")

WRIT = re.compile(r"(?:серия\s+)?\b(ФС|ВС|ЕС|АС|ВС)\s*(?:№|N|No\.?)?\s*(\d{6,9})\b", re.I)

INN_RX = re.compile(r"(?i)\bИНН\b[\s:№N.]*(\d{10}|\d{12})\b")
DEATH_DATE = re.compile(
    r"(?i)(?:дата\s+смерти|умер(?:ла)?|смерть\s+наступила)\W{0,25}"
    r"(\d{1,2}[./]\d{1,2}[./]\d{4}|[«\"“]?\d{1,2}[»\"”]?\s+[а-я]+\s+\d{4})")
TOKEN_RX = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-/_.]{3,34}")

CONTRACT_NO = re.compile(
    r"(?i:договор\w*(?:\s+(?:потребительского\s+)?(?:кредита|займа|микрозайма|цессии|уступки\s+прав"
    r"(?:\s*\(?требований\)?)?|уступки\s+права\s+требования))?|соглашени\w+)"
    r"\s*(?:№|N|No\.?)\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9\-/_.]{2,34})")

CASE_RESERVED = re.compile(r"^\d{1,2}[а-я]?-\d{1,6}/\d{2,4}", re.I)

# ---------------------------------------------------------------- стороны

COURT_PATTERNS = [
    re.compile(r"(?i:мирово\w+\s+судь\w+)[^\n]{0,120}"),
    re.compile(r"(?i:судебн\w+\s+участк\w+\s+№?\s*\d+)[^\n]{0,100}"),
    re.compile(r"[А-ЯЁ][А-Яа-яЁё\- \t]{2,50}(?i:районн\w+\s+суд)[^\n]{0,60}"),
    re.compile(r"[А-ЯЁ][А-Яа-яЁё\- \t]{2,50}(?i:городск\w+\s+суд)[^\n]{0,60}"),
    re.compile(r"(?i:арбитражн\w+\s+суд)[^\n]{0,90}"),
    re.compile(r"(?i:\w+ский\s+(?:областной|краевой|окружной)\s+суд)[^\n]{0,60}"),
]

COURT_TRIM = re.compile(
    r"(?i)\s*(?:,|;|\bв\s+составе|\bрассмотрев|\bпо\s+(?:делу|иску|заявлению)|\bпри\s+секретаре|"
    r"\bвыданн|\bустановил|\bрешил|\bопределил)")

_ORG_FORM = r"(?:ООО|АО|ПАО|НАО|ОАО|ЗАО|МКК|МФК|КПК|ИП|КБ|АКБ|ПКО)"
ORG_QUOTED = re.compile(_ORG_FORM + r"\s*[«\"“„]\s*[^»\"”]{2,90}[»\"”]")
ORG_FULL = re.compile(
    r"(?i:общество\s+с\s+ограниченной\s+ответственностью|акционерное\s+общество|публичное\s+акционерное\s+общество|"
    r"микрокредитная\s+компания|микрофинансовая\s+компания)\s*[«\"“„]\s*[^»\"”]{2,90}[»\"”]")
CREDITOR_LABEL = re.compile(
    r"(?i:взыскател\w+|кредитор\w*|займодавец\w*|истец\w*|заявител\w+|цессионари\w+|новый\s+кредитор)"
    r"[ \t]*(?:\([^)\n]{0,60}\))?[ \t]*[:\-–—]?[ \t]*([^\n]{3,200})")
CEDENT_LABEL = re.compile(r"(?i:цедент\w*|первоначальн\w+\s+кредитор\w*)[ \t]*(?:\([^)\n]{0,60}\))?[ \t]*[:\-–—]?[ \t]*([^\n]{3,200})")

# ---------------------------------------------------------------- суммы

_NUM = r"(\d{1,3}(?:(?:[ \u00a0]|\n)\d{3}(?!\d))+|\d+)(?:[.,](\d{1,2}))?"
MONEY = re.compile(
    r"(?<![\d.,/])" + _NUM + r"\s*(?i:руб(?:л[а-я]*)?\.?|р\.|₽|RUB)(?:\s*(\d{1,2})\s*(?i:коп(?:е[а-я]*)?\.?))?")
MONEY_LABELED = re.compile(
    r"(?i:сумм\w+\s+(?:основного\s+)?(?:долга|задолженности|взыскания|к\s+взысканию|кредита|займа)|"
    r"размер\w*\s+задолженности|остаток\s+(?:основного\s+)?долга|предмет\s+исполнения[^:\n]{0,80})"
    r"\s*[:\-–—]?\s*(?:составляет\s*)?(?<![\d.,/])" + _NUM + r"(?![\d]|[ \t]*%|/)")

_CATS = [
    ("fee", re.compile(r"пошлин", re.I)),
    ("penalty", re.compile(r"неустой|штраф|пени\b|пеня", re.I)),
    ("interest", re.compile(r"процент(?!н)", re.I)),
    ("credit", re.compile(r"кредит|заем\b|займ|лимит", re.I)),
    ("main", re.compile(r"задолжен|долг|основн|остат|предмет\s+исполнения|взыскать|взыскани|"
                        r"сумм[аы]\s+иска|цена\s+иска|размер\w*\s+задолженности", re.I)),
]


@dataclass
class Amount:
    value: Decimal
    pos: int
    end: int
    cat: str


def find_amounts(text: str) -> list[Amount]:
    out: list[Amount] = []
    spans: list[tuple[int, int]] = []
    for m in MONEY.finditer(text):
        v = to_decimal(m.group(1), m.group(2), m.group(3))
        if v is None or v <= 0:
            continue
        out.append(Amount(v, m.start(), m.end(), _category(text, m.start())))
        spans.append((m.start(), m.end()))
    for m in MONEY_LABELED.finditer(text):
        if any(a <= m.start() < b or a < m.end() <= b for a, b in spans):
            continue
        v = to_decimal(m.group(1), m.group(2))
        if v is None or v <= 0:
            continue
        label = m.group(0).lower()
        cat = "credit" if re.search(r"кредита|займа", label) else "main"
        out.append(Amount(v, m.start(), m.end(), cat))
    out.sort(key=lambda a: a.pos)
    return out


def _category(text: str, pos: int) -> str:
    ctx = text[max(0, pos - 140):pos]
    ctx = ctx.replace("\n", " ")
    best, best_at = "other", -1
    for cat, rx in _CATS:
        last = None
        for m in rx.finditer(ctx):
            last = m
        if last is not None and last.start() > best_at:
            best, best_at = cat, last.start()
    return best


LABELED_DATE = re.compile(
    r"(?i)дата\s+(?:вынесения|принятия|выдачи|составления|подписания)[^\d\n]{0,60}"
    r"(\d{1,2}[./]\d{1,2}[./]\d{4}|[«\"“]?\d{1,2}[»\"”]?\s+[а-я]+\s+\d{4})")

# ---------------------------------------------------------------- признаки

FLAG_RULES = [
    ("смерть / наследство", re.compile(r"умер(?:ла|ший|шего|шей)?\b|смерт\w+|скончал\w+|наследств\w+|наследник\w*|наследодател\w+", re.I)),
    ("банкротство", re.compile(r"банкрот\w*|несостоятельн\w+|финансов\w+\s+управляющ\w+|реструктуризац\w+\s+(?:долг|задолж)|реализаци\w+\s+имущества\s+гражданина", re.I)),
    ("отмена судебного приказа", re.compile(r"отмен\w+\s+судебного\s+приказа|отменить\s+судебный\s+приказ|судебный\s+приказ\s+отмен", re.I)),
    ("правопреемство / цессия", re.compile(r"правопреемств\w+|уступк\w+\s+(?:прав|требован)|цесси\w+|замен\w+\s+(?:стороны|взыскателя)", re.I)),
    ("невозможно взыскать", re.compile(r"невозможн\w+\s+(?:установить|взыскани)|невозможностью\s+взыскания|отсутствует\s+имущество|отсутствуют\s+(?:сведения|доходы)", re.I)),
    ("срок давности", re.compile(r"срок\w*\s+исковой\s+давности|пропущен\w*\s+срок", re.I)),
]

BASIS_46_47 = [
    re.compile(r"(?i:п(?:ункт\w*|п?\.)?)\s*(\d)\s*(?i:ч(?:асти|аст\w*|\.))\s*(\d)\s*(?i:ст(?:атьи|\.)?)\s*(46|47)\b"),
    re.compile(r"(?i:ст(?:атьи|\.)?)\s*(46|47)\s*(?i:ч(?:асти|аст\w*|\.))\s*(\d)\s*(?i:п(?:ункт\w*|п?\.)?)\s*(\d)"),
]
BASIS_LABEL = {
    ("46", "1", "3"): "невозможно установить местонахождение должника, имущества, счетов",
    ("46", "1", "4"): "у должника нет имущества, меры розыска безрезультатны",
    ("47", "1", "1"): "фактическое исполнение",
}


def _basis(text: str):
    for i, rx in enumerate(BASIS_46_47):
        m = rx.search(text)
        if m:
            if i == 0:
                p, ch, st = m.group(1), m.group(2), m.group(3)
            else:
                st, ch, p = m.group(1), m.group(2), m.group(3)
            return m, (st, ch, p)
    return None, None


# ---------------------------------------------------------------- извлечение


def _clean_org(raw: str) -> str:
    raw = raw.strip()
    m = ORG_QUOTED.search(raw) or ORG_FULL.search(raw)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip()
    raw = re.split(r"(?i:\bИНН\b|\bОГРН\b|\bадрес\b|\bдата\b|\bместонахождени)", raw)[0]
    raw = re.split(r"[,;]", raw)[0]
    return re.sub(r"\s+", " ", raw).strip(" .:-–—")[:120]


NO_CREDITOR = {"death_cert", "estate", "passport", "questionnaire", "bankr", "unknown"}
_PARTY_WORDS = re.compile(_ORG_FORM + r"|(?i:банк|общество|компания|кооператив|агентство|корпорация)")


def _is_party(s: str) -> bool:
    """Похоже ли значение на название организации или ФИО, а не на обрывок фразы."""
    if len(re.findall(r"[А-Яа-яЁёA-Za-z]", s)) < 4:
        return False
    return bool(_PARTY_WORDS.search(s)) or looks_like_person(s)


def _first_date(doc: Doc, lo: int = 0, hi: int | None = None, skip_birth: bool = True):
    text = doc.text[: hi if hi is not None else len(doc.text)]
    for d, s, e in find_dates(text):
        if s < lo:
            continue
        before = text[max(0, s - 45):s]
        after = text[e:e + 25]
        if skip_birth and (re.search(r"(?i)рожд|г\.?\s*р\.?\s*$|\bд\.?\s*р\.?\s*[:\-]?\s*$", before)
                           or re.match(r"(?i)\s*(?:г\.?\s*р\b|года\s+рожд)", after)):
            continue
        return Hit(d, s, e)
    return None


def extract(doc: Doc, doc_key: str, group: str) -> dict[str, Hit]:
    """Вернуть найденные поля: имя столбца -> Hit."""
    t = doc.text
    f: dict[str, Hit] = {}

    # дата документа: по метке, иначе первая дата в начале (кроме даты рождения)
    lm = LABELED_DATE.search(t)
    if lm:
        ds = find_dates(lm.group(1))
        if ds:
            f["Дата документа"] = Hit(ds[0][0], lm.start(1), lm.end(1), "по метке")
    if "Дата документа" not in f:
        h = _first_date(doc, 0, HEAD_CHARS + 600)
        if h:
            f["Дата документа"] = h

    # номер дела, УИД
    m = CASE_LABELED.search(t)
    if m:
        f["Номер дела"] = Hit(re.sub(r"\s+", " ", m.group(1)).strip(), m.start(1), m.end(1), "по метке")
    else:
        m = CASE_HASH.search(doc.head) or CASE_ANY.search(t)
        if m:
            f["Номер дела"] = Hit(m.group(1), m.start(1), m.end(1), "без метки")
    m = UID.search(t) or UID_ANY.search(t)
    if m:
        f["УИД"] = Hit(m.group(1), m.start(1), m.end(1))

    # суд
    for rx in COURT_PATTERNS:
        m = rx.search(doc.head)
        if m:
            val = re.sub(r"\s+", " ", m.group(0))
            tm = COURT_TRIM.search(val)
            if tm and tm.start() > 8:
                val = val[:tm.start()]
            f["Суд"] = Hit(val.strip(" ,.;")[:140], m.start(), m.end())
            break

    # исполнительное производство, исполнительный лист
    m = IP_LABELED.search(t) or IP_ANY.search(t)
    if m:
        f["Номер ИП"] = Hit(m.group(1), m.start(1), m.end(1))
    m = WRIT.search(t)
    if m and doc_key in ("writ", "fssp_start", "fssp_end", "fssp_refuse", "fssp_other",
                         "fssp_levy", "fssp_stop", "fssp_act", "ruling", "order_cancel"):
        f["Исп. лист (серия, №)"] = Hit(f"{m.group(1).upper()} {m.group(2)}", m.start(), m.end())

    # договор
    if group in (G_CONTRACT,) or doc_key in ("claim", "calc", "claim_letter", "decision", "order",
                                              "order_application", "ruling"):
        for m in CONTRACT_NO.finditer(t):
            val = m.group(1).strip(" .,;)")
            if CASE_RESERVED.match(val) or len(val) < 3:
                continue
            f["Номер договора"] = Hit(val, m.start(1), m.end(1))
            break

    # должник / заёмщик
    m, fio = _fio_match(FIO_LABELED, t)
    note = "по метке"
    if m is None:
        m, fio = _fio_match(FIO_DATIVE, t)
        note = "по предлогу"
    if m is None and doc_key in ("death_cert", "estate"):
        m, fio = _fio_match(FIO_ANY, t)
        note = "первое ФИО"
    if m is not None:
        f["ФИО"] = Hit(fio, m.start(), m.end(), note)
        # дата рождения рядом с ФИО, иначе первая в документе
        around = t[m.start(): m.end() + 200]
        b = BIRTH.search(around) or BIRTH_AFTER.search(around)
        if b:
            ds = find_dates(b.group(1))
            if ds:
                f["Дата рождения"] = Hit(ds[0][0], m.start() + b.start(1), m.start() + b.end(1))
    if "Дата рождения" not in f:
        b = BIRTH.search(t) or BIRTH_AFTER.search(t)
        if b:
            ds = find_dates(b.group(1))
            if ds:
                f["Дата рождения"] = Hit(ds[0][0], b.start(1), b.end(1))

    # взыскатель / кредитор (не для документов о смерти, анкет и т. п.)
    if doc_key not in NO_CREDITOR:
        if group != G_CONTRACT:
            for cm in CREDITOR_LABEL.finditer(t):
                org = _clean_org(cm.group(1))
                if not org:
                    continue
                if _is_party(org):
                    note = "по метке (физлицо)" if looks_like_person(org) else "по метке"
                    f["Взыскатель / кредитор"] = Hit(org, cm.start(1), cm.end(1), note)
                    break
        if "Взыскатель / кредитор" not in f:
            om = ORG_FULL.search(doc.head) or ORG_QUOTED.search(doc.head)
            if om:
                f["Взыскатель / кредитор"] = Hit(re.sub(r"\s+", " ", om.group(0)), om.start(), om.end(),
                                                 "первая организация в начале")
    if doc_key in ("cession", "cession_notice"):
        cd = CEDENT_LABEL.search(t)
        if cd:
            f["Цедент"] = Hit(_clean_org(cd.group(1)), cd.start(1), cd.end(1))

    # суммы
    amounts = find_amounts(t)
    main = [a for a in amounts if a.cat == "main" or (a.cat == "credit" and group != G_CONTRACT)]
    credit = [a for a in amounts if a.cat == "credit"]
    fees = [a for a in amounts if a.cat == "fee"]
    others = [a for a in amounts if a.cat in ("other", "interest", "penalty")]
    if main:
        a = max(main, key=lambda x: x.value)
        f["Сумма основная"] = Hit(a.value, a.pos, a.end, "по метке")
    elif others and group != G_CONTRACT:
        a = max(others, key=lambda x: x.value)
        f["Сумма основная"] = Hit(a.value, a.pos, a.end, "по максимуму, проверить")
    if fees:
        a = fees[0]
        f["Госпошлина"] = Hit(a.value, a.pos, a.end)
    if credit and group == G_CONTRACT:
        a = credit[0]
        f["Сумма кредита / займа"] = Hit(a.value, a.pos, a.end, "по метке")
    if amounts:
        uniq: list[str] = []
        for a in amounts:
            s = fmt_money(a.value)
            if s not in uniq:
                uniq.append(s)
            if len(uniq) >= 8:
                break
        f["Все суммы (руб.)"] = Hit("; ".join(uniq), amounts[0].pos, amounts[0].end)

    # ставка
    if group == G_CONTRACT:
        m = re.search(r"(\d{1,3}(?:[.,]\d{1,3})?)\s*%\s*(?i:годовых)", t)
        if m:
            f["Ставка, % годовых"] = Hit(Decimal(m.group(1).replace(",", ".")), m.start(1), m.end(1))

    # вступление в силу
    m = re.search(r"(?i)(?:дата\s+вступления\s+в\s+(?:законную\s+)?силу|вступил\w*\s+в\s+законную\s+силу)\s*[:\-]?\s*"
                  r"(\d{1,2}[./]\d{1,2}[./]\d{4}|[«\"“]?\d{1,2}[»\"”]?\s+[а-я]+\s+\d{4})", t)
    if m:
        ds = find_dates(m.group(1))
        if ds:
            f["Вступил в силу"] = Hit(ds[0][0], m.start(1), m.end(1))
    if "Вступил в силу" not in f:
        m = re.search(r"(?i)(?<!не\s)вступил\w*\s+в\s+законную\s+силу", t)
        if m:
            f["Вступил в силу"] = Hit("да (дата не найдена)", m.start(), m.end())

    # основание окончания / прекращения (ФССП)
    if group == G_FSSP:
        bm, parts = _basis(t)
        if bm and parts:
            text = f"ст. {parts[0]} ч. {parts[1]} п. {parts[2]}"
            lab = BASIS_LABEL.get(parts)
            if lab:
                text += f" ({lab})"
            f["Основание окончания"] = Hit(text, bm.start(), bm.end())

    m = INN_RX.search(t)
    if m:
        f["ИНН"] = Hit(m.group(1), m.start(1), m.end(1))
    if doc_key in ("death_cert", "estate"):
        dm = DEATH_DATE.search(t)
        if dm:
            ds = find_dates(dm.group(1))
            if ds:
                f["Дата смерти"] = Hit(ds[0][0], dm.start(1), dm.end(1))

    return f


def find_flags(doc: Doc, group: str, doc_key: str) -> list[str]:
    """Признаки для сортировки по воронке: смерть, банкротство, отмена и т. п."""
    if group == G_CONTRACT:
        return []  # в договорах эти слова есть в типовых условиях
    flags: list[str] = []
    for name, rx in FLAG_RULES:
        if rx.search(doc.text):
            if name == "отмена судебного приказа" and doc_key == "order_cancel":
                continue
            flags.append(name)
    return flags


def find_identifiers(doc: Doc) -> dict[str, list[str]]:
    """Все номера, по которым документ можно привязать к договору из портфеля."""
    t = doc.text
    ids: dict[str, list[str]] = {"contracts": [], "tokens": [], "inns": [], "cases": [], "ips": [], "writs": []}

    def add(key: str, val: str) -> None:
        val = val.strip(" .,;)")
        if val and val not in ids[key]:
            ids[key].append(val)

    for m in CONTRACT_NO.finditer(t):
        v = m.group(1).strip(" .,;)")
        if len(v) >= 3 and not CASE_RESERVED.match(v):
            add("contracts", v)
    for m in TOKEN_RX.finditer(t):
        v = m.group(0).strip(" .,;)-/_")
        if 4 <= len(v) <= 35 and re.search(r"[A-Za-zА-Яа-я]", v) and re.search(r"\d", v):
            add("tokens", v)
    for m in INN_RX.finditer(t):
        add("inns", m.group(1))
    for rx in (CASE_LABELED, CASE_HASH, CASE_ANY):
        for m in rx.finditer(t):
            add("cases", re.sub(r"\s+", " ", m.group(1)))
    for rx in (IP_LABELED, IP_ANY):
        for m in rx.finditer(t):
            add("ips", m.group(1))
    for m in WRIT.finditer(t):
        add("writs", f"{m.group(1).upper()} {m.group(2)}")
    return ids
