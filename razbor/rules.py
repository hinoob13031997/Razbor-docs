"""Сроки, ситуация по договору и свежесть разбора.

Правила расчёта УСЛОВНЫЕ: они взяты как рабочая заготовка и должны быть согласованы
с юристами. Все параметры (годы, месяцы, дни) меняются в разделе «Настройки».
Программа показывает для каждого срока правило и основание, чтобы юрист мог перепроверить.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Callable

from .store import Store

DEFAULTS: dict[str, int] = {
    "fresh_days": 14,        # разбор не старше: считается актуальным
    "stale_days": 30,        # старше: лучше обновить
    "limit_years": 3,        # исковая давность
    "reoffer_years": 3,      # повторное предъявление исполнительного листа
    "rtk_person_months": 2,  # физлицо-банкрот: включение требования в реестр
    "rtk_org_days": 30,      # юрлицо в наблюдении: заявление о требовании
    "estate_years": 3,       # наследство (условно)
    "check_months": 6,       # плановая проверка хода ИП
    "stalled_months": 12,    # ИП «без движения»
    "urgent_days": 30,       # «срочные» сроки
}
PARAM_TITLES = {
    "fresh_days": "Разбор документов актуален, дней", "stale_days": "Разбор считается устаревшим после, дней",
    "limit_years": "Срок исковой давности, лет", "reoffer_years": "Повторное предъявление исп. листа, лет",
    "rtk_person_months": "Включение требования в реестр (физлицо), месяцев",
    "rtk_org_days": "Заявление о требовании в наблюдении (юрлицо), дней",
    "estate_years": "Требования по наследству (условно), лет", "check_months": "Плановая проверка хода ИП, месяцев",
    "stalled_months": "ИП «без движения» после, месяцев", "urgent_days": "Срочные сроки: ближайшие, дней",
}

SIT_LABELS = {
    "death": "Должник умер", "bankr": "Банкротство", "liq": "Организация ликвидирована",
    "ip_end": "ИП окончено", "ip": "Идёт исполнительное производство",
    "court": "Есть решение суда", "expired": "Срок давности истёк", "pre": "Досудебная работа",
    "closed": "Исполнено / погашено", "stop": "Работа остановлена", "ip_filed": "Подано заявление об ИП",
}
SIT_CLS = {"death": "red", "bankr": "red", "liq": "red", "ip_end": "amb", "ip": "blue", "court": "blue",
           "expired": "red", "pre": "gray",
           "closed": "teal", "stop": "gray", "ip_filed": "blue"}


def load_params(store: Store) -> dict[str, int]:
    p = dict(DEFAULTS)
    for k in DEFAULTS:
        v = store.get_setting("p_" + k)
        if v is not None:
            try:
                p[k] = max(0, int(v))
            except ValueError:
                pass
    return p


def d(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def add_months(x: date, n: int) -> date:
    m = x.month - 1 + n
    y = x.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(x.day, calendar.monthrange(y, m)[1]))


def add_years(x: date, n: int) -> date:
    return add_months(x, 12 * n)


def fmt(s: str | date | None) -> str:
    x = d(s) if isinstance(s, str) else s
    return x.strftime("%d.%m.%Y") if x else "—"


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def years_txt(n: int) -> str:
    return f"{n} {plural(n, 'год', 'года', 'лет')}"


def months_txt(n: int) -> str:
    return f"{n} {plural(n, 'месяц', 'месяца', 'месяцев')}"


def months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0))


def derive(c: dict, facts: dict[str, str], p: dict[str, int], today: date) -> dict:
    """Ситуация, сроки и данные по ИП для одного договора.

    Берёт данные из CRM (c) и из прочитанных документов (facts); данные документов важнее.
    """
    status = (c.get("status") or "").lower()
    ov = (c.get("status_ov") or "").lower()
    ipst = (c.get("ip_status") or "").lower().strip()
    reason = (c.get("ip_end_reason") or facts.get("ip_end_basis") or "").lower()
    death = d(facts.get("death_date")) or d(c.get("death_date"))
    is_dead = bool(death) or "умер" in status or "умер" in ov
    bankr_date = d(facts.get("bankr_date")) or d(c.get("bankrupt_date"))
    is_bankr = bool(c.get("bankrupt")) or bankr_date is not None or "банкрот" in status or "банкрот" in ov \
        or "банкрот" in reason
    kind = c.get("kind") or "Физлицо"
    is_liq = "ликвид" in status
    closed = "исполнено" in status or "исполнено" in ov or "погашен" in ipst or "(исполнено)" in reason
    stopped = "стоп" in status
    decision = d(facts.get("decision_date")) or d(c.get("decision_date"))
    effective = d(facts.get("effective_date"))
    cancel = d(facts.get("order_cancel_date"))
    if decision and cancel and facts.get("decision_kind") == "order" and cancel >= decision:
        decision, effective = None, None  # судебный приказ отменён
    sa_cancel = "отмена са" in reason or "недействительным ид" in reason
    # ИП по данным CRM: чужие («не на нас»), аннулированные и отказанные не учитываем
    foreign = "не на нас" in ipst
    crm_ok = not foreign and ipst not in ("аннулировано", "отказ")
    crm_start = d(c.get("ip_rise")) if crm_ok else None
    crm_end = d(c.get("ip_end_crm")) if crm_ok else None
    renew = d(c.get("ip_renewal")) if crm_ok else None
    if crm_end and renew and renew > crm_end:
        crm_end = None  # производство возобновлено
    ip_start = d(facts.get("ip_start_date")) or crm_start or d(c.get("fssp_date"))
    ip_end = d(facts.get("ip_end_date")) or crm_end
    if ip_end and ip_start and ip_end < ip_start:
        ip_end = None
    ended_nodate = crm_ok and not ip_end and ipst in ("окончено", "архив")
    filed = crm_ok and ipst == "подано" and not ip_start
    # повторное предъявление: лист возвращается при окончании по ст. 46; по ст. 47 — нет
    reoffer_ok = "ст. 47" not in reason
    last_pay = d(c.get("last_pay_date"))
    dls: list[dict] = []

    def add(kind_: str, name: str, rule: str, when: date, src: str) -> None:
        dls.append({"kind": kind_, "name": name, "rule": rule, "date": when.isoformat(), "src": src})

    # --- ситуация
    if is_dead:
        sit = "death"
    elif is_bankr:
        sit = "bankr"
    elif is_liq:
        sit = "liq"
    elif closed:
        sit = "closed"
    elif stopped:
        sit = "stop"
    elif ip_end or ended_nodate:
        sit = "ip_end"
    elif ip_start:
        sit = "ip"
    elif filed:
        sit = "ip_filed"
    elif decision:
        sit = "court"
    else:
        sit = "pre"

    # --- сроки
    # давность показываем только на досудебной стадии: при смерти, банкротстве, ликвидации работают свои сроки
    if not decision and not ip_start and last_pay and sit == "pre":
        base = last_pay + timedelta(days=1)
        when = add_years(base, p["limit_years"])
        add("limit", "Срок исковой давности",
            f"{years_txt(p['limit_years'])} с даты просрочки {fmt(base)}; нужно проверить перерывы",
            when, f"CRM: дата последнего платежа {fmt(last_pay)}")
        if when < today:
            sit = "expired"
    if sit == "ip_end" and ip_end and reoffer_ok:
        when = add_years(ip_end, p["reoffer_years"])
        add("reoffer", "Повторно предъявить исполнительный лист",
            f"{years_txt(p['reoffer_years'])} со дня возвращения листа (ИП окончено {fmt(ip_end)})",
            when, f"окончание ИП {fmt(ip_end)}" + (f", {c.get('ip_end_reason')}" if c.get("ip_end_reason") else ""))
    elif sit == "court" and effective:
        when = add_years(effective, p["reoffer_years"])
        add("present", "Предъявить исполнительный лист к исполнению",
            f"{years_txt(p['reoffer_years'])} со дня вступления решения в силу ({fmt(effective)}); условный расчёт",
            when, f"решение суда от {fmt(decision)}")
    if sit == "bankr" and bankr_date:
        if kind == "Юрлицо":
            add("rtk", "Заявить требование в процедуре банкротства",
                f"{p['rtk_org_days']} дн. с даты судебного акта {fmt(bankr_date)}; условный расчёт, уточнить дату публикации",
                bankr_date + timedelta(days=p["rtk_org_days"]), f"судебный акт о банкротстве от {fmt(bankr_date)}")
        else:
            add("rtk", "Включить требование в реестр кредиторов",
                f"{months_txt(p['rtk_person_months'])} с даты судебного акта {fmt(bankr_date)}; условный расчёт, уточнить дату публикации",
                add_months(bankr_date, p["rtk_person_months"]), f"судебный акт о банкротстве от {fmt(bankr_date)}")
    if sit == "death" and death:
        add("estate", "Предъявить требования по наследственному делу",
            f"Условный расчёт: {years_txt(p['estate_years'])} с даты смерти {fmt(death)}; правило уточнит юрист",
            add_years(death, p["estate_years"]), f"дата смерти {fmt(death)}")
    ip_info = {"state": "none", "start": None, "end": None, "months": None, "stalled": 0}
    if ip_start and not ip_end and not ended_nodate:
        stalled = 0
        if sit == "ip":
            base = d(c.get("fssp_actual")) or ip_start
            nxt = add_months(base, p["check_months"])
            k = 1
            while nxt < today and p["check_months"] > 0 and k < 600:  # ближайшая плановая дата, не прошедшая
                k += 1
                nxt = add_months(base, p["check_months"] * k)
            add("check", "Плановая проверка хода ИП", f"раз в {months_txt(p['check_months'])}",
                nxt,
                f"данные ФССП на {fmt(base)}" if d(c.get("fssp_actual")) else f"ИП возбуждено {fmt(ip_start)}")
            income = last_pay if last_pay and last_pay >= ip_start else None
            stalled = 1 if months_between(income or ip_start, today) >= p["stalled_months"] else 0
        ip_info = {"state": "active", "start": ip_start.isoformat(), "end": None,
                   "months": months_between(ip_start, today), "stalled": stalled}
    elif ip_start and ip_end:
        ip_info = {"state": "ended", "start": ip_start.isoformat(), "end": ip_end.isoformat(),
                   "months": months_between(ip_start, ip_end), "stalled": 0}
    dls.sort(key=lambda x: x["date"])
    flags = {"sa_cancel": sa_cancel, "ended_nodate": ended_nodate, "no_reoffer": bool(ip_end) and not reoffer_ok,
             "foreign_ip": foreign, "ip_status": c.get("ip_status") or "", "reason": c.get("ip_end_reason") or "",
             "status": c.get("status") or ""}
    return {"sit": sit, "deadlines": dls, "ip": ip_info, "flags": flags}


def recalc_contracts(store: Store, ids: list[int] | None = None, today: date | None = None,
                     progress: Callable[[int, str], None] | None = None, cancel=None) -> int:
    """Пересчитать ситуацию и сроки. Без списка id пересчитывает весь портфель."""
    today = today or date.today()
    p = load_params(store)
    facts_all = store.all_facts(ids)
    conn = store.conn()
    from .actions import next_actions

    cols = ("id, kind, status, bankrupt, bankrupt_date, death_date, decision_date, fssp_date, fssp_actual, "
            "last_pay_date, estate_no, il_no, last_doc_at, status_ov, ip_status, ip_rise, ip_renewal, ip_end_crm, "
            "ip_end_reason")
    if ids is None:
        cur = conn.execute(f"SELECT {cols} FROM contracts")
    else:
        cur = conn.execute(f"SELECT {cols} FROM contracts WHERE id IN ({','.join('?' * len(ids))})", ids) if ids else None
    if cur is None:
        return 0
    n = 0
    while True:
        rows = cur.fetchmany(20000)
        if not rows:
            break
        upd, dl_rows, done_ids = [], [], []
        for r in rows:
            c = dict(r)
            res = derive(c, facts_all.get(c["id"], {}), p, today)
            first = res["deadlines"][0] if res["deadlines"] else None
            ip = res["ip"]
            c2 = dict(c, sit=res["sit"], ip_stalled=ip["stalled"], ip_start=ip["start"])
            acts = next_actions(c2, facts_all.get(c["id"], {}), res["deadlines"],
                                fresh_level(c.get("last_doc_at"), today, p)["level"], p, today, flags=res["flags"])
            top = acts[0] if acts else None
            upd.append((res["sit"], first["date"] if first else None, first["name"] if first else None,
                        ip["state"], ip["start"], ip["end"], ip["stalled"], ip["months"],
                        top["title"] if top else None, top["level"] if top else None, c["id"]))
            done_ids.append(c["id"])
            for x in res["deadlines"]:
                dl_rows.append((c["id"], x["kind"], x["name"], x["rule"], x["date"], x["src"]))
        with store.write() as w:
            for i in range(0, len(done_ids), 500):
                part = done_ids[i:i + 500]
                w.execute(f"DELETE FROM deadlines WHERE contract_id IN ({','.join('?' * len(part))})", part)
            w.executemany("INSERT INTO deadlines(contract_id,kind,name,rule,date,src) VALUES(?,?,?,?,?,?)", dl_rows)
            w.executemany("UPDATE contracts SET sit=?, next_dl_date=?, next_dl_name=?, ip_state=?, ip_start=?, "
                          "ip_end=?, ip_stalled=?, ip_months=?, next_action=?, next_action_level=? WHERE id=?", upd)
        n += len(rows)
        if progress:
            progress(n, "Пересчитываю сроки")
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Остановлено пользователем")
    store.set_setting("recalc_day", today.isoformat())
    return n


def fresh_level(last_doc_at: str | None, today: date, p: dict[str, int]) -> dict:
    """Свежесть разбора документов по договору."""
    if not last_doc_at:
        return {"level": "none", "label": "Не разбирался", "cls": "gray", "days": None}
    x = d(last_doc_at)
    days = (today - x).days if x else 9999
    if days <= p["fresh_days"]:
        return {"level": "fresh", "label": "Актуально", "cls": "teal", "days": days}
    if days <= p["stale_days"]:
        return {"level": "aging", "label": "Проверьте свежесть", "cls": "amb", "days": days}
    return {"level": "stale", "label": "Устарело", "cls": "red", "days": days}


def ago_txt(days: int | None) -> str:
    if days is None:
        return ""
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 60:
        return f"{days} дн. назад"
    return f"{int(days // 30.44)} мес. назад"
