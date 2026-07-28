import ast
import json
import re
import shutil
import subprocess
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import app
from test_plan_viewer.agent import failure_handling as failure_domain


def normalize_route(rule):
    return re.sub(r"<(?:[^:>]+:)?[^>]+>", "<id>", str(rule))


def route_methods():
    return {
        (normalize_route(rule.rule), method)
        for rule in app.app.url_map.iter_rules()
        for method in rule.methods
        if method not in {"HEAD", "OPTIONS"}
    }


def read_agent_source():
    return (
        app.APP_DIR / "static" / "js" / "features" / "agent.js"
    ).read_text(encoding="utf-8")


def read_failure_workspace_source():
    return (
        app.APP_DIR
        / "static"
        / "js"
        / "features"
        / "agent-failure-workspace.js"
    ).read_text(encoding="utf-8")


def read_app_source():
    return (app.APP_DIR / "static" / "app.js").read_text(
        encoding="utf-8"
    )


def render_index_template():
    with app.app.test_request_context("/"):
        return app.render_template("index.html")


class AgentFailureHandlingApiContractTests(unittest.TestCase):
    def test_failure_analysis_uses_dedicated_agent_without_prefer_keep(self):
        prompt_path = (
            app.APP_DIR
            / "project-template"
            / ".opencode"
            / "prompts"
            / "test-platform-failure-analyst.md"
        )
        prompt = prompt_path.read_text(encoding="utf-8")
        ensure_source = (
            Path(app.__file__)
            .read_text(encoding="utf-8")
            .split(
                "def ensure_test_platform_failure_analyst_agent():",
                1,
            )[1]
            .split("\ndef ", 1)[0]
        )

        self.assertNotIn("prefer keep", prompt.lower())
        self.assertNotIn("prefer keep", ensure_source.lower())
        self.assertIn("Do not return reviewer decisions", prompt)

        payload = {"kind": "failure_analysis"}
        expected = {"summary": "独立分析结果"}
        with patch.object(
            app,
            "call_agent_json_agent",
            return_value=expected,
        ) as call_json_agent:
            result = app.call_agent_failure_analyst(
                "agent-1",
                "review_failed_scripts",
                "分析失败",
                payload,
            )

        self.assertEqual(result, expected)
        call_json_agent.assert_called_once_with(
            "agent-1",
            "review_failed_scripts",
            "分析失败",
            payload,
            "test-platform-failure-analyst",
            app.ensure_test_platform_failure_analyst_agent,
            failure_domain.redact_agent_failure_value,
        )

    def test_failure_analyst_has_no_project_file_tools(self):
        config = json.loads(
            (
                app.APP_DIR
                / "project-template"
                / "opencode.json"
            ).read_text(encoding="utf-8")
        )
        analyst = config["agent"]["test-platform-failure-analyst"]

        self.assertEqual(
            analyst["permission"],
            {"*": "deny", "external_directory": "deny"},
        )
        self.assertEqual(
            analyst["tools"],
            {"*": False},
        )

    @unittest.skipUnless(
        shutil.which("opencode"),
        "opencode CLI is unavailable",
    )
    def test_failure_analyst_effective_permissions_end_in_wildcard_deny(self):
        result = subprocess.run(
            ["opencode", "agent", "list", "--pure"],
            cwd=app.APP_DIR / "project-template",
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        marker = "test-platform-failure-analyst (subagent)"
        section = result.stdout.split(marker, 1)[1].lstrip()
        permissions, _ = json.JSONDecoder().raw_decode(section)
        wildcard_indexes = [
            index
            for index, item in enumerate(permissions)
            if item.get("permission") == "*"
        ]

        self.assertTrue(wildcard_indexes)
        final_wildcard_index = wildcard_indexes[-1]
        self.assertEqual(
            permissions[final_wildcard_index]["action"],
            "deny",
        )
        dangerous = {
            "*", "bash", "edit", "write", "patch", "read",
            "task", "webfetch", "websearch",
        }
        self.assertFalse(
            any(
                item.get("permission") in dangerous
                and item.get("action") == "allow"
                for item in permissions[final_wildcard_index + 1:]
            )
        )

    def test_failure_analyst_redacts_model_text_event_and_result(self):
        def redact(value):
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            return str(value).replace("raw-secret", "******")

        ensure_agent = MagicMock()
        with (
            patch.object(app, "create_test_job"),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "append_agent_event") as append_event,
            patch.object(app, "send_opencode_prompt", return_value={}),
            patch.object(
                app,
                "collect_opencode_response_text",
                return_value='{"summary":"raw-secret"}',
            ),
            patch.object(app, "append_test_job_log") as append_log,
            patch.object(app, "finish_test_job"),
        ):
            result = app.call_agent_json_agent(
                "agent-1",
                "review_failed_scripts",
                "分析失败",
                {"kind": "failure_analysis"},
                "test-platform-failure-analyst",
                ensure_agent,
                redact,
            )

        self.assertEqual(result, {"summary": "******"})
        self.assertNotIn("raw-secret", append_log.call_args.args[1])
        self.assertEqual(
            append_event.call_args.args[4],
            {"summary": "******"},
        )

    def test_failure_domain_redactor_uses_builder_dependencies(self):
        dependencies = object()
        with (
            patch.object(
                app,
                "_diagnostic_builder_dependencies",
                return_value=dependencies,
            ),
            patch.object(
                app.agent_diagnostics,
                "redact_diagnostic_value",
                return_value="${PROJECT_ROOT}/tests/demo.spec.ts",
            ) as redact,
        ):
            result = app.resolve_agent_failure_dependency(
                "redact_value"
            )("/private/project/tests/demo.spec.ts")

        self.assertEqual(
            result,
            "${PROJECT_ROOT}/tests/demo.spec.ts",
        )
        self.assertIs(
            redact.call_args.kwargs["dependencies"],
            dependencies,
        )

    def test_failure_handling_domain_does_not_import_flask_or_app(self):
        source_path = (
            app.APP_DIR
            / "test_plan_viewer"
            / "agent"
            / "failure_handling.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertTrue(
            {"app", "flask"}.isdisjoint(imported_roots),
            imported_roots,
        )

    def test_failure_workspace_exposes_all_item_actions(self):
        routes = route_methods()
        run_prefix = "/api/agent/runs/<id>"
        item_prefix = f"{run_prefix}/failure-items/<id>"

        expected = {
            (item_prefix, "GET"),
            (f"{item_prefix}/analyze", "POST"),
            (f"{item_prefix}/retry", "POST"),
            (f"{item_prefix}/execute", "POST"),
            (f"{item_prefix}/script", "GET"),
            (f"{item_prefix}/script", "PATCH"),
            (item_prefix, "DELETE"),
            (f"{item_prefix}/ignore", "POST"),
            (f"{run_prefix}/continue", "POST"),
        }

        self.assertTrue(
            expected.issubset(routes),
            f"缺少失败处置 API：{sorted(expected - routes)}",
        )

    def test_failure_checkpoint_waiting_statuses_are_supported(self):
        self.assertIn("awaiting_failure_action", app.AGENT_RUN_STATUSES)
        self.assertIn("awaiting_action", app.AGENT_STEP_STATUSES)
        self.assertIn(
            "awaiting_failure_action",
            app.AGENT_PAUSED_STATUSES,
        )
        self.assertIn(
            "succeeded_with_unresolved",
            app.AGENT_RUN_STATUSES,
        )
        self.assertIn(
            "succeeded_with_unresolved",
            app.AGENT_TERMINAL_STATUSES,
        )
        self.assertEqual(
            app.validate_agent_status("succeeded_with_unresolved"),
            "succeeded_with_unresolved",
        )

        current = app.serialize_agent_run(
            {
                "run_id": "current-run",
                "status": "awaiting_failure_action",
                "summary_json": "{}",
                "plan_generation_json": "{}",
            }
        )

        self.assertEqual(current["status"], "awaiting_failure_action")

    def test_historical_plan_review_step_remains_readable(self):
        historical = app.serialize_agent_step(
            {
                "run_id": "legacy-run",
                "step_key": "review_plans",
                "step_name": "计划审查",
                "status": "succeeded",
                "input_json": '{"plan_count": 1}',
                "output_json": (
                    '{"plans":[{"plan_filename":"登录.md"}],'
                    '"decisions":[{"action":"keep"}]}'
                ),
                "counts_json": '{"kept":1}',
            }
        )
        removed = app.serialize_agent_step(
            {
                "run_id": "current-run",
                "step_key": "review_plans",
                "step_name": "计划审查",
                "status": "skipped",
                "input_json": "{}",
                "output_json": (
                    '{"reason":"removed_in_failure_checkpoint_v2"}'
                ),
                "counts_json": "{}",
            }
        )

        self.assertEqual(historical["status"], "succeeded")
        self.assertEqual(
            historical["output"]["decisions"][0]["action"],
            "keep",
        )
        self.assertEqual(removed["status"], "skipped")
        self.assertEqual(
            removed["output"]["reason"],
            "removed_in_failure_checkpoint_v2",
        )

    def test_new_pipeline_skips_plan_review_without_deleting_legacy_step(self):
        plans = [
            {
                "module_name": "登录",
                "plan_filename": "登录成功.md",
            }
        ]
        with (
            patch.object(app, "update_agent_step") as update_step,
            patch.object(app, "append_agent_event") as append_event,
        ):
            result = app.skip_agent_plan_review("agent-1", plans)

        self.assertEqual(result, plans)
        update_step.assert_called_once()
        self.assertEqual(update_step.call_args.args[:2], (
            "agent-1",
            "review_plans",
        ))
        self.assertEqual(
            update_step.call_args.kwargs["status"],
            "skipped",
        )
        self.assertEqual(
            update_step.call_args.kwargs["output_data"]["reason"],
            "removed_in_failure_checkpoint_v2",
        )
        self.assertEqual(
            update_step.call_args.kwargs["output_data"]["plans"],
            plans,
        )
        append_event.assert_called_once()

    def test_reviewer_parser_accepts_a_single_decision_object(self):
        decision = {
            "module_name": "登录",
            "plan_filename": "登录成功.md",
            "action": "keep",
            "reason": "计划有效。",
        }

        self.assertEqual(
            app.normalize_reviewer_decisions(decision, ["plans"]),
            [decision],
        )

    def test_run_detail_preserves_failure_groups_and_items(self):
        generation_failure = {
            "failure_item_id": "generation-1",
            "source_stage": "generate_scripts",
            "status": "unresolved",
        }
        repair_failure = {
            "failure_item_id": "repair-1",
            "source_stage": "repair_scripts",
            "status": "unresolved",
        }
        step = {
            "run_id": "agent-1",
            "step_key": "review_failed_scripts",
            "step_name": "失败分析与处置",
            "status": "awaiting_action",
            "input_json": "{}",
            "output_json": app.compact_json_dumps(
                {
                    "version": 2,
                    "failure_items": [
                        generation_failure,
                        repair_failure,
                    ],
                    "generation_failures": [generation_failure],
                    "repair_failures": [repair_failure],
                    "scripts": [],
                    "unresolved_count": 2,
                    "resolved_count": 0,
                }
            ),
            "counts_json": '{"failed":2}',
        }
        run = {
            "run_id": "agent-1",
            "status": "awaiting_failure_action",
            "current_step": "review_failed_scripts",
            "summary_json": "{}",
            "plan_generation_json": "{}",
        }
        client = app.app.test_client()

        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(app, "get_agent_run_row", return_value=run),
            patch.object(app, "list_agent_steps", return_value=[step]),
            patch.object(
                app,
                "list_agent_item_retry_flows",
                return_value=[],
            ),
        ):
            response = client.get("/api/agent/runs/agent-1")

        self.assertEqual(response.status_code, 200)
        output = next(
            item["output"]
            for item in response.get_json()["steps"]
            if item["step_key"] == "review_failed_scripts"
        )
        self.assertEqual(
            [item["failure_item_id"] for item in output["failure_items"]],
            ["generation-1", "repair-1"],
        )
        self.assertEqual(
            [item["item_id"] for item in output["generation_failures"]],
            ["generation-1"],
        )
        self.assertEqual(
            [item["item_id"] for item in output["repair_failures"]],
            ["repair-1"],
        )
        self.assertEqual(
            output["generation_failures"][0]["source_step"],
            "generate_scripts",
        )
        self.assertEqual(
            output["repair_failures"][0]["source_step"],
            "repair_scripts",
        )
        self.assertEqual(output["unresolved_count"], 2)


class AgentFailureCheckpointBehaviorTests(unittest.TestCase):
    def test_failure_evidence_redaction_fails_closed(self):
        secret = "Bearer platform-secret-token"
        with patch.object(
            failure_domain.agent_diagnostics,
            "redact_diagnostic_value",
            side_effect=RuntimeError("redactor unavailable"),
        ):
            redacted_mapping = (
                failure_domain.redact_agent_failure_value(
                    {"authorization": secret}
                )
            )
            redacted_list = failure_domain.redact_agent_failure_value(
                [{"authorization": secret}]
            )
            redacted_text = failure_domain.redact_agent_failure_value(
                secret
            )

        self.assertEqual(
            redacted_mapping,
            {"redaction_failed": True},
        )
        self.assertEqual(redacted_list, [])
        self.assertEqual(redacted_text, "[已隐藏]")
        self.assertNotIn(
            secret,
            repr((redacted_mapping, redacted_list, redacted_text)),
        )

    def test_all_generation_failures_pause_at_failure_checkpoint(self):
        requirement = {
            "requirement_uid": "requirement-1",
            "title": "登录需求",
        }
        plan = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
        }
        failure = {
            **plan,
            "filename": "登录失败.spec.ts",
            "error": "生成失败",
        }
        checkpoint_item = {
            "item_id": "generation-1",
            "source_type": "generation",
            "status": "unresolved",
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(app, "agent_register_task")
            )
            stack.enter_context(
                patch.object(app, "agent_cleanup_task")
            )
            stack.enter_context(
                patch.object(
                    app,
                    "use_project_context",
                    side_effect=lambda _project: nullcontext(),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "use_author_context",
                    side_effect=lambda _author: nullcontext(),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "get_agent_run_row",
                    return_value={
                        "run_id": "agent-1",
                        "requirement_uid": "requirement-1",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "get_requirement_by_uid",
                    return_value=requirement,
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "serialize_requirement",
                    return_value=requirement,
                )
            )
            for name in (
                "update_agent_run",
                "update_agent_step",
                "append_agent_event",
                "agent_set_current_job",
            ):
                stack.enter_context(patch.object(app, name))
            stack.enter_context(
                patch.object(
                    app,
                    "agent_analyze_requirement",
                    return_value=[{"module_name": "登录"}],
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_review_modules",
                    return_value=[{"module_name": "登录"}],
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_generate_plans",
                    return_value=[plan],
                )
            )
            skip_review = stack.enter_context(
                patch.object(
                    app,
                    "skip_agent_plan_review",
                    return_value=[plan],
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_generate_scripts",
                    return_value=([], [failure]),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_execute_generated_scripts",
                    return_value=([], []),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_repair_scripts",
                    return_value=([], []),
                )
            )
            prepare_checkpoint = stack.enter_context(
                patch.object(
                    app,
                    "prepare_agent_failure_checkpoint",
                    return_value=[checkpoint_item],
                )
            )
            create_suite = stack.enter_context(
                patch.object(app, "agent_create_suite")
            )
            run_suite = stack.enter_context(
                patch.object(app, "agent_run_suite")
            )
            app.run_agent_workflow(
                "agent-1",
                {"project_key": "demo"},
                "admin",
            )

        skip_review.assert_called_once_with("agent-1", [plan])
        prepare_checkpoint.assert_called_once_with(
            "agent-1",
            [failure],
            [],
        )
        create_suite.assert_not_called()
        run_suite.assert_not_called()

    def test_checkpoint_separates_generation_and_repair_failures(self):
        generated_failure = {
            "attempt_id": "attempt-generation",
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "error_type": "agent",
            "error": "模型没有生成脚本",
        }
        repair_failure = {
            "attempt_id": "attempt-repair",
            "module_name": "支付",
            "plan_filename": "支付失败.md",
            "filename": "支付失败.spec.ts",
            "error_type": "execution",
            "error": "修复后仍执行失败",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repair_script = (
                root / "tests" / "支付" / "支付失败.spec.ts"
            )
            repair_script.parent.mkdir(parents=True)
            repair_script.write_text(
                "import { test } from '@playwright/test';",
                encoding="utf-8",
            )

            def script_file(module_name, filename):
                return root / "tests" / module_name / filename

            with (
                patch.object(failure_domain, "agent_start_step"),
                patch.object(
                    failure_domain,
                    "get_script_file",
                    side_effect=script_file,
                ),
                patch.object(
                    failure_domain,
                    "redact_agent_failure_value",
                    side_effect=lambda value: value,
                ),
                patch.object(
                    failure_domain,
                    "get_agent_attempt",
                    return_value=None,
                ),
                patch.object(
                    failure_domain,
                    "get_test_job",
                    return_value=None,
                ),
                patch.object(
                    failure_domain,
                    "get_agent_run_row",
                    return_value={"summary_json": "{}"},
                ),
                patch.object(
                    failure_domain,
                    "update_agent_step",
                ) as update_step,
                patch.object(
                    failure_domain,
                    "update_agent_run",
                ) as update_run,
                patch.object(failure_domain, "append_agent_event"),
            ):
                items = failure_domain.prepare_agent_failure_checkpoint(
                    "agent-1",
                    [generated_failure],
                    [repair_failure],
                )

        self.assertEqual(
            [item["source_type"] for item in items],
            ["generation", "repair"],
        )
        generation_item, repair_item = items
        self.assertFalse(generation_item["script_exists"])
        self.assertFalse(generation_item["can_edit"])
        self.assertFalse(generation_item["can_execute"])
        self.assertFalse(generation_item["can_delete"])
        self.assertTrue(repair_item["script_exists"])
        self.assertTrue(repair_item["can_edit"])
        self.assertTrue(repair_item["can_execute"])
        self.assertTrue(repair_item["can_delete"])

        checkpoint_update = update_step.call_args
        self.assertEqual(
            checkpoint_update.kwargs["status"],
            "awaiting_action",
        )
        output = checkpoint_update.kwargs["output_data"]
        self.assertEqual(output["generation_failures"], [generation_item])
        self.assertEqual(output["repair_failures"], [repair_item])
        self.assertEqual(output["unresolved_count"], 2)
        self.assertEqual(output["resolved_count"], 0)
        self.assertEqual(
            update_run.call_args.kwargs["status"],
            "awaiting_failure_action",
        )

    def test_analysis_uses_cache_until_evidence_becomes_stale(self):
        cached = {
            "item_id": "failure-1",
            "source_type": "generation",
            "status": "unresolved",
            "error": "生成失败",
            "evidence_hash": "evidence-v1",
            "analysis": {"summary": "缓存分析"},
            "analysis_version": 1,
            "analysis_evidence_hash": "evidence-v1",
            "analysis_stale": False,
        }
        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=cached,
            ),
            patch.object(
                failure_domain,
                "call_agent_failure_analyst",
            ) as reviewer,
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
            ) as mutate,
        ):
            result = failure_domain.analyze_agent_failure_item(
                "agent-1",
                "failure-1",
            )

        self.assertIs(result, cached)
        reviewer.assert_not_called()
        mutate.assert_not_called()

        stale = {
            **cached,
            "evidence_hash": "evidence-v2",
            "analysis_stale": True,
        }
        checkpoint = {"failure_items": [stale]}

        def mutate_checkpoint(_run_id, mutator):
            result = mutator(checkpoint)
            return result, checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=stale,
            ),
            patch.object(
                failure_domain,
                "call_agent_failure_analyst",
                return_value={
                    "summary": "重新分析后的原因",
                    "root_cause_category": "model",
                    "confidence": 0.8,
                    "facts": ["模型没有生成目标文件"],
                    "recommended_action": "regenerate",
                    "suggestion": "缩小生成范围",
                },
            ) as reviewer,
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ),
            patch.object(
                failure_domain,
                "refresh_agent_failure_item_capabilities",
                side_effect=lambda item: item,
            ),
        ):
            refreshed = failure_domain.analyze_agent_failure_item(
                "agent-1",
                "failure-1",
            )

        reviewer.assert_called_once()
        self.assertNotIn(
            "prefer keep",
            repr(reviewer.call_args).lower(),
        )
        self.assertEqual(
            reviewer.call_args.args[3]["kind"],
            "failure_analysis",
        )
        self.assertEqual(
            refreshed["analysis"]["summary"],
            "重新分析后的原因",
        )
        self.assertEqual(refreshed["analysis_version"], 2)
        self.assertEqual(
            refreshed["analysis_evidence_hash"],
            "evidence-v2",
        )
        self.assertFalse(refreshed["analysis_stale"])
        self.assertEqual(refreshed["status"], "unresolved")

    def test_retry_attempt_creation_failure_rolls_back_claim(self):
        claimed = {
            "item_id": "generation-1",
            "source_step": "generate_scripts",
            "source_type": "generation",
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "status": "retrying",
            "source_failure": {},
        }
        failure = RuntimeError("创建重试 attempt 失败")

        with (
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                return_value=(claimed, "unresolved"),
            ),
            patch.object(
                failure_domain,
                "start_agent_attempt",
                side_effect=failure,
            ),
            patch.object(
                failure_domain,
                "rollback_agent_failure_item_action",
            ) as rollback,
            patch.object(
                failure_domain,
                "agent_generate_script_for_plan",
            ) as generate,
            self.assertRaisesRegex(RuntimeError, "创建重试 attempt 失败"),
        ):
            failure_domain.retry_agent_failure_item(
                "agent-1",
                "generation-1",
            )

        rollback.assert_called_once_with(
            "agent-1",
            claimed,
            "retrying",
            "unresolved",
            "retry",
            failure,
        )
        generate.assert_not_called()

    def test_retry_produces_a_script_that_still_requires_verification(self):
        claimed = {
            "item_id": "generation-1",
            "source_step": "generate_scripts",
            "source_type": "generation",
            "root_attempt_id": "attempt-root",
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "status": "retrying",
            "included_in_suite": False,
            "source_failure": {
                "module_name": "登录",
                "plan_filename": "登录失败.md",
            },
            "evidence_snapshot": [],
            "evidence_version": 1,
            "analysis": {"summary": "生成范围过宽"},
        }
        generated = {
            "module_name": "登录",
            "plan_filename": "登录失败.md",
            "filename": "登录失败.spec.ts",
            "job_id": "generator-retry",
            "asset": {
                "asset_id": 20,
                "current_revision_id": 30,
            },
        }

        def finish_action(
            _run_id,
            item,
            expected_status,
            final_status,
        ):
            self.assertEqual(expected_status, "retrying")
            return {**item, "status": final_status}

        with (
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                return_value=(claimed, "unresolved"),
            ),
            patch.object(
                failure_domain,
                "start_agent_attempt",
                return_value={"attempt_id": "attempt-retry"},
            ),
            patch.object(
                failure_domain,
                "agent_generate_script_for_plan",
                return_value=generated,
            ) as generate,
            patch.object(failure_domain, "finish_agent_attempt"),
            patch.object(
                failure_domain,
                "redact_agent_failure_value",
                side_effect=lambda value: value,
            ),
            patch.object(
                failure_domain,
                "finish_agent_failure_item_action",
                side_effect=finish_action,
            ),
            patch.object(
                failure_domain,
                "agent_execute_single_script_for_review",
            ) as execute,
        ):
            result = failure_domain.retry_agent_failure_item(
                "agent-1",
                "generation-1",
                instructions="只生成目标脚本",
            )

        self.assertEqual(result["status"], "pending_verification")
        self.assertFalse(result["included_in_suite"])
        self.assertEqual(
            result["latest_attempt"]["verification_status"]
            if "verification_status" in result["latest_attempt"]
            else "not_run",
            "not_run",
        )
        self.assertTrue(result["analysis_stale"])
        self.assertEqual(result["evidence_version"], 2)
        self.assertEqual(result["current_script"]["attempt_id"], "attempt-retry")
        self.assertEqual(
            generate.call_args.kwargs["instructions"],
            "只生成目标脚本",
        )
        execute.assert_not_called()

    def test_failed_retry_refreshes_candidate_artifact_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = (
                root / "tests" / "_partial" / "登录失败.spec.ts"
            )
            candidate.parent.mkdir(parents=True)
            candidate.write_text(
                "import { test } from '@playwright/test';",
                encoding="utf-8",
            )
            formal_script = (
                root / "tests" / "登录" / "登录失败.spec.ts"
            )
            claimed = {
                "item_id": "generation-1",
                "source_step": "generate_scripts",
                "source_type": "generation",
                "status": "retrying",
                "module_name": "登录",
                "plan_filename": "登录失败.md",
                "filename": "登录失败.spec.ts",
                "source_failure": {},
                "evidence_snapshot": [],
                "evidence_version": 1,
                "analysis": {"summary": "首次生成未产出正式脚本"},
            }
            failure_context = {
                "job_id": "job-retry",
                "test_run_id": "",
                "result_id": None,
                "asset_id": None,
                "error_type": "agent",
                "partial_artifacts": [str(candidate)],
            }

            def finish_action(
                _run_id,
                item,
                expected_status,
                final_status,
            ):
                self.assertEqual(expected_status, "retrying")
                self.assertEqual(final_status, "unresolved")
                return (
                    failure_domain
                    .refresh_agent_failure_item_capabilities(
                        {**item, "status": final_status}
                    )
                )

            def redact_with_project_marker(value):
                if isinstance(value, dict):
                    return {
                        key: redact_with_project_marker(item)
                        for key, item in value.items()
                    }
                if isinstance(value, list):
                    return [
                        redact_with_project_marker(item)
                        for item in value
                    ]
                text = str(value) if isinstance(value, Path) else value
                if isinstance(text, str):
                    for prefix in (
                        str(root.resolve(strict=False)),
                        str(root),
                    ):
                        text = text.replace(prefix, "${PROJECT_ROOT}")
                    return text
                return value

            with (
                patch.object(
                    failure_domain,
                    "claim_agent_failure_item",
                    return_value=(claimed, "unresolved"),
                ),
                patch.object(
                    failure_domain,
                    "start_agent_attempt",
                    return_value={"attempt_id": "attempt-retry"},
                ),
                patch.object(
                    failure_domain,
                    "agent_generate_script_for_plan",
                    side_effect=RuntimeError("生成器中断"),
                ),
                patch.object(
                    failure_domain,
                    "agent_attempt_failure_context",
                    return_value=failure_context,
                ),
                patch.object(
                    failure_domain,
                    "finish_agent_attempt",
                ) as finish_attempt,
                patch.object(
                    failure_domain,
                    "redact_agent_failure_value",
                    side_effect=redact_with_project_marker,
                ),
                patch.object(
                    failure_domain,
                    "get_script_file",
                    return_value=formal_script,
                ),
                patch.object(
                    failure_domain,
                    "get_project_root",
                    return_value=root,
                ),
                patch.object(
                    failure_domain,
                    "finish_agent_failure_item_action",
                    side_effect=finish_action,
                ),
                patch.object(
                    failure_domain,
                    "agent_set_current_job",
                ),
                patch.object(
                    failure_domain,
                    "agent_cleanup_task",
                ),
            ):
                result = failure_domain.retry_agent_failure_item(
                    "agent-1",
                    "generation-1",
                )

        self.assertEqual(result["status"], "unresolved")
        self.assertTrue(result["candidate_exists"])
        redacted_candidate = (
            "${PROJECT_ROOT}/tests/_partial/登录失败.spec.ts"
        )
        self.assertEqual(result["candidate_path"], redacted_candidate)
        self.assertEqual(
            result["editable_artifact_kind"],
            "candidate",
        )
        self.assertTrue(result["can_edit"])
        self.assertFalse(result["can_execute"])
        self.assertFalse(result["can_delete"])
        self.assertEqual(
            result["source_failure"]["partial_artifacts"],
            [redacted_candidate],
        )
        self.assertEqual(
            finish_attempt.call_args.kwargs["artifact_refs"],
            [{"source": "partial", "path": str(candidate)}],
        )

    def test_execution_success_resolves_and_includes_the_script(self):
        claimed = {
            "item_id": "repair-1",
            "source_step": "repair_scripts",
            "source_type": "repair",
            "root_attempt_id": "attempt-root",
            "module_name": "支付",
            "plan_filename": "支付失败.md",
            "filename": "支付失败.spec.ts",
            "status": "executing",
            "can_execute": True,
            "included_in_suite": False,
            "current_script": {
                "module_name": "支付",
                "filename": "支付失败.spec.ts",
            },
            "evidence_snapshot": [],
            "evidence_version": 2,
            "analysis": {"summary": "定位器失效"},
        }
        execution_result = {
            "ok": True,
            "status": "succeeded",
            "run_id": "test-run-1",
            "result_id": 42,
        }

        def finish_action(
            _run_id,
            item,
            expected_status,
            final_status,
        ):
            self.assertEqual(expected_status, "executing")
            return {**item, "status": final_status}

        with (
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                return_value=(claimed, "pending_verification"),
            ),
            patch.object(
                failure_domain,
                "start_agent_attempt",
                return_value={"attempt_id": "attempt-verify"},
            ),
            patch.object(
                failure_domain,
                "agent_execute_single_script_for_review",
                return_value=execution_result,
            ) as execute,
            patch.object(
                failure_domain,
                "finish_agent_attempt",
            ) as finish_attempt,
            patch.object(
                failure_domain,
                "redact_agent_failure_value",
                side_effect=lambda value: value,
            ),
            patch.object(
                failure_domain,
                "finish_agent_failure_item_action",
                side_effect=finish_action,
            ),
        ):
            result = failure_domain.execute_agent_failure_item(
                "agent-1",
                "repair-1",
            )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolution"], "verified")
        self.assertTrue(result["included_in_suite"])
        self.assertEqual(
            result["latest_attempt"]["verification_status"],
            "passed",
        )
        self.assertTrue(result["analysis_stale"])
        execute.assert_called_once()
        self.assertEqual(
            finish_attempt.call_args.kwargs["verification_status"],
            "passed",
        )

    def test_execute_attempt_creation_failure_rolls_back_claim(self):
        claimed = {
            "item_id": "repair-1",
            "source_step": "repair_scripts",
            "source_type": "repair",
            "module_name": "支付",
            "plan_filename": "支付失败.md",
            "filename": "支付失败.spec.ts",
            "status": "executing",
            "can_execute": True,
            "current_script": {
                "module_name": "支付",
                "filename": "支付失败.spec.ts",
            },
        }
        failure = RuntimeError("创建执行 attempt 失败")

        with (
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                return_value=(claimed, "pending_verification"),
            ),
            patch.object(
                failure_domain,
                "start_agent_attempt",
                side_effect=failure,
            ),
            patch.object(
                failure_domain,
                "rollback_agent_failure_item_action",
            ) as rollback,
            patch.object(
                failure_domain,
                "agent_execute_single_script_for_review",
            ) as execute,
            self.assertRaisesRegex(RuntimeError, "创建执行 attempt 失败"),
        ):
            failure_domain.execute_agent_failure_item(
                "agent-1",
                "repair-1",
            )

        rollback.assert_called_once_with(
            "agent-1",
            claimed,
            "executing",
            "pending_verification",
            "execute",
            failure,
        )
        execute.assert_not_called()

    def test_delete_requires_a_script_while_ignore_handles_no_artifact(self):
        no_script = {
            "item_id": "generation-1",
            "status": "unresolved",
            "module_name": "登录",
            "filename": "登录失败.spec.ts",
            "can_delete": False,
            "evidence_snapshot": [],
        }
        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=no_script,
            ),
            patch.object(
                failure_domain,
                "delete_script_asset",
            ) as delete_asset,
            self.assertRaisesRegex(
                failure_domain.AgentFailureCheckpointConflict,
                "没有可归档删除",
            ),
        ):
            failure_domain.delete_agent_failure_item(
                "agent-1",
                "generation-1",
            )
        delete_asset.assert_not_called()

        checkpoint = {"failure_items": [no_script]}

        def mutate_checkpoint(_run_id, mutator):
            result = mutator(checkpoint)
            return result, checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=no_script,
            ),
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ),
            patch.object(
                failure_domain,
                "redact_agent_failure_value",
                side_effect=lambda value: value,
            ),
            patch.object(
                failure_domain,
                "refresh_agent_failure_item_capabilities",
                side_effect=lambda item: item,
            ),
        ):
            ignored = failure_domain.ignore_agent_failure_item(
                "agent-1",
                "generation-1",
            )

        self.assertEqual(ignored["status"], "ignored")
        self.assertEqual(ignored["resolution"], "ignored")
        self.assertFalse(ignored["included_in_suite"])

    def test_delete_archives_existing_script_and_keeps_history(self):
        item = {
            "item_id": "repair-1",
            "status": "unresolved",
            "module_name": "支付",
            "filename": "支付失败.spec.ts",
            "can_delete": True,
            "evidence_snapshot": [],
            "evidence_version": 1,
        }
        checkpoint = {"failure_items": [item]}

        def mutate_checkpoint(_run_id, mutator):
            result = mutator(checkpoint)
            return result, checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=item,
            ),
            patch.object(
                failure_domain,
                "delete_script_asset",
                return_value={
                    "archive": {"path": ".trash/支付失败.spec.ts"},
                    "asset": {"asset_id": 20},
                },
            ) as delete_asset,
            patch.object(
                failure_domain,
                "serialize_asset",
                side_effect=lambda asset: asset,
            ),
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ),
            patch.object(
                failure_domain,
                "redact_agent_failure_value",
                side_effect=lambda value: value,
            ),
            patch.object(
                failure_domain,
                "refresh_agent_failure_item_capabilities",
                side_effect=lambda value: value,
            ),
        ):
            deleted = failure_domain.delete_agent_failure_item(
                "agent-1",
                "repair-1",
            )

        delete_asset.assert_called_once()
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual(deleted["resolution"], "archived")
        self.assertIsNone(deleted["current_script"])
        self.assertFalse(deleted["included_in_suite"])
        self.assertEqual(
            deleted["evidence_snapshot"][-1]["data"]["archive"]["path"],
            ".trash/支付失败.spec.ts",
        )

    def test_edit_claims_item_before_writing_script(self):
        with tempfile.TemporaryDirectory() as directory:
            script_file = Path(directory) / "支付失败.spec.ts"
            script_file.write_text(
                "test('旧脚本', async () => {});",
                encoding="utf-8",
            )
            item = {
                "item_id": "repair-1",
                "status": "unresolved",
                "module_name": "支付",
                "plan_filename": "支付失败.md",
                "filename": script_file.name,
                "script_exists": True,
                "script_path": str(script_file),
                "candidate_exists": False,
                "evidence_snapshot": [],
                "evidence_version": 1,
            }
            events = []
            original_write_text = Path.write_text

            def claim_item(*_args, **_kwargs):
                events.append("claim")
                return ({**item, "status": "editing"}, "unresolved")

            def record_write(path, *args, **kwargs):
                events.append("write")
                return original_write_text(path, *args, **kwargs)

            def finish_action(
                _run_id,
                updated,
                expected_status,
                final_status,
            ):
                self.assertEqual(expected_status, "editing")
                return {**updated, "status": final_status}

            with (
                patch.object(
                    failure_domain,
                    "get_agent_failure_item",
                    return_value=item,
                ),
                patch.object(
                    failure_domain,
                    "claim_agent_failure_item",
                    side_effect=claim_item,
                ),
                patch.object(
                    failure_domain,
                    "get_script_file",
                    return_value=script_file,
                ),
                patch.object(
                    Path,
                    "write_text",
                    new=record_write,
                ),
                patch.object(
                    failure_domain,
                    "sync_script_asset",
                    return_value={"asset_id": 10},
                ),
                patch.object(
                    failure_domain,
                    "serialize_asset",
                    side_effect=lambda asset: asset,
                ),
                patch.object(
                    failure_domain,
                    "finish_agent_failure_item_action",
                    side_effect=finish_action,
                ),
            ):
                saved = failure_domain.save_agent_failure_item_script(
                    "agent-1",
                    "repair-1",
                    "test('新脚本', async () => {});",
                )

        self.assertEqual(events[:2], ["claim", "write"])
        self.assertEqual(saved["status"], "pending_verification")

    def test_edit_path_failure_rolls_back_claim(self):
        item = {
            "item_id": "repair-1",
            "status": "unresolved",
            "module_name": "支付",
            "plan_filename": "支付失败.md",
            "filename": "支付失败.spec.ts",
            "script_exists": True,
            "candidate_exists": False,
        }
        claimed = {**item, "status": "editing"}
        failure = RuntimeError("解析脚本路径失败")

        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=item,
            ),
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                return_value=(claimed, "unresolved"),
            ),
            patch.object(
                failure_domain,
                "get_script_file",
                side_effect=failure,
            ),
            patch.object(
                failure_domain,
                "rollback_agent_failure_item_action",
            ) as rollback,
            self.assertRaisesRegex(RuntimeError, "解析脚本路径失败"),
        ):
            failure_domain.save_agent_failure_item_script(
                "agent-1",
                "repair-1",
                "test('新脚本', async () => {});",
            )

        rollback.assert_called_once_with(
            "agent-1",
            claimed,
            "editing",
            "unresolved",
            "edit",
            failure,
        )

    def test_edit_with_embedded_secret_restores_original_script(self):
        with tempfile.TemporaryDirectory() as directory:
            script_file = Path(directory) / "支付失败.spec.ts"
            original_content = "test('当前脚本', async () => {});"
            script_file.write_text(original_content, encoding="utf-8")
            item = {
                "item_id": "repair-1",
                "status": "unresolved",
                "module_name": "支付",
                "plan_filename": "支付失败.md",
                "filename": script_file.name,
                "script_exists": True,
                "candidate_exists": False,
            }
            claimed = {**item, "status": "editing"}

            with (
                patch.object(
                    failure_domain,
                    "get_agent_failure_item",
                    return_value=item,
                ),
                patch.object(
                    failure_domain,
                    "claim_agent_failure_item",
                    return_value=(claimed, "unresolved"),
                ),
                patch.object(
                    failure_domain,
                    "get_script_file",
                    return_value=script_file,
                ),
                patch.object(
                    failure_domain,
                    "sync_script_asset",
                    side_effect=app.sync_script_asset,
                ),
                patch.object(
                    failure_domain,
                    "rollback_agent_failure_item_action",
                ) as rollback,
                self.assertRaisesRegex(ValueError, "plaintext credential"),
            ):
                failure_domain.save_agent_failure_item_script(
                    "agent-1",
                    "repair-1",
                    "const password = 'Hardcoded-Password-1!';",
                )

            self.assertEqual(
                script_file.read_text(encoding="utf-8"),
                original_content,
            )

        rollback.assert_called_once()

    def test_edit_read_failure_rolls_back_without_overwriting_script(self):
        with tempfile.TemporaryDirectory() as directory:
            script_file = Path(directory) / "支付失败.spec.ts"
            original_content = "test('当前脚本', async () => {});"
            script_file.write_text(original_content, encoding="utf-8")
            item = {
                "item_id": "repair-1",
                "status": "unresolved",
                "module_name": "支付",
                "plan_filename": "支付失败.md",
                "filename": script_file.name,
                "script_exists": True,
                "candidate_exists": False,
            }
            claimed = {**item, "status": "editing"}
            failure = OSError("读取现有脚本失败")

            with (
                patch.object(
                    failure_domain,
                    "get_agent_failure_item",
                    return_value=item,
                ),
                patch.object(
                    failure_domain,
                    "claim_agent_failure_item",
                    return_value=(claimed, "unresolved"),
                ),
                patch.object(
                    failure_domain,
                    "get_script_file",
                    return_value=script_file,
                ),
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=failure,
                ),
                patch.object(
                    failure_domain,
                    "rollback_agent_failure_item_action",
                ) as rollback,
                patch.object(
                    failure_domain,
                    "sync_script_asset",
                ) as sync_asset,
                self.assertRaisesRegex(OSError, "读取现有脚本失败"),
            ):
                failure_domain.save_agent_failure_item_script(
                    "agent-1",
                    "repair-1",
                    "test('不应写入', async () => {});",
                )

            self.assertEqual(
                script_file.read_text(encoding="utf-8"),
                original_content,
            )

        rollback.assert_called_once_with(
            "agent-1",
            claimed,
            "editing",
            "unresolved",
            "edit",
            failure,
        )
        sync_asset.assert_not_called()

    def test_edit_hash_conflict_does_not_overwrite_script(self):
        with tempfile.TemporaryDirectory() as directory:
            script_file = Path(directory) / "支付失败.spec.ts"
            original_content = "test('当前版本', async () => {});"
            script_file.write_text(original_content, encoding="utf-8")
            item = {
                "item_id": "repair-1",
                "status": "unresolved",
                "module_name": "支付",
                "plan_filename": "支付失败.md",
                "filename": script_file.name,
                "script_exists": True,
                "script_path": str(script_file),
                "candidate_exists": False,
                "evidence_snapshot": [],
                "evidence_version": 1,
            }
            writes = []
            original_write_text = Path.write_text

            def record_write(path, *args, **kwargs):
                writes.append(str(path))
                return original_write_text(path, *args, **kwargs)

            with (
                patch.object(
                    failure_domain,
                    "get_agent_failure_item",
                    return_value=item,
                ),
                patch.object(
                    failure_domain,
                    "claim_agent_failure_item",
                    return_value=(
                        {**item, "status": "editing"},
                        "unresolved",
                    ),
                ) as claim_item,
                patch.object(
                    failure_domain,
                    "get_script_file",
                    return_value=script_file,
                ),
                patch.object(
                    Path,
                    "write_text",
                    new=record_write,
                ),
                patch.object(
                    failure_domain,
                    "sync_script_asset",
                ) as sync_asset,
                patch.object(
                    failure_domain,
                    "rollback_agent_failure_item_action",
                ) as rollback,
                self.assertRaisesRegex(
                    failure_domain.AgentFailureCheckpointConflict,
                    "脚本内容已被其他操作修改",
                ),
            ):
                failure_domain.save_agent_failure_item_script(
                    "agent-1",
                    "repair-1",
                    "test('过期编辑', async () => {});",
                    expected_content_sha256="stale-content-hash",
                )

            self.assertEqual(
                script_file.read_text(encoding="utf-8"),
                original_content,
            )

        claim_item.assert_called_once()
        self.assertEqual(writes, [])
        sync_asset.assert_not_called()
        rollback.assert_called_once()

    def test_delete_claims_item_before_archiving_script(self):
        item = {
            "item_id": "repair-1",
            "status": "unresolved",
            "module_name": "支付",
            "filename": "支付失败.spec.ts",
            "can_delete": True,
            "evidence_snapshot": [],
            "evidence_version": 1,
        }
        events = []

        def claim_item(*_args, **_kwargs):
            events.append("claim")
            return ({**item, "status": "deleting"}, "unresolved")

        def delete_asset(*_args, **_kwargs):
            events.append("delete")
            return {
                "archive": {"path": ".trash/支付失败.spec.ts"},
                "asset": {"asset_id": 20},
            }

        with (
            patch.object(
                failure_domain,
                "get_agent_failure_item",
                return_value=item,
            ),
            patch.object(
                failure_domain,
                "claim_agent_failure_item",
                side_effect=claim_item,
            ),
            patch.object(
                failure_domain,
                "delete_script_asset",
                side_effect=delete_asset,
            ),
            patch.object(
                failure_domain,
                "serialize_asset",
                side_effect=lambda asset: asset,
            ),
            patch.object(
                failure_domain,
                "finish_agent_failure_item_action",
                side_effect=lambda _run_id, updated, _expected, final: {
                    **updated,
                    "status": final,
                },
            ),
        ):
            result = failure_domain.delete_agent_failure_item(
                "agent-1",
                "repair-1",
            )

        self.assertEqual(events, ["claim", "delete"])
        self.assertEqual(result["status"], "deleted")

    def test_continue_keeps_unresolved_items_and_records_coverage_gap(self):
        checkpoint = {
            "failure_items": [
                {
                    "item_id": "generation-1",
                    "source_step": "generate_scripts",
                    "source_type": "generation",
                    "status": "unresolved",
                    "module_name": "登录",
                    "plan_filename": "登录失败.md",
                    "filename": "登录失败.spec.ts",
                    "error": "生成失败",
                }
            ],
            "version": 1,
        }
        executable = {
            "module_name": "支付",
            "filename": "支付成功.spec.ts",
        }

        def mutate_checkpoint(_run_id, mutator):
            result = mutator(checkpoint)
            normalized = (
                failure_domain
                .normalize_agent_failure_checkpoint_output(checkpoint)
            )
            checkpoint.clear()
            checkpoint.update(normalized)
            return result, checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_run_row",
                return_value={
                    "run_id": "agent-1",
                    "status": "awaiting_failure_action",
                    "summary_json": "{}",
                },
            ),
            patch.object(
                failure_domain,
                "get_agent_failure_checkpoint_output",
                return_value=checkpoint,
            ),
            patch.object(
                failure_domain,
                "list_agent_item_retry_flows",
                return_value=[],
            ),
            patch.object(
                failure_domain,
                "collect_agent_checkpoint_final_scripts",
                return_value=[executable],
            ),
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ),
            patch.object(
                failure_domain,
                "refresh_agent_failure_item_capabilities",
                side_effect=lambda item: item,
            ),
            patch.object(
                failure_domain,
                "update_agent_step",
            ) as update_step,
            patch.object(
                failure_domain,
                "update_agent_run",
            ) as update_run,
            patch.object(failure_domain, "append_agent_event"),
        ):
            context = (
                failure_domain
                .continue_agent_failure_checkpoint("agent-1")
            )

        self.assertEqual(context["final_scripts"], [executable])
        self.assertTrue(context["partial_success"])
        self.assertEqual(
            context["unresolved_items"][0]["status"],
            "kept_unresolved",
        )
        self.assertEqual(context["coverage_gap"]["count"], 1)
        self.assertEqual(
            update_step.call_args.kwargs["status"],
            "succeeded",
        )
        self.assertEqual(
            update_run.call_args.kwargs["status"],
            "running",
        )
        self.assertEqual(
            update_run.call_args.kwargs["current_step"],
            "create_suite",
        )
        self.assertTrue(
            update_run.call_args.kwargs["summary"]["partial_success"]
        )
        self.assertTrue(checkpoint["continuing"])
        self.assertIn("continue_claimed_at", checkpoint)

    def test_continuing_checkpoint_rejects_parallel_mutation(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "output_json": app.compact_json_dumps(
                {
                    "continuing": True,
                    "failure_items": [],
                }
            )
        }
        connection = MagicMock()
        connection.cursor.return_value = cursor
        mutator = MagicMock()

        with (
            patch.object(
                failure_domain,
                "require_platform_database",
                return_value={},
            ),
            patch.object(
                failure_domain,
                "get_agent_run_steps_table",
                return_value="agent_run_steps",
            ),
            patch.object(
                failure_domain,
                "get_current_project_id",
                return_value=7,
            ),
            patch.object(
                failure_domain,
                "platform_mysql_connection",
                return_value=nullcontext(connection),
            ),
            self.assertRaisesRegex(
                failure_domain.AgentFailureCheckpointConflict,
                "正在进入下一阶段",
            ),
        ):
            failure_domain.mutate_agent_failure_checkpoint(
                "agent-1",
                mutator,
            )

        mutator.assert_not_called()
        self.assertEqual(cursor.execute.call_count, 1)

    def test_cancelling_checkpoint_rejects_parallel_mutation(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "output_json": app.compact_json_dumps(
                {
                    "cancelling": True,
                    "failure_items": [],
                }
            )
        }
        connection = MagicMock()
        connection.cursor.return_value = cursor
        mutator = MagicMock()

        with (
            patch.object(
                failure_domain,
                "require_platform_database",
                return_value={},
            ),
            patch.object(
                failure_domain,
                "get_agent_run_steps_table",
                return_value="agent_run_steps",
            ),
            patch.object(
                failure_domain,
                "get_current_project_id",
                return_value=7,
            ),
            patch.object(
                failure_domain,
                "platform_mysql_connection",
                return_value=nullcontext(connection),
            ),
            self.assertRaisesRegex(
                failure_domain.AgentFailureCheckpointConflict,
                "正在取消任务",
            ),
        ):
            failure_domain.mutate_agent_failure_checkpoint(
                "agent-1",
                mutator,
            )

        mutator.assert_not_called()
        self.assertEqual(cursor.execute.call_count, 1)

    def test_cancel_checkpoint_claims_before_run_state_changes(self):
        checkpoint = {"failure_items": [], "version": 1}

        def mutate_checkpoint(_run_id, mutator, **_kwargs):
            return mutator(checkpoint), checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_run_row",
                return_value={
                    "run_id": "agent-1",
                    "status": "awaiting_failure_action",
                },
            ),
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ) as mutate,
            patch.object(
                failure_domain,
                "update_agent_step",
            ) as update_step,
            patch.object(
                failure_domain,
                "update_agent_run",
                return_value={
                    "run_id": "agent-1",
                    "status": "cancelled",
                },
            ) as update_run,
            patch.object(
                failure_domain,
                "append_agent_event",
            ),
        ):
            result = failure_domain.cancel_agent_failure_checkpoint(
                "agent-1",
            )

        self.assertTrue(checkpoint["cancelling"])
        self.assertIn("cancel_claimed_at", checkpoint)
        mutate.assert_called_once()
        self.assertEqual(
            update_step.call_args.kwargs["status"],
            "cancelled",
        )
        self.assertEqual(
            update_run.call_args.kwargs["status"],
            "cancelled",
        )
        self.assertEqual(result["status"], "cancelled")

    def test_resume_checkpoint_preserves_failure_history(self):
        checkpoint = {
            "cancelling": True,
            "cancel_claimed_at": 10,
            "failure_items": [
                {
                    "item_id": "generation-1",
                    "status": "unresolved",
                    "analysis": {"summary": "保留的分析"},
                    "evidence_snapshot": [{"evidence_id": "failure"}],
                }
            ],
        }

        def mutate_checkpoint(_run_id, mutator, **kwargs):
            self.assertTrue(kwargs["allow_continuing"])
            return mutator(checkpoint), checkpoint

        with (
            patch.object(
                failure_domain,
                "get_agent_run_row",
                return_value={
                    "run_id": "agent-1",
                    "status": "cancelled",
                    "current_step": "review_failed_scripts",
                },
            ),
            patch.object(
                failure_domain,
                "mutate_agent_failure_checkpoint",
                side_effect=mutate_checkpoint,
            ),
            patch.object(
                failure_domain,
                "update_agent_step",
            ) as update_step,
            patch.object(
                failure_domain,
                "update_agent_run",
                return_value={
                    "run_id": "agent-1",
                    "status": "awaiting_failure_action",
                },
            ) as update_run,
            patch.object(
                failure_domain,
                "append_agent_event",
            ),
        ):
            result = failure_domain.resume_agent_failure_checkpoint(
                "agent-1",
            )

        self.assertNotIn("cancelling", checkpoint)
        self.assertNotIn("cancel_claimed_at", checkpoint)
        self.assertEqual(
            checkpoint["failure_items"][0]["analysis"]["summary"],
            "保留的分析",
        )
        self.assertEqual(
            update_step.call_args.kwargs["output_data"],
            checkpoint,
        )
        self.assertTrue(update_step.call_args.kwargs["reopened"])
        self.assertEqual(
            update_run.call_args.kwargs["status"],
            "awaiting_failure_action",
        )
        self.assertTrue(update_run.call_args.kwargs["reopened"])
        self.assertEqual(result["status"], "awaiting_failure_action")

    def test_coverage_gap_is_rebuilt_from_kept_unresolved_items(self):
        with patch.object(
            failure_domain,
            "get_agent_failure_checkpoint_output",
            return_value={
                "failure_items": [
                    {
                        "item_id": "generation-1",
                        "status": "kept_unresolved",
                        "source_type": "generation",
                        "module_name": "登录",
                        "filename": "登录失败.spec.ts",
                        "error": "仍未生成",
                    },
                    {
                        "item_id": "repair-1",
                        "status": "resolved",
                    },
                ]
            },
        ):
            gap = failure_domain.get_agent_failure_coverage_gap(
                "agent-1",
            )

        self.assertEqual(gap["count"], 1)
        self.assertEqual(gap["items"][0]["item_id"], "generation-1")
        self.assertEqual(gap["items"][0]["error"], "仍未生成")

    def test_legacy_plan_review_resume_skips_removed_stage(self):
        plans = [
            {
                "module_name": "登录",
                "plan_filename": "登录成功.md",
            }
        ]
        script = {
            "module_name": "登录",
            "filename": "登录成功.spec.ts",
        }

        def list_output(_run_id, step_key, field_name):
            values = {
                ("review_modules", "modules"): [{"module_name": "登录"}],
                ("generate_plans", "plans"): plans,
            }
            return values[(step_key, field_name)]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(app, "agent_register_task")
            )
            stack.enter_context(
                patch.object(
                    app,
                    "use_project_context",
                    side_effect=lambda _project: nullcontext(),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "use_author_context",
                    side_effect=lambda _author: nullcontext(),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "get_agent_run_row",
                    return_value={
                        "run_id": "agent-1",
                        "requirement_uid": "requirement-1",
                        "summary_json": "{}",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "serialize_agent_run",
                    return_value={"pipeline_version": 1},
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "get_requirement_by_uid",
                    return_value={
                        "requirement_uid": "requirement-1",
                        "title": "登录",
                    },
                )
            )
            stack.enter_context(patch.object(app, "append_agent_event"))
            stack.enter_context(
                patch.object(
                    app,
                    "require_agent_step_list_output",
                    side_effect=list_output,
                )
            )
            skip_review = stack.enter_context(
                patch.object(
                    app,
                    "skip_agent_plan_review",
                    return_value=plans,
                )
            )
            legacy_review = stack.enter_context(
                patch.object(app, "agent_review_plans")
            )
            generate_scripts = stack.enter_context(
                patch.object(
                    app,
                    "agent_generate_scripts",
                    return_value=([script], []),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_execute_generated_scripts",
                    return_value=([script], []),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_repair_scripts",
                    return_value=([], []),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "prepare_agent_failure_checkpoint",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_create_suite",
                    return_value={"id": "suite-1", "items": [script]},
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "agent_run_suite",
                    return_value={"summary": {"passed": 1}},
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "get_agent_failure_coverage_gap",
                    return_value={"count": 0, "items": []},
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "serialize_requirement",
                    return_value={"requirement_uid": "requirement-1"},
                )
            )
            stack.enter_context(patch.object(app, "update_agent_run"))
            stack.enter_context(
                patch.object(app, "agent_set_current_job")
            )
            stack.enter_context(patch.object(app, "agent_cleanup_task"))
            app.run_agent_resume_workflow(
                "agent-1",
                {"key": "demo"},
                "tester",
                "review_plans",
            )

        skip_review.assert_called_once_with("agent-1", plans)
        legacy_review.assert_not_called()
        generate_scripts.assert_called_once_with("agent-1", plans)

    def test_downstream_resume_preserves_partial_success(self):
        script = {
            "module_name": "支付",
            "filename": "支付成功.spec.ts",
        }

        def list_output(_run_id, step_key, field_name):
            values = {
                ("review_modules", "modules"): [{"module_name": "支付"}],
                ("generate_plans", "plans"): [{"module_name": "支付"}],
                ("review_failed_scripts", "scripts"): [],
            }
            return values[(step_key, field_name)]

        def step_output(_run_id, step_key):
            return {
                "generate_scripts": {
                    "scripts": [script],
                    "failures": [],
                },
                "repair_scripts": {
                    "scripts": [],
                    "failures": [],
                },
            }[step_key]

        update_run = MagicMock()
        with (
            patch.object(app, "agent_register_task"),
            patch.object(
                app,
                "use_project_context",
                side_effect=lambda _project: nullcontext(),
            ),
            patch.object(
                app,
                "use_author_context",
                side_effect=lambda _author: nullcontext(),
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value={
                    "run_id": "agent-1",
                    "requirement_uid": "requirement-1",
                    "summary_json": '{"pipeline_version":2}',
                },
            ),
            patch.object(
                app,
                "get_requirement_by_uid",
                return_value={
                    "requirement_uid": "requirement-1",
                    "title": "支付",
                },
            ),
            patch.object(app, "append_agent_event"),
            patch.object(
                app,
                "require_agent_step_list_output",
                side_effect=list_output,
            ),
            patch.object(
                app,
                "get_agent_step_output",
                side_effect=step_output,
            ),
            patch.object(
                app,
                "get_optional_agent_step_output",
                return_value={
                    "scripts": [script],
                    "failures": [],
                },
            ),
            patch.object(
                app,
                "agent_create_suite",
                return_value={"id": "suite-1", "items": [script]},
            ),
            patch.object(
                app,
                "agent_run_suite",
                return_value={"summary": {"passed": 1}},
            ),
            patch.object(
                app,
                "get_agent_failure_coverage_gap",
                return_value={
                    "count": 1,
                    "items": [{"item_id": "generation-1"}],
                },
            ),
            patch.object(
                app,
                "serialize_requirement",
                return_value={"requirement_uid": "requirement-1"},
            ),
            patch.object(app, "update_agent_run", update_run),
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_cleanup_task"),
        ):
            app.run_agent_resume_workflow(
                "agent-1",
                {"key": "demo"},
                "tester",
                "create_suite",
            )

        final_update = update_run.call_args
        self.assertEqual(
            final_update.kwargs["status"],
            "succeeded_with_unresolved",
        )
        self.assertTrue(
            final_update.kwargs["summary"]["partial_success"],
        )
        self.assertEqual(
            final_update.kwargs["summary"]["coverage_gap"]["count"],
            1,
        )

    def test_continue_blocks_an_empty_test_suite(self):
        with (
            patch.object(
                failure_domain,
                "get_agent_run_row",
                return_value={
                    "run_id": "agent-1",
                    "status": "awaiting_failure_action",
                },
            ),
            patch.object(
                failure_domain,
                "get_agent_failure_checkpoint_output",
                return_value={"failure_items": []},
            ),
            patch.object(
                failure_domain,
                "list_agent_item_retry_flows",
                return_value=[],
            ),
            patch.object(
                failure_domain,
                "collect_agent_checkpoint_final_scripts",
                return_value=[],
            ),
            self.assertRaisesRegex(
                failure_domain.AgentFailureCheckpointConflict,
                "没有可执行脚本",
            ),
        ):
            failure_domain.continue_agent_failure_checkpoint(
                "agent-1"
            )


class AgentFailureHandlingApiBehaviorTests(unittest.TestCase):
    @staticmethod
    def waiting_run():
        return {
            "run_id": "agent-1",
            "status": "awaiting_failure_action",
            "summary_json": "{}",
            "plan_generation_json": "{}",
        }

    def test_analyze_endpoint_passes_force_and_returns_analysis(self):
        item = {
            "item_id": "generation-1",
            "status": "unresolved",
            "analysis": {"summary": "模型输出不完整"},
        }
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value=self.waiting_run(),
            ),
            patch.object(
                app,
                "get_agent_failure_item",
                return_value=item,
            ),
            patch.object(
                app,
                "analyze_agent_failure_item",
                return_value=item,
            ) as analyze,
        ):
            response = client.post(
                "/api/agent/runs/agent-1/"
                "failure-items/generation-1/analyze",
                json={"force": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["analysis"]["summary"],
            "模型输出不完整",
        )
        self.assertFalse(response.get_json()["cached"])
        analyze.assert_called_once_with(
            "agent-1",
            "generation-1",
            force=True,
        )

    def test_cancel_endpoint_returns_conflict_when_continue_has_claimed(self):
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value=self.waiting_run(),
            ),
            patch.object(
                app,
                "cancel_agent_failure_checkpoint",
                side_effect=(
                    failure_domain.AgentFailureCheckpointConflict(
                        "失败处置正在进入下一阶段，请勿重复操作。"
                    )
                ),
            ),
        ):
            response = client.post(
                "/api/agent/runs/agent-1/cancel",
                json={},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("正在进入下一阶段", response.get_json()["error"])

    def test_resume_cancelled_checkpoint_does_not_reset_history(self):
        run = {
            **self.waiting_run(),
            "status": "cancelled",
            "current_step": "review_failed_scripts",
            "summary_json": '{"pipeline_version":2}',
        }
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value=run,
            ),
            patch.object(
                app,
                "list_agent_item_retry_flows",
                return_value=[],
            ),
            patch.object(
                app,
                "get_active_agent_run_row",
                return_value=None,
            ),
            patch.object(
                app,
                "resume_agent_failure_checkpoint",
                return_value={
                    **run,
                    "status": "awaiting_failure_action",
                },
            ) as resume_checkpoint,
            patch.object(
                app,
                "agent_run_response",
                return_value={"run": run, "steps": [], "events": []},
            ),
            patch.object(
                app,
                "reset_agent_run_for_resume",
            ) as reset_run,
            patch.object(
                app,
                "start_agent_resume_thread",
            ) as start_thread,
        ):
            response = client.post(
                "/api/agent/runs/agent-1/resume",
                json={},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["checkpoint_restored"])
        resume_checkpoint.assert_called_once_with("agent-1")
        reset_run.assert_not_called()
        start_thread.assert_not_called()

    def test_retry_and_execute_endpoints_return_item_state(self):
        source_item = {
            "item_id": "generation-1",
            "source_type": "generation",
            "status": "unresolved",
        }
        retried_item = {
            **source_item,
            "status": "pending_verification",
            "latest_action": {
                "type": "retry",
                "status": "succeeded",
            },
        }
        resolved_item = {
            **retried_item,
            "status": "resolved",
            "included_in_suite": True,
            "latest_action": {
                "type": "execute",
                "status": "succeeded",
            },
        }
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value=self.waiting_run(),
            ),
            patch.object(
                app,
                "get_agent_failure_item",
                return_value=source_item,
            ),
            patch.object(
                app,
                "retry_agent_failure_item",
                return_value=retried_item,
            ) as retry,
            patch.object(
                app,
                "execute_agent_failure_item",
                return_value=resolved_item,
            ) as execute,
        ):
            retry_response = client.post(
                "/api/agent/runs/agent-1/"
                "failure-items/generation-1/retry",
                json={
                    "action": "regenerate",
                    "instructions": "只修改目标脚本",
                },
            )
            execute_response = client.post(
                "/api/agent/runs/agent-1/"
                "failure-items/generation-1/execute",
                json={},
            )

        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(
            retry_response.get_json()["item"]["status"],
            "pending_verification",
        )
        retry.assert_called_once_with(
            "agent-1",
            "generation-1",
            instructions="只修改目标脚本",
        )
        self.assertEqual(execute_response.status_code, 200)
        self.assertEqual(
            execute_response.get_json()["item"]["status"],
            "resolved",
        )
        execute.assert_called_once_with(
            "agent-1",
            "generation-1",
        )

    def test_edit_delete_and_ignore_endpoints_delegate_safely(self):
        edited_item = {
            "item_id": "repair-1",
            "status": "pending_verification",
        }
        deleted_item = {
            "item_id": "repair-1",
            "status": "deleted",
        }
        ignored_item = {
            "item_id": "generation-1",
            "status": "ignored",
        }
        script = {
            "item_id": "repair-1",
            "artifact_kind": "formal_script",
            "content": "test('支付', async () => {});",
        }
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_agent_run_row",
                return_value=self.waiting_run(),
            ),
            patch.object(
                app,
                "save_agent_failure_item_script",
                return_value=edited_item,
            ) as save_script,
            patch.object(
                app,
                "read_agent_failure_item_script",
                return_value=script,
            ),
            patch.object(
                app,
                "delete_agent_failure_item",
                return_value=deleted_item,
            ) as delete_item,
            patch.object(
                app,
                "ignore_agent_failure_item",
                return_value=ignored_item,
            ) as ignore_item,
        ):
            edit_response = client.patch(
                "/api/agent/runs/agent-1/"
                "failure-items/repair-1/script",
                json={
                    "content": script["content"],
                    "expected_content_sha256": "before-edit",
                },
            )
            delete_response = client.delete(
                "/api/agent/runs/agent-1/"
                "failure-items/repair-1"
            )
            ignore_response = client.post(
                "/api/agent/runs/agent-1/"
                "failure-items/generation-1/ignore",
                json={},
            )

        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(
            edit_response.get_json()["item"]["status"],
            "pending_verification",
        )
        save_script.assert_called_once_with(
            "agent-1",
            "repair-1",
            script["content"],
            expected_content_sha256="before-edit",
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.get_json()["deleted"])
        delete_item.assert_called_once_with("agent-1", "repair-1")
        self.assertEqual(ignore_response.status_code, 200)
        self.assertTrue(ignore_response.get_json()["ignored"])
        ignore_item.assert_called_once_with(
            "agent-1",
            "generation-1",
        )

    def test_continue_endpoint_rejects_zero_executable_scripts(self):
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "continue_agent_failure_checkpoint",
                side_effect=app.AgentFailureCheckpointConflict(
                    "没有可执行脚本，不能创建空测试集。"
                ),
            ),
            patch.object(
                app,
                "start_agent_failure_continue_thread",
            ) as start_thread,
        ):
            response = client.post(
                "/api/agent/runs/agent-1/continue",
                json={},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("没有可执行脚本", response.get_json()["error"])
        start_thread.assert_not_called()

    def test_continue_endpoint_starts_next_stage_with_coverage_gap(self):
        context = {
            "final_scripts": [
                {
                    "module_name": "支付",
                    "filename": "支付成功.spec.ts",
                }
            ],
            "partial_success": True,
            "coverage_gap": {
                "count": 1,
                "items": [{"item_id": "generation-1"}],
            },
        }
        client = app.app.test_client()
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "continue_agent_failure_checkpoint",
                return_value=context,
            ),
            patch.object(
                app,
                "get_current_project",
                return_value={"project_key": "demo"},
            ),
            patch.object(
                app,
                "current_platform_author",
                return_value="admin",
            ),
            patch.object(
                app,
                "start_agent_failure_continue_thread",
            ) as start_thread,
            patch.object(
                app,
                "agent_run_response",
                return_value={
                    "run": {
                        "run_id": "agent-1",
                        "status": "running",
                    },
                    "steps": [],
                    "events": [],
                },
            ),
        ):
            response = client.post(
                "/api/agent/runs/agent-1/continue",
                json={},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["continued"])
        self.assertTrue(response.get_json()["partial_success"])
        self.assertEqual(
            response.get_json()["coverage_gap"]["count"],
            1,
        )
        start_thread.assert_called_once_with(
            "agent-1",
            {"project_key": "demo"},
            "admin",
        )


class AgentFailureHandlingFrontendContractTests(unittest.TestCase):
    def test_failure_workspace_is_a_separate_feature_factory(self):
        workspace_source = read_failure_workspace_source()
        agent_source = read_agent_source()

        self.assertIn(
            "function createAgentFailureWorkspace(",
            workspace_source,
        )
        self.assertIn(
            "window.createAgentFailureWorkspace = "
            "createAgentFailureWorkspace",
            workspace_source,
        )
        self.assertIn(
            "createAgentFailureWorkspace({",
            agent_source,
        )

    def test_failure_workspace_has_separate_lists_and_desktop_actions(self):
        template = render_index_template()
        source = (
            read_agent_source()
            + read_failure_workspace_source()
        )

        for text in (
            "脚本生成失败",
            "脚本修复失败",
            "失败详情",
            "分析和建议",
            "重试",
            "执行",
            "删除",
            "继续任务",
        ):
            self.assertIn(text, template + source)

        for api_suffix in (
            "/analyze",
            "/retry",
            "/execute",
            "/script",
            "/ignore",
            "/continue",
        ):
            self.assertIn(api_suffix, source)

        self.assertIn("generation_failures", source)
        self.assertIn("repair_failures", source)
        self.assertIn("failure_items", source)

    def test_agent_execution_result_merge_helper_is_in_scope(self):
        agent_source = read_agent_source()
        app_source = read_app_source()
        legacy_call = re.search(
            r"mergeTestSuiteScriptResults\(\s*"
            r"eventResult\.script_results,\s*"
            r"stepResult\.script_results\s*\)",
            agent_source,
        )
        if legacy_call is None:
            self.assertRegex(
                agent_source,
                r"(?:function|const|let|var)\s+"
                r"mergeAgentScriptResults\b",
            )
            self.assertRegex(
                agent_source,
                r"mergeAgentScriptResults\(\s*"
                r"eventResult\.script_results,\s*"
                r"stepResult\.script_results\s*\)",
            )
            return

        prefix = agent_source[: legacy_call.start()]
        helper_is_local = bool(
            re.search(
                r"(?:function|const|let|var)\s+"
                r"mergeTestSuiteScriptResults\b",
                prefix,
            )
        )
        helper_is_injected = (
            "options.mergeTestSuiteScriptResults" in prefix
            and "mergeTestSuiteScriptResults" in app_source
        )

        self.assertTrue(
            helper_is_local or helper_is_injected,
            "Agent 历史任务结果合并使用了未定义的 "
            "mergeTestSuiteScriptResults。",
        )

    def test_new_pipeline_hides_only_the_skipped_plan_review_step(self):
        source = read_agent_source()
        timeline_source = source[
            source.index("function agentStepsForTimeline()") :
            source.index("function renderRetryStatusBar()")
        ]

        self.assertIn(
            "Number(state.selectedRun?.pipeline_version || 1) >= 2",
            timeline_source,
        )
        self.assertIn(
            'planReviewOutput.reason === "removed_in_failure_checkpoint_v2"',
            timeline_source,
        )
        self.assertIn(
            'AGENT_STEPS.filter(([key]) => key !== "review_plans")',
            timeline_source,
        )

    def test_failure_checkpoint_can_stop_and_edit_uses_optimistic_hash(self):
        agent_source = read_agent_source()
        workspace_source = read_failure_workspace_source()
        cancel_source = agent_source[
            agent_source.index("async function cancelRun()") :
            agent_source.index("async function resumeRun()")
        ]
        run_list_source = agent_source[
            agent_source.index("function renderRunList()") :
            agent_source.index("function renderTimeline()")
        ]

        self.assertIn(
            '["queued", "running"].includes('
            "state.selectedRun?.status) || isFailureCheckpointRun()",
            cancel_source,
        )
        self.assertIn(
            '["queued", "running"].includes(run.status) || '
            "isFailureCheckpointRun(run)",
            run_list_source,
        )
        self.assertIn(
            "state.failureEditContentSha256 = "
            'script.content_sha256 || ""',
            workspace_source,
        )
        self.assertIn(
            "expected_content_sha256: "
            'state.failureEditContentSha256 || ""',
            workspace_source,
        )
        self.assertRegex(
            workspace_source,
            r'\["queued", "running", "retrying", "executing", '
            r'"editing", "deleting", "analyzing"',
        )


if __name__ == "__main__":
    unittest.main()
