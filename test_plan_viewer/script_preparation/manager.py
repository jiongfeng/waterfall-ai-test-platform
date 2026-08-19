"""Ordinary module adapter for the shared script-preparation state machine."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
from pathlib import Path
import threading
import uuid
from typing import Any, Callable

from test_plan_viewer.agent.script_preparation import (
    ACTION_ABANDON,
    ACTION_EDIT,
    ACTION_EXECUTE,
    ACTION_REPAIR,
    BATCH_ACTIONS,
    BUSY_ITEM_STATUSES,
    HUMAN_ACTIONS,
    ITEM_STATUS_ABANDONED,
    ITEM_STATUS_AWAITING_HUMAN,
    ITEM_STATUS_READY,
    ScriptPreparationConflict,
    ScriptPreparationDependencies,
    ScriptPreparationService,
)
from test_plan_viewer.script_preparation.repository import (
    ModuleScriptPreparationConflict,
    ModuleScriptPreparationRepository,
)
from test_plan_viewer.script_preparation.target_lease import ScriptTargetBusy


class ModuleScriptPreparationCancelled(RuntimeError):
    """Raised cooperatively when a module preparation run is cancelled."""


_MISSING = object()


@dataclass(frozen=True)
class ModuleScriptPreparationServices:
    repository: ModuleScriptPreparationRepository
    generate_script: Callable[..., dict]
    execute_script: Callable[..., dict]
    repair_script: Callable[..., dict]
    analyze_failure: Callable[..., dict]
    save_script: Callable[..., dict]
    build_generation_prompt: Callable[..., str]
    build_repair_prompt: Callable[..., str]
    resolve_script_filename: Callable[..., str]
    validate_module_name: Callable[[str], str]
    validate_plan_filename: Callable[[str], str]
    get_plan_file: Callable[[str, str], Path]
    current_time_ms: Callable[[], int]
    current_author: Callable[[], str]
    get_project_language: Callable[[], str]
    register_task: Callable[[str], Any] = lambda _run_id: None
    cleanup_task: Callable[[str], Any] = lambda _run_id: None
    request_task_cancel: Callable[[str], Any] = lambda _run_id: None
    load_script_content: Callable[[dict], str | None] = lambda _item: None
    get_script_revision: Callable[[dict], Any] = lambda item: item.get(
        "current_revision_id"
    )
    reconcile_script_revision: Callable[[dict], Any] = lambda item: item.get(
        "current_revision_id"
    )
    target_lease: Callable[[str, str], Any] = lambda _module, _filename: nullcontext()


class ModuleScriptPreparationManager:
    """Coordinates persisted module runs without creating Agent run records."""

    def __init__(self, services):
        if not isinstance(services, ModuleScriptPreparationServices):
            raise TypeError("services must be ModuleScriptPreparationServices")
        self.services = services
        self.repository = services.repository
        self._worker_context = threading.local()
        self._action_worker_guard = threading.Lock()
        self._action_worker_locks = {}
        self._service = ScriptPreparationService(self._state_machine_dependencies())

    def _state_machine_dependencies(self):
        return ScriptPreparationDependencies(
            load_step_output=self._load_state,
            get_agent_run=self.repository.get,
            update_agent_step=self._update_step,
            update_agent_run=self._update_run,
            append_agent_event=lambda *_args, **_kwargs: None,
            generate_script=self._generate_script,
            execute_script=self.services.execute_script,
            repair_script=self.services.repair_script,
            analyze_failure=self.services.analyze_failure,
            save_script=self.services.save_script,
            build_generation_prompt=self.services.build_generation_prompt,
            build_repair_prompt=self.services.build_repair_prompt,
            resolve_script_filename=self.services.resolve_script_filename,
            current_time_ms=self.services.current_time_ms,
            redact_value=lambda value: value,
            is_cancelled_error=lambda error: isinstance(
                error, ModuleScriptPreparationCancelled
            ) or self.repository.is_cancel_requested(
                str(getattr(self._worker_context, "run_id", "") or "")
            ),
            make_id=lambda prefix: f"{prefix}-{uuid.uuid4().hex}",
            waiting_run_status="awaiting_action",
            get_project_language=self.services.get_project_language,
            validate_actionable_run=self._validate_actionable_run,
        )

    def _load_state(self, run_id, _step_key):
        run = self.repository.get(run_id)
        if not run:
            return None
        state = run.get("state")
        return state if isinstance(state, dict) and state else None

    def _update_step(
        self,
        run_id,
        _step_key,
        *,
        status=None,
        output_data=None,
        started=False,
        finished=False,
        **_kwargs,
    ):
        if output_data is not None:
            self.repository.save_state(
                run_id,
                output_data,
                step_status=status or "running",
                started=started,
                finished=finished,
            )

    def _update_run(
        self,
        run_id,
        *,
        status=None,
        error=None,
        finished=False,
        **_kwargs,
    ):
        if status is None and error is None and not finished:
            return
        mapped = {
            "awaiting_script_action": "awaiting_action",
            "succeeded": "completed",
        }.get(status, status or "running")
        self.repository.update_status(
            run_id, mapped, error=error, finished=finished
        )

    def _validate_actionable_run(self, run_id):
        worker = bool(getattr(self._worker_context, "allow_running", False))
        self.repository.assert_actionable(run_id, worker=worker)

    def _normalize_plans(self, module_name, plan_filenames):
        module_name = self.services.validate_module_name(module_name)
        normalized = []
        snapshots = []
        seen = set()
        for value in plan_filenames or []:
            filename = self.services.validate_plan_filename(str(value or "").strip())
            if filename in seen:
                continue
            path = self.services.get_plan_file(module_name, filename)
            if not path.is_file():
                raise FileNotFoundError(f"测试计划不存在：{module_name}/{filename}")
            normalized.append(filename)
            seen.add(filename)
        normalized.sort(key=lambda value: (value.casefold(), value))
        for filename in normalized:
            path = self.services.get_plan_file(module_name, filename)
            content = path.read_bytes()
            script_filename = self.services.resolve_script_filename(
                {"module_name": module_name, "plan_filename": filename}
            )
            script_revision_id = self.services.get_script_revision(
                {"module_name": module_name, "filename": script_filename}
            )
            snapshots.append(
                {
                    "module_name": module_name,
                    "plan_filename": filename,
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "content_size": len(content),
                    "script_filename": script_filename,
                    "script_revision_id": script_revision_id,
                }
            )
        if not normalized:
            raise ValueError("plan_filenames 必须是非空列表。")
        return module_name, normalized, snapshots

    def create_run(
        self, *, module_name, plan_filenames, client_request_id=""
    ):
        module_name, plan_filenames, plan_snapshots = self._normalize_plans(
            module_name, plan_filenames
        )
        run_id = f"script-preparation-{uuid.uuid4().hex}"
        run, created = self.repository.create(
            run_id=run_id,
            module_name=module_name,
            plan_filenames=plan_filenames,
            plan_snapshots=plan_snapshots,
            client_request_id=client_request_id,
            created_by=self.services.current_author(),
        )
        return {"run": self._public_run(run), "created": created}

    def run_initial(self, run_id):
        worker_token = f"worker-{uuid.uuid4().hex}"
        if not self.repository.claim_worker(run_id, worker_token):
            return
        self._set_worker_context(run_id)
        try:
            self.services.register_task(run_id)
            if self.repository.is_cancel_requested(run_id):
                raise ModuleScriptPreparationCancelled("脚本准备任务已取消。")
            run = self.repository.get(run_id)
            if not run:
                return
            if run.get("status") == "failing":
                self._reconcile_interrupted_assets(run_id)
                self._settle_failed(run_id, run.get("error") or "后台 worker 中断。")
                return
            elif isinstance(run.get("state"), dict) and run.get("state"):
                self._reconcile_interrupted_assets(run_id)
                self._service.resume(run_id)
            else:
                plans = list(run.get("plan_snapshots") or [])
                self._service.run(run_id, plans)
            self._reconcile_owned_assets_and_settle(run_id)
        except ScriptTargetBusy:
            # The previous process still owns the filesystem operation even
            # though its DB lease expired.  Defer recovery without rewriting
            # its persisted state.
            pass
        except Exception as exc:
            if self.repository.is_cancel_requested(run_id) or isinstance(
                exc, ModuleScriptPreparationCancelled
            ):
                self._finalize_cancelled_state(run_id, str(exc))
                self.repository.update_status(
                    run_id, "cancelled", error=str(exc), finished=True
                )
            else:
                fenced = self.repository.update_status(
                    run_id, "failing", error=str(exc)
                )
                if fenced.get("status") == "failing":
                    self._settle_failed(run_id, str(exc))
                elif fenced.get("status") == "cancelling":
                    self._finalize_cancelled_state(run_id, str(exc))
                    self.repository.update_status(
                        run_id, "cancelled", error=str(exc), finished=True
                    )
        finally:
            try:
                self.services.cleanup_task(run_id)
            except ModuleScriptPreparationConflict:
                pass
            try:
                self.repository.release_worker(run_id, force=True)
            except ModuleScriptPreparationConflict:
                pass
            self._clear_worker_context()

    def run_recovery(self, run_id):
        run = self.repository.get(run_id)
        if not run:
            return
        if run.get("status") == "failing":
            self.run_initial(run_id)
        elif run.get("action_queue"):
            self.run_actions(run_id)
        else:
            self.run_initial(run_id)

    def needs_recovery(self, run_id):
        run = self.repository.get(run_id)
        if not run:
            return False
        settled = {"awaiting_action", "completed", "failed", "cancelled"}
        interrupted = self._has_interrupted_state(run)
        if run.get("status") in settled and not (
            run.get("status") == "awaiting_action" and interrupted
        ):
            self.repository.clear_expired_worker(run_id, settled)
            return False
        lease_until = int(run.get("worker_lease_until") or 0)
        stale_owner = bool(run.get("worker_token")) and (
            lease_until <= self.services.current_time_ms()
        )
        recoverable = run.get("status") in {
            "queued",
            "running",
            "failing",
            "cancelling",
        }
        if not recoverable and not run.get("action_queue") and not stale_owner:
            return False
        return not run.get("worker_token") or lease_until <= self.services.current_time_ms()

    def get_snapshot(self, run_id):
        run = self.repository.get(run_id)
        if not run:
            raise FileNotFoundError("脚本准备任务不存在。")
        if run.get("status") in {"awaiting_action", "completed", "failed", "cancelled"} and not (
            run.get("status") == "awaiting_action"
            and self._has_interrupted_state(run)
        ):
            self.repository.clear_expired_worker(
                run_id, {"awaiting_action", "completed", "failed", "cancelled"}
            )
            run = self.repository.get(run_id)
        if run.get("status") in {"awaiting_action", "completed"}:
            self._reconcile_external_assets(run_id)
            run = self.repository.get(run_id)
        state = run.get("state") if isinstance(run.get("state"), dict) else {}
        if state:
            snapshot = self._service.get_snapshot(run_id)
        else:
            snapshot = {
                "schema_version": 1,
                "run_id": run_id,
                "version": 0,
                "initial_run_finished": False,
                "items": [],
                "counts": {
                    "total": len(run.get("plan_filenames") or []),
                    "queued": len(run.get("plan_filenames") or []),
                    "busy": 0,
                    "awaiting_human": 0,
                    "ready": 0,
                    "abandoned": 0,
                    "terminal": 0,
                },
                "error": "",
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
            }
        snapshot.update(
            {
                "status": (
                    "succeeded"
                    if run.get("status") == "completed"
                    else run.get("status")
                ),
                "run_status": run.get("status"),
                "module_name": run.get("module_name"),
                "plan_filenames": run.get("plan_filenames") or [],
                "pending_actions": run.get("action_queue") or [],
                "recent_actions": run.get("recent_actions") or [],
                "action_results": run.get("recent_actions") or [],
                "cancel_requested": bool(run.get("cancel_requested")),
                "run_error": run.get("error") or "",
                "error": run.get("error") or snapshot.get("error") or "",
                "summary": self._summary_text(
                    run.get("status"), snapshot.get("counts") or {}
                ),
            }
        )
        return snapshot

    def get_item(self, run_id, item_id):
        self._reconcile_external_assets(run_id)
        item = self._service.get_item(run_id, item_id)
        content = self.services.load_script_content(item)
        if content is not None and isinstance(item.get("current_script"), dict):
            item["current_script"] = {
                **item["current_script"],
                "content": content,
            }
        return item

    def _validate_item_action(self, snapshot, item_id, action, expected_revision_id=_MISSING):
        action = str(action or "").strip().lower()
        if action not in HUMAN_ACTIONS:
            raise ValueError("不支持的脚本人工操作。")
        item = next(
            (
                value
                for value in snapshot.get("items") or []
                if str(value.get("item_id") or "") == str(item_id or "")
            ),
            None,
        )
        if not item:
            raise FileNotFoundError("脚本准备项不存在。")
        if item.get("status") in BUSY_ITEM_STATUSES:
            raise ModuleScriptPreparationConflict("该脚本正在处理中。")
        if item.get("status") not in {
            ITEM_STATUS_AWAITING_HUMAN,
            ITEM_STATUS_READY,
            ITEM_STATUS_ABANDONED,
        }:
            raise ModuleScriptPreparationConflict("该脚本当前不能执行人工操作。")
        if action == ACTION_ABANDON:
            return item
        if action in {ACTION_EDIT, ACTION_EXECUTE, ACTION_REPAIR} and not isinstance(
            item.get("current_script"), dict
        ):
            raise ModuleScriptPreparationConflict("该操作需要已有脚本版本。")
        if expected_revision_id is not _MISSING and str(expected_revision_id) != str(
            item.get("current_revision_id")
        ):
            raise ScriptPreparationConflict("脚本版本已变化，请刷新后重试。")
        if isinstance(item.get("current_script"), dict):
            actual_revision_id = self.services.get_script_revision(item)
            if str(actual_revision_id) != str(item.get("current_revision_id")):
                raise ScriptPreparationConflict(
                    "脚本已被其他操作更新，请刷新后重试。"
                )
            if expected_revision_id is not _MISSING and str(expected_revision_id) != str(
                actual_revision_id
            ):
                raise ScriptPreparationConflict("脚本版本已变化，请刷新后重试。")
        return item

    def _generate_script(
        self,
        run_id,
        step_key,
        plan,
        *,
        original_prompt=None,
        supplemental_prompt="",
    ):
        path = self.services.get_plan_file(
            plan["module_name"], plan["plan_filename"]
        )
        if not path.is_file():
            raise FileNotFoundError(
                f"测试计划不存在：{plan['module_name']}/{plan['plan_filename']}"
            )
        expected_hash = str(plan.get("content_sha256") or "")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise ScriptPreparationConflict(
                "测试计划已在任务创建后发生变化，请重新发起脚本准备。"
            )
        expected_revision = getattr(
            self._worker_context, "expected_target_revision", _MISSING
        )
        if expected_revision is _MISSING:
            expected_revision = plan.get("script_revision_id")
        operation_plan = {
            **plan,
            "_expected_script_revision_id": expected_revision,
        }
        return self.services.generate_script(
            run_id,
            step_key,
            operation_plan,
            original_prompt=original_prompt,
            supplemental_prompt=supplemental_prompt,
        )

    def apply_or_enqueue_action(self, run_id, item_id, *, action, **parameters):
        self.repository.assert_actionable(run_id)
        normalized_action = str(action or "").strip().lower()
        self._reconcile_external_assets(
            run_id,
            excluded_item_ids={str(item_id)}
            if normalized_action == ACTION_ABANDON
            else None,
        )
        snapshot = self.get_snapshot(run_id)
        item = self._validate_item_action(
            snapshot,
            item_id,
            action,
            parameters.get("expected_revision_id", _MISSING),
        )
        execute_after_save = bool(parameters.get("execute_after_save"))
        if action == ACTION_EDIT:
            self.repository.claim_scope(run_id)
            worker_token = f"worker-{uuid.uuid4().hex}"
            if not self.repository.claim_worker(run_id, worker_token):
                raise ModuleScriptPreparationConflict("该脚本正在处理中。")
            self._set_worker_context(run_id)
            cancelled = False
            try:
                result = self._service.apply_action(
                    run_id,
                    item_id,
                    action=ACTION_EDIT,
                    content=parameters.get("content"),
                    execute_after_save=False,
                    expected_revision_id=parameters.get("expected_revision_id"),
                )
                self._reconcile_owned_assets_and_settle(run_id)
            except ModuleScriptPreparationCancelled:
                cancelled = True
            except Exception:
                if not self.repository.is_cancel_requested(run_id):
                    self._settle(run_id)
                raise
            finally:
                cancelled = self.repository.is_cancel_requested(run_id)
                if cancelled:
                    self._settle_cancelled(run_id)
                try:
                    self.repository.release_worker(run_id, force=True)
                except ModuleScriptPreparationConflict:
                    pass
                if not cancelled and self.repository.is_cancel_requested(run_id):
                    cancelled = self._settle_cancelled(run_id)
                self._clear_worker_context()
            if cancelled:
                raise ModuleScriptPreparationConflict("脚本准备任务已取消。")
            if not execute_after_save:
                return {**result, "queued": False}
            action = ACTION_EXECUTE
            parameters = {
                "expected_revision_id": result.get("item", {}).get(
                    "current_revision_id"
                )
            }
            item = result.get("item") or item
        queued = self._build_queued_action(item, action, parameters)
        self.repository.enqueue_actions(run_id, [queued])
        return {
            "accepted": True,
            "queued": True,
            "action_id": queued["action_id"],
            "item": item,
            "snapshot": self.get_snapshot(run_id),
        }

    def enqueue_batch(self, run_id, items, *, action, **shared_parameters):
        action = str(action or "").strip().lower()
        if action not in BATCH_ACTIONS:
            raise ValueError("批量操作仅支持执行、放弃、重新生成和重新修复。")
        self.repository.assert_actionable(run_id)
        item_ids = {
            str(value.get("item_id") if isinstance(value, dict) else value or "")
            for value in items or []
        }
        self._reconcile_external_assets(
            run_id,
            excluded_item_ids=item_ids if action == ACTION_ABANDON else None,
        )
        snapshot = self.get_snapshot(run_id)
        accepted = []
        rejected = []
        queued = []
        seen = set()
        for raw in items or []:
            options = dict(raw) if isinstance(raw, dict) else {}
            item_id = str(options.get("item_id") if options else raw or "").strip()
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            parameters = {
                key: options[key]
                if key in options
                else shared_parameters[key]
                for key in (
                    "original_prompt",
                    "supplemental_prompt",
                    "expected_revision_id",
                )
                if key in options or key in shared_parameters
            }
            try:
                item = self._validate_item_action(
                    snapshot,
                    item_id,
                    action,
                    parameters.get("expected_revision_id", _MISSING),
                )
                queued_action = self._build_queued_action(item, action, parameters)
                queued.append(queued_action)
                accepted.append(
                    {
                        "item_id": item_id,
                        "action": action,
                        "action_id": queued_action["action_id"],
                        "item": item,
                    }
                )
            except Exception as exc:
                rejected.append(
                    {"item_id": item_id, "action": action, "error": str(exc)}
                )
        if not accepted:
            return {"accepted": [], "rejected": rejected, "queued": False}
        self.repository.enqueue_actions(run_id, queued)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "queued": True,
            "snapshot": self.get_snapshot(run_id),
        }

    def _build_queued_action(self, item, action, parameters):
        if action == ACTION_ABANDON:
            parameters = {}
        allowed = {
            "original_prompt",
            "supplemental_prompt",
            "expected_revision_id",
        }
        return {
            "action_id": f"script-action-{uuid.uuid4().hex}",
            "item_id": item["item_id"],
            "action": action,
            "parameters": {
                key: value
                for key, value in parameters.items()
                if key in allowed
            },
            "state": "queued",
            "created_at": self.services.current_time_ms(),
        }

    def run_actions(self, run_id):
        with self._action_worker_guard:
            worker_lock = self._action_worker_locks.setdefault(
                run_id, threading.Lock()
            )
        if not worker_lock.acquire(blocking=False):
            return
        worker_token = f"worker-{uuid.uuid4().hex}"
        try:
            claimed = self.repository.claim_worker(run_id, worker_token)
        except Exception:
            worker_lock.release()
            raise
        if not claimed:
            worker_lock.release()
            return
        self._set_worker_context(run_id)
        released = False
        try:
            self.services.register_task(run_id)
            if self.repository.worker_took_over():
                self._reconcile_interrupted_assets(run_id)
                self._service.recover_interrupted(run_id)
            while True:
                while True:
                    if self.repository.is_cancel_requested(run_id):
                        raise ModuleScriptPreparationCancelled(
                            "脚本准备任务已取消。"
                        )
                    queued = self.repository.claim_next_action(run_id)
                    if not queued:
                        break
                    error = ""
                    try:
                        self._validate_item_action(
                            self.get_snapshot(run_id),
                            queued["item_id"],
                            queued["action"],
                            (queued.get("parameters") or {}).get(
                                "expected_revision_id", _MISSING
                            ),
                        )
                        self._worker_context.expected_target_revision = (
                            queued.get("parameters") or {}
                        ).get("expected_revision_id", _MISSING)
                        self._service.apply_action(
                            run_id,
                            queued["item_id"],
                            action=queued["action"],
                            **dict(queued.get("parameters") or {}),
                        )
                    except Exception as exc:
                        error = str(exc)
                    finally:
                        self._worker_context.expected_target_revision = _MISSING
                        self.repository.finish_action(
                            run_id, queued["action_id"], error=error
                        )
                    if self.repository.is_cancel_requested(run_id):
                        raise ModuleScriptPreparationCancelled(
                            "脚本准备任务已取消。"
                        )
                    self.repository.heartbeat_worker(run_id)
                if self.repository.is_cancel_requested(run_id):
                    raise ModuleScriptPreparationCancelled(
                        "脚本准备任务已取消。"
                    )
                self._reconcile_owned_assets_and_settle(run_id)
                if self.repository.release_worker(run_id):
                    released = True
                    self.services.cleanup_task(run_id)
                    break
        except ModuleScriptPreparationCancelled as exc:
            self._finalize_cancelled_state(run_id, str(exc))
            self.repository.update_status(
                run_id, "cancelled", error=str(exc), finished=True
            )
        except ScriptTargetBusy:
            pass
        finally:
            if not released:
                try:
                    self.services.cleanup_task(run_id)
                except ModuleScriptPreparationConflict:
                    pass
                try:
                    self.repository.release_worker(run_id, force=True)
                except ModuleScriptPreparationConflict:
                    pass
            self._clear_worker_context()
            worker_lock.release()

    def cancel(self, run_id):
        before = self.repository.get(run_id)
        if not before:
            raise FileNotFoundError("脚本准备任务不存在。")
        if before.get("status") in {"completed", "failed", "cancelled"}:
            return self._public_run(before)
        run = self.repository.request_cancel(run_id)
        if run.get("status") in {"completed", "failed", "cancelled"} or not run.get(
            "cancel_requested"
        ):
            return self._public_run(run)
        self.services.request_task_cancel(run_id)
        if not run.get("worker_token"):
            self._finalize_cancelled_state(run_id, "用户取消脚本准备任务。")
            run = self.repository.update_status(
                run_id, "cancelled", error="用户取消脚本准备任务。", finished=True
            )
        return self._public_run(run)

    def _finalize_cancelled_state(self, run_id, message):
        run = self.repository.get(run_id) or {}
        if run.get("state"):
            return self._service.cancel_interrupted(run_id, message)
        return self._service.initialize_cancelled(
            run_id, list(run.get("plan_snapshots") or []), message
        )

    def _settle_cancelled(self, run_id):
        if not self.repository.is_cancel_requested(run_id):
            return False
        message = "用户取消脚本准备任务。"
        self._finalize_cancelled_state(run_id, message)
        self.repository.update_status(
            run_id, "cancelled", error=message, finished=True
        )
        return True

    def _settle_failed(self, run_id, message):
        self._service.finalize_interrupted(
            run_id, message=message, error_type="worker_failed"
        )
        terminal = self.repository.update_status(
            run_id, "failed", error=message, finished=True
        )
        if terminal.get("status") == "cancelling":
            self._finalize_cancelled_state(run_id, message)
            terminal = self.repository.update_status(
                run_id, "cancelled", error=message, finished=True
            )
        return terminal

    @staticmethod
    def _has_interrupted_state(run):
        state = run.get("state") if isinstance(run, dict) else None
        return bool(
            isinstance(state, dict)
            and any(
                item.get("status") in BUSY_ITEM_STATUSES
                or any(
                    stage.get("status") in {"running", "pending"}
                    for stage in item.get("history") or []
                )
                for item in state.get("items") or []
            )
        )

    def _settle(self, run_id):
        run = self.repository.get(run_id)
        if not run or not isinstance(run.get("state"), dict) or not run.get("state"):
            return
        state = run["state"]
        counts = state.get("counts") or {}
        completed = bool(counts.get("total")) and counts.get("terminal") == counts.get(
            "total"
        )
        self.repository.save_state(
            run_id,
            state,
            step_status="succeeded" if completed else "awaiting_action",
            finished=completed,
        )
        if completed:
            self.repository.update_status(run_id, "completed", finished=True)

    def _reconcile_interrupted_assets(self, run_id):
        return self._reconcile_assets(run_id)

    def _reconcile_owned_assets_and_settle(self, run_id):
        """Fence the final revision scan and completion decision by module."""

        run = self.repository.get(run_id) or {}
        state = run.get("state") if isinstance(run.get("state"), dict) else {}
        items = list(state.get("items") or [])
        if not items:
            return self._settle(run_id)
        first = items[0]
        with self.services.target_lease(first["module_name"], first["filename"]):
            run = self.repository.get(run_id) or {}
            state = run.get("state") if isinstance(run.get("state"), dict) else {}
            revisions = [
                {
                    "item_id": item["item_id"],
                    "revision_id": self.services.reconcile_script_revision(item),
                }
                for item in state.get("items") or []
                if item.get("status") != ITEM_STATUS_ABANDONED
            ]
            changes = [
                revision
                for revision in revisions
                if str(revision["revision_id"])
                != str(
                    next(
                        item.get("current_revision_id")
                        for item in state.get("items") or []
                        if item.get("item_id") == revision["item_id"]
                    )
                )
            ]
            if changes:
                self._service.adopt_external_versions(run_id, changes)
            if self.repository.is_cancel_requested(run_id):
                raise ModuleScriptPreparationCancelled("脚本准备任务已取消。")
            self._settle(run_id)
            return bool(changes)

    def _reconcile_external_assets(self, run_id, excluded_item_ids=None):
        run = self.repository.get(run_id) or {}
        if run.get("status") not in {"awaiting_action", "completed"}:
            return False
        state = run.get("state") if isinstance(run.get("state"), dict) else {}
        candidates = [
            item
            for item in state.get("items") or []
            if str(item.get("item_id") or "") not in (excluded_item_ids or set())
            and not any(
                stage.get("status") == "running"
                for stage in item.get("history") or []
            )
        ]
        if not candidates:
            return False
        first = candidates[0]
        with self.services.target_lease(first["module_name"], first["filename"]):
            revisions = [
                {
                    "item_id": item["item_id"],
                    "revision_id": self.services.reconcile_script_revision(item),
                }
                for item in candidates
            ]
            revisions = [
                value
                for value in revisions
                if str(value["revision_id"])
                != str(
                    next(
                        item["current_revision_id"]
                        for item in candidates
                        if item["item_id"] == value["item_id"]
                    )
                )
            ]
            if not revisions:
                return False
            self.repository.claim_scope(run_id)
            worker_token = f"worker-{uuid.uuid4().hex}"
            if not self.repository.claim_worker(run_id, worker_token):
                raise ModuleScriptPreparationConflict("该脚本正在处理中。")
            self._set_worker_context(run_id)
            try:
                self._service.adopt_external_versions(run_id, revisions)
            finally:
                self._settle_cancelled(run_id)
                try:
                    self.repository.release_worker(run_id, force=True)
                finally:
                    self._settle_cancelled(run_id)
                    self._clear_worker_context()
        return True

    def _reconcile_assets(self, run_id):
        run = self.repository.get(run_id)
        state = run.get("state") if isinstance(run, dict) else None
        if not isinstance(state, dict):
            return False
        changed = False
        now = self.services.current_time_ms()
        candidates = [
            item
            for item in state.get("items") or []
            if item.get("status") in BUSY_ITEM_STATUSES
            or any(
                stage.get("status") == "running"
                for stage in item.get("history") or []
            )
        ]
        if not candidates:
            return False
        first = candidates[0]
        with self.services.target_lease(first["module_name"], first["filename"]):
            for item in candidates:
                module_name = item.get("module_name")
                filename = item.get("filename")
                actual_revision = self.services.reconcile_script_revision(item)
                previous_revision = item.get("current_revision_id")
                if str(actual_revision) == str(previous_revision):
                    continue
                changed = True
                stage = {
                    "stage_id": f"script-stage-{uuid.uuid4().hex}",
                    "sequence_no": len(item.get("history") or []) + 1,
                    "stage_type": "recovered_external_version",
                    "stage_name": "恢复已提交脚本版本",
                    "status": "succeeded",
                    "trigger": "worker_recovery",
                    "parent_stage_id": item.get("current_stage_id") or "",
                    "input_revision_id": previous_revision,
                    "output_revision_id": actual_revision,
                    "original_prompt": "",
                    "supplemental_prompt": "",
                    "result": {
                        "message": "检测到文件版本已提交但阶段状态未保存，已完成对账。",
                        "previous_revision_id": previous_revision,
                        "current_revision_id": actual_revision,
                    },
                    "error": "",
                    "started_at": now,
                    "finished_at": now,
                }
                item.setdefault("history", []).append(stage)
                item["current_stage_id"] = stage["stage_id"]
                item["current_revision_id"] = actual_revision
                if actual_revision is None:
                    item["current_script"] = None
                else:
                    script = dict(item.get("current_script") or {})
                    asset = dict(script.get("asset") or {})
                    asset["current_revision_id"] = actual_revision
                    item["current_script"] = {
                        **script,
                        "module_name": module_name,
                        "plan_filename": item.get("plan_filename") or "",
                        "filename": filename,
                        "asset": asset,
                    }
                item["updated_at"] = now
                item["version"] = int(item.get("version") or 0) + 1
            if changed:
                self.repository.save_state(
                    run_id,
                    state,
                    step_status="running",
                    started=False,
                    finished=False,
                )
        return changed

    def _set_worker_context(self, run_id):
        self._worker_context.run_id = run_id
        self._worker_context.allow_running = True

    def _clear_worker_context(self):
        self._worker_context.run_id = ""
        self._worker_context.allow_running = False

    @staticmethod
    def _public_run(run):
        if not run:
            return None
        return {
            key: run.get(key)
            for key in (
                "run_id",
                "module_name",
                "status",
                "plan_filenames",
                "cancel_requested",
                "error",
                "created_by",
                "started_at",
                "finished_at",
                "created_at",
                "updated_at",
            )
        }

    @staticmethod
    def _summary_text(status, counts):
        labels = {
            "queued": "等待开始",
            "running": "正在准备脚本",
            "failing": "正在收口失败状态",
            "awaiting_action": "等待人工处理",
            "completed": "脚本准备完成",
            "cancelling": "正在取消",
            "cancelled": "已取消",
            "failed": "任务失败",
        }
        total = int(counts.get("total") or 0)
        ready = int(counts.get("ready") or 0)
        abandoned = int(counts.get("abandoned") or 0)
        return f"{labels.get(status, status or '未知状态')}：共 {total}，通过 {ready}，放弃 {abandoned}"


__all__ = [
    "ModuleScriptPreparationCancelled",
    "ModuleScriptPreparationManager",
    "ModuleScriptPreparationServices",
]
