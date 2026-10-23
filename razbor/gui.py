"""Окно программы (tkinter, входит в стандартный Python)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .paths import app_dir
from .pipeline import Options, run
from .reader import OcrEngine

TITLE = f"Разбор документов {__version__}"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        self.geometry("760x640")
        self.minsize(680, 560)
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.result_path: str | None = None
        self.t_start = 0.0

        self.var_in = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_ocr = tk.BooleanVar(value=True)
        self.var_sub = tk.BooleanVar(value=True)
        self.var_cache = tk.BooleanVar(value=True)
        self.var_pages = tk.IntVar(value=10)
        self.var_workers = tk.IntVar(value=Options.__dataclass_fields__["workers"].default)
        self.var_status = tk.StringVar(value="Выберите папку с документами")

        self._build()
        self._check_ocr()
        self.after(150, self._poll)

    # ---------------------------------------------------------- интерфейс
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Программа работает полностью на этом компьютере: файлы никуда не отправляются.",
                  foreground="#1a6b2f").pack(anchor="w")

        f1 = ttk.LabelFrame(self, text="Что обработать")
        f1.pack(fill="x", **pad)
        f1.columnconfigure(1, weight=1)
        ttk.Label(f1, text="Папка с документами (PDF, JPG, PNG, TIF):").grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        ttk.Entry(f1, textvariable=self.var_in).grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        ttk.Button(f1, text="Выбрать…", command=self._pick_in).grid(row=1, column=2, padx=8)
        ttk.Checkbutton(f1, text="Включая вложенные папки", variable=self.var_sub).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        f2 = ttk.LabelFrame(self, text="Куда сохранить результат (Excel)")
        f2.pack(fill="x", **pad)
        f2.columnconfigure(0, weight=1)
        ttk.Entry(f2, textvariable=self.var_out).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(f2, text="Изменить…", command=self._pick_out).grid(row=0, column=1, padx=8)

        f3 = ttk.LabelFrame(self, text="Настройки")
        f3.pack(fill="x", **pad)
        ttk.Checkbutton(f3, text="Распознавать сканы (OCR)", variable=self.var_ocr).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(f3, text="Страниц-сканов на документ (0 = все):").grid(row=0, column=1, sticky="e", padx=(20, 4))
        ttk.Spinbox(f3, from_=0, to=500, width=5, textvariable=self.var_pages).grid(row=0, column=2, sticky="w")
        ttk.Label(f3, text="Потоков:").grid(row=1, column=1, sticky="e", padx=(20, 4))
        ttk.Spinbox(f3, from_=1, to=16, width=5, textvariable=self.var_workers).grid(row=1, column=2, sticky="w")
        ttk.Checkbutton(f3, text="Запоминать распознанный текст (можно остановиться и продолжить)",
                        variable=self.var_cache).grid(row=1, column=0, sticky="w", padx=8, pady=4)

        f4 = ttk.Frame(self)
        f4.pack(fill="x", **pad)
        self.btn_start = ttk.Button(f4, text="Начать обработку", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(f4, text="Остановить", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_open = ttk.Button(f4, text="Открыть результат", command=self._open_result, state="disabled")
        self.btn_open.pack(side="right")
        self.btn_folder = ttk.Button(f4, text="Папка с результатом", command=self._open_folder, state="disabled")
        self.btn_folder.pack(side="right", padx=8)

        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", **pad)
        ttk.Label(self, textvariable=self.var_status).pack(anchor="w", padx=10)

        self.log = scrolledtext.ScrolledText(self, height=12, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, **pad)

    def _check_ocr(self) -> None:
        eng = OcrEngine()
        if not eng.available:
            self._log("Внимание: " + (eng.problem() or "модуль распознавания не найден") +
                      " Текстовые PDF будут обработаны, сканы нет.")
        else:
            self._log("Модуль распознавания сканов найден.")

    # ---------------------------------------------------------- действия
    def _pick_in(self) -> None:
        d = filedialog.askdirectory(title="Папка с документами")
        if d:
            self.var_in.set(d)
            if not self.var_out.get():
                stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
                self.var_out.set(os.path.join(d, f"Результат_разбора_{stamp}.xlsx"))

    def _pick_out(self) -> None:
        p = filedialog.asksaveasfilename(title="Сохранить результат", defaultextension=".xlsx",
                                         filetypes=[("Excel", "*.xlsx")],
                                         initialfile=os.path.basename(self.var_out.get() or "Результат_разбора.xlsx"))
        if p:
            self.var_out.set(p)

    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        folder = self.var_in.get().strip()
        out = self.var_out.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(TITLE, "Выберите папку с документами.")
            return
        if not out:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
            out = os.path.join(folder, f"Результат_разбора_{stamp}.xlsx")
            self.var_out.set(out)
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
            self.var_out.set(out)
        try:
            pages = int(self.var_pages.get())
            workers = int(self.var_workers.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(TITLE, "Проверьте числа в настройках.")
            return
        opts = Options(
            input_dir=folder, output_path=out, recursive=self.var_sub.get(),
            use_ocr=self.var_ocr.get(), max_ocr_pages=max(0, pages), workers=max(1, workers),
            use_cache=self.var_cache.get(),
            custom_fields_path=str(app_dir() / "polya.json"),
        )
        self.cancel.clear()
        self.result_path = None
        self.bar.configure(value=0, maximum=1)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_open.configure(state="disabled")
        self.btn_folder.configure(state="disabled")
        self._log(f"Начало: {folder}")
        self.t_start = time.time()

        def work() -> None:
            try:
                s = run(opts, lambda d, t, n: self.q.put(("progress", d, t, n)), self.cancel)
                self.q.put(("done", s))
            except PermissionError:
                self.q.put(("fail", "Не удаётся записать результат. Возможно, файл Excel открыт. Закройте его или выберите другое имя."))
            except Exception as e:  # показываем пользователю, а не молча закрываемся
                self.q.put(("fail", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Останавливаю… дождитесь завершения текущих файлов")

    def _poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, name = msg
                    self.bar.configure(maximum=max(total, 1), value=done)
                    eta = ""
                    if done and total and done < total:
                        sec = (time.time() - self.t_start) / done * (total - done)
                        eta = f", осталось примерно {self._fmt(sec)}"
                    self.var_status.set(f"Обработано {done} из {total}{eta}")
                    if name and (done % 25 == 0 or done == total):
                        self._log(f"{done}/{total}  {name}")
                elif kind == "done":
                    self._finish(msg[1])
                elif kind == "fail":
                    self._reset_buttons()
                    self._log("Ошибка: " + msg[1])
                    messagebox.showerror(TITLE, msg[1])
        except queue.Empty:
            pass
        self.after(150, self._poll)

    @staticmethod
    def _fmt(sec: float) -> str:
        sec = int(sec)
        if sec < 90:
            return f"{sec} с"
        if sec < 5400:
            return f"{sec // 60} мин"
        return f"{sec // 3600} ч {sec % 3600 // 60} мин"

    def _reset_buttons(self) -> None:
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _finish(self, s) -> None:
        self._reset_buttons()
        ok = sum(1 for r in s.results if not r.error)
        bad = len(s.results) - ok
        chk = sum(1 for r in s.results if not r.error and r.reasons)
        for n in s.notes:
            self._log("Замечание: " + n)
        word = "Остановлено" if s.cancelled else "Готово"
        self.var_status.set(f"{word}. Прочитано {ok}, не прочитано {bad}, проверить вручную {chk}. "
                            f"Время: {self._fmt((s.finished - s.started).total_seconds())}")
        self._log(self.var_status.get())
        self._log("Результат: " + s.output_path)
        self.result_path = s.output_path
        self.btn_open.configure(state="normal")
        self.btn_folder.configure(state="normal")

    def _open_result(self) -> None:
        if self.result_path and os.path.exists(self.result_path):
            _open(self.result_path)

    def _open_folder(self) -> None:
        if self.result_path:
            _open(os.path.dirname(os.path.abspath(self.result_path)))


def _open(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror(TITLE, f"Не удалось открыть: {e}")


def start_gui() -> None:
    App().mainloop()
