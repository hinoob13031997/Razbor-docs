"""Где лежат Tesseract и его языковые данные; безопасные пути для Windows."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    """Папка программы (рядом с .exe или с app.py)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    # PyInstaller (onedir) кладёт данные в _internal, рядом с exe
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def is_ascii(p: str | os.PathLike) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def ascii_safe(path: str | os.PathLike) -> str:
    """Путь, который понимает tesseract.exe даже при кириллице в имени папки.

    Tesseract под Windows плохо открывает файлы по путям с русскими буквами
    (типичный случай: C:\\Users\\Иванов\\...). Короткое имя 8.3 состоит только
    из латиницы и цифр. Если оно недоступно, вернём исходный путь.
    """
    p = str(path)
    if is_ascii(p) or os.name != "nt":
        return p
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)  # type: ignore[attr-defined]
        if 0 < n < 1024 and is_ascii(buf.value):
            return buf.value
    except Exception:
        pass
    return p


def find_tesseract() -> str | None:
    """Путь к tesseract (exe). Порядок: переменная, рядом с программой, системный."""
    env = os.environ.get("RAZBOR_TESSERACT")
    cands: list[Path] = []
    if env:
        cands.append(Path(env))
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"
    for base in (app_dir(), _bundle_dir()):
        cands.append(base / "tesseract" / exe)
        cands.append(base / "_internal" / "tesseract" / exe)
    for c in cands:
        if c.is_file():
            return str(c)
    return shutil.which("tesseract")


def find_tessdata(tesseract_exe: str | None) -> str | None:
    """Папка с rus.traineddata и eng.traineddata."""
    cands: list[Path] = []
    env = os.environ.get("RAZBOR_TESSDATA") or os.environ.get("TESSDATA_PREFIX")
    if env:
        cands.append(Path(env))
        cands.append(Path(env) / "tessdata")
    for base in (app_dir(), _bundle_dir()):
        cands.append(base / "tessdata")
        cands.append(base / "tesseract" / "tessdata")
        cands.append(base / "_internal" / "tessdata")
        cands.append(base / "_internal" / "tesseract" / "tessdata")
    if tesseract_exe:
        cands.append(Path(tesseract_exe).parent / "tessdata")
    for c in cands:
        if (c / "rus.traineddata").is_file():
            return str(c)
    for c in cands:
        if c.is_dir() and any(c.glob("*.traineddata")):
            return str(c)
    return None
