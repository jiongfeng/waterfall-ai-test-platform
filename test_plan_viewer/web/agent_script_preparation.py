"""HTTP delivery for the Agent script-preparation workspace."""

from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request


SCRIPT_PREPARATION_ACTIONS = frozenset(
    {"edit", "execute", "abandon", "regenerate", "repair"}
)
SCRIPT_PREPARATION_ACTION_FIELDS = (
    "original_prompt",
    "supplemental_prompt",
    "content",
    "execute_after_save",
    "expected_revision_id",
)


class AgentScriptPreparationConflict(RuntimeError):
    """Fallback conflict type used by isolated Blueprint consumers."""


@dataclass(frozen=True)
class AgentScriptPreparationWebServices:
    """Application services consumed by script-preparation routes."""

    get_script_preparation_snapshot: Callable[[str], dict | None]
    get_script_preparation_item: Callable[[str, str], dict | None]
    apply_script_preparation_action: Callable[..., dict | None]
    apply_script_preparation_batch_action: Callable[..., Any]
    start_script_preparation_continue: Callable[[str], Any]
    claim_script_preparation_continue: Callable[[str], bool] = lambda _run_id: True
    conflict_type: type[Exception] = AgentScriptPreparationConflict


def _json_object():
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象。")
    return payload


def _action_name(payload):
    action = str(payload.get("action") or "").strip().lower()
    if action not in SCRIPT_PREPARATION_ACTIONS:
        supported = ", ".join(sorted(SCRIPT_PREPARATION_ACTIONS))
        raise ValueError(f"action 必须是以下值之一：{supported}。")
    return action


def _action_parameters(payload):
    return {
        field: payload[field]
        for field in SCRIPT_PREPARATION_ACTION_FIELDS
        if field in payload
    }


def _error_response(services, exc, operation):
    if isinstance(exc, FileNotFoundError):
        return jsonify({"error": str(exc)}), 404
    if isinstance(exc, services.conflict_type):
        return jsonify({"error": str(exc)}), 409
    if isinstance(exc, (TypeError, ValueError)):
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": f"{operation}：{exc}"}), 500


def _normalize_batch_result(result):
    if isinstance(result, dict):
        accepted = result.get("accepted", [])
        rejected = result.get("rejected", [])
        should_continue = result.get("should_continue") is True
    elif isinstance(result, list):
        accepted = [
            item
            for item in result
            if isinstance(item, dict) and item.get("accepted") is True
        ]
        rejected = [
            item
            for item in result
            if not isinstance(item, dict) or item.get("accepted") is not True
        ]
        should_continue = False
    else:
        raise RuntimeError("批量操作服务返回格式无效。")
    if not isinstance(accepted, list) or not isinstance(rejected, list):
        raise RuntimeError("批量操作结果必须包含 accepted/rejected 列表。")
    return accepted, rejected, should_continue


def _normalize_action_result(result):
    if not isinstance(result, dict):
        raise RuntimeError("脚本项操作服务返回格式无效。")
    should_continue = result.get("should_continue") is True
    if "item" in result:
        item = result.get("item")
    else:
        item = result
    if item is None:
        return None, should_continue
    if not isinstance(item, dict):
        raise RuntimeError("脚本项操作结果必须包含 item 对象。")
    return item, should_continue


def create_agent_script_preparation_blueprint(services):
    """Create the four script-preparation HTTP routes."""

    if not isinstance(services, AgentScriptPreparationWebServices):
        raise TypeError(
            "services must be an AgentScriptPreparationWebServices instance"
        )

    blueprint = Blueprint("agent_script_preparation", __name__)

    @blueprint.get("/api/agent/runs/<run_id>/script-preparation")
    def get_script_preparation(run_id):
        try:
            snapshot = services.get_script_preparation_snapshot(run_id)
            if snapshot is None:
                return jsonify({"error": "Agent 任务不存在。"}), 404
            return jsonify({"snapshot": snapshot, "error": None})
        except Exception as exc:
            return _error_response(services, exc, "读取脚本准备阶段失败")

    @blueprint.get("/api/agent/runs/<run_id>/script-items/<item_id>")
    def get_script_item(run_id, item_id):
        try:
            item = services.get_script_preparation_item(run_id, item_id)
            if item is None:
                return jsonify({"error": "脚本项不存在。"}), 404
            return jsonify({"item": item, "error": None})
        except Exception as exc:
            return _error_response(services, exc, "读取脚本项失败")

    @blueprint.post(
        "/api/agent/runs/<run_id>/script-items/<item_id>/actions"
    )
    def apply_script_item_action(run_id, item_id):
        try:
            payload = _json_object()
            action = _action_name(payload)
            result = services.apply_script_preparation_action(
                run_id,
                item_id,
                action=action,
                **_action_parameters(payload),
            )
            if result is None:
                return jsonify({"error": "脚本项不存在。"}), 404
            item, should_continue = _normalize_action_result(result)
            if item is None:
                return jsonify({"error": "脚本项不存在。"}), 404
            if should_continue:
                continuation_claimed = bool(
                    services.claim_script_preparation_continue(run_id)
                )
                if continuation_claimed:
                    services.start_script_preparation_continue(run_id)
            else:
                continuation_claimed = False
            response = jsonify(
                {
                    "accepted": True,
                    "item": item,
                    "should_continue": should_continue,
                    "continuation_claimed": continuation_claimed,
                    "error": None,
                }
            )
            return (response, 202) if should_continue else response
        except Exception as exc:
            return _error_response(services, exc, "执行脚本项操作失败")

    @blueprint.post(
        "/api/agent/runs/<run_id>/script-items/batch-actions"
    )
    def apply_script_item_batch_action(run_id):
        try:
            payload = _json_object()
            action = _action_name(payload)
            items = payload.get("items")
            if items is None:
                items = payload.get("item_ids")
            if not isinstance(items, list) or not items:
                raise ValueError("items 必须是非空列表。")
            result = services.apply_script_preparation_batch_action(
                run_id,
                items,
                action=action,
                **_action_parameters(payload),
            )
            accepted, rejected, should_continue = _normalize_batch_result(
                result
            )
            if should_continue:
                continuation_claimed = bool(
                    services.claim_script_preparation_continue(run_id)
                )
                if continuation_claimed:
                    services.start_script_preparation_continue(run_id)
            else:
                continuation_claimed = False
            response = jsonify(
                {
                    "accepted": accepted,
                    "rejected": rejected,
                    "should_continue": should_continue,
                    "continuation_claimed": continuation_claimed,
                    "error": None,
                }
            )
            return (response, 202) if should_continue else response
        except Exception as exc:
            return _error_response(services, exc, "执行脚本批量操作失败")

    return blueprint


__all__ = [
    "AgentScriptPreparationConflict",
    "AgentScriptPreparationWebServices",
    "SCRIPT_PREPARATION_ACTIONS",
    "create_agent_script_preparation_blueprint",
]
