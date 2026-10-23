"""Проверка логики окна без настоящего tkinter (его нет в тестовой среде).

Подставляем «пустой» tkinter и прогоняем: создание окна, запуск обработки,
опрос очереди, завершение. Внешний вид так не проверить, но опечатки и
ошибки в логике будут видны.
"""
import os, sys, time, types, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Dummy:
    def __init__(self, *a, **k): pass
    def __getattr__(self, name):
        return lambda *a, **k: None


class Var:
    def __init__(self, value=None): self.v = value
    def get(self): return self.v
    def set(self, v): self.v = v


class Tk(Dummy):
    def after(self, ms, fn=None, *a): return None


tk = types.ModuleType("tkinter")
tk.Tk = Tk
tk.StringVar = tk.BooleanVar = tk.IntVar = Var
tk.TclError = Exception
ttk = types.ModuleType("tkinter.ttk")
for n in ("Frame", "Label", "Entry", "Button", "Checkbutton", "Spinbox", "LabelFrame", "Progressbar"):
    setattr(ttk, n, Dummy)
fd = types.ModuleType("tkinter.filedialog"); mb = types.ModuleType("tkinter.messagebox"); sc = types.ModuleType("tkinter.scrolledtext")
sc.ScrolledText = Dummy
mb.showwarning = mb.showerror = lambda *a, **k: print("MSG", a)
tk.filedialog, tk.messagebox, tk.scrolledtext, tk.ttk = fd, mb, sc, ttk
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": fd,
                    "tkinter.messagebox": mb, "tkinter.scrolledtext": sc})

from razbor import gui  # noqa: E402

folder = sys.argv[1]
app = gui.App()
app.var_in.set(folder)
out = os.path.join(tempfile.mkdtemp(), "r.xlsx")
app.var_out.set(out)
app.var_pages.set(10); app.var_workers.set(3)
app._start()
for _ in range(600):
    app._poll()
    if app.result_path:
        break
    time.sleep(0.2)
print("статус:", app.var_status.get())
assert app.result_path and os.path.exists(out), "результат не создан"
print("OK, файл создан:", os.path.getsize(out), "байт")
