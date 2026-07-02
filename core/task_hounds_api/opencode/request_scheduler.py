"""Event-driven admission control for blocking OpenCode calls."""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class OpenCodeRequestScheduler:
    _instance: "OpenCodeRequestScheduler | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "OpenCodeRequestScheduler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._server_limit = max(1, int(os.getenv("POWER_TEAMS_OPENCODE_CONCURRENCY", "3")))
        self._server_slots: dict[str, asyncio.Semaphore] = {}
        self._project_graph_locks: dict[str, asyncio.Lock] = {}
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="opencode-request-scheduler",
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _admit(
        self,
        server_key: str,
        project_session_id: str | None,
        purpose: str,
        func: Callable[[], T],
    ) -> T:
        server_slot = self._server_slots.setdefault(
            server_key, asyncio.Semaphore(self._server_limit)
        )
        project_lock = None
        if project_session_id and purpose == "graph":
            project_lock = self._project_graph_locks.setdefault(
                project_session_id, asyncio.Lock()
            )
        async with server_slot:
            if project_lock is not None:
                async with project_lock:
                    return await asyncio.to_thread(func)
            return await asyncio.to_thread(func)

    def run(
        self,
        *,
        host: str,
        port: int,
        project_session_id: str | None,
        purpose: str,
        func: Callable[[], T],
        timeout: int | None,
    ) -> T:
        future = asyncio.run_coroutine_threadsafe(
            self._admit(
                f"{host}:{port}",
                project_session_id,
                purpose,
                func,
            ),
            self._loop,
        )
        wait_timeout = None if timeout is None else timeout + 30
        try:
            return future.result(timeout=wait_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("OpenCode request expired while queued or running")


def scheduled_call(**kwargs):
    return OpenCodeRequestScheduler.instance().run(**kwargs)
