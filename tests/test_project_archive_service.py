import ast
from contextlib import contextmanager
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from test_plan_viewer.configuration import (
    parse_project_key,
    parse_project_path_segment,
)
from test_plan_viewer.projects.archive import (
    ArchiveValidationDependencies,
)
from test_plan_viewer.projects.archive_service import (
    ProjectArchiveService,
    ProjectArchiveServiceDependencies,
)


def validate_module_name(value):
    value = str(value or "").strip()
    if (
        not value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("invalid module")
    return value


def validate_plan_filename(value):
    value = str(value or "").strip()
    if (
        not value.endswith(".md")
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("invalid plan")
    return value


def validate_script_filename(value):
    value = str(value or "").strip()
    if (
        not value.endswith(".spec.ts")
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("invalid script")
    return value


VALIDATION_DEPENDENCIES = ArchiveValidationDependencies(
    validate_module_name=validate_module_name,
    validate_plan_filename=validate_plan_filename,
    validate_script_filename=validate_script_filename,
    parse_project_key=parse_project_key,
    parse_project_path_segment=parse_project_path_segment,
    validate_suite_name=lambda value: str(value or "").strip(),
    validate_suite_description=(
        lambda value: str(value or "").strip()
    ),
    strip_spec_suffix=(
        lambda value: value.removesuffix(".spec.ts")
    ),
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executions = []
        self.lastrowid = 73

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows=None):
        self.fake_cursor = FakeCursor(rows)
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commit_count += 1


@contextmanager
def no_op_project_context(_project):
    yield


def make_dependencies(root, **overrides):
    root = Path(root)
    default_connection = FakeConnection()
    values = {
        "validation_dependencies": VALIDATION_DEPENDENCIES,
        "get_current_project": lambda: {
            "project_key": "demo",
            "name": "演示项目",
            "description": "归档测试",
            "specs_dir": "specs",
            "tests_dir": "tests",
        },
        "get_current_project_id": lambda: None,
        "get_project_root": lambda: root,
        "get_specs_dir": lambda: root / "specs",
        "get_tests_dir": lambda: root / "tests",
        "get_plan_file": (
            lambda module_name, filename: (
                root / "specs" / module_name / filename
            )
        ),
        "get_script_file": (
            lambda module_name, filename: (
                root / "tests" / module_name / filename
            )
        ),
        "get_project_relative_path": (
            lambda path: Path(path).resolve(
                strict=False
            ).relative_to(root.resolve(strict=False))
        ),
        "project_relative_path": (
            lambda path: Path(path).resolve(
                strict=False
            ).relative_to(
                root.resolve(strict=False)
            ).as_posix()
        ),
        "get_platform_database_config": (
            lambda: {"enabled": False}
        ),
        "ensure_platform_database_schema": Mock(),
        "get_test_assets_table": lambda _config: "`assets`",
        "get_test_asset_revisions_table": (
            lambda _config: "`asset_revisions`"
        ),
        "get_platform_projects_table": (
            lambda _config: "`projects`"
        ),
        "platform_table_sql": (
            lambda _config, table_name: f"`{table_name}`"
        ),
        "platform_mysql_connection": (
            lambda _config: default_connection
        ),
        "list_test_suites": lambda: [],
        "strip_spec_suffix": (
            lambda value: value.removesuffix(".spec.ts")
        ),
        "current_time_ms": lambda: 1700000000000,
        "current_platform_author": lambda: "tester",
        "get_test_suite_tables": (
            lambda: (
                {"enabled": True},
                "`test_suites`",
                "`test_suite_items`",
            )
        ),
        "ensure_playwright_asset_git_repo": Mock(),
        "run_git_command": (
            lambda _args, check=True: SimpleNamespace(
                returncode=0
            )
        ),
        "sync_plan_asset": (
            lambda _module, _path, **_kwargs: {
                "asset_id": 11
            }
        ),
        "sync_script_asset": (
            lambda _module, _path, **_kwargs: {
                "asset_id": 12
            }
        ),
        "create_project": (
            lambda payload: {
                **payload,
                "project_id": 7,
                "playwright_project_root": str(root),
            }
        ),
        "use_project_context": no_op_project_context,
        "remove_tree": shutil.rmtree,
    }
    values.update(overrides)
    return ProjectArchiveServiceDependencies(**values)


def make_manifest():
    return {
        "format_version": 1,
        "project": {
            "project_key": "demo",
            "name": "演示项目",
            "description": "来源描述",
            "specs_dir": "specs",
            "tests_dir": "tests",
        },
        "plans": [
            {
                "module_name": "登录",
                "filename": "登录.md",
                "path": "specs/登录/登录.md",
            }
        ],
        "scripts": [
            {
                "module_name": "登录",
                "filename": "登录.spec.ts",
                "display_name": "登录检查",
                "path": "tests/登录/登录.spec.ts",
                "from_plan": {
                    "module_name": "登录",
                    "filename": "登录.md",
                },
            }
        ],
        "test_suites": [
            {
                "suite_uid": "suite-1",
                "name": "冒烟",
                "description": "核心链路",
                "items": [
                    {
                        "module_name": "登录",
                        "filename": "登录.spec.ts",
                        "display_name": "登录检查",
                    }
                ],
            }
        ],
    }


def make_archive(manifest=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                manifest or make_manifest(),
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        bundle.writestr(
            "specs/登录/登录.md",
            "# 登录计划\n",
        )
        bundle.writestr(
            "tests/登录/登录.spec.ts",
            "test('登录', async () => {});\n",
        )
    return buffer.getvalue()


class ProjectArchiveExportServiceTests(unittest.TestCase):
    def test_build_zip_preserves_asset_links_suites_and_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_file = root / "specs" / "登录" / "登录.md"
            script_file = (
                root
                / "tests"
                / "登录"
                / "登录.spec.ts"
            )
            plan_file.parent.mkdir(parents=True)
            script_file.parent.mkdir(parents=True)
            plan_file.write_text("# 登录计划", encoding="utf-8")
            script_file.write_text(
                "test('登录', async () => {});",
                encoding="utf-8",
            )

            rows = [
                {
                    "asset_id": 11,
                    "asset_type": "plan",
                    "current_path": str(plan_file),
                },
                {
                    "asset_id": 12,
                    "asset_type": "script",
                    "current_path": str(script_file),
                    "from_plan_asset_id": 11,
                },
            ]
            connection = FakeConnection(rows)
            service = ProjectArchiveService(
                make_dependencies(
                    root,
                    get_current_project_id=lambda: 7,
                    get_platform_database_config=(
                        lambda: {"enabled": True}
                    ),
                    platform_mysql_connection=(
                        lambda _config: connection
                    ),
                    list_test_suites=lambda: [
                        {
                            "suite_uid": "suite-1",
                            "name": "冒烟",
                            "description": "核心链路",
                            "items": [
                                {
                                    "module_name": "登录",
                                    "filename": (
                                        "登录.spec.ts"
                                    ),
                                    "display_name": (
                                        "登录检查"
                                    ),
                                    "sort_order": 1,
                                }
                            ],
                        }
                    ],
                )
            )

            buffer, manifest = (
                service.build_project_export_zip()
            )

            self.assertEqual(
                manifest["scripts"][0]["from_plan"],
                {
                    "module_name": "登录",
                    "filename": "登录.md",
                },
            )
            self.assertEqual(
                manifest["test_suites"][0]["items"][0][
                    "display_name"
                ],
                "登录检查",
            )
            self.assertEqual(
                manifest["exported_at"],
                1700000000000,
            )
            self.assertEqual(
                manifest["modules"],
                [
                    {
                        "name": "登录",
                        "has_plans": True,
                        "has_scripts": True,
                    }
                ],
            )
            with zipfile.ZipFile(buffer) as bundle:
                self.assertEqual(
                    set(bundle.namelist()),
                    {
                        "manifest.json",
                        "specs/登录/登录.md",
                        "tests/登录/登录.spec.ts",
                    },
                )
                archived_manifest = json.loads(
                    bundle.read("manifest.json")
                )
                self.assertEqual(
                    archived_manifest,
                    manifest,
                )

    def test_collect_rejects_non_directory_asset_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid_root = root / "specs"
            invalid_root.write_text("not a directory")
            service = ProjectArchiveService(
                make_dependencies(root)
            )

            with self.assertRaisesRegex(
                ValueError,
                "项目资产目录不是目录",
            ):
                service.collect_project_export_files(
                    invalid_root,
                    ".md",
                    "specs",
                )


class ProjectArchiveImportServiceTests(unittest.TestCase):
    def test_import_restores_files_links_suites_and_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connection = FakeConnection()
            create_project = Mock(
                return_value={
                    "project_id": 7,
                    "project_key": "restored",
                    "name": "恢复项目",
                    "description": "来源描述",
                    "specs_dir": "plans",
                    "tests_dir": "e2e",
                    "playwright_project_root": str(root),
                }
            )
            sync_plan = Mock(return_value={"asset_id": 11})
            sync_script = Mock(
                return_value={"asset_id": 12}
            )
            git_calls = []

            def run_git_command(args, check=True):
                git_calls.append((args, check))
                return SimpleNamespace(returncode=0)

            service = ProjectArchiveService(
                make_dependencies(
                    root,
                    create_project=create_project,
                    get_current_project_id=lambda: 7,
                    platform_mysql_connection=(
                        lambda _config: connection
                    ),
                    sync_plan_asset=sync_plan,
                    sync_script_asset=sync_script,
                    run_git_command=run_git_command,
                )
            )

            result = service.import_project_archive(
                make_archive(),
                {
                    "project_key": "restored",
                    "name": "恢复项目",
                },
            )

            create_project.assert_called_once_with(
                {
                    "project_key": "restored",
                    "name": "恢复项目",
                    "description": "来源描述",
                    "specs_dir": "specs",
                    "tests_dir": "tests",
                }
            )
            self.assertEqual(
                (
                    root / "specs" / "登录" / "登录.md"
                ).read_text(encoding="utf-8"),
                "# 登录计划\n",
            )
            self.assertIn(
                "test('登录'",
                (
                    root
                    / "tests"
                    / "登录"
                    / "登录.spec.ts"
                ).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                sync_script.call_args.kwargs[
                    "from_plan_asset_id"
                ],
                11,
            )
            self.assertEqual(
                result["counts"],
                {
                    "modules": 1,
                    "plans": 1,
                    "scripts": 1,
                    "test_suites": 1,
                    "suite_items": 1,
                },
            )
            self.assertEqual(connection.commit_count, 1)
            self.assertEqual(
                len(connection.fake_cursor.executions),
                2,
            )
            self.assertIn(
                (
                    ["diff", "--cached", "--quiet"],
                    False,
                ),
                git_calls,
            )

    def test_failed_import_cleans_project_after_context_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = {
                "project_id": 7,
                "project_key": "demo",
                "playwright_project_root": str(root),
            }
            context_events = []

            @contextmanager
            def project_context(bound_project):
                context_events.append(
                    ("enter", bound_project["project_id"])
                )
                try:
                    yield
                finally:
                    context_events.append(
                        ("exit", bound_project["project_id"])
                    )

            service = ProjectArchiveService(
                make_dependencies(
                    root,
                    create_project=Mock(
                        return_value=project
                    ),
                    use_project_context=project_context,
                )
            )

            with (
                patch.object(
                    service,
                    "write_project_import_files",
                    side_effect=RuntimeError("disk full"),
                ),
                patch.object(
                    service,
                    "cleanup_imported_project",
                ) as cleanup,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "disk full",
                ):
                    service.import_project_archive(
                        make_archive()
                    )

            self.assertEqual(
                context_events,
                [("enter", 7), ("exit", 7)],
            )
            cleanup.assert_called_once_with(project)

    def test_clear_directory_rejects_root_and_path_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            service = ProjectArchiveService(
                make_dependencies(root)
            )

            with self.assertRaisesRegex(
                ValueError,
                "不能是项目根目录",
            ):
                service.clear_project_import_asset_directory(
                    root
                )
            with self.assertRaisesRegex(
                ValueError,
                "必须位于项目目录内",
            ):
                service.clear_project_import_asset_directory(
                    root.parent / "outside"
                )

    def test_cleanup_deletes_database_state_then_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "imported"
            root.mkdir()
            (root / "partial.txt").write_text("partial")
            connection = FakeConnection()
            remove_tree = Mock(wraps=shutil.rmtree)
            service = ProjectArchiveService(
                make_dependencies(
                    root,
                    get_platform_database_config=(
                        lambda: {"enabled": True}
                    ),
                    platform_mysql_connection=(
                        lambda _config: connection
                    ),
                    remove_tree=remove_tree,
                )
            )

            service.cleanup_imported_project(
                {
                    "project_id": 7,
                    "playwright_project_root": str(root),
                }
            )

            self.assertEqual(connection.commit_count, 1)
            sql = "\n".join(
                statement
                for statement, _params
                in connection.fake_cursor.executions
            )
            self.assertIn("`test_suites`", sql)
            self.assertIn("`asset_revisions`", sql)
            self.assertIn("`projects`", sql)
            remove_tree.assert_called_once_with(
                root,
                ignore_errors=True,
            )
            self.assertFalse(root.exists())


class ProjectArchiveBoundaryTests(unittest.TestCase):
    def test_domain_module_does_not_import_flask_or_app(self):
        module_path = (
            Path(__file__).parents[1]
            / "test_plan_viewer"
            / "projects"
            / "archive_service.py"
        )
        tree = ast.parse(
            module_path.read_text(encoding="utf-8")
        )
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imported_roots.add(
                    (node.module or "").split(".", 1)[0]
                )

        self.assertNotIn("flask", imported_roots)
        self.assertNotIn("app", imported_roots)


if __name__ == "__main__":
    unittest.main()
