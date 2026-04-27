# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for Manta.
#
#   pip install pyinstaller
#   pyinstaller manta.spec
#
# Result: dist/manta  (single executable). Just double-click in Terminal
# (or `chmod +x dist/manta && ./dist/manta`).

block_cipher = None


a = Analysis(
    ['manta/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'google.generativeai',
        'google.ai.generativelanguage',
        'groq',
        'rich',
        'dotenv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'PyQt5', 'PySide2', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='manta',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
