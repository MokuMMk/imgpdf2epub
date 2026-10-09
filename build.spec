# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 - 生成单文件 exe。

注意: 拖放功能依赖 tkinterdnd2 自带的 tkdnd 原生库，
      必须把 tkinterdnd2/tkdnd/win-x64 目录一起打包，否则 exe 里拖放会失效。
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# --- tkinterdnd2 的 tkdnd 原生库 (只取 Windows x64，减小体积) ---
datas = collect_data_files('tkinterdnd2', includes=['tkdnd/win-x64/**'])

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PIL._tkinter_finder',
        'tkinterdnd2',
        'tkinterdnd2.TkinterDnD',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'pandas', 'matplotlib', 'pytest',
        'IPython', 'notebook', 'setuptools', 'pkg_resources',
    ],
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
    name='图片PDF转EPUB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
