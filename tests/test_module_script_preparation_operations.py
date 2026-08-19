from contextlib import contextmanager
import inspect
import threading
import time
from unittest.mock import Mock, patch
import unittest

from test_plan_viewer.script_preparation.operations import (
    ModulePreparationTaskRegistry,
    _terminal_succeeded,
    analyze_failure,
    consume_sse,
)
from test_plan_viewer.script_preparation.repository import (
    ModuleScriptPreparationConflict,
    ModuleScriptPreparationRepository,
    ModuleScriptPreparationRepositoryDependencies,
)


class SseRuntime:
    JOB_LOG_TAIL_LIMIT = 1024

    @staticmethod
    def parse_sse_text_blocks(block):
        return block


class SseRegistry:
    def heartbeat(self, _run_id):
        return True

    def raise_if_cancelled(self, _run_id):
        return None


class FencedCursor:
    def __init__(self):
        self.rowcount = 0
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, _values):
        self.sql = sql


class FencedConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


class ReleaseCursor:
    def __init__(self, status="cancelled"):
        self.row = {
            "action_queue_json": "[]",
            "cancel_requested": 1,
            "status": status,
        }
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, _values):
        self.statements.append(sql)
        if sql.lstrip().startswith("UPDATE"):
            self.rowcount = 1
            terminal = self.row["status"] in {"completed", "failed", "cancelled"}
            if not terminal:
                self.row["status"] = "cancelling"
                self.row["finished_at"] = None

    def fetchone(self):
        return dict(self.row)


class ReleaseConnection(FencedConnection):
    def rollback(self):
        return None


class ClaimCursor:
    def __init__(self, status):
        self.row = {
            "status": status,
            "cancel_requested": 0,
            "worker_token": "expired-worker",
            "worker_lease_until": 1,
            "action_queue_json": "[]",
            "recent_actions_json": "[]",
        }
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, _values):
        self.statements.append(sql)
        if sql.lstrip().startswith("UPDATE"):
            self.rowcount = 1

    def fetchone(self):
        return dict(self.row)


class RetryableDatabaseError(RuntimeError):
    def __init__(self, code):
        self.errno = code
        super().__init__(code, "retryable transaction error")


class CreateCursor:
    def __init__(self, *, insert_error=None, existing=None):
        self.insert_error = insert_error
        self.existing = existing
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, _values):
        self.last_sql = sql
        if "INSERT INTO" in sql and self.insert_error:
            raise RetryableDatabaseError(self.insert_error)

    def fetchone(self):
        if "active_scope_key" in self.last_sql:
            return self.existing
        return None


class CreateConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        return None

    def rollback(self):
        return None


class ModulePreparationOperationTests(unittest.TestCase):
    def test_eof_without_terminal_event_is_failed(self):
        result = consume_sse(
            SseRuntime(),
            SseRegistry(),
            "run-1",
            iter([[('delta', {"text": "partial"})]]),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("未收到终态", result["error"])

    def test_done_event_is_terminal(self):
        result = consume_sse(
            SseRuntime(),
            SseRegistry(),
            "run-1",
            iter([[('done', {"status": "succeeded", "ok": True})]]),
        )
        self.assertEqual(result["status"], "succeeded")

    def test_blocked_or_running_event_is_not_a_successful_operation(self):
        for status in ("blocked", "running"):
            result = consume_sse(
                SseRuntime(),
                SseRegistry(),
                "run-1",
                iter([[('done', {"status": status, "ok": True})]]),
            )
            self.assertFalse(_terminal_succeeded(result))

    def test_heartbeat_is_throttled(self):
        repository = Mock()
        repository.heartbeat_worker.return_value = True
        registry = ModulePreparationTaskRegistry(repository, Mock())
        registry.register("run-1")
        registry.heartbeat("run-1")
        registry.heartbeat("run-1")
        self.assertEqual(repository.heartbeat_worker.call_count, 1)

    def test_worker_lease_is_short_and_heartbeat_runs_without_sse(self):
        repository = Mock()
        repository.current_worker_token.return_value = "worker-1"
        beat = threading.Event()
        project_context = threading.local()
        captured_project = "project-42"

        @contextmanager
        def use_captured_project():
            project_context.value = captured_project
            try:
                yield
            finally:
                project_context.value = None

        def heartbeat(*_args, **_kwargs):
            self.assertEqual(project_context.value, captured_project)
            beat.set()

        repository.heartbeat_worker.side_effect = heartbeat
        registry = ModulePreparationTaskRegistry(
            repository,
            Mock(),
            heartbeat_interval=0.01,
            heartbeat_context_factory=use_captured_project,
        )
        registry.register("run-1")
        self.assertTrue(beat.wait(0.2))
        registry.cleanup("run-1")
        calls_after_cleanup = repository.heartbeat_worker.call_count
        time.sleep(0.03)
        self.assertEqual(repository.heartbeat_worker.call_count, calls_after_cleanup)
        self.assertEqual(
            inspect.signature(ModuleScriptPreparationRepository.claim_worker)
            .parameters["lease_ms"]
            .default,
            30_000,
        )

    def test_old_worker_cleanup_does_not_stop_new_worker_heartbeat(self):
        repository = Mock()
        current = {"token": "old"}
        repository.current_worker_token.side_effect = lambda: current["token"]
        beats = []
        repository.heartbeat_worker.side_effect = (
            lambda _run, **kwargs: beats.append(kwargs["worker_token"])
        )
        registry = ModulePreparationTaskRegistry(
            repository, Mock(), heartbeat_interval=0.01
        )
        registry.register("run-1")
        current["token"] = "new"
        registry.register("run-1")
        time.sleep(0.03)
        current["token"] = "old"
        registry.cleanup("run-1")
        beats.clear()
        time.sleep(0.03)
        self.assertIn("new", beats)
        current["token"] = "new"
        registry.cleanup("run-1")

    def test_failure_analysis_redacts_payload_before_prompt_and_job_record(self):
        runtime = Mock()
        runtime.agent_message.return_value = "analyze"
        runtime.send_opencode_prompt_cancellable.return_value = "response"
        runtime.collect_opencode_response_text.return_value = '{"summary":"ok"}'
        runtime.extract_json_object_from_text.return_value = {"summary": "ok"}
        runtime.JOB_LOG_TAIL_LIMIT = 1024
        registry = Mock()

        def redact(value):
            if isinstance(value, dict):
                return {
                    key: "[redacted]" if key in {"password", "token", "authorization"} else redact(item)
                    for key, item in value.items()
                }
            return value

        with patch(
            "test_plan_viewer.script_preparation.operations."
            "agent_failure_handling.redact_agent_failure_value",
            side_effect=redact,
        ):
            analyze_failure(
                runtime,
                registry,
                "run-1",
                "prepare_scripts",
                {
                    "password": "secret-password",
                    "nested": {
                        "token": "secret-token",
                        "authorization": "Bearer secret-auth",
                    },
                },
            )

        job_prompt = runtime.create_test_job.call_args.kwargs["prompt"]
        sent_prompt = runtime.send_opencode_prompt_cancellable.call_args.args[0]
        for secret in ("secret-password", "secret-token", "secret-auth"):
            self.assertNotIn(secret, job_prompt)
            self.assertNotIn(secret, sent_prompt)
        self.assertIn("[redacted]", sent_prompt)

    def test_create_retries_duplicate_or_deadlock_and_recovers_existing_run(self):
        existing = {
            "run_id": "existing-run",
            "module_name": "登录",
            "status": "running",
            "plan_filenames_json": '["正常登录.md"]',
            "plan_snapshots_json": "[]",
            "state_json": "{}",
            "action_queue_json": "[]",
            "recent_actions_json": "[]",
        }
        for code in (1062, 1213):
            with self.subTest(code=code):
                connections = iter(
                    [
                        CreateConnection(CreateCursor(insert_error=code)),
                        CreateConnection(CreateCursor(existing=existing)),
                    ]
                )

                @contextmanager
                def connect(_config):
                    yield next(connections)

                repository = ModuleScriptPreparationRepository(
                    ModuleScriptPreparationRepositoryDependencies(
                        get_platform_database_config=lambda: {"enabled": True},
                        ensure_platform_database_schema=lambda _config: None,
                        platform_mysql_connection=connect,
                        get_script_preparation_runs_table=lambda _config: "runs",
                        get_current_project_id=lambda: 1,
                        current_time_ms=lambda: 10,
                        validate_uid=lambda value, _name: value,
                    )
                )
                run, created = repository.create(
                    run_id="new-run",
                    module_name="登录",
                    plan_filenames=["正常登录.md"],
                    plan_snapshots=[],
                    client_request_id="",
                    created_by="tester",
                )
                self.assertFalse(created)
                self.assertEqual(run["run_id"], "existing-run")

    def test_stale_worker_cannot_save_state(self):
        cursor = FencedCursor()
        connection = FencedConnection(cursor)

        @contextmanager
        def connect(_config):
            yield connection

        repository = ModuleScriptPreparationRepository(
            ModuleScriptPreparationRepositoryDependencies(
                get_platform_database_config=lambda: {"enabled": True},
                ensure_platform_database_schema=lambda _config: None,
                platform_mysql_connection=connect,
                get_script_preparation_runs_table=lambda _config: "runs",
                get_current_project_id=lambda: 1,
                current_time_ms=lambda: 10,
                validate_uid=lambda value, _name: value,
            )
        )
        repository.get = lambda _run_id: {
            "module_name": "登录",
            "action_queue": [],
        }
        repository._worker_context.worker_token = "stale-worker"
        with self.assertRaises(ModuleScriptPreparationConflict):
            repository.save_state(
                "run-1",
                {"initial_run_finished": False, "error": ""},
                step_status="running",
            )
        self.assertIn("worker_token = %s", cursor.sql)
        self.assertIn("'failing'", cursor.sql)

    def test_release_worker_never_downgrades_cancelled_to_cancelling(self):
        cursor = ReleaseCursor(status="cancelled")
        connection = ReleaseConnection(cursor)

        @contextmanager
        def connect(_config):
            yield connection

        repository = ModuleScriptPreparationRepository(
            ModuleScriptPreparationRepositoryDependencies(
                get_platform_database_config=lambda: {"enabled": True},
                ensure_platform_database_schema=lambda _config: None,
                platform_mysql_connection=connect,
                get_script_preparation_runs_table=lambda _config: "runs",
                get_current_project_id=lambda: 1,
                current_time_ms=lambda: 10,
                validate_uid=lambda value, _name: value,
            )
        )
        repository._worker_context.worker_token = "worker-1"
        self.assertTrue(repository.release_worker("run-1", force=True))
        update_sql = next(
            sql for sql in cursor.statements if sql.lstrip().startswith("UPDATE")
        )
        self.assertIn(
            "status = CASE WHEN status IN ('completed', 'failed', 'cancelled')",
            update_sql,
        )
        self.assertIn(
            "finished_at = CASE WHEN status IN ('completed', 'failed', 'cancelled')",
            update_sql,
        )
        self.assertEqual(cursor.row["status"], "cancelled")

    def test_claim_worker_recovers_failing_but_never_replays_terminal(self):
        def repository_for(status):
            cursor = ClaimCursor(status)
            connection = ReleaseConnection(cursor)

            @contextmanager
            def connect(_config):
                yield connection

            repository = ModuleScriptPreparationRepository(
                ModuleScriptPreparationRepositoryDependencies(
                    get_platform_database_config=lambda: {"enabled": True},
                    ensure_platform_database_schema=lambda _config: None,
                    platform_mysql_connection=connect,
                    get_script_preparation_runs_table=lambda _config: "runs",
                    get_current_project_id=lambda: 1,
                    current_time_ms=lambda: 10,
                    validate_uid=lambda value, _name: value,
                )
            )
            return repository, cursor

        recovering, recovering_cursor = repository_for("failing")
        self.assertTrue(recovering.claim_worker("run-1", "new-worker"))
        self.assertTrue(
            any(
                "worker_token = %s" in sql
                for sql in recovering_cursor.statements
                if sql.lstrip().startswith("UPDATE")
            )
        )

        terminal, terminal_cursor = repository_for("failed")
        self.assertFalse(terminal.claim_worker("run-1", "new-worker"))
        self.assertFalse(terminal.current_worker_token())
        self.assertFalse(
            any(
                "action_queue_json = %s" in sql
                for sql in terminal_cursor.statements
                if sql.lstrip().startswith("UPDATE")
            )
        )


if __name__ == "__main__":
    unittest.main()
