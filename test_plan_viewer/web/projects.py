"""HTTP delivery for project listing, creation, and settings."""

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify, request


@dataclass(frozen=True)
class ProjectWebServices:
    """Application services consumed by project HTTP handlers."""

    list_projects: Callable
    serialize_project: Callable
    get_current_project: Callable
    get_project_workspace_root_text: Callable
    create_project: Callable
    parse_target_system_config: Callable
    get_database_baseline_config: Callable
    get_plan_generation_config: Callable
    parse_database_baseline_config: Callable
    parse_plan_generation_config: Callable
    update_project_settings: Callable
    update_project_language: Callable
    can_manage_project_language: Callable
    serialize_coverage_profiles: Callable
    get_seed_script_relative_path: Callable
    update_project: Callable = lambda _project_key, _payload: {}
    delete_project: Callable = (
        lambda _project_key, _confirmation_name, _current_project_key: {}
    )
    get_config_project_keys: Callable = lambda: set()
    get_default_project_language: Callable = lambda: "en"
    get_seed_mode: Callable = (
        lambda target_system: target_system.get("seed_mode") or "login"
    )


def list_projects_response(services):
    try:
        config_project_keys = set(services.get_config_project_keys())
        projects = [
            services.serialize_project(project)
            for project in services.list_projects()
        ]
        for project in projects:
            project["is_system"] = (
                project.get("project_key") in config_project_keys
            )
        default_project = next(
            (
                project
                for project in projects
                if project.get("is_default")
            ),
            projects[0] if projects else None,
        )
        try:
            current_project = services.serialize_project(
                services.get_current_project()
            )
        except ValueError:
            current_project = default_project
        default_project = default_project or current_project
        return jsonify(
            {
                "projects": projects,
                "current_project": current_project,
                "default_project": default_project,
                "project_workspace_root": (
                    services.get_project_workspace_root_text()
                ),
                "default_project_language": (
                    services.get_default_project_language()
                ),
                "error": None,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "projects": [],
                "error": f"读取项目列表失败：{exc}",
            }
        ), 500


def create_project_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        project = services.create_project(payload)
        return jsonify({"project": project, "error": None}), 201
    except ValueError as exc:
        status = 409 if "已存在" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except Exception as exc:
        return jsonify({"error": f"创建项目失败：{exc}"}), 500


def update_project_response(services, project_key):
    payload = request.get_json(silent=True) or {}
    try:
        project = services.update_project(project_key, payload)
        return jsonify(
            {
                "project": services.serialize_project(project),
                "error": None,
            }
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if "不存在或已禁用" in message else 409 if "系统项目" in message else 400
        return jsonify({"error": message}), status
    except Exception as exc:
        return jsonify({"error": f"修改项目失败：{exc}"}), 500


def delete_project_response(services, project_key):
    payload = request.get_json(silent=True) or {}
    current_project_key = str(
        request.headers.get("X-Project-Key") or ""
    ).strip()
    if not current_project_key:
        return jsonify({"error": "缺少当前项目标识。"}), 400
    try:
        result = services.delete_project(
            project_key,
            payload.get("confirmation_name"),
            current_project_key,
        )
        return jsonify({"deleted": result, "error": None})
    except ValueError as exc:
        message = str(exc)
        if "不存在或已禁用" in message:
            status = 404
        elif any(
            marker in message
            for marker in (
                "不能删除",
                "不能删除，请先切换",
                "至少需要保留",
                "暂不能删除",
            )
        ):
            status = 409
        else:
            status = 400
        return jsonify({"error": message}), status
    except Exception as exc:
        return jsonify({"error": f"删除项目失败：{exc}"}), 500


def get_project_settings_response(services):
    try:
        project = services.get_current_project()
        target_system = services.parse_target_system_config(
            project.get("target_system")
        )
        seed_mode = services.get_seed_mode(target_system)
        database_baseline = services.get_database_baseline_config()
        plan_generation = services.get_plan_generation_config()
        return jsonify(
            {
                "project": services.serialize_project(
                    project,
                    include_sensitive=True,
                ),
                "target_system": target_system,
                "seed_mode": seed_mode,
                "database_baseline": (
                    services.parse_database_baseline_config(
                        database_baseline
                    )
                ),
                "plan_generation": plan_generation,
                "coverage_profiles": (
                    services.serialize_coverage_profiles()
                ),
                "seed_script_path": (
                    services.get_seed_script_relative_path()
                ),
                "error": None,
            }
        )
    except Exception as exc:
        return jsonify(
            {"error": f"读取项目配置失败：{exc}"}
        ), 500


def save_project_settings_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        target_system_payload = payload.get("target_system")
        requested_seed_mode = payload.get("seed_mode")
        if isinstance(target_system_payload, dict) and (
            "seed_mode" not in target_system_payload
            or "seed_mode" in payload
        ):
            if "seed_mode" not in payload:
                current_project = services.get_current_project()
                requested_seed_mode = (
                    services.parse_target_system_config(
                        current_project.get("target_system")
                    ).get("seed_mode")
                )
            target_system_payload = {
                **target_system_payload,
                "seed_mode": requested_seed_mode,
            }
        elif "seed_mode" in payload and target_system_payload is None:
            current_project = services.get_current_project()
            target_system_payload = {
                **services.parse_target_system_config(
                    current_project.get("target_system")
                ),
                "seed_mode": requested_seed_mode,
            }
        target_system = services.parse_target_system_config(
            target_system_payload
        )
        database_baseline = services.parse_database_baseline_config(
            payload.get("database_baseline")
        )
        plan_generation = services.parse_plan_generation_config(
            payload.get("plan_generation")
        )
        project = services.update_project_settings(
            target_system,
            database_baseline,
            plan_generation,
        )
        updated_target_system = services.parse_target_system_config(
            project.get("target_system")
        )
        effective_seed_mode = services.get_seed_mode(
            updated_target_system
        )
        return jsonify(
            {
                "project": services.serialize_project(
                    project,
                    include_sensitive=True,
                ),
                "target_system": updated_target_system,
                "seed_mode": effective_seed_mode,
                "database_baseline": (
                    services.parse_database_baseline_config(
                        project.get("database_baseline")
                    )
                ),
                "plan_generation": (
                    services.parse_plan_generation_config(
                        project.get("plan_generation")
                    )
                ),
                "coverage_profiles": (
                    services.serialize_coverage_profiles()
                ),
                "seed_script_path": (
                    services.get_seed_script_relative_path()
                ),
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"保存项目配置失败：{exc}"}
        ), 500


def update_project_language_response(services):
    if not services.can_manage_project_language():
        return jsonify({"error": "Only the built-in admin role can change the project language."}), 403

    payload = request.get_json(silent=True) or {}
    try:
        project = services.update_project_language(payload.get("language"))
        serialized_project = services.serialize_project(project)
        return jsonify(
            {
                "project": serialized_project,
                "language": serialized_project.get("language"),
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to update project language: {exc}"}), 500


def create_projects_blueprint(services):
    """Create project routes using application-provided collaborators."""

    if not isinstance(services, ProjectWebServices):
        raise TypeError("services must be a ProjectWebServices instance")

    blueprint = Blueprint("projects", __name__)
    blueprint.add_url_rule(
        "/api/projects",
        view_func=lambda: list_projects_response(services),
        methods=["GET"],
        endpoint="list_projects",
    )
    blueprint.add_url_rule(
        "/api/projects",
        view_func=lambda: create_project_response(services),
        methods=["POST"],
        endpoint="create_project",
    )
    blueprint.add_url_rule(
        "/api/projects/<project_key>",
        view_func=lambda project_key: update_project_response(
            services,
            project_key,
        ),
        methods=["PATCH"],
        endpoint="update_project",
    )
    blueprint.add_url_rule(
        "/api/projects/<project_key>",
        view_func=lambda project_key: delete_project_response(
            services,
            project_key,
        ),
        methods=["DELETE"],
        endpoint="delete_project",
    )
    blueprint.add_url_rule(
        "/api/project-settings",
        view_func=lambda: get_project_settings_response(services),
        methods=["GET"],
        endpoint="get_project_settings",
    )
    blueprint.add_url_rule(
        "/api/project-settings",
        view_func=lambda: save_project_settings_response(services),
        methods=["PUT"],
        endpoint="save_project_settings",
    )
    blueprint.add_url_rule(
        "/api/project-language",
        view_func=lambda: update_project_language_response(services),
        methods=["PUT"],
        endpoint="update_project_language",
    )
    return blueprint
