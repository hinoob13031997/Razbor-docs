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

    def test_cover_letter_and_decision(self):
        from datetime import date
        from razbor.reader import PageText, ReadResult
        from razbor.analyze import analyze
        from razbor import namecheck, matcher
        cover="04.02.2026\nТестовый районный суд направляет в Ваш адрес копию решения."
        decision="Дело № 2-159/2026\nРЕШЕНИЕ\nИменем Российской Федерации\n28 января 2026 года\nТестовый районный суд, рассмотрев дело по иску кредитора.\nЗаёмщику предоставлен кредит в сумме 615 000 руб.\nСуд РЕШИЛ: Взыскать основной долг в размере 44 917,93 руб.; неустойку в размере 24 659,94 руб.; расходы по оплате услуг представителя 10 000 руб.; государственную пошлину 8 039 руб. В остальной части отказать."
        a=analyze(ReadResult(pages=[PageText(1,cover,"text"),PageText(2,decision,"text")],page_count=2))
        self.assertEqual(a.doc_key,"decision")
        self.assertEqual(a.fields["Дата документа"].value,date(2026,1,28))
        self.assertEqual(a.pages["Дата документа"],2)
        self.assertNotIn("Сумма основная",a.fields)
        self.assertNotIn("amount_granted",matcher.facts_from(a))
        self.assertNotIn("615",a.summary)
        self.assertIn("отказ",a.fields["Результат рассмотрения"].value)
        self.assertEqual(namecheck.check_name("РЕШЕНИЕ_от_28.01.2026.pdf",a).verdict,"ok")
        self.assertTrue(any("госпошлина" in k for k in a.fields if k.startswith("Сумма в резолютивной части")))
        self.assertTrue(any("расходы представителя" in k for k in a.fields if k.startswith("Сумма в резолютивной части")))

    def test_multiple_awards(self):
        from decimal import Decimal
        text = ("РЕШИЛ:\nВзыскать проценты за пользование кредитом за период с 01.01.2024 по 31.01.2024 в размере 1 200 руб.; "
                "неустойку за просрочку уплаты основного долга в размере 2 500 руб.; "
                "неустойку за просрочку уплаты процентов в размере 300 руб.; "
                "проценты за пользование кредитом в размере 12% годовых за период с 01.02.2024 по дату полного погашения задолженности на остаток основного долга в сумме 100 000 рублей; "
                "неустойку за просрочку уплаты основного долга за период с 01.02.2024 по дату полного погашения задолженности на остаток основного долга в размере 0.1% за каждый день просрочки; "
                "неустойку за просрочку уплаты процентов за период с 01.02.2024 по дату полного погашения задолженности по процентам в размере 0.1% за каждый день просрочки с суммы задолженности по процентам, а также 4 000 рублей расходы по оплате услуг представителя, 2 000 рублей расходы по оплате госпошлины. В остальной части отказать.")
        r=extract_review(Doc([text]))
        self.assertEqual(r["Итого распознанных фиксированных взысканий"].value,Decimal("10000"))
        self.assertEqual(len([k for k in r if k.startswith("Фиксированное взыскание ")]),5)
        self.assertEqual(len([k for k in r if k.startswith("Начисление до исполнения")]),3)
        self.assertIn("100 000",r["Начисление до исполнения"].value)
        self.assertIn("в день",r["Начисление до исполнения 2"].value)

    def test_classification(self):
        self.assertEqual(classify.classify("АПЕЛЛЯЦИОННОЕ ОПРЕДЕЛЕНИЕ\nСудебная коллегия рассмотрела жалобу.").key,"appeal")
        self.assertEqual(classify.classify("КАССАЦИОННОЕ ОПРЕДЕЛЕНИЕ\nСудебная коллегия рассмотрела жалобу.").key,"cassation")

if __name__=="__main__":
    unittest.main()
