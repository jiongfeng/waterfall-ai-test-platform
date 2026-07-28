import io
import json
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from test_plan_viewer.setup import (
    model,
    repository,
    runner,
    service,
    validation,
)


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
                "environment_refs": {
                    "TOKEN": "TARGET_SETUP_TOKEN"
                },
                "timeout_seconds": 12,
            },
            None,
            dependencies,
        )

        self.assertEqual(seen_working_directories, ["fixtures"])
        self.assertEqual(payload["uid"], "generated")
        self.assertEqual(payload["script_content"], "echo restore")
        self.assertEqual(
            payload["environment_refs"],
            {"TOKEN": "TARGET_SETUP_TOKEN"},
        )
        self.assertEqual(payload["timeout_seconds"], 12)

    def test_payload_rejects_plaintext_and_requires_legacy_migration(self):
        dependencies = validation.SetupValidationDependencies(
            resolve_working_directory=lambda _value: None,
            validate_uid=lambda value, field_name, generate=False: (
                str(value or "generated")
            ),
            normalize_name=validation.normalize_setup_name,
            normalize_string_map=validation.normalize_setup_string_map,
            normalize_timeout=validation.normalize_setup_timeout,
        )
        base_payload = {
            "name": "恢复数据库",
            "script_content": "echo restore",
        }

        with self.assertRaisesRegex(
            ValueError,
            "environment_overrides",
        ):
            validation.normalize_setup_script_payload(
                {
                    **base_payload,
                    "environment_overrides": {
                        "TOKEN": "plaintext-secret"
                    },
                },
                None,
                dependencies,
            )

        with self.assertRaisesRegex(ValueError, "重新绑定"):
            validation.normalize_setup_script_payload(
                {"description": "只改描述"},
                {
                    **base_payload,
                    "uid": "legacy",
                    "credentials_migration_required": True,
                    "environment_refs": {},
                },
                dependencies,
            )

        migrated = validation.normalize_setup_script_payload(
            {
                "environment_refs": {
                    "TOKEN": "TARGET_SETUP_TOKEN"
                }
            },
            {
                **base_payload,
                "uid": "legacy",
                "credentials_migration_required": True,
                "environment_refs": {},
            },
            dependencies,
        )
        self.assertEqual(
            migrated["environment_refs"],
            {"TOKEN": "TARGET_SETUP_TOKEN"},
        )

        for references in (
            {"BAD-NAME": "TARGET_SETUP_TOKEN"},
            {"TOKEN": "BAD-NAME"},
            {"TOKEN": ""},
            {"LEAK": "PLATFORM_SESSION_SECRET"},
            {"PATH": "TARGET_SETUP_PATH"},
            {"lc_all": "TARGET_SETUP_LOCALE"},
        ):
            with self.subTest(references=references), self.assertRaisesRegex(
                ValueError,
                "environment_refs",
            ):
                validation.normalize_setup_script_payload(
                    {
                        **base_payload,
                        "environment_refs": references,
                    },
                    None,
                    dependencies,
                )


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
            "environment_refs": {
                "API_TOKEN": "TARGET_SETUP_API_TOKEN"
            },
            "_resolved_environment_values": (
                "long-secret-value",
            ),
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
        self.assertNotIn(
            "_resolved_environment_values",
            snapshot["script"],
        )
        self.assertNotIn("inline-token", snapshot["nested"][0])

    def test_legacy_environment_values_are_never_serialized(self):
        def load_json(value, fallback):
            if value in (None, ""):
                return fallback
            return json.loads(value)

        legacy = model.serialize_setup_script(
            {
                "script_uid": "legacy",
                "name": "旧脚本",
                "script_content": "echo restore",
                "environment_json": json.dumps(
                    {
                        "API_TOKEN": "plaintext-secret",
                        "MODE": "regression",
                    }
                ),
            },
            load_json,
        )

        self.assertEqual(legacy["environment_refs"], {})
        self.assertTrue(legacy["credentials_migration_required"])
        self.assertEqual(
            legacy["legacy_environment_keys"],
            ["API_TOKEN", "MODE"],
        )
        self.assertNotIn("environment_overrides", legacy)
        self.assertNotIn("plaintext-secret", repr(legacy))

        current = model.serialize_setup_script(
            {
                "script_uid": "current",
                "name": "新脚本",
                "script_content": "echo restore",
                "environment_json": json.dumps(
                    repository.serialize_setup_environment_envelope(
                        {
                            "API_TOKEN": "TARGET_SETUP_API_TOKEN"
                        }
                    )
                ),
            },
            load_json,
        )
        self.assertEqual(
            current["environment_refs"],
            {"API_TOKEN": "TARGET_SETUP_API_TOKEN"},
        )
        self.assertFalse(
            current["credentials_migration_required"]
        )
        self.assertEqual(current["legacy_environment_keys"], [])

    def test_malicious_v2_environment_is_safely_canonicalized(self):
        malicious = {
            "version": 2,
            "environment_refs": {
                "SAFE_TOKEN": "TARGET_SETUP_SAFE_TOKEN",
                "LEAKED_TOKEN": "plaintext-secret",
                "PATH": "TARGET_SETUP_PATH",
            },
            "environment_overrides": {
                "LEGACY_TOKEN": "another-plaintext-secret",
            },
            "password": "top-level-secret",
        }

        envelope = (
            model.build_setup_environment_scrub_envelope(
                malicious
            )
        )

        self.assertEqual(
            envelope,
            {
                "version": 2,
                "environment_refs": {
                    "SAFE_TOKEN": "TARGET_SETUP_SAFE_TOKEN",
                },
                "credentials_migration_required": True,
                "legacy_environment_keys": [
                    "LEAKED_TOKEN",
                    "LEGACY_TOKEN",
                    "PATH",
                    "password",
                ],
            },
        )
        self.assertNotIn("plaintext-secret", repr(envelope))
        self.assertNotIn("top-level-secret", repr(envelope))
        self.assertIsNone(
            model.build_setup_environment_scrub_envelope(
                envelope
            )
        )
        self.assertEqual(
            model.deserialize_setup_environment(malicious),
            {
                "environment_refs": {
                    "SAFE_TOKEN": "TARGET_SETUP_SAFE_TOKEN",
                },
                "credentials_migration_required": True,
                "legacy_environment_keys": [
                    "LEAKED_TOKEN",
                    "LEGACY_TOKEN",
                    "PATH",
                    "password",
                ],
            },
        )
        noncanonical_version = {
            **malicious,
            "version": 2.0,
        }
        self.assertEqual(
            model.build_setup_environment_scrub_envelope(
                noncanonical_version
            )["environment_refs"],
            {
                "SAFE_TOKEN": "TARGET_SETUP_SAFE_TOKEN",
            },
        )

    def test_empty_legacy_environment_scrub_is_idempotent(self):
        envelope = (
            model.build_setup_environment_scrub_envelope({})
        )

        self.assertEqual(
            envelope,
            {
                "version": 2,
                "environment_refs": {},
            },
        )
        self.assertIsNone(
            model.build_setup_environment_scrub_envelope(
                envelope
            )
        )

    def test_legacy_run_snapshot_drops_environment_values(self):
        snapshot = json.dumps(
            {
                "script": {
                    "environment_overrides": {
                        "API_TOKEN": "plaintext-secret"
                    }
                }
            }
        )
        serialized = model.serialize_setup_run(
            {
                "run_uid": "legacy-run",
                "script_snapshot_json": snapshot,
            },
            lambda value, fallback: (
                json.loads(value) if value else fallback
            ),
        )

        script = serialized["script_snapshot"]["script"]
        self.assertNotIn("environment_overrides", script)
        self.assertEqual(script["environment_refs"], {})
        self.assertEqual(
            script["legacy_environment_keys"],
            ["API_TOKEN"],
        )
        self.assertNotIn("plaintext-secret", repr(serialized))

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


class SetupRepositoryMigrationTests(unittest.TestCase):
    def test_legacy_environment_json_is_scrubbed_once_without_values(self):
        class Cursor:
            def __init__(self, rows):
                self.rows = rows
                self.executions = []

            def execute(self, sql, parameters=()):
                self.executions.append((sql, parameters))

            def fetchall(self):
                return self.rows

        current_envelope = json.dumps(
            {
                "version": 2,
                "environment_refs": {
                    "API_TOKEN": "TARGET_SETUP_API_TOKEN"
                },
            }
        )
        cursor = Cursor(
            [
                {
                    "script_id": 1,
                    "environment_json": json.dumps(
                        {
                            "API_TOKEN": "plaintext-secret",
                            "MODE": "regression",
                            "BAD-NAME": "invalid-key-secret",
                        }
                    ),
                },
                {
                    "script_id": 2,
                    "environment_json": "{invalid-json",
                },
                {
                    "script_id": 3,
                    "environment_json": current_envelope,
                },
                {
                    "script_id": 4,
                    "environment_json": json.dumps(
                        {
                            "version": 2,
                            "environment_refs": {
                                "SAFE_TOKEN": (
                                    "TARGET_SETUP_SAFE_TOKEN"
                                ),
                                "LEAKED_TOKEN": (
                                    "plaintext-secret-v2"
                                ),
                            },
                            "password": "top-level-secret-v2",
                        }
                    ),
                },
            ]
        )

        scrubbed = (
            repository.scrub_legacy_setup_environment_rows(
                cursor,
                "`setup_scripts`",
            )
        )

        self.assertEqual(scrubbed, 3)
        updates = cursor.executions[1:]
        self.assertEqual(len(updates), 3)
        first_envelope = json.loads(updates[0][1][0])
        self.assertEqual(
            first_envelope,
            {
                "version": 2,
                "environment_refs": {},
                "credentials_migration_required": True,
                "legacy_environment_keys": [
                    "API_TOKEN",
                    "MODE",
                ],
            },
        )
        self.assertNotIn("plaintext-secret", repr(updates))
        self.assertNotIn("regression", repr(updates))
        self.assertNotIn("invalid-key-secret", repr(updates))
        self.assertEqual(updates[0][1][1], 1)
        self.assertTrue(
            json.loads(updates[1][1][0])[
                "credentials_migration_required"
            ]
        )
        malicious_v2_envelope = json.loads(
            updates[2][1][0]
        )
        self.assertEqual(
            malicious_v2_envelope["environment_refs"],
            {
                "SAFE_TOKEN": "TARGET_SETUP_SAFE_TOKEN",
            },
        )
        self.assertTrue(
            malicious_v2_envelope[
                "credentials_migration_required"
            ]
        )
        self.assertEqual(
            malicious_v2_envelope[
                "legacy_environment_keys"
            ],
            ["LEAKED_TOKEN", "password"],
        )
        self.assertNotIn("plaintext-secret-v2", repr(updates))
        self.assertNotIn("top-level-secret-v2", repr(updates))

        idempotent_cursor = Cursor(
            [
                {
                    "script_id": 1,
                    "environment_json": updates[0][1][0],
                },
                {
                    "script_id": 2,
                    "environment_json": updates[1][1][0],
                },
                {
                    "script_id": 3,
                    "environment_json": current_envelope,
                },
                {
                    "script_id": 4,
                    "environment_json": updates[2][1][0],
                },
            ]
        )
        self.assertEqual(
            repository.scrub_legacy_setup_environment_rows(
                idempotent_cursor,
                "`setup_scripts`",
            ),
            0,
        )
        self.assertEqual(len(idempotent_cursor.executions), 1)


class SetupRunnerTests(unittest.TestCase):
    def test_application_keeps_the_runner_safe_environment_factory(self):
        dependencies = app._setup_runner_dependencies()

        self.assertIs(
            dependencies.environment_factory,
            runner.build_setup_environment,
        )

    def test_default_environment_does_not_inherit_platform_secrets(self):
        environment = runner.build_setup_environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/tmp/demo",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PLATFORM_SESSION_SECRET": "session-secret",
                "PLATFORM_DB_PASSWORD": "database-secret",
                "OPENCODE_SERVER_PASSWORD": "opencode-secret",
            }
        )

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["LC_ALL"], "C.UTF-8")
        self.assertNotIn("PLATFORM_SESSION_SECRET", environment)
        self.assertNotIn("PLATFORM_DB_PASSWORD", environment)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", environment)

    def test_timeout_requests_process_cancellation_and_keeps_output(self):
        script = {
            "script_content": "echo restore",
            "working_directory": "",
            "environment_refs": {
                "API_TOKEN": "TARGET_SETUP_API_TOKEN"
            },
        }
        killed = []

        class TimedOutProcess:
            args = ["/bin/bash", "-c", "echo restore"]
            stdout = io.BytesIO(
                b"secret-value restore started\n"
            )

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
            redact_setup_text=lambda value, script=None, limit=4000: (
                model.redact_setup_text(
                    value,
                    script,
                    limit,
                    redact_sensitive_text=lambda text, limit=None: str(
                        text or ""
                    ),
                )
            ),
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

        with (
            patch.dict(
                runner.os.environ,
                {"TARGET_SETUP_API_TOKEN": "secret-value"},
            ),
            self.assertRaises(subprocess.TimeoutExpired) as raised,
        ):
            runner.execute_setup_script_once_unlocked(
                script,
                3,
                dependencies,
            )

        self.assertEqual(killed, [process])
        self.assertIn("restore started", raised.exception.output)
        self.assertNotIn("secret-value", raised.exception.output)
        self.assertIn("******", raised.exception.output)

    def test_missing_or_legacy_environment_fails_before_shell_start(self):
        popen_calls = []
        dependencies = runner.SetupRunnerDependencies(
            resolve_working_directory=lambda _value: Path("/tmp"),
            normalize_process_output=str,
            redact_setup_text=lambda value, script=None, limit=4000: str(
                value
            ),
            read_process_output=runner.read_setup_process_output,
            close_process_output=lambda _process, _reader: None,
            kill_process=lambda _process: None,
            output_buffer_factory=runner.SetupOutputRingBuffer,
            popen=lambda *_args, **_kwargs: popen_calls.append(
                (_args, _kwargs)
            ),
            environment_factory=dict,
        )

        with (
            patch.dict(runner.os.environ, {}, clear=True),
            self.assertRaisesRegex(
                ValueError,
                "TARGET_MISSING_SETUP_SECRET",
            ),
        ):
            runner.execute_setup_script_once_unlocked(
                {
                    "script_content": "echo never",
                    "environment_refs": {
                        "API_TOKEN": "TARGET_MISSING_SETUP_SECRET"
                    },
                },
                3,
                dependencies,
            )

        with self.assertRaisesRegex(ValueError, "旧版明文"):
            runner.execute_setup_script_once_unlocked(
                {
                    "script_content": "echo never",
                    "environment_overrides": {
                        "API_TOKEN": "plaintext-secret"
                    },
                },
                3,
                dependencies,
            )

        for references in (
            {"LEAK": "PLATFORM_SESSION_SECRET"},
            {"PATH": "TARGET_SETUP_PATH"},
            {"lc_all": "TARGET_SETUP_LOCALE"},
        ):
            with (
                self.subTest(references=references),
                self.assertRaisesRegex(ValueError, "environment_refs"),
            ):
                runner.execute_setup_script_once_unlocked(
                    {
                        "script_content": "echo never",
                        "environment_refs": references,
                    },
                    3,
                    dependencies,
                )

        self.assertEqual(popen_calls, [])

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
