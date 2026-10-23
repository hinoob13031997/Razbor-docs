"""Сборка папки RazborDocs для Windows (запускать на Windows).

Нужно: Python 3.10+, установленный Tesseract (сборка UB Mannheim) и интернет на время сборки.
  pip install -r requirements.txt -r requirements-build.txt
  python tools/build_windows.py [--tesseract "C:\\Program Files\\Tesseract-OCR"] [--tessdata ПАПКА]

Готовая программа: dist\\RazborDocs\\RazborDocs.exe (и архив dist\\RazborDocs.zip).
В готовой программе интернет уже не нужен.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata"
DEFAULT_TESS = [r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR"]


def find_tesseract(arg: str | None) -> str:
    for c in ([arg] if arg else []) + DEFAULT_TESS:
        if c and os.path.isfile(os.path.join(c, "tesseract.exe")):
            return c
    sys.exit("Не найден Tesseract. Установите его (UB Mannheim) или укажите --tesseract ПАПКА")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tesseract")
    ap.add_argument("--tessdata", help="папка с rus/eng/osd .traineddata (иначе скачаем tessdata_fast)")
    args = ap.parse_args()

    tess = find_tesseract(args.tesseract)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "razbor.spec"], cwd=ROOT)

    out = os.path.join(ROOT, "dist", "RazborDocs")
    if not os.path.isdir(out):
        sys.exit("PyInstaller не создал папку dist\\RazborDocs")

    # tesseract.exe и его библиотеки (без документации и деинсталлятора)
    dst = os.path.join(out, "tesseract")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(tess):
        p = os.path.join(tess, name)
        if os.path.isfile(p) and (name.lower() == "tesseract.exe" or name.lower().endswith(".dll")):
            shutil.copy2(p, dst)

    # языковые данные
    td = os.path.join(out, "tessdata")
    os.makedirs(td, exist_ok=True)
    for lang in ("rus", "eng", "osd"):
        target = os.path.join(td, f"{lang}.traineddata")
        src = None
        for base in (args.tessdata, os.path.join(tess, "tessdata")):
            if base and os.path.isfile(os.path.join(base, f"{lang}.traineddata")):
                src = os.path.join(base, f"{lang}.traineddata")
                break
        if src:
            shutil.copy2(src, target)
        else:
            print("Скачиваю", lang)
            urllib.request.urlretrieve(TESSDATA_URL.format(lang), target)
        if os.path.getsize(target) < 100_000:
            sys.exit(f"Файл {target} слишком маленький: загрузка не удалась")

    for name in ("polya.example.json", "README.md"):
        if os.path.isfile(os.path.join(ROOT, name)):
            shutil.copy2(os.path.join(ROOT, name), out)

    zip_path = shutil.make_archive(os.path.join(ROOT, "dist", "RazborDocs"), "zip", os.path.join(ROOT, "dist"), "RazborDocs")
    print("Готово:", out)
    print("Архив:", zip_path)


if __name__ == "__main__":
    main()
