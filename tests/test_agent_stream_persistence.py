import json
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app


def valid_plan_markdown():
    return "```json\n" + json.dumps(
        {
            "cases": [
                {
                    "title": "登录成功",
                    "filename": "登录成功.md",
                    "steps": ["提交有效账号"],
                }
            ]
        },
        ensure_ascii=False,
    ) + "\n```\n"


class BlockingEventResponse:
    def __init__(self, on_iter=None):
        self.closed = threading.Event()
        self.on_iter = on_iter

    def __iter__(self):
        if self.on_iter:
            self.on_iter()
        self.closed.wait(5)
        if False:
            yield b""

    def close(self):
        self.closed.set()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class AgentStreamBatchingTests(unittest.TestCase):
    def test_batched_event_serialization_keeps_payload_text_compatibility(self):
        event = app.serialize_agent_event(
            {
                "event_id": 1,
                "run_id": "agent-1",
                "event_type": "log",
                "message": "batched text",
                "payload_json": json.dumps({"batched": True, "chunk_count": 4}),
            }
        )

        self.assertEqual(event["message"], "batched text")
        self.assertEqual(event["payload"]["text"], "batched text")

    def test_execution_stdout_is_batched_and_terminal_snapshot_is_bounded(self):
        persisted = []
        terminal_snapshots = []

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "execution.log"

            def chunks():
                output = app._BufferedExecutionOutput("execution-1", agent_stream=True)
                try:
                    for _ in range(10_000):
                        chunk = output.emit_delta("x")
                        if chunk:
                            yield chunk
                    chunk = output.flush("terminal")
                    if chunk:
                        yield chunk
                    output.finish_job("succeeded")
                finally:
                    output.close()

            def persist_batch(_run, _step, job_id, text, metadata, **kwargs):
                persisted.append(
                    {
                        "job_id": job_id,
                        "text": text,
                        "metadata": metadata,
                        "snapshot": kwargs.get("job_log_snapshot"),
                    }
                )

            def finish_job(job_id, status, *, log_writer=None, **_kwargs):
                terminal_snapshots.append((job_id, status, log_writer.snapshot()))

            with (
                patch.object(app, "get_job_log_path", return_value=log_path),
                patch.object(app, "is_platform_database_enabled", return_value=False),
                patch.object(app, "agent_raise_if_cancelled"),
                patch.object(app, "persist_agent_stream_batch", side_effect=persist_batch),
                patch.object(app, "persist_test_job_log_snapshot") as persist_snapshot,
                patch.object(app, "finish_test_job", side_effect=finish_job) as finish,
            ):
                result = app.consume_agent_sse_generator("agent-1", "execute_scripts", chunks())

            persisted_text = "".join(item["text"] for item in persisted)
            self.assertEqual(persisted_text, "x" * 10_000)
            self.assertEqual(log_path.read_text(encoding="utf-8"), persisted_text)
            self.assertLessEqual(len(persisted), 3)
            self.assertTrue(all(item["job_id"] == "execution-1" for item in persisted))
            self.assertTrue(all(item["metadata"].get("batched") is True for item in persisted))
            self.assertTrue(
                all(item["metadata"].get("stream_kind") == "process-output" for item in persisted)
            )
            self.assertTrue(
                all(len(item["text"].encode("utf-8")) <= 16 * 1024 for item in persisted)
            )
            self.assertEqual(sum(item["snapshot"] is not None for item in persisted), 0)
            self.assertEqual(persist_snapshot.call_count, 0)
            self.assertEqual(finish.call_count, 1)
            self.assertEqual(len(terminal_snapshots), 1)
            self.assertEqual(terminal_snapshots[0][0:2], ("execution-1", "succeeded"))
            self.assertEqual(terminal_snapshots[0][2].size, 10_000)
            self.assertEqual(terminal_snapshots[0][2].tail, "x" * 10_000)
            self.assertEqual(result["logs"], "x" * 10_000)

    def test_ten_thousand_single_character_deltas_create_only_size_bounded_events(self):
        persisted = []

        def chunks():
            for _ in range(10_000):
                yield app.sse_payload("delta", {"text": "x", "job_id": "planner-1"})

        with (
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "persist_agent_stream_batch",
                side_effect=lambda _run, _step, job_id, text, metadata, **kwargs: persisted.append(
                    (job_id, text, metadata, kwargs.get("job_log_snapshot"))
                ),
            ),
            patch.object(app, "append_agent_event"),
        ):
            result = app.consume_agent_sse_generator("agent-1", "generate_plans", chunks())

        self.assertEqual("".join(item[1] for item in persisted), "x" * 10_000)
        self.assertLessEqual(len(persisted), 3)
        self.assertTrue(all(item[0] == "planner-1" for item in persisted))
        self.assertTrue(all(len(item[1].encode("utf-8")) <= 16 * 1024 for item in persisted))
        self.assertEqual(result["logs"], "x" * 10_000)

    def test_structured_status_flushes_pending_delta_first(self):
        timeline = []

        def chunks():
            yield app.sse_payload("delta", {"text": "tail", "job_id": "planner-1"})
            yield app.sse_payload("status", {"status": "running", "job_id": "planner-1"})

        with (
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "persist_agent_stream_batch",
                side_effect=lambda *_args, **_kwargs: timeline.append("delta"),
            ),
            patch.object(
                app,
                "append_agent_event",
                side_effect=lambda *_args, **_kwargs: timeline.append("status"),
            ),
        ):
            app.consume_agent_sse_generator("agent-1", "generate_plans", chunks())

        self.assertEqual(timeline, ["delta", "status"])

    def test_prebatched_delta_commits_before_the_producer_resumes(self):
        timeline = []
        snapshot = {"log_path": "/tmp/job.log", "log_tail": "tail", "log_size": 4}

        def chunks():
            yield app.sse_payload(
                "delta",
                {
                    "text": "tail",
                    "job_id": "planner-1",
                    "batched": True,
                    "chunk_count": 4,
                    "_job_log_snapshot": snapshot,
                },
            )
            timeline.append("producer-resumed")
            yield app.sse_payload("status", {"status": "running", "job_id": "planner-1"})

        with (
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "persist_agent_stream_batch",
                side_effect=lambda *_args, **kwargs: timeline.append(
                    ("persist", kwargs.get("job_log_snapshot"))
                ),
            ),
            patch.object(
                app,
                "append_agent_event",
                side_effect=lambda *_args, **_kwargs: timeline.append("status"),
            ),
        ):
            app.consume_agent_sse_generator("agent-1", "generate_plans", chunks())

        self.assertEqual(timeline, [("persist", snapshot), "producer-resumed", "status"])

    def test_commit_ambiguous_error_is_not_retried(self):
        def chunks():
            yield app.sse_payload(
                "delta",
                {"text": "tail", "job_id": "planner-1", "batched": True},
            )

        with (
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "persist_agent_stream_batch",
                side_effect=app.AgentStreamCommitAmbiguous("unknown commit"),
            ) as persist,
        ):
            with self.assertRaises(app.AgentStreamCommitAmbiguous):
                app.consume_agent_sse_generator("agent-1", "generate_plans", chunks())

        self.assertEqual(persist.call_count, 1)

    def test_batch_persistence_uses_one_connection_and_one_commit(self):
        class Cursor:
            def __init__(self):
                self.statements = []
                self.lastrowid = 77

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, values):
                self.statements.append((statement, values))

        class Connection:
            def __init__(self):
                self.cursor_value = Cursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.commits += 1

        connection = Connection()
        snapshot = {"log_path": "/tmp/job.log", "log_tail": "tail", "log_size": 4}
        with (
            patch.object(app, "require_platform_database", return_value={"enabled": True}),
            patch.object(app, "get_agent_run_events_table", return_value="events"),
            patch.object(app, "get_test_jobs_table", return_value="jobs"),
            patch.object(app, "get_current_project_id", return_value=1),
            patch.object(app, "current_time_ms", return_value=100),
            patch.object(app, "platform_mysql_connection", return_value=nullcontext(connection)),
        ):
            event_id = app.persist_agent_stream_batch(
                "agent-1",
                "generate_plans",
                "planner-1",
                "batched text",
                {"chunk_count": 12, "text": "duplicate"},
                job_log_snapshot=snapshot,
            )

        self.assertEqual(event_id, 77)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(len(connection.cursor_value.statements), 2)
        self.assertIn("COALESCE(log_size, 0) <=", connection.cursor_value.statements[0][0])
        event_values = connection.cursor_value.statements[1][1]
        self.assertEqual(event_values[4], "batched text")
        self.assertNotIn("duplicate", event_values[5])

    def test_batch_checkpoint_rejects_a_missing_job_before_event_insert(self):
        class Cursor:
            rowcount = 0

            def __init__(self):
                self.statements = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, values):
                self.statements.append((statement, values))

            def fetchone(self):
                return None

        class Connection:
            def __init__(self):
                self.cursor_value = Cursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.commits += 1

        connection = Connection()
        with (
            patch.object(app, "require_platform_database", return_value={"enabled": True}),
            patch.object(app, "get_agent_run_events_table", return_value="events"),
            patch.object(app, "get_test_jobs_table", return_value="jobs"),
            patch.object(app, "get_current_project_id", return_value=1),
            patch.object(app, "current_time_ms", return_value=100),
            patch.object(app, "platform_mysql_connection", return_value=nullcontext(connection)),
        ):
            with self.assertRaisesRegex(RuntimeError, "任务不存在"):
                app.persist_agent_stream_batch(
                    "agent-1",
                    "generate_plans",
                    "missing-job",
                    "batched text",
                    {},
                    job_log_snapshot={"log_path": "/tmp/job.log", "log_tail": "tail", "log_size": 4},
                )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(len(connection.cursor_value.statements), 2)

    def test_database_cancel_checks_are_throttled_to_twice_per_second(self):
        app.AGENT_RUN_TASKS.pop("agent-1", None)
        try:
            with (
                patch.object(app.time, "monotonic", side_effect=[10.0, 10.1, 10.49, 10.5]),
                patch.object(app, "get_agent_run_row", return_value={"status": "running"}) as get_run,
            ):
                self.assertFalse(app.agent_is_cancelled("agent-1"))
                self.assertFalse(app.agent_is_cancelled("agent-1"))
                self.assertFalse(app.agent_is_cancelled("agent-1"))
                self.assertFalse(app.agent_is_cancelled("agent-1"))
        finally:
            app.AGENT_RUN_TASKS.pop("agent-1", None)

        self.assertEqual(get_run.call_count, 2)

    def test_terminal_job_update_does_not_issue_a_followup_select(self):
        with (
            patch.object(app, "current_time_ms", return_value=123),
            patch.object(app, "update_test_job") as update_job,
        ):
            app.finish_test_job("planner-1", "succeeded")

        self.assertIs(update_job.call_args.kwargs["fetch"], False)
        self.assertEqual(update_job.call_args.kwargs["status"], "succeeded")


class ExecutionStreamCancellationTests(unittest.TestCase):
    class BlockingStdout:
        def __init__(self):
            self.closed = threading.Event()

        def __iter__(self):
            self.closed.wait(5)
            if False:
                yield b""

        def close(self):
            self.closed.set()

    class Process:
        def __init__(self):
            self.stdout = ExecutionStreamCancellationTests.BlockingStdout()
            self.calls = []
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.calls.append("terminate")

        def wait(self, timeout=None):
            self.calls.append(("wait", timeout))
            if not self.killed:
                raise app.subprocess.TimeoutExpired("npx", timeout)
            return -9

        def kill(self):
            self.calls.append("kill")
            self.killed = True

    def runtime_patches(self, stack, process, project_root):
        stack.enter_context(patch.object(app, "sync_script_asset", return_value={"asset_id": 7}))
        stack.enter_context(patch.object(app, "create_test_run"))
        stack.enter_context(patch.object(app, "create_test_job"))
        stack.enter_context(patch.object(app, "build_execution_env_metadata", return_value={}))
        stack.enter_context(
            patch.object(app, "create_run_result_for_script", return_value={"result_id": 11})
        )
        stack.enter_context(patch.object(app, "append_test_job_log"))
        stack.enter_context(
            patch.object(app, "get_script_test_relative_path", return_value="tests/模块/脚本.spec.ts")
        )
        stack.enter_context(
            patch.object(app, "get_script_module_dir", return_value=project_root / "tests" / "模块")
        )
        stack.enter_context(patch.object(app, "get_playwright_execution_env", return_value={}))
        update_result = stack.enter_context(patch.object(app, "update_run_result"))
        update_run = stack.enter_context(patch.object(app, "update_test_run"))
        finish_job = stack.enter_context(patch.object(app, "finish_test_job"))
        stack.enter_context(patch.object(app.subprocess, "Popen", return_value=process))
        return update_result, update_run, finish_job

    def assert_cancelled(self, process, update_result, update_run, finish_job):
        self.assertTrue(process.stdout.closed.is_set())
        self.assertEqual(
            process.calls,
            ["terminate", ("wait", 1.0), "kill", ("wait", 1.0)],
        )
        self.assertEqual(update_result.call_args.kwargs["status"], "interrupted")
        self.assertIn("流式连接已关闭", update_result.call_args.kwargs["error_message"])
        self.assertEqual(update_run.call_args.kwargs["status"], "cancelled")
        self.assertTrue(update_run.call_args.kwargs["finished"])
        self.assertEqual(finish_job.call_args.args[1], "cancelled")

    def test_single_script_close_stops_process_and_terminalizes_records(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            project_root = Path(directory)
            process = self.Process()
            update_result, update_run, finish_job = self.runtime_patches(
                stack, process, project_root
            )
            context = {
                "script_file": project_root / "tests" / "模块" / "脚本.spec.ts",
                "project_root": project_root,
                "video_config": project_root / "video.config.ts",
                "results_dir": project_root / "results",
                "report_dir": project_root / "report",
                "run_id": "single-cancel",
                "command": ["npx"],
                "command_text": "npx playwright test",
                "setup_resolution": None,
            }
            generator = app.stream_script_execution(
                "模块", "脚本.spec.ts", context, agent_stream=True
            )
            next(generator)
            self.assertTrue(next(generator).startswith(":"))
            generator.close()

        self.assert_cancelled(process, update_result, update_run, finish_job)

    def test_module_close_stops_process_and_interrupts_pending_results(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            project_root = Path(directory)
            process = self.Process()
            update_result, update_run, finish_job = self.runtime_patches(
                stack, process, project_root
            )
            context = {
                "execution_mode": app.EXECUTION_MODE_BATCH,
                "project_root": project_root,
                "video_config": project_root / "video.config.ts",
                "report_dir": project_root / "report",
                "run_id": "module-cancel",
                "command": ["npx"],
                "command_text": "npx playwright test",
                "merge_config": None,
                "setup_resolution": None,
            }
            generator = app.stream_module_script_execution(
                "模块", ["脚本.spec.ts"], context, agent_stream=True
            )
            next(generator)
            next(generator)
            self.assertTrue(next(generator).startswith(":"))
            generator.close()

        self.assert_cancelled(process, update_result, update_run, finish_job)

    def test_suite_close_stops_process_and_interrupts_pending_results(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            project_root = Path(directory)
            process = self.Process()
            update_result, update_run, finish_job = self.runtime_patches(
                stack, process, project_root
            )
            items = [
                {
                    "module_name": "模块",
                    "filename": "脚本.spec.ts",
                    "key": "模块/脚本.spec.ts",
                    "relative_path": "tests/模块/脚本.spec.ts",
                }
            ]
            context = {
                "execution_mode": app.EXECUTION_MODE_BATCH,
                "items": items,
                "project_root": project_root,
                "video_config": project_root / "video.config.ts",
                "report_dir": project_root / "report",
                "run_id": "suite-cancel",
                "command": ["npx"],
                "command_text": "npx playwright test",
                "relative_path_keys": {items[0]["relative_path"]: items[0]["key"]},
                "merge_config": None,
                "setup_resolution": None,
            }
            generator = app.stream_test_suite_execution(
                "suite", "回归测试集", items, context, agent_stream=True
            )
            next(generator)
            next(generator)
            self.assertTrue(next(generator).startswith(":"))
            generator.close()

        self.assert_cancelled(process, update_result, update_run, finish_job)


class OpenCodeStreamLifecycleTests(unittest.TestCase):
    def stream_patches(self, response, timeout):
        return (
            patch.object(app, "register_opencode_task"),
            patch.object(app, "cleanup_opencode_task"),
            patch.object(app, "prepare_bound_setup"),
            patch.object(app, "is_opencode_task_cancelled", return_value=False),
            patch.object(app, "build_opencode_session_payload", return_value={}),
            patch.object(app, "opencode_project_query", return_value={}),
            patch.object(app, "opencode_request", return_value={"id": "session-1"}),
            patch.object(app, "set_opencode_task_session", return_value=False),
            patch.object(app, "get_opencode_task_timeout_seconds", return_value=timeout),
            patch.object(app, "opencode_event_stream", return_value=response),
            patch.object(app, "send_opencode_prompt_async", return_value={}),
            patch.object(app, "abort_opencode_session"),
        )

    def test_silent_stream_honors_wall_clock_deadline(self):
        response = BlockingEventResponse()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan.md"
            started_at = time.monotonic()
            with ExitStack() as stack:
                for stream_patch in self.stream_patches(response, 0.25):
                    stack.enter_context(stream_patch)
                events = []
                for chunk in app.stream_plan_generation(
                    "模块",
                    "prompt",
                    target,
                    setup_targets=[],
                    completion_required=False,
                ):
                    events.extend(app.parse_sse_text_blocks(chunk))
            elapsed = time.monotonic() - started_at

        done = [payload for event, payload in events if event == "done"][-1]
        self.assertFalse(done["ok"])
        self.assertIn("实时输出超时", done["error"])
        self.assertLess(elapsed, 1.5)
        self.assertTrue(response.closed.is_set())

    def test_valid_stable_plan_finishes_during_upstream_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "计划索引.md"
            response = BlockingEventResponse(
                on_iter=lambda: target.write_text(valid_plan_markdown(), encoding="utf-8")
            )
            with ExitStack() as stack:
                for stream_patch in self.stream_patches(response, 3):
                    stack.enter_context(stream_patch)
                abort = stack.enter_context(patch.object(app, "abort_opencode_session"))
                events = []
                for chunk in app.stream_plan_generation(
                    "模块",
                    "prompt",
                    target,
                    setup_targets=[],
                    default_agent="playwright-test-planner",
                    validate_plan_completion=True,
                ):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [payload for event, payload in events if event == "done"][-1]
        phases = [
            payload
            for event, payload in events
            if event == "status" and payload.get("plan_phase") == "splitting"
        ]
        self.assertTrue(done["ok"])
        self.assertEqual(phases[0]["case_count"], 1)
        abort.assert_called_with("session-1")
        self.assertTrue(response.closed.is_set())

    def test_deferred_job_success_stays_non_terminal_until_plan_split(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "计划索引.md"
            response = BlockingEventResponse(
                on_iter=lambda: target.write_text(
                    valid_plan_markdown(),
                    encoding="utf-8",
                )
            )
            with ExitStack() as stack:
                for stream_patch in self.stream_patches(response, 3):
                    stack.enter_context(stream_patch)
                events = []
                for chunk in app.stream_plan_generation(
                    "模块",
                    "prompt",
                    target,
                    setup_targets=[],
                    default_agent="playwright-test-planner",
                    finish_job_on_success=False,
                    validate_plan_completion=True,
                    success_payload_factory=lambda: {
                        "plan_filename": target.name,
                    },
                ):
                    events.extend(app.parse_sse_text_blocks(chunk))

        self.assertFalse(any(event == "done" for event, _payload in events))
        self.assertFalse(
            any(
                event == "status" and payload.get("status") == "succeeded"
                for event, payload in events
            )
        )
        ready = [
            payload
            for event, payload in events
            if event == "status" and payload.get("source_ready")
        ]
        self.assertEqual(ready[0]["status"], "running")
        self.assertEqual(ready[0]["plan_phase"], "splitting")

    def test_silent_agent_stream_observes_cross_process_database_cancel(self):
        response = BlockingEventResponse()
        app.AGENT_RUN_TASKS.pop("agent-1", None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "plan.md"
                with ExitStack() as stack:
                    for stream_patch in self.stream_patches(response, 3):
                        stack.enter_context(stream_patch)
                    abort = stack.enter_context(
                        patch.object(app, "abort_opencode_session")
                    )
                    stack.enter_context(
                        patch.object(
                            app,
                            "get_agent_run_row",
                            side_effect=[{"status": "running"}, {"status": "cancelling"}],
                        )
                    )
                    stack.enter_context(patch.object(app, "append_agent_event"))
                    stack.enter_context(
                        patch.object(app, "get_job_log_path", return_value=Path(directory) / "planner-1.log")
                    )
                    stack.enter_context(patch.object(app, "update_test_job"))
                    finish = stack.enter_context(patch.object(app, "finish_test_job"))
                    stack.enter_context(
                        patch.object(
                            app,
                            "get_test_job",
                            return_value={"job_id": "planner-1", "status": "cancelled"},
                        )
                    )
                    started_at = time.monotonic()
                    with self.assertRaises(app.OpencodeTaskCancelled):
                        app.consume_agent_sse_generator(
                            "agent-1",
                            "generate_plans",
                            app.stream_plan_generation(
                                "模块",
                                "prompt",
                                target,
                                setup_targets=[],
                                completion_required=False,
                                cancel_job_id="planner-1",
                                job_id="planner-1",
                                agent_stream=True,
                                agent_cancel_check=lambda: app.agent_raise_if_cancelled("agent-1"),
                            ),
                            generator_handles_cancellation=True,
                        )
                    elapsed = time.monotonic() - started_at
        finally:
            app.AGENT_RUN_TASKS.pop("agent-1", None)

        self.assertLess(elapsed, 1.5)
        self.assertEqual(finish.call_args.args[:2], ("planner-1", "cancelled"))
        abort.assert_called_with("session-1")
        self.assertTrue(response.closed.is_set())

    def test_generator_close_terminalizes_the_job_and_flushes_its_log(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan.md"
            log_path = Path(directory) / "planner-1.log"
            with (
                patch.object(app, "register_opencode_task"),
                patch.object(app, "cleanup_opencode_task"),
                patch.object(app, "update_test_job"),
                patch.object(app, "get_test_job", return_value={"job_id": "planner-1"}),
                patch.object(app, "get_job_log_path", return_value=log_path),
                patch.object(app, "finish_test_job") as finish,
            ):
                generator = app.stream_plan_generation(
                    "模块",
                    "prompt",
                    target,
                    setup_targets=[],
                    cancel_job_id="planner-1",
                    job_id="planner-1",
                )
                first_event = next(generator)
                generator.close()
                log_text = log_path.read_text(encoding="utf-8")

        self.assertEqual(list(app.parse_sse_text_blocks(first_event))[0][0], "status")
        self.assertEqual(finish.call_args.args[:2], ("planner-1", "cancelled"))
        self.assertIn("流式连接已关闭", log_text)


class AgentEventStreamCatchupTests(unittest.TestCase):
    def test_caught_up_page_reads_events_run_and_retries_on_one_connection(self):
        class Cursor:
            def __init__(self):
                self.statements = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, values):
                self.statements.append((statement, values))

            def fetchall(self):
                if len(self.statements) == 1:
                    return [{"event_id": 1}]
                return [{"retry_flow_id": "retry-1"}]

            def fetchone(self):
                return {"run_id": "agent-1", "status": "running"}

        class Connection:
            def __init__(self):
                self.cursor_value = Cursor()

            def cursor(self):
                return self.cursor_value

        connection = Connection()
        calls = []

        def connect(_config):
            calls.append(connection)
            return nullcontext(connection)

        with (
            patch.object(app, "require_platform_database", return_value={"enabled": True}),
            patch.object(app, "get_agent_run_events_table", return_value="events"),
            patch.object(app, "get_agent_runs_table", return_value="runs"),
            patch.object(app, "get_agent_item_retry_flows_table", return_value="retries"),
            patch.object(app, "get_current_project_id", return_value=1),
            patch.object(app, "platform_mysql_connection", side_effect=connect),
        ):
            rows, run, retries = app.read_agent_event_stream_page("agent-1", 0, 200)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(connection.cursor_value.statements), 3)
        self.assertEqual(rows, [{"event_id": 1}])
        self.assertEqual(run["status"], "running")
        self.assertEqual(retries, [{"retry_flow_id": "retry-1"}])

    def test_terminal_stream_drains_every_full_page_before_done(self):
        rows = [
            {
                "event_id": index,
                "run_id": "agent-1",
                "step_key": "generate_plans",
                "event_type": "log",
                "message": str(index),
                "payload_json": "{}",
                "created_at": index,
            }
            for index in range(1, 402)
        ]

        def list_page(_run_id, after_id, limit):
            page = [row for row in rows if row["event_id"] > after_id][:limit]
            if len(page) == limit:
                return page, None, None
            return page, {"run_id": "agent-1", "status": "succeeded"}, []

        with app.app.test_request_context("/api/agent/runs/agent-1/events-stream"):
            with (
                patch.object(app, "get_agent_run_row", return_value={"run_id": "agent-1", "status": "succeeded"}),
                patch.object(app, "read_agent_event_stream_page", side_effect=list_page),
            ):
                response = app.stream_agent_run_events_api("agent-1")
                payload = response.get_data(as_text=True)

        events = list(app.parse_sse_text_blocks(payload))
        agent_events = [data for event, data in events if event == "agent-event"]
        self.assertEqual([item["event_id"] for item in agent_events], list(range(1, 402)))
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["last_event_id"], 401)

    def test_terminal_stream_rechecks_for_a_late_final_event_before_done(self):
        final_row = {
            "event_id": 1,
            "run_id": "agent-1",
            "step_key": "run_suite",
            "event_type": "status",
            "message": "Agent 全流程执行完成。",
            "payload_json": "{}",
            "created_at": 1,
        }
        pages = [
            ([], {"run_id": "agent-1", "status": "succeeded"}, []),
            ([final_row], {"run_id": "agent-1", "status": "succeeded"}, []),
            ([], {"run_id": "agent-1", "status": "succeeded"}, []),
        ]

        with app.app.test_request_context(
            "/api/agent/runs/agent-1/events-stream"
        ):
            with (
                patch.object(
                    app,
                    "get_agent_run_row",
                    return_value={"run_id": "agent-1", "status": "succeeded"},
                ),
                patch.object(
                    app,
                    "read_agent_event_stream_page",
                    side_effect=pages,
                ) as read_page,
                patch.object(app.time, "sleep"),
            ):
                response = app.stream_agent_run_events_api("agent-1")
                payload = response.get_data(as_text=True)

        events = list(app.parse_sse_text_blocks(payload))
        self.assertEqual(read_page.call_count, 3)
        self.assertEqual(
            [data["event_id"] for event, data in events if event == "agent-event"],
            [1],
        )
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["last_event_id"], 1)


class AgentItemRetryTerminalPublicationTests(unittest.TestCase):
    def test_terminal_event_is_persisted_before_flow_leaves_active_status(self):
        flow = {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "status": "finalizing",
            "current_phase": "verifying",
            "result_json": "{}",
            "cancel_requested": 0,
        }
        terminal_flow = {**flow, "status": "failed", "error": "verification failed"}
        timeline = []

        def append_event(_run_id, projected_flow, message, **kwargs):
            timeline.append(("event", dict(projected_flow), message, kwargs))
            return 42

        def update_flow(_run_id, _retry_flow_id, **updates):
            timeline.append(("flow", dict(updates)))
            return terminal_flow

        with (
            patch.object(app, "append_agent_item_retry_event", side_effect=append_event),
            patch.object(app, "update_agent_item_retry_flow", side_effect=update_flow),
        ):
            result = app.complete_agent_item_retry_flow(
                "agent-1",
                "retry-1",
                "failed",
                current_phase="verifying",
                progress_message="脚本修复后复验仍然失败。",
                result={"verification": {"status": "failed"}},
                error="verification failed",
                event_message="脚本修复后复验仍然失败。",
                event_type="error",
                flow=flow,
            )

        self.assertEqual([entry[0] for entry in timeline], ["event", "flow"])
        self.assertEqual(timeline[0][1]["status"], "failed")
        self.assertEqual(timeline[0][1]["current_phase"], "verifying")
        self.assertEqual(timeline[0][2], "脚本修复后复验仍然失败。")
        self.assertEqual(timeline[0][3]["event_type"], "error")
        self.assertEqual(timeline[1][1]["expected_statuses"], {"finalizing"})
        self.assertEqual(timeline[1][1]["status"], "failed")
        self.assertEqual(result, terminal_flow)
        self.assertEqual(flow["status"], "finalizing")

    def test_terminal_event_failure_does_not_hide_the_active_flow(self):
        flow = {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "status": "finalizing",
            "current_phase": "completed",
            "result_json": "{}",
        }

        with (
            patch.object(
                app,
                "append_agent_item_retry_event",
                side_effect=RuntimeError("event insert failed"),
            ),
            patch.object(app, "update_agent_item_retry_flow") as update_flow,
        ):
            with self.assertRaisesRegex(RuntimeError, "event insert failed"):
                app.complete_agent_item_retry_flow(
                    "agent-1",
                    "retry-1",
                    "succeeded",
                    current_phase="completed",
                    progress_message="脚本已重新生成并验证通过。",
                    result={"execution": {"status": "succeeded"}},
                    event_message="脚本已重新生成并验证通过。",
                    flow=flow,
                )

        update_flow.assert_not_called()
        self.assertEqual(flow["status"], "finalizing")


class MultiplePlanFinalizationTests(unittest.TestCase):
    def test_mixed_conflict_fails_before_registration_or_source_deletion(self):
        source = Path("/tmp/计划索引.md")
        split_result = {
            "created": [{"filename": "可创建.md"}],
            "reused": [],
            "skipped": [
                {
                    "filename": "内容冲突.md",
                    "reason": "文件已存在。",
                    "reason_code": "content_conflict",
                }
            ],
            "conflicts": [
                {
                    "filename": "内容冲突.md",
                    "reason": "文件已存在。",
                    "reason_code": "content_conflict",
                }
            ],
            "reason_code": "case_content_conflict",
        }

        with (
            patch.object(
                app,
                "split_or_repair_multiple_plan",
                return_value=split_result,
            ),
            patch.object(app, "append_test_job_log") as append_log,
            patch.object(app, "sync_plan_asset") as sync_asset,
            patch.object(app, "get_plan_file") as get_plan_file,
            patch.object(app, "link_requirement_module_plan") as link_plan,
            patch.object(app, "delete_intermediate_plan_file") as delete_source,
        ):
            with self.assertRaisesRegex(RuntimeError, "内容冲突.*内容冲突.md"):
                app.finalize_multiple_plan_files(
                    "登录",
                    source,
                    "planner-1",
                    "source",
                    "split",
                    requirement={"id": 7},
                    requirement_module_uid="module-1",
                )

        append_log.assert_called_once()
        sync_asset.assert_not_called()
        get_plan_file.assert_not_called()
        link_plan.assert_not_called()
        delete_source.assert_not_called()

    def test_reused_plans_are_registered_before_the_source_is_deleted(self):
        source = Path("/tmp/计划索引.md")
        reused = {"filename": "登录成功.md"}
        timeline = []

        def sync_asset(_module, path, **_kwargs):
            if path == source:
                return {"asset_id": 1, "filename": source.name}
            return {"asset_id": 2, "filename": Path(path).name}

        with (
            patch.object(app, "sync_plan_asset", side_effect=sync_asset),
            patch.object(
                app,
                "split_or_repair_multiple_plan",
                return_value={"created": [], "reused": [reused], "skipped": []},
            ),
            patch.object(app, "get_plan_file", return_value=Path("/tmp/登录成功.md")),
            patch.object(app, "serialize_asset", side_effect=lambda value: value),
            patch.object(
                app,
                "link_requirement_module_plan",
                side_effect=lambda *_args, **_kwargs: timeline.append("link") or {"id": 3},
            ),
            patch.object(
                app,
                "delete_intermediate_plan_file",
                side_effect=lambda *_args, **_kwargs: timeline.append("delete") or {"asset": {"asset_id": 1}},
            ),
            patch.object(app, "append_test_job_log"),
            patch.object(app, "serialize_requirement_module", side_effect=lambda value: value),
            patch.object(app, "list_asset_revisions", return_value=[]),
        ):
            result = app.finalize_multiple_plan_files(
                "登录",
                source,
                "planner-1",
                "source",
                "split",
                requirement={"id": 7},
                requirement_module_uid="module-1",
            )

        self.assertEqual(timeline, ["link", "delete"])
        self.assertEqual([item["plan_filename"] for item in result["plans"]], ["登录成功.md"])
        self.assertEqual(result["asset"]["asset_id"], 2)


class AgentPlanRecoveryTests(unittest.TestCase):
    def test_resume_reuses_valid_source_plan_without_starting_opencode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "计划索引.md"
            target.write_text(valid_plan_markdown(), encoding="utf-8")
            module = {
                "module_uid": "module-1",
                "module_name": "登录",
                "plan_name": "计划索引",
                "planner_prompt": "生成登录计划",
            }
            normalized = {
                "coverage_profile": "core",
                "coverage_prompt": "",
                "prompt_customized": False,
            }
            split_result = {
                "plans": [{"module_name": "登录", "plan_filename": "登录成功.md"}],
                "split": {"created": [{"filename": "登录成功.md"}]},
                "deleted_source": {},
                "asset": {"asset_id": 9},
            }
            with (
                patch.object(app, "validate_module_name", side_effect=lambda value: value),
                patch.object(app, "get_agent_run_row", return_value={"plan_generation_json": "{}"}),
                patch.object(app, "serialize_agent_run", return_value={"plan_generation": {}}),
                patch.object(app, "normalize_plan_generation_request", return_value=normalized),
                patch.object(app, "compose_editable_plan_prompt", return_value="prompt"),
                patch.object(app, "get_current_project_language", return_value="en"),
                patch.object(app, "get_plan_filename_from_name", return_value=target.name),
                patch.object(app, "get_plan_target_path", return_value=target),
                patch.object(app, "find_legacy_agent_plan_job", return_value=None),
                patch.object(app, "build_multiple_plan_generation_prompt", return_value="full prompt"),
                patch.object(app, "build_plan_prompt_context", return_value={}),
                patch.object(app, "create_test_job"),
                patch.object(app, "agent_set_current_job") as set_current_job,
                patch.object(app, "append_agent_artifact_progress"),
                patch.object(app, "append_test_job_log"),
                patch.object(app, "append_agent_event"),
                patch.object(app, "finalize_multiple_plan_files", return_value=split_result) as finalize,
                patch.object(app, "finish_test_job") as finish,
                patch.object(app, "stream_plan_generation") as stream,
            ):
                result = app.agent_generate_plan_for_module(
                    "agent-1",
                    "generate_plans",
                    {"id": 1},
                    module,
                    resume_failure={"job_id": "planner-old", "partial_artifacts": [str(target)]},
                )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["plans"], split_result["plans"])
        finalize.assert_called_once()
        stream.assert_not_called()
        self.assertEqual(finish.call_args.args[1], "succeeded")
        self.assertEqual(set_current_job.call_args_list[-1].args, ("agent-1", ""))


if __name__ == "__main__":
    unittest.main()
