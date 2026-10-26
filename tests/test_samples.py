"""Сквозная проверка на выдуманных документах.

Запуск:
  python tools/make_samples.py ПАПКА_С_ОБРАЗЦАМИ
  python tests/test_samples.py ПАПКА_С_ОБРАЗЦАМИ [--workers N] [--no-ocr]
Нужен tesseract и данные rus.traineddata (переменные RAZBOR_TESSERACT / RAZBOR_TESSDATA).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razbor.pipeline import Options, run  # noqa: E402


def norm(v):
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return f"{v:.2f}" if v == v.quantize(Decimal("0.01")) and "." not in str(v) else str(v)
    return str(v)


def main() -> int:
    folder = sys.argv[1]
    workers = 4
    use_ocr = "--no-ocr" not in sys.argv
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    exp = json.load(open(os.path.join(folder, "expected.json"), encoding="utf-8"))
    out = os.path.join(tempfile.mkdtemp(), "res.xlsx")
    s = run(Options(input_dir=folder, output_path=out, workers=workers, use_ocr=use_ocr, use_cache=False))
    by_rel = {}
    for r in s.results:
        rel = os.path.join(r.rel_dir, r.name).replace(os.sep, "/") if r.rel_dir else r.name
        by_rel[rel] = r
    fails = 0
    checks = 0
    for rel, e in exp.items():
        r = by_rel.get(rel)
        if r is None:
            print("НЕТ РЕЗУЛЬТАТА:", rel)
            fails += 1
            continue
        a = r.analysis
        print(f"\n{rel}  [{r.seconds:.1f} c, {r.method}, ocr={r.ocr_conf and round(r.ocr_conf)}]")
        if "error" in e:
            checks += 1
            if e["error"] in (r.error or ""):
                print("  ок: ошибка:", r.error)
            else:
                print("  ОШИБКА: ожидали ошибку", e["error"], "получили", repr(r.error))
                fails += 1
            continue
        if r.error:
            print("  ОШИБКА чтения:", r.error)
            fails += 1
            continue
        if "review" in e:
            checks += 1
            if any(e["review"] in x for x in r.reasons):
                print("  ок: проверить:", r.reasons)
            else:
                print("  ОШИБКА: ожидали причину", e["review"], "получили", r.reasons)
                fails += 1
            continue
        print(f"  тип: {a.doc_label} ({a.doc_key}, {a.score}); уточнение: {a.title!r}")
        for k, v in e.items():
            checks += 1
            if k == "type":
                if a.doc_key != v:
                    print(f"  ОШИБКА тип: ждали {v}, получили {a.doc_key}")
                    fails += 1
            elif k == "name_verdict":
                got_v = (r.name_check or {}).get("verdict")
                if got_v != v:
                    print(f"  ОШИБКА сверка названия: ждали {v}, получили {got_v}: {(r.name_check or {}).get('problems')}")
                    fails += 1
                else:
                    print("  ок: сверка названия:", (r.name_check or {}).get("title"), "->", (r.name_check or {}).get("suggest"))
            elif k == "flags":
                if sorted(a.flags) != sorted(v):
                    print(f"  ОШИБКА признаки: ждали {v}, получили {a.flags}")
                    fails += 1
            elif k == "dup_file_of" or k == "dup_text_of":
                other = by_rel[v]
                want = "копия файла" if k == "dup_file_of" else "тот же текст"
                if not (r.dup_group and r.dup_group == other.dup_group and want in r.dup_kind):
                    print(f"  ОШИБКА дубль: {r.dup_group} {r.dup_kind!r} vs {other.dup_group}")
                    fails += 1
            else:
                hit = a.fields.get(k)
                got = norm(hit.value) if hit is not None else None
                ok_ = got == v or (v.endswith("*") and got is not None and got.startswith(v[:-1]))
                if not ok_:
                    print(f"  ОШИБКА {k}: ждали {v!r}, получили {got!r}")
                    fails += 1
        shown = {k: (norm(h.value)) for k, h in a.fields.items() if k not in e}
        if shown:
            print("  ещё найдено:", shown)
        if r.reasons:
            print("  проверить:", r.reasons)
    print(f"\nПроверок: {checks}, расхождений: {fails}")
    print("Excel:", out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
