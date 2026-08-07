import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
from test_plan_viewer.configuration import (
    parse_plan_generation_config,
    parse_project_key,
    parse_project_path_segment,
)
from test_plan_viewer.projects import model
from test_plan_viewer.projects import repository
from test_plan_viewer.projects import service
from test_plan_viewer.projects import workspace


PROJECTS_PACKAGE = (
    Path(__file__).resolve().parents[1]
    / "test_plan_viewer"
    / "projects"
)


def make_repository_dependencies(**overrides):
    values = {
        "get_platform_database_config": lambda: {"enabled": False},
        "ensure_platform_database_schema": Mock(),
        "get_platform_projects_table": lambda _config: "`projects`",
        "platform_mysql_connection": Mock(),
        "get_config_projects": lambda: [],
        "get_config_default_project": lambda: {
            "project_key": "default",
            "project_id": None,
        },
        "serialize_project_row": model.serialize_project_row,
        "parse_plan_generation_config": parse_plan_generation_config,
        "current_time_ms": lambda: 1234,
    }
    values.update(overrides)
    return repository.ProjectRepositoryDependencies(**values)


def make_workspace_dependencies(template_dir, **overrides):
    values = {
        "load_config": lambda: {"error": None},
        "template_dir": Path(template_dir),
        "dependency_dirs": (),
        "text_suffixes": frozenset({".md", ".ts", ".json", ""}),
        "subprocess_run": Mock(),
        "get_project_workspace_root_text": Mock(),
        "get_project_template_dependency_source_text": Mock(),
        "get_project_dependency_source_root_for_create": Mock(),
        "template_relative_target_path": (
            workspace.template_relative_target_path
        ),
        "render_project_template_text": (
            workspace.render_project_template_text
        ),
        "copy_project_template_files": Mock(),
        "copy_project_template_dependencies": Mock(),
        "run_project_git_command": Mock(),
        "initialize_created_project_git_repo": Mock(),
    }
    values.update(overrides)
    return workspace.ProjectWorkspaceDependencies(**values)


def make_service_dependencies(workspace_root, **overrides):
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
        "get_project_workspace_root_for_create": lambda: Path(
            workspace_root
        ),
        "get_created_project_root": workspace.get_created_project_root,
        "initialize_created_project_directory": Mock(),
        "create_project_record": Mock(),
        "current_context_project": lambda: None,
        "get_project_by_key": Mock(),
        "get_current_project": lambda: {"project_id": None},
        "update_project_settings": Mock(),
        "remove_tree": shutil.rmtree,
        "uuid_hex": lambda: "fixed",
    }
    values.update(overrides)
    return service.ProjectServiceDependencies(**values)


class ProjectModelTests(unittest.TestCase):
    def test_create_payload_is_normalized_without_framework_state(self):
        payload = model.normalize_create_project_payload(
            {
                "project_key": " demo ",
                "name": " 演示项目 ",
                "description": " details ",
                "specs_dir": "plans",
            },
            parse_project_key=parse_project_key,
            parse_project_path_segment=parse_project_path_segment,
        )

        self.assertEqual(
            payload,
            {
                "project_key": "demo",
                "name": "演示项目",
                "description": "details",
                "specs_dir": "plans",
                "tests_dir": "tests",
                "language": "en",
            },
        )

    def test_public_serialization_redacts_only_the_password(self):
        project = {
            "project_id": 7,
            "project_key": "demo",
            "name": "Demo",
            "target_system": {
                "base_url": "https://example.test",
                "username": "tester",
                "password": "secret",
            },
        }

        public = model.serialize_project(project)
        sensitive = model.serialize_project(
            project,
            include_sensitive=True,
        )

        self.assertEqual(public["target_system"]["password"], "")
        self.assertEqual(
            sensitive["target_system"]["password"],
            "secret",
        )
        self.assertEqual(public["key"], "demo")

    def test_row_serialization_tolerates_invalid_json_columns(self):
        project = model.serialize_project_row(
            {
                "project_id": 3,
                "project_key": "broken",
                "target_system_json": "{",
                "plan_generation_json": "not-json",
            }
        )

        self.assertEqual(project["project_key"], "broken")
        self.assertEqual(project["target_system"]["password"], "")
        self.assertEqual(
            project["plan_generation"]["default_coverage_profile"],
            "core",
        )


class ProjectRepositoryTests(unittest.TestCase):
    def test_disabled_database_uses_the_configuration_default(self):
        default_project = {
            "project_key": "configured",
            "project_id": None,
        }
        ensure_schema = Mock()
        dependencies = make_repository_dependencies(
            get_config_default_project=lambda: default_project,
            ensure_platform_database_schema=ensure_schema,
        )

        self.assertEqual(
            repository.list_projects(dependencies),
            [default_project],
        )
        ensure_schema.assert_not_called()

    def test_configuration_lookup_keeps_the_legacy_default_fallback(self):
        dependencies = make_repository_dependencies(
            get_config_projects=lambda: [
                {"project_key": "alpha"},
                {"project_key": "beta"},
            ],
            get_config_default_project=lambda: {
                "project_key": "alpha",
                "project_id": None,
            },
        )

        selected = repository.get_project_by_key(
            "beta",
            dependencies,
        )
        missing = repository.get_project_by_key(
            "missing",
            dependencies,
        )

        self.assertEqual(selected["project_key"], "beta")
        self.assertIsNone(selected["project_id"])
        self.assertEqual(missing["project_key"], "alpha")


class ProjectWorkspaceTests(unittest.TestCase):
    def test_template_copy_remaps_project_directories_and_placeholders(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template_dir = root / "template"
            target_dir = root / "target"
            (template_dir / "specs").mkdir(parents=True)
            (template_dir / "tests").mkdir(parents=True)
            (template_dir / "specs" / "readme.md").write_text(
                "{{PROJECT_KEY}}|{{PROJECT_NAME}}|{{PACKAGE_NAME}}",
                encoding="utf-8",
            )
            (template_dir / "tests" / "smoke.spec.ts").write_text(
                "{{TESTS_DIR}}",
                encoding="utf-8",
            )
            dependencies = make_workspace_dependencies(template_dir)

            workspace.copy_project_template_files(
                target_dir,
                "Demo.Project",
                "演示项目",
                "plans",
                "checks",
                dependencies,
            )

            self.assertEqual(
                (target_dir / "plans" / "readme.md").read_text(
                    encoding="utf-8"
                ),
                "Demo.Project|演示项目|demo.project",
            )
            self.assertEqual(
                (
                    target_dir / "checks" / "smoke.spec.ts"
                ).read_text(encoding="utf-8"),
                "checks",
            )

    def test_checked_in_template_becomes_unlicensed_generated_workspace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template_dir = root / "template"
            target_dir = root / "target"
            template_dir.mkdir()
            (template_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "{{PACKAGE_NAME}}",
                        "private": True,
                        "license": "Apache-2.0",
                        "x-playwright-platform-template": True,
                    }
                ),
                encoding="utf-8",
            )
            (template_dir / "package-lock.json").write_text(
                json.dumps(
                    {
                        "name": "{{PACKAGE_NAME}}",
                        "lockfileVersion": 3,
                        "packages": {
                            "": {
                                "name": "{{PACKAGE_NAME}}",
                                "license": "Apache-2.0",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            dependencies = make_workspace_dependencies(template_dir)

            workspace.copy_project_template_files(
                target_dir,
                "demo",
                "Demo",
                "specs",
                "tests",
                dependencies,
            )

            package = json.loads(
                (target_dir / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            package_lock = json.loads(
                (target_dir / "package-lock.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(package["private"])
            self.assertEqual(package["license"], "UNLICENSED")
            self.assertNotIn(
                "x-playwright-platform-template",
                package,
            )
            self.assertEqual(
                package_lock["packages"][""]["license"],
                "UNLICENSED",
            )

    def test_same_template_and_dependency_source_copies_dependencies_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template_dir = root / "template"
            target_dir = root / "target"
            dependency_dirs = (
                Path("node_modules"),
                Path(".opencode") / "node_modules",
            )
            (template_dir / ".opencode" / "prompts").mkdir(
                parents=True
            )
            (
                template_dir
                / "node_modules"
                / "@playwright"
                / "test"
            ).mkdir(parents=True)
            (
                template_dir
                / ".opencode"
                / "node_modules"
                / "@opencode-ai"
                / "plugin"
            ).mkdir(parents=True)
            (template_dir / "package.json").write_text(
                '{"name":"{{PACKAGE_NAME}}"}',
                encoding="utf-8",
            )
            (
                template_dir
                / ".opencode"
                / "prompts"
                / "agent.md"
            ).write_text(
                "{{PROJECT_NAME}}",
                encoding="utf-8",
            )
            playwright_package = (
                template_dir
                / "node_modules"
                / "@playwright"
                / "test"
                / "package.json"
            )
            playwright_package.write_text(
                '{"description":"{{PROJECT_NAME}}"}',
                encoding="utf-8",
            )
            opencode_package = (
                template_dir
                / ".opencode"
                / "node_modules"
                / "@opencode-ai"
                / "plugin"
                / "package.json"
            )
            opencode_package.write_text(
                '{"description":"{{PROJECT_NAME}}"}',
                encoding="utf-8",
            )
            bin_dir = template_dir / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            playwright_link = bin_dir / "playwright"
            try:
                playwright_link.symlink_to(
                    Path("../@playwright/test/cli.js")
                )
            except OSError:
                playwright_link = None

            dependencies = make_workspace_dependencies(
                template_dir,
                dependency_dirs=dependency_dirs,
            )

            workspace.copy_project_template_files(
                target_dir,
                "demo",
                "演示项目",
                "specs",
                "tests",
                dependencies,
            )

            self.assertEqual(
                (target_dir / "package.json").read_text(
                    encoding="utf-8"
                ),
                '{"name":"demo"}',
            )
            self.assertEqual(
                (
                    target_dir
                    / ".opencode"
                    / "prompts"
                    / "agent.md"
                ).read_text(encoding="utf-8"),
                "演示项目",
            )
            self.assertFalse(
                (target_dir / "node_modules").exists()
            )
            self.assertFalse(
                (
                    target_dir
                    / ".opencode"
                    / "node_modules"
                ).exists()
            )

            workspace.copy_project_template_dependencies(
                template_dir,
                target_dir,
                dependencies,
            )

            self.assertEqual(
                (
                    target_dir
                    / "node_modules"
                    / "@playwright"
                    / "test"
                    / "package.json"
                ).read_text(encoding="utf-8"),
                '{"description":"{{PROJECT_NAME}}"}',
            )
            self.assertEqual(
                (
                    target_dir
                    / ".opencode"
                    / "node_modules"
                    / "@opencode-ai"
                    / "plugin"
                    / "package.json"
                ).read_text(encoding="utf-8"),
                '{"description":"{{PROJECT_NAME}}"}',
            )
            if playwright_link is not None:
                copied_link = (
                    target_dir
                    / "node_modules"
                    / ".bin"
                    / "playwright"
                )
                self.assertTrue(copied_link.is_symlink())
                self.assertEqual(
                    copied_link.readlink(),
                    Path("../@playwright/test/cli.js"),
                )

    def test_directory_initialization_uses_supplied_collaborators(self):
        calls = []
        dependencies = make_workspace_dependencies(
            Path("/unused"),
            get_project_dependency_source_root_for_create=(
                lambda: Path("/dependencies")
            ),
            copy_project_template_files=(
                lambda *args: calls.append(("template", args))
            ),
            copy_project_template_dependencies=(
                lambda *args: calls.append(("dependencies", args))
            ),
            initialize_created_project_git_repo=(
                lambda *args: calls.append(("git", args))
            ),
        )

        workspace.initialize_created_project_directory(
            Path("/workspace/demo"),
            "demo",
            "Demo",
            "specs",
            "tests",
            dependencies,
        )

        self.assertEqual(
            [name for name, _args in calls],
            ["template", "dependencies", "git"],
        )


class ProjectServiceTests(unittest.TestCase):
    def test_creation_scaffolds_then_persists_the_normalized_project(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)
            initialized = []

            def initialize(project_root, *args):
                initialized.append((project_root, args))
                (project_root / "marker.txt").write_text(
                    "created",
                    encoding="utf-8",
                )

            def create_record(config, project, project_root):
                self.assertEqual(config, {"enabled": True})
                self.assertTrue((project_root / "marker.txt").is_file())
                return {
                    **project,
                    "project_id": 9,
                    "playwright_project_root": str(project_root),
                }

            dependencies = make_service_dependencies(
                workspace_root,
                initialize_created_project_directory=initialize,
                create_project_record=create_record,
            )

            created = service.ProjectService(
                dependencies
            ).create_project(
                {"project_key": "demo", "name": " Demo "}
            )

            self.assertEqual(created["project_id"], 9)
            self.assertEqual(created["name"], "Demo")
            self.assertEqual(len(initialized), 1)
            self.assertTrue(
                (workspace_root / "demo" / "marker.txt").is_file()
            )
            dependencies.ensure_platform_database_schema.assert_called_once_with(
                {"enabled": True}
            )
            dependencies.assert_project_key_available.assert_called_once_with(
                {"enabled": True},
                "demo",
            )

    def test_persistence_failure_removes_the_renamed_workspace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace_root = Path(temporary_directory)

            def initialize(project_root, *_args):
                (project_root / "marker.txt").write_text(
                    "created",
                    encoding="utf-8",
                )

            dependencies = make_service_dependencies(
                workspace_root,
                initialize_created_project_directory=initialize,
                create_project_record=Mock(
                    side_effect=RuntimeError("insert failed")
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                service.ProjectService(
                    dependencies
                ).create_project(
                    {"project_key": "demo", "name": "Demo"}
                )

            self.assertFalse((workspace_root / "demo").exists())
            self.assertFalse(
                (workspace_root / ".demo.creating-fixed").exists()
            )

    def test_thread_context_takes_precedence_over_repository_lookup(self):
        repository_lookup = Mock()
        dependencies = make_service_dependencies(
            Path("/unused"),
            current_context_project=lambda: {
                "project_key": "threaded"
            },
            get_project_by_key=repository_lookup,
        )

        selected = service.ProjectService(
            dependencies
        ).resolve_current_project("requested")

        self.assertEqual(selected["project_key"], "threaded")
        repository_lookup.assert_not_called()


class ProjectPackageBoundaryTests(unittest.TestCase):
    def test_projects_package_imports_neither_app_nor_flask(self):
        for path in sorted(PROJECTS_PACKAGE.glob("*.py")):
            with self.subTest(path=path.name):
                tree = ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                )
                imported_roots = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported_roots.update(
                            alias.name.split(".", 1)[0]
                            for alias in node.names
                        )
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                    ):
                        imported_roots.add(
                            node.module.split(".", 1)[0]
                        )
                self.assertNotIn("app", imported_roots)
                self.assertNotIn("flask", imported_roots)


class ProjectRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.auth_disabled = patch.object(
            app,
            "get_auth_config",
            return_value={"enabled": False},
        )

    def test_project_routes_keep_dynamic_app_patch_points(self):
        raw_project = {
            "project_id": 4,
            "project_key": "demo",
            "name": "Demo",
            "playwright_project_root": "/workspace/demo",
            "target_system": {"password": "secret"},
            "is_default": True,
        }
        with (
            self.auth_disabled,
            patch.object(
                app,
                "list_projects_from_mysql",
                return_value=[raw_project],
            ) as list_projects,
            patch.object(
                app,
                "get_current_project",
                return_value=raw_project,
            ),
            patch.object(
                app,
                "get_project_workspace_root_text",
                return_value="/workspace",
            ),
        ):
            response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["projects"][0]["project_key"], "demo")
        self.assertEqual(
            payload["projects"][0]["target_system"]["password"],
            "",
        )
        self.assertEqual(payload["project_workspace_root"], "/workspace")
        list_projects.assert_called_once_with()

    def test_create_route_preserves_status_and_error_contracts(self):
        created = {"project_id": 11, "project_key": "new-project"}
        with (
            self.auth_disabled,
            patch.object(
                app,
                "create_project_in_mysql",
                return_value=created,
            ) as create_project,
        ):
            response = self.client.post(
                "/api/projects",
                json={"project_key": "new-project", "name": "New"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["project"], created)
        create_project.assert_called_once_with(
            {"project_key": "new-project", "name": "New"}
        )

    def test_project_settings_routes_preserve_sensitive_payload(self):
        project = {
            "project_id": 5,
            "project_key": "demo",
            "target_system": {
                "base_url": "https://example.test",
                "username": "tester",
                "password": "secret",
            },
            "database_baseline": {"enabled": False},
            "plan_generation": {
                "default_coverage_profile": "standard"
            },
        }
        with (
            self.auth_disabled,
            patch.object(
                app,
                "get_current_project",
                return_value=project,
            ),
            patch.object(
                app,
                "get_database_baseline_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "get_plan_generation_config",
                return_value=project["plan_generation"],
            ),
        ):
            response = self.client.get("/api/project-settings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["target_system"]["password"],
            "secret",
        )

        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "update_current_project_settings_in_mysql",
                return_value=project,
            ) as update_settings,
            patch.object(
                app,
                "get_current_project",
                return_value=project,
            ),
        ):
            response = self.client.put(
                "/api/project-settings",
                json={
                    "target_system": project["target_system"],
                    "database_baseline": {"enabled": False},
                    "plan_generation": project["plan_generation"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["project"]["target_system"]["password"],
            "secret",
        )
        update_settings.assert_called_once()

    def test_compatibility_wrappers_resolve_patched_collaborators(self):
        with patch.object(
            app,
            "parse_target_system_config",
            return_value={"password": "patched"},
        ):
            serialized = app.serialize_project(
                {"project_key": "demo"},
                include_sensitive=True,
            )
        self.assertEqual(
            serialized["target_system"]["password"],
            "patched",
        )

        with patch.object(
            app,
            "npm_package_name_from_project_key",
            return_value="patched-package",
        ):
            rendered = app.render_project_template_text(
                "{{PACKAGE_NAME}}",
                "demo",
                "Demo",
                "specs",
                "tests",
            )
        self.assertEqual(rendered, "patched-package")

        with patch.object(
            app,
            "get_current_project",
            return_value={"project_id": "17"},
        ):
            self.assertEqual(app.get_current_project_id(), 17)


if __name__ == "__main__":
    unittest.main()
