"""Чтение файлов: текстовый слой PDF, распознавание сканов (Tesseract), изображения.

Ничего не отправляется в сеть. Картинки страниц передаются в tesseract через stdin,
поэтому на диск (во временную папку) страницы не пишутся.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import zlib
from dataclasses import dataclass, field

from . import READER_VERSION
from .paths import ascii_safe, find_tessdata, find_tesseract
from .textutil import looks_garbled, normalize

# pdfium не потокобезопасен: все обращения к нему идут под этим замком.
# Тяжёлая часть (распознавание) выполняется вне замка и идёт параллельно.
PDFIUM_LOCK = threading.Lock()

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_EXT = {".pdf"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT

MIN_TEXT_CHARS = 25  # меньше этого на странице: считаем страницу сканом


class ReadError(Exception):
    """Файл нельзя прочитать (понятное пользователю сообщение)."""


class Cancelled(Exception):
    pass


@dataclass
class PageText:
    index: int  # номер страницы, с 1
    text: str
    method: str  # text | ocr | empty | skipped
    conf: float | None = None


@dataclass
class ReadResult:
    pages: list[PageText]
    page_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def pages_read(self) -> int:
        return sum(1 for p in self.pages if p.method in ("text", "ocr"))

    @property
    def method(self) -> str:
        kinds = {p.method for p in self.pages if p.method in ("text", "ocr")}
        if kinds == {"text"}:
            return "текст"
        if kinds == {"ocr"}:
            return "скан (OCR)"
        if kinds == {"text", "ocr"}:
            return "смешанный"
        return "нет текста"

    @property
    def ocr_conf(self) -> float | None:
        vals = [(p.conf, len(p.text)) for p in self.pages if p.method == "ocr" and p.conf is not None]
        if not vals:
            return None
        total = sum(w for _, w in vals) or 1
        return sum(c * w for c, w in vals) / total


# ---------------------------------------------------------------- OCR


class OcrEngine:
    def __init__(self, exe: str | None = None, tessdata: str | None = None,
                 lang: str = "rus+eng", timeout: int = 240):
        self.exe = exe or find_tesseract()
        self.tessdata = tessdata or find_tessdata(self.exe)
        self.lang = lang
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.exe) and bool(self.tessdata)

    def problem(self) -> str | None:
        if not self.exe:
            return "Не найден tesseract (модуль распознавания сканов)."
        if not self.tessdata:
            return "Не найдены языковые данные tesseract (rus.traineddata)."
        return None

    def _run(self, png: bytes) -> tuple[str, float, int]:
        cmd = [
            ascii_safe(self.exe),  # type: ignore[arg-type]
            "stdin", "stdout",
            "-l", self.lang,
            "--tessdata-dir", ascii_safe(self.tessdata),  # type: ignore[arg-type]
            "--psm", "3",
            "-c", "tessedit_create_tsv=1",
        ]
        env = dict(os.environ)
        env["OMP_THREAD_LIMIT"] = "1"  # несколько копий tesseract идут параллельно
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            proc = subprocess.run(cmd, input=png, capture_output=True, env=env,
                                  timeout=self.timeout, **kwargs)
        except subprocess.TimeoutExpired:
            raise ReadError("Распознавание страницы заняло слишком много времени")
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise ReadError(f"Ошибка tesseract: {tail[0][:200]}")
        return _parse_tsv(proc.stdout.decode("utf-8", "replace"))

    def _osd_angle(self, img) -> int | None:
        """Угол, на который надо повернуть страницу по часовой стрелке (0/90/180/270)."""
        small = img.copy()
        small.thumbnail((1800, 1800))
        cmd = [
            ascii_safe(self.exe),  # type: ignore[arg-type]
            "stdin", "stdout", "--tessdata-dir", ascii_safe(self.tessdata),  # type: ignore[arg-type]
            "--psm", "0",
        ]
        env = dict(os.environ)
        env["OMP_THREAD_LIMIT"] = "1"
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000
        try:
            proc = subprocess.run(cmd, input=_to_png(small), capture_output=True, env=env,
                                  timeout=90, **kwargs)
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0:
            return None
        out = proc.stdout.decode("utf-8", "replace")
        m = re.search(r"Rotate:\s*(\d+)", out)
        c = re.search(r"Orientation confidence:\s*([\d.]+)", out)
        if not m or (c and float(c.group(1)) < 1.5):
            return None
        return int(m.group(1)) % 360

    def recognize(self, img, autorotate: bool = True) -> tuple[str, float]:
        """Распознать PIL-картинку. Возвращает (текст, средняя уверенность 0..100).

        Хорошие страницы читаются одним проходом. Если результат слабый, проверяем,
        не повёрнута ли страница, и пробуем убрать «шум» медианным фильтром.
        """
        from PIL import ImageFilter, ImageOps

        img = img.convert("L")
        try:
            img = ImageOps.autocontrast(img, cutoff=0)
        except Exception:
            pass
        t0 = time.monotonic()
        best = self._run(_to_png(img))  # (text, conf, words)
        if not autorotate or (best[1] >= 60 and best[2] >= 15):
            return best[0], best[1]
        if time.monotonic() - t0 > 40:  # страница-«каша»: повторные попытки только съедят время
            return best[0], best[1]

        def score(r):
            return r[2] * max(r[1], 1.0)

        angle = self._osd_angle(img)
        if angle in (90, 180, 270):
            rotated = img.rotate(-angle, expand=True, fillcolor=255)
            cand = self._run(_to_png(rotated))
            if score(cand) > score(best):
                best, img = cand, rotated
        if best[1] < 55:
            try:
                clean = img.filter(ImageFilter.MedianFilter(3))
                cand = self._run(_to_png(clean))
                if score(cand) > score(best):
                    best = cand
            except ReadError:
                pass
        return best[0], best[1]


def _to_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _parse_tsv(tsv: str) -> tuple[str, float, int]:
    lines: dict[tuple[int, int, int], list[str]] = {}
    order: list[tuple[int, int, int]] = []
    confs: list[float] = []
    for row in tsv.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if not word:
            continue
        try:
            conf = float(parts[10])
            key = (int(parts[2]), int(parts[3]), int(parts[4]))
        except ValueError:
            continue
        if conf >= 0:
            confs.append(conf)
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
    out: list[str] = []
    prev_par = None
    for key in order:
        par = key[:2]
        if prev_par is not None and par != prev_par:
            out.append("")
        prev_par = par
        out.append(" ".join(lines[key]))
    mean = sum(confs) / len(confs) if confs else 0.0
    return "\n".join(out), mean, len(confs)


# ---------------------------------------------------------------- чтение файла


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pages_to_ocr(n: int, limit: int) -> set[int]:
    """Какие страницы (индексы с 0) распознавать: первые и несколько последних."""
    if limit <= 0 or n <= limit:
        return set(range(n))
    tail = min(2, max(1, limit // 4))
    head = limit - tail
    return set(range(head)) | set(range(n - tail, n))


def read_document(path: str, *, ocr: OcrEngine | None, use_ocr: bool = True,
                  max_ocr_pages: int = 10, dpi: int = 250,
                  cancel: threading.Event | None = None) -> ReadResult:
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXT:
        return _read_pdf(path, ocr, use_ocr, max_ocr_pages, dpi, cancel)
    if ext in IMAGE_EXT:
        return _read_image(path, ocr, use_ocr, max_ocr_pages, cancel)
    raise ReadError("Неподдерживаемый тип файла")


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _read_pdf(path, ocr, use_ocr, max_ocr_pages, dpi, cancel) -> ReadResult:
    import pypdfium2 as pdfium

    result_pages: dict[int, PageText] = {}
    warnings: list[str] = []
    need_ocr: list[int] = []

    try:
        fobj = open(path, "rb")
    except OSError as e:
        raise ReadError(f"Не удаётся открыть файл: {e.strerror or e}")
    with fobj:
        with PDFIUM_LOCK:
            try:
                pdf = pdfium.PdfDocument(fobj)
            except pdfium.PdfiumError as e:
                msg = str(e).lower()
                if "password" in msg:
                    raise ReadError("PDF защищён паролем")
                raise ReadError("Файл повреждён или это не PDF")
            n = len(pdf)
            try:
                for i in range(n):
                    page = pdf[i]
                    try:
                        tp = page.get_textpage()
                        try:
                            raw = tp.get_text_bounded()
                        finally:
                            tp.close()
                    finally:
                        page.close()
                    text = normalize(raw)
                    if len(text.replace("\n", "").replace(" ", "")) >= MIN_TEXT_CHARS and not looks_garbled(text):
                        result_pages[i] = PageText(i + 1, text, "text")
                    else:
                        need_ocr.append(i)
            except Exception:
                pdf.close()
                raise

        try:
            if n == 0:
                raise ReadError("В PDF нет страниц")
            todo: list[int] = []
            if need_ocr:
                if not use_ocr:
                    warnings.append("Есть страницы без текста; распознавание сканов выключено")
                elif ocr is None or not ocr.available:
                    warnings.append("Есть страницы без текста; модуль распознавания не найден")
                else:
                    allowed = _pages_to_ocr(len(need_ocr), max_ocr_pages)
                    todo = [need_ocr[k] for k in sorted(allowed)]
                    skipped = len(need_ocr) - len(todo)
                    if skipped:
                        warnings.append(f"Распознаны не все страницы-сканы: пропущено {skipped} из {len(need_ocr)}")
            for i in range(n):
                if i not in result_pages and i not in todo:
                    result_pages[i] = PageText(i + 1, "", "skipped")

            for i in todo:
                _check_cancel(cancel)
                with PDFIUM_LOCK:
                    page = pdf[i]
                    try:
                        w, h = page.get_size()
                        scale = dpi / 72.0
                        longest = max(w, h) * scale
                        if longest > 5200:
                            scale *= 5200 / longest
                        img = page.render(scale=scale, grayscale=True).to_pil()
                    finally:
                        page.close()
                try:
                    text, conf = ocr.recognize(img)  # type: ignore[union-attr]
                except ReadError as e:
                    warnings.append(f"Страница {i + 1}: {e}")
                    result_pages[i] = PageText(i + 1, "", "empty")
                    continue
                text = normalize(text)
                if text:
                    result_pages[i] = PageText(i + 1, text, "ocr", conf)
                else:
                    result_pages[i] = PageText(i + 1, "", "empty")
        finally:
            with PDFIUM_LOCK:
                pdf.close()

    pages = [result_pages[i] for i in range(n)]
    return ReadResult(pages, n, warnings)


def _read_image(path, ocr, use_ocr, max_ocr_pages, cancel) -> ReadResult:
    from PIL import Image, ImageSequence

    if not use_ocr:
        raise ReadError("Для изображений нужно включить распознавание сканов")
    if ocr is None or not ocr.available:
        raise ReadError("Модуль распознавания не найден")
    try:
        im = Image.open(path)
    except Exception:
        raise ReadError("Файл повреждён или это не изображение")
    pages: list[PageText] = []
    warnings: list[str] = []
    frames = getattr(im, "n_frames", 1)
    limit = frames if max_ocr_pages <= 0 else max_ocr_pages
    for k, frame in enumerate(ImageSequence.Iterator(im)):
        _check_cancel(cancel)
        if k >= limit:
            warnings.append(f"Распознаны не все кадры: {limit} из {frames}")
            break
        try:
            text, conf = ocr.recognize(frame.copy())
        except ReadError as e:
            warnings.append(f"Кадр {k + 1}: {e}")
            pages.append(PageText(k + 1, "", "empty"))
            continue
        text = normalize(text)
        pages.append(PageText(k + 1, text, "ocr" if text else "empty", conf if text else None))
    return ReadResult(pages, frames, warnings)


# ---------------------------------------------------------------- кэш текста


class TextCache:
    """Кэш распознанного текста (SQLite), чтобы продолжать после остановки.

    Хранит текст страниц сжатым. Файл лежит там, где вы укажете (по умолчанию
    рядом с итоговым Excel), и содержит те же данные, что и документы.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS texts (key TEXT PRIMARY KEY, data BLOB)")
        self._db.commit()

    @staticmethod
    def key(sha: str, use_ocr: bool, max_pages: int, dpi: int, lang: str) -> str:
        return f"{sha}|{int(use_ocr)}|{max_pages}|{dpi}|{lang}|v{READER_VERSION}"

    def get(self, key: str) -> ReadResult | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM texts WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            d = json.loads(zlib.decompress(row[0]).decode("utf-8"))
            pages = [PageText(p["i"], p["t"], p["m"], p.get("c")) for p in d["pages"]]
            return ReadResult(pages, d["n"], list(d.get("w", [])))
        except Exception:
            return None

    def put(self, key: str, rr: ReadResult) -> None:
        d = {
            "n": rr.page_count,
            "w": rr.warnings,
            "pages": [{"i": p.index, "t": p.text, "m": p.method, "c": p.conf} for p in rr.pages],
        }
        blob = zlib.compress(json.dumps(d, ensure_ascii=False).encode("utf-8"), 6)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO texts (key, data) VALUES (?, ?)", (key, blob))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass
