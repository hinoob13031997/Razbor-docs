"""Локальный сервер программы: экран открывается в браузере, данные не покидают компьютер.

Сервер слушает только 127.0.0.1 (с других компьютеров недоступен), на случайном порту.
Доступ защищён одноразовой ссылкой с ключом: ключ превращается в cookie.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import batch as B
from . import crm, folders, matcher, rules, templates
from .actions import next_actions
from .paths import _bundle_dir, app_dir
from .store import Store

VERSION = "0.2 (пробная версия)"
IDLE_EXIT_SEC = 180


# ---------------------------------------------------------------- папка данных

def data_dir() -> Path:
    env = os.environ.get("RAZBOR_DATA")
    if env:
        p = Path(env)
    else:
        p = app_dir() / "Данные"
        try:
            p.mkdir(parents=True, exist_ok=True)
            t = p / ".w"
            t.write_text("1")
            t.unlink()
        except OSError:
            p = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "RazborDocs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def web_dir() -> Path:
    for base in (_bundle_dir(), app_dir(), Path(__file__).resolve().parent.parent):
        for sub in ("web", "_internal/web"):
            if (base / sub / "index.html").is_file():
                return base / sub
    return app_dir() / "web"


# ---------------------------------------------------------------- фоновые задачи

class Job:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title = kind, title
        self.state = "running"      # running | done | error | cancelled
        self.done = 0
        self.total = 0
        self.msg = ""
        self.result: dict = {}
        self.error = ""
        self.cancel = threading.Event()
        self.started = time.time()
        self.ended = 0.0

    def info(self) -> dict:
        end = self.ended or time.time()
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state, "done": self.done,
                "total": self.total, "msg": self.msg, "result": self.result, "error": self.error,
                "seconds": int(end - self.started)}


class Ctx:
    """Всё, что нужно обработчикам запросов."""

    def __init__(self, base: Path):
        self.base = base
        self.store = Store(str(base / "razbor.sqlite"))
        self.uploads = base / "uploads"
        self.docs = base / "docs"
        self.batches = base / "batches"
        self.out = base / "Документы"
        self.imports = base / "imports"
        for p in (self.uploads, self.docs, self.batches, self.out, self.imports):
            p.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.batch_cache: dict[str, B.Batch] = {}
        self.lock = threading.RLock()
        self.last_ping = time.time()
        self.seen_ping = False
        self.token = secrets.token_urlsafe(24)
        self.port = 0
        self.quit = threading.Event()

    # --- задачи
    def run_job(self, job: Job, fn) -> Job:
        self.jobs[job.id] = job

        def target() -> None:
            try:
                job.result = fn(job) or {}
                job.state = "cancelled" if job.cancel.is_set() else "done"
            except InterruptedError:
                job.state = "cancelled"
                job.msg = "Остановлено"
            except Exception as e:
                job.state = "error"
                job.error = f"{type(e).__name__}: {e}"
                self.log(traceback.format_exc())
            finally:
                job.ended = time.time()

        threading.Thread(target=target, daemon=True).start()
        return job

    def busy(self) -> bool:
        return any(j.state == "running" for j in self.jobs.values())

    def log(self, text: str) -> None:
        try:
            with open(self.base / "log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except OSError:
            pass

    # --- пачки
    def get_batch(self, bid: str) -> B.Batch | None:
        if not re.fullmatch(r"[0-9a-f]{8,16}", bid or ""):
            return None
        with self.lock:
            b = self.batch_cache.get(bid)
            if b is not None and b.source and b.state == "reading":
                b = None  # пачку пополняет чтение подключённой папки: берём свежую с диска
            if b is None:
                p = self.batches / f"{bid}.json"
                if not p.is_file():
                    return None
                b = B.Batch.load(str(p))
                self.batch_cache[bid] = b
            return b

    def new_batch(self) -> B.Batch:
        bid = uuid.uuid4().hex[:10]
        d = self.uploads / bid
        d.mkdir(parents=True, exist_ok=True)
        b = B.Batch(id=bid, dir=str(d), created=datetime.now().isoformat(timespec="seconds"))
        with self.lock:
            self.batch_cache[bid] = b
        b.save(str(self.batches))
        return b

    def params(self) -> dict:
        return rules.load_params(self.store)

    def recalc_if_new_day(self) -> None:
        """При запуске: пересчитать сроки на сегодня и проверить новые файлы в подключённых папках."""
        today = date.today().isoformat()
        need_recalc = bool(self.store.scalar("SELECT COUNT(*) FROM contracts")) and \
            self.store.get_setting("recalc_day") != today
        sources = [s["id"] for s in folders.list_sources(self.store) if s["available"]] \
            if self.store.get_setting("auto_scan", "1") == "1" else []
        if not need_recalc and not sources:
            return

        def run(j: Job) -> dict:
            if need_recalc:
                j.title = "Пересчёт сроков на сегодня"
                rules.recalc_contracts(self.store, progress=lambda n, m: (setattr(j, "done", n), setattr(j, "msg", m)),
                                       cancel=j.cancel)
            out = {}
            for sid in sources:
                j.title = "Проверка новых файлов в подключённых папках"
                out[sid] = self.scan(j, sid)
            return out
        self.run_job(Job("startup", "Подготовка"), run)

    def scan(self, j: Job, sid: int) -> dict:
        def prog(n: int, t: int, name: str) -> None:
            j.done, j.total, j.msg = n, t, name
        return folders.scan_source(self.store, sid, str(self.batches), prog, j.cancel,
                                   custom_fields_path=str(app_dir() / "polya.json"))


# ---------------------------------------------------------------- вспомогательное

def _fresh(c: dict, p: dict, today: date) -> dict:
    f = rules.fresh_level(c.get("last_doc_at"), today, p)
    f["ago"] = rules.ago_txt(f["days"])
    f["by"] = c.get("last_doc_by") or ""
    f["at"] = c.get("last_doc_at") or ""
    return f


def _row_out(r: dict, p: dict, today: date) -> dict:
    r = dict(r)
    r["fresh"] = _fresh(r, p, today)
    r["sit_label"] = rules.SIT_LABELS.get(r.get("sit") or "pre", "")
    r["sit_cls"] = rules.SIT_CLS.get(r.get("sit") or "pre", "gray")
    dl = r.get("next_dl_date")
    r["dl_days"] = (date.fromisoformat(dl) - today).days if dl else None
    return r


def _safe_rel(rel: str) -> str | None:
    rel = rel.replace("\\", "/").strip("/")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." or ":" in x for x in parts):
        return None
    clean = []
    for x in parts:
        x = re.sub(r'[<>"|?*\x00-\x1f]', "_", x).strip(" .") or "_"
        clean.append(x[:120])
    return "/".join(clean)


def msg_obj(msg: str) -> dict:
    return {"error": msg}


def _contract_card(ctx: Ctx, cid: int) -> dict | None:
    c = ctx.store.contract(cid)
    if not c:
        return None
    p, today = ctx.params(), date.today()
    docs = ctx.store.documents_for(cid)
    facts = ctx.store.facts_for(cid)
    fields = []
    for k, title in crm.FIELD_TITLES.items():
        v = c.get(k)
        if v in (None, "", 0) and k != "bankrupt":
            continue
        if k == "bankrupt":
            if not v:
                continue
            v = "да"
        fields.append({"key": k, "title": title, "value": v,
                       "kind": "date" if k in crm.DATE_FIELDS else "money" if k in crm.MONEY_FIELDS else "text"})
    dls = ctx.store.deadlines_for(cid)
    for x in dls:
        x["days"] = (date.fromisoformat(x["date"]) - today).days
    checks = matcher.compare_with_crm(c, docs)
    flags = rules.derive(c, facts, p, today)["flags"]
    acts = next_actions(c, facts, dls, rules.fresh_level(c.get("last_doc_at"), today, p)["level"], p, today, checks,
                        flags)
    rec = templates.RECOMMEND.get(c.get("sit") or "pre")
    if c.get("sit") == "ip" and c.get("ip_stalled"):
        rec = "ip_new"
    row = _row_out(c, p, today)
    return {"contract": {k: c.get(k) for k in ("id", "number", "fio", "kind", "birth", "inn", "debt", "status", "sit",
                                              "responsible", "creditor", "ip_state", "ip_start", "ip_end",
                                              "ip_months", "ip_stalled", "docs_count", "last_doc_at", "last_doc_by")},
            "sit_label": row["sit_label"], "sit_cls": row["sit_cls"], "fresh": row["fresh"], "fields": fields,
            "deadlines": dls, "events": ctx.store.events_for(cid), "documents": docs, "facts": facts,
            "checks": checks, "actions": acts, "recommend": rec, "params": p,
            "manual": ctx.store.manual_for(cid)}


def _batch_out(b: B.Batch, ctx: Ctx, with_items: bool = True) -> dict:
    out = {"id": b.id, "state": b.state, "created": b.created, "counts": b.counts(), "notes": b.notes[:10],
           "total": len(b.items), "auto": b.auto, "source": b.source}
    if with_items:
        ids = {i.chosen for i in b.items if i.chosen} | {c["id"] for i in b.items for c in i.candidates}
        info = {}
        if ids:
            lst = list(ids)
            for r in ctx.store.q(f"SELECT id, number, fio, last_doc_at, last_doc_by FROM contracts "
                                 f"WHERE id IN ({','.join('?' * len(lst))})", lst):
                info[r["id"]] = r
        p, today = ctx.params(), date.today()
        items = []
        for i in list(b.items):
            ch = info.get(i.chosen) if i.chosen else None
            items.append({
                "idx": i.idx, "rel": i.rel, "name": i.name, "status": i.status, "doc_label": i.doc_label,
                "group": i.group, "pages": i.pages, "note": i.note, "reasons": i.reasons, "seen": i.seen,
                "written": i.written, "skipped": i.skipped, "chosen": i.chosen,
                "chosen_info": ({"id": ch["id"], "number": ch["number"], "fio": ch["fio"],
                                 "fresh": _fresh(ch, p, today)} if ch else None),
                "candidates": i.candidates[:6], "nfields": len(i.fields)})
        out["items"] = items
    return out


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "RazborDocs"
    protocol_version = "HTTP/1.1"
    ctx: Ctx

    def log_message(self, fmt, *args):  # без шума в консоли
        pass

    # --- ответы
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    _raw: bytes | None = None

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str) -> None:
        if self.command == "POST" and self._raw is None:
            self.close_connection = True  # тело запроса не прочитано: соединение закрываем
        self._json(msg_obj(msg), code)

    def _body(self) -> bytes:
        if self._raw is None:
            n = int(self.headers.get("Content-Length") or 0)
            self._raw = self.rfile.read(n) if n else b""
        return self._raw

    def _jbody(self) -> dict:
        raw = self._body()
        try:
            d = json.loads(raw.decode("utf-8")) if raw else {}
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    # --- проверки
    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").lower()
        return h in (f"127.0.0.1:{self.ctx.port}", f"localhost:{self.ctx.port}")

    def _authed(self) -> bool:
        ck = self.headers.get("Cookie") or ""
        m = re.search(r"(?:^|;\s*)rt=([^;]+)", ck)
        return bool(m and secrets.compare_digest(m.group(1), self.ctx.token))

    def _dispatch(self, method: str) -> None:
        self._raw = None
        try:
            if not self._host_ok():
                return self._err(403, "Недопустимый адрес")
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            path = u.path
            if path == "/" and "t" in q:
                if not secrets.compare_digest(q["t"], self.ctx.token):
                    return self._err(403, "Неверная ссылка. Запустите программу ещё раз.")
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"rt={self.ctx.token}; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self._authed():
                body = ("<meta charset=utf-8><body style='font:16px sans-serif;padding:40px'>"
                        "Откройте программу через её ярлык или файл RazborDocs.exe: ссылка с ключом "
                        "открывается автоматически.</body>").encode("utf-8")
                return self._send(403, body, "text/html; charset=utf-8")
            if method == "POST" and (self.headers.get("Origin") or "").rstrip("/") not in (
                    "", f"http://127.0.0.1:{self.ctx.port}", f"http://localhost:{self.ctx.port}"):
                return self._err(403, "Чужой источник запроса")
            if path.startswith("/api/"):
                if method == "POST" and not path.endswith("/upload"):
                    if int(self.headers.get("Content-Length") or 0) > 5_000_000:
                        return self._err(413, "Слишком большой запрос")
                    self._body()
                return self._api(method, path[5:], q)
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self.ctx.log(traceback.format_exc())
            try:
                self._err(500, f"Ошибка программы: {type(e).__name__}: {e}")
            except Exception:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_HEAD(self):
        self._dispatch("GET")

    # --- статика
    def _static(self, path: str) -> None:
        root = web_dir()
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (root / rel).resolve()
        if root.resolve() not in f.parents and f != root.resolve() or not f.is_file():
            return self._err(404, "Не найдено")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def _file(self, path: str, name: str, download: bool = False) -> None:
        if not os.path.isfile(path):
            return self._err(404, "Файл не найден")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.lower().endswith(".docx"):
            ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        disp = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    # --- API
    def _api(self, method: str, route: str, q: dict) -> None:
        ctx, st = self.ctx, self.ctx.store
        parts = route.strip("/").split("/")
        head = parts[0]
        today = date.today()

        if head == "ping":
            ctx.last_ping, ctx.seen_ping = time.time(), True
            return self._json({"ok": True, "busy": ctx.busy()})

        if head == "summary":
            p = ctx.params()
            sc = st.sit_counts()
            total = st.scalar("SELECT COUNT(*) FROM contracts")
            d30 = (today.toordinal() + p["urgent_days"])
            until = date.fromordinal(d30).isoformat()
            fresh_cut = date.fromordinal(today.toordinal() - p["fresh_days"]).isoformat()
            stale_cut = date.fromordinal(today.toordinal() - p["stale_days"]).isoformat()
            out = {
                "today": today.isoformat(), "version": VERSION, "total": total, "sit": sc,
                "overdue": st.scalar("SELECT COUNT(*) FROM contracts WHERE next_dl_date < ?", (today.isoformat(),)),
                "urgent": st.scalar("SELECT COUNT(*) FROM contracts WHERE next_dl_date >= ? AND next_dl_date <= ?",
                                    (today.isoformat(), until)),
                "stalled": st.scalar("SELECT COUNT(*) FROM contracts WHERE ip_stalled=1"),
                "ip_active": st.scalar("SELECT COUNT(*) FROM contracts WHERE ip_state='active'"),
                "docs": st.scalar("SELECT COUNT(*) FROM documents"),
                "analysed": st.scalar("SELECT COUNT(*) FROM contracts WHERE last_doc_at IS NOT NULL"),
                "fresh": st.scalar("SELECT COUNT(*) FROM contracts WHERE last_doc_at >= ?", (fresh_cut,)),
                "stale": st.scalar("SELECT COUNT(*) FROM contracts WHERE last_doc_at < ?", (stale_cut,)),
                "last_import": st.last_import(), "params": p, "urgent_days": p["urgent_days"],
                "batches": [], "user": B.current_user(), "data_dir": str(ctx.base),
            }
            for f in sorted(ctx.batches.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
                b = ctx.get_batch(f.stem)
                if b:
                    out["batches"].append({"id": b.id, "created": b.created, "state": b.state, "total": len(b.items),
                                           "counts": b.counts()})
            nxt = st.q("SELECT id, number, fio, next_dl_name, next_dl_date FROM contracts WHERE next_dl_date IS NOT NULL "
                       "AND next_dl_date >= ? ORDER BY next_dl_date LIMIT 6", (today.isoformat(),))
            for r in nxt:
                r["dl_days"] = (date.fromisoformat(r["next_dl_date"]) - today).days
            out["next"] = nxt
            return self._json(out)

        if head == "contracts":
            p = ctx.params()
            res = st.list_contracts(q.get("q", ""), q.get("sit", ""), q.get("fresh", ""), int(q.get("page", 1) or 1),
                                    min(200, int(q.get("size", 50) or 50)), today.isoformat(), p["fresh_days"],
                                    p["stale_days"])
            res["rows"] = [_row_out(r, p, today) for r in res["rows"]]
            return self._json(res)

        if head == "contract" and len(parts) == 2:
            card = _contract_card(ctx, int(parts[1]))
            return self._json(card) if card else self._err(404, "Договор не найден")

        if head == "import" and parts[1:] == ["upload"] and method == "POST":
            name = re.sub(r"[^\w.\- ]", "_", q.get("name", "portfolio.csv"))[:80] or "portfolio.csv"
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".csv", ".xlsx", ".xlsm", ".txt", ".xls"):
                return self._err(400, "Нужен файл .xlsx или .csv")
            dst = ctx.imports / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
            left = int(self.headers.get("Content-Length") or 0)
            with open(dst, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if ctx.busy():
                return self._err(409, "Сейчас выполняется другая задача. Дождитесь её окончания.")
            job = Job("import", f"Загрузка портфеля: {name}")

            def run(j: Job) -> dict:
                def prog(n: int, m: str) -> None:
                    j.done, j.msg = n, m
                try:
                    res = crm.import_crm(st, str(dst), prog, j.cancel)
                finally:  # копию выгрузки не храним: данные уже в базе
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                j.msg = "Считаю сроки"
                rules.recalc_contracts(st, progress=lambda n, m: setattr(j, "done", n), cancel=j.cancel)
                return res
            ctx.run_job(job, run)
            return self._json(job.info())

        if head == "job" and len(parts) >= 2:
            job = ctx.jobs.get(parts[1])
            if not job:
                return self._err(404, "Задача не найдена")
            if len(parts) == 3 and parts[2] == "cancel":
                job.cancel.set()
            return self._json(job.info())

        if head == "jobs":
            return self._json([j.info() for j in ctx.jobs.values() if j.state == "running"])

        # ---- пачки
        if head == "batch":
            if parts[1:] == ["new"] and method == "POST":
                return self._json(_batch_out(ctx.new_batch(), ctx, False))
            if len(parts) < 2:
                return self._err(404, "Нет такой пачки")
            b = ctx.get_batch(parts[1])
            if not b:
                return self._err(404, "Пачка не найдена")
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "" and method == "GET":
                return self._json(_batch_out(b, ctx))
            if sub == "upload" and method == "POST":
                if b.state not in ("new",):
                    return self._err(409, "В эту пачку уже нельзя добавлять файлы")
                rel = _safe_rel(urllib.parse.unquote(q.get("path", "")))
                if not rel:
                    return self._err(400, "Недопустимое имя файла")
                dst = Path(b.dir) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                n = 1
                stem, ext = os.path.splitext(str(dst))
                while dst.exists():
                    dst = Path(f"{stem} ({n}){ext}")
                    n += 1
                left = int(self.headers.get("Content-Length") or 0)
                with open(dst, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                return self._json({"ok": True})
            if sub == "start" and method == "POST":
                if ctx.busy():
                    return self._err(409, "Сейчас выполняется другая задача. Дождитесь её окончания.")
                body = self._jbody()
                job = Job("read", "Чтение документов")
                b.state = "reading"

                def run(j: Job) -> dict:
                    def prog(n: int, t: int, name: str) -> None:
                        j.done, j.total, j.msg = n, t, name
                    B.read_batch(st, b, str(ctx.base / "cache.sqlite"), progress=prog, cancel=j.cancel,
                                 custom_fields_path=str(app_dir() / "polya.json"),
                                 use_ocr=bool(body.get("ocr", True)))
                    b.save(str(ctx.batches))
                    return {"batch": b.id, "counts": b.counts()}
                ctx.run_job(job, run)
                return self._json(job.info())
            if sub in ("pick", "skip", "write") and b.state == "reading":
                return self._err(409, "Папка ещё читается. Подтверждать можно после окончания чтения.")
            if sub == "pick" and method == "POST":
                d = self._jbody()
                it = next((i for i in b.items if i.idx == d.get("idx")), None)
                if not it or it.written:
                    return self._err(400, "Документ не найден или уже записан")
                cid = d.get("contract_id")
                if cid is not None and not st.contract(int(cid)):
                    return self._err(404, "Договор не найден")
                it.chosen = int(cid) if cid is not None else None
                it.skipped = False
                b.summary = b.counts()
                b.save(str(ctx.batches))
                return self._json({"ok": True, "counts": b.counts()})
            if sub == "skip" and method == "POST":
                d = self._jbody()
                it = next((i for i in b.items if i.idx == d.get("idx")), None)
                if not it or it.written:
                    return self._err(400, "Документ не найден или уже записан")
                it.skipped = bool(d.get("skip", True))
                b.summary = b.counts()
                b.save(str(ctx.batches))
                return self._json({"ok": True, "counts": b.counts()})
            if sub == "write" and method == "POST":
                if b.state == "reading":
                    return self._err(409, "Чтение ещё не закончено")
                res = B.write_batch(st, b, str(ctx.docs))
                b.save(str(ctx.batches))
                return self._json({"ok": True, **res, "counts": b.counts()})
            if sub == "item" and len(parts) == 4:
                it = next((i for i in b.items if i.idx == int(parts[3])), None)
                if not it:
                    return self._err(404, "Документ не найден")
                return self._json({"fields": it.fields, "facts": it.facts, "reasons": it.reasons,
                                   "event": it.event, "name": it.name})
            if sub == "file" and len(parts) == 4:
                it = next((i for i in b.items if i.idx == int(parts[3])), None)
                if not it:
                    return self._err(404, "Документ не найден")
                return self._file(it.path, it.name)
            return self._err(404, "Нет такого действия")

        if head == "batches":
            out = []
            for f in sorted(ctx.batches.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
                b = ctx.get_batch(f.stem)
                if b:
                    out.append(_batch_out(b, ctx, False))
            return self._json(out)

        if head == "doc" and len(parts) == 3 and parts[2] == "file":
            r = st.one("SELECT stored, name FROM documents WHERE id=?", (int(parts[1]),))
            if not r:
                return self._err(404, "Документ не найден")
            if not r["stored"] or not os.path.isfile(r["stored"]):
                return self._err(404, "Копия документа в программе не хранится, а исходный файл недоступен")
            return self._file(r["stored"], r["name"], q.get("download") == "1")

        # ---- подключённые папки
        if head == "sources":
            if len(parts) == 1:
                return self._json({"sources": folders.list_sources(st),
                                   "auto_scan": st.get_setting("auto_scan", "1") == "1"})
            if parts[1] == "add" and method == "POST":
                try:
                    src = folders.add_source(st, str(self._jbody().get("path", "")))
                except ValueError as e:
                    return self._err(400, str(e))
                if ctx.busy():
                    return self._json({"source": src, "job": None,
                                       "note": "Папка подключена. Чтение начнётся, когда закончится текущая задача: "
                                               "нажмите «Проверить новые файлы» позже."})
                job = ctx.run_job(Job("scan", "Чтение подключённой папки"), lambda j: ctx.scan(j, src["id"]))
                return self._json({"source": src, "job": job.info()})
            if parts[1] == "pick" and method == "POST":
                return self._json({"path": folders.pick_folder()})
            if parts[1] == "auto" and method == "POST":
                st.set_setting("auto_scan", "1" if self._jbody().get("on") else "0")
                return self._json({"ok": True})
            if len(parts) == 3 and parts[1].isdigit() and method == "POST":
                sid = int(parts[1])
                if parts[2] == "scan":
                    if ctx.busy():
                        return self._err(409, "Сейчас выполняется другая задача. Дождитесь её окончания.")
                    job = ctx.run_job(Job("scan", "Чтение подключённой папки"), lambda j: ctx.scan(j, sid))
                    return self._json(job.info())
                if parts[2] == "remove":
                    folders.remove_source(st, sid)
                    return self._json({"ok": True})
            return self._err(404, "Нет такого действия")

        # ---- сроки и ИП
        if head == "deadlines":
            p = ctx.params()
            kind, per = q.get("kind", ""), q.get("period", "90")
            where, args = [], []
            if kind:
                where.append("d.kind=?")
                args.append(kind)
            if per == "overdue":
                where.append("d.date < ?")
                args.append(today.isoformat())
            elif per.isdigit():
                where.append("d.date >= ? AND d.date <= ?")
                args += [today.isoformat(), date.fromordinal(today.toordinal() + int(per)).isoformat()]
            w = (" WHERE " + " AND ".join(where)) if where else ""
            page = max(1, int(q.get("page", 1) or 1))
            total = st.scalar(f"SELECT COUNT(*) FROM deadlines d{w}", args)
            rows = st.q("SELECT d.kind, d.name, d.rule, d.date, d.src, c.id, c.number, c.fio, c.sit, c.last_doc_at "
                        f"FROM deadlines d JOIN contracts c ON c.id=d.contract_id{w} ORDER BY d.date LIMIT 100 OFFSET ?",
                        args + [(page - 1) * 100])
            for r in rows:
                r["days"] = (date.fromisoformat(r["date"]) - today).days
                r["sit_label"] = rules.SIT_LABELS.get(r["sit"] or "pre", "")
                r["fresh"] = _fresh(r, p, today)
            kinds = st.q("SELECT kind, COUNT(*) n FROM deadlines GROUP BY kind")
            return self._json({"total": total, "rows": rows, "kinds": kinds})

        if head == "ip":
            p = ctx.params()
            f = q.get("filter", "stalled")
            page = max(1, int(q.get("page", 1) or 1))
            cond = {"stalled": "ip_stalled=1", "active": "ip_state='active'", "ended": "ip_state='ended'"}.get(
                f, "ip_state!='none'")
            total = st.scalar(f"SELECT COUNT(*) FROM contracts WHERE {cond}")
            rows = st.q("SELECT id, number, fio, debt, ip_state, ip_start, ip_end, ip_months, ip_stalled, fssp_paid, "
                        "last_pay_date, fssp_actual, next_dl_date, next_dl_name, last_doc_at FROM contracts "
                        f"WHERE {cond} ORDER BY ip_months DESC, id LIMIT 100 OFFSET ?", [(page - 1) * 100])
            for r in rows:
                r["fresh"] = _fresh(r, p, today)
            return self._json({"total": total, "rows": rows, "counts": {
                "stalled": st.scalar("SELECT COUNT(*) FROM contracts WHERE ip_stalled=1"),
                "active": st.scalar("SELECT COUNT(*) FROM contracts WHERE ip_state='active'"),
                "ended": st.scalar("SELECT COUNT(*) FROM contracts WHERE ip_state='ended'")}})

        # ---- шаблоны
        if head == "templates":
            return self._json([{"id": t["id"], "name": t["name"], "sub": t["sub"]} for t in templates.TEMPLATES])
        if head == "render":
            cid, tid = int(q.get("cid", 0)), q.get("tpl", "")
            if tid not in templates.BY_ID or not st.contract(cid):
                return self._err(404, "Нет такого шаблона или договора")
            return self._json(templates.render(st, cid, tid))
        if head == "manual" and method == "POST":
            d = self._jbody()
            cid = int(d.get("cid", 0))
            key = str(d.get("key", ""))
            if not st.contract(cid) or not re.fullmatch(r"[A-Za-z]{2,20}", key):
                return self._err(400, "Недопустимые данные")
            st.set_manual(cid, key, str(d.get("value", ""))[:500])
            return self._json({"ok": True})
        if head == "docx":
            cid, tid = int(q.get("cid", 0)), q.get("tpl", "")
            if tid not in templates.BY_ID or not st.contract(cid):
                return self._err(404, "Нет такого шаблона или договора")
            path = templates.make_docx(st, cid, tid, str(ctx.out))
            return self._file(path, os.path.basename(path), True)

        # ---- настройки
        if head == "settings":
            if method == "POST":
                d = self._jbody()
                for k, v in (d.get("params") or {}).items():
                    if k in rules.DEFAULTS:
                        try:
                            st.set_setting("p_" + k, str(max(0, int(v))))
                        except (TypeError, ValueError):
                            pass
                if "org_name" in d:
                    st.set_setting("org_name", str(d["org_name"])[:200])
                if d.get("recalc") and not ctx.busy():
                    ctx.run_job(Job("recalc", "Пересчёт сроков"),
                                lambda j: {"n": rules.recalc_contracts(
                                    st, progress=lambda n, m: setattr(j, "done", n), cancel=j.cancel)})
            return self._json({"params": ctx.params(), "titles": rules.PARAM_TITLES, "defaults": rules.DEFAULTS,
                               "org_name": st.get_setting("org_name", "") or "", "data_dir": str(ctx.base),
                               "version": VERSION,
                               "imports": st.q("SELECT at, file, rows, added, updated, skipped FROM imports "
                                               "ORDER BY id DESC LIMIT 10")})

        if head == "quit" and method == "POST":
            self._json({"ok": True})
            ctx.quit.set()
            return

        return self._err(404, "Нет такого запроса")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:  # без поиска имени компьютера (сеть в программе закрыта)
        self.socket.bind(self.server_address)
        self.server_name, self.server_port = "127.0.0.1", self.socket.getsockname()[1]


def _existing_instance(base: Path) -> str | None:
    """Если программа уже запущена, вернуть её ссылку."""
    f = base / "server.json"
    try:
        info = json.loads(f.read_text(encoding="utf-8"))
        s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1.5)
        s.close()
        return f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    except Exception:
        return None


def run(open_browser: bool = True) -> int:
    base = data_dir()
    link = _existing_instance(base)
    if link:
        if open_browser:
            webbrowser.open(link)
        return 0
    ctx = Ctx(base)
    Handler.ctx = ctx
    srv = _Server(("127.0.0.1", 0), Handler)
    ctx.port = srv.server_port
    (base / "server.json").write_text(json.dumps({"port": ctx.port, "token": ctx.token, "pid": os.getpid()}),
                                      encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctx.recalc_if_new_day()
    url = f"http://127.0.0.1:{ctx.port}/?t={ctx.token}"
    if os.environ.get("RAZBOR_PRINT_URL"):
        print(url, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while not ctx.quit.is_set():
            time.sleep(0.5)
            idle = time.time() - ctx.last_ping
            if ctx.seen_ping and idle > IDLE_EXIT_SEC and not ctx.busy():
                break
            if not ctx.seen_ping and idle > 600 and not ctx.busy():
                break
    finally:
        try:
            (base / "server.json").unlink()
        except OSError:
            pass
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(run())
