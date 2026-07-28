"""Flask delivery for setup scripts, bindings, and trial runs."""

from dataclasses import dataclass
from typing import Any, Callable, Collection

from flask import Blueprint, jsonify, request


@dataclass(frozen=True)
class SetupWebServices:
    """Application capabilities used by setup HTTP handlers."""

    list_scripts: Callable[[], list]
    save_script: Callable[..., dict | None]
    delete_script: Callable[[str], bool]
    list_bindings: Callable[[], list]
    save_binding: Callable[..., dict | None]
    delete_binding: Callable[[str], bool]
    list_runs: Callable[[Any, str | None], list]
    get_script: Callable[[str], dict | None]
    get_current_project: Callable[[], dict]
    execute_profile: Callable[..., dict]
    preparation_error_type: type[Exception]
    binding_target_types: Collection[str]


def create_setup_blueprint(services):
    blueprint = Blueprint("setup", __name__)

    @blueprint.get("/api/setup-scripts")
    def list_setup_scripts():
        try:
            return jsonify(
                {
                    "scripts": services.list_scripts(),
                    "error": None,
                }
            )
        except Exception as exc:
            return jsonify(
                {
                    "scripts": [],
                    "error": f"读取准备脚本失败：{exc}",
                }
            ), 500

    @blueprint.post("/api/setup-scripts")
    def create_setup_script():
        try:
            script = services.save_script(
                request.get_json(silent=True) or {}
            )
            return jsonify(
                {"script": script, "error": None}
            ), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"创建准备脚本失败：{exc}"}
            ), 500

    @blueprint.put("/api/setup-scripts/<script_uid>")
    def update_setup_script(script_uid):
        try:
            script = services.save_script(
                request.get_json(silent=True) or {},
                script_uid,
            )
            if not script:
                return jsonify(
                    {"error": "准备脚本不存在。"}
                ), 404
            return jsonify({"script": script, "error": None})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"更新准备脚本失败：{exc}"}
            ), 500

    @blueprint.delete("/api/setup-scripts/<script_uid>")
    def delete_setup_script(script_uid):
        try:
            if not services.delete_script(script_uid):
                return jsonify(
                    {"error": "准备脚本不存在。"}
                ), 404
            return jsonify({"ok": True, "error": None})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"删除准备脚本失败：{exc}"}
            ), 500

    @blueprint.get("/api/setup-bindings")
    def list_setup_bindings():
        try:
            return jsonify(
                {
                    "bindings": services.list_bindings(),
                    "error": None,
                }
            )
        except Exception as exc:
            return jsonify(
                {
                    "bindings": [],
                    "error": f"读取准备绑定失败：{exc}",
                }
            ), 500

    @blueprint.post("/api/setup-bindings")
    def create_setup_binding():
        try:
            binding = services.save_binding(
                request.get_json(silent=True) or {}
            )
            return jsonify(
                {"binding": binding, "error": None}
            ), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"创建准备绑定失败：{exc}"}
            ), 500

    @blueprint.put("/api/setup-bindings/<binding_uid>")
    def update_setup_binding(binding_uid):
        try:
            binding = services.save_binding(
                request.get_json(silent=True) or {},
                binding_uid,
            )
            if not binding:
                return jsonify(
                    {"error": "准备绑定不存在。"}
                ), 404
            return jsonify(
                {"binding": binding, "error": None}
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"更新准备绑定失败：{exc}"}
            ), 500

    @blueprint.delete("/api/setup-bindings/<binding_uid>")
    def delete_setup_binding(binding_uid):
        try:
            if not services.delete_binding(binding_uid):
                return jsonify(
                    {"error": "准备绑定不存在。"}
                ), 404
            return jsonify({"ok": True, "error": None})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"删除准备绑定失败：{exc}"}
            ), 500

    @blueprint.get("/api/setup-runs")
    def list_setup_runs():
        try:
            runs = services.list_runs(
                request.args.get("limit", 50),
                request.args.get("script_uid"),
            )
            return jsonify({"runs": runs, "error": None})
        except ValueError as exc:
            return jsonify(
                {"runs": [], "error": str(exc)}
            ), 400
        except Exception as exc:
            return jsonify(
                {
                    "runs": [],
                    "error": f"读取准备执行记录失败：{exc}",
                }
            ), 500

    @blueprint.post(
        "/api/setup-scripts/<script_uid>/trial-run"
    )
    def trial_run_setup_script(script_uid):
        payload = request.get_json(silent=True) or {}
        try:
            script = services.get_script(script_uid)
            if not script:
                return jsonify(
                    {"error": "准备脚本不存在。"}
                ), 404

            target_type = str(
                payload.get("target_type") or "project"
            ).strip()
            project = services.get_current_project()
            target_key = str(
                payload.get("target_key")
                or project.get("project_key")
                or "default"
            ).strip()
            if target_type not in services.binding_target_types:
                raise ValueError(
                    "target_type must be 'project', "
                    "'test_suite' or 'script'."
                )

            resolution = {
                "script": script,
                "profile": script,
                "binding": {},
                "target": {
                    "scope_type": target_type,
                    "scope_key": target_key,
                },
            }
            try:
                run = services.execute_profile(
                    resolution,
                    parent_run_id=str(
                        payload.get("parent_run_id") or ""
                    ),
                    target_override=resolution["target"],
                )
            except services.preparation_error_type as exc:
                return jsonify(
                    {
                        "run": exc.summary,
                        "error": str(exc),
                    }
                ), 422
            return jsonify({"run": run, "error": None})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify(
                {"error": f"试运行准备脚本失败：{exc}"}
            ), 500

    return blueprint
