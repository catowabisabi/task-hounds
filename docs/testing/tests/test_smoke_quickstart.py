"""Smoke tests guarding the README quick start path.

These tests exist because a broken quick start is the single most
expensive bug for an open-source project: it fails every new user at
step one. They verify that

1. the documented entry points actually exist and parse arguments,
2. every file the README quick start references is present, and
3. the README never regresses to the removed ``core/api/server.py`` path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_CORE = _REPO / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def test_module_entry_point_help_runs():
    """`python -m task_hounds_api --help` (the documented run command)
    must exit 0 without starting a server."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_CORE) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "task_hounds_api", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(_REPO),
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout


def test_console_script_targets_exist():
    """Every [project.scripts] target in pyproject.toml must be importable
    and callable."""
    import importlib
    import re

    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    scripts_section = pyproject.split("[project.scripts]", 1)[1]
    targets = re.findall(r'=\s*"([\w.]+):(\w+)"', scripts_section)
    assert targets, "no console scripts found in pyproject.toml"
    for module_name, attr in targets:
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), f"{module_name}:{attr} is not callable"


# ---------------------------------------------------------------------------
# README quick start integrity
# ---------------------------------------------------------------------------

def test_readme_does_not_reference_removed_server_path():
    for readme in ("README.md", "README.zh-TW.md"):
        text = (_REPO / readme).read_text(encoding="utf-8")
        assert "core/api/server.py" not in text.replace("\\", "/"), (
            f"{readme} references the removed core/api/server.py entry point"
        )


def test_readme_referenced_files_exist():
    required = [
        "installation.cmd",
        ".env.example",
        "docker-compose.yml",
        "Dockerfile",
        "build_exe.ps1",
        "ui/web/package.json",
        "docs/guides/getting-started.md",
        "docs/architecture/agent-loop-contract.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
    ]
    missing = [rel for rel in required if not (_REPO / rel).exists()]
    assert not missing, f"README references missing files: {missing}"


def test_readme_architecture_tree_matches_repo():
    for rel in (
        "core/db",
        "core/task_hounds_api/api",
        "core/task_hounds_api/db",
        "core/task_hounds_api/opencode",
        "core/task_hounds_api/workflow",
        "ui/web",
        "ui/desktop",
    ):
        assert (_REPO / rel).is_dir(), f"README architecture tree lists missing dir: {rel}"


# ---------------------------------------------------------------------------
# Default project path
# ---------------------------------------------------------------------------

def test_default_project_path_env_override(monkeypatch, tmp_path):
    from task_hounds_api.api import main

    monkeypatch.setenv("TASK_HOUNDS_PROJECTS_DIR", str(tmp_path / "custom"))
    result = main._default_project_path()
    assert result == tmp_path / "custom" / "default-project"
    assert result.is_dir()


def test_default_project_path_defaults_to_home(monkeypatch, tmp_path):
    from task_hounds_api.api import main

    monkeypatch.delenv("TASK_HOUNDS_PROJECTS_DIR", raising=False)
    monkeypatch.setattr(main.Path, "home", staticmethod(lambda: tmp_path))
    # Force the non-Windows branch so a real legacy C:\task-hounds-projects
    # folder on a developer machine cannot affect the assertion.
    monkeypatch.setattr(main.os, "name", "posix")
    result = main._default_project_path()
    assert result == tmp_path / "task-hounds-projects" / "default-project"
    assert result.is_dir()
