# -*- mode: python ; coding: utf-8 -*-
# Сборка: python -m PyInstaller --noconfirm --clean razbor.spec
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [("web", "web")], [], []
for pkg in ("pypdfium2", "pypdfium2_raw"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["razbor.gui", "razbor.server", "razbor.batch", "razbor.store", "razbor.crm", "razbor.rules", "razbor.matcher", "razbor.templates", "razbor.docx_out", "sqlite3"],
    excludes=["numpy", "pandas", "matplotlib", "scipy", "cv2", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RazborDocs", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="RazborDocs")
