# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root=Path(SPECPATH)
a=Analysis([str(root/'launch.py')],pathex=[str(root/'src')],binaries=[],
    datas=[(str(root/'src/harmonica_studio/assets'),'harmonica_studio/assets'),
           (str(root/'third_party'),'third_party')],
    hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False,optimize=0)
# Qt on Windows imports the system ICU API. Some Python distributions ship an
# incompatible ICU with the same filename; do not shadow Windows' implementation.
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in ('icuuc.dll','icudt78.dll')]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='HarmonicaStudio',debug=False,
    icon=str(root/'src/harmonica_studio/assets/studio.ico'),
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='HarmonicaStudio')
