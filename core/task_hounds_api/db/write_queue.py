"""Single-writer priority queue for critical SQLite mutations."""
from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import Future
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class DatabaseWriteQueue:
    _instance: "DatabaseWriteQueue | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "DatabaseWriteQueue":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._items: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="sqlite-write-queue",
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            _priority, _sequence, func, future = self._items.get()
            try:
                if not future.cancelled():
                    future.set_result(func())
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                self._items.task_done()

    def submit(self, func: Callable[[], T], priority: int = 50) -> T:
        if threading.current_thread() is self._thread:
            return func()
        future: Future[T] = Future()
        self._items.put((priority, next(self._counter), func, future))
        return future.result()


def write(func: Callable[[], T], priority: int = 50) -> T:
    return DatabaseWriteQueue.instance().submit(func, priority)
