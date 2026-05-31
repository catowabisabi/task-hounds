# -*- mode: python ; coding: utf-8 -*-

import sys
import os

# Resolve paths relative to this spec file's directory (project root)
ROOT_DIR = os.path.abspath(globals().get('SPECPATH') or os.path.dirname(__file__))

block_cipher = None

a = Analysis(
    [os.path.join(ROOT_DIR, 'core', 'api', 'fastapi_server.py')],
    pathex=[ROOT_DIR],
    binaries=[],
    datas=[
        (os.path.join(ROOT_DIR, 'core'), 'core'),
        (os.path.join(ROOT_DIR, 'core', 'db', 'schema.sql'), 'core/db'),
        (os.path.join(ROOT_DIR, 'core', 'db', 'migrations'), 'core/db/migrations'),
        (os.path.join(ROOT_DIR, 'ui', 'web', 'dist'), 'ui/web/dist'),
    ] + (
        [(os.path.join(ROOT_DIR, '.env.example'), '.')]
        if os.path.exists(os.path.join(ROOT_DIR, '.env.example'))
        else []
    ),
    hiddenimports=[
        'core',
        'core.api',
        'core.api.fastapi_server',
        'core.api.server',
        'core.db',
        'core.db.migrations',
        'core.power_teams',
        'core.power_teams.runtime',
        'core.power_teams.runtime.opencode_lifecycle',
        'core.power_teams.runtime.opencode_supervisor',
        'core.power_teams.runtime.backend_registry',
        'core.power_teams.runtime.backends',
        'core.power_teams.runtime.backends.opencode',
        'core.power_teams.runtime.backends.hermes',
        'core.power_teams.runtime.backends.openclaw',
        'core.power_teams.runtime.backends.base',
        'core.power_teams.runtime.result_schema',
        'core.power_teams.agents',
        'core.power_teams.db',
        'core.power_teams.cli',
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
