"""Шаблоны документов: данные подставляются из карточки договора.

Тексты шаблонов условные: перед работой их должны согласовать юристы. Что нашлось в карточке,
подставляется; чего нет, показывается жёлтым полем [название] и заполняется вручную.
"""

from __future__ import annotations

import os
import re
from datetime import date

from . import docx_out
from .rules import fmt
from .store import Store

K = tuple  # (ключ, подпись поля)

TEMPLATES = [
    {"id": "ip_new", "name": "Заявление о возбуждении исполнительного производства",
     "sub": "Предъявление или повторное предъявление исполнительного листа",
     "head": [("В ", ), ("osp", "отдел судебных приставов"), "\n\nВзыскатель: ", ("org", "наша организация"),
              "\n\nДолжник: ", ("debtor", "ФИО"), ", ", ("idLine", "дата рождения"), "\nАдрес: ",
              ("address", "адрес должника")],
     "title": "ЗАЯВЛЕНИЕ о возбуждении исполнительного производства",
     "body": ["Решением ", ("decCourt", "суд, вынесший решение"), " от ", ("decDate", "дата решения"), " по делу № ",
              ("caseNo", "номер дела"), " с должника в пользу взыскателя взыскана задолженность по договору № ",
              ("contractNo", "номер договора"), " от ", ("contractDate", "дата договора"), " в размере ",
              ("amount", "сумма"), " руб. Решение вступило в законную силу ", ("effDate", "дата вступления в силу"),
              ".\n\nВыдан исполнительный лист ", ("ilNo", "серия и номер листа"), " от ", ("ilDate", "дата выдачи листа"),
              ". Ранее возбуждённое исполнительное производство № ", ("ipNo", "номер ИП"), " окончено ",
              ("ipEnd", "дата окончания ИП"),
              ", исполнительный лист возвращён взыскателю.\n\nНа основании изложенного, руководствуясь Федеральным "
              "законом «Об исполнительном производстве»,\n\nПРОШУ:\n\nвозбудить исполнительное производство на "
              "основании прилагаемого исполнительного листа.\n\nПриложение: исполнительный лист; постановление об "
              "окончании исполнительного производства; документы, подтверждающие переход права требования."]},
    {"id": "rtk", "name": "Заявление о включении требования в реестр кредиторов", "sub": "Банкротство должника",
     "head": [("court", "арбитражный суд"), "\n\nКредитор: ", ("org", "наша организация"), "\n\nДолжник: ",
              ("debtor", "должник"), ", ", ("idLine", "дата рождения или ИНН"), "\nДело № ", ("bankrCase", "номер дела"),
              "\nУправляющий: ", ("manager", "управляющий")],
     "title": "ЗАЯВЛЕНИЕ о включении требования в реестр требований кредиторов",
     "body": ["По делу № ", ("bankrCase", "номер дела"),
              " в отношении должника введена процедура банкротства, судебный акт от ", ("pub", "дата судебного акта"),
              ".\n\nТребование основано на договоре № ", ("contractNo", "номер договора"), " от ",
              ("contractDate", "дата договора"), " и составляет ", ("amount", "сумма"),
              " руб. основного долга и процентов.\n\nНа основании Федерального закона «О несостоятельности "
              "(банкротстве)»,\n\nПРОШУ:\n\nвключить требование ", ("org", "наша организация"), " в размере ",
              ("amount", "сумма"), " руб. в реестр требований кредиторов должника.\n\nПриложение: расчёт "
              "задолженности; договор; договор уступки права требования; документы, подтверждающие переход права."]},
    {"id": "claim", "name": "Исковое заявление о взыскании задолженности", "sub": "Кредитный договор или договор займа",
     "head": [("court", "суд"), "\n\nИстец: ", ("org", "наша организация"), "\n\nОтветчик: ", ("debtor", "ответчик"),
              ", ", ("idLine", "дата рождения или ИНН"), "\nАдрес: ", ("address", "адрес ответчика"),
              "\n\nЦена иска: ", ("amount", "сумма"), " руб.\nГоспошлина: ", ("fee", "госпошлина"), " руб."],
     "title": "ИСКОВОЕ ЗАЯВЛЕНИЕ о взыскании задолженности",
     "body": ["По договору № ", ("contractNo", "номер договора"), " от ", ("contractDate", "дата договора"),
              " ответчику предоставлены денежные средства. Обязательства по возврату ответчик не исполняет.\n\n"
              "Право требования перешло к истцу по договору уступки прав (требований) № ", ("cessNo", "номер договора цессии"),
              " от ", ("cessDate", "дата цессии"), ". Размер задолженности составляет ", ("amount", "сумма"),
              " руб.\n\nНа основании изложенного,\n\nПРОШУ:\n\nвзыскать с ответчика задолженность в размере ",
              ("amount", "сумма"), " руб. и расходы по уплате государственной пошлины.\n\nПриложение: расчёт "
              "задолженности; договор; договор уступки прав; уведомление должника об уступке."]},
    {"id": "notary", "name": "Запрос нотариусу о наследственном деле", "sub": "Должник умер",
     "head": ["Нотариусу ", ("notary", "нотариус"), "\n\nОт: ", ("org", "наша организация")],
     "title": "ЗАПРОС о наследственном деле",
     "body": [("org", "наша организация"), " является кредитором по договору № ", ("contractNo", "номер договора"), " от ",
              ("contractDate", "дата договора"), ", заключённому с ", ("debtor", "должник"), ", умершим ",
              ("deathDate", "дата смерти"), ". Размер задолженности: ", ("amount", "сумма"),
              " руб.\n\nПросим сообщить, заведено ли наследственное дело (№ ", ("estateNo", "номер наследственного дела"),
              ") и кто из наследников принял наследство, для предъявления требований кредитора.\n\nПриложение: копия "
              "свидетельства о смерти; документы, подтверждающие право требования."]},
]
BY_ID = {t["id"]: t for t in TEMPLATES}
RECOMMEND = {"ip_end": "ip_new", "court": "ip_new", "bankr": "rtk", "death": "notary", "pre": "claim",
             "expired": "claim", "liq": "claim"}


def money(v) -> str | None:
    if v is None:
        return None
    return f"{float(v):,.2f}".replace(",", " ").replace(".", ",")


def collect_data(store: Store, cid: int) -> dict[str, str]:
    """Значения для подстановки: из CRM, из прочитанных документов и введённые вручную."""
    c = store.contract(cid) or {}
    facts = store.facts_for(cid)
    docs = store.documents_for(cid)
    d: dict[str, str | None] = {}

    def doc_field(keys: tuple[str, ...], name: str) -> str | None:
        for x in docs:
            if x["doc_key"] in keys:
                for f in x["fields"]:
                    if f["name"] == name and f["value"]:
                        return f["value"]
        return None

    kind = c.get("kind")
    d["org"] = store.get_setting("org_name") or None
    d["debtor"] = c.get("fio")
    if kind == "Физлицо":
        d["idLine"] = f"дата рождения {fmt(c['birth'])}" if c.get("birth") else None
    else:
        d["idLine"] = f"ИНН {c['inn']}" if c.get("inn") else None
    d["address"] = c.get("address")
    d["contractNo"] = c.get("number")
    d["contractDate"] = fmt(c["contract_date"]) if c.get("contract_date") else None
    amount = c.get("principal_granted") or None
    if amount is None and facts.get("amount_granted"):
        try:
            amount = float(facts["amount_granted"])
        except ValueError:
            amount = None
    if amount is None:
        amount = c.get("principal_left") or c.get("debt")
    notes: list[str] = []
    if amount is None and c.get("debt_total"):
        if c.get("fee"):  # остаток с госпошлиной минус госпошлина
            amount = c["debt_total"] - c["fee"]
            notes.append(f"Сумма долга посчитана как остаток с госпошлиной ({money(c['debt_total'])} ₽) минус "
                         f"госпошлина ({money(c['fee'])} ₽).")
        else:
            notes.append(f"В CRM есть только остаток задолженности с госпошлиной ({money(c['debt_total'])} ₽). "
                         "Госпошлина отдельно не указана, поэтому сумму долга впишите вручную.")
    d["amount"] = money(amount)
    d["fee"] = money(c.get("fee"))
    d["caseNo"] = facts.get("case_no") or c.get("case_no")
    dec = facts.get("decision_date") or c.get("decision_date")
    d["decDate"] = fmt(dec) if dec else None
    d["effDate"] = fmt(facts["effective_date"]) if facts.get("effective_date") else None
    d["decCourt"] = doc_field(("decision", "order"), "Суд") or c.get("court")
    d["court"] = d["decCourt"] or c.get("court")
    d["ilNo"] = facts.get("il_no") or c.get("il_no")
    d["ilDate"] = fmt(c["il_date"]) if c.get("il_date") else None
    d["ipNo"] = facts.get("ip_no")
    d["ipEnd"] = fmt(facts["ip_end_date"]) if facts.get("ip_end_date") else None
    d["osp"] = c.get("fssp_otdel")
    d["cessNo"], d["cessDate"] = c.get("cession_no"), (fmt(c["cession_date"]) if c.get("cession_date") else None)
    bd = facts.get("bankr_date") or c.get("bankrupt_date")
    d["pub"] = fmt(bd) if bd else None
    d["bankrCase"] = facts.get("bankr_case") or c.get("bankrupt_case") or c.get("case_no")
    death = facts.get("death_date") or c.get("death_date")
    d["deathDate"] = fmt(death) if death else None
    d["estateNo"], d["notary"] = c.get("estate_no"), c.get("notary")
    d["manager"] = None
    out = {k: v for k, v in d.items() if v}
    out.update(store.manual_for(cid))
    if "amount" in store.manual_for(cid):
        notes = []
    out["_notes"] = notes  # type: ignore[assignment]
    return out


def build(items: list, data: dict[str, str]) -> tuple[list[dict], list[tuple[str, str]]]:
    toks: list[dict] = []
    missing: list[tuple[str, str]] = []
    for it in items:
        if isinstance(it, str):
            toks.append({"text": it, "filled": False, "missing": False, "key": ""})
        elif len(it) == 1:
            toks.append({"text": it[0], "filled": False, "missing": False, "key": ""})
        else:
            k, label = it
            v = data.get(k)
            if v:
                toks.append({"text": v, "filled": True, "missing": False, "key": k})
            else:
                toks.append({"text": f"[{label}]", "filled": False, "missing": True, "key": k})
                if (k, label) not in missing:
                    missing.append((k, label))
    return toks, missing


def render(store: Store, cid: int, tpl_id: str) -> dict:
    t = BY_ID[tpl_id]
    data = collect_data(store, cid)
    head, m1 = build(t["head"], data)
    body, m2 = build(t["body"], data)
    miss: list[tuple[str, str]] = []
    for x in m1 + m2:
        if x not in miss:
            miss.append(x)
    filled = sum(1 for x in head + body if x["filled"])
    return {"id": t["id"], "title": t["title"], "head": head, "body": body, "filled": filled,
            "missing": [{"key": k, "label": lab} for k, lab in miss], "notes": data.get("_notes", [])}


def safe_name(s: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", s).strip("_")[:60] or "документ"


def make_docx(store: Store, cid: int, tpl_id: str, out_dir: str) -> str:
    r = render(store, cid, tpl_id)
    c = store.contract(cid) or {}
    os.makedirs(out_dir, exist_ok=True)
    name = f"{safe_name(r['title'].split(' ')[0].lower())}_{safe_name(c.get('number') or str(cid))}_{date.today():%Y%m%d}.docx"
    path = os.path.join(out_dir, name)
    docx_out.build_docx(path, r["head"], r["title"], r["body"])
    return path
