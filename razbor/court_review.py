"""Консервативные подсказки по судебным актам без ИИ.

Ни один найденный признак не подтверждает юридическую силу акта автоматически.
"""
import re
from decimal import Decimal
from .fields import Hit, CASE_LABELED, CASE_HASH, find_amounts, Doc, COURT_PATTERNS, COURT_TRIM
from .textutil import find_dates, fmt_money

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

CONTINUING = re.compile(r"(?i)(?:по|до)\s+(?:день|дату|момент|даты|дня|момента)?\s*фактическ\w+\s+(?:исполнени\w+|погашени\w+|возврат\w+)|(?:до|по\s+дату)\s+полного\s+погашения|по\s+дату\s+погашения")
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
    out.update(structured_awards(tail,pos))
    cases = []
    for rx in (CASE_LABELED, CASE_HASH):
        for m in rx.finditer(doc.text):
            v=m.group(1).strip()
            if v not in [x[0] for x in cases]:
                cases.append((v,m.start(1),m.end(1)))
    for i,(v,a,b) in enumerate(cases[:15],1):
        out[f"Упомянутое дело {i}"] = Hit(v,a,b,"ссылка в тексте, не подтверждённая связь")
    return out

# Разделяем только явные предписания из резолютивной части, не мотивировку.
RATE = re.compile(r"(\d{1,3}(?:[.,]\d{1,5})?)\s*%\s*(годовых|за\s+каждый\s+день(?:\s+просрочки)?|в\s+день)",re.I)
REFUSAL = re.compile(r"(?i)\b(?:в\s+остальной\s+части|отказать|оставить\s+без\s+удовлетворения)")
EXPENSE = re.compile(r"(?i)\bрасход\w*\s+(?:по\s+оплате\s+)?(?:услуг\w*\s+)?представител\w*|\b(?:государственн\w+\s+пошлин\w*|госпошлин\w*)")
def component(clause):
    c=re.sub(r"\s+"," ",clause.lower())
    if re.search(r"неустойк\w*\s+за\s+просрочк\w*\s+уплат\w*\s+процент",c):
        return "Неустойка по процентам"
    if re.search(r"неустойк\w*\s+за\s+просрочк\w*\s+уплат\w*\s+основн",c):
        return "Неустойка по основному долгу"
    if re.search(r"процент\w*\s+за\s+пользовани\w*\s+кредит",c):
        return "Проценты за пользование кредитом"
    if re.search(r"основн\w+\s+долг",c) and not re.search(r"процент|неустойк",c):
        return "Основной долг"
    if re.search(r"задолженност\w*\s+по\s+(?:кредитн\w*\s+договор\w*|договор\w*\s+займ\w*)",c) and not re.search(r"процент|неустойк|основн\w*\s+долг",c):
        return "Задолженность по договору"
    return None

def structured_awards(tail,pos):
    out={}
    # Не классифицируем в качестве взыскания суммы из актов отмены/изменения.
    if not re.search(r"(?i)\bвзыскать\b",tail) or re.search(r"(?i)\b(?:отменить|изменить)\b",tail):
        return out
    refusal=REFUSAL.search(tail)
    end=refusal.start() if refusal else len(tail)
    active=tail[:end]
    fixed=[]
    uncertain=False
    for m in re.finditer(r"[^;]+",active):
        part=m.group()
        shift=pos+m.start()
        # Судебные расходы часто стоят в конце последнего пункта после «а также».
        split=re.search(r"(?i)[,]\s*а\s+также\s+(?=\d|расход)",part)
        rest=part[split.end():] if split else ""
        body=part[:split.start()] if split else part
        label=component(body)
        amounts=find_amounts(body)
        cont=CONTINUING.search(body)
        if cont and INTEREST.search(body):
            n=1+sum(k.startswith("Начисление до исполнения") for k in out)
            rate=RATE.search(body)
            dates=find_dates(body)
            start=re.search(r"(?i)\b(?:период\s+)?с\s*(\d{1,2}[./]\d{1,2}[./]\d{4})",body)
            start_dates=find_dates(start.group(1)) if start else []
            basis=re.search(r"(?i)(?:на\s+)?остат\w*\s+основн\w+\s+долг\w*|сумм\w*\s+задолженност\w*\s+по\s+процент",body)
            desc=[label or "Вид начисления уточнить"]
            if rate:
                desc.append(rate.group(1).replace(".",",")+"% "+("годовых" if rate.group(2).lower()=="годовых" else "в день"))
            if start_dates:
                desc.append("с "+start_dates[0][0].strftime("%d.%m.%Y"))
            desc.append(re.sub(r"\s+"," ",cont.group()))
            if basis:
                desc.append(re.sub(r"\s+"," ",basis.group()))
            if amounts:
                if len(amounts)==1 and basis:
                    desc.append("сумма базы: "+fmt_money(amounts[0].value)+" ₽ (проверить)")
                else:
                    uncertain=True
            out["Начисление до исполнения" if n==1 else f"Начисление до исполнения {n}"]=Hit(" · ".join(desc),shift,shift+len(body),"условия и база требуют подтверждения; не включать в фиксированное взыскание")
            if not (label and rate and start_dates and basis):
                uncertain=True
        elif label and len(amounts)==1 and not re.search(r"%|в\s+день|до\s+погашения",body,re.I):
            a=amounts[0]
            fixed.append((label,a.value,shift+a.pos,shift+a.end))
        elif amounts:
            # Расходы могут стоять отдельным пунктом; метки справа или слева.
            if not EXPENSE.search(body):
                uncertain=True
            else:
                if not split:
                    rest=body
                else:
                    uncertain=True
        if rest:
            shift_rest=shift+split.end() if split else shift
            aa=find_amounts(rest)
            for j,a in enumerate(aa):
                before=rest[aa[j-1].end if j else 0:a.pos]
                after=rest[a.end:aa[j+1].pos if j+1<len(aa) else len(rest)]
                # Метка после числа ограничивается ближайшей запятой.
                next_label=EXPENSE.search(after.split(",")[0])
                prev_label=EXPENSE.search(before)
                hit=next_label or prev_label
                if not hit:
                    uncertain=True
                    continue
                typ="Госпошлина" if re.search(r"пошлин",hit.group(),re.I) else "Расходы представителя"
                fixed.append((typ,a.value,shift_rest+a.pos,shift_rest+a.end))
    for n,(label,value,a,b) in enumerate(fixed,1):
        out[f"Фиксированное взыскание {n}: {label}"]=Hit(value,a,b,"предварительно; сверить оригинал")
    # Сумма только распознанных фиксированных позиций, не юридический остаток.
    if fixed:
        total=sum((r[1] for r in fixed),Decimal(0))
        out["Итого распознанных фиксированных взысканий"]=Hit(total,fixed[0][2],fixed[-1][3],
            "неполный итог: есть неразобранные условия или суммы" if uncertain else "предварительный итог распознанных позиций; не остаток задолженности")
    return out

def additional_dates(doc,fields):
    for key,pattern in [
        ("Дата мотивированного решения",r"(?i)мотивированн\w+\s+решени\w+\s+изготовлен\w*\s+"),
    ]:
        m=re.search(pattern,doc.text)
        if m:
            dates=find_dates(doc.text[m.end():m.end()+60])
            if dates:
                d,a,b=dates[0]
                fields[key]=Hit(d,m.end()+a,m.end()+b,"отдельная дата")
    for p,txt in enumerate(doc.pages[:2]):
        if COVER.search(txt):
            dates=find_dates(txt[:1200])
            if dates:
                d,a,b=dates[0]
                fields["Дата сопроводительного письма"]=Hit(d,doc.starts[p]+a,doc.starts[p]+b,"не дата решения")
            break
