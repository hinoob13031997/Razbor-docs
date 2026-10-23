"""Мелкие общие функции: чистка текста, даты, суммы."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")


def clean_cell(value):
    """Подготовить значение для записи в Excel.

    Убирает управляющие символы (Excel их не принимает) и не даёт строке,
    начинающейся с «=», стать формулой.
    """
    if isinstance(value, str):
        value = _CTRL.sub("", value)
        if len(value) > 32000:
            value = value[:32000] + "…"
    return value


def normalize(text: str) -> str:
    """Привести текст страницы к единому виду, не меняя слов."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = text.replace(" ", " ").replace(" ", " ").replace("­", "")
    text = _CTRL.sub("", text)
    # перенос слова через строку: «задолжен-\nность» -> «задолженность»
    text = re.sub(r"(?<=[а-яё])-\n(?=[а-яё])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_garbled(text: str) -> bool:
    """Текстовый слой есть, но читать его нельзя (битая кодировка шрифта)."""
    if not text:
        return False
    sample = text[:6000]
    letters = sum(1 for c in sample if c.isalpha())
    if letters < 40:
        return False
    private = sum(1 for c in sample if "" <= c <= "" or c == "�")
    if private / max(1, len(sample)) > 0.05:
        return True
    cyr = sum(1 for c in sample if "а" <= c.lower() <= "я" or c.lower() == "ё")
    lat = sum(1 for c in sample if "a" <= c.lower() <= "z")
    # русские документы: кириллицы почти нет, а латиница есть и выглядит как «каша»
    if cyr / letters < 0.05 and lat / letters > 0.5:
        words = re.findall(r"[A-Za-z]{3,}", sample)
        if words:
            vowels = sum(1 for w in words for c in w.lower() if c in "aeiouy")
            ratio = vowels / max(1, sum(len(w) for w in words))
            return ratio < 0.2 or ratio > 0.6
    return False


# ---------------------------------------------------------------- даты

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_ALT = "|".join(MONTHS)
_DATE_NUM = re.compile(r"(?<![\d.,/])(\d{1,2})[./](\d{1,2})[./](\d{4})(?![\d])")
_DATE_WORD = re.compile(
    r"[«\"“„]?\s*(\d{1,2})\s*[»\"”]?\s*(" + _MONTH_ALT + r")\s+(\d{4})", re.I
)


def _mk(y: int, m: int, d: int):
    if not (1930 <= y <= 2100):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str):
    """Все даты в тексте: список (date, начало, конец), по порядку появления."""
    found = []
    for m in _DATE_NUM.finditer(text):
        d = _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            found.append((d, m.start(), m.end()))
    for m in _DATE_WORD.finditer(text):
        d = _mk(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        if d:
            found.append((d, m.start(), m.end()))
    found.sort(key=lambda t: t[1])
    return found


# ---------------------------------------------------------------- суммы

def to_decimal(int_part: str, frac: str | None, kop: str | None = None) -> Decimal | None:
    digits = re.sub(r"\D", "", int_part)
    if not digits:
        return None
    frac = frac or kop or "00"
    if len(frac) == 1:
        frac = frac + "0" if kop is None else "0" + frac
    try:
        return Decimal(f"{int(digits)}.{frac}")
    except (InvalidOperation, ValueError):
        return None


def fmt_money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")
