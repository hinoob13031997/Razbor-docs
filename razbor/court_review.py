"""Консервативные подсказки по судебным актам без ИИ.

Ни один найденный признак не подтверждает юридическую силу акта автоматически.
"""
import re
from decimal import Decimal
from .fields import Hit, CASE_LABELED, CASE_HASH, find_amounts, Doc, COURT_PATTERNS, COURT_TRIM, ORG_QUOTED, ORG_FULL
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
        out["Проверка: резолютивная часть"] = Hit("Граница резолютивной части не найдена; суммы из текста не подтверждены.",0,min(len(doc.text),200),"сверить оригинал")
        return out
    if len(marks)>1 or len(list(TITLE.finditer(doc.text)))>1:
        out["Проверка: несколько резолютивных частей"] = Hit("В файле несколько маркеров решения: возможны разные акты либо цитаты предыдущего решения. Разделите документы и проверьте каждый.",0,len(doc.text),"автоматический итог не сформирован")
        return out
    pos = marks[-1].end()
    tail = doc.text[pos:]
    # Не включаем последующие указания о сроке обжалования в резолютивную часть.
    stop = re.search(r"(?im)^[ \t]*(?:решение|определение|постановление)\s+(?:может\s+быть\s+обжалован|вступил\w*\s+в\s+законную\s+силу)|^[ \t]*мотивированн\w+\s+решени\w+\s+изготовлен", tail)
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
    out.update(cross_checks(doc,tail,pos,out))
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
RATE = re.compile(r"(\d{1,3}(?:[.,]\d{1,5})?)\s*(?:%|процент\w*)\s*(годовых|(?:за\s+)?каждый\s+день(?:\s+просрочки)?|в\s+день|ежедневно)",re.I)
REFUSAL = re.compile(r"(?i)\b(?:в\s+остальной\s+части|отказать|оставить\s+без\s+удовлетворения|не\s+подлеж\w*\s+взыскани\w*|не\s+взыскать)")
AWARD = re.compile(r"(?i)\bвзыскать\b")
TOTAL_WORD = re.compile(r"(?i)\b(?:итого|всего|общ(?:ая|ей|ую)\s+сумм\w*|в\s+общем\s+размере)\s*(?:к\s+взысканию\s*)?(?:составляет\s*)?[:–—-]?\s*$")
COMPLEX = re.compile(r"(?i)\b(?:солидарно|субсидиарно|в\s+долевом\s+порядке|в\s+равных\s+долях|с\s+каждого|с\s+каждой|встречн\w*\s+иск\w*|зач[её]т\w*|за\s+вычетом|в\s+пределах\s+стоимости|не\s+более|не\s+свыше|в\s+доход\s+бюджет\w*|рассрочк\w*|отсрочк\w*|обратить\s+взыскание\s+на|в\s+иностранн\w+\s+валют\w*|при\s+условии)")
MODIFY = re.compile(r"(?i)\b(?:отменить|изменить|исправить\s+описку|разъяснить|дополнить|поворот\s+исполнения)\b")
AMBIGUOUS_SUM = re.compile(r"(?i)\b(?:в\s+том\s+числе|из\s+них|включая|из\s+которых|либо|или|взамен)\b")
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
    if re.search(r"неустойк|штраф|\bпен[яией]",c):
        return "Неустойка (вид уточнить)"
    if re.search(r"процент",c) and not re.search(r"основн\w+\s+долг",c):
        return "Проценты (вид уточнить)"
    if re.search(r"моральн\w+\s+вред",c):
        return "Компенсация морального вреда"
    if not EXPENSE.search(c) and re.search(r"почтов\w+\s+расход|расход\w*\s+на\s+экспертиз|судебн\w+\s+расход",c):
        return "Судебные расходы (вид уточнить)"
    return None

def structured_awards(tail,pos):
    out={}
    # Не переводим отменённые, изменённые, условные или распределённые
    # предписания в единый итог. Сами суммы и источник остаются доступны.
    if not AWARD.search(tail):
        if MODIFY.search(tail) or COMPLEX.search(tail):
            out["Проверка: особые условия взыскания"]=Hit("Найдено особое предписание суда; проверьте влияние на предыдущие акты и исполнение.",pos,pos+len(tail),"ручная проверка")
        return out
    debtor_mentions=re.findall(r"(?i)\bвзыскать\s+с\s+([^;\n]{1,150}?)(?=\s+в\s+пользу)",tail)
    if MODIFY.search(tail) or COMPLEX.search(tail) or len({re.sub(r"\s+"," ",x.lower()).strip() for x in debtor_mentions})>1:
        out["Проверка: особые условия взыскания"] = Hit(
            "Изменение акта, солидарность, доли, зачёт, ограничение или иной особый порядок: единый итог не сформирован.",pos,pos+len(tail),"ручная проверка")
        return out
    # Начало каждого нового предложения с распорядительным глаголом,
    # точка с запятой и новая нумерованная строка — границы пунктов.
    boundaries = re.compile(r";|,(?=\s*(?:[|¦]\s*)?(?:процент\w*\s+за\s+пользовани\w*|неустойк\w*\s+за\s+просрочк\w*|пен[яией]\w*\s+за\s+просрочк\w*)\b)|\.(?=\s+(?:взыскать|отказать|в\s+остальной\s+части|в\s+удовлетворении|исковые\s+требования|решение\s+вступил))|\n(?=\s*\d+[.)]\s+)",re.I)
    spans=[]
    last=0
    for sep in boundaries.finditer(tail):
        spans.append((last,sep.start()))
        last=sep.end()
    spans.append((last,len(tail)))
    fixed=[]
    uncertain=False
    awarded=False
    explicit_totals=[]
    for lo,hi in spans:
        part=tail[lo:hi]
        shift=pos+lo
        # «В удовлетворении ... отказать» может стоять до взыскания.
        # Не считать суммы в отказных пунктах; при смешанном предписании
        # не угадывать, к какой части относятся суммы.
        if REFUSAL.search(part):
            if AWARD.search(part) and find_amounts(part):
                uncertain=True
            awarded=False
            continue
        if re.match(r"(?is)^\s*(?:\d+[.)]\s*)?(?:признать|обязать|установить|прекратить|оставить|возвратить)\b",part):
            if find_amounts(part):
                uncertain=True
            awarded=False
            continue
        if AWARD.search(part):
            awarded=True
        if not awarded:
            continue
        if AMBIGUOUS_SUM.search(part):
            uncertain=True
            continue
        amounts_all=find_amounts(part)
        totals_here=[a for a in amounts_all if TOTAL_WORD.search(part[max(0,a.pos-95):a.pos])]
        if totals_here:
            explicit_totals.extend((a.value,shift+a.pos,shift+a.end) for a in totals_here)
            if len(amounts_all)!=len(totals_here):
                uncertain=True
            continue
        # Судебные расходы часто стоят в конце последнего пункта после «а также».
        split=re.search(r"(?i)[,]\s*(?:[|¦]\s*)?а\s+также\s+(?=\d|расход)",part)
        rest=part[split.end():] if split else ""
        body=part[:split.start()] if split else part
        label=component(body)
        amounts=find_amounts(body)
        cont=CONTINUING.search(body)
        if cont and INTEREST.search(body):
            n=1+sum(k.startswith("Начисление до исполнения") for k in out)
            rate_matches=list(RATE.finditer(body))
            rate=rate_matches[0] if len(rate_matches)==1 else None
            dates=find_dates(body)
            start_dates=[(d,a,b) for d,a,b in dates if re.search(r'(?i)\b(?:с|начиная\s+с)\s*[«"“]?\s*$',body[max(0,a-30):a])]
            if len(start_dates)!=1:
                start_dates=[]
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
        elif label and EXPENSE.search(body) and amounts:
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
    if explicit_totals:
        for n,(v,a,b) in enumerate(explicit_totals,1):
            out[f"Указанный судом общий итог {n}"]=Hit(v,a,b,"не прибавлять к составляющим; проверить охват")
        if len(explicit_totals)!=1 or (fixed and explicit_totals[0][0]!=sum(r[1] for r in fixed)):
            uncertain=True
    if uncertain:
        out["Проверка: неполный разбор сумм"]=Hit("Есть неоднозначные суммы или условия; распознанные позиции не являются полным итогом взыскания.",pos,pos+len(tail),"ручная проверка")
    if fixed and not uncertain:
        total=sum((r[1] for r in fixed),Decimal(0))
        out["Итого распознанных фиксированных взысканий"]=Hit(total,fixed[0][2],fixed[-1][3],
            "предварительный итог распознанных позиций; не остаток задолженности")
    return out

def cross_checks(doc,tail,pos,fields):
    """Сравниваем только явно обозначенные роли и итоги, не историю требований."""
    out={}
    motive=doc.text[:pos]
    def orgs(text,role,offset):
        hits=[]
        for m in re.finditer(role,text,re.I):
            block=text[m.end():m.end()+220]
            # Не захватывать следующую организацию через всё предложение.
            org=ORG_QUOTED.match(block.lstrip()) or ORG_FULL.match(block.lstrip())
            if org:
                start=m.end()+len(block)-len(block.lstrip())+org.start()
                hits.append((org.group(),offset+start,offset+start+len(org.group())))
        return hits
    def norm(s):
        return re.sub(r"[^а-яa-z0-9]","",s.lower().replace("ё","е"))
    title=TITLE.search(motive)
    body_start=title.end() if title else 0
    parties=orgs(motive[body_start:],r"\bпо\s+иску\s+",body_start)
    recipients=orgs(tail,r"\bв\s+пользу\s+",pos)
    if parties:
        a=parties[0]
        out["Истец по тексту акта"]=Hit(*a,"роль по формулировке; проверить")
    for n,a in enumerate(recipients[:25],1):
        out[f"Получатель взыскания {n}"]=Hit(*a,"по резолютивной части; не подтверждает текущего кредитора")
    if parties and recipients and norm(parties[0][0]) not in {norm(a[0]) for a in recipients}:
        out["Проверка: различаются участники"]=Hit("Истец и получатель взыскания указаны по-разному: проверьте правопреемство, встречный иск или описку.",parties[0][1],recipients[-1][2],"не заменять кредитора автоматически")
    if parties and len({norm(a[0]) for a in parties})>1:
        out["Проверка: разные наименования истца"]=Hit("В тексте несколько разных наименований истца. Возможны цитата другого акта, переход права или описка; проверьте роли.",parties[0][1],parties[-1][2],"не подтверждает правопреемство")
    if len({norm(a[0]) for a in recipients})>1:
        out["Проверка: несколько получателей"]=Hit("Несколько получателей взыскания: распределите суммы по получателям.",pos,pos+len(tail),"не объединять автоматически")
        fields.pop("Итого распознанных фиксированных взысканий",None)
    successors=orgs(motive,r"\b(?:право\s+требовани\w*[^.;]{0,400}?)?перешл\w*\s+",0)
    if successors:
        for n,x in enumerate(successors[:15],1):
            out[f"Переход права по тексту {n}"]=Hit(*x,"упоминание перехода права; проверить всю цепочку уступок")
        if recipients and norm(successors[-1][0]) not in {norm(x[0]) for x in recipients}:
            out["Проверка: цепочка уступок"]=Hit("Назван получатель права требования, отличный от получателя взыскания. Проверьте последующие уступки и документы о правопреемстве.",successors[-1][1],recipients[-1][2],"различие не доказывает ошибку суда")
    # Сверяем явный итог задолженности в выводе суда (не цену иска) с
    # распознанными долговыми позициями, исключив расходы. Это сигнал,
    # а не доказательство ошибки: охват сумм может различаться.
    r=re.compile(r"(?i)\b(?:суд\s+приходит\s+к\s+выводу|суд\s+считает\s+необходимым|подлежит\s+взысканию)\b[^;\n]{0,230}?\b(?:в\s+общей\s+сумме|в\s+общем\s+размере|всего|итого)\s*[:–—-]?\s*")
    debts=[v for k,v in fields.items() if k.startswith("Фиксированное взыскание ") and re.search(r"долг|задолженность|проценты|неустойка",k,re.I)]
    if debts and "Итого распознанных фиксированных взысканий" in fields:
        total=sum(x.value for x in debts)
        for m in r.finditer(motive):
            fragment=motive[m.end():m.end()+60]
            aa=find_amounts(fragment)
            if aa and aa[0].pos<5 and aa[0].value!=total:
                a=aa[0]
                out["Проверка: возможное расхождение сумм"]=Hit(
                    f"В мотивировке указан итог {fmt_money(a.value)} ₽, распознанные долговые позиции резолютивной части — {fmt_money(total)} ₽. Сверьте состав и арифметику.",
                    m.end()+a.pos,m.end()+a.end,"возможное несовпадение; не исправлять решение самостоятельно")
                break
        # Сравниваем также явно указанную сумму задолженности за тот же
        # период, только если период одинаков у всех долговых позиций.
        periods=[]
        for v in debts:
            before=doc.text[max(pos,v.pos-220):v.pos]
            ds=find_dates(before)
            if len(ds)>=2 and re.search(r"(?i)за\s+период",before):
                periods.append(tuple(d for d,_,_ in ds[-2:]))
        if len(periods)==len(debts) and len(set(periods))==1:
            rx=re.compile(r"(?i)\bзадолженност\w*[^;]{0,220}?за\s+период[^;]{0,130}?составил\w*\s*")
            for m in rx.finditer(motive):
                dates=find_dates(m.group())
                if len(dates)<2 or tuple(d for d,_,_ in dates[-2:])!=periods[0]:
                    continue
                aa=find_amounts(motive[m.end():m.end()+60])
                if aa and aa[0].pos<5 and aa[0].value!=total:
                    a=aa[0]
                    out["Проверка: возможное расхождение сумм"]=Hit(
                        f"В мотивировке задолженность за тот же период указана {fmt_money(a.value)} ₽, долговые позиции резолютивной части — {fmt_money(total)} ₽. Проверьте охват и арифметику.",
                        m.end()+a.pos,m.end()+a.end,"возможное несовпадение; не исправлять самостоятельно")
                    break
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
