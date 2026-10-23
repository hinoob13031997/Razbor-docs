"""Показать, какой текст программа получает из файла (для отладки правил).

python tools/dump_text.py файл.pdf [--no-ocr]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razbor.reader import OcrEngine, read_document  # noqa: E402


def main():
    path = sys.argv[1]
    use_ocr = "--no-ocr" not in sys.argv
    ocr = OcrEngine() if use_ocr else None
    t = time.time()
    rr = read_document(path, ocr=ocr, use_ocr=use_ocr)
    print(f"# {rr.method}, страниц {rr.page_count}, OCR {rr.ocr_conf}, {time.time() - t:.1f} c, {rr.warnings}")
    for p in rr.pages:
        print(f"--- стр. {p.index} [{p.method} {p.conf}] ---")
        print(p.text)


if __name__ == "__main__":
    main()
