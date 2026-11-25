"""Проверяемый локальный расчёт простых процентов.

Не устанавливает юридические основания и не распределяет платежи сам.
Все суммы погашения — уже подтверждённые суммы, отнесённые на базу начисления.
Дата начала и окончания включительны; погашение уменьшает базу со следующего дня.
Для иных условий судебного акта требуется отдельный режим.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from calendar import isleap
from collections import defaultdict

CENT = Decimal("0.01")

def _dec(value):
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("Ожидается конечное неотрицательное число")
    return d

def calculate(principal, start: date, end: date, *, fixed_rate=None,
              rates=None, verified_through=None, principal_payments=(), daily_rate=False):
    """Ставки — проценты годовых; rates — пары (дата действия, ставка).
    При переменных ставках verified_through задаёт проверенную дату покрытия.
    Платежи — пары (дата, сумма в погашение базы), не общий платёж.
    При daily_rate=True фиксированная ставка задаётся за день. Не учитывает капитализацию, ограничения неустоек, моратории и иные исключения.
    """
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
            rate = changes.get(day, rate)
            denominator = 1 if daily_rate else (366 if isleap(day.year) else 365)
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
                "segments": rows, "rate_unit": "day" if daily_rate else "year", "rounding": "Итог округляется один раз; строки показаны до копеек"}
