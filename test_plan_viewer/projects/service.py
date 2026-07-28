"""Project use cases independent from Flask and the application module."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from test_plan_viewer.projects.model import (
    normalize_create_project_payload,
)


@dataclass(frozen=True)
class ProjectServiceDependencies:
    """Application operations required by project use cases."""

    load_config: Callable
    parse_project_key: Callable
    parse_project_path_segment: Callable
    get_platform_database_config: Callable
    ensure_platform_database_schema: Callable
    assert_project_key_available: Callable
    get_project_workspace_root_for_create: Callable
    get_created_project_root: Callable
    initialize_created_project_directory: Callable
    create_project_record: Callable
    current_context_project: Callable
    get_project_by_key: Callable
    get_current_project: Callable
    update_project_settings: Callable
    remove_tree: Callable
    uuid_hex: Callable


class ProjectService:
    """Coordinate configuration, persistence, and workspace operations."""

    def __init__(self, dependencies):
        if not isinstance(dependencies, ProjectServiceDependencies):
            raise TypeError(
                "dependencies must be ProjectServiceDependencies"
            )
        self.dependencies = dependencies

    def get_config_projects(self):
        config = self.dependencies.load_config()
        if config["error"]:
            raise RuntimeError(config["error"])
        return config.get("projects") or []

    def get_config_default_project(self):
        config = self.dependencies.load_config()
        if config["error"]:
            raise RuntimeError(config["error"])
        projects = config.get("projects") or []
        default_key = config.get("default_project_key")
        for project in projects:
            if project["project_key"] == default_key:
                return {**project, "project_id": None}
        if projects:
            return {**projects[0], "project_id": None}
        raise RuntimeError("未配置可用项目。")

    def create_project(self, payload):
        project = normalize_create_project_payload(
            payload,
            parse_project_key=self.dependencies.parse_project_key,
            parse_project_path_segment=(
                self.dependencies.parse_project_path_segment
            ),
        )
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise ValueError(
                "新增项目需要启用平台 MySQL 持久化。"
            )

        self.dependencies.ensure_platform_database_schema(config)
        self.dependencies.assert_project_key_available(
            config,
            project["project_key"],
        )

        workspace_root = (
            self.dependencies.get_project_workspace_root_for_create()
        )
        if workspace_root.exists() and not workspace_root.is_dir():
            raise ValueError(
                f"project_workspace_root 不是目录：{workspace_root}"
            )
        workspace_root.mkdir(parents=True, exist_ok=True)
        project_root = self.dependencies.get_created_project_root(
            workspace_root,
            project["project_key"],
        )
        if project_root.exists():
            raise ValueError(f"项目目录已存在：{project_root}")

        temporary_root = (
            Path(workspace_root)
            / (
                f".{project['project_key']}.creating-"
                f"{self.dependencies.uuid_hex()}"
            )
        )
        if temporary_root.exists():
            self.dependencies.remove_tree(temporary_root)
        temporary_root.mkdir(parents=True)

        project_directory_created = False
        try:
            self.dependencies.initialize_created_project_directory(
                temporary_root,
                project["project_key"],
                project["name"],
                project["specs_dir"],
                project["tests_dir"],
            )
            if project_root.exists():
                raise ValueError(
                    f"项目目录已存在：{project_root}"
                )
            temporary_root.rename(project_root)
            project_directory_created = True
            return self.dependencies.create_project_record(
                config,
                project,
                project_root,
            )
        except Exception:
            if project_directory_created:
                self.dependencies.remove_tree(
                    project_root,
                    ignore_errors=True,
                )
            else:
                self.dependencies.remove_tree(
                    temporary_root,
                    ignore_errors=True,
                )
            raise

    def resolve_current_project(self, requested_key):
        threaded_project = self.dependencies.current_context_project()
        if threaded_project:
            return threaded_project
        return self.dependencies.get_project_by_key(requested_key)

    def update_current_project_settings(
        self,
        target_system,
        database_baseline,
        plan_generation,
    ):
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise RuntimeError(
                "项目配置需要启用平台 MySQL 持久化。"
            )
        self.dependencies.ensure_platform_database_schema(config)
        project = self.dependencies.get_current_project()
        project_key = project.get("project_key")
        if not project_key:
            raise RuntimeError("当前项目不可用。")
        return self.dependencies.update_project_settings(
            config,
            project_key,
            target_system,
            database_baseline,
            plan_generation,
        )

    def get_current_project_id(self):
        project_id = self.dependencies.get_current_project().get(
            "project_id"
        )
        if project_id is None:
            return None
        return int(project_id)
