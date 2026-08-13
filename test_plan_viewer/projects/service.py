"""Project use cases independent from Flask and the application module."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from test_plan_viewer.configuration import normalize_project_language
from test_plan_viewer.projects.model import (
    normalize_create_project_payload,
    normalize_update_project_payload,
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
    update_project_metadata: Callable
    delete_project_data: Callable
    remove_tree: Callable
    uuid_hex: Callable
    update_project_language: Callable = lambda *_args: None


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

    def get_config_default_project_language(self):
        config = self.dependencies.load_config()
        if config["error"]:
            raise RuntimeError(config["error"])
        return normalize_project_language(
            config.get("default_project_language")
        )

    def create_project(self, payload):
        default_language = self.get_config_default_project_language()
        project = normalize_create_project_payload(
            payload,
            parse_project_key=self.dependencies.parse_project_key,
            parse_project_path_segment=(
                self.dependencies.parse_project_path_segment
            ),
            default_language=default_language,
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

    def _configured_project_keys(self):
        return {
            project.get("project_key")
            for project in self.get_config_projects()
            if project.get("project_key")
        }

    def update_project(self, project_key, payload):
        project_key = self.dependencies.parse_project_key(
            project_key,
            "project_key",
        )
        if project_key in self._configured_project_keys():
            raise ValueError("系统项目由配置文件托管，不能修改。")
        metadata = normalize_update_project_payload(payload)
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise ValueError("修改项目需要启用平台 MySQL 持久化。")
        self.dependencies.ensure_platform_database_schema(config)
        return self.dependencies.update_project_metadata(
            config,
            project_key,
            metadata,
        )

    def delete_project(
        self,
        project_key,
        confirmation_name,
        current_project_key,
    ):
        project_key = self.dependencies.parse_project_key(
            project_key,
            "project_key",
        )
        if project_key in self._configured_project_keys():
            raise ValueError("系统项目由配置文件托管，不能删除。")
        if project_key == str(current_project_key or "").strip():
            raise ValueError("当前项目不能删除，请先切换到其他项目。")

        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise ValueError("删除项目需要启用平台 MySQL 持久化。")
        self.dependencies.ensure_platform_database_schema(config)
        project = self.dependencies.get_project_by_key(project_key)
        project_name = str(project.get("name") or project_key)
        if str(confirmation_name or "").strip() != project_name:
            raise ValueError("输入的项目名称不匹配。")

        workspace_root = Path(
            self.dependencies.get_project_workspace_root_for_create()
        ).expanduser().resolve()
        expected_root_path = self.dependencies.get_created_project_root(
            workspace_root,
            project_key,
        ).expanduser()
        project_root_text = str(
            project.get("playwright_project_root") or ""
        ).strip()
        if not project_root_text:
            raise ValueError("项目工作区目录为空，已拒绝删除。")
        project_root_path = Path(project_root_text).expanduser()
        if project_root_path.is_symlink() or expected_root_path.is_symlink():
            raise ValueError("项目工作区目录不能是符号链接，已拒绝删除。")
        expected_root = expected_root_path.resolve()
        project_root = project_root_path.resolve()
        if project_root != expected_root or project_root.parent != workspace_root:
            raise ValueError("项目工作区目录不在受控工作区内，已拒绝删除。")

        deleting_root = workspace_root / (
            f".{project_key}.deleting-{self.dependencies.uuid_hex()}"
        )
        directory_moved = False
        if project_root.exists():
            if not project_root.is_dir():
                raise ValueError("项目工作区路径不是目录，已拒绝删除。")
            if deleting_root.exists():
                raise RuntimeError(f"临时删除目录已存在：{deleting_root}")
            project_root.rename(deleting_root)
            directory_moved = True

        try:
            result = self.dependencies.delete_project_data(
                config,
                project_key,
            )
        except Exception:
            if directory_moved and deleting_root.exists():
                deleting_root.rename(project_root)
            raise

        if directory_moved:
            try:
                self.dependencies.remove_tree(deleting_root)
            except Exception as exc:
                raise RuntimeError(
                    "项目数据库数据已删除，但本地工作区目录清理失败："
                    f"{exc}"
                ) from exc

        return {
            **result,
            "workspace_deleted": directory_moved,
        }

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

    def update_current_project_language(self, language):
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise RuntimeError(
                "项目语言需要启用平台 MySQL 持久化。"
            )
        self.dependencies.ensure_platform_database_schema(config)
        project = self.dependencies.get_current_project()
        project_key = project.get("project_key")
        if not project_key:
            raise RuntimeError("当前项目不可用。")
        return self.dependencies.update_project_language(
            config,
            project_key,
            language,
        )

    def get_current_project_id(self):
        project_id = self.dependencies.get_current_project().get(
            "project_id"
        )
        if project_id is None:
            return None
        return int(project_id)
