from contextlib import ExitStack
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, patch

import app
from test_plan_viewer.agent import localization


CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


class AgentLocalizationTests(unittest.TestCase):
    def test_multiple_plan_deletion_log_uses_captured_project_language(self):
        split_result = {
            "created": [{"filename": "browse-products.md"}],
            "reused": [],
            "skipped": [],
        }

        for language, expected in (
            ("en", "Deleted intermediate multi-case Markdown: catalog-case-index.md\n"),
            ("zh-CN", "已删除多计划中间 Markdown：catalog-case-index.md\n"),
        ):
            with self.subTest(language=language), app.use_project_context(
                {"language": language}
            ), ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        app,
                        "split_or_repair_multiple_plan",
                        return_value=split_result,
                    )
                )
                stack.enter_context(
                    patch.object(
                        app,
                        "sync_plan_asset",
                        return_value={"asset_id": 1},
                    )
                )
                stack.enter_context(
                    patch.object(
                        app,
                        "get_plan_file",
                        return_value=Path("/tmp/browse-products.md"),
                    )
                )
                stack.enter_context(
                    patch.object(app, "serialize_asset", side_effect=lambda value: value)
                )
                stack.enter_context(
                    patch.object(
                        app,
                        "delete_intermediate_plan_file",
                        return_value={"asset": {"asset_id": 2}},
                    )
                )
                append_log = stack.enter_context(
                    patch.object(app, "append_test_job_log")
                )
                stack.enter_context(patch.object(app, "list_asset_revisions", return_value=[]))

                app.finalize_multiple_plan_files(
                    "Catalog",
                    Path("/tmp/catalog-case-index.md"),
                    "planner-1",
                    "source",
                    "split",
                )

            append_log.assert_called_once_with("planner-1", expected)

    def test_english_multiple_plan_endpoints_preserve_final_payload_without_cjk(self):
        def success_stream(module_name, _prompt, target_file, **kwargs):
            payload = kwargs["success_payload_factory"]()
            status = {
                "status": "succeeded",
                "module_name": module_name,
                "target_path": str(target_file),
                "error": None,
                **payload,
            }
            yield app.sse_payload("status", status)
            yield app.sse_payload("done", {"ok": True, **payload})

        endpoint_cases = (
            (
                "manual",
                "/api/plan-generation-stream",
                app.create_plan_generation_stream,
                {
                    "module_name": "Catalog",
                    "plan_name": "Catalog Case Index",
                    "generation_mode": "multiple",
                    "prompt": "Create catalog test plans.",
                },
            ),
            (
                "requirement",
                "/api/requirements/req-1/modules/catalog-module/generate-plan-stream",
                lambda: app.generate_requirement_module_plan_stream(
                    "req-1", "catalog-module"
                ),
                {
                    "module_name": "Catalog",
                    "plan_name": "Catalog Case Index",
                    "generation_mode": "multiple",
                    "prompt": "Create catalog test plans.",
                },
            ),
        )
        normalized = {
            "coverage_profile": "core",
            "coverage_prompt": "",
            "prompt_customized": False,
        }

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog-case-index.md"
            generated = Path(directory) / "browse-products.md"
            split_result = {
                "created": [{"filename": generated.name}],
                "reused": [],
                "skipped": [],
            }

            def sync_asset(_module_name, path, **_kwargs):
                path = Path(path)
                return {
                    "asset_id": 10 if path == target else 11,
                    "module_name": "Catalog",
                    "title": path.stem.replace("-", " ").title(),
                    "status": "active",
                }

            for name, path, endpoint, request_payload in endpoint_cases:
                with self.subTest(endpoint=name), ExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            app,
                            "normalize_plan_generation_request",
                            return_value=normalized,
                        )
                    )
                    stack.enter_context(
                        patch.object(app, "get_current_project_language", return_value="en")
                    )
                    stack.enter_context(patch.object(app, "validate_module_name"))
                    stack.enter_context(
                        patch.object(
                            app,
                            "get_plan_filename_from_name",
                            return_value=target.name,
                        )
                    )
                    stack.enter_context(
                        patch.object(app, "get_plan_target_path", return_value=target)
                    )
                    stack.enter_context(
                        patch.object(
                            app,
                            "build_multiple_plan_generation_prompt",
                            return_value="Generate the plan index.",
                        )
                    )
                    stack.enter_context(
                        patch.object(app, "build_plan_prompt_context", return_value={})
                    )
                    stack.enter_context(patch.object(app, "create_test_job"))
                    stack.enter_context(
                        patch.object(app, "build_setup_targets", return_value=[])
                    )
                    split_plan = stack.enter_context(
                        patch.object(
                            app,
                            "split_or_repair_multiple_plan",
                            return_value=split_result,
                        )
                    )
                    stack.enter_context(
                        patch.object(app, "sync_plan_asset", side_effect=sync_asset)
                    )
                    stack.enter_context(
                        patch.object(app, "get_plan_file", return_value=generated)
                    )
                    stack.enter_context(
                        patch.object(app, "serialize_asset", side_effect=lambda value: value)
                    )
                    stack.enter_context(
                        patch.object(
                            app,
                            "delete_intermediate_plan_file",
                            return_value={
                                "ok": True,
                                "module": "Catalog",
                                "plan_filename": target.name,
                                "archive": {
                                    "reason": "delete intermediate multiple plan"
                                },
                                "asset": {
                                    "asset_id": 10,
                                    "module_name": "Catalog",
                                    "title": "Catalog Case Index",
                                    "status": "deleted",
                                },
                                "error": None,
                            },
                        )
                    )
                    append_log = stack.enter_context(
                        patch.object(app, "append_test_job_log")
                    )
                    stack.enter_context(
                        patch.object(app, "list_asset_revisions", return_value=[])
                    )
                    stack.enter_context(
                        patch.object(app, "stream_plan_generation", side_effect=success_stream)
                    )
                    if name == "requirement":
                        stack.enter_context(
                            patch.object(
                                app,
                                "get_requirement_by_uid",
                                return_value={"id": 1},
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                app,
                                "get_requirement_module",
                                return_value={
                                    "id": 2,
                                    "module_uid": "catalog-module",
                                    "module_name": "Catalog",
                                    "plan_name": "Catalog Case Index",
                                    "planner_prompt": "Create catalog test plans.",
                                },
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                app,
                                "serialize_requirement_module",
                                side_effect=lambda value: value,
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                app,
                                "link_requirement_module_plan",
                                return_value={
                                    "module_uid": "catalog-module",
                                    "module_name": "Catalog",
                                },
                            )
                        )

                    with app.app.test_request_context(
                        path,
                        method="POST",
                        json=request_payload,
                        headers={"X-Project-Key": "demo"},
                    ):
                        response = endpoint()
                        events = app.parse_sse_text_blocks(response.get_data(as_text=True))

                split_plan.assert_called_once()
                append_log.assert_called_once_with(
                    ANY,
                    "Deleted intermediate multi-case Markdown: catalog-case-index.md\n",
                )
                terminal_payloads = [
                    payload
                    for event, payload in events
                    if event == "done"
                    or (event == "status" and payload.get("status") == "succeeded")
                ]
                self.assertEqual(len(terminal_payloads), 2)
                for payload in terminal_payloads:
                    self.assertEqual(
                        [item["plan_filename"] for item in payload["plans"]],
                        [generated.name],
                    )
                    self.assertEqual(payload["split"], split_result)
                    self.assertEqual(
                        payload["deleted_source"]["plan_filename"], target.name
                    )
                    self.assertIsNone(
                        CJK_PATTERN.search(json.dumps(payload, ensure_ascii=False))
                    )

    def test_english_splitter_prompt_and_platform_wrappers_have_no_cjk(self):
        prompt = localization.splitter_prompt("en")
        wrappers = [
            localization.message("en", "tool_input", title="read"),
            localization.message("en", "tool_output", title="read"),
            localization.message("en", "split_plan_failed", error="content conflict"),
        ]
        self.assertIsNone(CJK_PATTERN.search("\n".join([prompt, *wrappers])))
        self.assertIsNotNone(CJK_PATTERN.search(localization.splitter_prompt("zh-CN")))

    def test_english_dynamic_events_and_retry_prompts_have_no_cjk(self):
        legacy_events = [
            "已创建测试集：Agent-demo，脚本 3 条。",
            "脚本生成队列已准备，共 11 个计划。",
            "脚本修复队列已准备，共 2 个脚本。",
            "脚本准备状态已更新。",
        ]
        localized_events = [localization.event_message("en", item) for item in legacy_events]
        prompts = [
            localization.append_supplemental_prompt(
                "en", "Base prompt", "Use role A.", "generation"
            ),
            localization.append_supplemental_prompt(
                "en", "Base prompt", "Keep assertions.", "repair"
            ),
            localization.message("en", "failure_analysis_instruction"),
            localization.message("en", "script_generation_title", target="Checkout/submit"),
            localization.message("en", "script_repair_success", target="checkout.spec.ts"),
        ]
        self.assertIsNone(CJK_PATTERN.search("\n".join([*localized_events, *prompts])))
        self.assertIn("11 plans", localized_events[1])
        self.assertIn(
            "本次重新生成补充要求",
            localization.append_supplemental_prompt("zh-CN", "基础", "补充", "generation"),
        )

    def test_known_platform_errors_and_ui_stream_copy_are_english(self):
        values = [
            localization.event_message("en", "Agent 任务失败：需求不存在。"),
            localization.event_message(
                "en",
                "脚本生成失败：Catalog/Browse.md，测试计划不存在：/workspace/specs/Catalog/Browse.md",
            ),
            localization.message("en", "step_failed", step="Run", error="测试集没有可执行脚本。"),
            localization.message(
                "en", "step_failed", step="Run", error=RuntimeError("需求不存在。")
            ),
            localization.message("en", "seed_generation_title"),
            localization.message("en", "seed_generation_success", target="seed.spec.ts"),
            localization.message("en", "requirement_plan_title", module="Catalog"),
            localization.message("en", "requirement_plan_success", target="Catalog.md"),
            localization.message("en", "manual_script_generation_title", target="Catalog/Browse"),
            localization.message("en", "manual_script_generation_success", target="Browse.spec.ts"),
            localization.message("en", "manual_script_repair_title", target="Browse.spec.ts"),
            localization.message("en", "manual_script_repair_success", target="Browse.spec.ts"),
        ]
        self.assertIsNone(CJK_PATTERN.search("\n".join(values)))
        unknown = "目标系统返回中文错误"
        self.assertIn(unknown, localization.message("en", "step_failed", step="Run", error=unknown))
        self.assertIn(
            "任务结束",
            localization.message("zh-CN", "manual_script_repair_success", target="x.spec.ts"),
        )

    def test_english_agent_execution_wrappers_do_not_modify_process_output(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(app, "agent_project_language", return_value="en"),
            patch.object(app, "get_job_log_path", return_value=Path(directory) / "execution.log"),
            patch.object(app, "append_test_job_log"),
        ):
            output = app._BufferedExecutionOutput("execution-1", agent_stream=True)
            try:
                wrappers = [
                    "执行模式：按文件串行执行。",
                    "准备执行第 1/3 个测试集脚本：Catalog/Browse.spec.ts",
                    "开始执行准备脚本：Seed login。",
                    "准备脚本完成：Seed login。",
                    "执行命令：npx playwright test Catalog/Browse.spec.ts",
                    "合并 Playwright 测试集执行报告。",
                ]
                localized = [
                    next(app.parse_sse_text_blocks(output.emit_log(message)))[1]["message"]
                    for message in wrappers
                ]
                raw_output = "目标系统返回中文原始输出\n"
                output.emit_delta(raw_output)
                delta = next(app.parse_sse_text_blocks(output.flush("test")))[1]["text"]
            finally:
                output.close()
        self.assertIsNone(CJK_PATTERN.search("\n".join(localized)))
        self.assertEqual(delta, raw_output)

    def test_duplicate_conflict_reports_its_reason_in_english(self):
        error = localization.plan_conflict_error(
            "en",
            [
                {
                    "filename": "checkout.md",
                    "reason": "Multiple cases resolve to the same filename.",
                    "reason_code": "duplicate_filename",
                }
            ],
        )
        self.assertIn("Multiple cases resolve to the same filename", error)
        self.assertIsNone(CJK_PATTERN.search(error))

    def test_agent_stream_fallback_messages_use_captured_english_language(self):
        events = []

        def chunks():
            yield app.sse_payload("status", {"status": "running"})
            yield app.sse_payload("done", {"ok": True, "status": "succeeded"})

        with (
            app.use_project_context({"language": "en"}),
            patch.object(app, "agent_raise_if_cancelled"),
            patch.object(app, "persist_agent_stream_batch"),
            patch.object(
                app,
                "append_agent_event",
                side_effect=lambda *args, **_kwargs: events.append(args[3]),
            ),
        ):
            app.consume_agent_sse_generator("agent-1", "generate_plans", chunks())

        self.assertEqual(events, ["Status: running", "Task succeeded"])
        self.assertIsNone(CJK_PATTERN.search("\n".join(events)))


if __name__ == "__main__":
    unittest.main()
