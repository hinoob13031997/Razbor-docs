"""Локальная база программы (SQLite): портфель из CRM, документы, сроки, история.

Всё хранится в одном файле на этом компьютере. Схему можно перенести на общий сервер,
когда понадобится общая база: таблицы и поля те же.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from typing import Iterable

# Поля договора, которые приходят из выгрузки CRM (всё остальное считает программа).
CRM_COLS = [
    "number", "contract_date", "fio", "fio_norm", "surname_key", "birth", "inn", "kind",
    "debt", "principal_left", "principal_court", "principal_granted", "principal_cession",
    "principal_initial", "penalties", "fee", "last_pay_date", "last_pay_sum", "overdue_end",
    "status", "responsible", "creditor", "court", "suit_date", "case_no", "case_norm",
    "decision_date", "satisfied", "il_type", "il_no", "il_norm", "il_date", "fssp_date",
    "fssp_otdel", "fssp_paid", "fssp_actual", "bankrupt", "bankrupt_date", "bankrupt_case",
    "death_date", "estate_no", "notary", "cession_no", "cession_date", "address",
]
REAL_COLS = {"debt", "principal_left", "principal_court", "principal_granted", "principal_cession",
             "principal_initial", "penalties", "fee", "last_pay_sum", "fssp_paid"}

DERIVED_COLS = ["sit", "next_dl_date", "next_dl_name", "ip_state", "ip_start", "ip_end",
                "ip_stalled", "ip_months"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts(
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL, number_norm TEXT NOT NULL UNIQUE,
  contract_date TEXT, fio TEXT, fio_norm TEXT, surname_key TEXT, birth TEXT, inn TEXT, kind TEXT,
  debt REAL, principal_left REAL, principal_court REAL, principal_granted REAL, principal_cession REAL,
  principal_initial REAL, penalties REAL, fee REAL, last_pay_date TEXT, last_pay_sum REAL, overdue_end TEXT,
  status TEXT, responsible TEXT, creditor TEXT, court TEXT, suit_date TEXT, case_no TEXT, case_norm TEXT,
  decision_date TEXT, satisfied TEXT, il_type TEXT, il_no TEXT, il_norm TEXT, il_date TEXT, fssp_date TEXT,
  fssp_otdel TEXT, fssp_paid REAL, fssp_actual TEXT, bankrupt INTEGER, bankrupt_date TEXT, bankrupt_case TEXT,
  death_date TEXT, estate_no TEXT, notary TEXT, cession_no TEXT, cession_date TEXT, address TEXT,
  sit TEXT, next_dl_date TEXT, next_dl_name TEXT, ip_state TEXT, ip_start TEXT, ip_end TEXT,
  ip_stalled INTEGER DEFAULT 0, ip_months INTEGER,
  last_doc_at TEXT, last_doc_by TEXT, docs_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_c_inn ON contracts(inn);
CREATE INDEX IF NOT EXISTS ix_c_birth ON contracts(birth);
CREATE INDEX IF NOT EXISTS ix_c_surname ON contracts(surname_key);
CREATE INDEX IF NOT EXISTS ix_c_case ON contracts(case_norm);
CREATE INDEX IF NOT EXISTS ix_c_il ON contracts(il_norm);
CREATE INDEX IF NOT EXISTS ix_c_sit ON contracts(sit);
CREATE INDEX IF NOT EXISTS ix_c_dl ON contracts(next_dl_date);
CREATE INDEX IF NOT EXISTS ix_c_stalled ON contracts(ip_stalled);

CREATE TABLE IF NOT EXISTS deadlines(
  contract_id INTEGER NOT NULL, kind TEXT, name TEXT, rule TEXT, date TEXT, src TEXT
);
CREATE INDEX IF NOT EXISTS ix_d_date ON deadlines(date);
CREATE INDEX IF NOT EXISTS ix_d_contract ON deadlines(contract_id);

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, contract_id INTEGER, sha TEXT, text_hash TEXT, name TEXT, ext TEXT,
  doc_key TEXT, doc_label TEXT, grp TEXT, reasons TEXT, fields TEXT, loaded_by TEXT, loaded_at TEXT,
  stored TEXT, pages INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_sha ON documents(sha);
CREATE INDEX IF NOT EXISTS ix_doc_text ON documents(text_hash);
CREATE INDEX IF NOT EXISTS ix_doc_contract ON documents(contract_id);

CREATE TABLE IF NOT EXISTS facts(
  contract_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, doc_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_f_contract ON facts(contract_id);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, contract_id INTEGER NOT NULL, date TEXT, text TEXT, doc_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_e_contract ON events(contract_id);

CREATE TABLE IF NOT EXISTS manual(
  contract_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(contract_id, key)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, added_at TEXT, added_by TEXT, last_scan TEXT, batch_id TEXT
);
CREATE TABLE IF NOT EXISTS src_files(
  path TEXT PRIMARY KEY, src_id INTEGER, size INTEGER, mtime REAL, status TEXT, batch_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_sf_src ON src_files(src_id);
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY, at TEXT, file TEXT, rows INTEGER, added INTEGER, updated INTEGER,
  skipped INTEGER, unmapped TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.write() as c:
            c.executescript(SCHEMA)
            have = {r[1] for r in c.execute("PRAGMA table_info(contracts)")}
            for col, typ in (("next_action", "TEXT"), ("next_action_level", "INTEGER")):
                if col not in have:  # база от прошлой версии
                    c.execute(f"ALTER TABLE contracts ADD COLUMN {col} {typ}")

    # ------------------------------------------------------------ соединения
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
        return c

    class _W:
        def __init__(self, store: "Store"):
            self.s = store

        def __enter__(self):
            self.s._write_lock.acquire()
            return self.s.conn()

        def __exit__(self, et, ev, tb):
            try:
                if et is None:
                    self.s.conn().commit()
                else:
                    self.s.conn().rollback()
            finally:
                self.s._write_lock.release()
            return False

    def write(self) -> "Store._W":
        """with store.write() as c: ... — запись под замком, с сохранением в конце."""
        return Store._W(self)

    def q(self, sql: str, args: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable = ()) -> dict | None:
        r = self.conn().execute(sql, tuple(args)).fetchone()
        return dict(r) if r is not None else None

    def scalar(self, sql: str, args: Iterable = (), default=0):
        r = self.conn().execute(sql, tuple(args)).fetchone()
        return default if r is None or r[0] is None else r[0]

    # ------------------------------------------------------------ настройки
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self.write() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    # ------------------------------------------------------------ портфель
    def upsert_contracts(self, rows: list[dict], norm_numbers: list[str]) -> tuple[int, int]:
        """Добавить или обновить договоры пачкой. Возвращает (добавлено, обновлено)."""
        if not rows:
            return 0, 0
        use = [k for k in CRM_COLS if k in rows[0]]  # обновляем только колонки, которые есть в выгрузке
        cols = ["number_norm"] + use
        ph = ",".join("?" * len(cols))
        # пустое значение в новой строке не затирает известное (несколько строк на договор, частичные выгрузки)
        upd = ",".join(f"{c}=COALESCE(excluded.{c}, contracts.{c})" for c in use if c != "number")
        sql = f"INSERT INTO contracts({','.join(cols)}) VALUES({ph}) ON CONFLICT(number_norm) DO UPDATE SET {upd}"
        with self.write() as c:
            before = c.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
            c.executemany(sql, [[nn] + [r.get(k) for k in use] for nn, r in zip(norm_numbers, rows)])
            after = c.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        added = after - before
        return added, len(rows) - added

    def contract(self, cid: int) -> dict | None:
        return self.one("SELECT * FROM contracts WHERE id=?", (cid,))

    def contracts_by_numbers(self, norms: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(norms), 400):
            part = norms[i:i + 400]
            out += self.q(f"SELECT * FROM contracts WHERE number_norm IN ({','.join('?' * len(part))})", part)
        return out

    def contracts_by(self, column: str, values: list[str]) -> list[dict]:
        assert column in ("inn", "birth", "surname_key", "case_norm", "il_norm")
        out: list[dict] = []
        for i in range(0, len(values), 400):
            part = values[i:i + 400]
            out += self.q(f"SELECT * FROM contracts WHERE {column} IN ({','.join('?' * len(part))})", part)
        return out

    def list_contracts(self, q: str = "", sit: str = "", fresh: str = "", page: int = 1, size: int = 50,
                       today: str | None = None, fresh_days: int = 14, stale_days: int = 30) -> dict:
        where, args = [], []
        q = (q or "").strip()
        if q:
            from .crm import norm_number, norm_text
            nn = norm_number(q)
            like = norm_text(q)
            where.append("(number_norm LIKE ? OR fio_norm LIKE ? OR inn LIKE ?)")
            args += [f"%{nn}%", f"%{like}%", f"{re.sub(r'[^0-9]', '', q) or '@@'}%"]
        if sit == "urgent":
            where.append("next_dl_date IS NOT NULL AND next_dl_date <= ?")
            args.append(_plus_days(today, 30))
        elif sit == "stalled":
            where.append("ip_stalled=1")
        elif sit:
            where.append("sit=?")
            args.append(sit)
        if fresh == "none":
            where.append("last_doc_at IS NULL")
        elif fresh == "fresh":
            where.append("last_doc_at >= ?")
            args.append(_plus_days(today, -fresh_days))
        elif fresh == "stale":
            where.append("last_doc_at < ?")
            args.append(_plus_days(today, -stale_days))
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.scalar(f"SELECT COUNT(*) FROM contracts{w}", args)
        rows = self.q(
            "SELECT id, number, fio, kind, debt, principal_left, sit, next_dl_date, next_dl_name, ip_state, ip_start, ip_end,"
            " ip_months, ip_stalled, last_doc_at, last_doc_by, docs_count, next_action, next_action_level FROM contracts" + w +
            " ORDER BY next_dl_date IS NULL, next_dl_date, id LIMIT ? OFFSET ?", args + [size, (max(1, page) - 1) * size])
        return {"total": total, "rows": rows}

    def sit_counts(self) -> dict[str, int]:
        return {r["sit"] or "pre": r["n"] for r in self.q("SELECT sit, COUNT(*) n FROM contracts GROUP BY sit")}

    # ------------------------------------------------------------ документы и факты
    def doc_by_sha(self, sha: str) -> dict | None:
        return self.one("SELECT d.*, c.number AS c_number, c.fio AS c_fio FROM documents d "
                        "LEFT JOIN contracts c ON c.id=d.contract_id WHERE d.sha=?", (sha,))

    def doc_by_text_hash(self, th: str) -> dict | None:
        if not th:
            return None
        return self.one("SELECT d.*, c.number AS c_number FROM documents d LEFT JOIN contracts c ON c.id=d.contract_id "
                        "WHERE d.text_hash=? LIMIT 1", (th,))

    def facts_for(self, cid: int) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.q("SELECT key, value FROM facts WHERE contract_id=? ORDER BY doc_id", (cid,)):
            k, v = r["key"], r["value"]
            if v is None:
                continue
            if k.endswith("_date") and k in out and re.match(r"\d{4}-\d{2}-\d{2}$", v):
                out[k] = max(out[k], v)
            else:
                out[k] = v
        return out

    def all_facts(self, ids: list[int] | None = None) -> dict[int, dict[str, str]]:
        res: dict[int, dict[str, str]] = {}
        if ids is not None:
            rows = []
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                rows += self.conn().execute(f"SELECT contract_id, key, value FROM facts WHERE contract_id IN "
                                            f"({','.join('?' * len(part))}) ORDER BY doc_id", part).fetchall()
        else:
            rows = self.conn().execute("SELECT contract_id, key, value FROM facts ORDER BY doc_id")
        for r in rows:
            d = res.setdefault(r[0], {})
            k, v = r[1], r[2]
            if v is None:
                continue
            if k.endswith("_date") and k in d and re.match(r"\d{4}-\d{2}-\d{2}$", v):
                d[k] = max(d[k], v)
            else:
                d[k] = v
        return res

    def manual_for(self, cid: int) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.q("SELECT key, value FROM manual WHERE contract_id=?", (cid,))}

    def set_manual(self, cid: int, key: str, value: str) -> None:
        with self.write() as c:
            if value.strip():
                c.execute("INSERT INTO manual(contract_id,key,value) VALUES(?,?,?) ON CONFLICT(contract_id,key) "
                          "DO UPDATE SET value=excluded.value", (cid, key, value.strip()))
            else:
                c.execute("DELETE FROM manual WHERE contract_id=? AND key=?", (cid, key))

    def deadlines_for(self, cid: int) -> list[dict]:
        return self.q("SELECT kind, name, rule, date, src FROM deadlines WHERE contract_id=? ORDER BY date", (cid,))

    def events_for(self, cid: int) -> list[dict]:
        return self.q("SELECT date, text, doc_id FROM events WHERE contract_id=? ORDER BY date, id", (cid,))

    def documents_for(self, cid: int) -> list[dict]:
        rows = self.q("SELECT id, name, doc_key, doc_label, grp, reasons, fields, loaded_by, loaded_at, pages, "
                      "stored IS NOT NULL AS has_file, stored AS path "
                      "FROM documents WHERE contract_id=? ORDER BY id DESC", (cid,))
        for r in rows:
            r["reasons"] = json.loads(r["reasons"] or "[]")
            r["fields"] = json.loads(r["fields"] or "[]")
        return rows

    def log_import(self, file: str, rows: int, added: int, updated: int, skipped: int, unmapped: list[str]) -> None:
        with self.write() as c:
            c.execute("INSERT INTO imports(at,file,rows,added,updated,skipped,unmapped) VALUES(?,?,?,?,?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), file, rows, added, updated, skipped,
                       json.dumps(unmapped, ensure_ascii=False)))

    def last_import(self) -> dict | None:
        r = self.one("SELECT * FROM imports ORDER BY id DESC LIMIT 1")
        if r:
            r["unmapped"] = json.loads(r["unmapped"] or "[]")
        return r


def _plus_days(today: str | None, n: int) -> str:
    from datetime import timedelta
    d = date.fromisoformat(today) if today else date.today()
    return (d + timedelta(days=n)).isoformat()
