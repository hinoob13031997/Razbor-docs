"""Проверяемый локальный расчёт простых процентов.

Не устанавливает юридические основания и не распределяет платежи сам.
Все суммы погашения — уже подтверждённые суммы, отнесённые на базу начисления.
Дата начала и окончания включительны; порядок учёта погашений и годовой делитель задаются явно.
Для иных условий судебного акта требуется отдельный режим.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from calendar import isleap
from collections import defaultdict

CENT = Decimal("0.01")

def _dec(value):
    try:
        d = Decimal(str(value).replace(",","."))
    except InvalidOperation:
        raise ValueError("Некорректное число") from None
    if not d.is_finite() or d < 0 or d > Decimal("1000000000000000"):
        raise ValueError("Ожидается конечное неотрицательное число")
    return d

def calculate(principal, start: date, end: date, *, fixed_rate=None,
              rates=None, verified_through=None, principal_payments=(), daily_rate=False,
              day_count="actual", payment_timing="after"):
    """Ставки — проценты годовых; rates — пары (дата действия, ставка).
    При переменных ставках verified_through задаёт проверенную дату покрытия.
    Платежи — пары (дата, сумма в погашение базы), не общий платёж.
    При daily_rate=True фиксированная ставка задаётся за день. Не учитывает капитализацию, ограничения неустоек, моратории и иные исключения.
    """
    if day_count not in ("actual","365","366","360") or payment_timing not in ("before","after"):
        raise ValueError("Неизвестный способ подсчёта дней или учёта платежей")
    if daily_rate and day_count!="actual":
        raise ValueError("Дневная ставка не требует годового делителя")
    if (end-start).days>36525:
        raise ValueError("Период расчёта не должен превышать 100 лет")
    if daily_rate and fixed_rate is None:
        raise ValueError("Дневная ставка должна быть фиксированной")
    if end < start:
        raise ValueError("Конец периода раньше начала")
    if (fixed_rate is None) == (rates is None):
        raise ValueError("Укажите ровно один вид ставки")
    balance = _dec(principal)
    if fixed_rate is not None:
        changes = {start: _dec(fixed_rate)}
    else:
        if verified_through is None or end > verified_through:
            raise ValueError("Справочник ставок не подтверждён до даты расчёта")
        changes = {}
        for day, rate in rates:
            if day in changes:
                raise ValueError("Дубли дат ставок")
            changes[day] = _dec(rate)
        if not changes or min(changes) > start:
            raise ValueError("Нет ставки на начало периода")
    pay = defaultdict(Decimal)
    for day, amount in principal_payments:
        if not start <= day <= end:
            raise ValueError("Платёж вне периода: уточните начальную базу")
        pay[day] += _dec(amount)
    rate = changes[max(d for d in changes if d <= start)]
    segments = []
    total = Decimal(0)
    day = start
    with localcontext() as ctx:
        ctx.prec = 40
        while True:
            if payment_timing=="before":
                if pay[day]>balance:
                    raise ValueError("Погашение превышает остаток базы")
                balance-=pay[day]
            rate = changes.get(day, rate)
            denominator = 1 if daily_rate else ((366 if isleap(day.year) else 365) if day_count=="actual" else int(day_count))
            key = (balance, rate, denominator)
            daily = balance * rate / Decimal(100 * denominator)
            if segments and segments[-1]["key"] == key:
                segments[-1]["end"] = day
                segments[-1]["days"] += 1
                segments[-1]["raw"] += daily
            else:
                segments.append({"key": key, "start": day, "end": day,
                                 "days": 1, "raw": daily})
            total += daily
            if payment_timing=="after":
                if pay[day] > balance:
                    raise ValueError("Погашение превышает остаток базы")
                balance -= pay[day]
            if day == end:
                break
            day += timedelta(days=1)
        rows = []
        for s in segments:
            base, rate, denominator = s.pop("key")
            rows.append({"start": s["start"].isoformat(), "end": s["end"].isoformat(),
                         "days": s["days"], "base": str(base), "rate": str(rate),
                         "denominator": denominator, "interest": str(s["raw"].quantize(CENT, ROUND_HALF_UP))})
        return {"interest": str(total.quantize(CENT, ROUND_HALF_UP)),
                "remaining_principal": str(balance.quantize(CENT, ROUND_HALF_UP)),
                "segments": rows, "day_count":day_count,"payment_timing":payment_timing,"rate_unit": "day" if daily_rate else "year", "rounding": "Итог округляется один раз; строки показаны до копеек"}

def calculate_input(d):
    """Один расчёт из явно заданных параметров API; никаких выводов из текста."""
    start,end=date.fromisoformat(d["start"]),date.fromisoformat(d["end"])
    kw={"principal_payments":[(date.fromisoformat(x["date"]),x["amount"]) for x in d.get("payments",[])],
        "day_count":d.get("day_count","actual"),"payment_timing":d.get("payment_timing","after")}
    mode=d.get("mode","fixed")
    if mode=="variable":
        kw.update(rates=[(date.fromisoformat(x["date"]),x["rate"]) for x in d["rates"]],
                  verified_through=date.fromisoformat(d["verified_through"]))
    elif mode in ("fixed","daily",None):
        kw.update(fixed_rate=d["rate"],daily_rate=mode=="daily")
    else:
        raise ValueError("Неизвестный тип ставки")
    return calculate(d["principal"],start,end,**kw)

def calculate_many(components, *, confirmed=False):
    """Независимые начисления. Начальные базы не складываются.
    Сумма может включать пересекающиеся периоды только при ручном подтверждении
    разных оснований начисления; одинаковые вводы требуют раздельной проверки.
    """
    if not isinstance(components,list) or not 1<=len(components)<=20:
        raise ValueError("Укажите от 1 до 20 начислений")
    if confirmed is not True:
        raise ValueError("Подтвердите самостоятельность начислений и раздельный учёт платежей")
    names=set(); results=[]
    for c in components:
        if not isinstance(c,dict):
            raise ValueError("Каждое начисление должно содержать параметры")
        label=c.get("label","")
        if not isinstance(label,str) or not 1<=len(label.strip())<=150 or label.strip().casefold() in names:
            raise ValueError("Введите разные названия начислений")
        names.add(label.strip().casefold())
        result=calculate_input(c)
        results.append({"label":label.strip(),"start":c["start"],"end":c["end"],**result})
    return {"components":results,"interest":str(sum(Decimal(x["interest"]) for x in results).quantize(CENT)),
            "rounding":"Сумма отдельно округлённых начислений. Базы не складываются; остаток долга не определён."}
