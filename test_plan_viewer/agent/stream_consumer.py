"""Persistence-aware consumer for internal Agent SSE generators."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable

from test_plan_viewer.agent.output_buffer import AgentOutputBatcher


@dataclass(frozen=True)
class AgentStreamConsumerDependencies:
    """Application callbacks used while consuming an internal SSE stream."""

    parse_sse_text_blocks: Callable[..., Any]
    persist_agent_stream_batch: Callable[..., Any]
    append_agent_event: Callable[..., Any]
    agent_raise_if_cancelled: Callable[..., Any]
    ambiguous_commit_error: type[BaseException]
    cancelled_error: type[BaseException]
    log_tail_limit: int
    sleep: Callable[[float], Any]
    batcher_factory: Callable[[], AgentOutputBatcher] = AgentOutputBatcher


@dataclass
class _PendingText:
    batcher: AgentOutputBatcher
    stream_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    snapshot: dict[str, Any] | None = None

    def clear_context(self) -> None:
        self.metadata = {}
        self.job_id = None
        self.snapshot = None


def consume_agent_sse_generator(
    run_id,
    step_key,
    generator,
    dependencies: AgentStreamConsumerDependencies,
    log_limit=2000,
    *,
    generator_handles_cancellation=False,
):
    """Consume, aggregate, and persist an internal Agent SSE generator.

    Model deltas and ordinary ``event: log`` messages share the same
    4 KiB/500 ms/16 KiB bounds, but use independent batches so event ordering
    and stream metadata remain unambiguous. Structured events force both
    buffers to durable storage before their own event row is inserted.
    """

    result = {"status": "running", "logs": ""}
    delta = _PendingText(dependencies.batcher_factory(), "model-output")
    tool_log = _PendingText(dependencies.batcher_factory(), "tool-log")

    def append_result_log(text):
        if text:
            result["logs"] = (
                f"{result.get('logs', '')}{text}"[-dependencies.log_tail_limit :]
            )

    def persist_stream_event(job_id, text, metadata, job_log_snapshot=None):
        error = None
        for attempt in range(2):
            try:
                dependencies.persist_agent_stream_batch(
                    run_id,
                    step_key,
                    job_id,
                    text,
                    metadata,
                    job_log_snapshot=job_log_snapshot,
                )
                return
            except Exception as exc:
                error = exc
                if isinstance(exc, dependencies.ambiguous_commit_error):
                    break
                if attempt == 0:
                    dependencies.sleep(0.05)
        if error is not None:
            raise error
        raise RuntimeError("Agent 输出批次持久化失败。")

    def persist_batch(state, batch, *, reason=None, job_log_snapshot=None):
        metadata = {
            **state.metadata,
            **batch.metadata(),
            "flush_reason": reason or batch.reason,
            "job_id": state.job_id,
            "stream_kind": state.metadata.get("stream_kind") or state.stream_kind,
        }
        metadata.pop("message", None)
        metadata.pop("text", None)
        metadata.pop("_job_log_snapshot", None)
        persist_stream_event(
            state.job_id,
            batch.text,
            metadata,
            job_log_snapshot=job_log_snapshot,
        )

    def flush_state(state, reason="structured"):
        batch = state.batcher.flush(reason=reason)
        if batch is None:
            return
        persist_batch(
            state,
            batch,
            reason=reason,
            job_log_snapshot=state.snapshot,
        )
        state.clear_context()

    def flush_due(state):
        batch = state.batcher.flush_due()
        if batch is None:
            return
        persist_batch(state, batch, reason="interval", job_log_snapshot=state.snapshot)
        state.clear_context()

    def add_text(state, text, metadata, job_id, snapshot=None):
        if state.batcher.has_pending and state.job_id != job_id:
            flush_state(state, "job-changed")
        flush_due(state)
        state.job_id = job_id
        state.metadata.update(metadata)
        if isinstance(snapshot, dict):
            state.snapshot = snapshot

        batches = state.batcher.add(text)
        if isinstance(snapshot, dict) and state.batcher.has_pending:
            checkpoint_batch = state.batcher.flush(reason="checkpoint")
            if checkpoint_batch is not None:
                batches.append(checkpoint_batch)

        snapshot_index = len(batches) - 1 if state.snapshot and not state.batcher.has_pending else -1
        for index, batch in enumerate(batches):
            persist_batch(
                state,
                batch,
                job_log_snapshot=state.snapshot if index == snapshot_index else None,
            )
        if not state.batcher.has_pending:
            state.clear_context()

    def flush_all(reason="structured"):
        flush_state(delta, reason)
        flush_state(tool_log, reason)

    def record_flush_failure(business_error, flush_error, phase):
        def error_text(error):
            return (str(error) or type(error).__name__)[:log_limit]

        business_text = error_text(business_error)
        flush_text = error_text(flush_error)
        payload = {
            "stream_flush_failure": True,
            "flush_phase": phase,
            "business_error": business_text,
            "business_error_type": type(business_error).__name__,
            "flush_error": flush_text,
            "flush_error_type": type(flush_error).__name__,
        }
        try:
            dependencies.append_agent_event(
                run_id,
                step_key,
                "error",
                f"Agent 流终止：{business_text}；剩余输出持久化失败：{flush_text}",
                payload,
                job_id=delta.job_id or tool_log.job_id,
            )
        except Exception:
            pass

    def consume_delta(data):
        text = data.get("text") or ""
        if not text:
            return
        if data.get("batched"):
            # The producer already applied the byte/time bounds. Commit before
            # requesting its next yield so generator resumption acknowledges
            # both the event row and optional job-log checkpoint.
            flush_state(delta, "before-prebatched")
            metadata = {
                key: value
                for key, value in data.items()
                if key not in {"text", "_job_log_snapshot"}
            }
            append_result_log(text)
            persist_stream_event(
                data.get("job_id") or None,
                text,
                metadata,
                job_log_snapshot=(
                    data.get("_job_log_snapshot")
                    if isinstance(data.get("_job_log_snapshot"), dict)
                    else None
                ),
            )
            return

        metadata = {
            key: value
            for key, value in data.items()
            if key not in {"text", "_job_log_snapshot"}
        }
        append_result_log(text)
        add_text(
            delta,
            text,
            metadata,
            data.get("job_id") or None,
            data.get("_job_log_snapshot"),
        )

    def consume_log(data):
        message = data.get("message") or ""
        if not message:
            return
        message = str(message)
        append_result_log(f"{message}\n")
        persisted_message = f"{message[:log_limit]}\n"
        metadata = {
            key: value
            for key, value in data.items()
            if key not in {"message", "text", "_job_log_snapshot"}
        }
        metadata["source_event_type"] = "log"
        add_text(
            tool_log,
            persisted_message,
            metadata,
            data.get("job_id") or None,
            data.get("_job_log_snapshot"),
        )

    def append_structured(event, data):
        if event == "status":
            result.update({key: value for key, value in data.items() if value is not None})
            dependencies.append_agent_event(
                run_id,
                step_key,
                "status",
                data.get("error") or f"状态：{data.get('status', '')}",
                data,
                job_id=data.get("job_id"),
                asset_id=(data.get("asset") or {}).get("asset_id")
                if isinstance(data.get("asset"), dict)
                else None,
            )
            return
        if event == "done":
            result.update(data)
            status = data.get("status") or (
                "failed" if data.get("ok") is False else "succeeded"
            )
            result["status"] = status
            dependencies.append_agent_event(
                run_id,
                step_key,
                "status",
                data.get("error") or f"任务{status}",
                data,
                job_id=data.get("job_id"),
                asset_id=(data.get("asset") or {}).get("asset_id")
                if isinstance(data.get("asset"), dict)
                else None,
                test_run_id=data.get("run_id"),
            )
            return
        if event in {"error", "decision"}:
            message = data.get("message") or data.get("error")
            if not message:
                message = json.dumps(data, ensure_ascii=False)[:log_limit]
            dependencies.append_agent_event(
                run_id,
                step_key,
                event,
                str(message)[:log_limit],
                data,
                job_id=data.get("job_id"),
            )
            return
        dependencies.append_agent_event(
            run_id,
            step_key,
            "log",
            json.dumps(data, ensure_ascii=False)[:log_limit],
            data,
        )

    try:
        for chunk in generator:
            if not generator_handles_cancellation:
                dependencies.agent_raise_if_cancelled(run_id)
            for event, data in dependencies.parse_sse_text_blocks(chunk):
                if not isinstance(data, dict):
                    data = {"value": data}
                if event != "delta" and not generator_handles_cancellation:
                    dependencies.agent_raise_if_cancelled(run_id, force=True)

                if event == "delta":
                    flush_state(tool_log, "before-delta")
                    consume_delta(data)
                elif event == "log":
                    flush_state(delta, "before-log")
                    if data.get("artifact_progress") or data.get("retry_flow_progress"):
                        flush_state(tool_log, "structured-log")
                        dependencies.append_agent_event(
                            run_id,
                            step_key,
                            "log",
                            str(data.get("message") or "")[:log_limit],
                            data,
                            job_id=data.get("job_id"),
                        )
                    else:
                        consume_log(data)
                else:
                    flush_all("structured")
                    append_structured(event, data)
            # Agent-only producers yield comment ticks while their upstream is
            # silent. They carry no event row, but make the 500 ms durability
            # threshold observable without a database-writing heartbeat.
            flush_due(delta)
            flush_due(tool_log)
        flush_all("generator-finished")
    except GeneratorExit as business_error:
        try:
            flush_all("generator-exit")
        except Exception as flush_error:
            record_flush_failure(business_error, flush_error, "generator-exit")
        raise
    except BaseException as business_error:
        try:
            flush_all("exception")
        except Exception as flush_error:
            record_flush_failure(business_error, flush_error, "exception")
        raise
    finally:
        close_generator = getattr(generator, "close", None)
        if callable(close_generator):
            try:
                close_generator()
            except Exception:
                pass
    if generator_handles_cancellation and result.get("status") == "cancelled":
        raise dependencies.cancelled_error(result.get("error") or "Agent 任务已取消。")
    return result


__all__ = ["AgentStreamConsumerDependencies", "consume_agent_sse_generator"]
