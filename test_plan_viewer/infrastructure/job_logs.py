"""Buffered, byte-accurate job log storage."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Callable
import weakref

from test_plan_viewer.process_output import decode_process_output


DEFAULT_TAIL_BYTES = 100_000
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 30.0
DEFAULT_SNAPSHOT_BYTES = 1024 * 1024


class _SharedPathLock:
    def __init__(self):
        self.lock = threading.RLock()


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: weakref.WeakValueDictionary[str, _SharedPathLock] = weakref.WeakValueDictionary()


def _lock_for_path(path: Path) -> _SharedPathLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _PATH_LOCKS_GUARD:
        shared = _PATH_LOCKS.get(key)
        if shared is None:
            shared = _SharedPathLock()
            _PATH_LOCKS[key] = shared
        return shared


@dataclass(frozen=True)
class JobLogSnapshot:
    path: str
    tail: str
    size: int
    captured_at: float

    @property
    def log_path(self) -> str:
        return self.path

    @property
    def log_tail(self) -> str:
        return self.tail

    @property
    def log_size(self) -> int:
        return self.size

    def as_updates(self) -> dict:
        return {
            "log_path": self.path,
            "log_tail": self.tail,
            "log_size": self.size,
        }


class BufferedJobLogWriter:
    """Keep one append handle, byte size, and a bounded UTF-8 tail.

    Instances that target the same path share an in-process lock.  Normal
    single-writer appends never reread the file; a size mismatch caused by a
    second writer is detected and reconciled while holding that lock.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        tail_bytes: int = DEFAULT_TAIL_BYTES,
        snapshot_interval: float = DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
        snapshot_bytes: int = DEFAULT_SNAPSHOT_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.path = Path(path)
        self.tail_bytes = int(tail_bytes)
        self.snapshot_interval = float(snapshot_interval)
        self.snapshot_bytes = int(snapshot_bytes)
        if self.tail_bytes <= 0:
            raise ValueError("tail_bytes must be positive")
        if self.snapshot_interval <= 0:
            raise ValueError("snapshot_interval must be positive")
        if self.snapshot_bytes <= 0:
            raise ValueError("snapshot_bytes must be positive")

        self._clock = clock
        self._shared_lock = _lock_for_path(self.path)
        self._file: BinaryIO | None = None
        self._tail = bytearray()
        self._size = 0
        self._closed = False
        self._opened = False
        now = float(clock())
        self._last_snapshot_at = now
        self._last_snapshot_size = 0

    def __enter__(self) -> BufferedJobLogWriter:
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    @property
    def size(self) -> int:
        with self._shared_lock.lock:
            self._sync_external_writes_locked()
            return self._size

    @property
    def tail(self) -> str:
        with self._shared_lock.lock:
            self._sync_external_writes_locked()
            return _decode_utf8_tail(bytes(self._tail))

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def opened(self) -> bool:
        return self._opened and not self._closed

    def open(self) -> BufferedJobLogWriter:
        with self._shared_lock.lock:
            self._ensure_open_locked()
        return self

    def append(self, text: str | bytes) -> JobLogSnapshot:
        """Append and flush one batch, returning its current file snapshot."""

        if isinstance(text, str):
            data = text.encode("utf-8")
        elif isinstance(text, bytes):
            data = text
        else:
            data = str(text).encode("utf-8")

        with self._shared_lock.lock:
            self._ensure_open_locked()
            self._sync_external_writes_locked()
            if data:
                self._file.write(data)
                self._file.flush()
                self._size += len(data)
                self._append_tail_locked(data)
            return self._snapshot_locked(float(self._clock()))

    def flush(self) -> None:
        with self._shared_lock.lock:
            if self._file is not None:
                self._file.flush()

    def snapshot(self, *, now: float | None = None) -> JobLogSnapshot:
        with self._shared_lock.lock:
            self._ensure_open_locked()
            self._sync_external_writes_locked()
            return self._snapshot_locked(self._resolve_now(now))

    def snapshot_due(self, *, now: float | None = None, force: bool = False) -> bool:
        """Return whether the DB cache should receive a new log checkpoint."""

        with self._shared_lock.lock:
            self._ensure_open_locked()
            self._sync_external_writes_locked()
            if force:
                return True
            if self._size <= self._last_snapshot_size:
                return False
            current = self._resolve_now(now)
            return (
                self._size - self._last_snapshot_size >= self.snapshot_bytes
                or current - self._last_snapshot_at >= self.snapshot_interval
            )

    checkpoint_due = snapshot_due

    def mark_snapshot_persisted(
        self,
        snapshot: JobLogSnapshot | None = None,
        *,
        now: float | None = None,
    ) -> None:
        """Advance checkpoint state only after the caller commits it."""

        with self._shared_lock.lock:
            self._ensure_open_locked()
            self._sync_external_writes_locked()
            persisted_size = self._size if snapshot is None else int(snapshot.size)
            if persisted_size < 0 or persisted_size > self._size:
                raise ValueError("snapshot size is outside the current log")
            self._last_snapshot_size = max(self._last_snapshot_size, persisted_size)
            self._last_snapshot_at = self._resolve_now(now)

    mark_checkpoint_persisted = mark_snapshot_persisted

    def close(self) -> None:
        with self._shared_lock.lock:
            if self._closed:
                return
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None
            self._closed = True

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError("BufferedJobLogWriter is closed")
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        self._opened = True
        self._reload_from_file_locked()
        self._last_snapshot_size = self._size

    def _sync_external_writes_locked(self) -> None:
        if self._file is None:
            return
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() != self._size:
            self._reload_from_file_locked()

    def _reload_from_file_locked(self) -> None:
        self._file.seek(0, os.SEEK_END)
        self._size = self._file.tell()
        self._file.seek(max(0, self._size - self.tail_bytes))
        self._tail = bytearray(self._file.read())
        if len(self._tail) > self.tail_bytes:
            del self._tail[: len(self._tail) - self.tail_bytes]
        self._file.seek(0, os.SEEK_END)

    def _append_tail_locked(self, data: bytes) -> None:
        if len(data) >= self.tail_bytes:
            self._tail = bytearray(data[-self.tail_bytes :])
            return
        self._tail.extend(data)
        overflow = len(self._tail) - self.tail_bytes
        if overflow > 0:
            del self._tail[:overflow]

    def _snapshot_locked(self, now: float) -> JobLogSnapshot:
        return JobLogSnapshot(
            path=str(self.path),
            tail=_decode_utf8_tail(bytes(self._tail)),
            size=self._size,
            captured_at=now,
        )

    def _resolve_now(self, now: float | None) -> float:
        return float(self._clock() if now is None else now)


def _decode_utf8_tail(data: bytes) -> str:
    if not data:
        return ""
    start = 0
    while start < len(data) and data[start] & 0xC0 == 0x80:
        start += 1
    aligned = data[start:]
    try:
        return aligned.decode("utf-8")
    except UnicodeDecodeError:
        return decode_process_output(aligned)


__all__ = [
    "BufferedJobLogWriter",
    "DEFAULT_SNAPSHOT_BYTES",
    "DEFAULT_SNAPSHOT_INTERVAL_SECONDS",
    "DEFAULT_TAIL_BYTES",
    "JobLogSnapshot",
]
