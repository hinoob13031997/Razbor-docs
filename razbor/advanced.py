"""Автономные инструменты: импорт платежей, справочник ставок, индексация,
подсказки судебных связей, проверенные обзоры практики и резервирование."""
import csv
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from . import dossiers, matcher
from .crm import norm_number

def init(store):
    with store.write() as c:
        c.execute("CREATE TABLE IF NOT EXISTS court_crm(dossier_id INTEGER NOT NULL, contract_id INTEGER NOT NULL, PRIMARY KEY(dossier_id,contract_id))")
        c.execute("CREATE TABLE IF NOT EXISTS practice(id INTEGER PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL,summary TEXT NOT NULL,created TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS rate_book(day TEXT PRIMARY KEY,rate TEXT NOT NULL)")

def _day(v):
    if isinstance(v,datetime):
        return v.date().isoformat()
    if isinstance(v,date):
        return v.isoformat()
    v=str(v).strip()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        return datetime.strptime(v,"%d.%m.%Y").date().isoformat()

def payment_rows(raw,filename):
    """Только явный шаблон: Дата, Сумма, В базу, Номер платежа."""
    if len(raw)>15*1024*1024:
        raise ValueError("Файл больше 15 МБ")
    if filename.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(x.file_size for x in z.infolist())>100*1024*1024:
                raise ValueError("Слишком большой распакованный Excel")
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
        try:
            it=wb.active.iter_rows(max_col=4)
            header=[str(c.value or "").strip().lower() for c in next(it,[])]
            rows=[]
            for cells in it:
                if len(rows)>=10000:
                    raise ValueError("Не более 10000 строк")
                if any(c.data_type=="f" for c in cells):
                    raise ValueError("Замените формулы значениями")
                rows.append([c.value for c in cells])
        finally:
            wb.close()
    elif filename.lower().endswith(".csv"):
        try:
            s=raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            s=raw.decode("cp1251")
        reader=csv.reader(io.StringIO(s),delimiter=";")
        header=[x.strip().lower() for x in next(reader,[])]
        rows=list(reader)
    else:
        raise ValueError("Нужен XLSX или CSV")
    if header!=["дата","сумма","в базу","номер платежа"]:
        raise ValueError("Заголовки: Дата;Сумма;В базу;Номер платежа")
    out=[]
    for n,r in enumerate(rows,2):
        if not any(v is not None and str(v).strip() for v in r):
            continue
        if len(r)!=4 or r[3] is None or not str(r[3]).strip():
            raise ValueError(f"Строка {n}: четыре поля и номер платежа обязательны")
        try:
            out.append({"date":_day(r[0]),"total":str(r[1]),"principal":"" if r[2] is None else str(r[2]),"reference":str(r[3]).strip()})
        except (ValueError,TypeError) as e:
            raise ValueError(f"Строка {n}: неверная дата") from e
    if not 1<=len(out)<=10000:
        raise ValueError("Нужно от 1 до 10000 платежей")
    return out

def save_rates(store,rows,verified,source):
    verified=_day(verified)
    if not isinstance(source,str) or not source.strip() or len(source)>1000:
        raise ValueError("Укажите источник справочника")
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:
        raise ValueError("Введите даты и ставки")
    seen=set()
    prepared=[]
    for r in rows:
        day=_day(r["date"])
        rate=dossiers._money(r["rate"])
        if day in seen or day>verified or rate>1000:
            raise ValueError("Повтор даты, дата после проверки или неверная ставка")
        seen.add(day)
        prepared.append((day,str(rate)))
    with store.write() as c:
        c.execute("DELETE FROM rate_book")
        c.executemany("INSERT INTO rate_book VALUES(?,?)",prepared)
        for k,v in (("rate_verified",verified),("rate_source",source.strip())):
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))

def rates(store):
    return {"rows":[{"date":r["day"],"rate":r["rate"]} for r in store.q("SELECT day,rate FROM rate_book ORDER BY day")],
            "verified":store.get_setting("rate_verified",""),"source":store.get_setting("rate_source","")}

def indexation(amount,months):
    """Математическая иллюстрация по подтвержденным полным месячным индексам.
    Не определяет законный период, частичные месяцы или распределение платежей."""
    base=dossiers._money(amount)
    if not isinstance(months,list) or not 1<=len(months)<=1200:
        raise ValueError("Введите от 1 до 1200 месячных индексов")
    old=None
    running=base
    segments=[]
    for r in months:
        m=r["month"]
        if not isinstance(m,str) or not re.fullmatch(r"\d{4}-\d{2}",m):
            raise ValueError("Месяц в формате ГГГГ-ММ")
        d=date.fromisoformat(m+"-01")
        if old and d.year*12+d.month!=old.year*12+old.month+1:
            raise ValueError("Месяцы должны идти подряд без пропусков")
        old=d
        v=dossiers._money(r["index"])
        if not Decimal("0")<v<=Decimal("1000"):
            raise ValueError("Индекс должен быть больше нуля")
        running*=v/100
        segments.append({"month":m,"index":str(v),"amount":str(running.quantize(Decimal(".01"),ROUND_HALF_UP))})
    return {"initial":str(base),"indexed":str(running.quantize(Decimal(".01"),ROUND_HALF_UP)),
            "difference":str((running-base).quantize(Decimal(".01"),ROUND_HALF_UP)),"segments":segments}

def suggestions(docs,links):
    out=[]
    existing={(x["child"],x["parent"]) for x in links}
    for child in docs:
        if child.get("doc_key") not in ("appeal","cassation"):
            continue
        refs={norm_number(str(f["value"])) for f in child["fields"] if f["name"].startswith("Упомянутое дело")}
        for parent in docs:
            if parent["sha"]==child["sha"] or (child["sha"],parent["sha"]) in existing:
                continue
            if not child["date"] or not parent["date"] or child["date"]<=parent["date"]:
                continue
            n=norm_number(parent.get("case") or "")
            if n and n in refs:
                out.append({"child":child["sha"],"parent":parent["sha"],"reason":"В тексте позднего акта упомянут номер более раннего дела; проверьте суд, стороны и ссылку."})
    return out[:200]

def connect_crm(store,did,cid):
    with store.write() as c:
        if not c.execute("SELECT 1 FROM court_dossiers WHERE id=?",(did,)).fetchone() or not c.execute("SELECT 1 FROM contracts WHERE id=?",(cid,)).fetchone():
            raise ValueError("Досье или договор не найден")
        c.execute("INSERT OR IGNORE INTO court_crm VALUES(?,?)",(did,cid))

def crm_checks(store,did):
    out=[]
    for r in store.q("SELECT contract_id FROM court_crm WHERE dossier_id=?",(did,)):
        c=store.contract(r["contract_id"])
        if c:
            out.append({"id":c["id"],"number":c["number"],"fio":c["fio"],"debt":c.get("debt"),"checks":matcher.compare_with_crm(c,store.documents_for(c["id"]))})
    return out

def practice_save(store,title,category,summary,confirmed):
    if confirmed is not True:
        raise ValueError("Подтвердите ручную проверку отсутствия персональных данных")
    vals=[title,category,summary]
    if any(not isinstance(x,str) or not x.strip() for x in vals) or len(title)>200 or len(category)>100 or len(summary)>20000:
        raise ValueError("Заполните название, категорию и проверенный обзор")
    with store.write() as c:
        return c.execute("INSERT INTO practice(title,category,summary,created) VALUES(?,?,?,?)",
                         (title.strip(),category.strip(),summary.strip(),datetime.now().isoformat(timespec="seconds"))).lastrowid

def practice_list(store):
    return store.q("SELECT * FROM practice ORDER BY id DESC LIMIT 1000")

def redact(text,terms):
    """Черновик текстового обзора, не обезличивание PDF и не гарантия анонимности."""
    if not isinstance(text,str) or len(text)>200000 or not isinstance(terms,list) or len(terms)>500:
        raise ValueError("Слишком большой текст или список замен")
    for term in sorted({x.strip() for x in terms if isinstance(x,str) and x.strip()},key=len,reverse=True):
        text=re.sub(re.escape(term),"[СКРЫТО]",text,flags=re.I)
    text=re.sub(r"(?i)[\w.+-]+@[\w.-]+\.[a-zа-я]{2,}","[EMAIL]",text)
    text=re.sub(r"(?<!\d)(?:\+?7|8)[\s().-]*(?:\d[\s().-]*){10}(?!\d)","[ТЕЛЕФОН]",text)
    text=re.sub(r"(?<!\d)(?:\d{12}|\d{10}|\d{4}\s+\d{6})(?!\d)","[НОМЕР]",text)
    return text

def backup(store):
    """Согласованный снимок SQLite и локальных оригиналов; без временных загрузок."""
    root=Path(store.path).resolve().parent
    fd,tmp=tempfile.mkstemp(suffix=".zip",dir=root)
    os.close(fd)
    dbtmp=None
    try:
        with tempfile.TemporaryDirectory() as td:
            dbtmp=Path(td)/"razbor.sqlite"
            with store._write_lock:
                conn=sqlite3.connect(dbtmp)
                try:
                    store.conn().backup(conn)
                finally:
                    conn.close()
                with zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED) as z:
                    z.write(dbtmp,"razbor.sqlite")
                    for name in ("court_originals","batches","docs"):
                        folder=root/name
                        if folder.is_dir():
                            for p in folder.rglob("*"):
                                if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp"):
                                    z.write(p,str(p.relative_to(root)))
                    z.writestr("README.txt","Локальный архив. Содержит персональные данные. Не передавать третьим лицам. Восстановление: закрыть программу, сохранить старую папку данных отдельно, извлечь архив в новую папку данных. Временные загрузки и исходные сетевые папки не включены.")
        return tmp
    except Exception:
        os.unlink(tmp)
        raise
