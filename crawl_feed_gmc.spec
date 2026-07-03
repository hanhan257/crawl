# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — chay: pyinstaller crawl_feed_gmc.spec"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
root = Path(SPECPATH)

datas = [
    (str(root / "app.py"), "."),
    (str(root / "app_brand.py"), "."),
    (str(root / ".streamlit"), ".streamlit"),
    (str(root / "utils"), "utils"),
    (str(root / "models"), "models"),
    (str(root / "crawlers"), "crawlers"),
    (str(root / "exporters"), "exporters"),
    (str(root / "ui"), "ui"),
    (str(root / "config.example.json"), "."),
]

hiddenimports = collect_submodules("utils")
hiddenimports += collect_submodules("models")
hiddenimports += collect_submodules("crawlers")
hiddenimports += collect_submodules("exporters")
hiddenimports += collect_submodules("ui")

for package in ("streamlit", "pandas", "altair", "pyarrow", "PIL", "click"):
    try:
        _datas, _binaries, _hidden = collect_all(package)
        datas += _datas
        hiddenimports += _hidden
    except Exception:
        pass

a = Analysis(
    ["launcher.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Crawl Feed GMC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Crawl Feed GMC",
)
