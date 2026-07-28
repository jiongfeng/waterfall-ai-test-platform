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
    serialize_coverage_profiles: Callable
    get_seed_script_relative_path: Callable


def list_projects_response(services):
    try:
        projects = [
            services.serialize_project(project)
            for project in services.list_projects()
        ]
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


def get_project_settings_response(services):
    try:
        project = services.get_current_project()
        target_system = services.parse_target_system_config(
            project.get("target_system")
        )
        database_baseline = services.get_database_baseline_config()
        plan_generation = services.get_plan_generation_config()
        return jsonify(
            {
                "project": services.serialize_project(
                    project,
                    include_sensitive=True,
                ),
                "target_system": target_system,
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
        target_system = services.parse_target_system_config(
            payload.get("target_system")
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
        return jsonify(
            {
                "project": services.serialize_project(
                    project,
                    include_sensitive=True,
                ),
                "target_system": (
                    services.parse_target_system_config(
                        project.get("target_system")
                    )
                ),
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
    return blueprint
