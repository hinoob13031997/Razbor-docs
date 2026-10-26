"""Проверка новой программы: портфель, привязка, сроки, шаблоны, сервер. Запуск: python tests/test_app.py"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from razbor import batch, crm, rules, templates  # noqa: E402
from razbor.reader import OcrEngine  # noqa: E402
from razbor.store import Store  # noqa: E402

fails = 0


def check(cond: bool, text: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + text)
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_demo_crm.py"), os.path.join(tmp, "crm.csv")], check=True)
st = Store(os.path.join(tmp, "r.sqlite"))
r = crm.import_crm(st, os.path.join(tmp, "crm.csv"))
check(r["rows"] == 25 and r["added"] == 25, "импорт CRM: 25 договоров")
r2 = crm.import_crm(st, os.path.join(tmp, "crm.csv"))
check(r2["added"] == 0 and r2["updated"] == 25, "повторный импорт обновляет, а не дублирует")
rules.recalc_contracts(st)
check(st.scalar("SELECT COUNT(*) FROM deadlines") > 0, "сроки посчитаны")
check(rules.fresh_level(None, date.today(), rules.DEFAULTS)["level"] == "none", "свежесть: не разбирался")
today = date.today()
check(rules.fresh_level(today.isoformat(), today, rules.DEFAULTS)["level"] == "fresh", "свежесть: сегодня актуально")
check(rules.fresh_level("2020-01-01", today, rules.DEFAULTS)["level"] == "stale", "свежесть: давно устарело")

ocr = OcrEngine(lang="rus+eng")
import shutil  # noqa: E402

work = os.path.join(tmp, "upload")
shutil.copytree(os.path.join(ROOT, "tests", "samples"), work)
b = batch.Batch(id="t1", dir=work, created="x")
batch.read_batch(st, b, os.path.join(tmp, "cache.sqlite"), use_ocr=ocr.available)
byname = {i.name: i for i in b.items}
num = lambda n: (st.contract(byname[n].chosen) or {}).get("number") if byname[n].chosen else None  # noqa: E731
check(num("01_sud_prikaz.pdf") == "ЗМ-2022/778", "судебный приказ привязан по номеру договора")
check(num("02_reshenie.pdf") == "КД-100200", "решение привязано по ФИО и дате рождения")
check(num("04_ispolnitelny_list.pdf") == "КД-100200", "исполнительный лист привязан по номеру дела")
check(byname["14_sud_prikaz_kopiya_tekst.pdf"].status == "seen", "копия с тем же текстом отмечена как повтор")
check(byname["21_broken.pdf"].status == "error", "битый файл не ломает пачку")
check(byname["11_bankrotstvo.pdf"].status in ("confirm", "ok"), "банкротство найдено в портфеле")
res = batch.write_batch(st, b, os.path.join(tmp, "docs"))
check(res["documents"] >= 4, "документы записаны в карточки")
c = st.one("SELECT * FROM contracts WHERE number='КД-100200'")
check(c["docs_count"] >= 3 and c["last_doc_at"] == today.isoformat(), "в карточке видно, когда разбирали")
if ocr.available:
    check(c["sit"] == "ip_end" and c["next_dl_name"].startswith("Повторно"), "ИП окончено: срок повторного предъявления")
check(not os.path.exists(os.path.join(work, "01_sud_prikaz.pdf")), "копия загруженного файла после записи удалена")
check(st.scalar("SELECT COUNT(*) FROM documents WHERE stored IS NOT NULL") == 0, "копии документов в программе не хранятся")
b2 = batch.Batch(id="t2", dir=os.path.join(ROOT, "tests", "samples"), created="x")
batch.read_batch(st, b2, os.path.join(tmp, "cache.sqlite"), use_ocr=False)
check(byname["01_sud_prikaz.pdf"].written and {i.name: i for i in b2.items}["01_sud_prikaz.pdf"].status == "seen",
      "повторная загрузка того же файла: уже разобран")
# подключённая папка: файлы читаются на месте
from razbor import folders  # noqa: E402

fold = os.path.join(tmp, "folder")
shutil.copytree(os.path.join(ROOT, "tests", "samples"), fold)
st2 = Store(os.path.join(tmp, "r2.sqlite"))
crm.import_crm(st2, os.path.join(tmp, "crm.csv"))
rules.recalc_contracts(st2)
src = folders.add_source(st2, fold)
r1 = folders.scan_source(st2, src["id"], os.path.join(tmp, "batches"), lambda *a: None, __import__("threading").Event(),
                         use_ocr=ocr.available)
check(r1["new"] == 22 and r1["auto"] >= 5, f"папка: прочитано {r1['new']}, записано сразу {r1['auto']}")
check(st2.scalar("SELECT COUNT(*) FROM documents WHERE stored LIKE ?", (fold + "%",)) == r1["auto"],
      "папка: в карточке путь к исходному файлу, без копии")
check(os.path.exists(os.path.join(fold, "01_sud_prikaz.pdf")), "папка: исходные файлы на месте")
r2 = folders.scan_source(st2, src["id"], os.path.join(tmp, "batches"), lambda *a: None, __import__("threading").Event())
check(r2["new"] == 0, "папка: повторная проверка не читает старые файлы")
shutil.copy(os.path.join(ROOT, "tests", "samples", "07_kreditny_dogovor.pdf"), os.path.join(fold, "новый.pdf"))
r3 = folders.scan_source(st2, src["id"], os.path.join(tmp, "batches"), lambda *a: None, __import__("threading").Event())
check(r3["new"] == 1 and r3["seen"] == 1, "папка: новый файл найден (и опознан как повтор)")
cid = c["id"]
t = templates.render(st, cid, "ip_new")
check(t["filled"] > 5 and any(m["key"] == "org" for m in t["missing"]), "шаблон: подставлено и показано, чего не хватает")
st.set_manual(cid, "org", "ООО Тест")
check(not any(m["key"] == "org" for m in templates.render(st, cid, "ip_new")["missing"]), "шаблон: ручное поле подставляется")
p = templates.make_docx(st, cid, "ip_new", tmp)
z = zipfile.ZipFile(p)
check("ООО Тест" in z.read("word/document.xml").decode("utf-8") and z.testzip() is None, "Word-файл собран")

# сумма: в CRM только остаток с госпошлиной
import csv as _csv  # noqa: E402

gp = os.path.join(tmp, "gp.csv")
with open(gp, "w", newline="", encoding="utf-8") as f:
    w = _csv.writer(f, delimiter=";")
    w.writerow(["id", "number_dog", "fio", "ocs_sum", "gospohlina"])
    w.writerow(["1", "ГП-1", "Тестов Тест Тестович", "10500.00", "500"])
    w.writerow(["2", "ГП-2", "Тестов Тест Тестович", "10500.00", "NULL"])
st3 = Store(os.path.join(tmp, "r3.sqlite"))
crm.import_crm(st3, gp)
c1 = st3.one("SELECT id FROM contracts WHERE number='ГП-1'")["id"]
c2 = st3.one("SELECT id FROM contracts WHERE number='ГП-2'")["id"]
r1 = templates.render(st3, c1, "claim")
check("10 000,00" in "".join(x["text"] for x in r1["body"]), "остаток с ГП минус ГП = сумма долга в иске")
r2 = templates.render(st3, c2, "claim")
check(any(m["key"] == "amount" for m in r2["missing"]) and r2["notes"], "без госпошлины сумма не подставляется, есть подсказка")

# сервер целиком
env = dict(os.environ, RAZBOR_DATA=os.path.join(tmp, "srv"), RAZBOR_PRINT_URL="1")
pr = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py"), "--no-browser"], env=env, stdout=subprocess.PIPE, text=True)
try:
    url = pr.stdout.readline().strip()
    base = url.split("/?")[0]
    check(url.startswith("http://127.0.0.1:"), "сервер слушает только 127.0.0.1")
    try:
        urllib.request.urlopen(base + "/api/summary")
        check(False, "без ключа доступа API закрыт")
    except urllib.error.HTTPError as e:
        check(e.code == 403, "без ключа доступа API закрыт")
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    op.open(url)
    d = json.load(op.open(base + "/api/summary"))
    check(d["total"] == 0, "новый сервер: портфель пуст")
    rq = urllib.request.Request(base + "/api/summary", headers={"Host": "evil.example"})
    try:
        op.open(rq)
        check(False, "чужой Host отклоняется")
    except urllib.error.HTTPError as e:
        check(e.code == 403, "чужой Host отклоняется")
    op.open(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST"))
    pr.wait(timeout=10)
    check(pr.returncode == 0, "программа закрывается по кнопке")
finally:
    if pr.poll() is None:
        pr.kill()
print("\nОШИБОК:", fails)
sys.exit(1 if fails else 0)
