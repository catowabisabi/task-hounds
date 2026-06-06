# -*- mode: python ; coding: utf-8 -*-

import sys
import os

# Resolve paths relative to this spec file's directory (project root)
ROOT_DIR = os.path.abspath(globals().get('SPECPATH') or os.path.dirname(__file__))

block_cipher = None

a = Analysis(
    [os.path.join(ROOT_DIR, 'core', 'task_hounds_api', '__main__.py')],
    pathex=[ROOT_DIR, os.path.join(ROOT_DIR, 'core')],
    binaries=[],
    datas=[
        (os.path.join(ROOT_DIR, 'core', 'db'), 'core/db'),
        (os.path.join(ROOT_DIR, 'ui', 'web', 'dist'), 'ui/web/dist'),
    ] + (
        [(os.path.join(ROOT_DIR, '.env.example'), '.')]
        if os.path.exists(os.path.join(ROOT_DIR, '.env.example'))
        else []
    ),
    hiddenimports=[
        'task_hounds_api',
        'task_hounds_api.__main__',
        'task_hounds_api.api',
        'task_hounds_api.api.main',
        'task_hounds_api.api.deps',
        'task_hounds_api.api.schemas',
        'task_hounds_api.api.routes',
        'task_hounds_api.api.routes.projects',
        'task_hounds_api.api.routes.agents',
        'task_hounds_api.api.routes.todos',
        'task_hounds_api.api.routes.workflow',
        'task_hounds_api.api.routes.chat',
        'task_hounds_api.api.routes.runtime',
        'task_hounds_api.api.routes.streams',
        'task_hounds_api.api.routes.settings',
        'task_hounds_api.api.routes.compat',
        'task_hounds_api.db',
        'task_hounds_api.db.connect',
        'task_hounds_api.db.ops',
        'task_hounds_api.db.ops.project',
        'task_hounds_api.db.ops.agent',
        'task_hounds_api.db.ops.todo',
        'task_hounds_api.db.ops.workflow',
        'task_hounds_api.db.ops.chat',
        'task_hounds_api.db.ops.runtime',
        'task_hounds_api.opencode',
        'task_hounds_api.opencode.config',
        'task_hounds_api.opencode.binary',
        'task_hounds_api.opencode.result',
        'task_hounds_api.opencode.process',
        'task_hounds_api.opencode.client',
        'task_hounds_api.opencode.lifecycle',
        'task_hounds_api.workflow',
        'task_hounds_api.workflow.models',
        'task_hounds_api.workflow.executor',
        'task_hounds_api.workflow.graph',
        'task_hounds_api.workflow.signals',
        'task_hounds_api.workflow.loop',
    ],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='task-hounds-fastapi-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
