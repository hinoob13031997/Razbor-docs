"""«Что делать»: следующий шаг по договору.

Подсказки строятся из ситуации, сроков, данных CRM и документов. Это рабочая заготовка:
формулировки и порядок должны согласовать юристы. Решение всегда принимает юрист.
"""

from __future__ import annotations

from datetime import date

from .rules import d, fmt, months_between

# уровни: 0 — срочно (просрочено или ≤ urgent_days), 1 — скоро (≤ 90 дн.), 2 — плановое, 3 — данные/проверка
LEVEL_CLS = {0: "red", 1: "amb", 2: "blue", 3: "gray"}
LEVEL_LABEL = {0: "Срочно", 1: "Скоро", 2: "По плану", 3: "Проверить данные"}


def _days(iso: str | None, today: date) -> int | None:
    x = d(iso)
    return (x - today).days if x else None


def _level(days: int | None, urgent: int) -> int:
    if days is None:
        return 2
    if days <= urgent:
        return 0
    if days <= 90:
        return 1
    return 2


def _when(days: int | None, iso: str | None) -> str:
    if days is None:
        return ""
    if days < 0:
        return f"срок прошёл {fmt(iso)} ({-days} дн. назад)"
    if days == 0:
        return "срок сегодня"
    return f"до {fmt(iso)} (через {days} дн.)"


def next_actions(c: dict, facts: dict, deadlines: list[dict], fresh_level: str, p: dict, today: date,
                 checks: list[str] | None = None) -> list[dict]:
    """Список шагов по договору, самый важный первым.

    Каждый шаг: title, why (основание), when (срок словами), date, level, cls, tpl (шаблон), go (куда перейти).
    """
    urgent = p.get("urgent_days", 30)
    dl = {x["kind"]: x for x in deadlines}
    sit = c.get("sit") or "pre"
    out: list[dict] = []

    def add(title: str, why: str, level: int, iso: str | None = None, tpl: str | None = None,
            go: str | None = None) -> None:
        days = _days(iso, today)
        out.append({"title": title, "why": why, "when": _when(days, iso), "date": iso, "days": days,
                    "level": level, "cls": LEVEL_CLS[level], "label": LEVEL_LABEL[level], "tpl": tpl, "go": go})

    def by_deadline(kind: str, title: str, why: str, tpl: str | None = None, overdue_title: str | None = None,
                    overdue_why: str | None = None) -> bool:
        x = dl.get(kind)
        if not x:
            return False
        days = _days(x["date"], today)
        if days is not None and days < 0 and overdue_title:
            add(overdue_title, overdue_why or x["rule"], 0, x["date"], None)
        else:
            add(title, f"{why} Расчёт: {x['rule']}.", _level(days, urgent), x["date"], tpl)
        return True

    # --- по ситуации
    if sit == "death":
        if not (c.get("estate_no") or facts.get("estate_no")):
            add("Запросить у нотариуса сведения о наследственном деле",
                f"Должник умер {fmt(facts.get('death_date') or c.get('death_date'))}. Номер наследственного дела неизвестен.",
                1, None, "notary")
        by_deadline("estate", "Предъявить требование к наследникам",
                    "Кредитор предъявляет требование к принявшим наследство.", "claim",
                    "Срок по наследству прошёл: проверить, предъявлялось ли требование",
                    "Срок рассчитан условно; проверьте, что уже сделано, и решите о дальнейшей работе.")
    elif sit == "bankr":
        if dl.get("rtk"):
            by_deadline("rtk", "Включить требование в реестр кредиторов", "Должник в процедуре банкротства.", "rtk",
                        "Срок включения в реестр прошёл: проверить, включено ли требование",
                        "Если требование не заявлено, обсудите заявление о восстановлении срока или включение за реестр.")
        else:
            add("Уточнить дату судебного акта о банкротстве",
                "В CRM отмечено банкротство, но даты судебного акта нет: срок включения в реестр не посчитать.", 3)
    elif sit == "liq":
        add("Проверить ликвидацию организации",
            "Рабочий статус «ликвидация». Проверьте выписку ЕГРЮЛ и решите о требовании к ликвидатору или списании.", 1)
    elif sit == "ip_end":
        by_deadline("reoffer", "Повторно предъявить исполнительный лист", "ИП окончено, лист возвращён взыскателю.",
                    "ip_new", "Срок повторного предъявления листа истёк",
                    "Проверьте, не прерывался ли срок (частичное исполнение, предъявление). Иначе — восстановление срока или списание.")
    elif sit == "ip":
        if c.get("ip_stalled"):
            ref = d(c.get("last_pay_date")) if c.get("last_pay_date") and (c.get("last_pay_date") or "") >= (c.get("ip_start") or "") else d(c.get("ip_start"))
            m = months_between(ref, today) if ref else None
            add("ИП без движения: запросить сведения о ходе ИП",
                f"Поступлений нет {m} мес. Запросите сводку по ИП; при необходимости — ходатайство о розыске "
                f"имущества или жалоба на бездействие пристава.", 0 if (m or 0) >= p.get("stalled_months", 12) else 1)
        else:
            by_deadline("check", "Плановая проверка хода ИП", "Исполнительное производство идёт.", None)
    elif sit == "court":
        if dl.get("present"):
            by_deadline("present", "Предъявить исполнительный лист к исполнению", "Решение вступило в силу, ИП не возбуждено.",
                        "ip_new", "Срок предъявления листа истёк",
                        "Проверьте, предъявлялся ли лист раньше. Иначе — восстановление срока или списание.")
        elif not (facts.get("il_no") or c.get("il_no")):
            add("Получить исполнительный лист / узнать дату вступления в силу",
                "Есть решение суда, но нет даты вступления в силу и исполнительного листа.", 3)
        else:
            add("Предъявить исполнительный лист к исполнению",
                "Исполнительный лист есть, ИП не возбуждено. Дата вступления в силу неизвестна, срок не посчитан.", 1, None, "ip_new")
    elif sit == "expired":
        x = dl.get("limit")
        add("Срок давности истёк: проверить перерывы",
            f"Давность истекла {fmt(x['date']) if x else ''}. Проверьте частичные оплаты и признание долга. "
            "Если перерывов нет — решение о списании или уступке.", 0, x["date"] if x else None)
    else:  # pre
        if dl.get("limit"):
            by_deadline("limit", "Подать иск или заявление о судебном приказе",
                        "Досудебная стадия, решения нет.", "claim")
        else:
            add("Уточнить дату последнего платежа", "Без неё срок исковой давности не посчитать.", 3)

    # --- общее: данные и документы
    if checks:
        add("Проверить расхождения CRM и документов", "; ".join(checks[:2]), 3)
    if fresh_level == "none":
        add("Загрузить документы по договору", "Документы ещё не разбирались: ситуация определена только по CRM.", 3,
            go="batch")
    elif fresh_level == "stale":
        add("Обновить разбор документов", f"Последний разбор старше {p.get('stale_days', 30)} дн.", 3, go="batch")

    out.sort(key=lambda x: (x["level"], x["days"] if x["days"] is not None else 99999))
    return out
