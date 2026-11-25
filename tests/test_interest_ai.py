"""Синтетические проверки модулей, без сети и персональных данных."""
import sys
from pathlib import Path
from datetime import date
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from razbor.interest import calculate
from razbor import local_ai

class InterestTest(unittest.TestCase):
    def test_fixed_with_payment(self):
        result = calculate(365000, date(2025,1,1), date(2025,1,30),
                           fixed_rate=10, principal_payments=[(date(2025,1,10), 73000)])
        self.assertEqual(result["interest"], "2600.00")
        self.assertEqual(result["remaining_principal"], "292000.00")
        self.assertEqual(len(result["segments"]), 2)

    def test_leap_and_rate_change(self):
        r = calculate(366000, date(2024,1,1), date(2024,1,2),
                      rates=[(date(2024,1,1),10),(date(2024,1,2),20)],
                      verified_through=date(2024,1,2))
        self.assertEqual(r["interest"], "300.00")

    def test_missing_coverage(self):
        with self.assertRaises(ValueError):
            calculate(100, date(2025,1,1), date(2025,1,2),
                      rates=[(date(2025,1,1),10)],verified_through=date(2025,1,1))

    def test_overpayment(self):
        with self.assertRaises(ValueError):
            calculate(100, date(2025,1,1), date(2025,1,2),
                      fixed_rate=10,principal_payments=[(date(2025,1,1),101)])

class AITest(unittest.TestCase):
    def test_quote_validation(self):
        import json
        quote = "Взыскать основной долг"
        findings = [
            {"kind":"award","value":"долг","page":1,"quote":quote},
            {"kind":"award","value":"выдумка","page":1,"quote":"Не существующая цитата"},
            {"kind":"award","value":"не та страница","page":2,"quote":quote},
        ]
        with patch.object(local_ai,"installed_models",return_value=["qwen3:8b"]), \
             patch.object(local_ai,"_request",return_value={"message":{"content":json.dumps({"findings":findings})}}):
            r = local_ai.analyze_chunk({1:quote}, model="qwen3:8b")
        self.assertEqual(len(r["findings"]),1)
        self.assertEqual(r["rejected"],2)
        self.assertTrue(r["requires_review"])

if __name__ == "__main__":
    unittest.main()
