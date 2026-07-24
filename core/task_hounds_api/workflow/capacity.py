"""Admission control for starting GraphFlow jobs.

The queue can hold many runs, but starting unbounded autonomous loops on a
small machine creates a bad failure mode: every job begins, then the local
runtime collapses under CPU, memory, or OpenCode contention. This module keeps
the policy explicit and operator-tunable through environment variables.
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass

from task_hounds_api.db.ops import graphflow_jobs as db_jobs


DEFAULT_CPU_LIMIT = 90.0
DEFAULT_MEMORY_LIMIT = 90.0


@dataclass(frozen=True)
class CapacitySnapshot:
    ok: bool
    reason: str | None
    active_jobs: int
    max_active_jobs: int
    worker_count: int
    opencode_concurrency: int
    cpu_percent: float | None
    max_cpu_percent: float
    memory_percent: float | None
    max_memory_percent: float

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "active_jobs": self.active_jobs,
            "max_active_jobs": self.max_active_jobs,
            "worker_count": self.worker_count,
            "opencode_concurrency": self.opencode_concurrency,
            "cpu_percent": self.cpu_percent,
            "max_cpu_percent": self.max_cpu_percent,
            "memory_percent": self.memory_percent,
            "max_memory_percent": self.max_memory_percent,
        }


def graphflow_worker_count() -> int:
    return _env_int("TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT", 1, minimum=1)


def opencode_concurrency() -> int:
    return _env_int("POWER_TEAMS_OPENCODE_CONCURRENCY", 3, minimum=1)


def max_active_jobs() -> int:
    configured = os.getenv("TASK_HOUNDS_MAX_ACTIVE_JOBS")
    if configured:
        return _env_int("TASK_HOUNDS_MAX_ACTIVE_JOBS", 1, minimum=1)
    return min(graphflow_worker_count(), opencode_concurrency())


def snapshot() -> CapacitySnapshot:
    active_jobs = len(db_jobs.active())
    worker_limit = graphflow_worker_count()
    oc_limit = opencode_concurrency()
    max_jobs = max_active_jobs()
    cpu = _cpu_percent()
    mem = _memory_percent()
    max_cpu = _env_float("TASK_HOUNDS_MAX_CPU_PERCENT", DEFAULT_CPU_LIMIT)
    max_mem = _env_float("TASK_HOUNDS_MAX_MEMORY_PERCENT", DEFAULT_MEMORY_LIMIT)

    reason = None
    if active_jobs >= max_jobs:
        reason = (
            f"active job capacity reached ({active_jobs}/{max_jobs}). "
            "Increase TASK_HOUNDS_MAX_ACTIVE_JOBS, TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT, "
            "and POWER_TEAMS_OPENCODE_CONCURRENCY if this machine can handle more."
        )
    elif cpu is not None and cpu >= max_cpu:
        reason = f"CPU is too busy ({cpu:.1f}% >= {max_cpu:.1f}%)."
    elif mem is not None and mem >= max_mem:
        reason = f"memory is too busy ({mem:.1f}% >= {max_mem:.1f}%)."
    elif max_jobs > worker_limit:
        reason = (
            f"max active jobs ({max_jobs}) exceeds GraphFlow workers ({worker_limit}). "
            "Increase TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT or lower TASK_HOUNDS_MAX_ACTIVE_JOBS."
        )
    elif max_jobs > oc_limit:
        reason = (
            f"max active jobs ({max_jobs}) exceeds OpenCode concurrency ({oc_limit}). "
            "Increase POWER_TEAMS_OPENCODE_CONCURRENCY or lower TASK_HOUNDS_MAX_ACTIVE_JOBS."
        )

    return CapacitySnapshot(
        ok=reason is None,
        reason=reason,
        active_jobs=active_jobs,
        max_active_jobs=max_jobs,
        worker_count=worker_limit,
        opencode_concurrency=oc_limit,
        cpu_percent=cpu,
        max_cpu_percent=max_cpu,
        memory_percent=mem,
        max_memory_percent=max_mem,
    )


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _cpu_percent() -> float | None:
    try:
        import psutil  # type: ignore

        return float(psutil.cpu_percent(interval=0.0))
    except Exception:
        pass
    if hasattr(os, "getloadavg"):
        try:
            load1, _load5, _load15 = os.getloadavg()
            cpus = os.cpu_count() or 1
            return max(0.0, min(100.0, (load1 / cpus) * 100.0))
        except OSError:
            return None
    return None


def _memory_percent() -> float | None:
    try:
        import psutil  # type: ignore

        return float(psutil.virtual_memory().percent)
    except Exception:
        pass
    if sys.platform == "win32":
        return _windows_memory_percent()
    return None


def _windows_memory_percent() -> float | None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except Exception:
        return None
    if not ok:
        return None
    return float(status.dwMemoryLoad)
