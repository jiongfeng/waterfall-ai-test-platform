"""HTTP delivery for non-streaming requirement operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, request, send_file


@dataclass(frozen=True)
class RequirementWebServices:
    """Application services consumed by requirement routes."""

    list_requirements: Callable[[], list]
    serialize_requirement: Callable[..., dict]
    create_requirement: Callable[..., dict]
    get_requirement: Callable[[str], dict]
    delete_requirement: Callable[[str], bool]
    list_modules: Callable[[int], list]
    get_module: Callable[[int, str], dict]
    serialize_module: Callable[[dict], dict]
    build_planner_prompt: Callable[..., str]
    update_module: Callable[[int, str, dict], dict]
    delete_module: Callable[[int, str], bool]


def list_requirements_response(services):
    try:
        requirements = [
            services.serialize_requirement(row)
            for row in services.list_requirements()
        ]
        return jsonify(
            {
                "requirements": requirements,
                "error": None,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "requirements": [],
                "error": f"读取需求列表失败：{exc}",
            }
        ), 500


def upload_requirement_response(services):
    try:
        requirement = services.create_requirement(
            request.files.get("file"),
            title=request.form.get("title"),
        )
        return jsonify(
            {
                "requirement": (
                    services.serialize_requirement(
                        requirement,
                        include_content=True,
                    )
                ),
                "error": None,
            }
        ), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"上传需求失败：{exc}"}
        ), 500


def get_requirement_response(services, requirement_uid):
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        modules = [
            services.serialize_module(row)
            for row in services.list_modules(requirement["id"])
        ]
        return jsonify(
            {
                "requirement": (
                    services.serialize_requirement(
                        requirement,
                        include_content=True,
                    )
                ),
                "modules": modules,
                "error": None,
            }
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"读取需求失败：{exc}"}
        ), 500


def download_requirement_response(
    services,
    requirement_uid,
):
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        path = Path(requirement.get("file_path") or "")
        if not path.exists() or not path.is_file():
            return jsonify(
                {"error": f"需求文件不存在：{path}"}
            ), 404
        return send_file(
            path,
            as_attachment=True,
            download_name=(
                requirement.get("filename") or path.name
            ),
            conditional=True,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"下载需求失败：{exc}"}
        ), 500


def delete_requirement_response(services, requirement_uid):
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        payload = request.get_json(silent=True) or {}
        confirmation_name = str(
            payload.get("confirmation_name") or ""
        ).strip()
        requirement_name = str(
            requirement.get("title") or ""
        ).strip()
        if confirmation_name != requirement_name:
            raise ValueError("输入的需求名称不匹配。")
        deleted = services.delete_requirement(requirement_uid)
        if not deleted:
            return jsonify({"error": "需求不存在。"}), 404
        return jsonify({"ok": True, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"删除需求失败：{exc}"}
        ), 500


def get_requirement_modules_response(
    services,
    requirement_uid,
):
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        modules = [
            services.serialize_module(row)
            for row in services.list_modules(requirement["id"])
        ]
        return jsonify({"modules": modules, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"读取候选模块失败：{exc}"}
        ), 500


def put_requirement_module_response(
    services,
    requirement_uid,
    module_uid,
):
    payload = request.get_json(silent=True) or {}
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        if payload.get("reset_planner_prompt"):
            existing = services.get_module(
                requirement["id"],
                module_uid,
            )
            if not existing:
                return jsonify(
                    {"error": "候选模块不存在。"}
                ), 404
            reset_data = {
                **services.serialize_module(existing),
                **payload,
            }
            reset_data["planner_prompt"] = (
                services.build_planner_prompt(
                    reset_data,
                    requirement=requirement,
                )
            )
            payload = reset_data
        row = services.update_module(
            requirement["id"],
            module_uid,
            payload,
        )
        if not row:
            return jsonify(
                {"error": "候选模块不存在。"}
            ), 404
        return jsonify(
            {
                "module": services.serialize_module(row),
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"保存候选模块失败：{exc}"}
        ), 500


def remove_requirement_module_response(
    services,
    requirement_uid,
    module_uid,
):
    try:
        requirement = services.get_requirement(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        deleted = services.delete_module(
            requirement["id"],
            module_uid,
        )
        if not deleted:
            return jsonify(
                {"error": "候选模块不存在。"}
            ), 404
        return jsonify({"ok": True, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"删除候选模块失败：{exc}"}
        ), 500


def create_requirements_blueprint(services):
    """Create non-streaming routes from explicit services."""

    if not isinstance(services, RequirementWebServices):
        raise TypeError(
            "services must be a RequirementWebServices instance"
        )

    blueprint = Blueprint("requirements", __name__)
    blueprint.add_url_rule(
        "/api/requirements",
        view_func=lambda: list_requirements_response(services),
        methods=["GET"],
        endpoint="list_requirements",
    )
    blueprint.add_url_rule(
        "/api/requirements/upload",
        view_func=lambda: upload_requirement_response(services),
        methods=["POST"],
        endpoint="upload_requirement",
    )
    blueprint.add_url_rule(
        "/api/requirements/<requirement_uid>",
        view_func=lambda requirement_uid: (
            get_requirement_response(
                services,
                requirement_uid,
            )
        ),
        methods=["GET"],
        endpoint="get_requirement",
    )
    blueprint.add_url_rule(
        "/api/requirements/<requirement_uid>/download",
        view_func=lambda requirement_uid: (
            download_requirement_response(
                services,
                requirement_uid,
            )
        ),
        methods=["GET"],
        endpoint="download_requirement",
    )
    blueprint.add_url_rule(
        "/api/requirements/<requirement_uid>",
        view_func=lambda requirement_uid: (
            delete_requirement_response(
                services,
                requirement_uid,
            )
        ),
        methods=["DELETE"],
        endpoint="delete_requirement",
    )
    blueprint.add_url_rule(
        "/api/requirements/<requirement_uid>/modules",
        view_func=lambda requirement_uid: (
            get_requirement_modules_response(
                services,
                requirement_uid,
            )
        ),
        methods=["GET"],
        endpoint="get_requirement_modules",
    )
    blueprint.add_url_rule(
        (
            "/api/requirements/<requirement_uid>/modules/"
            "<module_uid>"
        ),
        view_func=lambda requirement_uid, module_uid: (
            put_requirement_module_response(
                services,
                requirement_uid,
                module_uid,
            )
        ),
        methods=["PUT"],
        endpoint="put_requirement_module",
    )
    blueprint.add_url_rule(
        (
            "/api/requirements/<requirement_uid>/modules/"
            "<module_uid>"
        ),
        view_func=lambda requirement_uid, module_uid: (
            remove_requirement_module_response(
                services,
                requirement_uid,
                module_uid,
            )
        ),
        methods=["DELETE"],
        endpoint="remove_requirement_module",
    )
    return blueprint
