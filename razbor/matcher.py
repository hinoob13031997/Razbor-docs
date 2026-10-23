"""Привязка прочитанного документа к договору из портфеля и факты из документа."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .analyze import Analysis
from .crm import norm_number, norm_text
from .store import Store
from .textutil import fmt_money


# ---------------------------------------------------------------- ФИО

def _tok_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if len(a) <= 2 or len(b) <= 2:  # инициал
        return a[0] == b[0]
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= max(3, min(len(a), len(b)) - 2)


def fio_match(doc_fio: str, crm_norm: str) -> bool:
    """Совпадает ли ФИО из документа (в любом падеже, возможно с инициалами) с ФИО из портфеля."""
    a = [t for t in norm_text(doc_fio).split() if t not in ("ип", "индивидуальный", "предприниматель")]
    b = [t for t in (crm_norm or "").split() if t not in ("ип", "индивидуальный", "предприниматель")]
    if not a or not b or not _tok_match(a[0], b[0]):
        return False
    for i in (1, 2):
        if i < len(a) and i < len(b) and not _tok_match(a[i], b[i]):
            return False
    return len(a) >= 2 and len(b) >= 2


# ---------------------------------------------------------------- привязка

def _iso(v) -> str | None:
    if isinstance(v, date):
        return v.isoformat()
    return None


def match_document(store: Store, a: Analysis) -> dict:
    """Найти договоры, к которым относится документ.

    Возвращает {"status": "ok"|"confirm"|"nf", "candidates": [...]}.
    """
    ev: dict[int, dict] = {}
    rows: dict[int, dict] = {}

    def hit(c: dict, typ: str, score: int, reason: str) -> None:
        e = ev.setdefault(c["id"], {"types": {}, "reasons": []})
        rows[c["id"]] = c
        if typ not in e["types"]:
            e["types"][typ] = score
            e["reasons"].append(reason)

    ids = a.ids or {}
    nums = {}
    for v in list(ids.get("contracts", [])) + list(ids.get("tokens", [])):
        nums[norm_number(v)] = v
    for c in store.contracts_by_numbers(list(nums)):
        hit(c, "number", 100, f"номер договора {c['number']}")
    inns = [x for x in ids.get("inns", []) if x]
    for c in store.contracts_by("inn", inns) if inns else []:
        hit(c, "inn", 90, f"ИНН {c['inn']}")
    cases = [norm_number(x) for x in ids.get("cases", [])]
    for c in store.contracts_by("case_norm", cases) if cases else []:
        hit(c, "case", 80, f"номер дела {c['case_no']}")
    writs = [norm_number(x) for x in ids.get("writs", [])]
    for c in store.contracts_by("il_norm", writs) if writs else []:
        hit(c, "il", 80, f"исполнительный лист {c['il_no']}")

    fio = a.fields.get("ФИО")
    if fio is not None:
        fio_txt = str(fio.value)
        toks = norm_text(fio_txt).split()
        birth = _iso(a.fields["Дата рождения"].value) if "Дата рождения" in a.fields else None
        if birth:
            for c in store.contracts_by("birth", [birth]):
                if fio_match(fio_txt, c["fio_norm"]):
                    hit(c, "fio_birth", 85, "ФИО и дата рождения")
        if toks and not any("fio_birth" in e["types"] for e in ev.values()):
            for c in store.contracts_by("surname_key", [toks[0][:5]]):
                if fio_match(fio_txt, c["fio_norm"]):
                    hit(c, "fio", 40, "ФИО совпало, даты рождения нет")

    cands = []
    for cid, e in ev.items():
        scores = sorted(e["types"].values(), reverse=True)
        total = min(150, scores[0] + 10 * (len(scores) - 1))
        c = rows[cid]
        cands.append({"id": cid, "number": c["number"], "fio": c["fio"], "birth": c["birth"], "debt": c["debt"],
                      "score": total, "reasons": e["reasons"]})
    cands.sort(key=lambda x: -x["score"])
    if not cands:
        return {"status": "nf", "candidates": []}
    top = cands[0]
    second = cands[1] if len(cands) > 1 else None
    if top["score"] >= 85 and (second is None or second["score"] < top["score"] - 15):
        return {"status": "ok", "candidates": cands[:3]}
    return {"status": "confirm", "candidates": cands[:6]}


# ---------------------------------------------------------------- факты и поля для показа

def fmt_value(v) -> str:
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, Decimal):
        return fmt_money(v)
    return str(v)


def display_fields(a: Analysis) -> list[dict]:
    out = []
    for name, hit in a.fields.items():
        if name == "Все суммы (руб.)":
            continue
        out.append({"name": name, "value": fmt_value(hit.value), "page": a.pages.get(name),
                    "snippet": a.snippets.get(name, ""), "note": hit.note})
    for name, val in a.custom.items():
        out.append({"name": name, "value": val, "page": None, "snippet": "", "note": "свои поля"})
    return out


def facts_from(a: Analysis) -> dict[str, str]:
    """Что документ сообщает о договоре (даты и номера для расчёта сроков)."""
    f = a.fields
    out: dict[str, str] = {}

    def dt(name: str) -> str | None:
        h = f.get(name)
        return _iso(h.value) if h is not None else None

    dk = a.doc_key
    if dk in ("decision", "order"):
        if dt("Дата документа"):
            out["decision_date"] = dt("Дата документа")  # type: ignore[assignment]
            out["decision_kind"] = dk
        if dt("Вступил в силу"):
            out["effective_date"] = dt("Вступил в силу")  # type: ignore[assignment]
        if "Сумма основная" in f:
            out["amount_granted"] = str(f["Сумма основная"].value)
        if "Номер дела" in f:
            out["case_no"] = str(f["Номер дела"].value)
    elif dk == "order_cancel" and dt("Дата документа"):
        out["order_cancel_date"] = dt("Дата документа")  # type: ignore[assignment]
    elif dk == "writ" and "Исп. лист (серия, №)" in f:
        out["il_no"] = str(f["Исп. лист (серия, №)"].value)
    elif dk == "fssp_start":
        if dt("Дата документа"):
            out["ip_start_date"] = dt("Дата документа")  # type: ignore[assignment]
        if "Номер ИП" in f:
            out["ip_no"] = str(f["Номер ИП"].value)
    elif dk in ("fssp_end", "fssp_stop") and dt("Дата документа"):
        out["ip_end_date"] = dt("Дата документа")  # type: ignore[assignment]
        if "Основание окончания" in f:
            out["ip_end_basis"] = str(f["Основание окончания"].value)
    elif dk in ("death_cert", "estate"):
        if dt("Дата смерти"):
            out["death_date"] = dt("Дата смерти")  # type: ignore[assignment]
    elif dk == "bankr" and dt("Дата документа"):
        out["bankr_date"] = dt("Дата документа")  # type: ignore[assignment]
        if "Номер дела" in f:
            out["bankr_case"] = str(f["Номер дела"].value)
    return out


def doc_event(a: Analysis, name: str) -> tuple[str, str]:
    """(дата, текст) записи в историю договора."""
    dd = a.fields.get("Дата документа")
    when = _iso(dd.value) if dd is not None else None
    text = a.doc_label
    if "Номер дела" in a.fields:
        text += f", дело № {a.fields['Номер дела'].value}"
    if "Номер ИП" in a.fields and a.group == "ФССП":
        text += f", ИП № {a.fields['Номер ИП'].value}"
    return (when or date.today().isoformat(), text)


def compare_with_crm(c: dict, docs: list[dict]) -> list[str]:
    """Расхождения между CRM и прочитанными документами (для карточки)."""
    notes: list[str] = []
    for d in docs:
        if d["doc_key"] not in ("decision", "order"):
            continue
        fl = {x["name"]: x for x in d["fields"]}
        amt = fl.get("Сумма основная")
        granted = c.get("principal_granted")
        if amt and granted:
            try:
                v = float(re.sub(r"[^\d,]", "", amt["value"]).replace(",", "."))
            except ValueError:
                v = None
            if v is not None and abs(v - granted) > 0.5:
                notes.append(f"Сумма в документе «{d['doc_label']}» ({amt['value']} ₽) не совпадает с CRM "
                             f"«основной долг удовлетворено» ({granted:,.2f} ₽)".replace(",", " "))
        case = fl.get("Номер дела")
        if case and c.get("case_norm") and norm_number(case["value"]) != c["case_norm"]:
            notes.append(f"Номер дела в документе ({case['value']}) отличается от CRM ({c.get('case_no')})")
    return notes[:6]
