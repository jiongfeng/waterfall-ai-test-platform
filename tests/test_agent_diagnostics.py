import ast
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app
from test_plan_viewer.agent import diagnostics


def passthrough_redactor(value, *_configs):
    return str(value or "")


def load_json(value, fallback):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def zip_payloads(buffer):
    with zipfile.ZipFile(buffer, "r") as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
        }


class DiagnosticDependencyFactory:
    @staticmethod
    def builder(project_root, project=None, **overrides):
        project = project or {
            "project_key": "demo",
            "target_system": {},
            "database_baseline": {},
            "opencode_config": {},
        }
        values = {
            "get_current_project": lambda: project,
            "get_platform_database_config": lambda: {},
            "redact_sensitive_text": passthrough_redactor,
            "get_project_root": lambda: Path(project_root),
            "get_home_path": Path.home,
            "text_file_max_bytes": 10 * 1024 * 1024,
            "bundle_max_bytes": 50 * 1024 * 1024,
        }
        values.update(overrides)
        return diagnostics.DiagnosticBuilderDependencies(**values)

    @staticmethod
    def agent(project_root, project=None, **overrides):
        builder = overrides.pop(
            "builder",
            DiagnosticDependencyFactory.builder(
                project_root,
                project=project,
            ),
        )
        values = {
            "builder": builder,
            "load_json_column": load_json,
            "get_requirement_by_uid": lambda _uid: None,
            "read_requirement_markdown": lambda _item: "",
            "get_plan_target_path": (
                lambda module_name, filename:
                Path(project_root) / "specs" / module_name / filename
            ),
            "get_script_file": (
                lambda module_name, filename:
                Path(project_root) / "tests" / module_name / filename
            ),
            "get_asset_revision": lambda _asset_id, _revision_id: None,
            "git_show_file": lambda _commit, _path: "",
            "git_diff_file": lambda _commit, _path: "",
            "list_job_artifacts": lambda _job_id: [],
            "list_run_artifacts": lambda _run_id, _result_id: [],
            "serialize_run_artifact_payload": lambda item: dict(item),
            "get_agent_run_row": lambda _run_id: None,
            "get_agent_attempt": lambda _run_id, _attempt_id: None,
            "serialize_agent_run": lambda row: dict(row),
            "serialize_agent_attempt": lambda row: dict(row),
            "get_agent_step_row": lambda _run_id, _step_key: None,
            "serialize_agent_step": lambda row: dict(row),
            "get_test_job": lambda _job_id: None,
            "serialize_job": lambda row: dict(row),
            "agent_step_name": lambda step_key: step_key,
            "list_agent_events": lambda _run_id, _after, _limit: [],
            "serialize_agent_event": lambda row: dict(row),
            "get_job_log_path": (
                lambda job_id:
                Path(project_root) / ".jobs" / f"{job_id}.log"
            ),
            "get_test_run": lambda _run_id: None,
            "serialize_test_suite_execution_run": lambda row: dict(row),
            "get_run_result": lambda _result_id: None,
            "serialize_run_result": lambda row: dict(row),
            "get_git_head_sha": lambda: "deadbeef",
            "current_time_ms": lambda: 123456,
            "platform_version": lambda: "test-platform",
            "python_version": "test-python",
            "run_process": (
                lambda *_args, **_kwargs:
                SimpleNamespace(stdout="v1", stderr="")
            ),
            "format_timestamp": lambda _format: "20260102-030405",
            "bundle_format_version": 1,
            "playwright_config_filenames": (
                "playwright.config.ts",
                "playwright.config.js",
            ),
        }
        values.update(overrides)
        return diagnostics.AgentDiagnosticDependencies(**values)


class AgentDiagnosticBoundaryTests(unittest.TestCase):
    def test_package_imports_neither_flask_nor_legacy_app(self):
        agent_dir = app.APP_DIR / "test_plan_viewer" / "agent"
        forbidden = {"app", "flask"}
        for source_path in sorted(agent_dir.glob("*.py")):
            with self.subTest(source_path=source_path.name):
                tree = ast.parse(
                    source_path.read_text(encoding="utf-8")
                )
                imported_roots = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported_roots.update(
                            alias.name.split(".", 1)[0]
                            for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported_roots.add(
                            node.module.split(".", 1)[0]
                        )
                self.assertTrue(
                    forbidden.isdisjoint(imported_roots),
                    imported_roots,
                )

    def test_dependency_records_expose_all_runtime_capabilities(self):
        builder_fields = {
            item.name
            for item in diagnostics.DiagnosticBuilderDependencies.__dataclass_fields__.values()
        }
        self.assertEqual(
            builder_fields,
            {
                "get_current_project",
                "get_platform_database_config",
                "redact_sensitive_text",
                "get_project_root",
                "get_home_path",
                "text_file_max_bytes",
                "bundle_max_bytes",
            },
        )
        agent_fields = {
            item.name
            for item in diagnostics.AgentDiagnosticDependencies.__dataclass_fields__.values()
        }
        for required in (
            "builder",
            "get_agent_run_row",
            "get_agent_attempt",
            "list_agent_events",
            "list_job_artifacts",
            "list_run_artifacts",
            "run_process",
            "format_timestamp",
        ):
            self.assertIn(required, agent_fields)


class DiagnosticPureParityTests(unittest.TestCase):
    def test_secret_member_and_filename_helpers_match_legacy(self):
        secret_inputs = (
            {},
            {
                "username": "admin",
                "nested": {
                    "api_key": 123,
                    "ordinary": "visible",
                    "token": ["not-a-scalar"],
                },
            },
            [{"password": "p"}, {"cookie": "session-id"}],
        )
        for value in secret_inputs:
            with self.subTest(value=value):
                self.assertEqual(
                    diagnostics.collect_diagnostic_secret_values(value),
                    app.collect_diagnostic_secret_values(value),
                )

        member_names = (
            "logs/job.log",
            r"logs\job.log",
            "/leading/slash.txt",
            "",
            ".",
            "../escape.txt",
            "a/../escape.txt",
        )
        for value in member_names:
            with self.subTest(value=value):
                try:
                    expected = app.normalize_diagnostic_member_name(
                        value
                    )
                except Exception as expected_error:
                    with self.assertRaises(type(expected_error)) as actual:
                        diagnostics.normalize_diagnostic_member_name(
                            value
                        )
                    self.assertEqual(
                        str(actual.exception),
                        str(expected_error),
                    )
                else:
                    self.assertEqual(
                        diagnostics.normalize_diagnostic_member_name(
                            value
                        ),
                        expected,
                    )

        filenames = (
            ("登录 / 失败?.md", "fallback"),
            (" " * 3, "fallback.txt"),
            ("a" * 120, "fallback"),
        )
        for value, fallback in filenames:
            self.assertEqual(
                diagnostics.diagnostic_safe_filename(
                    value,
                    fallback,
                ),
                app.diagnostic_safe_filename(value, fallback),
            )

    def test_redaction_text_and_values_match_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            project = {
                "target_system": {
                    "username": "admin",
                    "password": "TopSecret",
                },
                "database_baseline": {"token": "db-token"},
                "opencode_config": {"api_key": "agent-key"},
            }
            builder_dependencies = (
                DiagnosticDependencyFactory.builder(
                    project_root,
                    project=project,
                    get_platform_database_config=lambda: {
                        "password": "DbSecret"
                    },
                    redact_sensitive_text=app.redact_sensitive_text,
                )
            )
            text = (
                f"username=admin password=TopSecret "
                f"token=db-token authorization=Bearer raw "
                f"path={project_root}"
            )
            value = {
                "password": "TopSecret",
                "message": text,
                "items": ({"api_key": "agent-key"},),
            }
            with (
                patch.object(
                    app,
                    "get_current_project",
                    return_value=project,
                ),
                patch.object(
                    app,
                    "get_platform_database_config",
                    return_value={"password": "DbSecret"},
                ),
                patch.object(
                    app,
                    "get_project_root",
                    return_value=project_root,
                ),
            ):
                self.assertEqual(
                    diagnostics.redact_diagnostic_text(
                        text,
                        dependencies=builder_dependencies,
                    ),
                    app.redact_diagnostic_text(text),
                )
                self.assertEqual(
                    diagnostics.redact_diagnostic_value(
                        value,
                        dependencies=builder_dependencies,
                    ),
                    app.redact_diagnostic_value(value),
                )

    def test_event_matching_matches_legacy_precedence(self):
        dependencies = SimpleNamespace(
            load_json_column=app.load_json_column
        )
        attempt = {
            "attempt_id": "attempt-1",
            "job_id": "job-1",
            "step_key": "generate_scripts",
            "module_name": "账户",
            "filename": "登录.spec.ts",
        }
        events = (
            {
                "payload_json": '{"attempt_id":"attempt-1"}',
                "step_key": "other",
            },
            {
                "payload_json": "{}",
                "job_id": "job-1",
                "step_key": "other",
            },
            {
                "payload_json": '{"module_name":"账户"}',
                "step_key": "generate_scripts",
            },
            {
                "payload_json": '{"filename":"登录.spec.ts"}',
                "step_key": "generate_scripts",
            },
            {
                "payload_json": "{}",
                "message": "unrelated",
                "step_key": "generate_scripts",
            },
            {
                "payload_json": '{"attempt_id":"attempt-1"}',
                "step_key": "other",
            },
        )
        for event in events:
            self.assertEqual(
                diagnostics.diagnostic_event_matches_attempt(
                    event,
                    attempt,
                    dependencies,
                ),
                app.diagnostic_event_matches_attempt(event, attempt),
            )


class DiagnosticBundleBuilderTests(unittest.TestCase):
    def test_builder_enforces_member_file_and_bundle_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            binary = root / "binary.bin"
            binary.write_bytes(b"\xff\xfe\x00")
            text_file = root / "job.log"
            text_file.write_text("safe text", encoding="utf-8")
            symlink = root / "linked.log"
            symlink.symlink_to(text_file)
            dependencies = DiagnosticDependencyFactory.builder(
                root,
                text_file_max_bytes=10_000,
                bundle_max_bytes=100_000,
            )
            builder = diagnostics.DiagnosticBundleBuilder(
                dependencies,
                redaction_context=([], set()),
            )

            self.assertTrue(builder.add_bytes("ok.txt", b"ok"))
            self.assertFalse(builder.add_bytes("ok.txt", b"duplicate"))
            self.assertFalse(
                builder.add_bytes("too-large.txt", b"x" * 10_001)
            )
            self.assertFalse(
                builder.add_project_text_file(
                    "outside.txt",
                    outside,
                )
            )
            self.assertFalse(
                builder.add_project_text_file(
                    "binary.bin",
                    binary,
                )
            )
            self.assertFalse(
                builder.add_project_text_file(
                    "linked.log",
                    symlink,
                )
            )
            self.assertTrue(
                builder.add_project_text_file(
                    "logs/job.log",
                    text_file,
                )
            )
            buffer = builder.build({"bundle_schema_version": 1})
            payloads = zip_payloads(buffer)

            self.assertIn("inventory.json", payloads)
            self.assertIn("manifest.json", payloads)
            inventory = json.loads(payloads["inventory.json"])
            omitted_reasons = {
                item["reason"]
                for item in inventory["omitted"]
            }
            self.assertIn("压缩包内路径重复", omitted_reasons)
            self.assertIn("文件超过单文件大小限制", omitted_reasons)
            self.assertIn(
                "默认诊断包不包含无法脱敏的二进制文件",
                omitted_reasons,
            )
            self.assertIn("不打包符号链接", omitted_reasons)
            outside.unlink(missing_ok=True)

    def test_artifact_collection_keeps_metadata_and_omits_binaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_file = root / "result.log"
            log_file.write_text("failure", encoding="utf-8")
            video_file = root / "run.webm"
            video_file.write_bytes(b"video")
            unknown_file = root / "unknown.dat"
            unknown_file.write_text("unknown", encoding="utf-8")
            run_artifacts = [
                {
                    "artifact_id": 1,
                    "artifact_type": "video",
                    "path": str(video_file),
                },
                {
                    "artifact_id": 2,
                    "artifact_type": "log",
                    "path": str(log_file),
                },
                {
                    "artifact_id": 3,
                    "artifact_type": "custom",
                    "path": str(unknown_file),
                },
            ]
            dependencies = DiagnosticDependencyFactory.agent(
                root,
                list_run_artifacts=(
                    lambda _run_id, _result_id: run_artifacts
                ),
            )
            builder = diagnostics.DiagnosticBundleBuilder(
                dependencies.builder,
                redaction_context=([], set()),
            )
            diagnostics.collect_diagnostic_artifacts(
                builder,
                {
                    "job_id": "",
                    "test_run_id": "run-1",
                    "result_id": 9,
                    "artifact_refs": [],
                },
                dependencies,
            )

            self.assertIn(
                "artifacts/run-artifacts.json",
                builder.files,
            )
            self.assertIn(
                "artifacts/log/result.log",
                builder.files,
            )
            self.assertNotIn(
                "artifacts/video/run.webm",
                builder.files,
            )
            reasons = {item["reason"] for item in builder.omitted}
            self.assertIn(
                "默认脱敏诊断包不包含视频、trace 或截图；元数据已保留",
                reasons,
            )
            self.assertIn("未识别的产物类型", reasons)


class DiagnosticFullBundleParityTests(unittest.TestCase):
    def test_full_bundle_matches_legacy_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_file = root / "specs" / "登录" / "登录失败.md"
            log_file = root / ".jobs" / "planner-failed.log"
            plan_file.parent.mkdir(parents=True)
            log_file.parent.mkdir(parents=True)
            plan_file.write_text("# 登录失败计划", encoding="utf-8")
            log_file.write_text(
                "任务失败 password=TopSecret",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                '{"name":"demo"}',
                encoding="utf-8",
            )
            project = {
                "project_key": "demo",
                "name": "演示项目",
                "playwright_project_root": str(root),
                "specs_dir": "specs",
                "tests_dir": "tests",
                "target_system": {
                    "username": "admin",
                    "password": "TopSecret",
                },
                "database_baseline": {"enabled": False},
                "plan_generation": {},
                "opencode_config": {},
            }
            run_row = {
                "run_id": "agent-1",
                "requirement_uid": "",
                "status": "failed",
            }
            attempt_row = {
                "attempt_id": "attempt-1",
                "run_id": "agent-1",
                "step_key": "generate_plans",
                "item_type": "plan",
                "item_key": "登录/登录失败.md",
                "module_name": "登录",
                "plan_filename": "登录失败.md",
                "status": "failed",
                "job_id": "planner-failed",
                "error_type": "agent",
                "error": "OpenCode 失败",
                "input_snapshot": {"module_name": "登录"},
                "artifact_refs": [],
            }
            step_row = {
                "input": {"modules": [{"module_name": "登录"}]},
                "output": {"failures": [{"attempt_id": "attempt-1"}]},
                "counts": {"failed": 1},
            }
            job_row = {
                "job_id": "planner-failed",
                "prompt": "使用账号 admin、密码 TopSecret 登录",
                "prompt_context": {},
                "log_path": str(log_file),
                "log_tail": "hidden duplicate",
            }
            event_rows = [
                {
                    "payload_json": '{"attempt_id":"attempt-1"}',
                    "step_key": "generate_plans",
                    "message": "password=TopSecret",
                }
            ]
            process_result = SimpleNamespace(
                stdout="v-test",
                stderr="",
            )

            patches = (
                patch.object(
                    app,
                    "get_current_project",
                    return_value=project,
                ),
                patch.object(
                    app,
                    "get_project_root",
                    return_value=root,
                ),
                patch.object(
                    app,
                    "get_platform_database_config",
                    return_value={"password": "DbSecret"},
                ),
                patch.object(
                    app,
                    "redact_sensitive_text",
                    side_effect=passthrough_redactor,
                ),
                patch.object(
                    app,
                    "get_agent_run_row",
                    return_value=run_row,
                ),
                patch.object(
                    app,
                    "get_agent_attempt",
                    return_value=attempt_row,
                ),
                patch.object(
                    app,
                    "serialize_agent_run",
                    side_effect=lambda row: dict(row),
                ),
                patch.object(
                    app,
                    "serialize_agent_attempt",
                    side_effect=lambda row: dict(row),
                ),
                patch.object(
                    app,
                    "get_agent_step_row",
                    return_value=step_row,
                ),
                patch.object(
                    app,
                    "serialize_agent_step",
                    side_effect=lambda row: dict(row),
                ),
                patch.object(
                    app,
                    "get_test_job",
                    return_value=job_row,
                ),
                patch.object(
                    app,
                    "serialize_job",
                    side_effect=lambda row: dict(row),
                ),
                patch.object(
                    app,
                    "list_agent_events",
                    return_value=event_rows,
                ),
                patch.object(
                    app,
                    "serialize_agent_event",
                    side_effect=lambda row: dict(row),
                ),
                patch.object(
                    app,
                    "list_job_artifacts",
                    return_value=[],
                ),
                patch.object(
                    app,
                    "get_plan_target_path",
                    return_value=plan_file,
                ),
                patch.object(
                    app,
                    "get_git_head_sha",
                    return_value="deadbeef",
                ),
                patch.object(
                    app,
                    "current_time_ms",
                    return_value=123456,
                ),
                patch.object(
                    app.platform,
                    "platform",
                    return_value="test-platform",
                ),
                patch.object(
                    app.subprocess,
                    "run",
                    return_value=process_result,
                ),
                patch.object(
                    app.time,
                    "strftime",
                    return_value="20260102-030405",
                ),
            )
            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                legacy_buffer, legacy_filename = (
                    app.build_agent_attempt_diagnostic_bundle(
                        "agent-1",
                        "attempt-1",
                    )
                )
                builder_dependencies = (
                    DiagnosticDependencyFactory.builder(
                        root,
                        project=project,
                        get_platform_database_config=(
                            app.get_platform_database_config
                        ),
                        redact_sensitive_text=(
                            app.redact_sensitive_text
                        ),
                    )
                )
                dependencies = (
                    DiagnosticDependencyFactory.agent(
                        root,
                        project=project,
                        builder=builder_dependencies,
                        get_agent_run_row=app.get_agent_run_row,
                        get_agent_attempt=app.get_agent_attempt,
                        serialize_agent_run=app.serialize_agent_run,
                        serialize_agent_attempt=(
                            app.serialize_agent_attempt
                        ),
                        get_agent_step_row=app.get_agent_step_row,
                        serialize_agent_step=app.serialize_agent_step,
                        get_test_job=app.get_test_job,
                        serialize_job=app.serialize_job,
                        agent_step_name=app.agent_step_name,
                        list_agent_events=app.list_agent_events,
                        serialize_agent_event=app.serialize_agent_event,
                        list_job_artifacts=app.list_job_artifacts,
                        get_plan_target_path=app.get_plan_target_path,
                        get_git_head_sha=app.get_git_head_sha,
                        current_time_ms=app.current_time_ms,
                        platform_version=app.platform.platform,
                        python_version=app.sys.version,
                        run_process=app.subprocess.run,
                        format_timestamp=app.time.strftime,
                        bundle_format_version=(
                            app.DIAGNOSTIC_BUNDLE_FORMAT_VERSION
                        ),
                        playwright_config_filenames=(
                            app.PLAYWRIGHT_CONFIG_FILENAMES
                        ),
                    )
                )
                new_buffer, new_filename = (
                    diagnostics.build_agent_attempt_diagnostic_bundle(
                        "agent-1",
                        "attempt-1",
                        dependencies,
                    )
                )

            self.assertEqual(new_filename, legacy_filename)
            self.assertEqual(
                zip_payloads(new_buffer),
                zip_payloads(legacy_buffer),
            )
            payloads = zip_payloads(new_buffer)
            combined = b"\n".join(payloads.values()).decode(
                "utf-8"
            )
            self.assertIn("failure/failure.json", payloads)
            self.assertNotIn("TopSecret", combined)
            self.assertIn("${PROJECT_ROOT}", combined)

    def test_missing_run_or_attempt_keeps_legacy_error(self):
        with tempfile.TemporaryDirectory() as directory:
            dependencies = DiagnosticDependencyFactory.agent(
                Path(directory)
            )
            with self.assertRaisesRegex(
                FileNotFoundError,
                "Agent 项目尝试记录不存在。",
            ):
                diagnostics.build_agent_attempt_diagnostic_bundle(
                    "missing",
                    "missing",
                    dependencies,
                )


if __name__ == "__main__":
    unittest.main()
