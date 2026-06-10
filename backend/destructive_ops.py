from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Iterator, Optional


@dataclass(frozen=True)
class ExclusiveOperation:
    key: str
    label: str
    detail: str
    started_at: datetime


class ExclusiveOperationInProgress(RuntimeError):
    def __init__(self, active: ExclusiveOperation):
        self.active = active
        super().__init__(f"{active.label} is already in progress")

    @property
    def user_message(self) -> str:
        return self.active.detail or f"{self.active.label} is already in progress. Retry when it finishes."


class ExclusiveOperationGate:
    def __init__(self) -> None:
        self._activity_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active: Optional[ExclusiveOperation] = None

    def snapshot(self) -> Optional[ExclusiveOperation]:
        with self._state_lock:
            return self._active

    def is_active(self) -> bool:
        return self.snapshot() is not None

    @contextmanager
    def coordinated_activity(self) -> Iterator[None]:
        self._activity_lock.acquire()
        try:
            yield
        finally:
            self._activity_lock.release()

    @contextmanager
    def begin(self, key: str, label: str, detail: str = "") -> Iterator[ExclusiveOperation]:
        op = ExclusiveOperation(
            key=key,
            label=label,
            detail=detail.strip(),
            started_at=datetime.now(timezone.utc),
        )
        with self._state_lock:
            if self._active is not None:
                raise ExclusiveOperationInProgress(self._active)
            self._active = op
        try:
            with self.coordinated_activity():
                yield op
        finally:
            with self._state_lock:
                if self._active == op:
                    self._active = None


exclusive_operation_gate = ExclusiveOperationGate()
