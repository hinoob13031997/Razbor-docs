"""Консервативные подсказки по судебным актам без ИИ.

Ни один найденный признак не подтверждает юридическую силу акта автоматически.
"""
import re
from .fields import Hit, CASE_LABELED, CASE_HASH, find_amounts, Doc, COURT_PATTERNS, COURT_TRIM
from .textutil import find_dates

OPERATIVE = re.compile(r"(?im)(?:^[ \t]*(?:суд(?:ебная коллегия)?[ \t]+)?(?:решил[аи]?|определил[аи]?|постановил[аи]?)(?:[ \t]*:[ \t]*|[ \t]*$)|\bсуд[ \t]+(?:РЕШИЛ|ОПРЕДЕЛИЛ|ПОСТАНОВИЛ)[ \t]*:)")
TITLE = re.compile(r"(?im)^[ \t]*(?:заочное[ \t]+решение|решение|апелляционное[ \t]+определение|кассационное[ \t]+определение|апелляционное[ \t]+постановление|кассационное[ \t]+постановление)[ \t]*$")
COVER = re.compile(r"(?i)направляет\s+в\s+ваш\s+адрес|направляем\s+вам|направляет\s+копию|сопроводительн\w+\s+письм")
BODY = re.compile(r"(?i)\b(?:рассмотрев|установил[аи]?|при\s+секретаре|по\s+иску)\b")
def correct_decision_header(doc, fields):
    """Дата только из шапки судебного акта, а не из письма или обстоятельств."""
    m = TITLE.search(doc.text[:30000])
    if m:
        start = m.end()
        block = doc.text[start:start+850]
        stop = BODY.search(block)
        if stop:
            block = block[:stop.start()]
        dates = find_dates(block)
        if len({d for d,_,_ in dates}) == 1:
            d,a,b = dates[0]
            fields["Дата документа"] = Hit(d,start+a,start+b,"дата в шапке судебного акта")
        else:
            fields.pop("Дата документа",None)
        # Суд ищем вокруг заголовка на его странице, не в сопроводительном письме.
        page = doc.page_of(m.start())-1
        begin = doc.starts[page]
        head = doc.pages[page][:max(1800,m.end()-begin+850)]
        for rx in COURT_PATTERNS:
            for cm in rx.finditer(head):
                val=re.sub(r"\s+"," ",cm.group(0))
                if COVER.search(val):
                    continue
                cut=COURT_TRIM.search(val)
                if cut and cut.start()>8:
                    val=val[:cut.start()]
                fields["Суд"]=Hit(val.strip(" ,.;")[:140],begin+cm.start(),begin+cm.end(),"шапка судебного акта")
                return
        if "Суд" in fields and COVER.search(str(fields["Суд"].value)):
            fields.pop("Суд",None)
    elif COVER.search(doc.text[:2500]):
        fields.pop("Дата документа",None)
        fields.pop("Суд",None)

COMPONENTS = [
    ("расходы представителя",r"расход\w*\s+(?:по\s+оплате\s+)?(?:услуг\w*\s+)?представител\w*"),
    ("госпошлина",r"(?:государственн\w+\s+пошлин\w*|госпошлин\w*)"),
    ("неустойка",r"неустойк\w*|штраф\w*|пен[яией]\w*"),
    ("проценты",r"процент\w*"),
    ("основной долг",r"основн\w+\s+долг\w*"),
]
def amount_label(text,a,previous_end):
    # Сначала ближайшая метка слева; если она отсутствует — явная метка справа.
    before=text[max(previous_end,a.pos-160):a.pos]
    after=text[a.end:a.end+90]
    found=[]
    for label,rx in COMPONENTS:
        for m in re.finditer(rx,before,re.I):
            found.append((m.start(),label))
    if found:
        return max(found)[1]
    for label,rx in COMPONENTS:
        if re.match(r"\s*(?:[-–—,:]\s*)?(?:"+rx+r")",after,re.I):
            return label
    return "назначение не определено"

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
    # Суммы в резолютивной части — отдельные позиции, не итог долга.
    previous=0
    for n,a in enumerate(find_amounts(tail)[:50],1):
        label=amount_label(tail,a,previous)
        previous=a.end
        out[f"Сумма в резолютивной части {n} ({label})"] = Hit(
            a.value,pos+a.pos,pos+a.end,"сверить назначение и роль суммы: взыскание, база будущих начислений или отказ")
    keys=set(out)
    if "Признак результата: Взыскание" in keys and "Признак результата: Отказ" in keys:
        out["Результат рассмотрения"]=Hit("Взыскание и отказ в части требований — проверить объём",pos,pos+min(300,len(tail)),"предварительный вывод")
    elif "Признак результата: Частичное удовлетворение" in keys:
        out["Результат рассмотрения"]=Hit("Частичное удовлетворение",pos,pos+min(300,len(tail)),"предварительный вывод")
    elif "Признак результата: Взыскание" in keys:
        out["Результат рассмотрения"]=Hit("В резолютивной части есть взыскание",pos,pos+min(300,len(tail)),"предварительный вывод")
    elif "Признак результата: Отказ" in keys:
        out["Результат рассмотрения"]=Hit("В резолютивной части есть отказ",pos,pos+min(300,len(tail)),"предварительный вывод")
    # Сохраняем весь фрагмент, а не только короткое окружение найденной суммы.
    out["Резолютивная часть"]=Hit(tail.strip()[:14000],pos,pos+min(len(tail),14000),"проверить по оригиналу")
    # Полная фраза о продолжающемся начислении — без догадки о базе и ставке.
    for m in CONTINUING.finditer(tail):
        lo, hi = max(0,m.start()-350), min(len(tail),m.end()+160)
        excerpt = tail[lo:hi]
        if INTEREST.search(excerpt):
            out["Начисление до исполнения"] = Hit(re.sub(r"\s+"," ",excerpt).strip(),
                                                  pos+lo,pos+hi,"условия проверить вручную")
            # Не ограничиваемся первым начислением: сохраняем остальные условия отдельно.
            extra = list(CONTINUING.finditer(tail))
            for j, x in enumerate(extra, 1):
                if x.start() == m.start():
                    continue
                aa, bb = max(0,x.start()-350), min(len(tail),x.end()+160)
                if INTEREST.search(tail[aa:bb]):
                    out[f"Начисление до исполнения {j}"] = Hit(re.sub(r"\s+"," ",tail[aa:bb]).strip(),pos+aa,pos+bb,"условия проверить вручную")
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
