"""Unified script-preparation state machine for Agent runs.

The module owns the product-facing workflow for one script:

    generate -> execute -> (repair -> execute) -> human review

It deliberately does not import Flask or the application composition root.  The
caller supplies persistence and the existing generation/execution/repair atoms
through :class:`ScriptPreparationDependencies`.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import threading
import uuid
from typing import Any, Callable


SCRIPT_PREPARATION_STEP_KEY = "prepare_scripts"
SCRIPT_PREPARATION_SCHEMA_VERSION = 1

ITEM_STATUS_QUEUED = "queued"
ITEM_STATUS_GENERATING = "generating"
ITEM_STATUS_EXECUTING = "executing"
ITEM_STATUS_REPAIRING = "repairing"
ITEM_STATUS_ANALYZING = "analyzing"
ITEM_STATUS_AWAITING_HUMAN = "awaiting_human"
ITEM_STATUS_READY = "ready"
ITEM_STATUS_ABANDONED = "abandoned"

BUSY_ITEM_STATUSES = frozenset(
    {
        ITEM_STATUS_GENERATING,
        ITEM_STATUS_EXECUTING,
        ITEM_STATUS_REPAIRING,
        ITEM_STATUS_ANALYZING,
    }
)
TERMINAL_ITEM_STATUSES = frozenset({ITEM_STATUS_READY, ITEM_STATUS_ABANDONED})

ACTION_EDIT = "edit"
ACTION_EXECUTE = "execute"
ACTION_ABANDON = "abandon"
ACTION_REGENERATE = "regenerate"
ACTION_REPAIR = "repair"
HUMAN_ACTIONS = frozenset(
    {ACTION_EDIT, ACTION_EXECUTE, ACTION_ABANDON, ACTION_REGENERATE, ACTION_REPAIR}
)
BATCH_ACTIONS = frozenset(
    {ACTION_EXECUTE, ACTION_ABANDON, ACTION_REGENERATE, ACTION_REPAIR}
)

STAGE_LABELS = {
    "generate": "生成",
    "execute": "执行",
    "repair": "修复",
    "human_review": "待人工处理",
    "manual_edit": "人工编辑脚本",
    "regenerate": "重新生成",
    "rerepair": "重新修复",
    "abandon": "放弃脚本",
}


class ScriptPreparationError(RuntimeError):
    """Base exception for invalid script-preparation operations."""


class ScriptPreparationConflict(ScriptPreparationError):
    """Raised when an item changed or is busy while an action is requested."""


class ScriptPreparationNotFound(FileNotFoundError, ScriptPreparationError):
    """Raised when a run or item cannot be found."""


@dataclass(frozen=True)
class ScriptPreparationDependencies:
    """Infrastructure and workflow atoms required by the state machine.

    ``load_step_output`` returns the persisted ``output_json`` object for the
    supplied run/step, or ``None`` when the step has not been initialized.
    ``update_agent_step`` follows the existing application callback signature.

    The three workflow atoms return dictionaries.  Generation and repair
    results should include ``asset.current_revision_id``.  Execution may either
    raise, return ``error``, or return an ``execution`` object with ``ok=False``
    / ``status='failed'`` to signal failure.
    """

    load_step_output: Callable[..., Any]
    get_agent_run: Callable[..., Any]
    update_agent_step: Callable[..., Any]
    update_agent_run: Callable[..., Any]
    append_agent_event: Callable[..., Any]
    generate_script: Callable[..., Any]
    execute_script: Callable[..., Any]
    repair_script: Callable[..., Any]
    analyze_failure: Callable[..., Any]
    save_script: Callable[..., Any]
    build_generation_prompt: Callable[..., str]
    build_repair_prompt: Callable[..., str]
    resolve_script_filename: Callable[..., str]
    current_time_ms: Callable[..., int]
    redact_value: Callable[..., Any]
    is_cancelled_error: Callable[[BaseException], bool]
    make_id: Callable[[str], str] = lambda prefix: f"{prefix}-{uuid.uuid4().hex}"
    waiting_run_status: str = "awaiting_script_action"


def script_preparation_dependencies_from_resolver(resolver):
    """Build lazy callbacks so composition-root replacements remain visible."""

    def lazy(name):
        return lambda *args, **kwargs: resolver(name)(*args, **kwargs)

    return ScriptPreparationDependencies(
        load_step_output=lazy("load_step_output"),
        get_agent_run=lazy("get_agent_run"),
        update_agent_step=lazy("update_agent_step"),
        update_agent_run=lazy("update_agent_run"),
        append_agent_event=lazy("append_agent_event"),
        generate_script=lazy("generate_script"),
        execute_script=lazy("execute_script"),
        repair_script=lazy("repair_script"),
        analyze_failure=lazy("analyze_failure"),
        save_script=lazy("save_script"),
        build_generation_prompt=lazy("build_generation_prompt"),
        build_repair_prompt=lazy("build_repair_prompt"),
        resolve_script_filename=lazy("resolve_script_filename"),
        current_time_ms=lazy("current_time_ms"),
        redact_value=lazy("redact_value"),
        is_cancelled_error=lazy("is_cancelled_error"),
        make_id=lazy("make_id"),
        waiting_run_status=str(resolver("waiting_run_status")),
    )


class ScriptPreparationService:
    """Persisted, deterministic orchestration for script preparation."""

    def __init__(self, dependencies: ScriptPreparationDependencies):
        if not isinstance(dependencies, ScriptPreparationDependencies):
            raise TypeError("dependencies must be ScriptPreparationDependencies")
        self.dependencies = dependencies
        self._lock = threading.RLock()

    def get_snapshot(self, run_id):
        with self._lock:
            state = self._load_state(run_id)
            return self._public_snapshot(state)

    def get_item(self, run_id, item_id):
        with self._lock:
            state = self._load_state(run_id)
            item = self._find_item(state, item_id)
            return self._public_value(item)

    def run(self, run_id, plans):
        """Initialize and run the automatic flow for every supplied plan."""

        plans = [dict(item) for item in (plans or []) if isinstance(item, dict)]
        if not plans:
            raise ValueError("脚本准备阶段至少需要一个测试计划。")

        with self._lock:
            existing = self._load_state(run_id, required=False)
            if existing and existing.get("items"):
                raise ScriptPreparationConflict("脚本准备阶段已经初始化，不能重复运行。")
            state = self._new_state(run_id, plans)
            self._persist(state, started=True)

            for item in state["items"]:
                self._run_generate_cycle(state, item, stage_type="generate", trigger="automatic")

            state["initial_run_finished"] = True
            self._persist(state)
            return self._operation_result(state)

    def apply_action(
        self,
        run_id,
        item_id,
        *,
        action,
        original_prompt=None,
        supplemental_prompt=None,
        content=None,
        execute_after_save=False,
        expected_revision_id=None,
    ):
        action = str(action or "").strip().lower()
        if action not in HUMAN_ACTIONS:
            raise ValueError("不支持的脚本人工操作。")

        with self._lock:
            self._assert_run_accepts_actions(run_id)
            state = self._load_state(run_id)
            item = self._find_item(state, item_id)
            self._assert_actionable(item, action)
            self._resolve_pending_human_stage(item, action)

            if action == ACTION_EDIT:
                self._manual_edit(
                    state,
                    item,
                    content=content,
                    execute_after_save=bool(execute_after_save),
                    expected_revision_id=expected_revision_id,
                )
            elif action == ACTION_EXECUTE:
                self._require_current_script(item, "重新执行")
                self._run_execute_cycle(state, item, trigger="human_execute")
            elif action == ACTION_ABANDON:
                self._abandon(state, item)
            elif action == ACTION_REGENERATE:
                base, supplement = self._resolve_prompts(
                    item,
                    ACTION_REGENERATE,
                    original_prompt,
                    supplemental_prompt,
                )
                item["included_in_suite"] = False
                self._run_generate_cycle(
                    state,
                    item,
                    stage_type="regenerate",
                    trigger="human_regenerate",
                    original_prompt=base,
                    supplemental_prompt=supplement,
                )
            elif action == ACTION_REPAIR:
                self._require_current_script(item, "重新修复")
                base, supplement = self._resolve_prompts(
                    item,
                    ACTION_REPAIR,
                    original_prompt,
                    supplemental_prompt,
                )
                item["included_in_suite"] = False
                self._run_repair_cycle(
                    state,
                    item,
                    stage_type="rerepair",
                    trigger="human_repair",
                    original_prompt=base,
                    supplemental_prompt=supplement,
                    analyze_after_failure=True,
                )

            self._persist(state)
            result = self._operation_result(state)
            result["item"] = self._public_value(item)
            result["accepted"] = True
            result["action"] = action
            return result

    def apply_batch_action(
        self,
        run_id,
        item_ids,
        *,
        action,
        original_prompt=None,
        supplemental_prompt=None,
        content=None,
        execute_after_save=False,
        expected_revision_id=None,
    ):
        """Apply a supported batch action sequentially with partial results."""

        action = str(action or "").strip().lower()
        if action not in BATCH_ACTIONS:
            raise ValueError("批量操作仅支持执行、放弃、重新生成和重新修复。")
        normalized_items = []
        seen = set()
        for value in item_ids or []:
            item_options = dict(value) if isinstance(value, dict) else {}
            item_id = str(item_options.get("item_id") if item_options else value or "").strip()
            if item_id and item_id not in seen:
                normalized_items.append({"item_id": item_id, **item_options})
                seen.add(item_id)
        if not normalized_items:
            raise ValueError("请至少选择一个脚本。")

        accepted = []
        rejected = []
        for item_options in normalized_items:
            item_id = item_options["item_id"]

            def item_value(key, shared):
                return item_options[key] if key in item_options else shared

            try:
                result = self.apply_action(
                    run_id,
                    item_id,
                    action=action,
                    original_prompt=item_value("original_prompt", original_prompt),
                    supplemental_prompt=item_value("supplemental_prompt", supplemental_prompt),
                    content=item_value("content", content),
                    execute_after_save=item_value("execute_after_save", execute_after_save),
                    expected_revision_id=item_value("expected_revision_id", expected_revision_id),
                )
                accepted.append(
                    {
                        "item_id": item_id,
                        "action": action,
                        "status": result["item"].get("status"),
                        "item": result["item"],
                    }
                )
            except Exception as exc:  # Batch must preserve per-item outcomes.
                rejected.append(
                    {
                        "item_id": item_id,
                        "action": action,
                        "error": str(exc),
                    }
                )

        snapshot = self.get_snapshot(run_id)
        result = self._result_from_public_snapshot(snapshot)
        result.update(
            {
                "action": action,
                "accepted": accepted,
                "rejected": rejected,
            }
        )
        return result

    def _new_state(self, run_id, plans):
        now = self._now()
        items = []
        for index, plan in enumerate(plans):
            module_name = str(plan.get("module_name") or "").strip()
            plan_filename = str(plan.get("plan_filename") or plan.get("filename") or "").strip()
            if not module_name or not plan_filename:
                raise ValueError("测试计划缺少 module_name 或 plan_filename。")
            filename = str(
                plan.get("script_filename")
                or self.dependencies.resolve_script_filename(plan)
                or ""
            ).strip()
            if not filename:
                raise ValueError(f"无法确定脚本文件名：{module_name}/{plan_filename}")
            generation_prompt = str(self.dependencies.build_generation_prompt(plan) or "").strip()
            if not generation_prompt:
                raise ValueError(f"脚本生成 Prompt 不能为空：{module_name}/{plan_filename}")
            items.append(
                {
                    "item_id": self.dependencies.make_id("script-preparation"),
                    "order_index": index,
                    "module_name": module_name,
                    "plan_filename": plan_filename,
                    "filename": filename,
                    "plan_snapshot": self._safe(plan),
                    "status": ITEM_STATUS_QUEUED,
                    "included_in_suite": False,
                    "current_script": None,
                    "current_revision_id": None,
                    "current_stage_id": "",
                    "history": [],
                    "prompt_defaults": {
                        "regenerate": generation_prompt,
                        "repair": "",
                    },
                    "latest_analysis": None,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        return {
            "schema_version": SCRIPT_PREPARATION_SCHEMA_VERSION,
            "run_id": str(run_id),
            "version": 0,
            "initial_run_finished": False,
            "items": items,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }

    def _run_generate_cycle(
        self,
        state,
        item,
        *,
        stage_type,
        trigger,
        original_prompt=None,
        supplemental_prompt="",
    ):
        base_prompt = str(
            original_prompt
            if original_prompt is not None
            else item.get("prompt_defaults", {}).get("regenerate")
            or ""
        ).strip()
        supplement = str(supplemental_prompt or "").strip()
        if not base_prompt:
            raise ValueError("重新生成的原 Prompt 不能为空。")
        item.setdefault("prompt_defaults", {})["regenerate"] = base_prompt
        stage = self._begin_stage(
            state,
            item,
            stage_type,
            trigger,
            original_prompt=base_prompt,
            supplemental_prompt=supplement,
            input_revision_id=None,
        )
        try:
            script = self.dependencies.generate_script(
                state["run_id"],
                SCRIPT_PREPARATION_STEP_KEY,
                deepcopy(item.get("plan_snapshot") or {}),
                original_prompt=base_prompt,
                supplemental_prompt=supplement,
            )
            self._assert_atom_succeeded(script, "脚本生成失败。")
            script = dict(script or {})
            item["current_script"] = self._safe(script)
            item["current_revision_id"] = self._script_revision_id(script)
            item["included_in_suite"] = False
            self._finish_stage(
                state,
                item,
                stage,
                "succeeded",
                result=script,
                output_revision_id=item.get("current_revision_id"),
            )
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            failure = self._failure_payload(exc, item, stage)
            self._finish_stage(state, item, stage, "failed", error=str(exc), result=failure)
            self._await_human(state, item, failure)
            return

        self._run_execute_cycle(state, item, trigger=trigger, allow_automatic_repair=True)

    def _run_execute_cycle(self, state, item, *, trigger, allow_automatic_repair=True):
        self._require_current_script(item, "执行")
        execution, failure = self._execute_once(state, item, trigger=trigger)
        if failure is None:
            self._mark_ready(item, execution)
            self._persist(state)
            return
        if not allow_automatic_repair:
            self._await_human(state, item, failure)
            return
        try:
            base_prompt = str(self.dependencies.build_repair_prompt(item, failure) or "").strip()
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            self._await_human(
                state,
                item,
                {**failure, "repair_prompt_error": str(exc)},
            )
            return
        if not base_prompt:
            self._await_human(
                state,
                item,
                {**failure, "repair_prompt_error": "自动修复 Prompt 为空。"},
            )
            return
        item.setdefault("prompt_defaults", {})["repair"] = base_prompt
        self._run_repair_cycle(
            state,
            item,
            stage_type="repair",
            trigger="automatic_repair",
            original_prompt=base_prompt,
            supplemental_prompt="",
            analyze_after_failure=True,
        )

    def _run_repair_cycle(
        self,
        state,
        item,
        *,
        stage_type,
        trigger,
        original_prompt,
        supplemental_prompt,
        analyze_after_failure,
    ):
        self._require_current_script(item, "修复")
        base_prompt = str(original_prompt or "").strip()
        supplement = str(supplemental_prompt or "").strip()
        if not base_prompt:
            raise ValueError("重新修复的原 Prompt 不能为空。")
        item.setdefault("prompt_defaults", {})["repair"] = base_prompt
        input_revision_id = item.get("current_revision_id")
        failure_context = self._latest_failed_stage_result(item)
        stage = self._begin_stage(
            state,
            item,
            stage_type,
            trigger,
            original_prompt=base_prompt,
            supplemental_prompt=supplement,
            input_revision_id=input_revision_id,
        )
        try:
            script = self.dependencies.repair_script(
                state["run_id"],
                SCRIPT_PREPARATION_STEP_KEY,
                deepcopy(item.get("current_script") or {}),
                failure=deepcopy(failure_context),
                original_prompt=base_prompt,
                supplemental_prompt=supplement,
            )
            self._assert_atom_succeeded(script, "脚本修复失败。")
            script = dict(script or {})
            item["current_script"] = self._safe(script)
            item["current_revision_id"] = self._script_revision_id(script)
            item["included_in_suite"] = False
            self._finish_stage(
                state,
                item,
                stage,
                "succeeded",
                result=script,
                output_revision_id=item.get("current_revision_id"),
            )
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            failure = self._failure_payload(exc, item, stage)
            self._finish_stage(state, item, stage, "failed", error=str(exc), result=failure)
            if analyze_after_failure:
                self._await_human(state, item, failure)
            return

        # A repair is always followed by exactly one explicit platform
        # verification.  A failed verification goes directly to human review;
        # it never starts a second automatic repair.
        execution, failure = self._execute_once(state, item, trigger="post_repair_verification")
        if failure is None:
            self._mark_ready(item, execution)
            self._persist(state)
        elif analyze_after_failure:
            self._await_human(state, item, failure)

    def _execute_once(self, state, item, *, trigger):
        input_revision_id = item.get("current_revision_id")
        stage = self._begin_stage(
            state,
            item,
            "execute",
            trigger,
            input_revision_id=input_revision_id,
        )
        try:
            execution = self.dependencies.execute_script(
                state["run_id"],
                SCRIPT_PREPARATION_STEP_KEY,
                deepcopy(item.get("current_script") or {}),
            )
            failure_message = self._execution_failure_message(execution)
            if failure_message:
                failure = self._safe(
                    {
                        **(execution if isinstance(execution, dict) else {}),
                        "error": failure_message,
                        "stage_id": stage.get("stage_id"),
                        "stage_type": stage.get("stage_type"),
                        "revision_id": input_revision_id,
                        "failed_at": self._now(),
                    }
                )
                self._finish_stage(
                    state,
                    item,
                    stage,
                    "failed",
                    error=failure_message,
                    result=failure,
                    input_revision_id=input_revision_id,
                    output_revision_id=input_revision_id,
                )
                return None, failure
            execution = dict(execution or {})
            self._finish_stage(
                state,
                item,
                stage,
                "succeeded",
                result=execution,
                input_revision_id=input_revision_id,
                output_revision_id=input_revision_id,
            )
            return execution, None
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            failure = self._failure_payload(exc, item, stage)
            self._finish_stage(
                state,
                item,
                stage,
                "failed",
                error=str(exc),
                result=failure,
                input_revision_id=input_revision_id,
                output_revision_id=input_revision_id,
            )
            return None, failure

    def _await_human(self, state, item, failure):
        has_script = isinstance(item.get("current_script"), dict)
        stage = self._begin_stage(
            state,
            item,
            "human_review",
            "automatic_analysis",
            input_revision_id=item.get("current_revision_id"),
        )
        item["status"] = ITEM_STATUS_ANALYZING
        self._persist(state)
        analysis_error = ""
        try:
            raw_analysis = self.dependencies.analyze_failure(
                state["run_id"],
                SCRIPT_PREPARATION_STEP_KEY,
                {
                    "item": self._public_value(item),
                    "failure": self._safe(failure),
                    "response_schema": {
                        "summary": "string",
                        "recommended_action": (
                            "regenerate|repair" if has_script else "regenerate"
                        ),
                        "prompt_patch": "string",
                    },
                    "available_actions": (
                        [ACTION_REGENERATE, ACTION_REPAIR]
                        if has_script
                        else [ACTION_REGENERATE]
                    ),
                },
            )
            analysis = self._normalize_analysis(
                raw_analysis,
                available_actions=(
                    {ACTION_REGENERATE, ACTION_REPAIR}
                    if has_script
                    else {ACTION_REGENERATE}
                ),
            )
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            analysis_error = str(exc)
            analysis = {
                "summary": "自动分析失败，请根据失败详情选择下一步操作。",
                "recommended_action": "",
                "prompt_patch": "",
                "analysis_status": "failed",
                "analysis_error": analysis_error,
            }

        regenerate_prompt = str(item.get("prompt_defaults", {}).get("regenerate") or "")
        try:
            repair_prompt = str(
                item.get("prompt_defaults", {}).get("repair")
                or self.dependencies.build_repair_prompt(item, failure)
                or ""
            )
        except Exception as exc:
            repair_prompt = ""
            analysis["repair_prompt_error"] = str(exc)
        if not has_script:
            repair_prompt = ""
        item.setdefault("prompt_defaults", {})["repair"] = repair_prompt
        patch = str(analysis.get("prompt_patch") or "")
        recommended_action = analysis.get("recommended_action") or ""
        analysis["prompt_options"] = {
            ACTION_REGENERATE: {
                "original_prompt": regenerate_prompt,
                "supplemental_prompt": patch if recommended_action == ACTION_REGENERATE else "",
                "enabled": True,
            },
            ACTION_REPAIR: {
                "original_prompt": repair_prompt,
                "supplemental_prompt": patch if recommended_action == ACTION_REPAIR else "",
                "enabled": bool(has_script and repair_prompt),
            },
        }
        item["latest_analysis"] = self._safe(analysis)
        item["status"] = ITEM_STATUS_AWAITING_HUMAN
        item["included_in_suite"] = False
        self._finish_stage(
            state,
            item,
            stage,
            "pending",
            result={"failure": failure, "analysis": analysis},
            error=analysis_error,
            output_revision_id=item.get("current_revision_id"),
        )

    def _manual_edit(
        self,
        state,
        item,
        *,
        content,
        execute_after_save,
        expected_revision_id,
    ):
        self._require_current_script(item, "人工编辑")
        content = str(content or "")
        if not content.strip():
            raise ValueError("脚本内容不能为空。")
        current_revision_id = item.get("current_revision_id")
        if expected_revision_id is not None and str(expected_revision_id) != str(current_revision_id):
            raise ScriptPreparationConflict("脚本版本已变化，请刷新后重新编辑。")
        stage = self._begin_stage(
            state,
            item,
            "manual_edit",
            "human_edit",
            input_revision_id=current_revision_id,
        )
        try:
            script = self.dependencies.save_script(
                state["run_id"],
                self._public_value(item),
                content,
                expected_revision_id=current_revision_id,
            )
            self._assert_atom_succeeded(script, "保存脚本失败。")
            script = dict(script or {})
            item["current_script"] = self._safe(script)
            item["current_revision_id"] = self._script_revision_id(script)
            item["included_in_suite"] = False
            self._finish_stage(
                state,
                item,
                stage,
                "succeeded",
                result=script,
                output_revision_id=item.get("current_revision_id"),
            )
        except Exception as exc:
            if self.dependencies.is_cancelled_error(exc):
                raise
            failure = self._failure_payload(exc, item, stage)
            self._finish_stage(state, item, stage, "failed", error=str(exc), result=failure)
            item["status"] = ITEM_STATUS_AWAITING_HUMAN
            self._persist(state)
            raise

        if execute_after_save:
            self._run_execute_cycle(state, item, trigger="manual_edit_execute")
        else:
            item["status"] = ITEM_STATUS_AWAITING_HUMAN
            self._persist(state)

    def _abandon(self, state, item):
        stage = self._begin_stage(
            state,
            item,
            "abandon",
            "human_abandon",
            input_revision_id=item.get("current_revision_id"),
        )
        item["status"] = ITEM_STATUS_ABANDONED
        item["included_in_suite"] = False
        self._finish_stage(
            state,
            item,
            stage,
            "succeeded",
            result={"reason": "用户放弃脚本；该脚本不会进入测试集。"},
            output_revision_id=item.get("current_revision_id"),
        )

    def _begin_stage(
        self,
        state,
        item,
        stage_type,
        trigger,
        *,
        original_prompt="",
        supplemental_prompt="",
        input_revision_id=None,
    ):
        now = self._now()
        stage = {
            "stage_id": self.dependencies.make_id("script-stage"),
            "sequence_no": len(item.get("history") or []) + 1,
            "stage_type": stage_type,
            "stage_name": STAGE_LABELS.get(stage_type, stage_type),
            "status": "running",
            "trigger": trigger,
            "parent_stage_id": item.get("current_stage_id") or "",
            "input_revision_id": input_revision_id,
            "output_revision_id": None,
            "original_prompt": str(original_prompt or ""),
            "supplemental_prompt": str(supplemental_prompt or ""),
            "result": None,
            "error": "",
            "started_at": now,
            "finished_at": None,
        }
        item.setdefault("history", []).append(stage)
        item["current_stage_id"] = stage["stage_id"]
        item["status"] = {
            "generate": ITEM_STATUS_GENERATING,
            "regenerate": ITEM_STATUS_GENERATING,
            "execute": ITEM_STATUS_EXECUTING,
            "repair": ITEM_STATUS_REPAIRING,
            "rerepair": ITEM_STATUS_REPAIRING,
            "human_review": ITEM_STATUS_ANALYZING,
            "manual_edit": ITEM_STATUS_AWAITING_HUMAN,
            "abandon": ITEM_STATUS_AWAITING_HUMAN,
        }.get(stage_type, item.get("status"))
        self._touch_item(item)
        self._persist(state)
        return stage

    def _finish_stage(
        self,
        state,
        item,
        stage,
        status,
        *,
        result=None,
        error="",
        input_revision_id=None,
        output_revision_id=None,
    ):
        stage["status"] = status
        stage["result"] = self._safe(result) if result is not None else None
        stage["error"] = str(error or "")
        if input_revision_id is not None:
            stage["input_revision_id"] = input_revision_id
        if output_revision_id is not None:
            stage["output_revision_id"] = output_revision_id
        stage["finished_at"] = self._now() if status != "pending" else None
        self._touch_item(item)
        self._persist(state)

    def _mark_ready(self, item, execution):
        item["status"] = ITEM_STATUS_READY
        item["included_in_suite"] = True
        if isinstance(execution, dict):
            item["current_script"] = self._safe(
                {
                    **(item.get("current_script") or {}),
                    "verification": execution,
                }
            )
        self._touch_item(item)

    def _resolve_pending_human_stage(self, item, action):
        for stage in reversed(item.get("history") or []):
            if stage.get("stage_type") == "human_review" and stage.get("status") == "pending":
                stage["status"] = "resolved"
                stage["finished_at"] = self._now()
                result = dict(stage.get("result") or {})
                result["selected_action"] = action
                stage["result"] = self._safe(result)
                break

    def _resolve_prompts(self, item, action, original_prompt, supplemental_prompt):
        analysis = item.get("latest_analysis") if isinstance(item.get("latest_analysis"), dict) else {}
        prompt_options = analysis.get("prompt_options") if isinstance(analysis.get("prompt_options"), dict) else {}
        defaults = prompt_options.get(action) if isinstance(prompt_options.get(action), dict) else {}
        fallback_key = "regenerate" if action == ACTION_REGENERATE else "repair"
        base = (
            str(original_prompt)
            if original_prompt is not None
            else str(defaults.get("original_prompt") or item.get("prompt_defaults", {}).get(fallback_key) or "")
        ).strip()
        supplement = (
            str(supplemental_prompt)
            if supplemental_prompt is not None
            else str(defaults.get("supplemental_prompt") or "")
        ).strip()
        if not base:
            raise ValueError("原 Prompt 不能为空。")
        return base, supplement

    def _normalize_analysis(self, value, *, available_actions=None):
        value = dict(value or {}) if isinstance(value, dict) else {}
        recommendation = value.get("recommendation") if isinstance(value.get("recommendation"), dict) else {}
        action = str(
            value.get("recommended_action") or recommendation.get("action") or ""
        ).strip().lower()
        allowed_actions = set(
            available_actions or {ACTION_REGENERATE, ACTION_REPAIR}
        )
        if action not in allowed_actions:
            expected = " 或 ".join(sorted(allowed_actions))
            raise ScriptPreparationError(
                f"自动分析必须推荐以下操作之一：{expected}。"
            )
        patch = str(
            value.get("prompt_patch")
            or recommendation.get("prompt_patch")
            or value.get("supplemental_prompt")
            or ""
        ).strip()
        if not patch:
            raise ScriptPreparationError("自动分析必须返回非空补充 Prompt。")
        return self._safe(
            {
                **value,
                "summary": str(value.get("summary") or "模型未返回失败摘要。").strip(),
                "recommended_action": action,
                "prompt_patch": patch,
                "analysis_status": "succeeded",
                "analyzed_at": self._now(),
            }
        )

    def _assert_run_accepts_actions(self, run_id):
        run = self.dependencies.get_agent_run(run_id)
        if not isinstance(run, dict):
            raise ScriptPreparationNotFound("Agent 任务不存在。")
        if str(run.get("status") or "") != self.dependencies.waiting_run_status:
            raise ScriptPreparationConflict(
                "Agent 任务已离开脚本人工处理阶段，请刷新后重试。"
            )
        if str(run.get("current_step") or "") != SCRIPT_PREPARATION_STEP_KEY:
            raise ScriptPreparationConflict(
                "Agent 任务已离开脚本准备阶段，请刷新后重试。"
            )

    def _assert_actionable(self, item, action):
        if item.get("status") in BUSY_ITEM_STATUSES:
            raise ScriptPreparationConflict("该脚本正在处理中，请稍后重试。")
        if item.get("status") == ITEM_STATUS_READY and action == ACTION_ABANDON:
            # Ready scripts may still be deliberately removed from the suite.
            return
        allowed_statuses = {ITEM_STATUS_AWAITING_HUMAN, ITEM_STATUS_ABANDONED, ITEM_STATUS_READY}
        if item.get("status") not in allowed_statuses:
            raise ScriptPreparationConflict("该脚本当前不能执行人工操作。")
        if item.get("status") == ITEM_STATUS_ABANDONED and action == ACTION_ABANDON:
            raise ScriptPreparationConflict("该脚本已经放弃。")

    @staticmethod
    def _require_current_script(item, action_name):
        if not isinstance(item.get("current_script"), dict):
            raise ScriptPreparationConflict(f"没有可用于{action_name}的脚本版本。")

    @staticmethod
    def _script_revision_id(script):
        script = script if isinstance(script, dict) else {}
        asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
        return (
            script.get("revision_id")
            or script.get("current_revision_id")
            or asset.get("current_revision_id")
        )

    @staticmethod
    def _execution_failure_message(execution):
        execution = execution if isinstance(execution, dict) else {}
        if execution.get("error"):
            return str(execution["error"])
        nested = execution.get("execution") if isinstance(execution.get("execution"), dict) else execution
        if nested.get("ok") is False or nested.get("status") in {
            "failed",
            "timedOut",
            "interrupted",
            "cancelled",
        }:
            return str(nested.get("error") or "脚本执行失败。")
        return ""

    @staticmethod
    def _assert_atom_succeeded(value, fallback_message):
        value = value if isinstance(value, dict) else {}
        if value.get("ok") is False or value.get("status") == "failed" or value.get("error"):
            raise ScriptPreparationError(str(value.get("error") or fallback_message))

    def _failure_payload(self, error, item, stage):
        payload = {
            "error": str(error or "脚本处理失败。"),
            "error_type": str(getattr(error, "error_type", "") or "unknown"),
            "job_id": str(getattr(error, "job_id", "") or ""),
            "test_run_id": str(getattr(error, "test_run_id", "") or ""),
            "result_id": getattr(error, "result_id", None),
            "partial_artifacts": list(getattr(error, "partial_artifacts", []) or []),
            "stage_id": stage.get("stage_id"),
            "stage_type": stage.get("stage_type"),
            "module_name": item.get("module_name"),
            "plan_filename": item.get("plan_filename"),
            "filename": item.get("filename"),
            "revision_id": item.get("current_revision_id"),
            "failed_at": self._now(),
        }
        return self._safe(payload)

    @staticmethod
    def _latest_failed_stage_result(item):
        for stage in reversed(item.get("history") or []):
            if stage.get("status") == "failed":
                result = stage.get("result")
                return deepcopy(result if isinstance(result, dict) else {"error": stage.get("error")})
        return {}

    def _load_state(self, run_id, *, required=True):
        value = self.dependencies.load_step_output(run_id, SCRIPT_PREPARATION_STEP_KEY)
        if value is None:
            if required:
                raise ScriptPreparationNotFound("脚本准备阶段不存在。")
            return None
        if not isinstance(value, dict):
            raise ScriptPreparationError("脚本准备阶段持久化数据格式无效。")
        if not value:
            if required:
                raise ScriptPreparationNotFound("脚本准备阶段尚未初始化。")
            return None
        if int(value.get("schema_version") or 0) != SCRIPT_PREPARATION_SCHEMA_VERSION:
            raise ScriptPreparationError("脚本准备阶段数据版本不受支持。")
        state = deepcopy(value)
        state.setdefault("items", [])
        return state

    @staticmethod
    def _find_item(state, item_id):
        item_id = str(item_id or "").strip()
        for item in state.get("items") or []:
            if str(item.get("item_id") or "") == item_id:
                return item
        raise ScriptPreparationNotFound("脚本准备项不存在。")

    def _persist(self, state, *, started=False):
        state["version"] = int(state.get("version") or 0) + 1
        state["updated_at"] = self._now()
        counts = self._counts(state)
        state["counts"] = counts
        state["error"] = ""
        all_terminal = counts["total"] > 0 and counts["terminal"] == counts["total"]
        should_continue = all_terminal
        busy_or_queued = counts["busy"] > 0 or counts["queued"] > 0
        if should_continue:
            step_status = "succeeded"
        elif busy_or_queued:
            step_status = "running"
        else:
            step_status = "awaiting_action"
        self.dependencies.update_agent_step(
            state["run_id"],
            SCRIPT_PREPARATION_STEP_KEY,
            status=step_status,
            input_data={"script_count": counts["total"]} if started else None,
            output_data=self._safe(state),
            counts=counts,
            error=state.get("error") or "",
            started=bool(started),
            finished=bool(should_continue),
        )
        if step_status == "running":
            self.dependencies.update_agent_run(
                state["run_id"],
                status="running",
                current_step=SCRIPT_PREPARATION_STEP_KEY,
                error="",
            )
        elif step_status == "awaiting_action":
            self.dependencies.update_agent_run(
                state["run_id"],
                status=self.dependencies.waiting_run_status,
                current_step=SCRIPT_PREPARATION_STEP_KEY,
                error=state.get("error") or "",
            )
        event_item = max(
            state.get("items") or [],
            key=lambda item: (int(item.get("updated_at") or 0), int(item.get("order_index") or 0)),
            default={},
        )
        self.dependencies.append_agent_event(
            state["run_id"],
            SCRIPT_PREPARATION_STEP_KEY,
            "status",
            "脚本准备状态已更新。",
            {
                "artifact_progress": True,
                "artifact_type": "script",
                "item_status": event_item.get("status") or step_status,
                "step_status": step_status,
                "step_output": self._safe(state),
                "counts": counts,
                "item_id": event_item.get("item_id") or "",
                "item": self._safe(event_item) if event_item else None,
            },
        )
        return state

    @staticmethod
    def _counts(state):
        items = list(state.get("items") or [])
        statuses = [item.get("status") for item in items]
        return {
            "total": len(items),
            "queued": sum(status == ITEM_STATUS_QUEUED for status in statuses),
            "busy": sum(status in BUSY_ITEM_STATUSES for status in statuses),
            "awaiting_human": sum(status == ITEM_STATUS_AWAITING_HUMAN for status in statuses),
            "ready": sum(status == ITEM_STATUS_READY for status in statuses),
            "abandoned": sum(status == ITEM_STATUS_ABANDONED for status in statuses),
            "terminal": sum(status in TERMINAL_ITEM_STATUSES for status in statuses),
        }

    def _operation_result(self, state):
        snapshot = self._public_snapshot(state)
        return self._result_from_public_snapshot(snapshot)

    @staticmethod
    def _result_from_public_snapshot(snapshot):
        counts = dict(snapshot.get("counts") or {})
        final_scripts = [
            deepcopy(item.get("current_script"))
            for item in snapshot.get("items") or []
            if item.get("status") == ITEM_STATUS_READY
            and item.get("included_in_suite")
            and isinstance(item.get("current_script"), dict)
        ]
        all_terminal = bool(counts.get("total")) and counts.get("terminal") == counts.get("total")
        should_continue = bool(all_terminal)
        paused = not all_terminal
        return {
            "paused": paused,
            "should_continue": should_continue,
            "final_scripts": final_scripts,
            "counts": counts,
            "snapshot": snapshot,
            "error": snapshot.get("error") or "",
        }

    def _public_snapshot(self, state):
        value = self._public_value(state)
        value["counts"] = self._counts(value)
        return value

    def _public_value(self, value):
        return deepcopy(self.dependencies.redact_value(deepcopy(value)))

    def _safe(self, value):
        return deepcopy(self.dependencies.redact_value(deepcopy(value)))

    def _touch_item(self, item):
        item["version"] = int(item.get("version") or 0) + 1
        item["updated_at"] = self._now()

    def _now(self):
        return int(self.dependencies.current_time_ms())


_SERVICE: ScriptPreparationService | None = None


def configure_script_preparation(dependencies: ScriptPreparationDependencies):
    global _SERVICE
    _SERVICE = ScriptPreparationService(dependencies)
    return _SERVICE


def claim_script_preparation_continue_record(
    *,
    connection_factory,
    runs_table,
    steps_table,
    project_id,
    run_id,
    now_ms,
):
    """Atomically move one completed preparation run to suite creation."""

    with connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {runs_table} AS runs
                SET status = 'running', current_step = 'create_suite',
                    error = '', updated_at = %s
                WHERE runs.project_id = %s AND runs.run_id = %s
                  AND runs.current_step = 'prepare_scripts'
                  AND runs.status IN ('running', 'awaiting_script_action')
                  AND EXISTS (
                    SELECT 1 FROM {steps_table} AS steps
                    WHERE steps.project_id = runs.project_id
                      AND steps.run_id = runs.run_id
                      AND steps.step_key = 'prepare_scripts'
                      AND steps.status = 'succeeded'
                  )
                """,
                (now_ms, project_id, run_id),
            )
            claimed = cursor.rowcount == 1
        connection.commit()
    return claimed


def _service():
    if _SERVICE is None:
        raise RuntimeError("Script preparation dependencies are not configured.")
    return _SERVICE


def run_script_preparation(run_id, plans):
    return _service().run(run_id, plans)


def run_agent_script_preparation(run_id, plans):
    """Run preparation and return the flattened payload expected by Agent UI."""

    result = _service().run(run_id, plans)
    snapshot = dict(result.get("snapshot") or {})
    snapshot.update(
        {
            "paused": bool(result.get("paused")),
            "should_continue": bool(result.get("should_continue")),
            "final_scripts": list(result.get("final_scripts") or []),
            "counts": dict(result.get("counts") or snapshot.get("counts") or {}),
            "error": result.get("error") or snapshot.get("error") or "",
        }
    )
    return snapshot


def get_script_preparation_snapshot(run_id):
    return _service().get_snapshot(run_id)


def get_script_preparation_item(run_id, item_id):
    return _service().get_item(run_id, item_id)


def apply_script_preparation_action(
    run_id,
    item_id,
    *,
    action,
    original_prompt=None,
    supplemental_prompt=None,
    content=None,
    execute_after_save=False,
    expected_revision_id=None,
):
    return _service().apply_action(
        run_id,
        item_id,
        action=action,
        original_prompt=original_prompt,
        supplemental_prompt=supplemental_prompt,
        content=content,
        execute_after_save=execute_after_save,
        expected_revision_id=expected_revision_id,
    )


def apply_script_preparation_batch_action(
    run_id,
    item_ids,
    *,
    action,
    original_prompt=None,
    supplemental_prompt=None,
    content=None,
    execute_after_save=False,
    expected_revision_id=None,
):
    return _service().apply_batch_action(
        run_id,
        item_ids,
        action=action,
        original_prompt=original_prompt,
        supplemental_prompt=supplemental_prompt,
        content=content,
        execute_after_save=execute_after_save,
        expected_revision_id=expected_revision_id,
    )


__all__ = [
    "ACTION_ABANDON",
    "ACTION_EDIT",
    "ACTION_EXECUTE",
    "ACTION_REGENERATE",
    "ACTION_REPAIR",
    "ScriptPreparationConflict",
    "ScriptPreparationDependencies",
    "ScriptPreparationError",
    "ScriptPreparationNotFound",
    "ScriptPreparationService",
    "apply_script_preparation_action",
    "apply_script_preparation_batch_action",
    "claim_script_preparation_continue_record",
    "configure_script_preparation",
    "get_script_preparation_item",
    "get_script_preparation_snapshot",
    "run_agent_script_preparation",
    "run_script_preparation",
    "script_preparation_dependencies_from_resolver",
]
