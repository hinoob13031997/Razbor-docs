"""Точка входа: программа открывается в браузере на этом компьютере (или командная строка).

Запуск программы:   python app.py
Простое окно:       python app.py --gui   (только чтение папки в Excel)
Командная строка:   python app.py --cli ПАПКА РЕЗУЛЬТАТ.xlsx [--no-ocr] [--max-pages 10] [--workers 4]
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys


_LOOP = ("127.0.0.1", "localhost", "::1", "", None)


def _block_network() -> None:
    """Запретить программе любые сетевые соединения, кроме обмена с собственным окном на этом же компьютере.

    В коде программы выхода в сеть нет, но так это ещё и проверяемо: любая попытка соединиться
    с другим адресом внутри программы завершится ошибкой.
    """
    def _refuse(*_a, **_k):
        raise OSError("Сетевые соединения в этой программе запрещены")

    def _host(addr) -> str | None:
        return addr[0] if isinstance(addr, tuple) and addr else None

    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex
    orig_addrinfo = socket.getaddrinfo

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(addr) not in _LOOP:
            _refuse()
        return orig_connect(self, addr)

    def connect_ex(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(addr) not in _LOOP:
            _refuse()
        return orig_connect_ex(self, addr)

    def addrinfo(host, *a, **k):
        if host not in _LOOP:
            _refuse()
        return orig_addrinfo(host, *a, **k)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = addrinfo  # type: ignore[assignment]


def main(argv: list[str]) -> int:
    multiprocessing.freeze_support()
    _block_network()
    if argv and argv[0] == "--cli":
        ap = argparse.ArgumentParser(prog="app.py --cli")
        ap.add_argument("folder")
        ap.add_argument("output")
        ap.add_argument("--no-ocr", action="store_true")
        ap.add_argument("--max-pages", type=int, default=10)
        ap.add_argument("--workers", type=int, default=0)
        ap.add_argument("--no-cache", action="store_true")
        ap.add_argument("--no-recursive", action="store_true")
        ap.add_argument("--fields", default=None, help="файл polya.json со своими полями")
        args = ap.parse_args(argv[1:])
        from razbor.paths import app_dir
        from razbor.pipeline import Options, run

        opts = Options(
            input_dir=args.folder, output_path=args.output, recursive=not args.no_recursive,
            use_ocr=not args.no_ocr, max_ocr_pages=args.max_pages, use_cache=not args.no_cache,
            custom_fields_path=args.fields or str(app_dir() / "polya.json"),
        )
        if args.workers > 0:
            opts.workers = args.workers

        def prog(done, total, name):
            if total:
                print(f"\r{done}/{total} {name[:50]:<50}", end="", flush=True)

        s = run(opts, prog)
        print()
        ok = sum(1 for r in s.results if not r.error)
        print(f"Готово: {ok} из {len(s.results)} файлов прочитано. Результат: {s.output_path}")
        for n in s.notes:
            print("Замечание:", n)
        return 0

    try:
        if argv and argv[0] == "--gui":
            from razbor.gui import start_gui

            start_gui()
            return 0
        from razbor.server import run

        return run(open_browser="--no-browser" not in argv)
    except Exception:  # в окне без консоли ошибку иначе не увидеть
        import traceback

        from razbor.paths import app_dir

        try:
            (app_dir() / "razbor_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
