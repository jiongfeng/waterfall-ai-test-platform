import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import app
from test_plan_viewer.configuration import (
    parse_plan_generation_config,
    parse_project_key,
    parse_project_path_segment,
)
from test_plan_viewer.projects import model, repository, service, workspace


class FakeCursor:
    def __init__(self, *, active_table=None):
        self.active_table = active_table
        self.rowcount = 0
        self.executed = []
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executed.append((normalized, params))
        self.rowcount = 0
        if normalized.startswith("SELECT * FROM `projects`"):
            self._result = {
                "project_id": 7,
                "project_key": "demo",
                "name": "Demo",
                "is_default": 0,
            }
        elif normalized.startswith("SELECT COUNT(*) AS total FROM `projects`"):
            self._result = {"total": 2}
        elif normalized.startswith("SELECT COUNT(*) AS total FROM"):
            table_name = normalized.split("FROM ", 1)[1].split(" ", 1)[0]
            self._result = {
                "total": 1 if table_name == f"`{self.active_table}`" else 0
            }
        elif normalized.startswith("DELETE FROM"):
            self.rowcount = 1
            self._result = None

    def fetchone(self):
        return self._result


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def repository_dependencies(connection):
    @contextmanager
    def connect(_config):
        yield connection

    return repository.ProjectRepositoryDependencies(
        get_platform_database_config=lambda: {"enabled": True},
        ensure_platform_database_schema=Mock(),
        get_platform_projects_table=lambda _config: "`projects`",
        platform_mysql_connection=connect,
        get_config_projects=lambda: [],
        get_config_default_project=lambda: {},
        serialize_project_row=model.serialize_project_row,
        parse_plan_generation_config=parse_plan_generation_config,
        current_time_ms=lambda: 123,
        platform_table_sql=lambda _config, name: f"`{name}`",
    )


def service_dependencies(workspace_root, project, **overrides):
    values = {
        "load_config": lambda: {
            "error": None,
            "projects": [],
            "default_project_key": "",
        },
        "parse_project_key": parse_project_key,
        "parse_project_path_segment": parse_project_path_segment,
        "get_platform_database_config": lambda: {"enabled": True},
        "ensure_platform_database_schema": Mock(),
        "assert_project_key_available": Mock(),
        "get_project_workspace_root_for_create": lambda: Path(workspace_root),
        "get_created_project_root": workspace.get_created_project_root,
        "initialize_created_project_directory": Mock(),
        "create_project_record": Mock(),
        "current_context_project": lambda: None,
        "get_project_by_key": lambda _key: project,
        "get_current_project": lambda: project,
        "update_project_settings": Mock(),
        "update_project_metadata": Mock(),
        "delete_project_data": Mock(
            return_value={
                "project_id": project.get("project_id"),
                "project_key": project.get("project_key"),
                "name": project.get("name"),
                "deleted_records": 9,
                "deleted_counts": {},
            }
        ),
        "remove_tree": shutil.rmtree,
        "uuid_hex": lambda: "fixed",
    }
    values.update(overrides)
    return service.ProjectServiceDependencies(**values)


class ProjectRepositoryDeletionTests(unittest.TestCase):
    def test_permanent_delete_removes_every_project_scoped_table(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)

        result = repository.delete_project_data(
            {"enabled": True},
            "demo",
            repository_dependencies(connection),
        )

        deleted_tables = {
            statement.split("DELETE FROM ", 1)[1].split(" ", 1)[0]
            for statement, _params in cursor.executed
            if statement.startswith("DELETE FROM")
        }
        expected_tables = {
            f"`{name}`"
            for name in (
                *repository.PROJECT_SCOPED_TABLES_DELETE_ORDER,
                "test_asset_revisions",
                "test_assets",
                "projects",
            )
        }
        self.assertEqual(deleted_tables, expected_tables)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(result["project_key"], "demo")

    def test_active_project_work_aborts_and_rolls_back(self):
        cursor = FakeCursor(active_table="agent_runs")
        connection = FakeConnection(cursor)

        with self.assertRaisesRegex(ValueError, "运行中或待处理"):
            repository.delete_project_data(
                {"enabled": True},
                "demo",
                repository_dependencies(connection),
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(
            any(
                statement.startswith("DELETE FROM")
                for statement, _params in cursor.executed
            )
        )


class ProjectServiceDeletionTests(unittest.TestCase):
    def test_delete_moves_workspace_then_deletes_database_and_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)
            project_root = workspace_root / "demo"
            project_root.mkdir()
            (project_root / "test.txt").write_text("data", encoding="utf-8")
            project = {
                "project_id": 7,
                "project_key": "demo",
                "name": "Demo",
                "playwright_project_root": str(project_root),
            }
            dependencies = service_dependencies(workspace_root, project)

            result = service.ProjectService(dependencies).delete_project(
                "demo",
                "Demo",
                "other",
            )

            self.assertFalse(project_root.exists())
            self.assertFalse((workspace_root / ".demo.deleting-fixed").exists())
            dependencies.delete_project_data.assert_called_once_with(
                {"enabled": True},
                "demo",
            )
            self.assertTrue(result["workspace_deleted"])

    def test_database_failure_restores_the_workspace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)
            project_root = workspace_root / "demo"
            project_root.mkdir()
            marker = project_root / "test.txt"
            marker.write_text("data", encoding="utf-8")
            project = {
                "project_id": 7,
                "project_key": "demo",
                "name": "Demo",
                "playwright_project_root": str(project_root),
            }
            dependencies = service_dependencies(
                workspace_root,
                project,
                delete_project_data=Mock(
                    side_effect=RuntimeError("database failed")
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "database failed"):
                service.ProjectService(dependencies).delete_project(
                    "demo",
                    "Demo",
                    "other",
                )

            self.assertTrue(marker.is_file())
            self.assertFalse((workspace_root / ".demo.deleting-fixed").exists())

    def test_delete_rejects_a_symlinked_project_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)
            real_root = workspace_root / "real-project"
            real_root.mkdir()
            project_root = workspace_root / "demo"
            project_root.symlink_to(real_root, target_is_directory=True)
            project = {
                "project_id": 7,
                "project_key": "demo",
                "name": "Demo",
                "playwright_project_root": str(project_root),
            }
            dependencies = service_dependencies(workspace_root, project)

            with self.assertRaisesRegex(ValueError, "符号链接"):
                service.ProjectService(dependencies).delete_project(
                    "demo",
                    "Demo",
                    "other",
                )

            self.assertTrue(real_root.is_dir())
            dependencies.delete_project_data.assert_not_called()

    def test_delete_requires_exact_name_and_non_current_non_system_project(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)
            project_root = workspace_root / "demo"
            project_root.mkdir()
            project = {
                "project_id": 7,
                "project_key": "demo",
                "name": "Demo",
                "playwright_project_root": str(project_root),
            }
            dependencies = service_dependencies(workspace_root, project)
            project_service = service.ProjectService(dependencies)

            with self.assertRaisesRegex(ValueError, "名称不匹配"):
                project_service.delete_project("demo", "demo", "other")
            with self.assertRaisesRegex(ValueError, "当前项目不能删除"):
                project_service.delete_project("demo", "Demo", "demo")

            system_dependencies = service_dependencies(
                workspace_root,
                project,
                load_config=lambda: {
                    "error": None,
                    "projects": [{"project_key": "demo"}],
                    "default_project_key": "demo",
                },
            )
            with self.assertRaisesRegex(ValueError, "系统项目"):
                service.ProjectService(system_dependencies).delete_project(
                    "demo",
                    "Demo",
                    "other",
                )


class ProjectManagementRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.auth_disabled = patch.object(
            app,
            "get_auth_config",
            return_value={"enabled": False},
        )

    def test_patch_updates_project_metadata(self):
        updated = {"project_id": 7, "project_key": "demo", "name": "Renamed"}
        with (
            self.auth_disabled,
            patch.object(
                app,
                "update_project_in_mysql",
                return_value=updated,
            ) as update_project,
        ):
            response = self.client.patch(
                "/api/projects/demo",
                json={"name": "Renamed", "description": "Description"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["project"]["name"], "Renamed")
        update_project.assert_called_once_with(
            "demo",
            {"name": "Renamed", "description": "Description"},
        )

    def test_delete_passes_confirmation_and_current_project(self):
        deleted = {"project_id": 7, "project_key": "demo", "name": "Demo"}
        with (
            self.auth_disabled,
            patch.object(
                app,
                "delete_project_in_mysql",
                return_value=deleted,
            ) as delete_project,
        ):
            response = self.client.delete(
                "/api/projects/demo",
                json={"confirmation_name": "Demo"},
                headers={"X-Project-Key": "other"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["deleted"], deleted)
        delete_project.assert_called_once_with("demo", "Demo", "other")


if __name__ == "__main__":
    unittest.main()
