import io
import os
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
from razbor import advanced, dossiers
from razbor.store import Store

class AdvancedTest(unittest.TestCase):
    def test_import(self):
        r=advanced.payment_rows("Дата;Сумма;В базу;Номер платежа\n01.02.2025;15000;10000;A1\n".encode(),"payments.csv")
        self.assertEqual(r[0]["date"],"2025-02-01")
        self.assertEqual(r[0]["reference"],"A1")
        from openpyxl import Workbook
        w=Workbook()
        w.active.append(["Дата","Сумма","В базу","Номер платежа"])
        w.active.append(["2025-02-01",15000,None,"A2"])
        b=io.BytesIO()
        w.save(b)
        self.assertEqual(advanced.payment_rows(b.getvalue(),"a.xlsx")[0]["principal"],"")
    def test_rates_backup_and_practice(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=Store(os.path.join(tmp,"test.sqlite"))
            dossiers.init(st)
            advanced.init(st)
            advanced.save_rates(st,[{"date":"2025-01-01","rate":"21"}],"2025-01-31","Учебный пример")
            self.assertEqual(advanced.rates(st)["rows"][0]["rate"],"21")
            with self.assertRaises(ValueError):
                advanced.practice_save(st,"Обзор","Категория","Текст",False)
            advanced.practice_save(st,"Обзор","Категория","Текст",True)
            self.assertEqual(len(advanced.practice_list(st)),1)
            self.assertNotIn("Иванов",advanced.redact("Иванов обратился",["Иванов"]))
            p=advanced.backup(st)
            try:
                with zipfile.ZipFile(p) as z:
                    self.assertIn("razbor.sqlite",z.namelist())
            finally:
                os.unlink(p)
            st.conn().close()
    def test_indexation(self):
        r=advanced.indexation("100000",[{"month":"2025-01","index":"101"},{"month":"2025-02","index":"102"}])
        self.assertEqual(Decimal(r["difference"]),Decimal("3020"))
        with self.assertRaises(ValueError):
            advanced.indexation("100000",[{"month":"2025-01","index":"101"},{"month":"2025-03","index":"102"}])

if __name__=="__main__":
    unittest.main()
