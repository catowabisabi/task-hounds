"""Shared prompt policy loaded by every project-aware agent."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agent_prompts"


@lru_cache(maxsize=1)
def project_methodology() -> str:
    path = _PROMPTS_DIR / "project_methodology.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def with_project_methodology(prompt: str) -> str:
    methodology = project_methodology()
    if not methodology:
        return prompt
    return f"{prompt.rstrip()}\n\n=== SHARED PROJECT METHOD ===\n{methodology}\n"
