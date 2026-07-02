"""Small process-safe-enough rotation helper for append-only runtime logs."""
from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def rotate_if_needed(
    path: Path,
    *,
    incoming_bytes: int = 0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    try:
        if not path.exists() or path.stat().st_size + incoming_bytes <= max_bytes:
            return
        oldest = path.with_name(f"{path.name}.{backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        # Logging must never interrupt an agent call.
        return
