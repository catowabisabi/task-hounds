# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Resolve paths relative to this spec file's directory (project root)
ROOT_DIR = os.path.abspath(globals().get('SPECPATH') or os.path.dirname(__file__))

block_cipher = None

collected_datas = []
collected_binaries = []
collected_hiddenimports = []
for package in ('langgraph', 'pydantic', 'fastapi', 'uvicorn'):
    datas, binaries, hiddenimports = collect_all(package)
    collected_datas += datas
    collected_binaries += binaries
    collected_hiddenimports += hiddenimports

a = Analysis(
    [os.path.join(ROOT_DIR, 'core', 'task_hounds_api', 'desktop_runtime.py')],
    pathex=[ROOT_DIR, os.path.join(ROOT_DIR, 'core')],
    binaries=collected_binaries,
    datas=[
        (os.path.join(ROOT_DIR, 'core', 'db'), 'core/db'),
        (os.path.join(ROOT_DIR, 'ui', 'web', 'dist'), 'ui/web/dist'),
    ] + collected_datas + (
        [(os.path.join(ROOT_DIR, '.env.example'), '.')]
        if os.path.exists(os.path.join(ROOT_DIR, '.env.example'))
        else []
    ),
    hiddenimports=collect_submodules('task_hounds_api') + collected_hiddenimports,
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
    name='task-hounds-runtime',
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    a.datas,
    a.zipfiles,
    name='task-hounds-runtime',
)
