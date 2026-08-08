from collections import deque
from copy import deepcopy
from pathlib import Path
import threading
import unittest
from unittest.mock import MagicMock, patch

from test_plan_viewer.agent.script_preparation import (
    ACTION_ABANDON,
    ACTION_EDIT,
    ACTION_EXECUTE,
    ACTION_REGENERATE,
    ACTION_REPAIR,
    SCRIPT_PREPARATION_STEP_KEY,
    ScriptPreparationConflict,
    ScriptPreparationDependencies,
    ScriptPreparationService,
)


class ScriptPreparationHarness:
    def __init__(self):
        self.output = None
        self.step_updates = []
        self.run_updates = []
        self.events = []
        self.generation_calls = []
        self.execution_calls = []
        self.repair_calls = []
        self.save_calls = []
        self.analysis_calls = []
        self.generate_outcomes = deque()
        self.execute_outcomes = deque()
        self.repair_outcomes = deque()
        self.analysis_outcomes = deque()
        self.revision = 0
        self.clock = 1_000
        self.identifier = 0
        self.run = {
            "run_id": "run-1",
            "status": "running",
            "current_step": SCRIPT_PREPARATION_STEP_KEY,
        }

    def dependencies(self):
        return ScriptPreparationDependencies(
            load_step_output=self.load_step_output,
            get_agent_run=self.get_agent_run,
            update_agent_step=self.update_agent_step,
            update_agent_run=self.update_agent_run,
            append_agent_event=self.append_agent_event,
            generate_script=self.generate_script,
            execute_script=self.execute_script,
            repair_script=self.repair_script,
            analyze_failure=self.analyze_failure,
            save_script=self.save_script,
            build_generation_prompt=self.build_generation_prompt,
            build_repair_prompt=self.build_repair_prompt,
            resolve_script_filename=self.resolve_script_filename,
            current_time_ms=self.current_time_ms,
            redact_value=deepcopy,
            is_cancelled_error=lambda error: isinstance(error, CancelledError),
            make_id=self.make_id,
        )

    def get_agent_run(self, run_id):
        if run_id != "run-1":
            return None
        return deepcopy(self.run)

    def load_step_output(self, run_id, step_key):
        self.assert_step(run_id, step_key)
        return deepcopy(self.output)

    def update_agent_step(self, run_id, step_key, **values):
        self.assert_step(run_id, step_key)
        self.output = deepcopy(values["output_data"])
        self.step_updates.append((run_id, step_key, deepcopy(values)))

    def update_agent_run(self, run_id, **values):
        self.run.update(values)
        self.run_updates.append((run_id, deepcopy(values)))

    def append_agent_event(
        self, run_id, step_key, event_type, message, payload
    ):
        self.assert_step(run_id, step_key)
        self.events.append(
            (
                run_id,
                step_key,
                event_type,
                message,
                deepcopy(payload),
            )
        )

    def generate_script(
        self,
        run_id,
        step_key,
        plan,
        *,
        original_prompt,
        supplemental_prompt,
    ):
        self.assert_step(run_id, step_key)
        self.generation_calls.append(
            {
                "plan": deepcopy(plan),
                "original_prompt": original_prompt,
                "supplemental_prompt": supplemental_prompt,
            }
        )
        outcome = self._next(self.generate_outcomes, None)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, dict):
            return deepcopy(outcome)
        return self.new_script(
            plan.get("script_filename")
            or self.resolve_script_filename(plan),
            "generated",
        )

    def execute_script(self, run_id, step_key, script):
        self.assert_step(run_id, step_key)
        self.execution_calls.append(deepcopy(script))
        outcome = self._next(self.execute_outcomes, True)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, dict):
            return deepcopy(outcome)
        if outcome:
            return {
                "execution": {
                    "ok": True,
                    "status": "succeeded",
                    "duration_ms": 125,
                }
            }
        return {
            "execution": {
                "ok": False,
                "status": "failed",
                "error": "locator timeout",
            }
        }

    def repair_script(
        self,
        run_id,
        step_key,
        script,
        *,
        failure,
        original_prompt,
        supplemental_prompt,
    ):
        self.assert_step(run_id, step_key)
        self.repair_calls.append(
            {
                "script": deepcopy(script),
                "failure": deepcopy(failure),
                "original_prompt": original_prompt,
                "supplemental_prompt": supplemental_prompt,
            }
        )
        outcome = self._next(self.repair_outcomes, None)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, dict):
            return deepcopy(outcome)
        return self.new_script(script.get("filename"), "repaired")

    def analyze_failure(self, run_id, step_key, payload):
        self.assert_step(run_id, step_key)
        self.analysis_calls.append(deepcopy(payload))
        outcome = self._next(
            self.analysis_outcomes,
            {
                "summary": "建议调整定位器后重新修复。",
                "recommended_action": "repair",
                "prompt_patch": "优先使用 data-testid。",
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return deepcopy(outcome)

    def save_script(
        self, run_id, item, content, *, expected_revision_id
    ):
        self.save_calls.append(
            {
                "run_id": run_id,
                "item": deepcopy(item),
                "content": content,
                "expected_revision_id": expected_revision_id,
            }
        )
        return self.new_script(item.get("filename"), content)

    @staticmethod
    def build_generation_prompt(plan):
        return f"GENERATE {plan['module_name']}/{plan['plan_filename']}"

    @staticmethod
    def build_repair_prompt(item, failure):
        return f"REPAIR {item['filename']}: {failure.get('error', '')}"

    @staticmethod
    def resolve_script_filename(plan):
        stem = Path(plan["plan_filename"]).stem
        return f"{stem}.spec.ts"

    def current_time_ms(self):
        self.clock += 1
        return self.clock

    def make_id(self, prefix):
        self.identifier += 1
        return f"{prefix}-{self.identifier}"

    def new_script(self, filename, content):
        self.revision += 1
        return {
            "filename": filename,
            "content": content,
            "asset": {"current_revision_id": self.revision},
        }

    @staticmethod
    def _next(values, default):
        return values.popleft() if values else default

    @staticmethod
    def assert_step(run_id, step_key):
        if run_id != "run-1":
            raise AssertionError(f"unexpected run_id: {run_id}")
        if step_key != SCRIPT_PREPARATION_STEP_KEY:
            raise AssertionError(f"unexpected step_key: {step_key}")


def make_plan(name="login"):
    return {
        "module_name": "account",
        "plan_filename": f"{name}.md",
    }


class CancelledError(RuntimeError):
    pass


class ScriptPreparationStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.harness = ScriptPreparationHarness()
        self.service = ScriptPreparationService(
            self.harness.dependencies()
        )

    def test_successful_generation_executes_and_continues(self):
        result = self.service.run("run-1", [make_plan()])

        item = result["snapshot"]["items"][0]
        self.assertEqual(item["status"], "ready")
        self.assertTrue(item["included_in_suite"])
        self.assertEqual(
            [stage["stage_type"] for stage in item["history"]],
            ["generate", "execute"],
        )
        self.assertTrue(
            all(stage["status"] == "succeeded" for stage in item["history"])
        )
        self.assertTrue(result["should_continue"])
        self.assertFalse(result["paused"])
        self.assertEqual(len(result["final_scripts"]), 1)
        self.assertEqual(result["counts"]["ready"], 1)

        self.assertGreater(len(self.harness.events), 0)
        for _, step_key, _, _, payload in self.harness.events:
            self.assertEqual(step_key, SCRIPT_PREPARATION_STEP_KEY)
            self.assertTrue(payload["artifact_progress"])
            self.assertIn("step_output", payload)
            self.assertIn("counts", payload)
            self.assertIn("item_id", payload)
        self.assertTrue(
            all(
                update[1]["current_step"] == SCRIPT_PREPARATION_STEP_KEY
                for update in self.harness.run_updates
            )
        )

    def test_one_automatic_repair_is_verified_without_a_second_repair(self):
        self.harness.execute_outcomes.extend([False, True])

        result = self.service.run("run-1", [make_plan()])

        item = result["snapshot"]["items"][0]
        self.assertEqual(
            [stage["stage_type"] for stage in item["history"]],
            ["generate", "execute", "repair", "execute"],
        )
        self.assertEqual(
            [stage["status"] for stage in item["history"]],
            ["succeeded", "failed", "succeeded", "succeeded"],
        )
        generated_revision = item["history"][0]["output_revision_id"]
        repaired_revision = item["history"][2]["output_revision_id"]
        self.assertEqual(
            item["history"][2]["input_revision_id"], generated_revision
        )
        self.assertNotEqual(generated_revision, repaired_revision)
        self.assertEqual(
            item["history"][3]["input_revision_id"], repaired_revision
        )
        self.assertEqual(
            item["history"][3]["output_revision_id"], repaired_revision
        )
        self.assertEqual(len(self.harness.repair_calls), 1)
        self.assertEqual(len(self.harness.analysis_calls), 0)
        self.assertTrue(result["should_continue"])

    def test_execution_policy_block_skips_ai_repair_and_analysis(self):
        self.harness.execute_outcomes.append(
            {
                "error": (
                    "Test execution is disabled by default. Set "
                    "PLATFORM_ALLOW_TEST_EXECUTION=true only for trusted "
                    "single-tenant projects."
                )
            }
        )

        result = self.service.run("run-1", [make_plan()])

        item = result["snapshot"]["items"][0]
        self.assertEqual(item["status"], "awaiting_human")
        self.assertEqual(
            [stage["stage_type"] for stage in item["history"]],
            ["generate", "execute", "blocked"],
        )
        self.assertEqual(item["history"][-1]["status"], "blocked")
        self.assertTrue(item["latest_analysis"]["blocked_by_environment"])
        self.assertEqual(item["latest_analysis"]["analysis_status"], "blocked")
        self.assertEqual(item["latest_analysis"]["recommended_action"], ACTION_EXECUTE)
        self.assertEqual(self.harness.repair_calls, [])
        self.assertEqual(self.harness.analysis_calls, [])
        self.assertTrue(result["paused"])

    def test_snapshot_remains_readable_during_remote_generation(self):
        started = threading.Event()
        release = threading.Event()
        original_generate = self.harness.generate_script

        def slow_generate(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return original_generate(*args, **kwargs)

        self.harness.generate_script = slow_generate
        self.service = ScriptPreparationService(self.harness.dependencies())
        result_holder = {}
        worker = threading.Thread(
            target=lambda: result_holder.setdefault(
                "result", self.service.run("run-1", [make_plan()])
            )
        )
        worker.start()
        self.assertTrue(started.wait(timeout=1))

        snapshot = self.service.get_snapshot("run-1")
        self.assertEqual(snapshot["items"][0]["status"], "generating")

        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result_holder["result"]["counts"]["ready"], 1)

    def test_failed_post_repair_verification_waits_for_human_then_rerepairs(self):
        self.harness.execute_outcomes.extend([False, False])
        self.harness.analysis_outcomes.append(
            {
                "summary": "定位器仍然失效。",
                "recommended_action": "repair",
                "prompt_patch": "改用 role 与可见名称组合定位。",
            }
        )

        result = self.service.run("run-1", [make_plan()])

        item = result["snapshot"]["items"][0]
        self.assertEqual(item["status"], "awaiting_human")
        self.assertEqual(
            [stage["stage_type"] for stage in item["history"]],
            ["generate", "execute", "repair", "execute", "human_review"],
        )
        self.assertEqual(item["history"][-1]["status"], "pending")
        self.assertEqual(len(self.harness.repair_calls), 1)
        self.assertTrue(result["paused"])
        self.assertFalse(result["should_continue"])
        self.assertEqual(
            item["latest_analysis"]["prompt_options"]["repair"][
                "supplemental_prompt"
            ],
            "改用 role 与可见名称组合定位。",
        )

        self.harness.execute_outcomes.append(True)
        action_result = self.service.apply_action(
            "run-1", item["item_id"], action=ACTION_REPAIR
        )

        updated = action_result["item"]
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["history"][4]["status"], "resolved")
        self.assertEqual(
            [stage["stage_type"] for stage in updated["history"][-2:]],
            ["rerepair", "execute"],
        )
        self.assertEqual(
            self.harness.repair_calls[-1]["supplemental_prompt"],
            "改用 role 与可见名称组合定位。",
        )
        self.assertTrue(action_result["should_continue"])

    def test_generation_failure_with_invalid_analysis_is_explicit_and_disables_repair(self):
        self.harness.generate_outcomes.append(RuntimeError("model offline"))
        self.harness.analysis_outcomes.append(
            {
                "summary": "错误地建议修复。",
                "recommended_action": "repair",
                "prompt_patch": "不可用于没有脚本的修复。",
            }
        )

        result = self.service.run("run-1", [make_plan()])

        item = result["snapshot"]["items"][0]
        analysis = item["latest_analysis"]
        self.assertIsNone(item["current_script"])
        self.assertEqual(analysis["analysis_status"], "failed")
        self.assertEqual(analysis["recommended_action"], "")
        self.assertIn("regenerate", analysis["analysis_error"])
        self.assertFalse(analysis["prompt_options"]["repair"]["enabled"])
        self.assertEqual(
            analysis["prompt_options"]["repair"]["original_prompt"], ""
        )
        self.assertEqual(
            analysis["prompt_options"]["repair"]["supplemental_prompt"],
            "",
        )
        with self.assertRaises(ScriptPreparationConflict):
            self.service.apply_action(
                "run-1", item["item_id"], action=ACTION_REPAIR
            )

        recovered = self.service.apply_action(
            "run-1", item["item_id"], action=ACTION_REGENERATE
        )

        self.assertEqual(recovered["item"]["status"], "ready")
        self.assertTrue(recovered["should_continue"])

    def test_manual_edit_inherits_revision_and_can_execute_later(self):
        self.harness.execute_outcomes.extend([False, False])
        result = self.service.run("run-1", [make_plan()])
        item = result["snapshot"]["items"][0]
        old_revision = item["current_revision_id"]

        with self.assertRaises(ScriptPreparationConflict):
            self.service.apply_action(
                "run-1",
                item["item_id"],
                action=ACTION_EDIT,
                content="test('changed', async () => {});",
                expected_revision_id="stale-revision",
            )

        saved = self.service.apply_action(
            "run-1",
            item["item_id"],
            action=ACTION_EDIT,
            content="test('changed', async () => {});",
            expected_revision_id=old_revision,
            execute_after_save=False,
        )

        saved_item = saved["item"]
        edit_stage = saved_item["history"][-1]
        self.assertEqual(edit_stage["stage_type"], "manual_edit")
        self.assertEqual(edit_stage["input_revision_id"], old_revision)
        self.assertEqual(
            edit_stage["output_revision_id"],
            saved_item["current_revision_id"],
        )
        self.assertNotEqual(
            edit_stage["input_revision_id"], edit_stage["output_revision_id"]
        )
        self.assertEqual(saved_item["status"], "awaiting_human")
        self.assertFalse(saved["should_continue"])

        self.harness.execute_outcomes.append(True)
        executed = self.service.apply_action(
            "run-1", item["item_id"], action=ACTION_EXECUTE
        )
        self.assertEqual(executed["item"]["status"], "ready")
        self.assertEqual(
            executed["item"]["history"][-1]["stage_type"], "execute"
        )
        self.assertTrue(executed["should_continue"])

    def test_all_abandoned_completes_without_scripts(self):
        self.harness.execute_outcomes.extend([False, False])
        result = self.service.run("run-1", [make_plan()])
        item = result["snapshot"]["items"][0]

        abandoned = self.service.apply_action(
            "run-1", item["item_id"], action=ACTION_ABANDON
        )

        self.assertEqual(abandoned["counts"]["abandoned"], 1)
        self.assertFalse(abandoned["paused"])
        self.assertTrue(abandoned["should_continue"])
        self.assertEqual(abandoned["final_scripts"], [])
        self.assertEqual(abandoned["error"], "")
        self.assertEqual(self.harness.step_updates[-1][2]["status"], "succeeded")

    def test_cancelled_generation_propagates_without_analysis(self):
        cancellation = CancelledError("cancelled")
        self.harness.generate_outcomes.append(cancellation)

        with self.assertRaises(CancelledError) as raised:
            self.service.run("run-1", [make_plan()])

        self.assertIs(raised.exception, cancellation)
        self.assertEqual(self.harness.analysis_calls, [])

    def test_action_rejects_run_that_left_awaiting_script_action(self):
        self.harness.execute_outcomes.extend([False, False])
        result = self.service.run("run-1", [make_plan()])
        item = result["snapshot"]["items"][0]
        self.harness.run.update(
            {"status": "succeeded", "current_step": "run_suite"}
        )

        with self.assertRaises(ScriptPreparationConflict):
            self.service.apply_action(
                "run-1", item["item_id"], action=ACTION_ABANDON
            )

    def test_custom_prompts_become_the_next_inheritance_baseline(self):
        self.harness.execute_outcomes.extend([False, False])
        result = self.service.run("run-1", [make_plan()])
        item_id = result["snapshot"]["items"][0]["item_id"]

        self.harness.generate_outcomes.append(RuntimeError("regenerate failed"))
        regenerated = self.service.apply_action(
            "run-1",
            item_id,
            action=ACTION_REGENERATE,
            original_prompt="CUSTOM GENERATE",
            supplemental_prompt="CUSTOM GENERATE PATCH",
        )
        self.assertEqual(
            regenerated["item"]["latest_analysis"]["prompt_options"][
                ACTION_REGENERATE
            ]["original_prompt"],
            "CUSTOM GENERATE",
        )

        self.harness.repair_outcomes.append(RuntimeError("repair failed"))
        repaired = self.service.apply_action(
            "run-1",
            item_id,
            action=ACTION_REPAIR,
            original_prompt="CUSTOM REPAIR",
            supplemental_prompt="CUSTOM REPAIR PATCH",
        )
        self.assertEqual(
            repaired["item"]["latest_analysis"]["prompt_options"][
                ACTION_REPAIR
            ]["original_prompt"],
            "CUSTOM REPAIR",
        )

    def test_invalid_or_empty_analysis_contract_is_marked_failed(self):
        invalid_results = (
            {
                "summary": "bad action",
                "recommended_action": "execute",
                "prompt_patch": "patch",
            },
            {
                "summary": "empty patch",
                "recommended_action": "repair",
                "prompt_patch": "",
            },
        )
        for invalid in invalid_results:
            with self.subTest(invalid=invalid):
                harness = ScriptPreparationHarness()
                service = ScriptPreparationService(harness.dependencies())
                harness.execute_outcomes.extend([False, False])
                harness.analysis_outcomes.append(invalid)

                result = service.run("run-1", [make_plan()])

                analysis = result["snapshot"]["items"][0]["latest_analysis"]
                self.assertEqual(analysis["analysis_status"], "failed")
                self.assertEqual(analysis["recommended_action"], "")
                self.assertTrue(analysis["analysis_error"])

    def test_batch_supports_per_item_prompts_and_partial_rejection(self):
        self.harness.execute_outcomes.extend([False, False, False, False])
        result = self.service.run(
            "run-1", [make_plan("login"), make_plan("profile")]
        )
        item_ids = [item["item_id"] for item in result["snapshot"]["items"]]

        batch = self.service.apply_batch_action(
            "run-1",
            [
                {
                    "item_id": item_ids[0],
                    "original_prompt": "LOGIN ORIGINAL",
                    "supplemental_prompt": "LOGIN PATCH",
                },
                {
                    "item_id": item_ids[1],
                    "original_prompt": "PROFILE ORIGINAL",
                    "supplemental_prompt": "PROFILE PATCH",
                },
                {"item_id": "missing-item"},
            ],
            action=ACTION_REGENERATE,
            original_prompt="SHARED ORIGINAL",
            supplemental_prompt="SHARED PATCH",
        )

        self.assertEqual(
            [item["item_id"] for item in batch["accepted"]], item_ids
        )
        self.assertEqual(
            [item["item_id"] for item in batch["rejected"]],
            ["missing-item"],
        )
        self.assertEqual(
            [
                (
                    call["original_prompt"],
                    call["supplemental_prompt"],
                )
                for call in self.harness.generation_calls[-2:]
            ],
            [
                ("LOGIN ORIGINAL", "LOGIN PATCH"),
                ("PROFILE ORIGINAL", "PROFILE PATCH"),
            ],
        )
        self.assertTrue(batch["should_continue"])
        self.assertEqual(batch["counts"]["ready"], 2)


class ScriptPreparationAppIntegrationTests(unittest.TestCase):
    def test_all_abandoned_skips_suite_creation_and_finishes_safely(self):
        import app

        with (
            patch.object(app, "agent_create_suite") as create_suite,
            patch.object(app, "agent_run_suite") as run_suite,
            patch.object(app, "update_agent_step") as update_step,
            patch.object(app, "update_agent_run") as update_run,
            patch.object(app, "append_agent_event"),
            patch.object(
                app,
                "serialize_requirement",
                return_value={"requirement_uid": "requirement-1"},
            ),
        ):
            app.finish_agent_after_script_preparation(
                "run-1",
                {"requirement_uid": "requirement-1"},
                [{"module_name": "account"}],
                [make_plan()],
                [],
                {"counts": {"total": 1, "ready": 0, "abandoned": 1}},
            )

        create_suite.assert_not_called()
        run_suite.assert_not_called()
        self.assertEqual(
            [call.args[1] for call in update_step.call_args_list],
            ["create_suite", "run_suite"],
        )
        self.assertTrue(
            all(call.kwargs["status"] == "skipped" for call in update_step.call_args_list)
        )
        self.assertEqual(
            update_run.call_args.kwargs["status"],
            "succeeded_with_unresolved",
        )
        self.assertTrue(update_run.call_args.kwargs["finished"])

    def test_continuation_claim_is_a_single_conditional_database_update(self):
        import app

        connection_context = MagicMock()
        connection = connection_context.__enter__.return_value
        cursor_context = connection.cursor.return_value
        cursor = cursor_context.__enter__.return_value
        cursor.rowcount = 1
        with (
            patch.object(app, "require_platform_database", return_value={}),
            patch.object(app, "get_agent_runs_table", return_value="agent_runs"),
            patch.object(app, "get_agent_run_steps_table", return_value="agent_steps"),
            patch.object(app, "get_current_project_id", return_value="project-1"),
            patch.object(app, "current_time_ms", return_value=123),
            patch.object(
                app,
                "platform_mysql_connection",
                return_value=connection_context,
            ),
        ):
            claimed = app.claim_agent_script_preparation_continue("run-1")
            cursor.rowcount = 0
            duplicate = app.claim_agent_script_preparation_continue("run-1")

        self.assertTrue(claimed)
        self.assertFalse(duplicate)
        self.assertEqual(cursor.execute.call_count, 2)
        sql = cursor.execute.call_args_list[0].args[0]
        self.assertIn("current_step = 'prepare_scripts'", sql)
        self.assertIn("steps.status = 'succeeded'", sql)
        self.assertIn("current_step = 'create_suite'", sql)


if __name__ == "__main__":
    unittest.main()
