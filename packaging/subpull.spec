# PyInstaller spec for the SubPull desktop app (one-folder build).
# Build:  pyinstaller packaging/subpull.spec --noconfirm
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "packaging" / "subpull.ico")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "subpull" / "ui" / "assets"), "subpull/ui/assets"),
           (str(ROOT / "subpull" / "i18n" / "locales"), "subpull/i18n/locales"),
           (str(ROOT / "LICENSE"), ".")],
    hiddenimports=["PySide6.QtSvg"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtOpenGL",
              "PySide6.QtOpenGLWidgets", "PySide6.QtConcurrent", "PySide6.QtXml", "PySide6.QtPrintSupport"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SubPull", icon=ICON,
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SubPull", upx=False)
