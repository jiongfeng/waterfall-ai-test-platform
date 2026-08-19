"""HTTP API for ordinary module script-preparation runs."""

from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from test_plan_viewer.agent.script_preparation import ScriptPreparationConflict
from test_plan_viewer.script_preparation.repository import (
    ModuleScriptPreparationConflict,
)


@dataclass(frozen=True)
class ModuleScriptPreparationWebServices:
    manager: Any
    start_initial: Callable[[str], Any]
    start_actions: Callable[[str], Any]


def _payload():
    value = request.get_json(silent=True)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("请求体必须是 JSON 对象。")
    return value


def _action_parameters(payload):
    fields = (
        "original_prompt",
        "supplemental_prompt",
        "content",
        "execute_after_save",
        "expected_revision_id",
    )
    return {field: payload[field] for field in fields if field in payload}


def _require_expected_revision(payload, items=None):
    if str(payload.get("action") or "").lower() == "abandon":
        return
    if "expected_revision_id" in payload or (
        items
        and all(
            isinstance(item, dict) and "expected_revision_id" in item
            for item in items
        )
    ):
        return
    raise ValueError("expected_revision_id 是必填字段。")


def _error(exc, operation):
    if isinstance(exc, FileNotFoundError):
        return jsonify({"error": str(exc)}), 404
    if isinstance(
        exc, (ModuleScriptPreparationConflict, ScriptPreparationConflict)
    ):
        payload = {"error": str(exc)}
        existing = getattr(exc, "existing_run", None)
        if isinstance(existing, dict):
            payload["existing_run"] = {
                key: existing.get(key)
                for key in (
                    "run_id",
                    "module_name",
                    "status",
                    "plan_filenames",
                    "created_at",
                    "updated_at",
                )
            }
        return jsonify(payload), 409
    if isinstance(exc, (TypeError, ValueError)):
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": f"{operation}：{exc}"}), 500


def create_module_script_preparation_blueprint(services):
    if not isinstance(services, ModuleScriptPreparationWebServices):
        raise TypeError("services must be ModuleScriptPreparationWebServices")
    manager = services.manager
    blueprint = Blueprint("module_script_preparation", __name__)

    @blueprint.post("/api/script-preparation-runs")
    def create_run():
        try:
            payload = _payload()
            plan_filenames = payload.get("plan_filenames")
            if not isinstance(plan_filenames, list) or not plan_filenames:
                raise ValueError("plan_filenames 必须是非空列表。")
            result = manager.create_run(
                module_name=payload.get("module_name"),
                plan_filenames=plan_filenames,
                client_request_id=payload.get("client_request_id"),
            )
            run = result["run"]
            if result["created"] or manager.needs_recovery(run["run_id"]) is True:
                services.start_initial(run["run_id"])
            return (
                jsonify(
                    {
                        "run": run,
                        "snapshot": manager.get_snapshot(run["run_id"]),
                        "created": result["created"],
                        "error": None,
                    }
                ),
                202 if result["created"] else 200,
            )
        except Exception as exc:
            return _error(exc, "创建脚本准备任务失败")

    @blueprint.get("/api/script-preparation-runs/<run_id>")
    def get_run(run_id):
        try:
            snapshot = manager.get_snapshot(run_id)
            if manager.needs_recovery(run_id) is True:
                services.start_initial(run_id)
            return jsonify({"snapshot": snapshot, "error": None})
        except Exception as exc:
            return _error(exc, "读取脚本准备任务失败")

    @blueprint.get("/api/script-preparation-runs/<run_id>/items/<item_id>")
    def get_item(run_id, item_id):
        try:
            return jsonify({"item": manager.get_item(run_id, item_id), "error": None})
        except Exception as exc:
            return _error(exc, "读取脚本准备项失败")

    @blueprint.post(
        "/api/script-preparation-runs/<run_id>/items/<item_id>/actions"
    )
    def apply_action(run_id, item_id):
        try:
            payload = _payload()
            _require_expected_revision(payload)
            result = manager.apply_or_enqueue_action(
                run_id,
                item_id,
                action=payload.get("action"),
                **_action_parameters(payload),
            )
            if result.get("queued"):
                services.start_actions(run_id)
            return (
                jsonify({**result, "error": None}),
                202 if result.get("queued") else 200,
            )
        except Exception as exc:
            return _error(exc, "执行脚本准备操作失败")

    @blueprint.post(
        "/api/script-preparation-runs/<run_id>/items/batch-actions"
    )
    def apply_batch_action(run_id):
        try:
            payload = _payload()
            items = payload.get("items")
            if items is None:
                items = payload.get("item_ids")
            if not isinstance(items, list) or not items:
                raise ValueError("items 必须是非空列表。")
            _require_expected_revision(payload, items)
            result = manager.enqueue_batch(
                run_id,
                items,
                action=payload.get("action"),
                **_action_parameters(payload),
            )
            if result.get("queued"):
                services.start_actions(run_id)
            return (
                jsonify({**result, "error": None}),
                202 if result.get("queued") else 200,
            )
        except Exception as exc:
            return _error(exc, "执行脚本准备批量操作失败")

    @blueprint.post("/api/script-preparation-runs/<run_id>/cancel")
    def cancel_run(run_id):
        try:
            return jsonify({"run": manager.cancel(run_id), "error": None}), 202
        except Exception as exc:
            return _error(exc, "取消脚本准备任务失败")

    return blueprint


__all__ = [
    "ModuleScriptPreparationWebServices",
    "create_module_script_preparation_blueprint",
]
