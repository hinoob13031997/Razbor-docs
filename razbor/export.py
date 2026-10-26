"""Выгрузка результатов в Excel."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import __version__
from .textutil import clean_cell

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERR_FONT = Font(color="C00000")
LINK_FONT = Font(color="0563C1", underline="single")

# (заголовок, ширина, ключ поля или функция)
MAIN_FIELDS = [
    ("Дата документа", 13), ("Номер дела", 18), ("УИД", 28), ("Суд", 38), ("Номер ИП", 20),
    ("Исп. лист (серия, №)", 18), ("Номер договора", 20), ("ФИО", 32), ("Дата рождения", 13),
    ("Взыскатель / кредитор", 36), ("Цедент", 30), ("Сумма основная", 15), ("Госпошлина", 12),
    ("Сумма кредита / займа", 16), ("Ставка, % годовых", 10), ("Вступил в силу", 16),
    ("Основание окончания", 44), ("Исполнительский сбор", 14), ("Кого заменили", 30), ("На кого заменили", 30),
    ("Дата уступки", 13), ("Номер договора уступки", 20), ("VIN (залог)", 20),
]


def _val(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _put(ws, row, col, value, number_format=None):
    cell = ws.cell(row=row, column=col)
    value = clean_cell(_val(value))
    cell.value = value
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        cell.data_type = "s"  # текст, а не формула
    if number_format:
        cell.number_format = number_format
    return cell


def _link(ws, row, col, path, name):
    cell = _put(ws, row, col, name)
    try:
        cell.hyperlink = os.path.abspath(path)
        cell.font = LINK_FONT
    except Exception:
        pass
    return cell


def _header(ws, headers):
    for i, (h, w) in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 32


def write_excel(summary, custom_fields) -> None:
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Итоги"
    ws_docs = wb.create_sheet("Документы")
    ws_chk = wb.create_sheet("Проверить")
    ws_dup = wb.create_sheet("Дубли")
    ws_err = wb.create_sheet("Ошибки")
    ws_det = wb.create_sheet("Поля (детально)")

    results = summary.results
    ok = [r for r in results if not r.error]
    errs = [r for r in results if r.error]
    review = [r for r in ok if r.reasons]
    dups = [r for r in ok if r.dup_group]

    # ------------------------------------------------------------ Документы
    custom_names = [c.name for c in (custom_fields or [])]
    headers = [("№", 6), ("Файл", 34), ("Папка", 24), ("Страниц", 9), ("Прочитано стр.", 10),
               ("Как прочитан", 14), ("OCR, %", 8), ("Группа", 18), ("Вид документа", 38),
               ("Уточнение", 40)]
    headers += MAIN_FIELDS
    headers += [("Признаки", 28), ("Все суммы (руб.)", 34), ("Дубль", 8), ("Проверить", 50)]
    headers += [(n, 22) for n in custom_names]
    _header(ws_docs, headers)
    col_of = {h: i for i, (h, _w) in enumerate(headers, 1)}

    for n, r in enumerate(ok, 1):
        row = n + 1
        a = r.analysis
        _put(ws_docs, row, 1, n)
        _link(ws_docs, row, col_of["Файл"], r.path, r.name)
        _put(ws_docs, row, col_of["Папка"], r.rel_dir)
        _put(ws_docs, row, col_of["Страниц"], r.page_count)
        _put(ws_docs, row, col_of["Прочитано стр."], r.pages_read)
        _put(ws_docs, row, col_of["Как прочитан"], r.method)
        if r.ocr_conf is not None:
            _put(ws_docs, row, col_of["OCR, %"], round(r.ocr_conf), "0")
        if a is not None:
            _put(ws_docs, row, col_of["Группа"], a.group)
            _put(ws_docs, row, col_of["Вид документа"], a.doc_label)
            _put(ws_docs, row, col_of["Уточнение"], a.title)
            for name, _w in MAIN_FIELDS:
                hit = a.fields.get(name)
                if hit is None:
                    continue
                v = hit.value
                fmt = None
                if isinstance(v, date):
                    fmt = "DD.MM.YYYY"
                elif isinstance(v, Decimal):
                    fmt = "#,##0.00" if name != "Ставка, % годовых" else "0.00"
                _put(ws_docs, row, col_of[name], v, fmt)
            _put(ws_docs, row, col_of["Признаки"], "; ".join(a.flags))
            h = a.fields.get("Все суммы (руб.)")
            if h:
                _put(ws_docs, row, col_of["Все суммы (руб.)"], h.value)
            for cname, cval in a.custom.items():
                if cname in col_of:
                    _put(ws_docs, row, col_of[cname], cval)
        _put(ws_docs, row, col_of["Дубль"], r.dup_group)
        if r.reasons:
            c = _put(ws_docs, row, col_of["Проверить"], "; ".join(r.reasons))
            c.fill = WARN_FILL
    ws_docs.freeze_panes = "C2"
    if ok:
        ws_docs.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(ok) + 1}"

    # ------------------------------------------------------------ Проверить
    _header(ws_chk, [("Файл", 36), ("Папка", 26), ("Вид документа", 38), ("Причины", 80)])
    for i, r in enumerate(review, 2):
        _link(ws_chk, i, 1, r.path, r.name)
        _put(ws_chk, i, 2, r.rel_dir)
        _put(ws_chk, i, 3, r.analysis.doc_label if r.analysis else "")
        _put(ws_chk, i, 4, "; ".join(r.reasons))
    ws_chk.freeze_panes = "A2"

    # ------------------------------------------------------------ Дубли
    _header(ws_dup, [("Группа", 9), ("Совпадение", 36), ("Файл", 38), ("Папка", 28),
                     ("Размер, КБ", 11), ("Дата документа", 14), ("Номер дела", 20)])
    for i, r in enumerate(sorted(dups, key=lambda x: (x.dup_group, x.rel_dir, x.name)), 2):
        _put(ws_dup, i, 1, r.dup_group)
        _put(ws_dup, i, 2, getattr(r, "dup_kind", ""))
        _link(ws_dup, i, 3, r.path, r.name)
        _put(ws_dup, i, 4, r.rel_dir)
        _put(ws_dup, i, 5, round(r.size / 1024, 1), "0.0")
        a = r.analysis
        if a and "Дата документа" in a.fields:
            _put(ws_dup, i, 6, a.fields["Дата документа"].value, "DD.MM.YYYY")
        if a and "Номер дела" in a.fields:
            _put(ws_dup, i, 7, a.fields["Номер дела"].value)
    ws_dup.freeze_panes = "A2"

    # ------------------------------------------------------------ Ошибки
    _header(ws_err, [("Файл", 40), ("Папка", 30), ("Что случилось", 70)])
    for i, r in enumerate(errs, 2):
        _link(ws_err, i, 1, r.path, r.name)
        _put(ws_err, i, 2, r.rel_dir)
        c = _put(ws_err, i, 3, r.error)
        c.font = ERR_FONT
    ws_err.freeze_panes = "A2"

    # ------------------------------------------------------------ Поля (детально)
    _header(ws_det, [("Файл", 34), ("Поле", 24), ("Значение", 40), ("Страница", 9),
                     ("Как найдено", 22), ("Фрагмент текста вокруг", 90)])
    i = 2
    for r in ok:
        a = r.analysis
        if a is None:
            continue
        for name, hit in a.fields.items():
            v = hit.value
            fmt = "DD.MM.YYYY" if isinstance(v, date) else ("#,##0.00" if isinstance(v, Decimal) else None)
            _link(ws_det, i, 1, r.path, r.name)
            _put(ws_det, i, 2, name)
            _put(ws_det, i, 3, v, fmt)
            _put(ws_det, i, 4, a.pages.get(name))
            _put(ws_det, i, 5, hit.note)
            _put(ws_det, i, 6, a.snippets.get(name, ""))
            i += 1
    ws_det.freeze_panes = "A2"
    if i > 2:
        ws_det.auto_filter.ref = f"A1:F{i - 1}"

    # ------------------------------------------------------------ Итоги
    _summary_sheet(ws0, summary, ok, errs, review, dups)

    out = summary.output_path
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    wb.save(out)


def _summary_sheet(ws, summary, ok, errs, review, dups):
    o = summary.options
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 60
    r = 1
    ws.cell(row=r, column=1, value="Разбор документов").font = Font(bold=True, size=16)
    r += 1
    ws.cell(row=r, column=1, value=f"Программа версии {__version__}. Обработка выполнена на этом компьютере, без интернета.")
    r += 2

    def line(label, value, fmt=None):
        nonlocal r
        ws.cell(row=r, column=1, value=label)
        c = ws.cell(row=r, column=2, value=clean_cell(value))
        if fmt:
            c.number_format = fmt
        r += 1

    dur = (summary.finished - summary.started).total_seconds()
    line("Папка с документами", o.input_dir)
    line("Начало", summary.started.strftime("%d.%m.%Y %H:%M:%S"))
    line("Время работы", f"{int(dur // 60)} мин {int(dur % 60)} с")
    line("Распознавание сканов", "включено" if o.use_ocr else "выключено")
    if o.use_ocr:
        line("Страниц на документ для распознавания", o.max_ocr_pages if o.max_ocr_pages > 0 else "все")
    line("Потоков", o.workers)
    if summary.cancelled:
        c = ws.cell(row=r, column=1, value="ОБРАБОТКА БЫЛА ОСТАНОВЛЕНА, в таблице только то, что успели обработать")
        c.font = Font(bold=True, color="C00000")
        r += 1
    r += 1
    line("Файлов обработано", len(ok) + len(errs))
    line("Прочитано успешно", len(ok))
    line("Не прочитано (лист «Ошибки»)", len(errs))
    line("Нужно проверить вручную (лист «Проверить»)", len(review))
    line("Файлов в группах дублей (лист «Дубли»)", len(dups))
    r += 1

    by_type: dict[str, list] = defaultdict(list)
    for x in ok:
        by_type[x.analysis.doc_label if x.analysis else "Не определён"].append(x)
    ws.cell(row=r, column=1, value="Вид документа").font = Font(bold=True)
    ws.cell(row=r, column=2, value="Документов").font = Font(bold=True)
    ws.cell(row=r, column=3, value="Сумма основная, руб. (без повторов)").font = Font(bold=True)
    r += 1
    counted_groups: set[str] = set()
    for label, items in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        total = Decimal(0)
        for x in items:
            if x.dup_group:  # дубли в сумму входят один раз
                if x.dup_group in counted_groups:
                    continue
                counted_groups.add(x.dup_group)
            h = x.analysis.fields.get("Сумма основная") if x.analysis else None
            if h is not None and isinstance(h.value, Decimal):
                total += h.value
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=len(items))
        c = ws.cell(row=r, column=3, value=float(total))
        c.number_format = "#,##0.00"
        r += 1
    r += 1
    if summary.notes:
        ws.cell(row=r, column=1, value="Замечания").font = Font(bold=True)
        r += 1
        for n in summary.notes:
            ws.cell(row=r, column=1, value=clean_cell(n)).font = ERR_FONT
            r += 1
        r += 1
    ws.cell(row=r, column=1, value="Как пользоваться").font = Font(bold=True)
    r += 1
    for t in (
        "Лист «Документы»: одна строка на файл. Имя файла — ссылка, по ней открывается оригинал.",
        "Лист «Поля (детально)»: у каждого значения указаны страница и фрагмент текста, чтобы сверить за секунды.",
        "Лист «Проверить»: файлы, где программа не уверена. Остальное тоже стоит проверять выборочно.",
        "Суммы и даты программа находит по формулировкам в тексте и может ошибиться, особенно на плохих сканах.",
        "Файл содержит персональные данные: храните его там же, где храните сами документы.",
    ):
        ws.cell(row=r, column=1, value=t)
        r += 1
