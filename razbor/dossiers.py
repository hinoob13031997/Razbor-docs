"""Локальные судебные досье: ручное объединение актов, без обязательной CRM."""
import json
import re
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

def attach(store, batch, indices, title="", dossier_id=None):
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
    return dict(d,documents=docs,warnings=warnings)

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
