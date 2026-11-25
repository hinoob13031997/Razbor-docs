"""Локальные судебные досье: ручное объединение актов, без обязательной CRM."""
import json
import re
import os
import shutil
import tempfile
from pathlib import Path
from decimal import Decimal, InvalidOperation
from .reader import file_sha256
from datetime import datetime, date
from .crm import norm_number

def init(store):
    with store.write() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS court_dossiers(
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
          reviewed_doc TEXT, review_note TEXT DEFAULT '')""")
        c.execute("""CREATE TABLE IF NOT EXISTS court_dossier_docs(
          dossier_id INTEGER NOT NULL, sha TEXT NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(dossier_id,sha))""")
    init_calculations(store)
    with store.write() as c:
        c.execute('CREATE TABLE IF NOT EXISTS court_payments(id INTEGER PRIMARY KEY, dossier_id INTEGER NOT NULL, day TEXT NOT NULL, total TEXT NOT NULL, principal TEXT, reference TEXT NOT NULL, UNIQUE(dossier_id,reference))')
        c.execute('CREATE TABLE IF NOT EXISTS court_links(dossier_id INTEGER NOT NULL, child TEXT NOT NULL, parent TEXT NOT NULL, relation TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(dossier_id,child,parent))')

def init_calculations(store):
    with store.write() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS court_calculations(
          id INTEGER PRIMARY KEY,dossier_id INTEGER NOT NULL,created TEXT NOT NULL,
          inputs TEXT NOT NULL,result TEXT NOT NULL,source_sha TEXT)""")

def save_calculation(store,did,inputs,result):
    with store.write() as c:
        row=c.execute("SELECT reviewed_doc FROM court_dossiers WHERE id=?",(did,)).fetchone()
        if not row:
            raise ValueError("Досье не найдено")
        return c.execute("INSERT INTO court_calculations(dossier_id,created,inputs,result,source_sha) VALUES(?,?,?,?,?)",
                         (did,datetime.now().isoformat(timespec="seconds"),
                          json.dumps(inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),row[0])).lastrowid

def calculations(store,did):
    rows=store.q("SELECT * FROM court_calculations WHERE dossier_id=? ORDER BY id DESC",(did,))
    return [dict(r,inputs=json.loads(r["inputs"]),result=json.loads(r["result"])) for r in rows]

def list_dossiers(store):
    return store.q("SELECT d.id,d.title,d.created,COUNT(x.sha) AS count FROM court_dossiers d LEFT JOIN court_dossier_docs x ON x.dossier_id=d.id GROUP BY d.id ORDER BY d.id DESC")

def _date(fields):
    s = next((str(f["value"]) for f in fields if f.get("name")=="Дата документа"),"")
    try:
        return datetime.strptime(s,"%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""

def _field(fields, name):
    return next((f.get("value","") for f in fields if f.get("name")==name),"")

def attach(store, batch, indices, title="", dossier_id=None, archive=False):
    if not isinstance(indices,list) or not indices or len(indices)>500 or any(type(i) is not int for i in indices):
        raise ValueError("Выберите от 1 до 500 документов")
    items={i.idx:i for i in batch.items}
    selected=[items.get(i) for i in dict.fromkeys(indices)]
    if any(not i or not i.sha or not i.fields or i.status=="error" for i in selected):
        raise ValueError("Выберите успешно прочитанные документы")
    if not isinstance(title,str) or (dossier_id is not None and type(dossier_id) is not int):
        raise ValueError("Неверные реквизиты досье")
    title=title.strip()
    if dossier_id is None and not 1<=len(title)<=200:
        raise ValueError("Введите название досье")
    if archive:
        for i in selected:
            archive_document(store,i)
    with store.write() as c:
        if dossier_id is None:
            dossier_id=c.execute("INSERT INTO court_dossiers(title,created) VALUES(?,?)",
                (title,datetime.now().isoformat(timespec="seconds"))).lastrowid
        elif not c.execute("SELECT id FROM court_dossiers WHERE id=?",(dossier_id,)).fetchone():
            raise ValueError("Досье не найдено")
        added=0
        for i in selected:
            data={"name":i.name,"batch":batch.id,"idx":i.idx,"doc_key":i.doc_key,
                  "doc_label":i.doc_label,"date":_date(i.fields),"case":_field(i.fields,"Номер дела"),
                  "court":_field(i.fields,"Суд"),"fields":i.fields,"reasons":i.reasons,
                  "text_hash":i.text_hash}
            # Не добавляем точные или текстовые копии в одно досье.
            if i.text_hash:
                hashes=[json.loads(x[0]).get("text_hash") for x in c.execute(
                    "SELECT data FROM court_dossier_docs WHERE dossier_id=?",(dossier_id,))]
                if i.text_hash in hashes:
                    continue
            added+=c.execute("INSERT OR IGNORE INTO court_dossier_docs(dossier_id,sha,data) VALUES(?,?,?)",
                      (dossier_id,i.sha,json.dumps(data,ensure_ascii=False))).rowcount
        if added:
            c.execute("UPDATE court_dossiers SET reviewed_doc=NULL,review_note='' WHERE id=?",(dossier_id,))
    return dossier_id

def detail(store, did):
    d=store.one("SELECT * FROM court_dossiers WHERE id=?",(did,))
    if not d:
        return None
    docs=[dict(json.loads(x["data"]),sha=x["sha"]) for x in store.q(
        "SELECT sha,data FROM court_dossier_docs WHERE dossier_id=?",(did,))]
    docs.sort(key=lambda x:(not x["date"],x["date"],x["name"]))
    case_groups={}
    for x in docs:
        case_groups.setdefault(norm_number(x["case"]) if x["case"] else "",[]).append(x["sha"])
    warnings=[]
    if len([k for k in case_groups if k])>1:
        warnings.append("Несколько номеров дел: проверьте связь инстанций или самостоятельность требований.")
    if "" in case_groups:
        warnings.append("Есть документы без установленного номера дела.")
    if any(not x["date"] for x in docs):
        warnings.append("Есть документы без установленной даты.")
    if any(any(f["name"]=="Начисление до исполнения" for f in x["fields"]) for x in docs):
        warnings.append("Найдены условия продолжающегося начисления: проверьте базу, ставку и платежи.")
    warnings.append("Хронология по извлечённым датам предварительная. Суммы не складываются автоматически.")
    for x in docs:
        x["has_original"] = archive_path(store,x["sha"]) is not None
    links=store.q("SELECT child,parent,relation,note FROM court_links WHERE dossier_id=?",(did,))
    payments=store.q("SELECT id,day,total,principal,reference FROM court_payments WHERE dossier_id=? ORDER BY day,id",(did,))
    return dict(d,documents=docs,warnings=warnings,calculations=calculations(store,did),links=links,payments=payments)

def review(store,did,sha,note):
    if not isinstance(note,str) or len(note)>5000:
        raise ValueError("Комментарий слишком длинный")
    with store.write() as c:
        if not c.execute("SELECT id FROM court_dossiers WHERE id=?",(did,)).fetchone():
            raise ValueError("Досье не найдено")
        if sha and not c.execute("SELECT 1 FROM court_dossier_docs WHERE dossier_id=? AND sha=?",(did,sha)).fetchone():
            raise ValueError("Документ не относится к досье")
        if sha and not note.strip():
            raise ValueError("Укажите основание ручного выбора")
        c.execute("UPDATE court_dossiers SET reviewed_doc=?,review_note=? WHERE id=?",(sha or None,note.strip(),did))

def _archive_root(store):
    return Path(store.path).resolve().parent / "court_originals"

def archive_path(store,sha):
    if not isinstance(sha,str) or not re.fullmatch(r"[a-f0-9]{64}",sha):
        return None
    p=_archive_root(store)/sha
    return p if p.is_file() and not p.is_symlink() else None

def archive_document(store,item):
    if archive_path(store,item.sha):
        return
    if not re.fullmatch(r"[a-f0-9]{64}",item.sha or ""):
        raise ValueError("Неверный отпечаток файла")
    if not os.path.isfile(item.path):
        raise ValueError("Оригинал недоступен: "+item.name)
    root=_archive_root(store)
    root.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=root)
    os.close(fd)
    try:
        shutil.copyfile(item.path,tmp)
        if file_sha256(tmp)!=item.sha:
            raise ValueError("Оригинал изменился: "+item.name)
        os.replace(tmp,root/item.sha)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def original(store,did,sha):
    row=store.one("SELECT data FROM court_dossier_docs WHERE dossier_id=? AND sha=?",(did,sha))
    path=archive_path(store,sha) if row else None
    if not path:
        return None
    return path,json.loads(row["data"])["name"]

RELATIONS=("upheld","changed","cancelled","remanded","enforcement","related")
def link(store,did,child,parent,relation,note):
    if not isinstance(note,str) or not note.strip() or len(note)>5000:
        raise ValueError("Укажите основание связи")
    if relation not in RELATIONS or child==parent:
        raise ValueError("Неверная связь")
    with store.write() as c:
        docs={x[0] for x in c.execute("SELECT sha FROM court_dossier_docs WHERE dossier_id=?",(did,))}
        if child not in docs or parent not in docs:
            raise ValueError("Акты должны быть в одном досье")
        graph={}
        for a,b in c.execute("SELECT child,parent FROM court_links WHERE dossier_id=?",(did,)):
            graph.setdefault(a,set()).add(b)
        pending=[parent]; visited=set()
        while pending:
            x=pending.pop()
            if x==child:
                raise ValueError("Связь образует цикл")
            if x not in visited:
                visited.add(x);pending.extend(graph.get(x,()))
        c.execute("INSERT INTO court_links VALUES(?,?,?,?,?) ON CONFLICT(dossier_id,child,parent) DO UPDATE SET relation=excluded.relation,note=excluded.note",
                  (did,child,parent,relation,note.strip()))
        c.execute("UPDATE court_dossiers SET reviewed_doc=NULL,review_note='' WHERE id=?",(did,))

def _money(value):
    try:
        v=Decimal(str(value).replace(",","."))
        if not v.is_finite() or v<0 or v>Decimal("1000000000000000") or v!=v.quantize(Decimal("0.01")):
            raise ValueError
        return v
    except (InvalidOperation,ValueError):
        raise ValueError("Сумма должна быть неотрицательной, не более двух знаков после запятой")

def add_payments(store,did,rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:
        raise ValueError("Передайте от 1 до 10000 платежей")
    prepared=[]
    for r in rows:
        day=date.fromisoformat(r["date"]).isoformat()
        total=_money(r["total"])
        principal=None if r.get("principal") in (None,"") else _money(r["principal"])
        ref=r.get("reference","")
        if not isinstance(ref,str) or not 1<=len(ref.strip())<=200 or total<=0 or (principal is not None and principal>total):
            raise ValueError("Проверьте сумму, распределение и уникальный номер платежа")
        prepared.append((did,day,str(total),None if principal is None else str(principal),ref.strip()))
    with store.write() as c:
        if not c.execute("SELECT id FROM court_dossiers WHERE id=?",(did,)).fetchone():
            raise ValueError("Досье не найдено")
        added=0
        for row in prepared:
            existing=c.execute("SELECT day,total,principal FROM court_payments WHERE dossier_id=? AND reference=?",(did,row[4])).fetchone()
            if existing:
                if (existing[0]!=row[1] or Decimal(existing[1])!=Decimal(row[2]) or
                    (None if existing[2] is None else Decimal(existing[2]))!=(None if row[3] is None else Decimal(row[3]))):
                    raise ValueError("Номер платежа уже существует с другими данными: "+row[4])
                continue
            c.execute("INSERT INTO court_payments(dossier_id,day,total,principal,reference) VALUES(?,?,?,?,?)",row)
            added+=1
    return added

def allocate_payment(store,did,pid,principal):
    """Подтверждение распределения; старые расчёты остаются историческими."""
    with store.write() as c:
        row=c.execute("SELECT total FROM court_payments WHERE dossier_id=? AND id=?",(did,pid)).fetchone()
        if not row:
            raise ValueError("Платёж не найден")
        v=None if principal in (None,"") else _money(principal)
        if v is not None and v>Decimal(row[0]):
            raise ValueError("Погашение базы превышает платёж")
        c.execute("UPDATE court_payments SET principal=? WHERE dossier_id=? AND id=?",(None if v is None else str(v),did,pid))
