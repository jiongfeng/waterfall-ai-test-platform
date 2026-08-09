"""Pure batching primitives for high-frequency Agent model output."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


DEFAULT_FLUSH_BYTES = 4 * 1024
DEFAULT_FLUSH_INTERVAL_SECONDS = 0.5
DEFAULT_MAX_BATCH_BYTES = 16 * 1024


@dataclass(frozen=True)
class AgentOutputBatch:
    """One ordered, UTF-8-safe batch of model output."""

    text: str
    chunk_count: int
    byte_size: int
    first_at: float
    last_at: float
    flushed_at: float
    reason: str

    def metadata(self) -> dict:
        """Return the compatibility metadata stored with a batched event."""

        return {
            "batched": True,
            "chunk_count": self.chunk_count,
            "byte_size": self.byte_size,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "flush_reason": self.reason,
        }


class AgentOutputBatcher:
    """Aggregate small text deltas by size or elapsed wall-clock time.

    ``add`` and ``flush_due`` return completed batches to the caller.  The
    caller remains responsible for persisting returned batches, which keeps
    this class deterministic and independent of files or databases.
    """

    def __init__(
        self,
        *,
        flush_bytes: int = DEFAULT_FLUSH_BYTES,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ):
        flush_bytes = int(flush_bytes)
        max_batch_bytes = int(max_batch_bytes)
        flush_interval = float(flush_interval)
        if flush_bytes <= 0:
            raise ValueError("flush_bytes must be positive")
        if max_batch_bytes <= 0:
            raise ValueError("max_batch_bytes must be positive")
        if flush_bytes > max_batch_bytes:
            raise ValueError("flush_bytes cannot exceed max_batch_bytes")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be positive")

        self.flush_bytes = flush_bytes
        self.flush_interval = flush_interval
        self.max_batch_bytes = max_batch_bytes
        self._clock = clock
        self._parts: list[str] = []
        self._byte_size = 0
        self._chunk_count = 0
        self._first_at: float | None = None
        self._last_at: float | None = None
        self._last_flush_at = float(clock())
        self._closed = False

    @property
    def has_pending(self) -> bool:
        return bool(self._parts)

    @property
    def pending_bytes(self) -> int:
        return self._byte_size

    @property
    def pending_chunk_count(self) -> int:
        return self._chunk_count

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def next_flush_in(self) -> float | None:
        """Seconds until the pending time threshold, or ``None`` when empty."""

        if not self.has_pending:
            return None
        return max(0.0, self.flush_interval - (float(self._clock()) - self._last_flush_at))

    def add(self, text: str, *, now: float | None = None) -> list[AgentOutputBatch]:
        """Append one upstream delta and return every batch it completes."""

        if self._closed:
            raise RuntimeError("AgentOutputBatcher is closed")
        if text is None:
            return []
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return []

        current = self._resolve_now(now)
        batches: list[AgentOutputBatch] = []
        due = self.flush_due(now=current)
        if due is not None:
            batches.append(due)

        remaining = text
        while remaining:
            capacity = self.max_batch_bytes - self._byte_size
            piece, remainder = _take_utf8_prefix(remaining, capacity)
            if not piece:
                # The pending text leaves fewer bytes than the next Unicode
                # code point needs. Flush it before accepting that code point.
                batch = self.flush(reason="max_bytes", now=current)
                if batch is not None:
                    batches.append(batch)
                    continue
                raise RuntimeError("max_batch_bytes is too small for a Unicode character")

            self._append_piece(piece, current)
            remaining = remainder
            if self._byte_size >= self.max_batch_bytes:
                reason = "max_bytes"
            elif self._byte_size >= self.flush_bytes:
                reason = "size"
            elif current - self._last_flush_at >= self.flush_interval:
                reason = "interval"
            else:
                reason = ""
            if reason:
                batch = self.flush(reason=reason, now=current)
                if batch is not None:
                    batches.append(batch)

        return batches

    def flush_due(self, *, now: float | None = None) -> AgentOutputBatch | None:
        """Flush pending text after the configured wall-clock interval."""

        if not self.has_pending:
            return None
        current = self._resolve_now(now)
        if current - self._last_flush_at < self.flush_interval:
            return None
        return self.flush(reason="interval", now=current)

    def flush(
        self,
        *,
        reason: str = "explicit",
        now: float | None = None,
    ) -> AgentOutputBatch | None:
        """Return and clear the pending batch without changing text order."""

        if not self.has_pending:
            return None
        current = self._resolve_now(now)
        batch = AgentOutputBatch(
            text="".join(self._parts),
            chunk_count=self._chunk_count,
            byte_size=self._byte_size,
            first_at=self._first_at if self._first_at is not None else current,
            last_at=self._last_at if self._last_at is not None else current,
            flushed_at=current,
            reason=str(reason or "explicit"),
        )
        self._parts.clear()
        self._byte_size = 0
        self._chunk_count = 0
        self._first_at = None
        self._last_at = None
        self._last_flush_at = current
        return batch

    def finish(
        self,
        *,
        reason: str = "finish",
        now: float | None = None,
    ) -> AgentOutputBatch | None:
        """Flush the final partial batch and reject future input."""

        if self._closed:
            return None
        batch = self.flush(reason=reason, now=now)
        self._closed = True
        return batch

    def flush_for_exception(
        self,
        error: BaseException | None = None,
        *,
        now: float | None = None,
    ) -> AgentOutputBatch | None:
        """Flush and close while preserving the caller's original exception."""

        reason = "exception"
        if error is not None:
            reason = f"exception:{type(error).__name__}"
        return self.finish(reason=reason, now=now)

    def _append_piece(self, piece: str, now: float) -> None:
        encoded_size = len(piece.encode("utf-8"))
        if encoded_size <= 0:
            return
        if self._first_at is None:
            self._first_at = now
        self._parts.append(piece)
        self._byte_size += encoded_size
        self._chunk_count += 1
        self._last_at = now

    def _resolve_now(self, now: float | None) -> float:
        return float(self._clock() if now is None else now)


def _take_utf8_prefix(text: str, byte_limit: int) -> tuple[str, str]:
    """Split ``text`` without cutting a Unicode code point."""

    if not text or byte_limit <= 0:
        return "", text
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text, ""

    used = 0
    index = 0
    for index, character in enumerate(text):
        width = len(character.encode("utf-8"))
        if used + width > byte_limit:
            return text[:index], text[index:]
        used += width
    return text, ""


__all__ = [
    "AgentOutputBatch",
    "AgentOutputBatcher",
    "DEFAULT_FLUSH_BYTES",
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_MAX_BATCH_BYTES",
]
