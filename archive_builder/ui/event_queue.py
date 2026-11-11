from __future__ import annotations

import queue
import threading


class CoalescingEventQueue:
    def __init__(self, max_events: int = 256) -> None:
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=max_events)
        self._lock = threading.Lock()
        self._latest_progress = None
        self._progress_pending = False

    def put_progress(self, payload: object) -> None:
        with self._lock:
            self._latest_progress = payload
            if self._progress_pending:
                return
            try:
                self._queue.put_nowait(("progress", None))
            except queue.Full:
                return
            self._progress_pending = True

    def put(self, item: tuple[str, object]) -> None:
        if item[0] == "progress":
            self.put_progress(item[1])
        else:
            self._queue.put(item)

    def get_nowait(self) -> tuple[str, object]:
        kind, payload = self._queue.get_nowait()
        if kind != "progress":
            return kind, payload
        with self._lock:
            payload = self._latest_progress
            self._latest_progress = None
            self._progress_pending = False
        return kind, payload
