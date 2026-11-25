"""Консервативные подсказки по судебным актам без ИИ.

Ни один найденный признак не подтверждает юридическую силу акта автоматически.
"""
import re
from .fields import Hit, CASE_LABELED, CASE_HASH
from .textutil import find_dates

OPERATIVE = re.compile(r"(?im)^[ \t]*(?:суд(?:ебная коллегия)?\s+)?(?:решил[аи]?|определил[аи]?|постановил[аи]?)\s*[:.]?[ \t]*$")
CONTINUING = re.compile(r"(?i)(?:по|до)\s+(?:день|дату|момент|даты|дня|момента)?\s*фактическ\w+\s+(?:исполнени\w+|погашени\w+|возврат\w+)|до\s+полного\s+погашения")
INTEREST = re.compile(r"(?i)процент\w+|неустойк\w+|пен[яий]\w*")
RESULTS = [
    ("Отмена / новое рассмотрение", r"отменить[^;]{0,350}направить[^;]{0,100}новое\s+рассмотрение"),
    ("Отмена", r"отменить|отмен[её]н"),
    ("Изменение", r"изменить"),
    ("Оставление без изменения", r"оставить\s+без\s+изменения"),
    ("Частичное удовлетворение", r"удовлетворить\s+частично|удовлетворить\s+в\s+части"),
    ("Отказ", r"отказать|оставить\s+без\s+удовлетворения"),
    ("Взыскание", r"взыскать"),
]
def extract_review(doc):
    """Возвращает поля с позицией для стандартных цитат и страниц."""
    out = {}
    marks = list(OPERATIVE.finditer(doc.text))
    if not marks:
        return out
    pos = marks[-1].end()
    tail = doc.text[pos:]
    # Не включаем последующие указания о сроке обжалования в резолютивную часть.
    stop = re.search(r"(?im)^[ \t]*(?:решение|определение|постановление)\s+может\s+быть\s+обжалован", tail)
    if stop:
        tail = tail[:stop.start()]
    for label, pattern in RESULTS:
        m = re.search(pattern, tail, re.I)
        if m:
            out["Признак результата: " + label] = Hit(m.group(0), pos+m.start(), pos+m.end(),
                                                      "предварительно, сверить резолютивную часть")
    # Полная фраза о продолжающемся начислении — без догадки о базе и ставке.
    for m in CONTINUING.finditer(tail):
        lo, hi = max(0,m.start()-350), min(len(tail),m.end()+160)
        excerpt = tail[lo:hi]
        if INTEREST.search(excerpt):
            out["Начисление до исполнения"] = Hit(re.sub(r"\s+"," ",excerpt).strip(),
                                                  pos+lo,pos+hi,"условия проверить вручную")
            break
    cases = []
    for rx in (CASE_LABELED, CASE_HASH):
        for m in rx.finditer(doc.text):
            v=m.group(1).strip()
            if v not in [x[0] for x in cases]:
                cases.append((v,m.start(1),m.end(1)))
    for i,(v,a,b) in enumerate(cases[:15],1):
        out[f"Упомянутое дело {i}"] = Hit(v,a,b,"ссылка в тексте, не подтверждённая связь")
    return out
