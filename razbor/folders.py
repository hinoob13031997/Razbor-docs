"""Подключённые папки: программа читает документы на месте, без копирования.

Прочитанные файлы запоминаются (путь, размер, время изменения), поэтому повторная проверка
берёт только новые и изменённые файлы. Уверенные привязки записываются в карточки сразу,
остальное попадает в список «Нужно подтвердить» / «Не найдены».
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from . import batch as B
from .analyze import load_custom_fields
from .pipeline import Options, list_files
from .reader import OcrEngine
from .rules import recalc_contracts
from .store import Store

CHUNK = 48


def add_source(store: Store, path: str) -> dict:
    path = os.path.normpath(path.strip().strip('"'))
    if not path or not os.path.isdir(path):
        raise ValueError(f"Папка не найдена или недоступна: {path}")
    with store.write() as c:
        c.execute("INSERT OR IGNORE INTO sources(path, added_at, added_by) VALUES(?,?,?)",
                  (path, datetime.now().isoformat(timespec="seconds"), B.current_user()))
    return store.one("SELECT * FROM sources WHERE path=?", (path,)) or {}


def list_sources(store: Store) -> list[dict]:
    out = []
    for s in store.q("SELECT * FROM sources ORDER BY id"):
        st = {r["status"]: r["n"] for r in store.q(
            "SELECT status, COUNT(*) n FROM src_files WHERE src_id=? GROUP BY status", (s["id"],))}
        s["stats"] = st
        s["files"] = sum(st.values())
        s["available"] = os.path.isdir(s["path"])
        out.append(s)
    return out


def remove_source(store: Store, sid: int) -> None:
    with store.write() as c:
        c.execute("DELETE FROM src_files WHERE src_id=?", (sid,))
        c.execute("DELETE FROM sources WHERE id=?", (sid,))


def scan_source(store: Store, sid: int, batches_dir: str, progress: Callable[[int, int, str], None],
                cancel: threading.Event, custom_fields_path: str | None = None, use_ocr: bool = True) -> dict:
    src = store.one("SELECT * FROM sources WHERE id=?", (sid,))
    if not src:
        raise ValueError("Папка не подключена")
    root = src["path"]
    if not os.path.isdir(root):
        raise ValueError(f"Папка недоступна: {root}. Проверьте сетевой диск.")
    progress(0, 0, "Ищу файлы в папке")
    files = list_files(root)
    known = {r["path"]: (r["size"], r["mtime"]) for r in
             store.q("SELECT path, size, mtime FROM src_files WHERE src_id=?", (sid,))}
    todo: list[tuple[str, int, float]] = []
    for f in files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        if known.get(f) == (st.st_size, st.st_mtime):
            continue
        todo.append((f, st.st_size, st.st_mtime))
        if cancel.is_set():
            raise InterruptedError("Остановлено пользователем")
    res = {"files": len(files), "new": len(todo), "auto": 0, "review": 0, "seen": 0, "errors": 0, "batch": None}
    if not todo:
        with store.write() as c:
            c.execute("UPDATE sources SET last_scan=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), sid))
        return res

    b = B.Batch(id=uuid.uuid4().hex[:10], dir=root, created=datetime.now().isoformat(timespec="seconds"),
                state="reading", source=root)
    with store.write() as c:
        c.execute("UPDATE sources SET batch_id=? WHERE id=?", (b.id, sid))
    res["batch"] = b.id
    b.save(batches_dir)

    opts = Options(input_dir=root, output_path=os.path.join(root, "_нет.xlsx"), use_ocr=use_ocr,
                   custom_fields_path=custom_fields_path)
    ocr = OcrEngine(lang=opts.ocr_lang) if use_ocr else None
    if use_ocr and ocr is not None and not ocr.available:
        b.notes.append(ocr.problem() or "Модуль распознавания недоступен: сканы не прочитаны")
    custom, errs = load_custom_fields(custom_fields_path)
    b.notes.extend(errs)
    done = 0
    idx = 0
    ex = ThreadPoolExecutor(max_workers=max(1, opts.workers))
    try:
        for i in range(0, len(todo), CHUNK):
            part = todo[i:i + CHUNK]
            items = list(ex.map(lambda x: B.process_item(store, 0, x[0], root, opts, ocr, None, custom, cancel, True), part))
            auto = [it for it in items if it.status == "ok" and it.chosen is not None]
            w = B.write_items(store, auto, recalc=False)
            if w["ids"]:
                recalc_contracts(store, w["ids"])
            res["auto"] += w["documents"]
            b.auto += w["documents"]
            rows = []
            for it, (path, size, mtime) in zip(items, part):
                if it.status == "error" and "Остановлено" in it.note:
                    continue  # не помечаем: прочитается в следующий раз
                if it.status == "seen":
                    res["seen"] += 1
                elif not it.written:
                    it.idx = idx
                    idx += 1
                    b.items.append(it)
                    res["errors" if it.status == "error" else "review"] += 1
                rows.append((path, sid, size, mtime, "written" if it.written else it.status, b.id))
            with store.write() as c:
                c.executemany("INSERT INTO src_files(path, src_id, size, mtime, status, batch_id) VALUES(?,?,?,?,?,?) "
                              "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                              "status=excluded.status, batch_id=excluded.batch_id", rows)
            done += len(part)
            progress(done, len(todo), os.path.basename(part[-1][0]))
            b.summary = b.counts()
            b.save(batches_dir)
            if cancel.is_set():
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        b.state = "ready"
        b.notes.insert(0, f"Папка {root}: новых файлов {len(todo)}, прочитано {done}, записано в карточки "
                          f"автоматически {b.auto}, уже были разобраны {res['seen']}.")
        b.summary = b.counts()
        b.save(batches_dir)
        with store.write() as c:
            c.execute("UPDATE sources SET last_scan=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), sid))
    return res


def pick_folder() -> str:
    """Окно выбора папки Windows (если доступно). Пустая строка, если не выбрали."""
    out: list[str] = [""]

    def run() -> None:
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            out[0] = filedialog.askdirectory(parent=root, title="Папка с документами") or ""
            root.destroy()
        except Exception:
            out[0] = ""

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(600)
    return os.path.normpath(out[0]) if out[0] else ""
