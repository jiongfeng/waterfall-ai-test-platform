import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from test_plan_viewer.infrastructure.job_logs import BufferedJobLogWriter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ToolOnlyEventResponse:
    def __init__(self, clock, count=40):
        self.clock = clock
        self.count = count
        self.closed = False

    def __iter__(self):
        self.clock.advance(31)
        for index in range(self.count):
            event = {
                "type": "message.part.updated",
                "properties": {
                    "sessionID": "session-1",
                    "part": {
                        "id": f"tool-{index}",
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "title": f"read-{index}",
                            "input": {"path": f"file-{index}.md"},
                            "output": "x " * 15_000,
                        },
                    },
                },
            }
            yield f"data: {json.dumps(event)}\n".encode()
            yield b"\n"
        idle = {
            "type": "session.idle",
            "properties": {"sessionID": "session-1"},
        }
        yield f"data: {json.dumps(idle)}\n".encode()
        yield b"\n"

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class PlanLogCheckpointTests(unittest.TestCase):
    def test_tool_only_stream_checkpoints_by_time_and_bytes(self):
        clock = FakeClock()
        response = ToolOnlyEventResponse(clock)
        real_writer = BufferedJobLogWriter

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            log_path = Path(directory) / "planner-1.log"
            stack.enter_context(patch.object(app, "register_opencode_task"))
            stack.enter_context(patch.object(app, "cleanup_opencode_task"))
            stack.enter_context(patch.object(app, "prepare_bound_setup"))
            stack.enter_context(
                patch.object(app, "is_opencode_task_cancelled", return_value=False)
            )
            stack.enter_context(
                patch.object(app, "build_opencode_session_payload", return_value={})
            )
            stack.enter_context(patch.object(app, "opencode_project_query", return_value={}))
            stack.enter_context(
                patch.object(
                    app,
                    "opencode_request",
                    side_effect=lambda path, *_args, **_kwargs: (
                        {"id": "session-1"} if path == "/session" else []
                    ),
                )
            )
            stack.enter_context(
                patch.object(app, "set_opencode_task_session", return_value=False)
            )
            stack.enter_context(
                patch.object(app, "get_opencode_task_timeout_seconds", return_value=3)
            )
            stack.enter_context(patch.object(app, "opencode_event_stream", return_value=response))
            stack.enter_context(patch.object(app, "send_opencode_prompt_async", return_value={}))
            stack.enter_context(patch.object(app, "get_job_log_path", return_value=log_path))
            stack.enter_context(patch.object(app, "update_test_job"))
            stack.enter_context(
                patch.object(
                    app,
                    "get_test_job",
                    return_value={"job_id": "planner-1", "status": "running"},
                )
            )
            stack.enter_context(patch.object(app, "finish_test_job"))
            stack.enter_context(
                patch.object(app, "is_platform_database_enabled", return_value=True)
            )
            direct_snapshot = stack.enter_context(
                patch.object(app, "persist_test_job_log_snapshot")
            )
            stack.enter_context(
                patch.object(
                    app,
                    "BufferedJobLogWriter",
                    side_effect=lambda path, tail_bytes: real_writer(
                        path,
                        tail_bytes=tail_bytes,
                        clock=clock,
                    ),
                )
            )

            events = []
            for chunk in app.stream_plan_generation(
                "Module",
                "prompt",
                Path(directory) / "plan.md",
                setup_targets=[],
                completion_required=False,
                job_id="planner-1",
                agent_stream=True,
            ):
                events.extend(app.parse_sse_text_blocks(chunk))
            final_log_size = log_path.stat().st_size

        checkpoint_events = [
            payload
            for event, payload in events
            if event == "log" and "_job_log_snapshot" in payload
        ]
        checkpoints = [payload["_job_log_snapshot"] for payload in checkpoint_events]
        self.assertEqual(
            len(checkpoints),
            2,
            (final_log_size, [item["log_size"] for item in checkpoints]),
        )
        self.assertGreater(checkpoints[0]["log_size"], 0)
        self.assertGreaterEqual(
            checkpoints[1]["log_size"] - checkpoints[0]["log_size"],
            1024 * 1024,
        )
        self.assertFalse(any(event == "delta" for event, _payload in events))
        self.assertTrue(
            all(payload["job_id"] == "planner-1" for payload in checkpoint_events)
        )
        direct_snapshot.assert_not_called()
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
