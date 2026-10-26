"""Массовая обработка папки: параллельное чтение, разбор, поиск дублей, выгрузка."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import __version__
from .analyze import Analysis, analyze, load_custom_fields, review_reasons
from .reader import (SUPPORTED_EXT, Cancelled, OcrEngine, ReadError, ReadResult, TextCache,
                     file_sha256, read_document)


@dataclass
class Options:
    input_dir: str
    output_path: str
    recursive: bool = True
    use_ocr: bool = True
    max_ocr_pages: int = 10
    workers: int = max(1, min(8, (os.cpu_count() or 2) - 1))
    use_cache: bool = True
    dpi: int = 250
    custom_fields_path: str | None = None
    ocr_lang: str = "rus+eng"


@dataclass
class DocResult:
    path: str
    rel_dir: str
    name: str
    size: int = 0
    sha: str = ""
    page_count: int = 0
    pages_read: int = 0
    method: str = ""
    ocr_conf: float | None = None
    analysis: Analysis | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    dup_group: str = ""
    dup_kind: str = ""
    seconds: float = 0.0
    from_cache: bool = False
    name_check: dict = field(default_factory=dict)   # сверка названия файла с содержимым


@dataclass
class RunSummary:
    results: list[DocResult]
    started: datetime
    finished: datetime
    options: Options
    cancelled: bool
    output_path: str
    notes: list[str] = field(default_factory=list)


ProgressCb = Callable[[int, int, str], None]


def list_files(root: str, recursive: bool = True) -> list[str]:
    out: list[str] = []
    if recursive:
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in SUPPORTED_EXT and not fn.startswith("~$"):
                    out.append(os.path.join(dirpath, fn))
    else:
        for fn in os.listdir(root):
            p = os.path.join(root, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in SUPPORTED_EXT:
                out.append(p)
    out.sort(key=lambda p: p.lower())
    return out


def add_name_check(res: "DocResult") -> None:
    """Сверить название файла с содержимым; при расхождении добавить причину «проверить»."""
    from . import namecheck

    try:
        nc = namecheck.check_name(res.name, res.analysis)
    except Exception as e:  # сверка вторична
        res.name_check = {"verdict": "unknown", "title": f"Сверка названия не выполнена ({type(e).__name__})",
                          "problems": [], "rows": [], "suggest": ""}
        return
    res.name_check = nc.to_dict()
    if nc.verdict in ("wrong", "typo", "generic"):
        first = nc.problems[0] if nc.problems else ""
        res.reasons.append(f"{nc.title}. {first}".strip() + (f" Правильнее: {nc.suggest}" if nc.suggest else ""))


def _process(path: str, root: str, opts: Options, ocr: OcrEngine | None, cache: TextCache | None,
             custom, cancel: threading.Event) -> DocResult:
    t0 = time.time()
    rel = os.path.relpath(os.path.dirname(path), root)
    res = DocResult(path=path, rel_dir="" if rel == "." else rel, name=os.path.basename(path))
    try:
        res.size = os.path.getsize(path)
        res.sha = file_sha256(path)
        rr: ReadResult | None = None
        key = TextCache.key(res.sha, opts.use_ocr, opts.max_ocr_pages, opts.dpi, opts.ocr_lang)
        if cache is not None:
            rr = cache.get(key)
            res.from_cache = rr is not None
        if rr is None:
            rr = read_document(path, ocr=ocr, use_ocr=opts.use_ocr, max_ocr_pages=opts.max_ocr_pages,
                               dpi=opts.dpi, cancel=cancel)
            if cache is not None:
                cache.put(key, rr)
        res.page_count = rr.page_count
        res.pages_read = rr.pages_read
        res.method = rr.method
        res.ocr_conf = rr.ocr_conf
        res.warnings = list(rr.warnings)
        res.analysis = analyze(rr, custom)
        res.reasons = review_reasons(rr, res.analysis)
        add_name_check(res)
    except Cancelled:
        res.error = "Остановлено пользователем"
    except ReadError as e:
        res.error = str(e)
    except Exception as e:  # не даём одному файлу остановить всю обработку
        res.error = f"Непредвиденная ошибка: {type(e).__name__}: {e}"
    res.seconds = time.time() - t0
    return res


def mark_duplicates(results: list[DocResult]) -> None:
    """Группы дублей.

    1) тот же текст (в том числе точные копии файла);
    2) вероятные дубли: тот же вид документа, номер дела и дата.
    """
    n = 0
    seen: set[int] = set()
    groups: dict[str, list[DocResult]] = {}
    for r in results:
        if r.error:
            continue
        a = r.analysis
        key = a.text_hash if a is not None and a.text_hash else "sha:" + r.sha
        groups.setdefault(key, []).append(r)
    for items in groups.values():
        if len(items) < 2:
            continue
        n += 1
        shas: dict[str, int] = {}
        for r in items:
            shas[r.sha] = shas.get(r.sha, 0) + 1
        for r in items:
            r.dup_group = f"Д{n:03d}"
            r.dup_kind = "точная копия файла" if shas[r.sha] > 1 else "тот же текст, файл другой"
            seen.add(id(r))
    case_groups: dict[tuple, list[DocResult]] = {}
    for r in results:
        a = r.analysis
        if r.error or id(r) in seen or a is None or a.doc_key == "unknown":
            continue
        no, dt = a.fields.get("Номер дела"), a.fields.get("Дата документа")
        if no and dt:
            case_groups.setdefault((a.doc_key, str(no.value).lower(), str(dt.value)), []).append(r)
    for items in case_groups.values():
        if len(items) > 1:
            n += 1
            for r in items:
                r.dup_group = f"Д{n:03d}"
                r.dup_kind = "вероятный дубль: тот же вид, дело и дата"
    for r in results:
        if r.dup_group:
            r.reasons.append(f"дубль ({r.dup_kind})")


def run(opts: Options, progress: ProgressCb | None = None,
        cancel: threading.Event | None = None) -> RunSummary:
    from .export import write_excel

    cancel = cancel or threading.Event()
    started = datetime.now()
    notes: list[str] = []
    files = list_files(opts.input_dir, opts.recursive)
    total = len(files)

    ocr = OcrEngine(lang=opts.ocr_lang) if opts.use_ocr else None
    if opts.use_ocr and ocr is not None and not ocr.available:
        notes.append(ocr.problem() or "Модуль распознавания недоступен")
    custom, errs = load_custom_fields(opts.custom_fields_path)
    notes.extend(errs)

    cache = None
    if opts.use_cache:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(opts.output_path)), exist_ok=True)
            cache_path = os.path.splitext(opts.output_path)[0] + ".кэш.sqlite"
            cache = TextCache(cache_path)
        except Exception as e:
            notes.append(f"Кэш недоступен: {e}")

    results: list[DocResult] = []
    done = 0
    if progress:
        progress(0, total, "")
    ex = ThreadPoolExecutor(max_workers=max(1, opts.workers))
    try:
        futs = {ex.submit(_process, p, opts.input_dir, opts, ocr, cache, custom, cancel): p for p in files}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            if progress:
                progress(done, total, r.name)
            if cancel.is_set():
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        if cache is not None:
            cache.close()

    cancelled = cancel.is_set()
    results = [r for r in results if r.error != "Остановлено пользователем"]
    results.sort(key=lambda r: (r.rel_dir.lower(), r.name.lower()))
    mark_duplicates(results)
    finished = datetime.now()
    summary = RunSummary(results, started, finished, opts, cancelled, opts.output_path, notes)
    write_excel(summary, custom)
    return summary
