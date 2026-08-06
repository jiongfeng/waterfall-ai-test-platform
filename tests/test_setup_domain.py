import io
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from test_plan_viewer.setup import model, runner, service, validation


class SetupValidationTests(unittest.TestCase):
    def test_timeout_is_bounded_and_reports_invalid_values(self):
        self.assertEqual(validation.normalize_setup_timeout(None), 300)
        self.assertEqual(validation.normalize_setup_timeout("45"), 45)

        for value in (0, -1, 7201, "invalid"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "timeout_seconds",
            ):
                validation.normalize_setup_timeout(value)

    def test_working_directory_is_confined_to_the_project(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            project_root = root / "project"
            child = project_root / "fixtures"
            outside = root / "outside"
            child.mkdir(parents=True)
            outside.mkdir()

            self.assertEqual(
                validation.resolve_setup_working_directory(
                    "fixtures",
                    project_root,
                ),
                child.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "当前项目目录内"):
                validation.resolve_setup_working_directory(
                    "../outside",
                    project_root,
                )

            (project_root / "linked").symlink_to(
                outside,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(ValueError, "当前项目目录内"):
                validation.resolve_setup_working_directory(
                    "linked",
                    project_root,
                )

    def test_payload_validation_uses_explicit_dependencies(self):
        seen_working_directories = []
        dependencies = validation.SetupValidationDependencies(
            resolve_working_directory=seen_working_directories.append,
            validate_uid=lambda value, field_name, generate=False: (
                str(value or "generated")
            ),
            normalize_name=validation.normalize_setup_name,
            normalize_string_map=validation.normalize_setup_string_map,
            normalize_timeout=validation.normalize_setup_timeout,
        )

        payload = validation.normalize_setup_script_payload(
            {
                "name": "恢复数据库",
                "content": "echo restore",
                "working_directory": "fixtures",
                "environment_overrides": {"TOKEN": "secret"},
                "timeout_seconds": 12,
            },
            None,
            dependencies,
        )

        self.assertEqual(seen_working_directories, ["fixtures"])
        self.assertEqual(payload["uid"], "generated")
        self.assertEqual(payload["script_content"], "echo restore")
        self.assertEqual(payload["timeout_seconds"], 12)


class SetupModelTests(unittest.TestCase):
    @staticmethod
    def redact_sensitive(value, limit=None):
        return str(value or "")

    def redact_text(self, value, script=None, limit=4000):
        return model.redact_setup_text(
            value,
            script,
            limit,
            redact_sensitive_text=self.redact_sensitive,
        )

    def test_text_and_snapshot_redaction_hides_all_setup_secrets(self):
        script = {
            "environment_overrides": {
                "API_TOKEN": "long-secret-value",
                "EMPTY": "",
            }
        }
        redacted = self.redact_text(
            (
                "long-secret-value token=short-token "
                "Authorization: Bearer bearer-token"
            ),
            script,
        )

        self.assertNotIn("long-secret-value", redacted)
        self.assertNotIn("short-token", redacted)
        self.assertNotIn("bearer-token", redacted)
        self.assertGreaterEqual(redacted.count("******"), 3)

        snapshot = model.redact_setup_snapshot(
            {
                "password": "database-password",
                "script": script,
                "nested": ["token=inline-token"],
            },
            redact_text=self.redact_text,
        )
        self.assertEqual(snapshot["password"], "******")
        self.assertEqual(
            snapshot["script"]["environment_overrides"]["API_TOKEN"],
            "******",
        )
        self.assertNotIn("inline-token", snapshot["nested"][0])

    def test_binding_precedence_prefers_scope_before_priority(self):
        targets = [
            {
                "scope_type": "script",
                "scope_key": "登录/正向.spec.ts",
            },
            {"scope_type": "test_suite", "scope_key": "suite"},
            {"scope_type": "project", "scope_key": "default"},
        ]
        selected = model.select_setup_binding(
            [
                {
                    "uid": "project",
                    "scope_type": "project",
                    "scope_key": "default",
                    "priority": 999,
                },
                {
                    "uid": "script-low",
                    "scope_type": "script",
                    "scope_key": "登录/正向.spec.ts",
                    "priority": 1,
                },
                {
                    "uid": "script-high",
                    "scope_type": "script",
                    "scope_key": "登录/正向.spec.ts",
                    "priority": 2,
                },
            ],
            targets,
        )

        self.assertEqual(selected["uid"], "script-high")


class SetupRunnerTests(unittest.TestCase):
    def test_timeout_requests_process_cancellation_and_keeps_output(self):
        script = {
            "script_content": "echo restore",
            "working_directory": "",
            "environment_overrides": {},
        }
        killed = []

        class TimedOutProcess:
            args = ["/bin/bash", "-c", "echo restore"]
            stdout = io.BytesIO(b"restore started\n")

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(self.args, timeout)

        class ImmediateThread:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

        process = TimedOutProcess()
        dependencies = runner.SetupRunnerDependencies(
            resolve_working_directory=lambda _value: Path("/tmp"),
            normalize_process_output=lambda value: value.decode("utf-8"),
            redact_setup_text=lambda value, script=None, limit=4000: value,
            read_process_output=runner.read_setup_process_output,
            close_process_output=lambda _process, _reader: None,
            kill_process=killed.append,
            output_buffer_factory=runner.SetupOutputRingBuffer,
            popen=lambda *_args, **_kwargs: process,
            clock=lambda: 1.0,
            thread_factory=ImmediateThread,
            environment_factory=dict,
            os_name="posix",
        )

        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            runner.execute_setup_script_once_unlocked(
                script,
                3,
                dependencies,
            )

        self.assertEqual(killed, [process])
        self.assertIn(b"restore started", raised.exception.output)

    def test_occupied_concurrency_key_times_out_without_execution(self):
        occupied_lock = threading.Lock()
        occupied_lock.acquire()
        calls = []
        try:
            with self.assertRaisesRegex(TimeoutError, "shared"):
                runner.execute_setup_script_once(
                    {"concurrency_key": "shared"},
                    0.01,
                    execute_unlocked=lambda *_args: calls.append("executed"),
                    concurrency_locks={"shared": occupied_lock},
                    concurrency_guard=threading.Lock(),
                )
        finally:
            occupied_lock.release()

        self.assertEqual(calls, [])


class SetupServiceTests(unittest.TestCase):
    def test_failed_execution_finishes_run_before_raising_summary_error(self):
        events = []
        finished_payloads = []
        script = {
            "uid": "restore",
            "name": "恢复数据库",
            "timeout_seconds": 5,
        }
        resolution = {
            "script": script,
            "target": {
                "scope_type": "project",
                "scope_key": "default",
            },
        }

        def create_record(*_args, **_kwargs):
            events.append("created")
            return {
                "setup_run_id": 7,
                "uid": "setup-run",
                "started_at": 100,
                "target": resolution["target"],
            }

        def execute_once(*_args):
            events.append("executed")
            return {
                "ok": False,
                "exit_code": 9,
                "output": "restore failed",
                "error": "Shell 退出码为 9。",
                "duration_ms": 12,
            }

        def finish_record(_run, execution_result):
            events.append("finished")
            finished_payloads.append(execution_result)
            return 112

        dependencies = service.SetupServiceDependencies(
            get_current_project=lambda: {"project_key": "default"},
            is_platform_database_enabled=lambda: True,
            list_setup_bindings=lambda **_kwargs: [],
            select_setup_binding=lambda *_args: None,
            get_setup_script=lambda _uid: None,
            create_setup_run_record=create_record,
            execute_setup_script_once=execute_once,
            finish_setup_run_record=finish_record,
            redact_setup_text=lambda value, script=None, limit=4000: str(
                value
            ),
            normalize_process_output=lambda value: str(value or ""),
            resolve_setup_profile=lambda _targets: None,
            execute_setup_profile=lambda *_args, **_kwargs: None,
            clock=lambda: 1.0,
        )

        with self.assertRaises(model.SetupPreparationError) as raised:
            service.SetupService(dependencies).execute_setup_profile(
                resolution,
                parent_run_id="parent",
            )

        self.assertEqual(events, ["created", "executed", "finished"])
        self.assertEqual(finished_payloads[0]["status"], "failed")
        self.assertEqual(finished_payloads[0]["exit_code"], 9)
        self.assertEqual(raised.exception.summary["status"], "failed")
        self.assertEqual(raised.exception.summary["script_uid"], "restore")


class SetupSourceBoundaryTests(unittest.TestCase):
    def test_setup_package_never_imports_the_legacy_app_module(self):
        package_dir = Path(validation.__file__).parent
        forbidden = re.compile(r"(^|\s)(import app|from app import)")
        for source_file in package_dir.glob("*.py"):
            with self.subTest(source_file=source_file.name):
                self.assertIsNone(
                    forbidden.search(
                        source_file.read_text(encoding="utf-8")
                    )
                )


if __name__ == "__main__":
    unittest.main()
