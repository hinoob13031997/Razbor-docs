"""Разбор одного документа: вид, поля, признаки, причины для ручной проверки."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from . import classify as C
from . import fields as F
from . import namecheck as NC
from .reader import ReadResult


@dataclass
class CustomField:
    name: str
    regex: re.Pattern
    group: int = 1
    only_types: tuple[str, ...] = ()


def load_custom_fields(path: str | None) -> tuple[list[CustomField], list[str]]:
    """Свои поля из файла polya.json (необязательный). Возвращает (поля, ошибки)."""
    if not path:
        return [], []
    errors: list[str] = []
    out: list[CustomField] = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return [], []
    except Exception as e:
        return [], [f"polya.json не прочитан: {e}"]
    if not isinstance(data, list):
        return [], ["polya.json: ожидается список полей"]
    for i, item in enumerate(data, 1):
        try:
            flags = 0
            if "i" in str(item.get("flags", "i")).lower():
                flags |= re.I
            if "m" in str(item.get("flags", "")).lower():
                flags |= re.M
            rx = re.compile(item["pattern"], flags)
            out.append(CustomField(str(item["name"]), rx, int(item.get("group", 1)),
                                   tuple(item.get("only_types", []))))
        except Exception as e:
            errors.append(f"polya.json, поле №{i}: {e}")
    return out, errors


@dataclass
class Analysis:
    doc_key: str = "unknown"
    doc_label: str = "Не определён"
    group: str = C.G_OTHER
    score: int = 0
    title: str = ""
    fields: dict[str, F.Hit] = field(default_factory=dict)
    custom: dict[str, str] = field(default_factory=dict)
    pages: dict[str, int] = field(default_factory=dict)   # поле -> страница
    snippets: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    text_hash: str = ""
    chars: int = 0
    ids: dict = field(default_factory=dict)
    summary: str = ""                                    # что это за документ, простыми словами
    issues: list = field(default_factory=list)           # возможные описки внутри документа
    stems: frozenset = frozenset()                       # первые буквы слов документа (для сверки названия)
    dates: list = field(default_factory=list)            # все даты текста: (ISO, чем является)


def text_fingerprint(text: str) -> str:
    """Хэш «смысла» текста: без регистра, пробелов и знаков; ловит копии с разным файлом."""
    t = re.sub(r"[^0-9a-zа-яё]+", "", text.lower())
    if len(t) < 80:
        return ""
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def analyze(rr: ReadResult, custom: list[CustomField] | None = None) -> Analysis:
    pages = [p.text for p in rr.pages]
    doc = F.Doc(pages)
    a = Analysis()
    a.chars = len(doc.text.replace("\n", "").replace(" ", ""))
    if a.chars < 30:
        return a
    cl = C.classify(doc.text)
    a.doc_key, a.doc_label, a.group, a.score = cl.key, cl.label, cl.group, cl.score
    a.title = C.title_detail(doc.head)
    a.fields = F.extract(doc, cl.key, cl.group)
    for name, hit in a.fields.items():
        a.pages[name] = doc.page_of(hit.pos)
        a.snippets[name] = doc.snippet(hit.pos, hit.end)
    a.flags = F.find_flags(doc, cl.group, cl.key)
    a.text_hash = text_fingerprint(doc.text)
    a.ids = F.find_identifiers(doc)
    try:
        a.stems = NC.word_stems(doc.text)
        a.dates = NC.date_index(doc.text, a)
        a.summary = NC.describe(a)
        a.issues = NC.internal_issues(doc.text, doc.head, a)
        for wrong, right in cl.repaired:
            a.issues.append(f"Заголовок документа написан с ошибкой или плохо распознан («{wrong}», должно быть «{right}»). "
                            f"Вид определён по содержанию: {a.doc_label.lower()}.")
    except Exception as e:  # расшифровка вторична: она не должна ломать чтение
        a.summary = a.summary or ""
        a.issues = a.issues or [f"расшифровка не построена: {type(e).__name__}"]
    for cf in custom or []:
        if cf.only_types and cl.key not in cf.only_types:
            continue
        m = cf.regex.search(doc.text)
        if m:
            try:
                a.custom[cf.name] = m.group(cf.group).strip()
            except (IndexError, re.error):
                a.custom[cf.name] = m.group(0).strip()
    return a


KEY_FIELDS = ("Номер дела", "ФИО", "Сумма основная", "Номер договора", "Номер ИП",
              "Исп. лист (серия, №)", "Сумма кредита / займа")


def review_reasons(rr: ReadResult, a: Analysis, ocr_warn_below: float = 70.0) -> list[str]:
    reasons: list[str] = []
    if a.chars < 50:
        reasons.append("текст не найден (пустой скан или нераспознан)")
        return reasons
    conf = rr.ocr_conf
    if conf is not None and conf < ocr_warn_below:
        reasons.append(f"низкая уверенность распознавания ({conf:.0f}%)")
    if a.doc_key == "unknown":
        reasons.append("вид документа не определён")
    elif not any(k in a.fields for k in KEY_FIELDS):
        reasons.append("не найдено ни одного ключевого поля")
    s = a.fields.get("Сумма основная")
    if s is not None and "максимуму" in s.note:
        reasons.append("сумма выбрана по максимуму, проверьте")
    if "ФИО" in a.fields and a.fields["ФИО"].note == "первое ФИО":
        reasons.append("ФИО взято без метки")
    for w in rr.warnings:
        if "пропущено" in w or "не все" in w:
            reasons.append(w)
    for x in a.issues:
        reasons.append(x)
    return reasons


def display_value(v):
    if isinstance(v, Decimal):
        return float(v)
    return v
