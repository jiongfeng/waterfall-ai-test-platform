import io
import inspect
import json
import subprocess
import tempfile
import threading
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import app


def read_platform_javascript():
    static_dir = app.APP_DIR / "static"
    module_files = sorted((static_dir / "js").rglob("*.js"))
    source_files = [*module_files, static_dir / "app.js"]
    return "\n".join(
        f"/* {source_file.relative_to(static_dir)} */\n"
        + source_file.read_text(encoding="utf-8")
        for source_file in source_files
    )


def render_index_template():
    with app.app.test_request_context("/"):
        return app.render_template("index.html")


def read_platform_stylesheets():
    static_dir = app.APP_DIR / "static"
    module_files = sorted((static_dir / "css").rglob("*.css"))
    source_files = [*module_files, static_dir / "styles.css"]
    return "\n".join(
        f"/* {source_file.relative_to(static_dir)} */\n"
        + source_file.read_text(encoding="utf-8")
        for source_file in source_files
    )


class SaveAssetRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.client = app.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def assert_save_route_rolls_back(self, url, payload, file_getter, sync_function, suffix):
        asset_file = self.project_root / suffix
        asset_file.parent.mkdir(parents=True, exist_ok=True)
        asset_file.write_text("原始内容", encoding="utf-8")

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_project_root", return_value=self.project_root),
            patch.object(app, file_getter, return_value=asset_file),
            patch.object(app, sync_function, side_effect=RuntimeError("版本同步失败")),
        ):
            response = self.client.put(url, json=payload)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(asset_file.read_text(encoding="utf-8"), "原始内容")
        self.assertFalse(list(asset_file.parent.glob(f".{asset_file.name}.*.tmp")))

    def test_module_save_restores_file_when_version_sync_fails(self):
        self.assert_save_route_rolls_back(
            "/api/modules/登录模块",
            {"markdown": "修改后的模块内容"},
            "get_module_file",
            "sync_plan_asset",
            "specs/登录模块/登录模块.md",
        )

    def test_plan_save_restores_file_when_version_sync_fails(self):
        self.assert_save_route_rolls_back(
            "/api/plans/登录模块/登录计划.md",
            {"markdown": "修改后的计划内容"},
            "get_plan_file",
            "sync_plan_asset",
            "specs/登录模块/登录计划.md",
        )

    def test_script_save_restores_file_when_version_sync_fails(self):
        self.assert_save_route_rolls_back(
            "/api/test-scripts/登录模块/登录脚本.spec.ts",
            {
                "content": "修改后的脚本内容",
                "expected_revision_id": None,
            },
            "get_script_file",
            "sync_script_asset",
            "tests/登录模块/登录脚本.spec.ts",
        )

    def test_script_revision_content_get_does_not_require_a_json_body(self):
        asset = {"asset_id": 1, "asset_type": "script"}
        revision = {
            "revision_id": 2,
            "git_commit_sha": "deadbeef",
            "file_path": "tests/登录模块/登录脚本.spec.ts",
        }
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_test_asset_by_id", return_value=asset),
            patch.object(app, "get_asset_revision", return_value=revision),
            patch.object(app, "git_show_file", return_value="历史脚本内容"),
        ):
            response = self.client.get("/api/assets/1/revisions/2/content")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "历史脚本内容")

    def test_script_save_and_restore_reject_null_or_missing_revision_baseline(self):
        script_file = self.project_root / "tests/login/login.spec.ts"
        script_file.parent.mkdir(parents=True)
        script_file.write_text("original", encoding="utf-8")
        asset = {
            "asset_id": 1,
            "asset_type": "script",
            "module_name": "login",
            "current_path": str(script_file),
            "current_revision_id": 5,
        }
        revision = {"revision_id": 2, "version_no": 2, "git_commit_sha": "deadbeef", "file_path": str(script_file)}
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_script_file", return_value=script_file),
            patch.object(app, "get_test_asset_by_path", return_value=asset),
            patch.object(app, "get_test_asset_by_id", return_value=asset),
            patch.object(app, "get_asset_revision", return_value=revision),
            patch.object(
                app,
                "call_with_script_target_lease",
                side_effect=lambda _runtime, _module, _filename, callback: callback(),
            ),
        ):
            stale_save = self.client.put(
                "/api/test-scripts/login/login.spec.ts",
                json={"content": "stale", "expected_revision_id": None},
            )
            missing_restore = self.client.post("/api/assets/1/revisions/2/restore", json={})
            stale_restore = self.client.post(
                "/api/assets/1/revisions/2/restore",
                json={"expected_revision_id": None},
            )
        self.assertEqual(stale_save.status_code, 409)
        self.assertEqual(missing_restore.status_code, 400)
        self.assertEqual(stale_restore.status_code, 409)
        self.assertEqual(script_file.read_text(encoding="utf-8"), "original")

    def test_repair_prompt_redacts_failure_before_serialization(self):
        failure = {"authorization": "Bearer secret-token", "password": "secret-password"}
        with (
            patch.object(app, "validate_module_name", side_effect=lambda value: value),
            patch.object(app, "validate_script_filename", side_effect=lambda value: value),
            patch.object(app, "validate_plan_filename", side_effect=lambda value: value),
            patch.object(app, "get_current_project_language", return_value="en"),
            patch.object(
                app.agent_failure_handling,
                "redact_agent_failure_value",
                return_value={"authorization": "[redacted]", "password": "[redacted]"},
            ),
        ):
            prompt = app.build_agent_script_repair_prompt(
                {"module_name": "login", "filename": "login.spec.ts", "plan_filename": "login.md"},
                failure,
            )
        self.assertNotIn("secret-token", prompt)
        self.assertNotIn("secret-password", prompt)

    def test_plan_save_restores_file_when_response_metadata_loading_fails(self):
        asset_file = self.project_root / "specs" / "登录模块" / "登录计划.md"
        asset_file.parent.mkdir(parents=True)
        asset_file.write_text("原始内容", encoding="utf-8")

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_project_root", return_value=self.project_root),
            patch.object(app, "get_plan_file", return_value=asset_file),
            patch.object(app, "sync_plan_asset", return_value={"asset_id": 1}),
            patch.object(app, "list_asset_revisions", side_effect=RuntimeError("版本列表读取失败")),
        ):
            response = self.client.put(
                "/api/plans/登录模块/登录计划.md",
                json={"markdown": "修改后的计划内容"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(asset_file.read_text(encoding="utf-8"), "原始内容")

    def test_save_rollback_compensates_a_git_commit_created_before_sync_failure(self):
        asset_file = self.project_root / "specs" / "登录模块" / "登录计划.md"
        asset_file.parent.mkdir(parents=True)
        asset_file.write_text("原始内容", encoding="utf-8")
        self.run_git("init")
        self.run_git("config", "user.name", "Regression Test")
        self.run_git("config", "user.email", "regression-test@example.com")
        self.run_git("add", ".")
        self.run_git("commit", "-m", "initial")

        def commit_then_fail():
            self.run_git("add", ".")
            self.run_git("commit", "-m", "partial save")
            raise RuntimeError("数据库版本同步失败")

        with (
            patch.object(app, "get_project_root", return_value=self.project_root),
            patch.object(
                app,
                "project_relative_path",
                side_effect=lambda path: Path(path).relative_to(self.project_root).as_posix(),
            ),
            self.assertRaisesRegex(RuntimeError, "数据库版本同步失败"),
        ):
            app.save_asset_content_with_rollback(
                asset_file,
                "修改后的内容",
                commit_then_fail,
                lambda: None,
                "rollback failed save: 登录模块/登录计划.md",
            )

        self.assertEqual(asset_file.read_text(encoding="utf-8"), "原始内容")
        self.assertEqual(self.run_git("status", "--porcelain").stdout, "")
        self.assertEqual(self.run_git("show", "HEAD:specs/登录模块/登录计划.md").stdout, "原始内容")

    def run_git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )




class SetupScriptRegressionTests(unittest.TestCase):
    @staticmethod
    def script(uid="restore-database", name="恢复数据库"):
        return {
            "uid": uid,
            "name": name,
            "description": "",
            "script_content": "echo restore",
            "working_directory": "",
            "environment_overrides": {},
            "timeout_seconds": 30,
            "concurrency_key": "",
            "enabled": True,
        }

    def resolution(self, script=None):
        script = script or self.script()
        return {
            "binding": {"uid": "binding", "scope_type": "project", "scope_key": "default"},
            "target": {"scope_type": "project", "scope_key": "default"},
            "script": script,
            "profile": script,
        }

    def test_script_validation_requires_safe_content_and_runtime_fields(self):
        with patch.object(app, "resolve_setup_working_directory", return_value=Path("/tmp/project")):
            normalized = app.normalize_setup_script_payload(
                {
                    "name": "恢复数据库",
                    "script_content": "echo restore\n",
                    "environment_overrides": {"MODE": "regression"},
                    "timeout_seconds": 45,
                }
            )
            self.assertEqual(normalized["script_content"], "echo restore\n")
            self.assertEqual(normalized["environment_overrides"], {"MODE": "regression"})
            self.assertEqual(normalized["timeout_seconds"], 45)

            for payload, message in (
                ({"name": "空脚本", "script_content": "  "}, "script_content"),
                ({"name": "非法脚本", "script_content": "echo ok\x00"}, "null character"),
                ({"name": "超时错误", "script_content": "echo ok", "timeout_seconds": 0}, "timeout_seconds"),
                ({"name": "环境错误", "script_content": "echo ok", "environment_overrides": []}, "environment_overrides"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    app.normalize_setup_script_payload(payload)

    def test_binding_precedence_is_scope_then_priority(self):
        targets = [
            {"scope_type": "script", "scope_key": "模块/脚本.spec.ts"},
            {"scope_type": "test_suite", "scope_key": "suite"},
            {"scope_type": "project", "scope_key": "default"},
        ]
        selected = app.select_setup_binding(
            [
                {"uid": "project", "script_uid": "project-script", "scope_type": "project", "scope_key": "default", "priority": 999, "enabled": True},
                {"uid": "suite", "script_uid": "suite-script", "scope_type": "test_suite", "scope_key": "suite", "priority": 100, "enabled": True},
                {"uid": "script-low", "script_uid": "low-script", "scope_type": "script", "scope_key": "模块/脚本.spec.ts", "priority": 1, "enabled": True},
                {"uid": "script-high", "script_uid": "high-script", "scope_type": "script", "scope_key": "模块/脚本.spec.ts", "priority": 2, "enabled": True},
                {"uid": "script-disabled", "script_uid": "disabled-script", "scope_type": "script", "scope_key": "模块/脚本.spec.ts", "priority": 999, "enabled": False},
            ],
            targets,
        )
        self.assertEqual(selected["uid"], "script-high")

    def test_resolver_returns_the_selected_script_and_target(self):
        binding = {
            "uid": "suite-binding",
            "script_uid": "restore-database",
            "scope_type": "test_suite",
            "scope_key": "suite",
            "priority": 10,
            "enabled": True,
        }
        script = self.script()
        targets = [
            {"scope_type": "test_suite", "scope_key": "suite"},
            {"scope_type": "project", "scope_key": "default"},
        ]
        with (
            patch.object(app, "is_platform_database_enabled", return_value=True),
            patch.object(app, "list_setup_bindings_from_mysql", return_value=[binding]),
            patch.object(app, "get_setup_script_from_mysql", return_value=script),
        ):
            resolution = app.resolve_setup_profile(targets)

        self.assertIs(resolution["script"], script)
        self.assertIs(resolution["profile"], script)
        self.assertEqual(resolution["target"], targets[0])

    def test_missing_binding_does_not_fall_back_to_the_legacy_database_baseline(self):
        with patch.object(app, "resolve_setup_profile", return_value=None), patch.object(
            app, "prepare_database_baseline_for_test"
        ) as legacy_restore:
            result = app.prepare_bound_setup(
                "test-run", [{"scope_type": "project", "scope_key": "default"}]
            )

        self.assertIsNone(result)
        legacy_restore.assert_not_called()

    def test_opencode_generation_runs_bound_setup_before_session_without_legacy_baseline(self):
        targets = [{"scope_type": "project", "scope_key": "default"}]
        timeline = []

        def prepare_setup(parent_run_id, actual_targets, emit_log=None):
            timeline.append(("setup", parent_run_id, actual_targets))
            emit_log("开始执行准备脚本：恢复数据库。")
            emit_log("准备脚本完成：恢复数据库。")
            return {"uid": "setup-run", "status": "succeeded"}

        def create_session(path, *_args, **_kwargs):
            self.assertEqual(path, "/session")
            timeline.append(("session",))
            return {"id": "session-1"}

        def send_prompt(session_id, *_args, **_kwargs):
            timeline.append(("prompt", session_id))
            return {}

        with (
            patch.object(
                app,
                "prepare_bound_setup",
                side_effect=prepare_setup,
            ),
            patch.object(app, "prepare_database_baseline_for_test") as legacy_restore,
            patch.object(app, "register_opencode_task"),
            patch.object(app, "is_opencode_task_cancelled", return_value=False),
            patch.object(app, "build_opencode_session_payload", return_value={}),
            patch.object(app, "opencode_project_query", return_value={}),
            patch.object(app, "opencode_request", side_effect=create_session),
            patch.object(app, "set_opencode_task_session", return_value=False),
            patch.object(app, "get_opencode_task_timeout_seconds", return_value=30),
            patch.object(app, "opencode_event_stream", side_effect=RuntimeError("stream unavailable")),
            patch.object(app, "send_opencode_prompt_to_session", side_effect=send_prompt),
            patch.object(app, "summarize_opencode_response", return_value=""),
            patch.object(app, "cleanup_opencode_task"),
        ):
            chunks = list(
                app.stream_plan_generation(
                    "模块",
                    "prompt",
                    Path("/tmp/not-created.md"),
                    completion_required=False,
                    setup_targets=targets,
                    setup_parent_run_id="agent-run",
                )
            )

        self.assertEqual(
            timeline,
            [
                ("setup", "agent-run", targets),
                ("session",),
                ("prompt", "session-1"),
            ],
        )
        self.assertTrue(any("开始执行准备脚本" in chunk for chunk in chunks))
        legacy_restore.assert_not_called()
        self.assertNotIn(
            "prepare_database",
            inspect.signature(app.stream_plan_generation).parameters,
        )

    def test_opencode_generation_setup_failure_prevents_session_creation(self):
        targets = [{"scope_type": "project", "scope_key": "default"}]
        failed_setup = {
            "uid": "setup-failed",
            "status": "failed",
            "script_uid": "restore-database",
        }

        def fail_setup(_parent_run_id, _targets, emit_log=None):
            emit_log("开始执行准备脚本：恢复数据库。")
            raise app.SetupPreparationError(
                "准备失败：恢复数据库失败",
                failed_setup,
            )

        with (
            patch.object(
                app,
                "prepare_bound_setup",
                side_effect=fail_setup,
            ),
            patch.object(app, "prepare_database_baseline_for_test") as legacy_restore,
            patch.object(app, "register_opencode_task"),
            patch.object(app, "is_opencode_task_cancelled", return_value=False),
            patch.object(app, "opencode_request") as create_session,
            patch.object(app, "cleanup_opencode_task"),
        ):
            events = []
            for chunk in app.stream_plan_generation(
                "模块",
                "prompt",
                Path("/tmp/not-created.md"),
                completion_required=False,
                setup_targets=targets,
                setup_parent_run_id="agent-run",
            ):
                events.extend(app.parse_sse_text_blocks(chunk))

        create_session.assert_not_called()
        legacy_restore.assert_not_called()
        done = [payload for event, payload in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertIn("准备失败", done["error"])

    def test_background_plan_generation_uses_bound_setup_instead_of_legacy_baseline(self):
        targets = [{"scope_type": "project", "scope_key": "default"}]
        timeline = []

        def prepare_setup(parent_run_id, actual_targets, emit_log=None):
            timeline.append(("setup", parent_run_id, actual_targets))
            emit_log("准备脚本完成。")

        def send_prompt(*_args, **_kwargs):
            timeline.append(("prompt",))
            return {}

        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "计划.md"
            target_file.write_text("generated", encoding="utf-8")
            with (
                patch.object(app, "update_generation_job"),
                patch.object(app, "append_generation_log"),
                patch.object(app, "build_setup_targets", return_value=targets),
                patch.object(
                    app,
                    "prepare_bound_setup",
                    side_effect=prepare_setup,
                ),
                patch.object(app, "prepare_database_baseline_for_test") as legacy_restore,
                patch.object(app, "send_opencode_prompt", side_effect=send_prompt),
                patch.object(app, "summarize_opencode_response", return_value=""),
            ):
                app.run_plan_generation_job(
                    "planner-job",
                    "prompt",
                    target_file,
                    "playwright-test-planner",
                )

        self.assertEqual(
            timeline,
            [
                ("setup", "planner-job", targets),
                ("prompt",),
            ],
        )
        legacy_restore.assert_not_called()

    def test_shell_runtime_uses_bash_cwd_environment_timeout_and_redacts_secrets(self):
        script = self.script()
        script["script_content"] = "echo $API_TOKEN"
        script["environment_overrides"] = {"API_TOKEN": "secret-value", "MODE": "regression"}

        class FakeProcess:
            def __init__(self):
                self.args = ["/bin/bash", "-c", script["script_content"]]
                self.stdout = io.BytesIO(b"secret-value\n")
                self.wait_timeouts = []

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                return 0

        process = FakeProcess()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                app.os.environ,
                {"PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION": "true"},
            ),
            patch.object(
                app,
                "resolve_setup_working_directory",
                return_value=Path(directory),
            ),
            patch.object(
                app.subprocess,
                "Popen",
                return_value=process,
            ) as popen,
        ):
            result = app.execute_setup_script_once(script, 17)

        command = popen.call_args.args[0]
        self.assertIn(command[0], {"/bin/bash", "/bin/sh"})
        self.assertEqual(command[1:], ["-c", "echo $API_TOKEN"])
        self.assertEqual(popen.call_args.kwargs["cwd"], Path(directory))
        self.assertEqual(popen.call_args.kwargs["env"]["MODE"], "regression")
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(popen.call_args.kwargs["bufsize"], 0)
        self.assertEqual(popen.call_args.kwargs["start_new_session"], app.os.name == "posix")
        self.assertEqual(process.wait_timeouts, [17])
        self.assertTrue(result["ok"])
        self.assertNotIn("secret-value", result["output"])
        self.assertIn("******", result["output"])

    def test_shell_timeout_kills_the_process_group_and_preserves_captured_output(self):
        script = self.script()

        class TimedOutProcess:
            def __init__(self):
                self.args = ["/bin/bash", "-c", script["script_content"]]
                self.stdout = io.BytesIO(b"restore started\n")
                self.pid = 4321
                self.wait_timeouts = []
                self.killed = False

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                if len(self.wait_timeouts) == 1:
                    raise subprocess.TimeoutExpired(self.args, timeout)
                return -9

            def kill(self):
                self.killed = True

        process = TimedOutProcess()
        with (
            patch.dict(
                app.os.environ,
                {"PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION": "true"},
            ),
            patch.object(app, "resolve_setup_working_directory", return_value=Path("/tmp")),
            patch.object(app.subprocess, "Popen", return_value=process),
            patch.object(app.os, "killpg") as killpg,
            self.assertRaises(subprocess.TimeoutExpired) as raised,
        ):
            app.execute_setup_script_once(script, 3)

        if app.os.name == "posix":
            killpg.assert_called_once_with(process.pid, app.signal.SIGKILL)
            self.assertFalse(process.killed)
        else:
            killpg.assert_not_called()
            self.assertTrue(process.killed)
        self.assertEqual(process.wait_timeouts, [3, 5])
        self.assertIn(b"restore started", raised.exception.output)

    def test_host_setup_script_execution_is_disabled_by_default(self):
        with (
            patch.dict(
                app.os.environ,
                {"PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION": ""},
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION",
            ),
        ):
            app.execute_setup_script_once(self.script(), 3)

    def test_concurrency_key_times_out_while_the_same_script_is_running(self):
        script = self.script("locked", "互斥脚本")
        script["concurrency_key"] = "shared-fixture"
        occupied_lock = threading.Lock()
        occupied_lock.acquire()
        app.SETUP_CONCURRENCY_LOCKS["shared-fixture"] = occupied_lock
        try:
            with self.assertRaisesRegex(TimeoutError, "shared-fixture"):
                app.execute_setup_script_once(script, 0.01)
        finally:
            occupied_lock.release()
            app.SETUP_CONCURRENCY_LOCKS.pop("shared-fixture", None)

    def test_failed_shell_run_is_persisted_and_raises_a_structured_error(self):
        setup_run = {
            "setup_run_id": 1,
            "uid": "setup-run",
            "started_at": 1000,
            "target": {"scope_type": "project", "scope_key": "default"},
        }
        with (
            patch.object(app, "create_setup_run_record", return_value=setup_run),
            patch.object(
                app,
                "execute_setup_script_once",
                return_value={
                    "ok": False,
                    "exit_code": 7,
                    "output": "restore failed",
                    "error": "Shell 退出码为 7。",
                    "duration_ms": 12,
                },
            ),
            patch.object(app, "finish_setup_run_record", return_value=1100) as finish_run,
            self.assertRaisesRegex(app.SetupPreparationError, "Shell 退出码") as raised,
        ):
            app.execute_setup_profile(self.resolution(), parent_run_id="test-run")

        self.assertEqual(raised.exception.summary["script_uid"], "restore-database")
        self.assertEqual(raised.exception.summary["status"], "failed")
        execution_result = finish_run.call_args.args[1]
        self.assertEqual(execution_result["status"], "failed")
        self.assertEqual(execution_result["exit_code"], 7)

    def test_setup_script_crud_api_contract(self):
        client = app.app.test_client()
        script = self.script()
        auth = patch.object(app, "get_auth_config", return_value={"enabled": False})

        with auth, patch.object(app, "list_setup_scripts_from_mysql", return_value=[script]):
            response = client.get("/api/setup-scripts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["scripts"], [script])

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "save_setup_script_in_mysql", return_value=script
        ) as save:
            response = client.post("/api/setup-scripts", json=script)
            updated = client.put("/api/setup-scripts/restore-database", json={"name": "新名称"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(save.call_args_list[0].args, (script,))
        self.assertEqual(save.call_args_list[1].args, ({"name": "新名称"}, "restore-database"))

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "save_setup_script_in_mysql", return_value=None
        ):
            self.assertEqual(client.put("/api/setup-scripts/missing", json={"name": "x"}).status_code, 404)

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "delete_setup_script_in_mysql", side_effect=[True, False]
        ):
            self.assertEqual(client.delete("/api/setup-scripts/restore-database").status_code, 200)
            self.assertEqual(client.delete("/api/setup-scripts/missing").status_code, 404)

    def test_binding_crud_api_uses_script_uid(self):
        client = app.app.test_client()
        binding = {
            "uid": "suite-binding",
            "script_uid": "restore-database",
            "scope_type": "test_suite",
            "scope_key": "suite-1",
            "scope_label": "回归测试集",
            "priority": 10,
            "enabled": True,
        }
        normalized = app.normalize_setup_binding_payload(binding)
        self.assertEqual(normalized["script_uid"], "restore-database")

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "list_setup_bindings_from_mysql", return_value=[binding]
        ):
            response = client.get("/api/setup-bindings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["bindings"], [binding])

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "save_setup_binding_in_mysql", return_value=binding
        ) as save:
            created = client.post("/api/setup-bindings", json=binding)
            updated = client.put("/api/setup-bindings/suite-binding", json={"priority": 20})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(save.call_args_list[0].args, (binding,))
        self.assertEqual(save.call_args_list[1].args, ({"priority": 20}, "suite-binding"))

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "delete_setup_binding_in_mysql", return_value=True
        ):
            self.assertEqual(client.delete("/api/setup-bindings/suite-binding").status_code, 200)

    def test_delete_script_cascades_bindings_but_preserves_run_history(self):
        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params):
                statements.append((" ".join(sql.split()), params))

        class Connection:
            def __init__(self):
                self.committed = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                self.committed = True

        connection = Connection()
        with (
            patch.object(app, "get_setup_tables", return_value={}),
            patch.object(app, "get_current_project_id", return_value=9),
            patch.object(app, "get_setup_scripts_table", return_value="setup_scripts"),
            patch.object(app, "get_setup_bindings_table", return_value="setup_bindings"),
            patch.object(app, "get_setup_script_row", return_value={"script_id": 42}),
            patch.object(app, "platform_mysql_connection", return_value=connection),
        ):
            deleted = app.delete_setup_script_in_mysql("restore-database")

        self.assertTrue(deleted)
        self.assertTrue(connection.committed)
        self.assertEqual([sql.split()[2] for sql, _params in statements], ["setup_bindings", "setup_scripts"])
        self.assertFalse(any("setup_runs" in sql for sql, _params in statements))

    def test_trial_run_and_filtered_history_api_contract(self):
        client = app.app.test_client()
        script = self.script()
        run = {"uid": "setup-run", "script_uid": script["uid"], "status": "succeeded"}
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_setup_script_from_mysql", return_value=script),
            patch.object(app, "get_current_project", return_value={"project_key": "demo"}),
            patch.object(app, "execute_setup_profile", return_value=run) as execute,
        ):
            response = client.post(
                "/api/setup-scripts/restore-database/trial-run",
                json={"target_type": "test_suite", "target_key": "suite-1"},
            )
        self.assertEqual(response.status_code, 200)
        resolution = execute.call_args.args[0]
        self.assertIs(resolution["script"], script)
        self.assertEqual(execute.call_args.kwargs["target_override"], {"scope_type": "test_suite", "scope_key": "suite-1"})

        failed_run = {"uid": "setup-failed", "script_uid": script["uid"], "status": "failed"}
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_setup_script_from_mysql", return_value=script),
            patch.object(app, "get_current_project", return_value={"project_key": "demo"}),
            patch.object(
                app,
                "execute_setup_profile",
                side_effect=app.SetupPreparationError("准备失败", failed_run),
            ),
        ):
            response = client.post("/api/setup-scripts/restore-database/trial-run", json={})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["run"], failed_run)

        with patch.object(app, "get_auth_config", return_value={"enabled": False}), patch.object(
            app, "list_setup_runs_from_mysql", return_value=[run]
        ) as list_runs:
            response = client.get("/api/setup-runs?limit=25&script_uid=restore-database")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["runs"], [run])
        list_runs.assert_called_once_with("25", "restore-database")

    def test_batch_execution_routes_do_not_offer_script_targets_to_the_resolver(self):
        client = app.app.test_client()
        items = [{"module_name": "模块", "filename": "脚本.spec.ts"}]
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_current_project", return_value={"project_key": "default"}),
            patch.object(app, "resolve_setup_profile", return_value=None) as resolve_module,
            patch.object(
                app,
                "build_module_script_execution_context",
                return_value={"filenames": ["脚本.spec.ts"]},
            ),
            patch.object(app, "stream_module_script_execution", return_value=iter(())),
        ):
            response = client.post(
                "/api/module-script-execution-stream",
                json={
                    "module_name": "模块",
                    "filenames": ["脚本.spec.ts"],
                    "execution_mode": "batch",
                },
            )
        self.assertEqual(response.status_code, 200)
        resolve_module.assert_called_once_with(
            [{"scope_type": "project", "scope_key": "default"}]
        )

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_current_project", return_value={"project_key": "default"}),
            patch.object(app, "resolve_setup_profile", return_value=None) as resolve_suite,
            patch.object(app, "build_test_suite_execution_context", return_value={"items": items}),
            patch.object(app, "stream_test_suite_execution", return_value=iter(())),
        ):
            response = client.post(
                "/api/test-suite-execution-stream",
                json={
                    "suite_id": "suite",
                    "suite_name": "回归测试集",
                    "items": items,
                    "execution_mode": "batch",
                },
            )
        self.assertEqual(response.status_code, 200)
        resolve_suite.assert_called_once_with(
            [
                {"scope_type": "test_suite", "scope_key": "suite"},
                {"scope_type": "project", "scope_key": "default"},
            ]
        )

    def test_setup_failure_prevents_playwright_process_start(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            context = {
                "script_file": project_root / "tests" / "模块" / "脚本.spec.ts",
                "project_root": project_root,
                "relative_script_path": "tests/模块/脚本.spec.ts",
                "video_config": project_root / "video.config.ts",
                "results_dir": project_root / "results",
                "report_dir": project_root / "report",
                "run_id": "run",
                "command": ["npx", "playwright", "test"],
                "command_text": "npx playwright test",
                "setup_resolution": self.resolution(),
            }
            failed_setup = {"uid": "setup-run", "status": "failed", "script_uid": "restore-database"}
            with (
                patch.object(app, "sync_script_asset", return_value={"asset_id": 1}),
                patch.object(app, "create_test_run"),
                patch.object(app, "create_test_job"),
                patch.object(app, "build_execution_env_metadata", return_value={}),
                patch.object(app, "get_script_test_relative_path", return_value="tests/模块/脚本.spec.ts"),
                patch.object(app, "create_run_result_for_script", return_value={"result_id": 1}),
                patch.object(app, "append_test_job_log"),
                patch.object(
                    app,
                    "execute_setup_profile",
                    side_effect=app.SetupPreparationError("准备失败：动作失败", failed_setup),
                ),
                patch.object(app, "update_run_result") as update_result,
                patch.object(app, "update_test_run") as update_run,
                patch.object(app, "register_execution_artifacts"),
                patch.object(app, "finish_test_job"),
                patch.object(app, "build_run_video_result", return_value={"video": None}),
                patch.object(app, "build_playwright_report_result", return_value={"report": None}),
                patch.object(app.subprocess, "Popen") as popen,
            ):
                events = []
                for chunk in app.stream_script_execution("模块", "脚本.spec.ts", context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        popen.assert_not_called()
        done = [payload for event, payload in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertIn("准备失败", done["error"])
        self.assertEqual(done["setup"]["uid"], "setup-run")
        self.assertEqual(update_result.call_args.kwargs["database_reset_status"], "failed")
        self.assertEqual(update_run.call_args.kwargs["completed_files"], 0)


class ScriptExecutionHistoryArtifactTests(unittest.TestCase):
    def test_recent_script_results_include_their_report_and_video(self):
        result_rows = [
            {
                "result_id": 17,
                "run_id": "run-history",
                "script_asset_id": 3,
                "status": "failed",
                "execution_mode": "batch_once",
                "command": "npx playwright test tests/模块/脚本.spec.ts",
                "stdout_tail": "historical output",
            }
        ]
        artifact_rows = [
            {
                "artifact_id": 21,
                "run_id": "run-history",
                "result_id": None,
                "artifact_type": "html_report",
                "path": "/project/playwright-report/run-history/index.html",
                "relative_path": "playwright-report/run-history/index.html",
                "url": "/api/playwright-reports/playwright-report/run-history/index.html",
            },
            {
                "artifact_id": 22,
                "run_id": "run-history",
                "result_id": 17,
                "artifact_type": "video",
                "path": "/project/test-results/run-history/video.webm",
                "relative_path": "test-results/run-history/video.webm",
                "url": "/api/run-videos/test-results/run-history/video.webm",
            },
        ]

        class Cursor:
            def __init__(self):
                self.result_sets = [result_rows, artifact_rows]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, *_args):
                return None

            def fetchall(self):
                return self.result_sets.pop(0)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

        with (
            patch.object(app, "get_platform_database_config", return_value={"enabled": True}),
            patch.object(app, "ensure_platform_database_schema"),
            patch.object(app, "get_test_run_results_table", return_value="test_run_results"),
            patch.object(app, "get_test_runs_table", return_value="test_runs"),
            patch.object(app, "get_test_run_artifacts_table", return_value="test_run_artifacts"),
            patch.object(app, "get_current_project_id", return_value=9),
            patch.object(app, "get_project_root", return_value=Path("/project")),
            patch.object(app, "platform_mysql_connection", return_value=Connection()),
        ):
            results = app.list_recent_script_results(3)

        serialized = app.serialize_run_result(results[0])
        self.assertEqual(serialized["run_id"], "run-history")
        self.assertEqual(serialized["command"], result_rows[0]["command"])
        self.assertEqual(serialized["report"]["artifact_id"], 21)
        self.assertEqual(serialized["video"]["artifact_id"], 22)


class MergedReportTraceTests(unittest.TestCase):
    def test_blob_inputs_are_outside_html_report_output(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            with patch.object(app, "get_database_baseline_config", return_value={"enabled": False}):
                artifacts = app.create_video_override_config(project_root, reporter_mode="blob")

            self.assertEqual(artifacts["blob_report_dir"].parent, artifacts["results_dir"])
            self.assertNotIn(artifacts["report_dir"], artifacts["blob_report_dir"].parents)


class ModuleSetupExecutionTests(unittest.TestCase):
    @staticmethod
    def fixture(project_root):
        filenames = ["脚本1.spec.ts", "脚本2.spec.ts", "脚本3.spec.ts"]
        script = {
            "uid": "module-setup",
            "name": "模块准备脚本",
            "script_content": "echo restore",
            "working_directory": "",
            "environment_overrides": {},
            "timeout_seconds": 30,
            "concurrency_key": "",
            "enabled": True,
        }
        resolution = {
            "binding": {
                "uid": "project-binding",
                "script_uid": script["uid"],
                "scope_type": "project",
                "scope_key": "default",
            },
            "target": {"scope_type": "project", "scope_key": "default"},
            "script": script,
            "profile": script,
        }
        context = {
            "execution_mode": app.EXECUTION_MODE_SERIAL_PER_FILE,
            "project_root": project_root,
            "video_config": project_root / "video.config.ts",
            "blob_report_dir": project_root / "blob-report",
            "results_dir": project_root / "test-results",
            "report_dir": project_root / "report",
            "json_report_file": project_root / "results.json",
            "run_id": "module-execution-with-setup",
            "command": ["npx"],
            "command_text": "npx playwright test",
            "merge_config": None,
            "merge_command": ["npx", "playwright", "merge-reports"],
            "merge_command_text": "npx playwright merge-reports",
            "setup_resolution": resolution,
        }
        return filenames, resolution, context

    @staticmethod
    def runtime_patches(project_root, resolution, timeline, process_error=None):
        class SuccessfulProcess:
            stdout = []

            @staticmethod
            def wait():
                return 0

        class SuccessfulMerge:
            returncode = 0
            stdout = b""
            stderr = b""

        def start_process(*_args, **kwargs):
            timeline.append(("playwright", Path(kwargs["env"]["TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE"]).stem))
            if process_error:
                raise process_error
            output_file = Path(kwargs["env"]["TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE"])
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"blob")
            return SuccessfulProcess()

        def persist_result(result_id, **updates):
            timeline.append(
                (
                    "persist",
                    result_id,
                    updates.get("status"),
                    updates.get("database_reset_status"),
                )
            )

        return (
            patch.object(app, "create_test_run"),
            patch.object(app, "create_test_job"),
            patch.object(app, "build_execution_env_metadata", return_value={}),
            patch.object(
                app,
                "create_run_result_for_script",
                side_effect=lambda _run_id, order_index, *_args, **_kwargs: {"result_id": order_index},
            ),
            patch.object(app, "append_test_job_log"),
            patch.object(app, "get_current_project", return_value={"project_key": "default"}),
            patch.object(app, "get_script_module_dir", return_value=project_root / "tests" / "模块"),
            patch.object(
                app,
                "get_script_test_relative_path",
                side_effect=lambda module_name, filename: f"tests/{module_name}/{filename}",
            ),
            patch.object(app, "resolve_setup_profile", return_value=resolution),
            patch.object(app, "prepare_database_baseline_for_test"),
            patch.object(app, "build_playwright_test_command", return_value=(["npx"], "npx playwright test")),
            patch.object(app, "get_playwright_execution_env", return_value={}),
            patch.object(
                app,
                "parse_playwright_json_script_results",
                side_effect=lambda _path, _module, names, _fallback: {names[0]: "succeeded"},
            ),
            patch.object(app, "update_run_result", side_effect=persist_result),
            patch.object(app, "register_script_video_artifact"),
            patch.object(app, "update_test_run"),
            patch.object(app, "register_execution_artifacts"),
            patch.object(app, "finish_test_job"),
            patch.object(app, "build_playwright_report_result", return_value={"report": "report"}),
            patch.object(app.subprocess, "Popen", side_effect=start_process),
            patch.object(app.subprocess, "run", return_value=SuccessfulMerge()),
        )

    def test_serial_module_resolves_and_runs_setup_before_each_file_until_failure(self):
        timeline = []
        setup_calls = 0

        def run_setup(_resolution, **kwargs):
            nonlocal setup_calls
            setup_calls += 1
            target_key = kwargs["target_override"]["scope_key"]
            timeline.append(("setup", target_key))
            if setup_calls == 2:
                raise app.SetupPreparationError(
                    "准备失败：恢复数据库失败",
                    {"uid": "failed-setup", "status": "failed", "target_key": target_key},
                )
            return {"uid": "successful-setup", "status": "succeeded"}

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            filenames, resolution, context = self.fixture(project_root)
            patches = self.runtime_patches(project_root, resolution, timeline)
            with ExitStack() as stack:
                runtime_mocks = [stack.enter_context(item) for item in patches]
                execute_setup = stack.enter_context(
                    patch.object(app, "execute_setup_profile", side_effect=run_setup)
                )
                events = []
                for chunk in app.stream_module_script_execution("模块", filenames, context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertEqual(execute_setup.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in runtime_mocks[8].call_args_list],
            [
                [
                    {"scope_type": "script", "scope_key": "模块/脚本1.spec.ts"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
                [
                    {"scope_type": "script", "scope_key": "模块/脚本2.spec.ts"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
            ],
        )
        self.assertEqual(
            timeline,
            [
                ("setup", "模块/脚本1.spec.ts"),
                ("playwright", "part-001"),
                ("persist", 1, "succeeded", "succeeded"),
                ("setup", "模块/脚本2.spec.ts"),
                ("persist", 2, "failed", "failed"),
                ("persist", 3, "interrupted", None),
            ],
        )
        self.assertEqual(runtime_mocks[15].call_args.kwargs["completed_files"], 1)
        runtime_mocks[9].assert_not_called()

    def test_serial_module_process_start_failure_does_not_mark_unattempted_resets_succeeded(self):
        timeline = []

        def run_setup(_resolution, **kwargs):
            timeline.append(("setup", kwargs["target_override"]["scope_key"]))
            return {"uid": "successful-setup", "status": "succeeded"}

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            filenames, resolution, context = self.fixture(project_root)
            patches = self.runtime_patches(
                project_root,
                resolution,
                timeline,
                process_error=FileNotFoundError("npx"),
            )
            with ExitStack() as stack:
                runtime_mocks = [stack.enter_context(item) for item in patches]
                stack.enter_context(patch.object(app, "execute_setup_profile", side_effect=run_setup))
                events = []
                for chunk in app.stream_module_script_execution("模块", filenames, context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertEqual(
            done["script_results"],
            {
                "脚本1.spec.ts": "failed",
                "脚本2.spec.ts": "interrupted",
                "脚本3.spec.ts": "interrupted",
            },
        )
        self.assertEqual(
            [item for item in timeline if item[0] == "persist"],
            [
                ("persist", 1, "failed", "succeeded"),
                ("persist", 2, "interrupted", None),
                ("persist", 3, "interrupted", None),
            ],
        )
        self.assertEqual(runtime_mocks[15].call_args.kwargs["completed_files"], 0)


class TestSuiteExecutionResultTests(unittest.TestCase):
    @staticmethod
    def serial_suite_fixture(project_root, item_count=2):
        items = [
            {
                "module_name": f"模块{index}",
                "filename": f"脚本{index}.spec.ts",
                "key": f"模块{index}/脚本{index}.spec.ts",
                "relative_path": f"tests/模块{index}/脚本{index}.spec.ts",
            }
            for index in range(1, item_count + 1)
        ]
        resolution = {
            "binding": {
                "uid": "project-binding",
                "script_uid": "regression-setup-script",
                "scope_type": "project",
                "scope_key": "default",
            },
            "target": {"scope_type": "project", "scope_key": "default"},
            "script": {
                "uid": "regression-setup-script",
                "name": "回归测试标准准备",
                "script_content": "echo restore",
                "working_directory": "",
                "environment_overrides": {},
                "timeout_seconds": 30,
                "concurrency_key": "",
                "enabled": True,
            },
        }
        resolution["profile"] = resolution["script"]
        context = {
            "execution_mode": app.EXECUTION_MODE_SERIAL_PER_FILE,
            "items": items,
            "project_root": project_root,
            "video_config": project_root / "video.config.ts",
            "blob_report_dir": project_root / "blob-report",
            "results_dir": project_root / "test-results",
            "report_dir": project_root / "report",
            "json_report_file": project_root / "results.json",
            "relative_path_keys": {item["relative_path"]: item["key"] for item in items},
            "run_id": "execution-with-setup",
            "command": ["npx"],
            "command_text": "npx playwright test",
            "merge_config": None,
            "merge_command": ["npx", "playwright", "merge-reports"],
            "merge_command_text": "npx playwright merge-reports",
            "setup_resolution": resolution,
        }
        return items, resolution, context

    @staticmethod
    def serial_suite_runtime_patches(resolution, timeline, update_side_effect=None):
        class SuccessfulProcess:
            stdout = []

            @staticmethod
            def wait():
                return 0

        class SuccessfulMerge:
            returncode = 0
            stdout = b""
            stderr = b""

        def start_process(*_args, **kwargs):
            output_file = Path(kwargs["env"]["TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE"])
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"blob")
            timeline.append(("playwright", output_file.stem))
            return SuccessfulProcess()

        return (
            patch.object(app, "create_test_run"),
            patch.object(app, "create_test_job"),
            patch.object(app, "build_execution_env_metadata", return_value={}),
            patch.object(
                app,
                "create_run_result_for_script",
                side_effect=lambda _run_id, order_index, *_args, **_kwargs: {"result_id": order_index},
            ),
            patch.object(app, "append_test_job_log"),
            patch.object(app, "get_current_project", return_value={"project_key": "default"}),
            patch.object(app, "resolve_setup_profile", return_value=resolution),
            patch.object(app, "prepare_database_baseline_for_test"),
            patch.object(app, "build_playwright_test_command", return_value=(["npx"], "npx playwright test")),
            patch.object(app, "get_playwright_execution_env", return_value={}),
            patch.object(
                app,
                "parse_playwright_json_relative_script_results",
                side_effect=lambda _path, relative_keys, _fallback: {
                    next(iter(relative_keys.values())): "succeeded"
                },
            ),
            patch.object(app, "update_run_result", side_effect=update_side_effect),
            patch.object(app, "register_script_video_artifact"),
            patch.object(app, "update_test_run"),
            patch.object(app, "register_execution_artifacts"),
            patch.object(app, "finish_test_job"),
            patch.object(app, "build_playwright_report_result", return_value={"report": "report"}),
            patch.object(app.subprocess, "Popen", side_effect=start_process),
            patch.object(app.subprocess, "run", return_value=SuccessfulMerge()),
        )

    def run_batch_suite(self, project_root, setup_failure=None):
        class SuccessfulProcess:
            stdout = []

            @staticmethod
            def wait():
                return 0

        items, resolution, context = self.serial_suite_fixture(project_root)
        context["execution_mode"] = app.EXECUTION_MODE_BATCH
        with ExitStack() as stack:
            stack.enter_context(patch.object(app, "create_test_run"))
            stack.enter_context(patch.object(app, "create_test_job"))
            stack.enter_context(patch.object(app, "build_execution_env_metadata", return_value={}))
            stack.enter_context(
                patch.object(
                    app,
                    "create_run_result_for_script",
                    side_effect=lambda _run_id, order_index, *_args, **_kwargs: {"result_id": order_index},
                )
            )
            stack.enter_context(patch.object(app, "append_test_job_log"))
            if setup_failure:
                execute_setup = stack.enter_context(
                    patch.object(app, "execute_setup_profile", side_effect=setup_failure)
                )
            else:
                execute_setup = stack.enter_context(
                    patch.object(
                        app,
                        "execute_setup_profile",
                        return_value={"uid": "batch-setup", "status": "succeeded"},
                    )
                )
            stack.enter_context(patch.object(app, "get_playwright_execution_env", return_value={}))
            stack.enter_context(
                patch.object(
                    app,
                    "parse_playwright_json_relative_script_results",
                    side_effect=lambda _path, relative_keys, _fallback: {
                        key: "succeeded" for key in relative_keys.values()
                    },
                )
            )
            update_result = stack.enter_context(patch.object(app, "update_run_result"))
            update_run = stack.enter_context(patch.object(app, "update_test_run"))
            stack.enter_context(patch.object(app, "register_execution_artifacts"))
            stack.enter_context(patch.object(app, "finish_test_job"))
            stack.enter_context(
                patch.object(app, "build_playwright_report_result", return_value={"report": "report"})
            )
            popen = stack.enter_context(
                patch.object(app.subprocess, "Popen", return_value=SuccessfulProcess())
            )
            events = []
            for chunk in app.stream_test_suite_execution("suite", "回归测试集", items, context):
                events.extend(app.parse_sse_text_blocks(chunk))
        return items, events, execute_setup, popen, update_result, update_run

    def test_serial_suite_runs_bound_setup_before_each_script(self):
        timeline = []

        def run_setup(_resolution, **kwargs):
            timeline.append(("setup", kwargs["target_override"]["scope_key"]))
            return {"uid": f"setup-{len(timeline)}", "status": "succeeded"}

        def persist_result(result_id, **updates):
            timeline.append(
                (
                    "persist",
                    result_id,
                    updates.get("status"),
                    updates.get("database_reset_status"),
                )
            )

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            items, resolution, context = self.serial_suite_fixture(project_root)
            patches = self.serial_suite_runtime_patches(resolution, timeline, persist_result)
            with ExitStack() as stack:
                runtime_mocks = [stack.enter_context(item) for item in patches]
                execute_setup = stack.enter_context(
                    patch.object(app, "execute_setup_profile", side_effect=run_setup)
                )
                events = []
                for chunk in app.stream_test_suite_execution("suite", "回归测试集", items, context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(execute_setup.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in runtime_mocks[6].call_args_list],
            [
                [
                    {"scope_type": "script", "scope_key": "模块1/脚本1.spec.ts"},
                    {"scope_type": "test_suite", "scope_key": "suite"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
                [
                    {"scope_type": "script", "scope_key": "模块2/脚本2.spec.ts"},
                    {"scope_type": "test_suite", "scope_key": "suite"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
            ],
        )
        self.assertEqual(
            timeline,
            [
                ("setup", "模块1/脚本1.spec.ts"),
                ("playwright", "part-001"),
                ("persist", 1, "succeeded", "succeeded"),
                ("setup", "模块2/脚本2.spec.ts"),
                ("playwright", "part-002"),
                ("persist", 2, "succeeded", "succeeded"),
            ],
        )
        runtime_mocks[7].assert_not_called()

    def test_serial_suite_setup_failure_marks_only_current_reset_failed(self):
        timeline = []
        setup_calls = 0

        def run_setup(_resolution, **kwargs):
            nonlocal setup_calls
            setup_calls += 1
            target_key = kwargs["target_override"]["scope_key"]
            timeline.append(("setup", target_key))
            if setup_calls == 2:
                raise app.SetupPreparationError(
                    "准备失败：恢复数据库失败",
                    {"uid": "failed-setup", "status": "failed", "target_key": target_key},
                )
            return {"uid": "successful-setup", "status": "succeeded"}

        def persist_result(result_id, **updates):
            timeline.append(
                (
                    "persist",
                    result_id,
                    updates.get("status"),
                    updates.get("database_reset_status"),
                )
            )

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            items, resolution, context = self.serial_suite_fixture(project_root, item_count=3)
            patches = self.serial_suite_runtime_patches(resolution, timeline, persist_result)
            with ExitStack() as stack:
                runtime_mocks = [stack.enter_context(item) for item in patches]
                execute_setup = stack.enter_context(
                    patch.object(app, "execute_setup_profile", side_effect=run_setup)
                )
                events = []
                for chunk in app.stream_test_suite_execution("suite", "回归测试集", items, context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertIn("准备失败", done["error"])
        self.assertEqual(execute_setup.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in runtime_mocks[6].call_args_list],
            [
                [
                    {"scope_type": "script", "scope_key": "模块1/脚本1.spec.ts"},
                    {"scope_type": "test_suite", "scope_key": "suite"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
                [
                    {"scope_type": "script", "scope_key": "模块2/脚本2.spec.ts"},
                    {"scope_type": "test_suite", "scope_key": "suite"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
            ],
        )
        self.assertEqual(
            timeline,
            [
                ("setup", "模块1/脚本1.spec.ts"),
                ("playwright", "part-001"),
                ("persist", 1, "succeeded", "succeeded"),
                ("setup", "模块2/脚本2.spec.ts"),
                ("persist", 2, "failed", "failed"),
                ("persist", 3, "interrupted", None),
            ],
        )
        self.assertEqual(done["completed_files"], 1)
        final_run_update = runtime_mocks[13].call_args_list[-1]
        self.assertEqual(final_run_update.kwargs["completed_files"], 1)
        runtime_mocks[7].assert_not_called()

    def test_batch_suite_runs_one_shared_setup_and_never_uses_a_script_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "get_current_project", return_value={"project_key": "default"}
        ):
            items, events, execute_setup, popen, update_result, update_run = self.run_batch_suite(
                Path(directory)
            )
            self.assertEqual(
                app.build_setup_targets(suite_uid="suite", items=None),
                [
                    {"scope_type": "test_suite", "scope_key": "suite"},
                    {"scope_type": "project", "scope_key": "default"},
                ],
            )
            self.assertEqual(
                app.build_setup_targets(),
                [{"scope_type": "project", "scope_key": "default"}],
            )

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "succeeded")
        execute_setup.assert_called_once()
        popen.assert_called_once()
        self.assertEqual(
            [call.kwargs.get("database_reset_status") for call in update_result.call_args_list],
            ["succeeded"] * len(items),
        )
        self.assertEqual(update_run.call_args.kwargs["completed_files"], len(items))

    def test_batch_suite_setup_failure_fails_all_results_without_starting_playwright(self):
        failed_setup = {"uid": "setup-failed", "status": "failed", "script_uid": "regression-setup-script"}
        failure = app.SetupPreparationError("准备失败：恢复数据库失败", failed_setup)
        with tempfile.TemporaryDirectory() as directory:
            items, events, execute_setup, popen, update_result, update_run = self.run_batch_suite(
                Path(directory), setup_failure=failure
            )

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["completed_files"], 0)
        execute_setup.assert_called_once()
        popen.assert_not_called()
        self.assertEqual(
            [
                (call.kwargs.get("status"), call.kwargs.get("database_reset_status"))
                for call in update_result.call_args_list
            ],
            [("failed", "failed")] * len(items),
        )
        self.assertEqual(update_run.call_args.kwargs["completed_files"], 0)

    def test_historical_execution_videos_are_recovered_from_json_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            script_file = project_root / "tests" / "模块A" / "通过.spec.ts"
            video_file = project_root / "playwright-report" / "resources" / "video.webm"
            json_report_file = project_root / "playwright-report" / "report.json"
            script_file.parent.mkdir(parents=True)
            video_file.parent.mkdir(parents=True)
            script_file.write_text("test('通过', () => {});", encoding="utf-8")
            video_file.write_bytes(b"video")
            json_report_file.write_text(
                app.json.dumps(
                    {
                        "suites": [
                            {
                                "file": "tests/模块A/通过.spec.ts",
                                "specs": [
                                    {
                                        "tests": [
                                            {
                                                "results": [
                                                    {
                                                        "attachments": [
                                                            {
                                                                "name": "video",
                                                                "contentType": "video/webm",
                                                                "path": str(video_file),
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(app, "get_project_root", return_value=project_root):
                videos = app.collect_test_suite_execution_video_fallbacks(
                    [
                        {
                            "run_id": "historical-run",
                            "result_id": 42,
                            "script_path": str(script_file),
                        }
                    ],
                    {"historical-run": str(json_report_file)},
                )

        self.assertEqual(Path(videos[42]["path"]), video_file.resolve(strict=False))
        self.assertEqual(videos[42]["relative_path"], "playwright-report/resources/video.webm")
        self.assertEqual(videos[42]["url"], "/api/run-videos/playwright-report/resources/video.webm")

    def test_historical_serial_execution_videos_are_recovered_from_part_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            script_file = project_root / "tests" / "模块A" / "通过.spec.ts"
            video_file = (
                project_root
                / "test-results"
                / app.RUN_ARTIFACTS_DIR_NAME
                / "historical-run"
                / "part-001"
                / "chromium"
                / "case"
                / "video.webm"
            )
            script_file.parent.mkdir(parents=True)
            video_file.parent.mkdir(parents=True)
            script_file.write_text("test('通过', () => {});", encoding="utf-8")
            video_file.write_bytes(b"video")

            with patch.object(app, "get_project_root", return_value=project_root):
                videos = app.collect_test_suite_execution_video_fallbacks(
                    [
                        {
                            "run_id": "historical-run",
                            "result_id": 42,
                            "order_index": 1,
                            "script_path": str(script_file),
                        }
                    ],
                    {},
                )

        self.assertEqual(Path(videos[42]["path"]), video_file.resolve(strict=False))
        self.assertEqual(
            videos[42]["relative_path"],
            "test-results/test-plan-viewer-runs/historical-run/part-001/chromium/case/video.webm",
        )
        self.assertEqual(
            videos[42]["url"],
            "/api/run-videos/test-results/test-plan-viewer-runs/historical-run/part-001/chromium/case/video.webm",
        )

    def test_screenshot_and_trace_attachments_are_registered_as_execution_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            screenshot = project_root / "test-results" / "failure.png"
            trace = project_root / "test-results" / "trace.zip"
            report = project_root / "playwright-report" / "report.json"
            screenshot.parent.mkdir(parents=True)
            report.parent.mkdir(parents=True)
            screenshot.write_bytes(b"png")
            trace.write_bytes(b"zip")
            report.write_text(
                json.dumps(
                    {
                        "suites": [
                            {
                                "file": "tests/模块A/失败.spec.ts",
                                "specs": [
                                    {
                                        "tests": [
                                            {
                                                "results": [
                                                    {
                                                        "attachments": [
                                                            {"name": "screenshot", "contentType": "image/png", "path": str(screenshot)},
                                                            {"name": "trace", "contentType": "application/zip", "path": str(trace)},
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(app, "get_project_root", return_value=project_root):
                artifacts = app.collect_playwright_evidence_artifacts_by_key(
                    report,
                    {"tests/模块A/失败.spec.ts": "失败.spec.ts"},
                )

        self.assertEqual({item["artifact_type"] for item in artifacts}, {"screenshot", "trace"})
        self.assertEqual({item["key"] for item in artifacts}, {"失败.spec.ts"})

    def test_finalize_error_results_preserves_completed_statuses(self):
        results = app.finalize_script_results_after_error(
            ["模块A/通过.spec.ts", "模块B/失败.spec.ts", "模块C/未完成.spec.ts"],
            {
                "模块A/通过.spec.ts": "succeeded",
                "模块B/失败.spec.ts": "failed",
                "模块C/未完成.spec.ts": "running",
            },
            unresolved_status="interrupted",
        )

        self.assertEqual(
            results,
            {
                "模块A/通过.spec.ts": "succeeded",
                "模块B/失败.spec.ts": "failed",
                "模块C/未完成.spec.ts": "interrupted",
            },
        )

    def test_stream_error_after_pass_preserves_passed_result(self):
        class PassedProcess:
            stdout = [b"  1 passed (1.0s)\n"]

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            items = [
                {
                    "module_name": "模块A",
                    "filename": "通过.spec.ts",
                    "key": "模块A/通过.spec.ts",
                    "relative_path": "tests/模块A/通过.spec.ts",
                },
                {
                    "module_name": "模块B",
                    "filename": "启动失败.spec.ts",
                    "key": "模块B/启动失败.spec.ts",
                    "relative_path": "tests/模块B/启动失败.spec.ts",
                },
            ]
            context = {
                "execution_mode": app.EXECUTION_MODE_SERIAL_PER_FILE,
                "items": items,
                "project_root": project_root,
                "video_config": project_root / "video.config.ts",
                "blob_report_dir": project_root / "blob-report",
                "results_dir": project_root / "test-results",
                "report_dir": project_root / "report",
                "json_report_file": project_root / "results.json",
                "run_id": "execution-regression",
                "command": ["npx"],
                "command_text": "npx playwright test",
                "merge_config": None,
                "merge_command": [],
                "merge_command_text": "merge",
            }
            popen_calls = 0

            def start_process(*_args, **_kwargs):
                nonlocal popen_calls
                popen_calls += 1
                if popen_calls == 1:
                    return PassedProcess()
                raise OSError("第二个脚本启动失败")

            with (
                patch.object(app, "create_test_run"),
                patch.object(app, "create_test_job"),
                patch.object(app, "build_execution_env_metadata", return_value={}),
                patch.object(
                    app,
                    "create_run_result_for_script",
                    side_effect=lambda _run_id, order_index, *_args, **_kwargs: {"result_id": order_index},
                ),
                patch.object(app, "append_test_job_log"),
                patch.object(app, "prepare_database_baseline_for_test", return_value=[]),
                patch.object(app, "build_playwright_test_command", return_value=(["npx"], "npx playwright test")),
                patch.object(app, "get_playwright_execution_env", return_value={}),
                patch.object(
                    app,
                    "parse_playwright_json_relative_script_results",
                    side_effect=lambda _path, relative_keys, _fallback: {next(iter(relative_keys.values())): "succeeded"},
                ),
                patch.object(app, "update_run_result") as update_result,
                patch.object(app, "register_script_video_artifact"),
                patch.object(app, "update_test_run"),
                patch.object(app, "register_execution_artifacts", side_effect=RuntimeError("产物登记失败")),
                patch.object(app, "finish_test_job"),
                patch.object(
                    app,
                    "build_playwright_report_result",
                    return_value={"report": None, "report_error": "未生成报告"},
                ),
                patch.object(app.subprocess, "Popen", side_effect=start_process),
            ):
                events = []
                for chunk in app.stream_test_suite_execution("suite", "回归测试集", items, context):
                    events.extend(app.parse_sse_text_blocks(chunk))

        done = [data for event, data in events if event == "done"][-1]
        self.assertEqual(done["status"], "failed")
        self.assertEqual(
            done["script_results"],
            {
                "模块A/通过.spec.ts": "succeeded",
                "模块B/启动失败.spec.ts": "failed",
            },
        )
        self.assertTrue(any("产物登记失败" in message for message in done["finalization_errors"]))
        persisted_statuses = [(call.args[0], call.kwargs.get("status")) for call in update_result.call_args_list]
        self.assertIn((1, "succeeded"), persisted_statuses)
        self.assertIn((2, "failed"), persisted_statuses)
        self.assertNotIn((1, "failed"), persisted_statuses)

    def test_frontend_stream_errors_preserve_completed_results(self):
        source = read_platform_javascript()

        self.assertIn(
            'mergeTestSuiteScriptResults(previousResult.script_results, data.script_results)',
            source,
        )
        self.assertIn(
            'finalizeTestSuiteScriptResults(suite.items, current.script_results, "interrupted")',
            source,
        )
        self.assertNotIn(
            'record.script_results?.[scriptKey] || record.status || "unknown"',
            source,
        )

    def test_agent_execution_stage_reuses_test_suite_result_panel(self):
        source = read_platform_javascript()
        agent_source = (app.APP_DIR / "static/js/features/agent.js").read_text(encoding="utf-8")
        template = render_index_template()

        self.assertIn(
            'class="test-suite-execution-result-panel agent-execution-result-panel hidden"',
            template,
        )
        self.assertIn('data-agent-id="executionResultTableBody"', template)
        self.assertIn("function renderExecutionResultPanel(record, view, options = {})", source)
        self.assertIn("function loadAgentExecutionResult(options = {})", source)
        self.assertIn("/execution-records?limit=20", source)
        self.assertIn('state.activeStepKey === "run_suite"', source)
        self.assertIn("function normalizeAgentExecutionResultStatus(status)", agent_source)
        self.assertIn("normalizeAgentExecutionResultStatus(scriptResults[item.key])", agent_source)


class PlanCoveragePromptTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_profiles_are_templates_and_custom_text_wins(self):
        with patch.object(app, "get_plan_generation_config", return_value={"default_coverage_profile": "core"}):
            value = app.normalize_plan_generation_request(
                {
                    "coverage_profile": "comprehensive",
                    "coverage_prompt": "只覆盖反向用例。",
                    "prompt_customized": True,
                }
            )

        self.assertEqual(value["coverage_profile"], "comprehensive")
        self.assertEqual(value["coverage_prompt"], "只覆盖反向用例。")
        self.assertTrue(value["prompt_customized"])

    def test_editable_prompt_contains_replaceable_policy_block(self):
        prompt = app.compose_editable_plan_prompt("模块上下文", "只覆盖反向用例。")

        self.assertIn(app.COVERAGE_POLICY_START, prompt)
        self.assertIn("只覆盖反向用例。", prompt)
        self.assertIn(app.COVERAGE_POLICY_END, prompt)

    def test_execution_prompt_does_not_reapply_selected_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(app, "get_workspace_relative_path", return_value="specs/登录/反向测试.md"),
            patch.object(app, "get_database_baseline_config", return_value={"enabled": False}),
        ):
            prompt = app.build_generation_prompt("只覆盖反向用例。", Path(directory) / "反向测试.md")

        self.assertIn("只覆盖反向用例。", prompt)
        self.assertNotIn("全面回归", prompt)
        self.assertNotIn("正向案例中的主要流程", prompt)
        self.assertIn("planner_save_plan", prompt)

    def test_requirement_module_prompt_is_coverage_neutral(self):
        with (
            patch.object(app, "get_seed_script_relative_path", return_value="tests/seed/seed.spec.ts"),
        ):
            prompt = app.build_planner_prompt_from_requirement_module(
                {
                    "module_name": "审批管理",
                    "test_points": ["审批通过", "驳回原因必填"],
                    "matched_inventory": {},
                    "write_risk": True,
                },
                requirement={"title": "审批需求"},
            )

        self.assertIn("驳回原因必填", prompt)
        self.assertNotIn("只生成正向", prompt)
        self.assertNotIn("最多", prompt)

    def test_more_than_absolute_case_limit_fails_without_truncation(self):
        cases = [{"title": f"用例{index}", "filename": f"用例{index}.md", "steps": []} for index in range(26)]
        with self.assertRaisesRegex(ValueError, "绝对上限 25"):
            app.split_case_index_cases("登录模块", "索引.md", cases)

    def test_defaults_api_returns_all_three_editable_templates(self):
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_plan_generation_config", return_value={"default_coverage_profile": "standard"}),
            patch.object(app, "get_plan_target_path", return_value=Path("/tmp/specs/<模块名>/<测试计划名>.md")),
            patch.object(
                app,
                "get_current_target_system_config",
                return_value={"base_url": "http://example.test", "login_path": "/login"},
            ),
            patch.object(app, "get_seed_script_relative_path", return_value="tests/seed/seed.spec.ts"),
        ):
            response = self.client.get("/api/plan-generation-defaults")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["default_coverage_profile"], "standard")
        self.assertEqual([item["key"] for item in payload["coverage_profiles"]], ["core", "standard", "comprehensive"])
        self.assertEqual([item["suggested_max_cases"] for item in payload["coverage_profiles"]], [10, 15, 25])

    def test_legacy_positive_notice_removal_is_exact(self):
        legacy = (
            "计划范围限制：只关注正向案例中的主要流程，优先覆盖用户最常用、业务价值最高的成功路径；不要主动扩展异常、"
            "边界、兼容性、权限绕过或低频分支场景。模块计划最多包含 10 个测试用例，不足时不要为了凑数补充重复或次要用例。"
        )
        manual = "人工要求：只关注正向案例中的主要流程，但必须覆盖审批人角色。"

        cleaned = app.strip_legacy_coverage_notices(f"模块基础\n{legacy}\n{manual}")

        self.assertNotIn(legacy, cleaned)
        self.assertIn(manual, cleaned)

    def test_legacy_agent_resume_finds_the_existing_execution_prompt(self):
        with (
            patch.object(
                app,
                "list_agent_events",
                return_value=[
                    {
                        "step_key": "generate_plans",
                        "job_id": "planner-old",
                        "payload_json": '{"module_name":"审批管理"}',
                    }
                ],
            ),
            patch.object(app, "get_test_job", return_value={"job_id": "planner-old", "prompt": "旧任务实际执行 Prompt"}),
        ):
            job = app.find_legacy_agent_plan_job("agent-old", "审批管理")

        self.assertEqual(job["prompt"], "旧任务实际执行 Prompt")

    def test_frontend_does_not_resplit_server_finalized_multiple_plan(self):
        source = read_platform_javascript()

        self.assertIn("let filenames = generatedPlanFilenames(result);", source)
        self.assertIn("if (listed.includes(planFilename))", source)
        self.assertIn(
            "filenames = generatedPlanFilenames(await splitGeneratedPlanCases(moduleName, planFilename));",
            source,
        )
        self.assertIn("await selectPlan(moduleName, filenames[0], true);", source)
        self.assertNotIn("await splitGeneratedPlanCases(resultModule, resultPlanFilename);", source)


class AgentTaskToolbarTests(unittest.TestCase):
    def test_task_actions_keep_the_approved_fixed_order(self):
        template = render_index_template()

        toolbar_start = template.index('class="agent-toolbar"')
        new_task = template.index('data-agent-id="newRunButton"', toolbar_start)
        resume_task = template.index('data-agent-id="resumeButton"', toolbar_start)
        stop_task = template.index('data-agent-id="cancelButton"', toolbar_start)

        self.assertLess(new_task, resume_task)
        self.assertLess(resume_task, stop_task)

    def test_task_action_availability_follows_single_run_state(self):
        source = read_platform_javascript()

        self.assertIn("elements.newRunButton.disabled = Boolean(activeRun || pausedRun || activeRetryRun);", source)
        self.assertIn(
            'const canResume = Boolean(run && isResumableStatus(run.status) && !activeRun && !pausedRun && !activeRetryRun);',
            source,
        )
        self.assertIn(
            'const canStop = Boolean(run && ["queued", "running", "awaiting_script_action"].includes(run.status));',
            source,
        )
        self.assertIn('elements.cancelButtonLabel.textContent = isCancelling ? "正在停止…" : "停止任务";', source)

    def test_task_dropdown_loads_all_runs_and_has_no_legacy_more_toggle(self):
        frontend_source = read_platform_javascript()
        template = render_index_template()

        limit_parameter = inspect.signature(app.list_agent_run_rows).parameters["limit"]
        self.assertIsNone(limit_parameter.default)
        self.assertIn('placeholder="搜索任务"', template)
        self.assertNotIn('data-agent-id="historyToggle"', template)
        self.assertNotIn("historyExpanded", frontend_source)

    def test_new_task_modal_form_uses_the_full_dialog_width(self):
        stylesheet = read_platform_stylesheets()

        modal_form_rule = stylesheet.index(".agent-panel .task-dialog .launch-form {")
        modal_form_rule_end = stylesheet.index("}", modal_form_rule)
        modal_form_styles = stylesheet[modal_form_rule:modal_form_rule_end]

        self.assertIn("grid-template-columns: minmax(0, 1fr);", modal_form_styles)
        self.assertIn("justify-content: stretch;", modal_form_styles)


class AgentPlanResumeTests(unittest.TestCase):
    def test_resume_step_moves_back_to_plan_generation_when_plan_failures_remain(self):
        resume_output = {
            "plans": [{"module_name": "登录", "plan_filename": "登录成功.md"}],
            "failures": [{"module_uid": "module-b", "module_name": "账户流水", "error": "TLS error"}],
            "skipped": [],
        }
        with patch.object(app, "get_agent_plan_resume_output", return_value=resume_output):
            from_step = app.resolve_agent_resume_step("agent-1", "prepare_scripts")

        self.assertEqual(from_step, "generate_plans")

    def test_plan_resume_retries_only_failed_modules_and_merges_previous_plans(self):
        modules = [
            {"module_uid": "module-a", "module_name": "登录"},
            {"module_uid": "module-b", "module_name": "账户流水"},
        ]
        previous_plan = {"module_name": "登录", "plan_filename": "登录成功.md", "path": "/plans/登录成功.md"}
        retry_plan = {"module_name": "账户流水", "plan_filename": "流水查询.md", "path": "/plans/流水查询.md"}
        resume_output = {
            "plans": [previous_plan],
            "failures": [{"module_uid": "module-b", "module_name": "账户流水", "error": "TLS error"}],
            "skipped": [],
        }
        finished = {}

        def capture_finish(_run_id, _step_key, output_data=None, counts=None):
            finished["output"] = output_data
            finished["counts"] = counts

        with (
            patch.object(app, "get_agent_run_row", return_value={}),
            patch.object(app, "serialize_agent_run", return_value={"plan_generation": {}}),
            patch.object(app, "normalize_plan_generation_request", return_value={"coverage_profile": "standard"}),
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "start_agent_attempt", return_value={"attempt_id": "attempt-retry"}),
            patch.object(app, "finish_agent_attempt"),
            patch.object(app, "append_agent_artifact_progress"),
            patch.object(
                app,
                "agent_generate_plan_for_module",
                return_value={
                    "status": "succeeded",
                    "module_name": "账户流水",
                    "plan_filename": "流水查询.md",
                    "plans": [retry_plan],
                    "job_id": "planner-new",
                },
            ) as generate,
            patch.object(app, "agent_finish_step", side_effect=capture_finish),
        ):
            plans = app.agent_generate_plans("agent-1", {"id": 1}, modules, resume_output=resume_output)

        self.assertEqual([call.args[3]["module_uid"] for call in generate.call_args_list], ["module-b"])
        self.assertEqual(plans, [previous_plan, retry_plan])
        self.assertEqual(finished["output"]["failures"], [])
        self.assertEqual(finished["counts"], {"generated": 2, "failed": 0, "skipped": 0, "modules": 2})

    def test_plan_generation_stops_when_any_module_still_fails(self):
        modules = [
            {"module_uid": "module-a", "module_name": "登录"},
            {"module_uid": "module-b", "module_name": "账户流水"},
        ]
        generated_plan = {"module_name": "登录", "plan_filename": "登录成功.md"}

        def generate_module(_run_id, _step_key, _requirement, module):
            if module["module_uid"] == "module-b":
                raise app.AgentItemFailure("TLS error", job_id="planner-flow", error_type="agent")
            return {
                "status": "succeeded",
                "module_name": "登录",
                "plan_filename": "登录成功.md",
                "plans": [generated_plan],
                "job_id": "planner-login",
            }

        with (
            patch.object(app, "get_agent_run_row", return_value={}),
            patch.object(app, "serialize_agent_run", return_value={"plan_generation": {}}),
            patch.object(app, "normalize_plan_generation_request", return_value={"coverage_profile": "standard"}),
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-login"}, {"attempt_id": "attempt-flow"}],
            ),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "append_agent_artifact_progress"),
            patch.object(app, "agent_generate_plan_for_module", side_effect=generate_module),
            patch.object(app, "agent_finish_step") as finish,
            self.assertRaisesRegex(RuntimeError, "仍有 1 个模块计划生成失败"),
        ):
            app.agent_generate_plans("agent-1", {"id": 1}, modules)

        finish.assert_not_called()
        self.assertEqual([call.args[2] for call in finish_attempt.call_args_list], ["succeeded", "failed"])
        self.assertEqual(finish_attempt.call_args_list[-1].kwargs["job_id"], "planner-flow")


class AgentAttemptFlowTests(unittest.TestCase):
    def test_script_generation_success_persists_attempt_and_keeps_id_in_stage_output(self):
        plan = {
            "module_name": "登录",
            "plan_filename": "登录成功.md",
            "asset": {"asset_id": 10},
        }
        generated = {
            "module_name": "登录",
            "plan_filename": "登录成功.md",
            "filename": "登录成功.spec.ts",
            "path": "/tmp/登录成功.spec.ts",
            "job_id": "generator-success",
            "asset": {"asset_id": 20, "current_revision_id": 30, "from_plan_asset_id": 10},
        }

        with (
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "start_agent_attempt", return_value={"attempt_id": "attempt-success"}),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "append_agent_artifact_progress"),
            patch.object(app, "agent_generate_script_for_plan", return_value=generated),
            patch.object(app, "agent_finish_step"),
        ):
            scripts, failures = app.agent_generate_scripts("agent-1", [plan])

        self.assertEqual(failures, [])
        self.assertEqual(scripts[0]["attempt_id"], "attempt-success")
        self.assertEqual(finish_attempt.call_args.args[:3], ("agent-1", "attempt-success", "succeeded"))
        self.assertEqual(finish_attempt.call_args.kwargs["job_id"], "generator-success")
        self.assertEqual(finish_attempt.call_args.kwargs["outcome_type"], "generated")

    def test_all_script_generation_failures_are_returned_for_failure_checkpoint(self):
        plan = {"module_name": "登录", "plan_filename": "登录失败.md"}
        progress_calls = []

        def capture_progress(*args, **kwargs):
            progress_calls.append((args, kwargs))

        with (
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "start_agent_attempt", return_value={"attempt_id": "attempt-failed"}),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "append_agent_artifact_progress", side_effect=capture_progress),
            patch.object(
                app,
                "agent_generate_script_for_plan",
                side_effect=app.AgentItemFailure(
                    "OpenCode 请求超时",
                    job_id="generator-failed",
                    error_type="timeout",
                    partial_artifacts=["/tmp/登录失败.spec.ts"],
                ),
            ),
            patch.object(app, "agent_finish_step") as finish_step,
        ):
            scripts, failures = app.agent_generate_scripts(
                "agent-1",
                [plan],
            )

        self.assertEqual(scripts, [])
        self.assertEqual(len(failures), 1)
        failure = progress_calls[-1][1]["item"]
        self.assertEqual(failure["attempt_id"], "attempt-failed")
        self.assertEqual(failure["failure_id"], "attempt-failed")
        self.assertEqual(failure["job_id"], "generator-failed")
        self.assertEqual(failure["error_type"], "timeout")
        self.assertEqual(finish_attempt.call_args.args[:3], ("agent-1", "attempt-failed", "failed"))
        self.assertEqual(finish_attempt.call_args.kwargs["job_id"], "generator-failed")
        finish_step.assert_called_once()
        self.assertEqual(
            finish_step.call_args.args[2]["failures"],
            failures,
        )

    def test_attempt_serialization_distinguishes_success_and_failure(self):
        succeeded = app.serialize_agent_attempt(
            {
                "attempt_id": "attempt-ok",
                "status": "succeeded",
                "outcome_type": "passed",
                "input_snapshot_json": "{}",
                "output_summary_json": "{}",
                "artifact_refs_json": "[]",
                "finished_at": 20,
            }
        )
        failed = app.serialize_agent_attempt(
            {
                "attempt_id": "attempt-bad",
                "status": "failed",
                "error_message": "失败",
                "input_snapshot_json": "{}",
                "output_summary_json": "{}",
                "artifact_refs_json": "[]",
                "finished_at": 30,
            }
        )

        self.assertEqual(succeeded["outcome_type"], "passed")
        self.assertIsNone(succeeded["failed_at"])
        self.assertEqual(failed["error"], "失败")
        self.assertEqual(failed["failed_at"], 30)

    def test_execution_attempts_persist_passed_and_failed_results(self):
        scripts = [
            {"module_name": "模块A", "filename": "通过.spec.ts", "asset": {"asset_id": 1, "current_revision_id": 11}},
            {"module_name": "模块B", "filename": "失败.spec.ts", "asset": {"asset_id": 2, "current_revision_id": 22}},
        ]

        def execute(_run_id, _step_key, script):
            passed = script["module_name"] == "模块A"
            return {
                **script,
                "execution_run_id": f"run-{script['module_name']}",
                "execution_job_id": f"job-{script['module_name']}",
                "execution": {
                    "status": "succeeded" if passed else "failed",
                    "job_id": f"job-{script['module_name']}",
                    "run_id": f"run-{script['module_name']}",
                    "result_id": 101 if passed else 202,
                },
                **({} if passed else {"error": "脚本执行失败，退出码：1"}),
            }

        with (
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-pass"}, {"attempt_id": "attempt-fail"}],
            ),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "append_agent_event"),
            patch.object(app, "agent_execute_generated_script", side_effect=execute),
            patch.object(app, "agent_finish_step"),
        ):
            passed, failures = app.agent_execute_generated_scripts("agent-1", scripts)

        self.assertEqual(passed[0]["attempt_id"], "attempt-pass")
        self.assertEqual(failures[0]["attempt_id"], "attempt-fail")
        self.assertEqual(failures[0]["failure_id"], "attempt-fail")
        self.assertEqual([call.args[2] for call in finish_attempt.call_args_list], ["succeeded", "failed"])
        self.assertEqual(finish_attempt.call_args_list[0].kwargs["outcome_type"], "passed")
        self.assertEqual(finish_attempt.call_args_list[1].kwargs["result_id"], 202)

    def test_repair_attempts_persist_repaired_and_failed_results(self):
        scripts = [
            {"module_name": "模块A", "filename": "已修复.spec.ts", "asset": {"asset_id": 1, "current_revision_id": 10}},
            {"module_name": "模块B", "filename": "修复失败.spec.ts", "asset": {"asset_id": 2, "current_revision_id": 20}},
        ]

        def repair(_run_id, _step_key, script):
            if script["module_name"] == "模块B":
                raise app.AgentItemFailure("修复工具失败", job_id="healer-failed", error_type="tool")
            return {
                **script,
                "repair_job_id": "healer-success",
                "asset": {"asset_id": 1, "current_revision_id": 11},
            }

        with (
            patch.object(app, "agent_start_step"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-repaired"}, {"attempt_id": "attempt-repair-failed"}],
            ),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "append_agent_artifact_progress"),
            patch.object(app, "agent_repair_script", side_effect=repair),
            patch.object(app, "agent_finish_step"),
        ):
            repaired, failures = app.agent_repair_scripts("agent-1", scripts)

        self.assertEqual(repaired[0]["attempt_id"], "attempt-repaired")
        self.assertEqual(failures[0]["attempt_id"], "attempt-repair-failed")
        self.assertEqual(failures[0]["job_id"], "healer-failed")
        self.assertEqual([call.args[2] for call in finish_attempt.call_args_list], ["succeeded", "failed"])
        self.assertEqual(finish_attempt.call_args_list[0].kwargs["outcome_type"], "repaired")
        self.assertEqual(finish_attempt.call_args_list[0].kwargs["verification_status"], "not_run")


class AgentDiagnosticBundleTests(unittest.TestCase):
    def test_bundle_builder_redacts_secrets_and_project_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            secret_file = project_root / "failure.log"
            secret_file.write_text(
                f"username=admin password=TopSecret token=abc123 path={project_root}",
                encoding="utf-8",
            )
            project = {
                "project_key": "demo",
                "playwright_project_root": str(project_root),
                "target_system": {"username": "admin", "password": "TopSecret"},
                "database_baseline": {"enabled": False},
                "opencode_config": {"api_key": "abc123"},
            }
            with (
                patch.object(app, "get_current_project", return_value=project),
                patch.object(app, "get_project_root", return_value=project_root),
                patch.object(app, "get_platform_database_config", return_value={"password": "DbSecret"}),
            ):
                builder = app.DiagnosticBundleBuilder()
                builder.add_project_text_file("logs/job.log", secret_file)
                builder.add_json("environment/config.json", project)
                buffer = builder.build({"bundle_schema_version": 1})

            with zipfile.ZipFile(buffer, "r") as archive:
                names = set(archive.namelist())
                combined = b"\n".join(archive.read(name) for name in names).decode("utf-8")

        self.assertIn("logs/job.log", names)
        self.assertIn("inventory.json", names)
        self.assertIn("manifest.json", names)
        self.assertNotIn("TopSecret", combined)
        self.assertNotIn("abc123", combined)
        self.assertNotIn(str(project_root), combined)
        self.assertIn("${PROJECT_ROOT}", combined)
        self.assertIn("******", combined)

    def test_diagnostic_download_route_returns_zip_attachment(self):
        client = app.app.test_client()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"ok": True}))
        buffer.seek(0)

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(
                app,
                "build_agent_attempt_diagnostic_bundle",
                return_value=(buffer, "agent-diagnostic-test.zip"),
            ),
        ):
            response = client.get("/api/agent/runs/agent-1/attempts/attempt-1/diagnostic-bundle")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn("agent-diagnostic-test.zip", response.headers["Content-Disposition"])

    def test_legacy_diagnostic_route_backfills_attempt_before_download(self):
        client = app.app.test_client()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"ok": True}))
        buffer.seek(0)
        selector = {
            "step_key": "generate_scripts",
            "module_name": "清收统计与日汇总",
            "plan_filename": "按年参数缺失校验.md",
            "job_id": "generator-legacy",
        }

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "create_legacy_agent_failure_attempt", return_value="attempt-legacy-1") as create_attempt,
            patch.object(
                app,
                "build_agent_attempt_diagnostic_bundle",
                return_value=(buffer, "agent-diagnostic-legacy.zip"),
            ) as build_bundle,
        ):
            response = client.post("/api/agent/runs/agent-1/legacy-diagnostic-bundle", json=selector)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        create_attempt.assert_called_once_with("agent-1", selector)
        build_bundle.assert_called_once_with("agent-1", "attempt-legacy-1")

    @unittest.skip("旧失败阶段已由唯一脚本准备状态机替代")
    def test_legacy_failure_is_persisted_as_a_deterministic_attempt(self):
        failure = {
            "module_name": "清收统计与日汇总",
            "plan_filename": "按年参数缺失校验.md",
            "asset": {"asset_id": 765, "current_revision_id": 872},
            "error": "OpenCode HTTP 500",
        }
        step_row = {
            "step_key": "generate_scripts",
            "status": "succeeded",
            "input_json": json.dumps({"plans": [failure]}, ensure_ascii=False),
            "output_json": json.dumps({"scripts": [], "failures": [failure]}, ensure_ascii=False),
            "started_at": 1000,
            "finished_at": 3000,
            "error": "",
        }
        selector = {
            "step_key": "generate_scripts",
            "module_name": failure["module_name"],
            "plan_filename": failure["plan_filename"],
            "job_id": "generator-legacy",
        }

        with (
            patch.object(app, "get_agent_step_row", return_value=step_row),
            patch.object(app, "find_legacy_agent_failure_job_event", return_value={"job_id": "generator-legacy", "created_at": 2500}),
            patch.object(app, "get_current_project_id", return_value=53),
            patch.object(app, "get_test_job", return_value={"job_id": "generator-legacy", "created_at": 1500}),
            patch.object(app, "get_agent_attempt", return_value=None),
            patch.object(app, "start_agent_attempt") as start_attempt,
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "update_agent_step") as update_step,
        ):
            attempt_id = app.create_legacy_agent_failure_attempt("agent-1", selector)

        self.assertTrue(attempt_id.startswith("attempt-legacy-"))
        self.assertEqual(start_attempt.call_args.kwargs["attempt_id"], attempt_id)
        self.assertEqual(start_attempt.call_args.kwargs["started_at"], 1500)
        self.assertEqual(finish_attempt.call_args.args[:3], ("agent-1", attempt_id, "failed"))
        self.assertEqual(finish_attempt.call_args.kwargs["job_id"], "generator-legacy")
        self.assertEqual(finish_attempt.call_args.kwargs["source_asset_id"], 765)
        persisted_failure = update_step.call_args.kwargs["output_data"]["failures"][0]
        self.assertEqual(persisted_failure["attempt_id"], attempt_id)
        self.assertEqual(persisted_failure["failed_at"], 2500)

    def test_full_bundle_contains_attempt_prompt_log_source_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            plan_file = project_root / "specs" / "登录" / "登录失败.md"
            log_file = project_root / ".test-plan-viewer" / "jobs" / "planner-failed.log"
            plan_file.parent.mkdir(parents=True)
            log_file.parent.mkdir(parents=True)
            plan_file.write_text("# 登录失败计划", encoding="utf-8")
            log_file.write_text("任务失败 password=TopSecret", encoding="utf-8")
            (project_root / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
            project = {
                "project_id": 1,
                "project_key": "demo",
                "name": "演示项目",
                "playwright_project_root": str(project_root),
                "specs_dir": "specs",
                "tests_dir": "tests",
                "target_system": {"username": "admin", "password": "TopSecret"},
                "database_baseline": {"enabled": False},
                "plan_generation": {},
                "opencode_config": {},
            }
            run_row = {
                "run_id": "agent-1",
                "requirement_uid": "",
                "status": "failed",
                "current_step": "generate_plans",
            }
            attempt_row = {
                "attempt_id": "attempt-1",
                "run_id": "agent-1",
                "step_key": "generate_plans",
                "attempt_no": 1,
                "item_type": "plan",
                "item_key": "登录/登录失败.md",
                "module_name": "登录",
                "plan_filename": "登录失败.md",
                "status": "failed",
                "job_id": "planner-failed",
                "error_type": "agent",
                "error_message": "OpenCode 失败",
                "input_snapshot_json": '{"module_name":"登录"}',
                "output_summary_json": '{}',
                "artifact_refs_json": '[]',
            }
            step_row = {
                "run_id": "agent-1",
                "step_key": "generate_plans",
                "step_name": "计划生成",
                "status": "failed",
                "input_json": '{"modules":[{"module_name":"登录"}]}',
                "output_json": '{"failures":[{"attempt_id":"attempt-1"}]}',
                "counts_json": '{"failed":1}',
            }
            job_row = {
                "job_id": "planner-failed",
                "job_type": "planner",
                "status": "failed",
                "prompt": "使用账号 admin、密码 TopSecret 登录",
                "prompt_context_json": '{}',
                "log_path": str(log_file),
                "error": "OpenCode 失败",
            }

            with (
                patch.object(app, "get_current_project", return_value=project),
                patch.object(app, "get_project_root", return_value=project_root),
                patch.object(app, "get_platform_database_config", return_value={"enabled": True, "password": "DbSecret"}),
                patch.object(app, "get_agent_run_row", return_value=run_row),
                patch.object(app, "get_agent_attempt", return_value=attempt_row),
                patch.object(app, "get_agent_step_row", return_value=step_row),
                patch.object(app, "get_test_job", return_value=job_row),
                patch.object(app, "list_agent_events", return_value=[]),
                patch.object(app, "list_job_artifacts", return_value=[]),
                patch.object(app, "get_git_head_sha", return_value="deadbeef"),
            ):
                buffer, filename = app.build_agent_attempt_diagnostic_bundle("agent-1", "attempt-1")

            with zipfile.ZipFile(buffer, "r") as archive:
                names = set(archive.namelist())
                prompt = archive.read("prompts/effective-prompt.txt").decode("utf-8")
                log = archive.read("logs/job.log").decode("utf-8")
                manifest = json.loads(archive.read("manifest.json"))

        self.assertTrue(filename.endswith(".zip"))
        self.assertIn("README_FOR_CODEX.md", names)
        self.assertIn("failure/failure.json", names)
        self.assertIn("sources/登录失败.md", names)
        self.assertIn("environment/package.json", names)
        self.assertNotIn("TopSecret", prompt)
        self.assertNotIn("TopSecret", log)
        self.assertEqual(manifest["attempt_id"], "attempt-1")

    def test_failure_dialog_exposes_attempt_download_only_for_failed_artifacts(self):
        source = read_platform_javascript()
        template = render_index_template()
        stylesheet = read_platform_stylesheets()

        self.assertIn('data-agent-id="artifactDiagnosticDownload"', template)
        self.assertIn("attemptId: item.attempt_id || item.failure_id", source)
        self.assertIn("async function downloadArtifactDiagnosticBundle()", source)
        self.assertIn("/legacy-diagnostic-bundle", source)
        self.assertIn("历史失败会在首次操作时补建诊断记录", source)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto auto;", stylesheet)


class ScriptGenerationLanguageRouteTests(unittest.TestCase):
    VALID_SCRIPT = (
        "import { test, expect } from '@playwright/test';\n"
        "test('successful login', async () => {\n"
        "  expect(true).toBe(true);\n"
        "});\n"
    )

    def test_single_generation_uses_language_captured_before_stream_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_file = root / "specs" / "Authentication" / "Successful Login.md"
            script_dir = root / "tests" / "Authentication"
            target_file = script_dir / "Successful Login.spec.ts"
            candidate_file = root / "candidates" / target_file.name
            plan_file.parent.mkdir(parents=True)
            script_dir.mkdir(parents=True)
            plan_file.write_text("# Successful Login\n", encoding="utf-8")
            language = {"value": "en"}
            candidate_languages = []

            def candidate_path(_module_name, _plan_filename, _job_id, language=None):
                candidate_languages.append(language)
                return candidate_file

            def generated_stream(*_args, **kwargs):
                # Simulate a project setting change while OpenCode is running.
                # Finalization must continue to use the request's captured locale.
                language["value"] = "zh-CN"
                candidate_file.write_text(self.VALID_SCRIPT, encoding="utf-8")
                payload = kwargs["success_payload_factory"]()
                yield app.sse_payload(
                    "done",
                    {"ok": True, "status": "succeeded", **payload},
                )

            def script_path(module_name, filename):
                self.assertEqual(module_name, "Authentication")
                self.assertEqual(filename, target_file.name)
                return target_file

            with (
                patch.object(app, "get_auth_config", return_value={"enabled": False}),
                patch.object(
                    app,
                    "agent_project_language",
                    side_effect=lambda: language["value"],
                ),
                patch.object(app, "get_plan_target_path", return_value=plan_file),
                patch.object(app, "get_script_module_dir", return_value=script_dir),
                patch.object(app, "get_script_file", side_effect=script_path),
                patch.object(
                    app,
                    "get_script_generation_candidate_file",
                    side_effect=candidate_path,
                ),
                patch.object(
                    app,
                    "collect_generation_managed_files",
                    return_value=[plan_file, target_file],
                ),
                patch.object(
                    app,
                    "iter_generation_managed_files",
                    side_effect=lambda: iter(
                        path
                        for path in (plan_file, target_file)
                        if path.exists()
                    ),
                ),
                patch.object(app, "sync_plan_asset", return_value={"asset_id": 10}),
                patch.object(app, "sync_script_asset", return_value={"asset_id": 20}),
                patch.object(app, "serialize_asset", side_effect=lambda value: value),
                patch.object(app, "list_asset_revisions", return_value=[]),
                patch.object(app, "create_test_job"),
                patch.object(app, "build_script_generation_prompt", return_value="prompt"),
                patch.object(app, "build_setup_targets", return_value=[]),
                patch.object(app, "backup_script_file", return_value=None),
                patch.object(app, "stream_plan_generation", side_effect=generated_stream),
            ):
                response = app.app.test_client().post(
                    "/api/script-generation-stream",
                    json={
                        "module_name": "Authentication",
                        "plan_filename": plan_file.name,
                        "prompt": "Generate the script.",
                        "job_id": "generator-language-test",
                    },
                )
                events = list(
                    app.parse_sse_text_blocks(response.get_data(as_text=True))
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(candidate_languages, ["en"])
            self.assertEqual(target_file.read_text(encoding="utf-8"), self.VALID_SCRIPT)
            done = [data for event, data in events if event == "done"][-1]
            self.assertEqual(done["status"], "succeeded")
            self.assertEqual(done["script_filename"], target_file.name)


class AgentItemRetryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.get_agent_run_row_patcher = patch.object(
            app,
            "get_agent_run_row",
            return_value={"summary_json": "{}"},
        )
        self.get_agent_run_row = self.get_agent_run_row_patcher.start()
        self.addCleanup(self.get_agent_run_row_patcher.stop)

    def make_flow(self, auto_repair=True):
        return {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "root_attempt_id": "attempt-root",
            "item_type": "script",
            "item_key": "登录/登录失败.md",
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "status": "queued",
            "current_phase": "queued",
            "progress_message": "等待重新生成脚本。",
            "auto_repair": 1 if auto_repair else 0,
            "result_json": "{}",
            "cancel_requested": 0,
        }

    def make_root_attempt(self):
        return {
            "attempt_id": "attempt-root",
            "run_id": "agent-1",
            "step_key": "generate_scripts",
            "status": "failed",
            "item_key": "登录/登录失败.md",
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "input_snapshot_json": json.dumps(
                {
                    "module_name": "登录",
                    "plan_filename": "登录失败.md",
                    "asset": {"asset_id": 10},
                },
                ensure_ascii=False,
            ),
            "output_summary_json": "{}",
        }

    def stateful_flow_updater(self, flow, updates_seen):
        def update(_run_id, _retry_flow_id, **updates):
            updates_seen.append(dict(updates))
            for key, value in updates.items():
                if key == "result":
                    flow["result_json"] = json.dumps(value, ensure_ascii=False)
                elif key in {"auto_repair", "cancel_requested"}:
                    flow[key] = 1 if value else 0
                else:
                    flow[key] = value
            return dict(flow)

        return update

    def test_retry_generation_then_execution_passes_without_repair(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "job_id": "generator-2",
            "asset": {"asset_id": 20, "current_revision_id": 21, "from_plan_asset_id": 10},
        }
        executed = {
            **generated,
            "execution_run_id": "run-1",
            "execution_job_id": "execution-1",
            "execution": {"status": "succeeded", "run_id": "run-1", "job_id": "execution-1", "result_id": 31},
        }

        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(app, "update_agent_item_retry_flow", side_effect=self.stateful_flow_updater(flow, updates_seen)),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-generation"}, {"attempt_id": "attempt-execution"}],
            ),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "agent_generate_script_for_plan", return_value=generated) as generate_one,
            patch.object(app, "agent_execute_generated_script", return_value=executed) as execute_one,
            patch.object(app, "agent_repair_script") as repair_one,
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "supersede_agent_failed_script_review"),
            patch.object(app, "mark_agent_suite_stale_after_item_retry"),
        ):
            app.run_agent_item_retry_workflow("agent-1", "retry-1", {"project_key": "demo"}, "admin")

        generate_one.assert_called_once()
        execute_one.assert_called_once()
        repair_one.assert_not_called()
        self.assertEqual(finish_attempt.call_count, 2)
        self.assertTrue(any(item.get("status") == "succeeded" and item.get("current_phase") == "completed" for item in updates_seen))

    def test_retry_generation_restores_the_original_run_language(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "Authentication",
            "plan_filename": "Successful Login.md",
            "filename": "Successful Login.spec.ts",
            "job_id": "generator-english-retry",
            "asset": {
                "asset_id": 20,
                "current_revision_id": 21,
                "from_plan_asset_id": 10,
            },
        }
        executed = {
            **generated,
            "execution": {
                "status": "succeeded",
                "run_id": "run-english-retry",
                "job_id": "execution-english-retry",
                "result_id": 31,
            },
        }
        observed_languages = []

        def generate_one(*_args, **_kwargs):
            observed_languages.append(app.agent_project_language())
            return generated

        self.get_agent_run_row.return_value = {
            "summary_json": json.dumps({"language": "en"}),
        }
        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(
                app,
                "update_agent_item_retry_flow",
                side_effect=self.stateful_flow_updater(flow, updates_seen),
            ),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[
                    {"attempt_id": "attempt-generation"},
                    {"attempt_id": "attempt-execution"},
                ],
            ),
            patch.object(app, "finish_agent_attempt"),
            patch.object(
                app,
                "agent_generate_script_for_plan",
                side_effect=generate_one,
            ),
            patch.object(
                app,
                "agent_execute_generated_script",
                return_value=executed,
            ),
            patch.object(app, "agent_repair_script"),
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "supersede_agent_failed_script_review"),
            patch.object(app, "mark_agent_suite_stale_after_item_retry"),
        ):
            app.run_agent_item_retry_workflow(
                "agent-1",
                "retry-1",
                {"project_key": "demo", "language": "zh-CN"},
                "admin",
            )

        self.assertEqual(observed_languages, ["en"])
        self.assertTrue(
            any(item.get("status") == "succeeded" for item in updates_seen)
        )

    def test_retry_execution_failure_repairs_once_and_verifies(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "job_id": "generator-2",
            "asset": {"asset_id": 20, "current_revision_id": 21, "from_plan_asset_id": 10},
        }
        execution_failure = {
            **generated,
            "error": "脚本执行失败，退出码：1",
            "execution_run_id": "run-failed",
            "execution_job_id": "execution-failed",
            "execution": {"status": "failed", "run_id": "run-failed", "job_id": "execution-failed", "result_id": 41},
        }
        repaired = {
            **generated,
            "repair_job_id": "healer-1",
            "repair_test_run_id": "repair-run",
            "repair_result_id": 51,
            "asset": {"asset_id": 20, "current_revision_id": 22, "from_plan_asset_id": 10},
        }
        verification_passed = {
            **repaired,
            "execution_run_id": "run-passed",
            "execution_job_id": "execution-passed",
            "execution": {"status": "succeeded", "run_id": "run-passed", "job_id": "execution-passed", "result_id": 61},
        }

        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(app, "update_agent_item_retry_flow", side_effect=self.stateful_flow_updater(flow, updates_seen)),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[
                    {"attempt_id": "attempt-generation"},
                    {"attempt_id": "attempt-execution"},
                    {"attempt_id": "attempt-repair"},
                    {"attempt_id": "attempt-verification"},
                ],
            ) as start_attempt,
            patch.object(app, "finish_agent_attempt"),
            patch.object(app, "agent_generate_script_for_plan", return_value=generated),
            patch.object(app, "agent_execute_generated_script", side_effect=[execution_failure, verification_passed]) as execute_one,
            patch.object(app, "agent_repair_script", return_value=repaired) as repair_one,
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "supersede_agent_failed_script_review"),
            patch.object(app, "mark_agent_suite_stale_after_item_retry"),
        ):
            app.run_agent_item_retry_workflow("agent-1", "retry-1", {"project_key": "demo"}, "admin")

        self.assertEqual(start_attempt.call_count, 4)
        self.assertEqual(execute_one.call_count, 2)
        repair_one.assert_called_once()
        phases = [item.get("current_phase") for item in updates_seen if item.get("current_phase")]
        self.assertEqual(phases, ["generating", "executing", "repairing", "verifying", "completed", "completed"])
        self.assertTrue(any(item.get("status") == "finalizing" for item in updates_seen))
        self.assertTrue(any(item.get("status") == "succeeded" for item in updates_seen))

    def test_environment_execution_failure_is_blocked_without_repair(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "asset": {"asset_id": 20, "current_revision_id": 21},
        }
        blocked = {
            **generated,
            "error": "数据库基线恢复失败",
            "execution": {"status": "failed", "result_id": 71},
        }

        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(app, "update_agent_item_retry_flow", side_effect=self.stateful_flow_updater(flow, updates_seen)),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-generation"}, {"attempt_id": "attempt-execution"}],
            ),
            patch.object(app, "finish_agent_attempt"),
            patch.object(app, "agent_generate_script_for_plan", return_value=generated),
            patch.object(app, "agent_execute_generated_script", return_value=blocked),
            patch.object(app, "agent_repair_script") as repair_one,
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "clear_agent_retry_step_markers"),
            patch.object(app, "append_agent_item_retry_event"),
        ):
            app.run_agent_item_retry_workflow("agent-1", "retry-1", {"project_key": "demo"}, "admin")

        repair_one.assert_not_called()
        self.assertTrue(any(item.get("status") == "blocked" for item in updates_seen))

    def test_execution_exception_is_recorded_as_blocked_item_without_repair(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "asset": {"asset_id": 20, "current_revision_id": 21},
        }

        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(app, "update_agent_item_retry_flow", side_effect=self.stateful_flow_updater(flow, updates_seen)),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[{"attempt_id": "attempt-generation"}, {"attempt_id": "attempt-execution"}],
            ),
            patch.object(app, "finish_agent_attempt") as finish_attempt,
            patch.object(app, "agent_generate_script_for_plan", return_value=generated),
            patch.object(app, "agent_execute_generated_script", side_effect=RuntimeError("数据库基线恢复失败")),
            patch.object(app, "agent_repair_script") as repair_one,
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "clear_agent_retry_step_markers"),
            patch.object(app, "append_agent_item_retry_event"),
        ):
            app.run_agent_item_retry_workflow("agent-1", "retry-1", {"project_key": "demo"}, "admin")

        repair_one.assert_not_called()
        self.assertTrue(any(item.get("status") == "blocked" for item in updates_seen))
        execution_finish = finish_attempt.call_args_list[-1]
        self.assertEqual(execution_finish.kwargs.get("error_type"), "environment")
        self.assertIn("数据库基线恢复失败", execution_finish.kwargs.get("output_summary", {}).get("error", ""))

    def test_failed_verification_removes_repair_candidate_from_effective_scripts(self):
        flow = self.make_flow()
        updates_seen = []
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "asset": {"asset_id": 20, "current_revision_id": 21},
        }
        execution_failure = {
            **generated,
            "error": "脚本执行失败，退出码：1",
            "execution": {"status": "failed", "result_id": 41},
        }
        repaired = {
            **generated,
            "asset": {"asset_id": 20, "current_revision_id": 22},
        }
        verification_failure = {
            **repaired,
            "error": "脚本执行失败，退出码：1",
            "execution": {"status": "failed", "result_id": 61},
        }

        with (
            patch.object(app, "register_agent_item_retry_task"),
            patch.object(app, "cleanup_agent_item_retry_task"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "get_agent_item_retry_flow", return_value=flow),
            patch.object(app, "get_agent_attempt", return_value=self.make_root_attempt()),
            patch.object(app, "update_agent_item_retry_flow", side_effect=self.stateful_flow_updater(flow, updates_seen)),
            patch.object(
                app,
                "start_agent_attempt",
                side_effect=[
                    {"attempt_id": "attempt-generation"},
                    {"attempt_id": "attempt-execution"},
                    {"attempt_id": "attempt-repair"},
                    {"attempt_id": "attempt-verification"},
                ],
            ),
            patch.object(app, "finish_agent_attempt"),
            patch.object(app, "agent_generate_script_for_plan", return_value=generated),
            patch.object(app, "agent_execute_generated_script", side_effect=[execution_failure, verification_failure]),
            patch.object(app, "agent_repair_script", return_value=repaired),
            patch.object(app, "merge_agent_retry_step_result") as merge_result,
            patch.object(app, "clear_agent_retry_step_markers"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "supersede_agent_failed_script_review") as supersede_review,
            patch.object(app, "mark_agent_suite_stale_after_item_retry"),
        ):
            app.run_agent_item_retry_workflow("agent-1", "retry-1", {"project_key": "demo"}, "admin")

        repair_failure_merge = next(
            call
            for call in merge_result.call_args_list
            if len(call.args) >= 4 and call.args[1] == "repair_scripts" and call.args[3] == "failed"
        )
        self.assertTrue(repair_failure_merge.kwargs.get("remove_matching_script"))
        self.assertNotIn("script_item", repair_failure_merge.kwargs)
        self.assertTrue(any(item.get("status") == "failed" for item in updates_seen))
        supersede_review.assert_not_called()


class AgentItemRetryMergeTests(unittest.TestCase):
    @unittest.skip("旧单项重试合并已由脚本准备项动作替代")
    def test_generation_merge_preserves_other_items_and_is_idempotent(self):
        target_failure = {
            "attempt_id": "attempt-root",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "filename": "目标.spec.ts",
            "error": "生成失败",
        }
        row = {
            "output_json": json.dumps(
                {
                    "scripts": [{"module_name": "模块A", "plan_filename": "其他.md", "filename": "其他.spec.ts"}],
                    "failures": [target_failure, {"module_name": "模块C", "plan_filename": "仍失败.md", "error": "仍失败"}],
                    "retrying": [{"retry_flow_id": "retry-1", "module_name": "模块B", "plan_filename": "目标.md"}],
                    "resolved_failures": [],
                },
                ensure_ascii=False,
            ),
            "counts_json": json.dumps({"generated": 1, "failed": 2, "plans": 3}),
        }

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params):
                if sql.lstrip().startswith("UPDATE"):
                    row["output_json"] = params[0]
                    row["counts_json"] = params[1]

            def fetchone(self):
                return dict(row)

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                return None

        flow = {
            "retry_flow_id": "retry-1",
            "root_attempt_id": "attempt-root",
            "item_key": "模块B/目标.md",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "filename": "目标.spec.ts",
            "status": "running",
            "current_phase": "executing",
        }
        generated = {
            "attempt_id": "attempt-generation",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "filename": "目标.spec.ts",
        }

        with (
            patch.object(app, "require_platform_database", return_value={"table_prefix": ""}),
            patch.object(app, "get_current_project_id", return_value=1),
            patch.object(app, "platform_mysql_connection", side_effect=lambda _config: Connection()),
        ):
            first = app.merge_agent_retry_step_result(
                "agent-1",
                "generate_scripts",
                flow,
                "produced",
                script_item=generated,
            )
            second = app.merge_agent_retry_step_result(
                "agent-1",
                "generate_scripts",
                flow,
                "produced",
                script_item=generated,
            )

        self.assertEqual(len(first["output"]["scripts"]), 2)
        self.assertEqual(len(second["output"]["scripts"]), 2)
        self.assertEqual([item["module_name"] for item in second["output"]["failures"]], ["模块C"])
        self.assertEqual(len(second["output"]["resolved_failures"]), 1)
        self.assertEqual(second["counts"]["generated"], 2)
        self.assertEqual(second["counts"]["failed"], 1)

    @unittest.skip("旧单项重试合并已由脚本准备项动作替代")
    def test_failed_retry_replaces_current_failure_without_marking_it_resolved(self):
        original_failure = {
            "attempt_id": "attempt-root",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "error": "首次生成失败",
        }
        row = {
            "output_json": json.dumps(
                {"scripts": [], "failures": [original_failure], "retrying": [], "resolved_failures": []},
                ensure_ascii=False,
            ),
            "counts_json": json.dumps({"generated": 0, "failed": 1, "plans": 1}),
        }

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params):
                if sql.lstrip().startswith("UPDATE"):
                    row["output_json"] = params[0]
                    row["counts_json"] = params[1]

            def fetchone(self):
                return dict(row)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                return None

        flow = {
            "retry_flow_id": "retry-1",
            "root_attempt_id": "attempt-root",
            "item_key": "模块B/目标.md",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "filename": "目标.spec.ts",
            "status": "failed",
            "current_phase": "generating",
        }
        latest_failure = {
            "attempt_id": "attempt-retry",
            "module_name": "模块B",
            "plan_filename": "目标.md",
            "error": "再次生成失败",
        }
        with (
            patch.object(app, "require_platform_database", return_value={}),
            patch.object(app, "get_agent_run_steps_table", return_value="agent_run_steps"),
            patch.object(app, "get_current_project_id", return_value=1),
            patch.object(app, "platform_mysql_connection", return_value=Connection()),
        ):
            merged = app.merge_agent_retry_step_result(
                "agent-1",
                "generate_scripts",
                flow,
                "failed",
                failure_item=latest_failure,
            )

        self.assertEqual([item["attempt_id"] for item in merged["output"]["failures"]], ["attempt-retry"])
        self.assertEqual(merged["output"]["resolved_failures"], [])
        self.assertEqual(merged["counts"]["resolved"], 0)

    def test_final_script_collection_deduplicates_and_keeps_latest_candidate(self):
        original = {
            "module_name": "登录",
            "filename": "登录.spec.ts",
            "asset": {"asset_id": 20, "current_revision_id": 21},
        }
        repaired = {
            **original,
            "asset": {"asset_id": 20, "current_revision_id": 22},
            "verification_status": "passed",
        }

        scripts = app.dedupe_agent_scripts([original, repaired])

        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["asset"]["current_revision_id"], 22)


class AgentItemRetryApiTests(unittest.TestCase):
    def test_retry_api_reuses_active_flow_without_starting_second_thread(self):
        client = app.app.test_client()
        run = {"run_id": "agent-1", "status": "failed"}
        attempt = {
            "attempt_id": "attempt-root",
            "run_id": "agent-1",
            "step_key": "generate_scripts",
            "status": "failed",
            "item_key": "登录/失败.md",
        }
        flow = {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "root_attempt_id": "attempt-root",
            "item_key": "登录/失败.md",
            "status": "queued",
            "current_phase": "queued",
            "auto_repair": 1,
            "result_json": "{}",
        }
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_agent_run_row", return_value=run) as get_run,
            patch.object(app, "get_active_agent_run_row", return_value=None),
            patch.object(app, "get_agent_attempt", return_value=attempt) as get_attempt,
            patch.object(app, "is_current_agent_generation_failure", return_value=True),
            patch.object(app, "create_agent_item_retry_flow", side_effect=[(flow, True), (flow, False)]) as create_flow,
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "get_current_project", return_value={"project_key": "demo"}),
            patch.object(app, "current_platform_author", return_value="admin"),
            patch.object(app, "start_agent_item_retry_thread") as start_thread,
        ):
            first = client.post("/api/agent/runs/agent-1/attempts/attempt-root/retry", json={"auto_repair": True})
            second = client.post("/api/agent/runs/agent-1/attempts/attempt-root/retry", json={"auto_repair": True})

        self.assertEqual(
            first.status_code,
            202,
            {"response": first.get_json(), "get_run": get_run.call_count, "get_attempt": get_attempt.call_count, "create": create_flow.call_count},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["retry_flow"]["retry_flow_id"], "retry-1")
        self.assertTrue(second.get_json()["idempotent"])
        start_thread.assert_called_once()

    def test_legacy_failure_attempt_api_returns_retryable_attempt(self):
        client = app.app.test_client()
        attempt = {
            "attempt_id": "attempt-legacy-1",
            "run_id": "agent-1",
            "step_key": "generate_scripts",
            "status": "failed",
            "item_key": "登录/失败.md",
            "input_snapshot_json": "{}",
            "output_summary_json": "{}",
            "artifact_refs_json": "[]",
        }
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_agent_run_row", return_value={"run_id": "agent-1"}),
            patch.object(app, "create_legacy_agent_failure_attempt", return_value="attempt-legacy-1"),
            patch.object(app, "get_agent_attempt", return_value=attempt),
        ):
            response = client.post(
                "/api/agent/runs/agent-1/legacy-failure-attempt",
                json={"step_key": "generate_scripts", "module_name": "登录", "plan_filename": "失败.md"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["attempt"]["attempt_id"], "attempt-legacy-1")

    def test_resume_is_blocked_while_another_run_has_active_item_retry(self):
        client = app.app.test_client()
        active_flow = {
            "retry_flow_id": "retry-other",
            "run_id": "agent-other",
            "root_attempt_id": "attempt-other",
            "item_key": "登录/失败.md",
            "status": "running",
            "current_phase": "executing",
            "auto_repair": 1,
            "result_json": "{}",
        }
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(
                app,
                "get_agent_run_row",
                return_value={"run_id": "agent-1", "status": "failed", "current_step": "generate_scripts"},
            ),
            patch.object(app, "list_agent_item_retry_flows", return_value=[active_flow]) as list_flows,
            patch.object(app, "start_agent_resume_thread") as start_resume,
        ):
            response = client.post("/api/agent/runs/agent-1/resume", json={"from_step": "generate_scripts"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("当前项目有脚本正在重试并验证", response.get_json()["error"])
        list_flows.assert_called_once_with(active_only=True, limit=1)
        start_resume.assert_not_called()

    def test_retry_api_rejects_superseded_historical_attempt(self):
        client = app.app.test_client()
        attempt = {
            "attempt_id": "attempt-old",
            "run_id": "agent-1",
            "step_key": "generate_scripts",
            "status": "failed",
            "item_key": "登录/失败.md",
        }
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_agent_run_row", return_value={"run_id": "agent-1", "status": "failed"}),
            patch.object(app, "get_active_agent_run_row", return_value=None),
            patch.object(app, "get_agent_attempt", return_value=attempt),
            patch.object(app, "is_current_agent_generation_failure", return_value=False),
            patch.object(app, "create_agent_item_retry_flow") as create_flow,
        ):
            response = client.post("/api/agent/runs/agent-1/attempts/attempt-old/retry", json={})

        self.assertEqual(response.status_code, 409)
        self.assertIn("已被后续结果替代", response.get_json()["error"])
        create_flow.assert_not_called()

    def test_retry_thread_start_failure_releases_persisted_flow(self):
        client = app.app.test_client()
        attempt = {
            "attempt_id": "attempt-root",
            "run_id": "agent-1",
            "step_key": "generate_scripts",
            "status": "failed",
            "item_key": "登录/失败.md",
        }
        flow = {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "root_attempt_id": "attempt-root",
            "item_key": "登录/失败.md",
            "status": "queued",
            "current_phase": "queued",
            "result_json": "{}",
        }
        failed_flow = {**flow, "status": "failed", "error": "thread failed"}
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_agent_run_row", return_value={"run_id": "agent-1", "status": "failed"}),
            patch.object(app, "get_active_agent_run_row", return_value=None),
            patch.object(app, "get_agent_attempt", return_value=attempt),
            patch.object(app, "is_current_agent_generation_failure", return_value=True),
            patch.object(app, "create_agent_item_retry_flow", return_value=(flow, True)),
            patch.object(app, "merge_agent_retry_step_result"),
            patch.object(app, "append_agent_item_retry_event"),
            patch.object(app, "get_current_project", return_value={"project_key": "demo"}),
            patch.object(app, "current_platform_author", return_value="admin"),
            patch.object(app, "start_agent_item_retry_thread", side_effect=RuntimeError("thread failed")),
            patch.object(app, "clear_agent_retry_step_markers") as clear_markers,
            patch.object(app, "update_agent_item_retry_flow", return_value=failed_flow) as update_flow,
        ):
            response = client.post("/api/agent/runs/agent-1/attempts/attempt-root/retry", json={})

        self.assertEqual(response.status_code, 500)
        clear_markers.assert_called_once()
        self.assertEqual(update_flow.call_args.kwargs.get("status"), "failed")
        self.assertEqual(update_flow.call_args.kwargs.get("expected_statuses"), {"queued"})

    def test_cancel_does_not_overwrite_terminal_flow(self):
        terminal_flow = {
            "retry_flow_id": "retry-1",
            "run_id": "agent-1",
            "status": "succeeded",
        }
        with patch.object(app, "update_agent_item_retry_flow", return_value=terminal_flow) as update_flow:
            flow, result = app.request_agent_item_retry_cancel("agent-1", "retry-1")

        self.assertEqual(flow["status"], "succeeded")
        self.assertFalse(result["cancel_requested"])
        self.assertEqual(update_flow.call_args.kwargs.get("expected_statuses"), {"queued", "running"})


class AgentItemRetryFrontendContractTests(unittest.TestCase):
    def test_retry_ui_and_background_restore_contract(self):
        source = read_platform_javascript()
        template = render_index_template()
        stylesheet = read_platform_stylesheets()

        for element_id in (
            "retryStatusBar",
            "retryStatusTitle",
            "retryStatusMeta",
            "retryStatusView",
            "artifactRetryProgress",
            "artifactRetryProgressSteps",
            "artifactRetryAutoRepair",
            "artifactRetryCancelButton",
            "artifactRetryButton",
        ):
            self.assertIn(f'data-agent-id="{element_id}"', template)
        self.assertIn("async function retryArtifactAndVerify()", source)
        self.assertIn("/legacy-failure-attempt", source)
        self.assertIn("/retry-flows", source)
        self.assertIn("function shouldObserveSelectedRun()", source)
        self.assertIn("payload.retry_flow_progress", source)
        self.assertIn("state.activeRetryFlows.length", source)
        self.assertIn("agent:selected-run:", source)
        self.assertIn("const streamRunId = state.selectedRunId;", source)
        self.assertIn("function scheduleRetryTerminalRefresh", source)
        self.assertIn("async function cancelArtifactRetry()", source)
        self.assertIn('new Set(["queued", "running", "finalizing", "cancelling"])', source)
        self.assertIn("if (event.run_id && event.run_id !== state.selectedRunId)", source)
        select_run_source = source[source.index("async function selectRun(runId)") : source.index("async function submitRun")]
        self.assertLess(select_run_source.index("stopEventStream();"), select_run_source.index("state.selectedRunId = runId;"))
        self.assertIn("artifactModuleName !== flowModuleName", source)
        self.assertIn("正在重新生成", source)
        self.assertIn("正在自动修复", source)
        self.assertIn("正在复验", source)
        self.assertIn("artifact-retry-progress", stylesheet)

        generation_case = source[source.index('case "generate_scripts":') : source.index('case "execute_scripts":')]
        self.assertLess(generation_case.index("output.resolved_failures"), generation_case.index("output.failures"))
        self.assertLess(generation_case.index("output.failures"), generation_case.index("output.retrying"))


class ScriptTargetLeaseRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = app.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def execution_context(self):
        video = self.root / "video.config.ts"
        video.write_text("temporary", encoding="utf-8")
        return {
            "script_file": self.root / "script.spec.ts",
            "command_text": "npx playwright test",
            "command": ["npx", "playwright", "test"],
            "project_root": self.root,
            "video_config": video,
            "results_dir": self.root / "results",
            "report_dir": self.root / "report",
            "setup_resolution": None,
        }

    def test_sync_execution_busy_returns_409_and_cleans_video_config(self):
        context = self.execution_context()
        lease = Mock()
        lease.acquire.side_effect = app.ScriptTargetBusy("busy")
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "build_setup_targets", return_value=[]),
            patch.object(app, "resolve_setup_profile", return_value=None),
            patch.object(app, "build_script_execution_context", return_value=context),
            patch.object(app, "acquire_script_target_lease", return_value=lease),
        ):
            response = self.client.post(
                "/api/script-executions",
                json={"module_name": "登录", "filename": "登录.spec.ts"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(context["video_config"].exists())

    def test_sync_execution_and_recording_release_lease_on_completion(self):
        context = self.execution_context()
        lease = Mock()
        completed = Mock(returncode=0, stdout=b"ok", stderr=b"")
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "build_setup_targets", return_value=[]),
            patch.object(app, "resolve_setup_profile", return_value=None),
            patch.object(app, "build_script_execution_context", return_value=context),
            patch.object(app, "acquire_script_target_lease", return_value=lease),
            patch.object(app.subprocess, "run", return_value=completed),
            patch.object(app, "get_playwright_execution_env", return_value={}),
            patch.object(app, "summarize_process_output", return_value="ok"),
            patch.object(app, "build_run_video_result", return_value={}),
            patch.object(app, "build_playwright_report_result", return_value={}),
        ):
            response = self.client.post(
                "/api/script-executions",
                json={"module_name": "登录", "filename": "登录.spec.ts"},
            )
        self.assertEqual(response.status_code, 200)
        lease.release.assert_called_once()

        script = self.root / "recorded.spec.ts"
        script.write_text("test('recorded', () => {});", encoding="utf-8")
        recording_context = {
            "script_file": script,
            "command_text": "record",
            "command": ["npx", "codegen"],
            "project_root": self.root,
        }
        recording_lease = Mock()
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "build_script_recording_context", return_value=recording_context),
            patch.object(app, "acquire_script_target_lease", return_value=recording_lease),
            patch.object(app.subprocess, "run", side_effect=RuntimeError("boom")),
        ):
            response = self.client.post(
                "/api/script-recordings",
                json={"module_name": "登录", "filename": "recorded.spec.ts"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "failed")
        recording_lease.release.assert_called_once()


class SetupPreparationFrontendContractTests(unittest.TestCase):
    def test_setup_script_management_uses_only_the_three_table_api_contract(self):
        source = read_platform_javascript()
        stylesheet = read_platform_stylesheets()

        for endpoint in (
            'requestJson("/api/setup-scripts")',
            'requestJson("/api/setup-bindings")',
            'requestJson("/api/setup-runs")',
            'requestJson(`/api/setup-runs${suffix}`)',
            'requestJson(`/api/setup-scripts/${encodePathPart(script.uid)}/trial-run`',
        ):
            self.assertIn(endpoint, source)
        self.assertIn("function renderSetupScriptsPanel()", source)
        self.assertIn("function renderSetupScriptModal()", source)
        self.assertIn("function renderSetupScriptRunDetailModal()", source)
        self.assertIn('id="setupNewScript"', source)
        self.assertIn('class="setup-data-table setup-scripts-table"', source)
        self.assertIn("width: min(1220px, calc(100vw - 56px));", stylesheet)
        self.assertIn("grid-template-columns: minmax(0, 1.86fr) minmax(350px, 1fr);", stylesheet)

        for legacy in (
            "/api/setup-actions",
            "/api/setup-profiles",
            "renderSetupActionsPanel",
            "renderSetupProfilesPanel",
            "profileDraft",
            "testProjectDatabaseRestore",
            "正在测试数据库恢复",
            "数据库恢复测试完成",
        ):
            self.assertNotIn(legacy, source)

    def test_script_editor_keeps_an_isolated_draft_and_persists_one_binding(self):
        source = read_platform_javascript()

        self.assertIn("scriptDraft: null", source)
        self.assertIn("setup.scriptDraft = draft", source)
        self.assertIn("cloneSetupScript(existing)", source)
        for element_id in (
            "setupScriptForm",
            "setupScriptName",
            "setupScriptDescription",
            "setupScriptContent",
            "setupWorkingDirectory",
            "setupTimeoutSeconds",
            "setupConcurrencyKey",
            "setupScriptEnabled",
            "setupScopeKey",
            "setupScriptSaveTrial",
        ):
            self.assertIn(f'id="{element_id}"', source)
        self.assertIn('name="scope_type"', source)
        self.assertIn("function persistSetupScriptBinding(scriptUid)", source)
        self.assertIn("script_uid: scriptUid", source)
        self.assertIn("scope_key: draft.scope_key", source)
        self.assertLess(
            source.index('requestJson(sourceUid ? `/api/setup-scripts/'),
            source.index("const savedBinding = await persistSetupScriptBinding(saved.uid);"),
        )
        self.assertIn('key: `${moduleItem.name}/${script.name}`', source)
        self.assertNotIn("data-setup-drag-step", source)

    def test_trial_run_and_history_use_an_independent_execution_detail_modal(self):
        source = read_platform_javascript()

        self.assertIn("function openSetupRunDetail(scriptUid)", source)
        self.assertIn("async function trialRunSetupScript(", source)
        self.assertIn("function renderSetupScriptRunDetailModal()", source)
        self.assertIn('data-setup-open-runs="${escapeHtml(script.uid)}"', source)
        self.assertIn('data-setup-trial-script="${escapeHtml(script.uid)}"', source)
        self.assertIn('id="setupTrialRun"', source)
        self.assertIn('class="setup-run-history"', source)
        self.assertIn("setup-script-run-output", source)
        self.assertIn("await loadSetupPreparationRuns(script.uid);", source)
        self.assertIn("setup.selectedRunUid = getSetupScriptRuns(script.uid)[0]?.uid || \"\";", source)
        self.assertIn("历史执行记录仍会保留", source)
        self.assertIn('requestJson(`/api/setup-scripts/${encodePathPart(script.uid)}`, { method: "DELETE" })', source)

if __name__ == "__main__":
    unittest.main()
