"""Bounded, stoppable background reading for OpenCode SSE responses."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Callable, Iterable, Literal

SseStreamItemKind = Literal["event", "eof", "error"]


def iter_sse_events(response):
    """Parse an iterable byte response without importing the Flask web package."""

    event_name = None
    data_lines = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


@dataclass(frozen=True)
class SseStreamItem:
    """One parsed SSE event or an explicit terminal reader state."""

    kind: SseStreamItemKind
    event: str | None = None
    data: str | None = None
    error: Exception | None = None


class BoundedSseReader:
    """Read a blocking SSE response on a daemon thread with bounded buffering."""

    def __init__(
        self,
        response,
        *,
        max_queue_size: int = 256,
        join_timeout: float = 1.0,
        event_iterator: Callable[[object], Iterable[tuple[str | None, str]]] = (
            iter_sse_events
        ),
        thread_name: str = "opencode-sse-reader",
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive.")
        if join_timeout < 0:
            raise ValueError("join_timeout must be non-negative.")

        self.response = response
        self.max_queue_size = int(max_queue_size)
        self.join_timeout = float(join_timeout)
        self._event_iterator = event_iterator
        self._queue: queue.Queue[SseStreamItem] = queue.Queue(
            maxsize=self.max_queue_size
        )
        self._stop_event = threading.Event()
        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> "BoundedSseReader":
        with self._start_lock:
            if self._closed:
                raise RuntimeError("Cannot start a closed SSE reader.")
            if not self._started:
                self._started = True
                self._thread.start()
        return self

    def _publish(self, item: SseStreamItem) -> bool:
        while not self._stop_event.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _run(self) -> None:
        try:
            for event_name, data in self._event_iterator(self.response):
                if not self._publish(
                    SseStreamItem(kind="event", event=event_name, data=data)
                ):
                    return
            self._publish(SseStreamItem(kind="eof"))
        except Exception as exc:
            if not self._stop_event.is_set():
                self._publish(SseStreamItem(kind="error", error=exc))

    def get(self, timeout: float | None = None) -> SseStreamItem:
        """Get the next item, raising ``queue.Empty`` when the timeout elapses."""

        return self._queue.get(timeout=timeout)

    def poll(self, timeout: float = 0.0) -> SseStreamItem | None:
        """Get the next item or return ``None`` so callers can check deadlines."""

        try:
            return self.get(timeout=timeout)
        except queue.Empty:
            return None

    def join(self, timeout: float | None = None) -> bool:
        if self._started:
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def close(self) -> bool:
        """Close the HTTP response and wait only for the configured bound."""

        self._closed = True
        self._stop_event.set()
        close_response = getattr(self.response, "close", None)
        if callable(close_response):
            try:
                close_response()
            except Exception:
                pass
        return self.join(timeout=self.join_timeout)

    def __enter__(self) -> "BoundedSseReader":
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


__all__ = ["BoundedSseReader", "SseStreamItem", "SseStreamItemKind"]
