from collections import deque
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_plan_viewer.agent.script_preparation import ScriptPreparationConflict
from test_plan_viewer.script_preparation.manager import (
    ModuleScriptPreparationCancelled,
    ModuleScriptPreparationManager,
    ModuleScriptPreparationServices,
)
from test_plan_viewer.script_preparation.repository import (
    ACTIONABLE_RUN_STATUSES,
    ModuleScriptPreparationConflict,
)
from test_plan_viewer.script_preparation.operations import (
    ModulePreparationOperationFailed,
)


class MemoryRepository:
    def __init__(self):
        self.runs = {}
        self.requests = {}
        self.clock = 100
        self.took_over = False
        self.stale_worker = False

    def get(self, run_id):
        value = self.runs.get(run_id)
        return deepcopy(value) if value else None

    def create(
        self,
        *,
        run_id,
        module_name,
        plan_filenames,
        plan_snapshots,
        client_request_id,
        created_by,
    ):
        if client_request_id and client_request_id in self.requests:
            existing = self.runs[self.requests[client_request_id]]
            if (
                existing["module_name"] == module_name
                and existing["plan_filenames"] == plan_filenames
            ):
                return deepcopy(existing), False
            raise ModuleScriptPreparationConflict("idempotency conflict")
        for value in self.runs.values():
            if value["module_name"] == module_name and value["status"] in {
                "queued",
                "running",
                "failing",
                "awaiting_action",
                "cancelling",
            }:
                if value["plan_filenames"] == plan_filenames:
                    return deepcopy(value), False
                raise ModuleScriptPreparationConflict("active module conflict")
        value = {
            "run_id": run_id,
            "module_name": module_name,
            "status": "queued",
            "plan_filenames": list(plan_filenames),
            "plan_snapshots": deepcopy(plan_snapshots),
            "state": {},
            "action_queue": [],
            "recent_actions": [],
            "cancel_requested": False,
            "current_job_id": None,
            "error": "",
            "created_by": created_by,
            "created_at": self.tick(),
            "updated_at": self.clock,
            "started_at": None,
            "finished_at": None,
        }
        self.runs[run_id] = value
        if client_request_id:
            self.requests[client_request_id] = run_id
        return deepcopy(value), True

    def save_state(self, run_id, state, *, step_status, started=False, finished=False):
        value = self.runs[run_id]
        value["state"] = deepcopy(state)
        if value.get("status") in {"completed", "failed", "cancelled", "failing"}:
            pass
        elif value.get("cancel_requested"):
            value["status"] = "cancelling"
        elif (
            (finished or step_status == "succeeded")
            and not value["action_queue"]
            and not value.get("worker_token")
        ):
            value["status"] = "completed"
            value["finished_at"] = self.tick()
        elif step_status == "awaiting_action":
            value["status"] = "awaiting_action"
        else:
            value["status"] = "running"
        if started and value["started_at"] is None:
            value["started_at"] = self.tick()
        value["updated_at"] = self.tick()

    def update_status(self, run_id, status, *, error=None, finished=False):
        value = self.runs[run_id]
        current = value.get("status")
        if current in {"completed", "failed", "cancelled"}:
            status = current
        elif status == "failed" and (
            value.get("cancel_requested") or current == "cancelling"
        ):
            status = "cancelling"
        elif status != "cancelled" and value.get("cancel_requested"):
            status = "cancelling"
        value["status"] = status
        if error is not None:
            value["error"] = error
        if finished or status in {"completed", "failed", "cancelled"}:
            value["finished_at"] = self.tick()
        value["updated_at"] = self.tick()
        return deepcopy(value)

    def clear_expired_worker(self, run_id, statuses):
        value = self.runs[run_id]
        if value.get("status") in statuses and self.stale_worker:
            value["worker_token"] = ""
            value["worker_lease_until"] = None
            return True
        return False

    def assert_actionable(self, run_id, *, worker=False):
        value = self.runs[run_id]
        allowed = set(ACTIONABLE_RUN_STATUSES)
        if worker:
            allowed.add("running")
        if value["status"] not in allowed:
            raise ModuleScriptPreparationConflict("not actionable")
        if value["cancel_requested"]:
            raise ModuleScriptPreparationConflict("cancelling")
        if value.get("worker_token") and not worker:
            raise ModuleScriptPreparationConflict("worker busy")
        return deepcopy(value)

    def claim_scope(self, run_id):
        self.runs[run_id]["status"] = "running"

    def enqueue_actions(self, run_id, actions):
        self.assert_actionable(run_id)
        value = self.runs[run_id]
        busy = {item["item_id"] for item in value["action_queue"]}
        if any(item["item_id"] in busy for item in actions):
            raise ModuleScriptPreparationConflict("already queued")
        value["action_queue"].extend(deepcopy(actions))
        value["status"] = "running"
        return deepcopy(value)

    def claim_next_action(self, run_id):
        for item in self.runs[run_id]["action_queue"]:
            if item["state"] == "queued":
                item["state"] = "running"
                return deepcopy(item)
        return None

    def claim_worker(self, run_id, worker_token):
        value = self.runs[run_id]
        recoverable_awaiting = (
            value.get("status") == "awaiting_action"
            and bool(value.get("worker_token"))
            and self.stale_worker
        )
        if value.get("status") not in {
            "queued",
            "running",
            "failing",
            "cancelling",
        } and not recoverable_awaiting:
            if value.get("worker_token") and self.stale_worker:
                value["worker_token"] = None
                value["worker_lease_until"] = None
            return False
        if value.get("worker_token") and value["worker_token"] != worker_token:
            if not self.stale_worker:
                return False
            self.took_over = True
            running = [
                item for item in value["action_queue"] if item["state"] == "running"
            ]
            value["action_queue"] = [
                item for item in value["action_queue"] if item["state"] != "running"
            ]
            value["recent_actions"].extend(
                {**item, "state": "failed", "error": "worker_interrupted"}
                for item in running
            )
        else:
            self.took_over = False
        value["worker_token"] = worker_token
        value["worker_lease_until"] = self.clock + 100
        return True

    def worker_took_over(self):
        return bool(getattr(self, "took_over", False))

    def heartbeat_worker(self, _run_id, **_kwargs):
        return True

    def current_worker_token(self):
        return self.runs.get(next(reversed(self.runs), ""), {}).get("worker_token")

    def release_worker(self, run_id, *, force=False):
        del force
        value = self.runs[run_id]
        value["worker_token"] = None
        value["worker_lease_until"] = None
        if value.get("cancel_requested") and value.get("status") not in {
            "completed",
            "failed",
            "cancelled",
        }:
            value["status"] = "cancelling"
            value["finished_at"] = None
        return True

    def finish_action(self, run_id, action_id, *, error=""):
        value = self.runs[run_id]
        completed = next(
            item
            for item in value["action_queue"]
            if item["action_id"] == action_id
        )
        value["action_queue"] = [
            item
            for item in value["action_queue"]
            if item["action_id"] != action_id
        ]
        value["recent_actions"].append(
            {
                **deepcopy(completed),
                "state": "failed" if error else "succeeded",
                "error": error,
            }
        )

    def request_cancel(self, run_id):
        value = self.runs[run_id]
        for item in value["action_queue"]:
            value["recent_actions"].append(
                {**deepcopy(item), "state": "cancelled"}
            )
        value["action_queue"] = []
        value["cancel_requested"] = True
        value["status"] = "cancelling"
        return deepcopy(value)

    def is_cancel_requested(self, run_id):
        return bool(run_id and self.runs.get(run_id, {}).get("cancel_requested"))

    def set_current_job(self, run_id, job_id):
        self.runs[run_id]["current_job_id"] = job_id or None

    def tick(self):
        self.clock += 1
        return self.clock


class ModuleManagerHarness:
    def __init__(self, root):
        self.root = Path(root)
        self.repository = MemoryRepository()
        self.revision = 0
        self._actual_revision = None
        self.asset_revisions = {}
        self.actual_revision_overrides = {}
        self.target_lease = lambda _module, _filename: nullcontext()
        self.execute_outcomes = deque()
        self.repair_calls = 0
        self.analysis_calls = 0
        self.execute_calls = 0
        self.cancel_during_execute = False

    def manager(self):
        return ModuleScriptPreparationManager(
            ModuleScriptPreparationServices(
                repository=self.repository,
                generate_script=self.generate,
                execute_script=self.execute,
                repair_script=self.repair,
                analyze_failure=self.analyze,
                save_script=self.save,
                build_generation_prompt=lambda plan: (
                    f"generate {plan['plan_filename']}"
                ),
                build_repair_prompt=lambda item, failure: (
                    f"repair {item['filename']}: {failure.get('error', '')}"
                ),
                resolve_script_filename=lambda plan: (
                    f"{Path(plan['plan_filename']).stem}.spec.ts"
                ),
                validate_module_name=lambda value: str(value).strip(),
                validate_plan_filename=lambda value: str(value).strip(),
                get_plan_file=lambda module, filename: (
                    self.root / module / filename
                ),
                current_time_ms=self.repository.tick,
                current_author=lambda: "tester",
                get_project_language=lambda: "zh-CN",
                load_script_content=lambda item: "test('ok', async () => {});",
                get_script_revision=self.actual_for,
                reconcile_script_revision=self.actual_for,
                target_lease=self.target_lease,
            )
        )

    def actual_for(self, item):
        return self.actual_revision_overrides.get(
            item.get("filename"),
            self.asset_revisions.get(item.get("filename"), self.actual_revision),
        )

    @property
    def actual_revision(self):
        return self._actual_revision

    @actual_revision.setter
    def actual_revision(self, value):
        self._actual_revision = value
        for filename in self.asset_revisions:
            self.asset_revisions[filename] = value

    def commit_revision(self, filename, value):
        self._actual_revision = value
        self.asset_revisions[filename] = value

    def generate(self, _run, _step, plan, **_kwargs):
        self.revision += 1
        filename = f"{Path(plan['plan_filename']).stem}.spec.ts"
        self.commit_revision(filename, self.revision)
        return {
            "module_name": plan["module_name"],
            "plan_filename": plan["plan_filename"],
            "filename": filename,
            "asset": {"current_revision_id": self.revision},
        }

    def execute(self, _run, _step, script):
        self.execute_calls += 1
        if self.cancel_during_execute:
            self.repository.request_cancel(_run)
        passed = self.execute_outcomes.popleft() if self.execute_outcomes else True
        if passed:
            return {**script, "execution": {"ok": True, "status": "succeeded"}}
        return {
            **script,
            "execution": {"ok": False, "status": "failed", "error": "timeout"},
            "error": "timeout",
        }

    def repair(self, _run, _step, script, **_kwargs):
        self.repair_calls += 1
        self.revision += 1
        self.commit_revision(script["filename"], self.revision)
        return {**script, "asset": {"current_revision_id": self.revision}}

    def analyze(self, _run, _step, _payload):
        self.analysis_calls += 1
        return {
            "summary": "需要调整定位器",
            "recommended_action": "repair",
            "prompt_patch": "使用 data-testid",
        }

    def save(self, _run, item, content, *, expected_revision_id):
        if str(expected_revision_id) != str(self.actual_for(item)):
            raise ScriptPreparationConflict("revision conflict")
        self.revision += 1
        self.commit_revision(item["filename"], self.revision)
        return {
            **item["current_script"],
            "content": content,
            "asset": {"current_revision_id": self.revision},
        }


class ModuleScriptPreparationManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.harness = ModuleManagerHarness(self.temporary.name)
        self.manager = self.harness.manager()
        module = Path(self.temporary.name) / "登录"
        module.mkdir()
        (module / "正常登录.md").write_text("# 正常登录", encoding="utf-8")
        (module / "异常登录.md").write_text("# 异常登录", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, plans=None, request_id="request-1"):
        return self.manager.create_run(
            module_name="登录",
            plan_filenames=plans or ["正常登录.md"],
            client_request_id=request_id,
        )

    def test_create_is_idempotent_and_rejects_active_module_conflict(self):
        first = self.create()
        second = self.create()
        recovered = self.create(request_id="request-after-lost-response")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertFalse(recovered["created"])
        self.assertEqual(first["run"]["run_id"], second["run"]["run_id"])
        self.assertEqual(first["run"]["run_id"], recovered["run"]["run_id"])
        with self.assertRaises(ModuleScriptPreparationConflict):
            self.create(["异常登录.md"], request_id="request-2")

    def test_generate_execute_success_completes_without_test_suite(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["status"], "succeeded")
        self.assertEqual(snapshot["run_status"], "completed")
        self.assertEqual(snapshot["counts"]["ready"], 1)
        self.assertEqual(
            [stage["stage_type"] for stage in snapshot["items"][0]["history"]],
            ["generate", "execute"],
        )

    def test_terminal_stale_worker_is_cleared_without_restarting_state_machine(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        run = self.harness.repository.runs[run_id]
        run["worker_token"] = "expired-worker"
        run["worker_lease_until"] = 0
        self.harness.repository.stale_worker = True
        with patch.object(self.manager, "run_initial") as restart:
            snapshot = self.manager.get_snapshot(run_id)
            self.assertFalse(self.manager.needs_recovery(run_id))
        self.assertEqual(snapshot["run_status"], "completed")
        self.assertFalse(self.harness.repository.runs[run_id]["worker_token"])
        restart.assert_not_called()

    def test_failing_fence_recovery_never_resumes_or_completes(self):
        run_id = self.create(request_id="failing-recovery")["run"]["run_id"]
        self.manager.run_initial(run_id)
        run = self.harness.repository.runs[run_id]
        run.update(
            {
                "status": "failing",
                "error": "worker crashed after failure fence",
                "finished_at": None,
                "worker_token": None,
            }
        )
        self.assertTrue(self.manager.needs_recovery(run_id))
        with patch.object(self.manager._service, "resume") as resume:
            self.manager.run_recovery(run_id)
        resume.assert_not_called()
        self.assertEqual(self.harness.repository.runs[run_id]["status"], "failed")
        self.assertEqual(
            self.harness.repository.runs[run_id]["error"],
            "worker crashed after failure fence",
        )

    def test_cancel_wins_before_failure_fence_and_failure_wins_before_cancel(self):
        def crash_after_state(_run_id):
            raise RuntimeError("fatal manager failure")

        cancel_run = self.create(request_id="cancel-before-failing")["run"]["run_id"]
        original_update = self.harness.repository.update_status

        def cancel_before_failing(run_id, status, **kwargs):
            if status == "failing":
                value = self.harness.repository.runs[run_id]
                value["cancel_requested"] = True
                value["status"] = "cancelling"
            return original_update(run_id, status, **kwargs)

        with (
            patch.object(
                self.manager, "_reconcile_owned_assets_and_settle", crash_after_state
            ),
            patch.object(
                self.harness.repository,
                "update_status",
                side_effect=cancel_before_failing,
            ),
        ):
            self.manager.run_initial(cancel_run)
        self.assertEqual(
            self.harness.repository.runs[cancel_run]["status"], "cancelled"
        )

        failed_run = self.create(request_id="failure-before-cancel")["run"]["run_id"]
        with patch.object(
            self.manager, "_reconcile_owned_assets_and_settle", crash_after_state
        ):
            self.manager.run_initial(failed_run)
        self.assertEqual(self.harness.repository.runs[failed_run]["status"], "failed")
        self.assertEqual(self.manager.cancel(failed_run)["status"], "failed")

    def test_cancel_does_not_finalize_from_stale_prelock_snapshot(self):
        run_id = self.create()["run"]["run_id"]
        run = self.harness.repository.runs[run_id]
        run["status"] = "awaiting_action"
        run["worker_token"] = None
        locked = deepcopy(run)
        locked.update(
            {
                "status": "cancelling",
                "cancel_requested": True,
                "worker_token": "live-worker",
                "worker_lease_until": self.harness.repository.tick() + 30_000,
            }
        )
        with (
            patch.object(
                self.harness.repository, "request_cancel", return_value=locked
            ),
            patch.object(self.manager, "_finalize_cancelled_state") as finalize,
        ):
            result = self.manager.cancel(run_id)
        self.assertEqual(result["status"], "cancelling")
        finalize.assert_not_called()

    def test_failure_repairs_once_then_awaits_human(self):
        self.harness.execute_outcomes.extend([False, False])
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["status"], "awaiting_action")
        self.assertEqual(snapshot["counts"]["awaiting_human"], 1)
        self.assertEqual(self.harness.repair_calls, 1)
        self.assertEqual(self.harness.analysis_calls, 1)
        self.assertEqual(
            [stage["stage_type"] for stage in snapshot["items"][0]["history"]],
            ["generate", "execute", "repair", "execute", "human_review"],
        )

    def test_plan_hash_snapshot_detects_change_before_generation(self):
        run_id = self.create()["run"]["run_id"]
        plan = Path(self.temporary.name) / "登录" / "正常登录.md"
        plan.write_text("# 已修改", encoding="utf-8")
        self.manager.run_initial(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        first_stage = snapshot["items"][0]["history"][0]
        self.assertEqual(first_stage["status"], "failed")
        self.assertIn("测试计划已在任务创建后发生变化", first_stage["error"])

    def test_real_asset_revision_cas_blocks_stale_action(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.harness.actual_revision = 99
        with self.assertRaises(ScriptPreparationConflict):
            self.manager.apply_or_enqueue_action(
                run_id,
                item["item_id"],
                action="execute",
                expected_revision_id=item["current_revision_id"],
            )

    def test_batch_partial_and_async_error_are_observable(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        result = self.manager.enqueue_batch(
            run_id,
            [
                {
                    "item_id": item["item_id"],
                    "expected_revision_id": item["current_revision_id"],
                    "original_prompt": "per-item original",
                    "supplemental_prompt": "per-item patch",
                },
                "missing",
            ],
            action="execute",
        )
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(len(result["rejected"]), 1)
        queued = self.harness.repository.get(run_id)["action_queue"][0]
        self.assertEqual(
            queued["parameters"],
            {
                "expected_revision_id": item["current_revision_id"],
                "original_prompt": "per-item original",
                "supplemental_prompt": "per-item patch",
            },
        )
        self.harness.actual_revision = 99
        self.manager.run_actions(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["pending_actions"], [])
        self.assertEqual(snapshot["recent_actions"][-1]["state"], "failed")
        self.assertIn("其他操作更新", snapshot["recent_actions"][-1]["error"])

    def test_cancel_clears_queued_actions(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.manager.apply_or_enqueue_action(
            run_id,
            item["item_id"],
            action="execute",
            expected_revision_id=item["current_revision_id"],
        )
        cancelled = self.manager.cancel(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(snapshot["pending_actions"], [])
        self.assertEqual(snapshot["recent_actions"][-1]["state"], "cancelled")

    def test_cancel_before_worker_initialization_persists_terminal_items(self):
        run_id = self.create(["正常登录.md", "异常登录.md"])["run"]["run_id"]
        self.manager.cancel(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertEqual(snapshot["counts"]["queued"], 0)
        self.assertEqual(snapshot["counts"]["busy"], 0)
        self.assertEqual(snapshot["counts"]["abandoned"], 2)
        self.assertEqual(snapshot["counts"]["terminal"], 2)
        self.assertTrue(
            all(item["history"][-1]["stage_type"] == "cancelled" for item in snapshot["items"])
        )

    def test_cancel_during_last_operation_cannot_be_overwritten_by_completion(self):
        self.harness.cancel_during_execute = True
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertTrue(snapshot["cancel_requested"])
        self.assertFalse(
            any(
                stage["status"] == "running"
                for item in snapshot["items"]
                for stage in item["history"]
            )
        )

    def test_cancel_closes_generate_execute_and_repair_stages(self):
        def assert_cancelled_at(atom_name):
            harness = ModuleManagerHarness(self.temporary.name)

            def cancel(*args, **_kwargs):
                harness.repository.request_cancel(args[0])
                raise ModuleScriptPreparationCancelled(f"cancelled in {atom_name}")

            if atom_name == "generate":
                harness.generate = cancel
            elif atom_name == "execute":
                harness.execute = cancel
            else:
                harness.execute_outcomes.append(False)
                harness.repair = cancel
            manager = harness.manager()
            run_id = manager.create_run(
                module_name="登录",
                plan_filenames=["正常登录.md"],
                client_request_id=f"cancel-{atom_name}",
            )["run"]["run_id"]
            manager.run_initial(run_id)
            snapshot = manager.get_snapshot(run_id)
            self.assertEqual(snapshot["run_status"], "cancelled")
            self.assertEqual(snapshot["pending_actions"], [])
            self.assertIsNone(harness.repository.get(run_id).get("worker_token"))
            for item in snapshot["items"]:
                self.assertNotIn(
                    item["status"],
                    {"queued", "generating", "executing", "repairing", "analyzing"},
                )
                self.assertFalse(
                    any(stage["status"] == "running" for stage in item["history"])
                )

        for atom_name in ("generate", "execute", "repair"):
            with self.subTest(atom_name=atom_name):
                assert_cancelled_at(atom_name)

    def test_cancel_closes_pending_human_review(self):
        self.harness.execute_outcomes.extend([False, False])
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        self.manager.cancel(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        item = snapshot["items"][0]
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertEqual(item["status"], "abandoned")
        self.assertFalse(item["included_in_suite"])
        self.assertEqual(item["history"][-1]["status"], "failed")
        self.assertEqual(
            item["history"][-1]["result"]["error_type"], "cancelled"
        )

    def test_cancel_during_blocked_manual_edit_closes_item_state(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        entered = threading.Event()
        release = threading.Event()
        original_save = self.harness.save

        def blocked_save(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return original_save(*args, **kwargs)

        self.harness.save = blocked_save
        self.manager = self.harness.manager()
        errors = []

        def edit():
            try:
                self.manager.apply_or_enqueue_action(
                    run_id,
                    item["item_id"],
                    action="edit",
                    content="test('edited', async () => {});",
                    expected_revision_id=item["current_revision_id"],
                )
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(
            target=edit
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        self.harness.repository.runs[run_id]["worker_lease_until"] = 10**9
        self.manager.cancel(run_id)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ModuleScriptPreparationConflict)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["run_status"], "cancelled")
        self.assertEqual(snapshot["items"][0]["status"], "abandoned")
        self.assertFalse(
            any(
                stage["status"] in {"running", "pending"}
                for stage in snapshot["items"][0]["history"]
            )
        )
        self.assertIsNone(self.harness.repository.get(run_id)["worker_token"])

    def test_terminal_cancel_is_idempotent(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        result = self.manager.cancel(run_id)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.manager.get_snapshot(run_id)["cancel_requested"])

    def test_plan_order_is_canonical_for_response_recovery(self):
        first = self.create(["正常登录.md", "异常登录.md"])
        second = self.create(
            ["异常登录.md", "正常登录.md"], request_id="request-reordered"
        )
        self.assertFalse(second["created"])
        self.assertEqual(first["run"]["run_id"], second["run"]["run_id"])

    def test_takeover_never_replays_running_action_but_drains_queued(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        initial_execute_calls = self.harness.execute_calls
        run = self.harness.repository.runs[run_id]
        run["status"] = "running"
        run["worker_token"] = "dead-worker"
        run["action_queue"] = [
            {
                "action_id": "interrupted-execute",
                "item_id": item["item_id"],
                "action": "execute",
                "parameters": {"expected_revision_id": item["current_revision_id"]},
                "state": "running",
            },
            {
                "action_id": "queued-abandon",
                "item_id": item["item_id"],
                "action": "abandon",
                "parameters": {},
                "state": "queued",
            },
        ]
        self.harness.repository.stale_worker = True
        self.manager.run_actions(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(self.harness.execute_calls, initial_execute_calls)
        self.assertEqual(snapshot["counts"]["abandoned"], 1)
        interrupted = next(
            action
            for action in snapshot["recent_actions"]
            if action["action_id"] == "interrupted-execute"
        )
        self.assertEqual(interrupted["state"], "failed")

    def test_recovery_adopts_asset_committed_before_state(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        run = self.harness.repository.runs[run_id]
        item = run["state"]["items"][0]
        previous = item["current_revision_id"]
        item["history"].append(
            {
                "stage_id": "manual-edit-crash",
                "stage_type": "manual_edit",
                "stage_name": "人工编辑脚本",
                "status": "running",
                "started_at": self.harness.repository.tick(),
            }
        )
        item["current_stage_id"] = "manual-edit-crash"
        item["status"] = "awaiting_human"
        run["status"] = "awaiting_action"
        run["worker_token"] = "dead-worker"
        self.harness.repository.stale_worker = True
        self.harness.actual_revision = previous + 8
        self.manager.run_initial(run_id)
        recovered = self.manager.get_snapshot(run_id)["items"][0]
        self.assertEqual(recovered["current_revision_id"], previous + 8)
        self.assertEqual(recovered["status"], "awaiting_human")
        self.assertIn(
            "recovered_external_version",
            [stage["stage_type"] for stage in recovered["history"]],
        )

    def test_snapshot_is_visible_while_external_atom_is_blocked(self):
        entered = threading.Event()
        release = threading.Event()
        original = self.harness.generate

        def blocked_generate(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(*args, **kwargs)

        self.harness.generate = blocked_generate
        self.manager = self.harness.manager()
        run_id = self.create()["run"]["run_id"]
        worker = threading.Thread(target=self.manager.run_initial, args=(run_id,))
        worker.start()
        self.assertTrue(entered.wait(1))
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["items"][0]["status"], "generating")
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_edit_release_window_rejects_new_queue(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        run = self.harness.repository.runs[run_id]
        item = run["state"]["items"][0]
        run["status"] = "awaiting_action"
        run["worker_token"] = "edit-worker-before-release"
        with self.assertRaises(ModuleScriptPreparationConflict):
            self.manager.apply_or_enqueue_action(
                run_id,
                item["item_id"],
                action="execute",
                expected_revision_id=item["current_revision_id"],
            )

    def test_failed_atom_adopts_rollback_revision_before_human_review(self):
        def failed_generate(_run, _step, plan, **_kwargs):
            self.harness.actual_revision = 11
            raise ModulePreparationOperationFailed(
                "generation failed after rollback",
                rollback_script={
                    "module_name": plan["module_name"],
                    "plan_filename": plan["plan_filename"],
                    "filename": "正常登录.spec.ts",
                    "asset": {"current_revision_id": 11},
                },
            )

        self.harness.generate = failed_generate
        self.manager = self.harness.manager()
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.assertEqual(item["current_revision_id"], 11)
        self.assertEqual(item["status"], "awaiting_human")
        self.assertEqual(item["history"][0]["status"], "failed")

    def test_failed_manual_edit_adopts_rollback_revision(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]

        def failed_save(_run, current, _content, **_kwargs):
            self.harness.actual_revision = 12
            raise ModulePreparationOperationFailed(
                "manual sync failed after rollback",
                rollback_script={
                    **current["current_script"],
                    "asset": {"current_revision_id": 12},
                },
            )

        self.harness.save = failed_save
        self.manager = self.harness.manager()
        with self.assertRaises(ModulePreparationOperationFailed):
            self.manager.apply_or_enqueue_action(
                run_id,
                item["item_id"],
                action="edit",
                content="test('edited', async () => {});",
                expected_revision_id=item["current_revision_id"],
            )
        recovered = self.manager.get_snapshot(run_id)["items"][0]
        self.assertEqual(recovered["current_revision_id"], 12)
        self.assertEqual(recovered["status"], "awaiting_human")
        self.assertEqual(recovered["history"][-1]["status"], "failed")

    def test_external_edit_is_adopted_before_each_workbench_action(self):
        for action in ("edit", "execute", "regenerate", "repair"):
            with self.subTest(action=action):
                harness = ModuleManagerHarness(self.temporary.name)
                manager = harness.manager()
                run_id = manager.create_run(
                    module_name="登录",
                    plan_filenames=["正常登录.md"],
                    client_request_id=f"external-{action}",
                )["run"]["run_id"]
                manager.run_initial(run_id)
                before = manager.get_snapshot(run_id)["items"][0]
                harness.actual_revision += 10
                refreshed = manager.get_item(run_id, before["item_id"])
                self.assertEqual(
                    refreshed["current_revision_id"], harness.actual_revision
                )
                self.assertEqual(refreshed["status"], "awaiting_human")
                self.assertFalse(refreshed["included_in_suite"])
                self.assertIn(
                    "external_version_adopted",
                    [stage["stage_type"] for stage in refreshed["history"]],
                )
                parameters = {
                    "expected_revision_id": refreshed["current_revision_id"]
                }
                if action == "edit":
                    parameters["content"] = "test('edited', async () => {});"
                elif action == "repair":
                    parameters["original_prompt"] = "repair current script"
                result = manager.apply_or_enqueue_action(
                    run_id, refreshed["item_id"], action=action, **parameters
                )
                if result.get("queued"):
                    manager.run_actions(run_id)
                self.assertFalse(
                    any(
                        recent.get("state") == "failed"
                        for recent in manager.get_snapshot(run_id)["recent_actions"]
                    )
                )

    def test_awaiting_run_adopts_external_revision_and_remains_actionable(self):
        self.harness.execute_outcomes.extend([False, False])
        run_id = self.create(request_id="awaiting-external-edit")["run"]["run_id"]
        self.manager.run_initial(run_id)
        before = self.manager.get_snapshot(run_id)["items"][0]
        self.assertEqual(
            self.harness.repository.runs[run_id]["status"], "awaiting_action"
        )
        self.harness.actual_revision = before["current_revision_id"] + 10
        refreshed = self.manager.get_item(run_id, before["item_id"])
        self.assertEqual(refreshed["current_revision_id"], self.harness.actual_revision)
        self.assertEqual(refreshed["status"], "awaiting_human")
        result = self.manager.apply_or_enqueue_action(
            run_id,
            refreshed["item_id"],
            action="execute",
            expected_revision_id=refreshed["current_revision_id"],
        )
        self.assertTrue(result["queued"])

    def test_abandon_does_not_require_asset_revision_cas(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.harness.actual_revision += 10
        result = self.manager.apply_or_enqueue_action(
            run_id,
            item["item_id"],
            action="abandon",
            expected_revision_id=item["current_revision_id"],
        )
        self.assertTrue(result["queued"])
        self.manager.run_actions(run_id)
        self.assertEqual(
            self.manager.get_snapshot(run_id)["items"][0]["status"], "abandoned"
        )

    def test_external_delete_and_create_are_adopted_as_unverified(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.harness.actual_revision = None
        deleted = self.manager.get_item(run_id, item["item_id"])
        self.assertIsNone(deleted["current_script"])
        self.assertEqual(deleted["status"], "awaiting_human")
        self.assertEqual(
            deleted["latest_analysis"]["recommended_action"], "regenerate"
        )
        self.harness.actual_revision = 77
        created = self.manager.get_item(run_id, item["item_id"])
        self.assertEqual(created["current_revision_id"], 77)
        self.assertIsInstance(created["current_script"], dict)
        self.assertFalse(created["included_in_suite"])
        self.assertEqual(created["latest_analysis"]["recommended_action"], "execute")

    def test_regenerate_stage_inherits_replaced_revision(self):
        run_id = self.create()["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        previous = item["current_revision_id"]
        self.manager.apply_or_enqueue_action(
            run_id,
            item["item_id"],
            action="regenerate",
            expected_revision_id=previous,
            original_prompt="regenerate from current version",
        )
        self.manager.run_actions(run_id)
        regenerated = next(
            stage
            for stage in self.manager.get_snapshot(run_id)["items"][0]["history"]
            if stage["stage_type"] == "regenerate"
        )
        self.assertEqual(regenerated["input_revision_id"], previous)
        self.assertNotEqual(regenerated["output_revision_id"], previous)

    def test_final_revision_barrier_reopens_external_edit_before_completion(self):
        entered = threading.Event()
        release = threading.Event()

        class BarrierLease:
            def __enter__(inner_self):
                entered.set()
                self.assertTrue(release.wait(2))
                return inner_self

            def __exit__(inner_self, *_args):
                return False

        self.harness.target_lease = lambda _module, _filename: BarrierLease()
        self.manager = self.harness.manager()
        run_id = self.create(request_id="final-barrier")["run"]["run_id"]
        worker = threading.Thread(target=self.manager.run_initial, args=(run_id,))
        worker.start()
        self.assertTrue(entered.wait(1))
        self.harness.actual_revision = 99
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        snapshot = self.manager.get_snapshot(run_id)
        self.assertEqual(snapshot["run_status"], "awaiting_action")
        self.assertEqual(snapshot["counts"]["awaiting_human"], 1)
        self.assertFalse(snapshot["items"][0]["included_in_suite"])

    def test_action_completion_reconciles_external_drift_in_other_item(self):
        run_id = self.create(
            ["正常登录.md", "异常登录.md"], request_id="full-run-drift"
        )["run"]["run_id"]
        self.manager.run_initial(run_id)
        items = self.manager.get_snapshot(run_id)["items"]
        first, second = items
        self.manager.apply_or_enqueue_action(
            run_id,
            second["item_id"],
            action="execute",
            expected_revision_id=second["current_revision_id"],
        )
        self.harness.actual_revision_overrides[first["filename"]] = (
            first["current_revision_id"] + 100
        )
        self.manager.run_actions(run_id)
        snapshot = self.manager.get_snapshot(run_id)
        refreshed_first = next(
            item for item in snapshot["items"] if item["item_id"] == first["item_id"]
        )
        self.assertEqual(snapshot["run_status"], "awaiting_action")
        self.assertEqual(refreshed_first["status"], "awaiting_human")
        self.assertFalse(refreshed_first["included_in_suite"])

    def test_terminal_failed_run_is_not_recovered_from_expired_worker(self):
        run_id = self.create(request_id="failed-terminal")["run"]["run_id"]
        run = self.harness.repository.runs[run_id]
        run.update(
            {
                "status": "failed",
                "worker_token": "expired-worker",
                "worker_lease_until": 0,
            }
        )
        self.assertFalse(self.manager.needs_recovery(run_id))
        self.manager.run_recovery(run_id)
        self.assertEqual(self.harness.repository.runs[run_id]["status"], "failed")

    def test_null_revision_baseline_rejects_single_and_batch_lost_updates(self):
        run_id = self.create(request_id="null-cas-actions")["run"]["run_id"]
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        with self.assertRaises(ScriptPreparationConflict):
            self.manager.apply_or_enqueue_action(
                run_id,
                item["item_id"],
                action="execute",
                expected_revision_id=None,
            )
        batch = self.manager.enqueue_batch(
            run_id,
            [{"item_id": item["item_id"], "expected_revision_id": None}],
            action="repair",
        )
        self.assertFalse(batch["accepted"])
        self.assertIn("版本", batch["rejected"][0]["error"])
        self.assertFalse(self.harness.repository.runs[run_id]["action_queue"])

    def test_initial_generation_preserves_externally_created_null_baseline(self):
        calls = []

        def cas_generate(_run, _step, plan, **_kwargs):
            calls.append(plan.get("_expected_script_revision_id"))
            if str(plan.get("_expected_script_revision_id")) != str(
                self.harness.actual_revision
            ):
                raise ScriptPreparationConflict("脚本版本已变化，请刷新后重试。")
            return self.harness.generate(_run, _step, plan)

        self.harness.generate = cas_generate
        self.manager = self.harness.manager()
        run_id = self.create(request_id="null-cas-initial")["run"]["run_id"]
        self.harness.actual_revision = 77
        self.manager.run_initial(run_id)
        item = self.manager.get_snapshot(run_id)["items"][0]
        self.assertEqual(calls, [None])
        self.assertEqual(self.harness.revision, 0)
        self.assertEqual(item["current_revision_id"], 77)
        self.assertEqual(item["status"], "awaiting_human")


if __name__ == "__main__":
    unittest.main()
