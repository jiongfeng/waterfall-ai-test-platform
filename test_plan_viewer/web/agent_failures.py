"""HTTP adapter for the Agent failure-analysis checkpoint."""

from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request


@dataclass(frozen=True)
class AgentFailureWebServices:
    conflict_type: type
    get_agent_run_row: Callable[..., Any]
    serialize_agent_run: Callable[..., Any]
    get_agent_failure_item: Callable[..., Any]
    analyze_agent_failure_item: Callable[..., Any]
    retry_agent_failure_item: Callable[..., Any]
    execute_agent_failure_item: Callable[..., Any]
    read_agent_failure_item_script: Callable[..., Any]
    save_agent_failure_item_script: Callable[..., Any]
    delete_agent_failure_item: Callable[..., Any]
    ignore_agent_failure_item: Callable[..., Any]
    continue_agent_failure_checkpoint: Callable[..., Any]
    get_current_project: Callable[..., Any]
    current_platform_author: Callable[..., Any]
    start_agent_failure_continue_thread: Callable[..., Any]
    agent_run_response: Callable[..., Any]


def agent_failure_web_services_from_resolver(conflict_type, resolver):
    def lazy(name):
        return lambda *args, **kwargs: resolver(name)(*args, **kwargs)

    values = {
        field_name: lazy(field_name)
        for field_name in AgentFailureWebServices.__dataclass_fields__
        if field_name != "conflict_type"
    }
    return AgentFailureWebServices(conflict_type=conflict_type, **values)


def create_agent_failures_blueprint(services: AgentFailureWebServices):
    blueprint = Blueprint("agent_failures", __name__)

    def require_waiting_run(run_id):
        run = services.get_agent_run_row(run_id)
        if not run:
            raise FileNotFoundError("Agent 任务不存在。")
        if run.get("status") != "awaiting_failure_action":
            raise services.conflict_type("该 Agent 任务当前不在失败处置阶段。")
        return run

    def error_response(exc, action):
        if isinstance(exc, FileNotFoundError):
            return jsonify({"error": str(exc)}), 404
        if isinstance(exc, services.conflict_type):
            return jsonify({"error": str(exc)}), 409
        if isinstance(exc, ValueError):
            return jsonify({"error": str(exc)}), 400
        return jsonify({"error": f"{action}：{exc}"}), 500

    @blueprint.get("/api/agent/runs/<run_id>/failure-items/<item_id>")
    def get_failure_item(run_id, item_id):
        try:
            require_waiting_run(run_id)
            item = services.get_agent_failure_item(run_id, item_id)
            if not item:
                raise FileNotFoundError("失败项不存在。")
            return jsonify({"item": item, "error": None})
        except Exception as exc:
            return error_response(exc, "读取失败项失败")

    @blueprint.post("/api/agent/runs/<run_id>/failure-items/<item_id>/analyze")
    def analyze_failure_item(run_id, item_id):
        payload = request.get_json(silent=True) or {}
        try:
            require_waiting_run(run_id)
            before = services.get_agent_failure_item(run_id, item_id)
            if not before:
                raise FileNotFoundError("失败项不存在。")
            force = bool(payload.get("force"))
            cache_hit = bool(before.get("analysis") and not before.get("analysis_stale") and not force)
            item = services.analyze_agent_failure_item(run_id, item_id, force=force)
            return jsonify(
                {
                    "item": item,
                    "analysis": item.get("analysis"),
                    "cached": cache_hit,
                    "error": None,
                }
            )
        except Exception as exc:
            return error_response(exc, "分析失败项失败")

    @blueprint.post("/api/agent/runs/<run_id>/failure-items/<item_id>/retry")
    def retry_failure_item(run_id, item_id):
        payload = request.get_json(silent=True) or {}
        try:
            require_waiting_run(run_id)
            item = services.get_agent_failure_item(run_id, item_id)
            if not item:
                raise FileNotFoundError("失败项不存在。")
            requested_action = str(payload.get("action") or "").strip()
            expected_action = "regenerate" if item.get("source_type") == "generation" else "repair"
            if requested_action and requested_action != expected_action:
                raise ValueError(f"该失败项只能执行 {expected_action}。")
            updated = services.retry_agent_failure_item(
                run_id,
                item_id,
                instructions=str(payload.get("instructions") or payload.get("prompt") or ""),
            )
            return jsonify({"item": updated, "result": updated.get("latest_action"), "error": None})
        except Exception as exc:
            return error_response(exc, "重试失败项失败")

    @blueprint.post("/api/agent/runs/<run_id>/failure-items/<item_id>/execute")
    def execute_failure_item(run_id, item_id):
        try:
            require_waiting_run(run_id)
            item = services.execute_agent_failure_item(run_id, item_id)
            return jsonify({"item": item, "result": item.get("latest_action"), "error": None})
        except Exception as exc:
            return error_response(exc, "执行失败项脚本失败")

    @blueprint.get("/api/agent/runs/<run_id>/failure-items/<item_id>/script")
    def get_failure_item_script(run_id, item_id):
        try:
            require_waiting_run(run_id)
            script = services.read_agent_failure_item_script(run_id, item_id)
            return jsonify({"script": script, "error": None})
        except Exception as exc:
            return error_response(exc, "读取失败项脚本失败")

    @blueprint.patch("/api/agent/runs/<run_id>/failure-items/<item_id>/script")
    def save_failure_item_script(run_id, item_id):
        payload = request.get_json(silent=True) or {}
        try:
            require_waiting_run(run_id)
            item = services.save_agent_failure_item_script(
                run_id,
                item_id,
                payload.get("content"),
                expected_content_sha256=payload.get("expected_content_sha256") or "",
            )
            script = services.read_agent_failure_item_script(run_id, item_id)
            return jsonify({"item": item, "script": script, "error": None})
        except Exception as exc:
            return error_response(exc, "保存失败项脚本失败")

    @blueprint.delete("/api/agent/runs/<run_id>/failure-items/<item_id>")
    def delete_failure_item(run_id, item_id):
        try:
            require_waiting_run(run_id)
            item = services.delete_agent_failure_item(run_id, item_id)
            return jsonify({"item": item, "deleted": True, "error": None})
        except Exception as exc:
            return error_response(exc, "删除失败项脚本失败")

    @blueprint.post("/api/agent/runs/<run_id>/failure-items/<item_id>/ignore")
    def ignore_failure_item(run_id, item_id):
        try:
            require_waiting_run(run_id)
            item = services.ignore_agent_failure_item(run_id, item_id)
            return jsonify({"item": item, "ignored": True, "error": None})
        except Exception as exc:
            return error_response(exc, "保留失败项失败")

    @blueprint.post("/api/agent/runs/<run_id>/continue")
    def continue_failure_checkpoint(run_id):
        try:
            context = services.continue_agent_failure_checkpoint(run_id)
            services.start_agent_failure_continue_thread(
                run_id,
                services.get_current_project(),
                services.current_platform_author(),
            )
            return (
                jsonify(
                    {
                        **services.agent_run_response(run_id, include_events=True),
                        "continued": True,
                        "partial_success": context["partial_success"],
                        "coverage_gap": context["coverage_gap"],
                        "error": None,
                    }
                ),
                202,
            )
        except Exception as exc:
            return error_response(exc, "继续 Agent 任务失败")

    return blueprint


__all__ = [
    "AgentFailureWebServices",
    "agent_failure_web_services_from_resolver",
    "create_agent_failures_blueprint",
]
