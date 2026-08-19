from collections import deque
from copy import deepcopy
from pathlib import Path
from contextlib import nullcontext
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
from test_plan_viewer.script_preparation import agent_adapter
from test_plan_viewer.script_preparation import composition


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

    def test_nonterminal_or_blocked_execution_never_marks_script_ready(self):
        for status in ("running", "blocked"):
            with self.subTest(status=status):
                harness = ScriptPreparationHarness()
                harness.execute_outcomes.extend(
                    [
                        {"execution": {"ok": True, "status": status}},
                        {"execution": {"ok": True, "status": status}},
                    ]
                )
                service = ScriptPreparationService(harness.dependencies())
                result = service.run("run-1", [make_plan()])
                item = result["snapshot"]["items"][0]
                self.assertEqual(item["status"], "awaiting_human")
                self.assertFalse(item["included_in_suite"])
                self.assertEqual(item["history"][1]["status"], "failed")
                self.assertEqual(item["history"][3]["status"], "failed")

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

    def test_agent_regenerate_inherits_and_cas_checks_current_revision(self):
        self.harness.execute_outcomes.extend([False, False])
        result = self.service.run("run-1", [make_plan()])
        item = result["snapshot"]["items"][0]
        previous = item["current_revision_id"]
        regenerated = self.service.apply_action(
            "run-1",
            item["item_id"],
            action=ACTION_REGENERATE,
            original_prompt="REGENERATE CURRENT",
        )
        stage = next(
            value
            for value in regenerated["item"]["history"]
            if value["stage_type"] == "regenerate"
        )
        self.assertEqual(stage["input_revision_id"], previous)
        self.assertEqual(
            self.harness.generation_calls[-1]["plan"][
                "_expected_script_revision_id"
            ],
            previous,
        )

    def test_agent_adapter_adopts_external_revision_before_actions(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "awaiting_script_action",
            "current_step": SCRIPT_PREPARATION_STEP_KEY,
        }
        runtime.validate_module_name.side_effect = lambda value: value
        runtime.validate_script_filename.side_effect = lambda value: value
        script_file = MagicMock()
        script_file.is_file.return_value = True
        runtime.get_script_file.return_value = script_file
        runtime.sync_script_asset.return_value = {"current_revision_id": 2}
        item = {
            "item_id": "item-1",
            "module_name": "account",
            "filename": "login.spec.ts",
            "current_revision_id": 1,
        }
        with (
            patch.object(
                agent_adapter,
                "acquire_script_target_lease",
                return_value=nullcontext(),
            ),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_snapshot",
                return_value={"items": [item]},
            ),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_item",
                return_value=item,
            ),
            patch.object(
                agent_adapter.state_machine,
                "adopt_script_preparation_external_versions",
            ) as adopt,
        ):
            changed = agent_adapter.reconcile_items_for_web(
                runtime, "run-1", ["item-1"]
            )
        self.assertTrue(changed)
        adopt.assert_called_once_with(
            "run-1", [{"item_id": "item-1", "revision_id": 2}]
        )

    def test_agent_adapter_reconciles_entire_batch_before_other_item_action(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "awaiting_script_action",
            "current_step": SCRIPT_PREPARATION_STEP_KEY,
        }
        runtime.validate_module_name.side_effect = lambda value: value
        runtime.validate_script_filename.side_effect = lambda value: value
        runtime.get_script_file.return_value.is_file.return_value = True
        items = [
            {
                "item_id": "item-a",
                "module_name": "account",
                "filename": "a.spec.ts",
                "current_revision_id": 1,
            },
            {
                "item_id": "item-b",
                "module_name": "account",
                "filename": "b.spec.ts",
                "current_revision_id": 7,
            },
        ]
        runtime.sync_script_asset.side_effect = [
            {"current_revision_id": 2},
            {"current_revision_id": 7},
        ]
        with (
            patch.object(
                agent_adapter, "acquire_script_target_lease", return_value=nullcontext()
            ),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_snapshot",
                return_value={"items": items},
            ),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_item",
                side_effect=lambda _run_id, item_id: next(
                    item for item in items if item["item_id"] == item_id
                ),
            ),
            patch.object(
                agent_adapter.state_machine,
                "adopt_script_preparation_external_versions",
            ) as adopt,
        ):
            changed = agent_adapter.reconcile_items_for_web(
                runtime, "run-1", ["item-b"]
            )
        self.assertTrue(changed)
        adopt.assert_called_once_with(
            "run-1", [{"item_id": "item-a", "revision_id": 2}]
        )

    def test_agent_final_barrier_holds_through_reconcile_and_suite_callback(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "running",
            "current_step": "create_suite",
        }
        runtime.get_agent_step_row.side_effect = lambda _run_id, step: {
            "status": "succeeded" if step == SCRIPT_PREPARATION_STEP_KEY else "queued"
        }
        runtime.get_agent_step_output.return_value = {}
        active = {"value": False}

        class Barrier:
            def __enter__(inner_self):
                active["value"] = True
                return inner_self

            def __exit__(inner_self, *_args):
                active["value"] = False

        with (
            patch.object(
                agent_adapter, "script_preparation_barrier", return_value=Barrier()
            ),
            patch.object(
                agent_adapter,
                "reconcile_items_for_web",
                side_effect=lambda *_args, **_kwargs: self.assertTrue(active["value"]),
            ),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_snapshot",
                return_value={
                    "items": [],
                    "counts": {"total": 1, "terminal": 1},
                },
            ),
        ):
            result = agent_adapter.finish_with_script_preparation_barrier(
                runtime,
                "run-1",
                lambda _snapshot: active["value"],
            )
        self.assertTrue(result)
        self.assertFalse(active["value"])

    def test_agent_failure_analysis_redacts_payload_before_model_call(self):
        runtime = MagicMock()
        runtime.agent_message.return_value = "analyze"
        with patch.object(
            agent_adapter.failure_handling,
            "redact_agent_failure_value",
            return_value={"authorization": "[redacted]", "password": "[redacted]"},
        ):
            agent_adapter.analyze_failure(
                runtime,
                "run-1",
                SCRIPT_PREPARATION_STEP_KEY,
                {
                    "authorization": "Bearer secret-token",
                    "password": "secret-password",
                },
            )
        sent = runtime.call_agent_failure_analyst.call_args.args[3]
        self.assertNotIn("secret-token", str(sent))
        self.assertNotIn("secret-password", str(sent))

    def test_claimed_continuation_is_recovered_once_and_local_duplicates_wait(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "running",
            "current_step": "create_suite",
        }
        runtime.get_agent_step_row.return_value = {"status": "succeeded"}
        runtime.get_current_project.return_value = {"project_id": 7}
        runtime.current_platform_author.return_value = "tester"
        run_id = "run-durable-continuation"
        composition._CONTINUATION_RUNS.discard(run_id)
        thread = MagicMock()
        with patch.object(composition.threading, "Thread", return_value=thread) as factory:
            first = composition.start_agent_script_preparation_continuation(
                runtime, run_id, recover=True
            )
            second = composition.start_agent_script_preparation_continuation(
                runtime, run_id, recover=True
            )
        self.assertTrue(first)
        self.assertFalse(second)
        factory.assert_called_once()
        factory.call_args.kwargs["target"]()
        runtime.run_agent_script_preparation_continue_workflow.assert_called_once()
        self.assertNotIn(run_id, composition._CONTINUATION_RUNS)

    def test_succeeded_prepare_step_is_claimed_and_dispatched_after_crash(self):
        runtime = MagicMock()
        run = {"status": "running", "current_step": SCRIPT_PREPARATION_STEP_KEY}
        runtime.get_agent_run_row.side_effect = lambda _run_id: dict(run)
        runtime.get_agent_step_row.return_value = {"status": "succeeded"}
        runtime.claim_agent_script_preparation_continue.side_effect = (
            lambda _run_id: run.update(
                {"status": "running", "current_step": "create_suite"}
            )
            or True
        )
        runtime.get_current_project.return_value = {"project_id": 7}
        runtime.current_platform_author.return_value = "tester"
        run_id = "run-prepare-crash"
        composition._CONTINUATION_RUNS.discard(run_id)
        thread = MagicMock()
        with patch.object(composition.threading, "Thread", return_value=thread):
            started = composition.start_agent_script_preparation_continuation(
                runtime, run_id, recover=True
            )
        self.assertTrue(started)
        runtime.claim_agent_script_preparation_continue.assert_called_once_with(run_id)
        thread.start.assert_called_once()
        composition._CONTINUATION_RUNS.discard(run_id)

    def test_delayed_continuation_does_not_revive_terminal_agent_run(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "succeeded",
            "current_step": "run_suite",
        }
        runtime.get_agent_step_row.return_value = {"status": "succeeded"}
        active = {"value": False}

        class Barrier:
            def __enter__(inner_self):
                active["value"] = True
                return inner_self

            def __exit__(inner_self, *_args):
                active["value"] = False

        callback = MagicMock()
        with (
            patch.object(
                agent_adapter, "script_preparation_barrier", return_value=Barrier()
            ),
            patch.object(agent_adapter, "reconcile_items_for_web"),
        ):
            result = agent_adapter.finish_with_script_preparation_barrier(
                runtime, "run-1", callback
            )
        self.assertIsNone(result)
        callback.assert_not_called()
        self.assertFalse(active["value"])

    def test_interrupted_suite_create_or_execution_is_failed_under_barrier(self):
        for current_step in ("create_suite", "run_suite"):
            with self.subTest(current_step=current_step):
                runtime = MagicMock()
                runtime.get_agent_run_row.return_value = {
                    "status": "running",
                    "current_step": current_step,
                    "suite_uid": None,
                }
                runtime.get_agent_step_row.side_effect = lambda _run_id, step: {
                    "status": "succeeded"
                    if step == SCRIPT_PREPARATION_STEP_KEY
                    else "running"
                }
                runtime.get_agent_step_output.return_value = {}
                callback = MagicMock()
                with (
                    patch.object(
                        agent_adapter,
                        "script_preparation_barrier",
                        return_value=nullcontext(),
                    ),
                    patch.object(agent_adapter, "reconcile_items_for_web"),
                ):
                    result = agent_adapter.finish_with_script_preparation_barrier(
                        runtime, "run-1", callback
                    )
                self.assertIsNone(result)
                callback.assert_not_called()
                runtime.mark_agent_workflow_failed.assert_called_once()

    def test_created_suite_row_is_discovered_before_create_step_link_is_saved(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "running",
            "current_step": "create_suite",
            "suite_uid": None,
        }
        runtime.get_agent_step_row.side_effect = lambda _run_id, step: {
            "status": "succeeded"
            if step == SCRIPT_PREPARATION_STEP_KEY
            else "running"
        }
        runtime.get_agent_step_output.return_value = {}
        runtime.list_test_suites_from_mysql.return_value = [
            {
                "id": "suite-1",
                "description": "Agent run run-1 自动创建。",
                "items": [],
            }
        ]
        callback = MagicMock(return_value="continued")
        with (
            patch.object(
                agent_adapter, "script_preparation_barrier", return_value=nullcontext()
            ),
            patch.object(agent_adapter, "reconcile_items_for_web"),
            patch.object(
                agent_adapter.state_machine,
                "get_script_preparation_snapshot",
                return_value={"counts": {"total": 1, "terminal": 1}},
            ),
        ):
            result = agent_adapter.finish_with_script_preparation_barrier(
                runtime, "run-1", callback
            )
        self.assertEqual(result, "continued")
        callback.assert_called_once()
        runtime.mark_agent_workflow_failed.assert_not_called()

    def test_explicit_null_revision_is_compared_in_agent_action_and_generation(self):
        result = self.service.run("run-1", [make_plan()])
        item = result["snapshot"]["items"][0]
        with self.assertRaises(ScriptPreparationConflict):
            self.service.apply_action(
                "run-1",
                item["item_id"],
                action=ACTION_EXECUTE,
                expected_revision_id=None,
            )

        runtime = MagicMock()
        runtime.validate_module_name.side_effect = lambda value: value
        runtime.get_generated_script_filename_from_plan_filename.return_value = (
            "login.spec.ts"
        )
        runtime.agent_project_language.return_value = "zh-CN"
        runtime.get_script_file.return_value = MagicMock()
        runtime.get_test_asset_by_path.return_value = {"current_revision_id": 2}
        with patch.object(
            agent_adapter, "acquire_script_target_lease", return_value=nullcontext()
        ):
            with self.assertRaises(ScriptPreparationConflict):
                agent_adapter.generate_script(
                    runtime,
                    "run-1",
                    SCRIPT_PREPARATION_STEP_KEY,
                    {
                        "module_name": "account",
                        "plan_filename": "login.md",
                        "_expected_script_revision_id": None,
                    },
                )

    def test_cancelled_web_action_closes_state_and_agent_run(self):
        runtime = MagicMock()
        runtime.OpencodeTaskCancelled = CancelledError
        runtime.get_agent_run_row.return_value = {"status": "cancelling"}
        cancellation = CancelledError("cancelled")
        with (
            patch.object(
                agent_adapter.state_machine,
                "apply_script_preparation_action",
                side_effect=cancellation,
            ),
            patch.object(
                agent_adapter.state_machine,
                "cancel_script_preparation_interrupted",
            ) as cancel_state,
        ):
            with self.assertRaises(ScriptPreparationConflict):
                agent_adapter.apply_action_for_web(
                    runtime,
                    "run-1",
                    "item-1",
                    action=ACTION_EXECUTE,
                    expected_revision_id=1,
                )
        cancel_state.assert_called_once_with("run-1", "cancelled")
        runtime.mark_agent_workflow_cancelled.assert_called_once_with(
            "run-1", "cancelled"
        )

    def test_awaiting_cancel_cas_loser_does_not_overwrite_claimed_action(self):
        runtime = MagicMock()
        runtime.update_agent_run.return_value = (
            False,
            {
                "status": "running",
                "current_step": SCRIPT_PREPARATION_STEP_KEY,
            },
        )
        with patch.object(
            agent_adapter, "script_preparation_barrier", return_value=nullcontext()
        ):
            accepted, run, result = agent_adapter.cancel_awaiting_agent_workflow(
                runtime, "run-1", "awaiting_script_action"
            )
        self.assertFalse(accepted)
        self.assertEqual(run["status"], "running")
        self.assertEqual(result, {})
        self.assertEqual(
            runtime.update_agent_run.call_args.kwargs["expected_status"],
            "awaiting_script_action",
        )
        runtime.agent_request_cancel.assert_not_called()
        runtime.mark_agent_workflow_cancelled.assert_not_called()

    def test_awaiting_cancel_winner_finalizes_state_inside_barrier(self):
        runtime = MagicMock()
        events = []

        class Barrier:
            def __enter__(inner_self):
                events.append("enter")
                return inner_self

            def __exit__(inner_self, *_args):
                events.append("exit")

        runtime.update_agent_run.side_effect = lambda *_args, **_kwargs: (
            events.append("cas") or (True, {"status": "cancelling"})
        )
        runtime.agent_request_cancel.side_effect = lambda _run_id: (
            events.append("job") or {"cancel_requested": True}
        )
        runtime.update_agent_step.side_effect = lambda *_args, **_kwargs: events.append(
            "step"
        )
        runtime.mark_agent_workflow_cancelled.side_effect = (
            lambda *_args: events.append("run") or True
        )
        statuses = iter(("cancelling", "cancelled"))
        runtime.get_agent_run_row.side_effect = lambda _run_id: (
            events.append("get") or {"status": next(statuses)}
        )
        with (
            patch.object(
                agent_adapter, "script_preparation_barrier", return_value=Barrier()
            ),
            patch.object(
                agent_adapter.state_machine,
                "cancel_script_preparation_interrupted",
                side_effect=lambda *_args: events.append("state"),
            ),
        ):
            accepted, run, result = agent_adapter.cancel_awaiting_agent_workflow(
                runtime, "run-1", "awaiting_script_action"
            )
        self.assertTrue(accepted)
        self.assertEqual(run["status"], "cancelled")
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(
            events, ["enter", "cas", "job", "get", "state", "run", "get", "exit"]
        )
        runtime.update_agent_step.assert_not_called()

    def test_late_resume_claims_queued_run_and_yields_to_cancellation(self):
        for step in (
            "upload_requirement",
            "analyze_requirement",
            "review_modules",
            "generate_plans",
            SCRIPT_PREPARATION_STEP_KEY,
            "create_suite",
            "run_suite",
        ):
            with self.subTest(step=step):
                runtime = MagicMock()
                runtime.update_agent_run.return_value = (
                    True,
                    {"status": "running", "current_step": step},
                )
                claimed = agent_adapter.claim_agent_resume(runtime, "run-1", step)
                self.assertTrue(claimed)
                self.assertEqual(
                    runtime.update_agent_run.call_args.kwargs["expected_status"],
                    "queued",
                )
                self.assertEqual(
                    runtime.update_agent_run.call_args.kwargs["current_step"], step
                )

        runtime = MagicMock()
        runtime.update_agent_run.return_value = (
            False,
            {"status": "cancelling", "current_step": "run_suite"},
        )
        with patch.object(agent_adapter, "finalize_agent_cancellation") as finalize:
            claimed = agent_adapter.claim_agent_resume(
                runtime, "run-1", "run_suite"
            )
        self.assertFalse(claimed)
        finalize.assert_called_once_with(runtime, "run-1", "用户请求取消。")

    def test_step_and_preparation_updates_cannot_revive_cancelling_run(self):
        runtime = MagicMock()
        runtime.OpencodeTaskCancelled = CancelledError
        runtime.get_agent_run_row.return_value = {"status": "running"}
        runtime.update_agent_run.return_value = (
            False,
            {"status": "cancelling"},
        )
        with self.assertRaises(CancelledError):
            agent_adapter.claim_agent_step_start(runtime, "run-1", "review_modules")
        self.assertEqual(
            runtime.update_agent_run.call_args.kwargs["expected_status"], "running"
        )

        runtime.update_agent_run.reset_mock()
        with self.assertRaises(CancelledError):
            agent_adapter.update_preparation_agent_run(
                runtime,
                "run-1",
                status="awaiting_script_action",
                current_step=SCRIPT_PREPARATION_STEP_KEY,
            )
        self.assertEqual(
            runtime.update_agent_run.call_args.kwargs["expected_status"], "running"
        )

    def test_terminal_publish_closes_step_and_event_before_atomic_commit(self):
        for terminal, expected, current_step in (
            ("cancelled", "cancelling", SCRIPT_PREPARATION_STEP_KEY),
            ("failed", "running", "run_suite"),
        ):
            with self.subTest(terminal=terminal):
                events = []
                runtime = MagicMock()
                runtime.require_platform_database.return_value = {}
                runtime.get_agent_runs_table.return_value = "agent_runs"
                runtime.get_agent_run_steps_table.return_value = "agent_steps"
                runtime.get_agent_run_events_table.return_value = "agent_events"
                runtime.get_current_project_id.return_value = 7
                runtime.validate_uid.side_effect = lambda value, _field: value
                runtime.current_time_ms.return_value = 123
                runtime.get_agent_run_row.return_value = {"status": terminal}
                connection_context = MagicMock()
                connection = connection_context.__enter__.return_value
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = {
                    "status": expected,
                    "current_step": current_step,
                }
                cursor.rowcount = 1

                def execute(sql, *_args):
                    if "UPDATE agent_runs SET status" in sql:
                        events.append("terminal")
                    elif "UPDATE agent_steps" in sql:
                        events.append("step")

                cursor.execute.side_effect = execute
                runtime.insert_agent_event_row.side_effect = (
                    lambda *_args, **_kwargs: events.append("event")
                )
                connection.commit.side_effect = lambda: events.append("commit")
                runtime.platform_mysql_connection.return_value = connection_context

                applied, run = agent_adapter.publish_agent_terminal(
                    runtime,
                    "run-1",
                    expected_status=expected,
                    terminal_status=terminal,
                    error="terminal reason",
                    fallback_step=current_step,
                )
                self.assertTrue(applied)
                self.assertEqual(run["status"], terminal)
                self.assertLess(max(i for i, value in enumerate(events) if value in {"step", "event"}), events.index("terminal"))
                self.assertEqual(events[-1], "commit")

    def test_terminal_run_rejects_preparation_child_tail(self):
        runtime = MagicMock()
        runtime.require_platform_database.return_value = {}
        runtime.get_agent_runs_table.return_value = "agent_runs"
        runtime.get_agent_run_steps_table.return_value = "agent_steps"
        runtime.get_agent_run_events_table.return_value = "agent_events"
        runtime.get_current_project_id.return_value = 7
        runtime.validate_uid.side_effect = lambda value, _field: value
        runtime.current_time_ms.return_value = 123
        connection_context = MagicMock()
        connection = connection_context.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"status": "failed"}
        runtime.platform_mysql_connection.return_value = connection_context
        with self.assertRaises(ScriptPreparationConflict):
            agent_adapter.persist_preparation_state_atomic(
                runtime,
                "run-1",
                step_values={
                    "status": "running",
                    "output_data": {"items": []},
                    "counts": {"total": 1},
                },
                event_values={
                    "event_type": "status",
                    "message": "late",
                    "payload": {},
                },
                run_values={"status": "running", "error": ""},
            )
        self.assertEqual(cursor.execute.call_count, 1)
        runtime.insert_agent_event_row.assert_not_called()
        connection.commit.assert_not_called()

    def test_cancel_before_atomic_step_start_writes_no_child_state(self):
        runtime = MagicMock()
        runtime.OpencodeTaskCancelled = CancelledError
        runtime.require_platform_database.return_value = {}
        runtime.get_agent_runs_table.return_value = "agent_runs"
        runtime.get_agent_run_steps_table.return_value = "agent_steps"
        runtime.get_agent_run_events_table.return_value = "agent_events"
        runtime.get_current_project_id.return_value = 7
        runtime.validate_uid.side_effect = lambda value, _field: value
        runtime.current_time_ms.return_value = 123
        connection_context = MagicMock()
        connection = connection_context.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"status": "cancelling"}
        runtime.platform_mysql_connection.return_value = connection_context
        with self.assertRaises(CancelledError):
            agent_adapter.start_agent_step_atomic(
                runtime, "run-1", "review_modules", {"modules": 1}
            )
        self.assertEqual(cursor.execute.call_count, 1)
        runtime.insert_agent_event_row.assert_not_called()
        connection.commit.assert_not_called()

    def test_running_or_cancelling_manual_action_is_recovered_from_polling(self):
        cases = (
            ("running", "running", SCRIPT_PREPARATION_STEP_KEY),
            ("cancelling", "succeeded", SCRIPT_PREPARATION_STEP_KEY),
            ("cancelling", "succeeded", "create_suite"),
            ("cancelling", "succeeded", "run_suite"),
        )
        for status, step_status, current_step in cases:
            with self.subTest(status=status, current_step=current_step):
                runtime = MagicMock()
                runtime.get_agent_run_row.return_value = {
                    "status": status,
                    "current_step": current_step,
                }
                runtime.get_agent_step_row.return_value = {"status": step_status}
                runtime.get_current_project.return_value = {"project_id": 7}
                runtime.current_platform_author.return_value = "tester"
                runtime.use_project_context.return_value = nullcontext()
                runtime.use_author_context.return_value = nullcontext()
                run_id = f"run-recover-{status}-{current_step}"
                composition._CONTINUATION_RUNS.discard(run_id)
                thread = MagicMock()
                with (
                    patch.object(
                        composition.threading, "Thread", return_value=thread
                    ) as thread_factory,
                    patch.object(
                        agent_adapter, "recover_interrupted_for_web"
                    ) as recover,
                ):
                    started = composition.start_agent_script_preparation_continuation(
                        runtime, run_id, recover=True
                    )
                    thread_factory.call_args.kwargs["target"]()
                self.assertTrue(started)
                recover.assert_called_once_with(runtime, run_id)
                runtime.run_agent_script_preparation_continue_workflow.assert_not_called()

    def test_initial_preparation_barrier_finalizes_cancelled_stage(self):
        runtime = MagicMock()
        runtime.OpencodeTaskCancelled = CancelledError
        runtime.get_agent_run_row.return_value = {"status": "cancelling"}
        cancellation = CancelledError("cancelled")
        with (
            patch.object(
                agent_adapter, "script_preparation_plan_barrier", return_value=nullcontext()
            ),
            patch.object(
                agent_adapter.state_machine,
                "run_agent_script_preparation",
                side_effect=cancellation,
            ),
            patch.object(
                agent_adapter.state_machine,
                "cancel_script_preparation_interrupted",
            ) as cancel_state,
        ):
            with self.assertRaises(CancelledError):
                agent_adapter.run_agent_script_preparation_with_barrier(
                    runtime, "run-1", [make_plan()]
                )
        cancel_state.assert_called_once_with("run-1", "cancelled")

    def test_claimed_continuation_cancel_is_terminalized_under_barrier(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {
            "status": "cancelling",
            "current_step": "create_suite",
        }
        with (
            patch.object(
                agent_adapter, "script_preparation_barrier", return_value=nullcontext()
            ),
            patch.object(
                agent_adapter.state_machine,
                "cancel_script_preparation_interrupted",
            ) as cancel_state,
        ):
            recovered = agent_adapter.recover_interrupted_for_web(runtime, "run-1")
        self.assertTrue(recovered)
        cancel_state.assert_called_once_with("run-1", "Agent 任务已取消。")
        runtime.mark_agent_workflow_cancelled.assert_called_once_with(
            "run-1", "Agent 任务已取消。"
        )

    def test_orphan_agent_suite_is_reused_and_completed(self):
        runtime = MagicMock()
        suite = {
            "id": "suite-1",
            "description": "Agent run run-1 自动创建。",
            "items": [],
        }
        runtime.list_test_suites_from_mysql.return_value = [suite]
        completed = {
            **suite,
            "items": [
                {
                    "item_id": 1,
                    "module_name": "account",
                    "filename": "login.spec.ts",
                }
            ],
        }
        runtime.add_test_suite_items_in_mysql.return_value = completed
        items = [{"module_name": "account", "filename": "login.spec.ts"}]

        result = agent_adapter.find_or_create_agent_suite(
            runtime, "run-1", "Agent-login", items
        )

        self.assertEqual(result, completed)
        runtime.create_test_suite_in_mysql.assert_not_called()
        runtime.add_test_suite_items_in_mysql.assert_called_once_with(
            "suite-1", items
        )
        runtime.agent_generate_script_for_plan.assert_not_called()

    def test_agent_completion_and_cancel_transitions_use_opposing_cas(self):
        runtime = MagicMock()
        runtime.get_agent_run_row.return_value = {"status": "cancelling"}
        with (
            patch.object(
                agent_adapter,
                "publish_agent_terminal",
                return_value=(False, {"status": "cancelling"}),
            ) as publish,
            patch.object(
                agent_adapter.state_machine,
                "cancel_script_preparation_interrupted",
            ) as cancel_state,
        ):
            completed = agent_adapter.complete_agent_workflow(
                runtime, "run-1", "succeeded", {"passed": 1}
            )
        self.assertFalse(completed)
        self.assertEqual(
            publish.call_args.kwargs["expected_status"],
            "running",
        )
        cancel_state.assert_called_once()
        runtime.mark_agent_workflow_cancelled.assert_called_once()
        runtime.append_agent_event.assert_not_called()

        runtime.reset_mock()
        runtime.update_agent_run.return_value = (False, {"status": "succeeded"})
        accepted, run = agent_adapter.request_agent_workflow_cancel(
            runtime, "run-1", "running"
        )
        self.assertFalse(accepted)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(
            runtime.update_agent_run.call_args.kwargs["expected_status"],
            "running",
        )

    def test_failed_persisted_suite_execution_cannot_finish_agent(self):
        outputs = (
            {
                "result": {"ok": False, "status": "failed"},
                "summary": {"failed": 1},
            },
            {"result": {"ok": True, "status": "running"}},
            {"result": {"ok": True, "status": "blocked"}},
            {"summary": {"passed": 1}},
        )
        for output in outputs:
            with self.subTest(output=output):
                runtime = MagicMock()
                runtime.get_agent_run_row.return_value = {
                    "status": "running",
                    "current_step": "run_suite",
                }
                runtime.get_agent_step_row.side_effect = lambda _run_id, step: {
                    "status": "succeeded"
                    if step in {SCRIPT_PREPARATION_STEP_KEY, "run_suite"}
                    else "queued"
                }
                runtime.get_agent_step_output.return_value = output
                callback = MagicMock()
                with (
                    patch.object(
                        agent_adapter,
                        "script_preparation_barrier",
                        return_value=nullcontext(),
                    ),
                    patch.object(agent_adapter, "reconcile_items_for_web"),
                ):
                    result = agent_adapter.finish_with_script_preparation_barrier(
                        runtime, "run-1", callback
                    )
                self.assertIsNone(result)
                callback.assert_not_called()
                runtime.mark_agent_workflow_failed.assert_called_once()

    def test_recovered_agent_suite_removes_abandoned_script_exactly(self):
        runtime = MagicMock()
        item_a = {
            "item_id": 1,
            "module_name": "account",
            "filename": "a.spec.ts",
        }
        item_b = {
            "item_id": 2,
            "module_name": "account",
            "filename": "b.spec.ts",
        }
        suite = {
            "id": "suite-1",
            "description": "Agent run run-1 自动创建。",
            "items": [item_a, item_b],
        }
        runtime.list_test_suites_from_mysql.return_value = [suite]
        runtime.delete_test_suite_item_in_mysql.return_value = {
            **suite,
            "items": [item_a],
        }
        desired = [
            {
                "module_name": "account",
                "filename": "a.spec.ts",
                "display_name": "A",
            }
        ]

        result = agent_adapter.find_or_create_agent_suite(
            runtime, "run-1", "Agent-suite", desired
        )

        self.assertEqual(result["items"], [item_a])
        runtime.delete_test_suite_item_in_mysql.assert_called_once_with(
            "suite-1", 2
        )
        runtime.add_test_suite_items_in_mysql.assert_not_called()

    def test_agent_suite_rejects_extra_item_added_during_reorder(self):
        runtime = MagicMock()
        item_a = {
            "item_id": 1,
            "module_name": "account",
            "filename": "a.spec.ts",
        }
        item_b = {
            "item_id": 2,
            "module_name": "account",
            "filename": "b.spec.ts",
        }
        item_c = {
            "item_id": 3,
            "module_name": "account",
            "filename": "c.spec.ts",
        }
        suite = {
            "id": "suite-1",
            "description": "Agent run run-1 自动创建。",
            "items": [item_b, item_a],
        }
        runtime.list_test_suites_from_mysql.return_value = [suite]
        runtime.reorder_test_suite_items_in_mysql.return_value = {
            **suite,
            "items": [item_a, item_b, item_c],
        }
        desired = [
            {"module_name": "account", "filename": "a.spec.ts"},
            {"module_name": "account", "filename": "b.spec.ts"},
        ]

        with self.assertRaisesRegex(RuntimeError, "发生变化"):
            agent_adapter.find_or_create_agent_suite(
                runtime, "run-1", "Agent-suite", desired
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
    def tearDown(self):
        try:
            import app

            with app.AGENT_RUN_TASK_LOCK:
                app.AGENT_RUN_TASKS.clear()
            app.AGENT_RUN_TASK_CONTEXT.run_id = ""
            app.AGENT_RUN_TASK_CONTEXT.worker_token = ""
        finally:
            super().tearDown()

    def test_failure_and_cancel_use_opposing_terminal_cas(self):
        import app

        cancelled = {"status": "cancelled"}
        with (
            patch.object(
                app,
                "get_agent_run_row",
                return_value={"status": "running", "current_step": "run_suite"},
            ),
            patch.object(
                app.script_preparation_agent_adapter,
                "publish_agent_terminal",
                return_value=(False, {"status": "cancelling"}),
            ),
            patch.object(app, "agent_fail_step") as fail_step,
            patch.object(app, "append_agent_event") as event,
            patch.object(
                app.script_preparation_agent_adapter,
                "finalize_agent_cancellation",
                side_effect=lambda *_args: cancelled.update(status="cancelled"),
            ) as finalize,
        ):
            failed = app.mark_agent_workflow_failed(
                "run-1", RuntimeError("worker failed"), "run_suite"
            )
        self.assertFalse(failed)
        self.assertEqual(cancelled["status"], "cancelled")
        finalize.assert_called_once()
        fail_step.assert_not_called()
        event.assert_not_called()

        with (
            patch.object(
                app,
                "get_agent_run_row",
                return_value={"status": "running", "current_step": "run_suite"},
            ),
            patch.object(
                app.script_preparation_agent_adapter,
                "publish_agent_terminal",
                return_value=(True, {"status": "failed"}),
            ),
            patch.object(
                app, "update_agent_run", return_value=(False, {"status": "failed"})
            ),
            patch.object(app, "agent_fail_step") as fail_step,
            patch.object(app, "append_agent_event") as event,
        ):
            failed = app.mark_agent_workflow_failed(
                "run-1", RuntimeError("worker failed"), "run_suite"
            )
            cancel_accepted, run = agent_adapter.request_agent_workflow_cancel(
                app, "run-1", "running"
            )
        self.assertTrue(failed)
        self.assertFalse(cancel_accepted)
        self.assertEqual(run["status"], "failed")
        fail_step.assert_not_called()
        event.assert_not_called()

        with (
            patch.object(
                app, "get_agent_run_row", return_value={"status": "succeeded"}
            ),
            patch.object(
                app.script_preparation_agent_adapter,
                "publish_agent_terminal",
                return_value=(False, {"status": "succeeded"}),
            ) as publish,
            patch.object(app, "list_agent_steps") as steps,
            patch.object(app, "append_agent_event") as event,
        ):
            cancelled = app.mark_agent_workflow_cancelled(
                "run-1", RuntimeError("late cancel")
        )
        self.assertFalse(cancelled)
        publish.assert_called_once()
        steps.assert_not_called()
        event.assert_not_called()

    def test_new_agent_workflow_does_not_use_resume_claim(self):
        import app

        preparation = {
            "paused": False,
            "final_scripts": [],
            "counts": {"total": 1, "abandoned": 1},
        }
        with (
            patch.object(app, "agent_register_task"),
            patch.object(app, "use_project_context", return_value=nullcontext()),
            patch.object(app, "use_author_context", return_value=nullcontext()),
            patch.object(
                app,
                "get_agent_run_row",
                return_value={"requirement_uid": "requirement-1"},
            ),
            patch.object(app, "restore_agent_run_project_language"),
            patch.object(app, "get_requirement_by_uid", return_value={"title": "R"}),
            patch.object(
                app.script_preparation_agent_adapter,
                "claim_agent_workflow_start",
                return_value=True,
            ) as start_claim,
            patch.object(app, "update_agent_step"),
            patch.object(app, "append_agent_event"),
            patch.object(app, "agent_analyze_requirement", return_value=[]),
            patch.object(app, "agent_review_modules", return_value=[]),
            patch.object(app, "agent_generate_plans", return_value=[make_plan()]),
            patch.object(
                app.script_preparation_agent_adapter,
                "run_agent_script_preparation_with_barrier",
                return_value=preparation,
            ),
            patch.object(app, "finish_agent_after_script_preparation"),
            patch.object(app.script_preparation_agent_adapter, "claim_agent_resume") as claim,
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_cleanup_task"),
            patch.object(app, "mark_agent_workflow_failed") as failed,
        ):
            app.run_agent_workflow("run-1", {"project_id": 1}, "tester")
        start_claim.assert_called_once_with(app, "run-1", "upload_requirement")
        claim.assert_not_called()
        failed.assert_not_called()

    def test_worker_generation_does_not_inherit_cancel_or_cleanup_new_resume(self):
        import app

        first = app.agent_register_task("run-1")
        app.agent_request_cancel("run-1")
        second = app.agent_register_task("run-1", replace=True)
        self.assertNotEqual(first, second)
        self.assertFalse(app.AGENT_RUN_TASKS["run-1"]["cancel_requested"])

        app.AGENT_RUN_TASK_CONTEXT.run_id = "run-1"
        app.AGENT_RUN_TASK_CONTEXT.worker_token = first
        self.assertTrue(app.agent_is_cancelled("run-1"))
        app.agent_cleanup_task("run-1")
        self.assertEqual(app.AGENT_RUN_TASKS["run-1"]["worker_token"], second)

        app.AGENT_RUN_TASK_CONTEXT.worker_token = second
        app.agent_cleanup_task("run-1")
        self.assertNotIn("run-1", app.AGENT_RUN_TASKS)

    def test_polling_does_not_dispatch_over_live_agent_worker(self):
        runtime = MagicMock()
        runtime.agent_has_live_task.return_value = True
        for recover in (False, True):
            with self.subTest(recover=recover), patch.object(
                composition.threading, "Thread"
            ) as thread:
                started = composition.start_agent_script_preparation_continuation(
                    runtime, "run-1", recover=recover
                )
                self.assertFalse(started)
                thread.assert_not_called()

    def test_late_stage_resume_claims_before_finishing(self):
        import app

        for from_step in ("create_suite", "run_suite"):
            with self.subTest(from_step=from_step):
                suite = {"id": "suite-1", "items": []}
                with (
                    patch.object(app, "agent_register_task"),
                    patch.object(
                        app, "use_project_context", return_value=nullcontext()
                    ),
                    patch.object(app, "use_author_context", return_value=nullcontext()),
                    patch.object(
                        app,
                        "get_agent_run_row",
                        return_value={"status": "queued", "requirement_uid": "r-1"},
                    ),
                    patch.object(
                        app.script_preparation_agent_adapter,
                        "claim_agent_resume",
                        return_value=True,
                    ) as claim,
                    patch.object(app, "restore_agent_run_project_language"),
                    patch.object(
                        app, "get_requirement_by_uid", return_value={"title": "R"}
                    ),
                    patch.object(app, "append_agent_event"),
                    patch.object(
                        app,
                        "require_agent_step_list_output",
                        side_effect=lambda _run_id, step, _field: (
                            [{"module_name": "account"}]
                            if step == "review_modules"
                            else [make_plan()]
                        ),
                    ),
                    patch.object(
                        app.agent_script_preparation,
                        "get_script_preparation_snapshot",
                        return_value={"paused": False, "items": []},
                    ),
                    patch.object(
                        app,
                        "get_prepared_scripts",
                        return_value=[
                            {"module_name": "account", "filename": "a.spec.ts"}
                        ],
                    ),
                    patch.object(app, "get_agent_step_output", return_value={"suite": suite}),
                    patch.object(app, "finish_agent_after_script_preparation") as finish,
                    patch.object(app, "agent_set_current_job"),
                    patch.object(app, "agent_cleanup_task"),
                    patch.object(app, "mark_agent_workflow_failed") as failed,
                ):
                    app.run_agent_resume_workflow(
                        "run-1", {"project_id": 1}, "tester", from_step
                    )
                claim.assert_called_once_with(app, "run-1", from_step)
                finish.assert_called_once()
                failed.assert_not_called()

    def test_prepare_scripts_resume_claims_queued_run_before_state_machine(self):
        import app

        preparation = {"paused": False, "final_scripts": [], "items": []}
        with (
            patch.object(app, "agent_register_task", return_value="worker-1"),
            patch.object(app, "use_project_context", return_value=nullcontext()),
            patch.object(app, "use_author_context", return_value=nullcontext()),
            patch.object(
                app,
                "get_agent_run_row",
                return_value={"status": "queued", "requirement_uid": "r-1"},
            ),
            patch.object(
                app.script_preparation_agent_adapter,
                "claim_agent_resume",
                return_value=True,
            ) as claim,
            patch.object(app, "restore_agent_run_project_language"),
            patch.object(app, "get_requirement_by_uid", return_value={"title": "R"}),
            patch.object(app, "append_agent_event"),
            patch.object(
                app,
                "require_agent_step_list_output",
                side_effect=lambda _run, step, _field: (
                    [{"module_name": "account"}]
                    if step == "review_modules"
                    else [make_plan()]
                ),
            ),
            patch.object(
                app.script_preparation_agent_adapter,
                "run_agent_script_preparation_with_barrier",
                return_value=preparation,
            ) as prepare,
            patch.object(app, "finish_agent_after_script_preparation") as finish,
            patch.object(app, "agent_set_current_job"),
            patch.object(app, "agent_cleanup_task"),
            patch.object(app, "mark_agent_workflow_failed") as failed,
        ):
            app.run_agent_resume_workflow(
                "run-1", {"project_id": 1}, "tester", SCRIPT_PREPARATION_STEP_KEY
            )
        claim.assert_called_once_with(app, "run-1", SCRIPT_PREPARATION_STEP_KEY)
        prepare.assert_called_once()
        finish.assert_called_once()
        failed.assert_not_called()

    def test_agent_suite_failure_persists_failed_step_with_output(self):
        import app

        suite = {
            "id": "suite-1",
            "name": "suite",
            "items": [{"module_name": "account", "filename": "a.spec.ts"}],
        }
        outcomes = (
            {
                "ok": False,
                "status": "failed",
                "error": "playwright failed",
                "script_results": {},
                "returncode": 1,
            },
            {
                "ok": True,
                "status": "running",
                "script_results": {},
                "returncode": None,
            },
            {
                "ok": True,
                "status": "blocked",
                "script_results": {},
                "returncode": None,
            },
        )
        for failed in outcomes:
            with self.subTest(status=failed["status"]):
                with (
                    patch.object(app, "agent_start_step"),
                    patch.object(app, "build_setup_targets", return_value=[]),
                    patch.object(app, "resolve_setup_profile", return_value={}),
                    patch.object(
                        app,
                        "build_test_suite_execution_context",
                        return_value={"items": suite["items"]},
                    ),
                    patch.object(
                        app, "stream_test_suite_execution", return_value=iter(())
                    ),
                    patch.object(
                        app, "consume_agent_sse_generator", return_value=failed
                    ),
                    patch.object(
                        app,
                        "build_execution_summary",
                        return_value={
                            "total": 1,
                            "passed": 0,
                            "failed": 1,
                            "skipped": 0,
                            "unknown": 0,
                        },
                    ),
                    patch.object(app, "update_agent_step") as update_step,
                    patch.object(app, "agent_fail_step") as fail_step,
                    patch.object(app, "agent_finish_step") as finish_step,
                ):
                    with self.assertRaises(RuntimeError):
                        app.agent_run_suite("run-1", suite)

            update_step.assert_called_once()
            self.assertEqual(
                update_step.call_args.kwargs["output_data"]["result"], failed
            )
            fail_step.assert_called_once()
            finish_step.assert_not_called()

    def test_update_agent_run_expected_status_is_part_of_database_cas(self):
        import app

        connection_context = MagicMock()
        connection = connection_context.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        with (
            patch.object(app, "require_platform_database", return_value={}),
            patch.object(app, "get_agent_runs_table", return_value="agent_runs"),
            patch.object(app, "get_current_project_id", return_value="project-1"),
            patch.object(app, "current_time_ms", return_value=123),
            patch.object(app, "platform_mysql_connection", return_value=connection_context),
            patch.object(app, "get_agent_run_row", return_value={"status": "running"}),
        ):
            app.update_agent_run(
                "run-1", status="cancelling", expected_status="running"
            )

        sql, parameters = cursor.execute.call_args.args
        self.assertIn("AND status = %s", sql)
        self.assertEqual(parameters[-1], "running")

    def test_resume_reset_uses_observed_status_and_timestamp_cas(self):
        import app

        def run_reset(rowcount):
            connection_context = MagicMock()
            connection = connection_context.__enter__.return_value
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.rowcount = rowcount
            with (
                patch.object(app, "ensure_agent_run_step_rows"),
                patch.object(app, "require_platform_database", return_value={}),
                patch.object(app, "get_agent_runs_table", return_value="agent_runs"),
                patch.object(app, "get_agent_run_steps_table", return_value="agent_steps"),
                patch.object(app, "get_current_project_id", return_value="project-1"),
                patch.object(app, "current_time_ms", return_value=123),
                patch.object(
                    app, "platform_mysql_connection", return_value=connection_context
                ),
                patch.object(app, "append_agent_event"),
                patch.object(app, "get_agent_run_row", return_value={"status": "queued"}),
            ):
                if rowcount:
                    app.reset_agent_run_for_resume_record(
                        "run-1", "prepare_scripts", "failed", 99
                    )
                else:
                    with self.assertRaises(app.AgentItemRetryConflict):
                        app.reset_agent_run_for_resume_record(
                            "run-1", "prepare_scripts", "failed", 99
                        )
            return cursor

        winner = run_reset(1)
        first_sql, first_parameters = winner.execute.call_args_list[0].args
        self.assertIn("status = %s AND updated_at = %s", first_sql)
        self.assertEqual(first_parameters[-2:], ("failed", 99))

        loser = run_reset(0)
        self.assertEqual(loser.execute.call_count, 1)

    def test_resume_rejects_same_run_that_became_active(self):
        import app

        with (
            patch.object(app, "list_agent_item_retry_flows", return_value=[]),
            patch.object(
                app,
                "get_active_agent_run_row",
                return_value={"run_id": "run-1", "status": "running"},
            ),
            patch.object(app, "reset_agent_run_for_resume_record") as reset,
        ):
            with self.assertRaises(app.AgentItemRetryConflict):
                app.reset_agent_run_for_resume(
                    "run-1", "prepare_scripts", "failed", 99
                )
        reset.assert_not_called()

    def test_all_abandoned_skips_suite_creation_and_finishes_safely(self):
        import app

        with (
            patch.object(app, "agent_create_suite") as create_suite,
            patch.object(app, "agent_run_suite") as run_suite,
            patch.object(app, "update_agent_step") as update_step,
            patch.object(app, "append_agent_event"),
            patch.object(
                app.script_preparation_agent_adapter, "clear_agent_suite"
            ) as clear_suite,
            patch.object(
                app.script_preparation_agent_adapter,
                "publish_agent_terminal",
                return_value=(True, {"status": "succeeded_with_unresolved"}),
            ) as publish,
            patch.object(
                app.script_preparation_agent_adapter,
                "finish_with_script_preparation_barrier",
                side_effect=lambda _runtime, _run_id, callback: callback(
                    {
                        "items": [],
                        "counts": {"total": 1, "ready": 0, "abandoned": 1},
                    }
                ),
            ),
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
        clear_suite.assert_called_once_with(app, "run-1")
        self.assertEqual(
            [call.args[1] for call in update_step.call_args_list],
            ["create_suite", "run_suite"],
        )
        self.assertTrue(
            all(call.kwargs["status"] == "skipped" for call in update_step.call_args_list)
        )
        self.assertEqual(
            publish.call_args.kwargs["terminal_status"],
            "succeeded_with_unresolved",
        )

    def test_persisted_suite_is_reused_and_succeeded_execution_is_not_rerun(self):
        import app

        suite = {"id": "suite-1", "name": "existing", "items": [{"module_name": "account", "filename": "login.spec.ts"}]}
        execution = {"summary": {"total": 1, "passed": 1}}
        script = {"module_name": "account", "filename": "login.spec.ts"}
        with (
            patch.object(app, "agent_create_suite") as create_suite,
            patch.object(app, "agent_run_suite") as run_suite,
            patch.object(app, "get_agent_step_row", return_value={"status": "running"}),
            patch.object(app, "agent_finish_step") as finish_step,
            patch.object(app, "append_agent_event"),
            patch.object(
                app.script_preparation_agent_adapter,
                "publish_agent_terminal",
                return_value=(True, {"status": "succeeded"}),
            ) as publish,
            patch.object(app, "serialize_requirement", return_value={}),
        ):
            app._finish_agent_after_script_preparation(
                "run-1",
                {"title": "requirement"},
                [{"module_name": "account"}],
                [make_plan()],
                [script],
                {"counts": {"total": 1, "ready": 1}},
                suite=suite,
                execution=execution,
            )
        create_suite.assert_not_called()
        run_suite.assert_not_called()
        finish_step.assert_called_once_with(
            "run-1",
            "create_suite",
            {"suite": suite},
            {"scripts": 1, "suite_count": 1},
        )
        self.assertEqual(publish.call_args.kwargs["terminal_status"], "succeeded")

    def test_all_abandoned_removes_owned_suite_and_clears_run_reference(self):
        runtime = MagicMock()
        runtime.list_test_suites_from_mysql.return_value = [
            {
                "id": "suite-old",
                "description": "Agent run run-1 自动创建。",
                "items": [{"module_name": "account", "filename": "old.spec.ts"}],
            }
        ]
        suite = agent_adapter.clear_agent_suite(runtime, "run-1")
        self.assertEqual(suite["id"], "suite-old")
        runtime.delete_test_suite_in_mysql.assert_called_once_with("suite-old")
        runtime.update_agent_run.assert_called_once_with("run-1", suite_uid="")

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
