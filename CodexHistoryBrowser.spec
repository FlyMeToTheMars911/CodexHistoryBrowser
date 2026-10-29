# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / 'desktop.py')],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / 'static'), 'static')],
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium',
                   'zstandard', 'zstandard.backend_c'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3', 'tkinter',
              'webview.platforms.gtk', 'webview.platforms.qt', 'webview.platforms.cocoa'],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas,
    [], name='CodexHistoryBrowser', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
)
