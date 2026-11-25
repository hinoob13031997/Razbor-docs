"""Синтетические тесты новых досье, без OCR, интернета и ИИ."""
import os
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
from razbor import dossiers, classify
from razbor.batch import Batch, Item
from razbor.store import Store
from razbor.fields import Doc
from razbor.court_review import extract_review

class DossierTest(unittest.TestCase):
    def test_dossier_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=Store(os.path.join(tmp,"test.sqlite"))
            dossiers.init(st)
            b=Batch("1234567890",tmp,"2026-01-01")
            def item(idx,sha,day):
                return Item(idx=idx,rel=sha+".pdf",name=sha+".pdf",path=sha+".pdf",
                    sha=sha,text_hash=sha,doc_key="decision",doc_label="Решение суда",
                    status="nf",fields=[{"name":"Дата документа","value":day},
                    {"name":"Номер дела","value":"2-1/2026"}])
            b.items=[item(0,"later","20.01.2026"),item(1,"earlier","01.01.2026")]
            did=dossiers.attach(st,b,[0,1],"Учебное дело")
            self.assertEqual([x["sha"] for x in dossiers.detail(st,did)["documents"]],["earlier","later"])
            dossiers.review(st,did,"later","Проверено по оригиналу")
            dossiers.attach(st,b,[0],dossier_id=did)
            self.assertEqual(dossiers.detail(st,did)["reviewed_doc"],"later")
            b.items.append(item(2,"new","01.02.2026"))
            dossiers.attach(st,b,[2],dossier_id=did)
            self.assertIsNone(dossiers.detail(st,did)["reviewed_doc"])
            self.assertEqual(dossiers.list_dossiers(st)[0]["count"],3)
            with self.assertRaises(ValueError):
                dossiers.review(st,did,"foreign","Проверено")
            st.conn().close()

    def test_archive_links_and_payments(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=Store(os.path.join(tmp,"test.sqlite"))
            dossiers.init(st)
            b=Batch("1234567890",tmp,"2026-01-01")
            for idx in range(2):
                p=Path(tmp)/f"{idx}.pdf"
                payload=f"synthetic {idx}".encode()
                p.write_bytes(payload)
                b.items.append(Item(idx=idx,rel=p.name,name=p.name,path=str(p),
                    sha=hashlib.sha256(payload).hexdigest(),status="nf",fields=[{"name":"Дата документа","value":"01.01.2026"}]))
            did=dossiers.attach(st,b,[0,1],"Архив",archive=True)
            a,z=[i.sha for i in b.items]
            Path(b.items[0].path).unlink()
            self.assertEqual(dossiers.original(st,did,a)[0].read_bytes(),b"synthetic 0")
            self.assertIsNone(dossiers.original(st,did+1,a))
            dossiers.review(st,did,a,"Проверено")
            dossiers.link(st,did,z,a,"changed","Страница 2")
            self.assertIsNone(dossiers.detail(st,did)["reviewed_doc"])
            with self.assertRaises(ValueError):
                dossiers.link(st,did,a,z,"changed","Цикл")
            rows=[{"date":"2026-01-01","total":"100","principal":"60","reference":"1"},
                  {"date":"2026-01-02","total":"200","reference":"2"}]
            self.assertEqual(dossiers.add_payments(st,did,rows),2)
            self.assertEqual(dossiers.add_payments(st,did,rows),0)
            self.assertIsNone(dossiers.detail(st,did)["payments"][1]["principal"])
            with self.assertRaises(ValueError):
                dossiers.add_payments(st,did,[dict(rows[0],principal="101")])
            with self.assertRaises(ValueError):
                dossiers.add_payments(st,did,[dict(rows[0],total="110")])
            st.conn().close()

    def test_review(self):
        doc=Doc(["Дело № 2-123/2026\nИстец просил взыскать 200000 рублей.\nРЕШИЛ:\nВзыскать проценты 10% годовых по день фактического исполнения обязательств."])
        r=extract_review(doc)
        self.assertIn("Начисление до исполнения",r)
        self.assertIn("Признак результата: Взыскание",r)
        self.assertNotIn("Начисление до исполнения",extract_review(Doc(["Истец просил взыскать проценты по день фактического исполнения."])))

    def test_classification(self):
        self.assertEqual(classify.classify("АПЕЛЛЯЦИОННОЕ ОПРЕДЕЛЕНИЕ\nСудебная коллегия рассмотрела жалобу.").key,"appeal")
        self.assertEqual(classify.classify("КАССАЦИОННОЕ ОПРЕДЕЛЕНИЕ\nСудебная коллегия рассмотрела жалобу.").key,"cassation")

if __name__=="__main__":
    unittest.main()
