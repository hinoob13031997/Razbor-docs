"""Пачка документов: чтение, привязка к договорам, запись в карточки."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Callable

from . import matcher
from .analyze import analyze, load_custom_fields, review_reasons
from .pipeline import Options, _process, list_files
from .reader import Cancelled, OcrEngine, TextCache, file_sha256
from .rules import recalc_contracts
from .store import Store


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "пользователь"


@dataclass
class Item:
    idx: int
    rel: str
    name: str
    path: str
    sha: str = ""
    status: str = "nf"          # ok | confirm | nf | seen | error
    doc_key: str = ""
    doc_label: str = ""
    group: str = ""
    pages: int = 0
    fields: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    chosen: int | None = None
    note: str = ""
    seen: dict | None = None
    text_hash: str = ""
    event: list = field(default_factory=list)
    written: bool = False
    skipped: bool = False
    inplace: bool = False      # файл лежит в подключённой папке: не копируем и не удаляем


@dataclass
class Batch:
    id: str
    dir: str
    created: str
    state: str = "new"          # new | reading | ready | written
    items: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    auto: int = 0               # записано автоматически (подключённая папка)
    source: str = ""            # путь подключённой папки, если пачка из неё

    def save(self, folder: str) -> None:
        os.makedirs(folder, exist_ok=True)
        data = asdict(self)
        tmp = os.path.join(folder, self.id + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, self.id + ".json"))

    @staticmethod
    def load(path: str) -> "Batch":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        items = [Item(**i) for i in d.pop("items", [])]
        return Batch(items=items, **d)

    def counts(self) -> dict:
        c = {"ok": 0, "confirm": 0, "nf": 0, "seen": 0, "error": 0, "written": 0}
        for i in self.items:
            if i.written:
                c["written"] += 1
            elif i.skipped:
                continue
            elif i.chosen is not None and i.status in ("confirm", "nf", "error"):
                c["ok"] += 1
            else:
                c[i.status] = c.get(i.status, 0) + 1
        return c


def _seen_info(row: dict) -> dict:
    return {"contract": row.get("c_number") or "", "by": row.get("loaded_by") or "",
            "at": (row.get("loaded_at") or "")[:10], "name": row.get("name") or ""}


def process_item(store: Store, i: int, path: str, base: str, opts: Options, ocr, cache, custom,
                 cancel: threading.Event, inplace: bool = False) -> Item:
    """Прочитать один файл и найти его договор (в карточки ещё не пишет)."""
    rel = os.path.relpath(path, base)
    it = Item(idx=i, rel=rel, name=os.path.basename(path), path=path, inplace=inplace)
    try:
        it.sha = file_sha256(path)
        prior = store.doc_by_sha(it.sha)
        if prior:
            it.status, it.seen = "seen", _seen_info(prior)
            it.note = "Этот файл уже разобран"
            return it
        res = _process(path, base, opts, ocr, cache, custom, cancel)
        if res.error:
            it.status, it.note = "error", res.error
            return it
        a = res.analysis
        it.pages = res.page_count
        it.doc_key, it.doc_label, it.group = a.doc_key, a.doc_label, a.group
        it.text_hash = a.text_hash
        it.reasons = list(res.reasons)
        it.fields = matcher.display_fields(a)
        it.facts = matcher.facts_from(a)
        it.event = list(matcher.doc_event(a, it.name))
        prior = store.doc_by_text_hash(a.text_hash)
        if prior:
            it.status, it.seen = "seen", _seen_info(prior)
            it.note = "Такой же текст уже разобран (другой файл)"
            return it
        m = matcher.match_document(store, a)
        it.status, it.candidates = m["status"], m["candidates"]
        if m["status"] == "ok":
            it.chosen = m["candidates"][0]["id"]
    except Cancelled:
        it.status, it.note = "error", "Остановлено пользователем"
    except Exception as e:  # один файл не должен остановить всю пачку
        it.status, it.note = "error", f"Непредвиденная ошибка: {type(e).__name__}: {e}"
    return it


def read_batch(store: Store, batch: Batch, cache_path: str, workers: int | None = None,
               progress: Callable[[int, int, str], None] | None = None, cancel: threading.Event | None = None,
               custom_fields_path: str | None = None, use_ocr: bool = True) -> Batch:
    """Прочитать все файлы пачки и привязать их к договорам. Файл ещё не записан в карточки."""
    cancel = cancel or threading.Event()
    files = list_files(batch.dir)
    total = len(files)
    opts = Options(input_dir=batch.dir, output_path=os.path.join(batch.dir, "_нет.xlsx"), use_ocr=use_ocr,
                   custom_fields_path=custom_fields_path)
    if workers:
        opts.workers = workers
    ocr = OcrEngine(lang=opts.ocr_lang) if use_ocr else None
    if use_ocr and ocr is not None and not ocr.available:
        batch.notes.append(ocr.problem() or "Модуль распознавания недоступен: сканы не прочитаны")
    custom, errs = load_custom_fields(custom_fields_path)
    batch.notes.extend(errs)
    cache = None
    try:
        cache = TextCache(cache_path)
    except Exception as e:
        batch.notes.append(f"Кэш недоступен: {e}")

    batch.state = "reading"
    items: list[Item] = []
    done = 0
    if progress:
        progress(0, total, "")

    def work(i: int, path: str) -> Item:
        return process_item(store, i, path, batch.dir, opts, ocr, cache, custom, cancel)

    ex = ThreadPoolExecutor(max_workers=max(1, opts.workers))
    try:
        futs = [ex.submit(work, i, p) for i, p in enumerate(files)]
        for fut in as_completed(futs):
            items.append(fut.result())
            done += 1
            if progress:
                progress(done, total, items[-1].name)
            if cancel.is_set():
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        if cache is not None:
            cache.close()

    items.sort(key=lambda x: (x.rel.lower()))
    for n, it in enumerate(items):
        it.idx = n
    # повторы внутри самой пачки
    seen_sha: dict[str, Item] = {}
    seen_txt: dict[str, Item] = {}
    for it in items:
        if it.status == "seen":
            continue
        first = seen_sha.get(it.sha) or (seen_txt.get(it.text_hash) if it.text_hash else None)
        if first is not None:
            it.status, it.chosen = "seen", None
            it.seen = {"contract": "", "by": current_user(), "at": date.today().isoformat(), "name": first.name}
            it.note = f"Повтор в этой пачке: {first.name}"
            continue
        seen_sha[it.sha] = it
        if it.text_hash:
            seen_txt[it.text_hash] = it
    batch.items = items
    batch.state = "ready"
    batch.summary = batch.counts()
    return batch


def write_items(store: Store, items: list[Item], recalc: bool = True) -> dict:
    """Записать привязанные документы в карточки. Копии документов не создаются:
    для файлов из подключённой папки запоминается путь, загруженная копия после записи удаляется."""
    user, now = current_user(), datetime.now().isoformat(timespec="seconds")
    touched: set[int] = set()
    n_docs = 0
    for it in items:
        if it.written or it.skipped or it.chosen is None or it.status in ("seen",):
            continue
        ext = os.path.splitext(it.name)[1].lower()
        stored = it.path if it.inplace else None
        with store.write() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO documents(contract_id,sha,text_hash,name,ext,doc_key,doc_label,grp,reasons,fields,"
                "loaded_by,loaded_at,stored,pages) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it.chosen, it.sha, it.text_hash, it.name, ext, it.doc_key, it.doc_label, it.group,
                 json.dumps(it.reasons, ensure_ascii=False), json.dumps(it.fields, ensure_ascii=False),
                 user, now, stored, it.pages))
            if cur.rowcount == 0:
                it.written = True
                continue
            doc_id = cur.lastrowid
            for k, v in it.facts.items():
                c.execute("INSERT INTO facts(contract_id,key,value,doc_id) VALUES(?,?,?,?)", (it.chosen, k, v, doc_id))
            if it.event:
                c.execute("INSERT INTO events(contract_id,date,text,doc_id) VALUES(?,?,?,?)",
                          (it.chosen, it.event[0], it.event[1], doc_id))
        it.written = True
        if not it.inplace:
            try:
                os.remove(it.path)
            except OSError:
                pass
        touched.add(it.chosen)
        n_docs += 1
    if touched:
        ids = sorted(touched)
        with store.write() as c:
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                c.execute(
                    f"UPDATE contracts SET last_doc_at=?, last_doc_by=?, docs_count=(SELECT COUNT(*) FROM documents "
                    f"WHERE contract_id=contracts.id) WHERE id IN ({','.join('?' * len(part))})",
                    [date.today().isoformat(), user] + part)
        if recalc:
            recalc_contracts(store, ids)
    return {"documents": n_docs, "contracts": len(touched), "ids": sorted(touched)}


def write_batch(store: Store, batch: Batch, docs_dir: str | None = None) -> dict:
    """Записать привязанные документы пачки в карточки и пересчитать сроки."""
    res = write_items(store, batch.items)
    res.pop("ids", None)
    batch.summary = batch.counts()
    if all(i.written or i.skipped or i.status == "seen" for i in batch.items):
        batch.state = "written"
        if not batch.source:  # загруженные копии больше не нужны
            shutil.rmtree(batch.dir, ignore_errors=True)
    return res
