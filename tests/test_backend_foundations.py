import unittest
from pathlib import Path
from unittest.mock import patch

from test_plan_viewer.core.validation import (
    normalize_confidence,
    normalize_json_object_or_array,
    normalize_string_list,
    validate_uid,
)
from test_plan_viewer.infrastructure import mysql
from test_plan_viewer.projects import context as project_context


class CoreValidationTests(unittest.TestCase):
    def test_normalizes_lists_from_text_and_objects(self):
        self.assertEqual(
            normalize_string_list("登录，查询\n导出"),
            ["登录", "查询", "导出"],
        )
        self.assertEqual(
            normalize_string_list([{"name": "登录"}, {"title": "查询"}, "导出"]),
            ["登录", "查询", "导出"],
        )

    def test_json_normalization_and_confidence_are_bounded(self):
        self.assertEqual(
            normalize_json_object_or_array('{"enabled": true}', {}),
            {"enabled": True},
        )
        self.assertEqual(
            normalize_json_object_or_array("plain note", {}),
            {"notes": "plain note"},
        )
        self.assertEqual(normalize_confidence(-1), 0.0)
        self.assertEqual(normalize_confidence(3), 1.0)
        self.assertIsNone(normalize_confidence("unknown"))

    def test_uid_validation_rejects_path_like_values(self):
        self.assertEqual(validate_uid("agent-run_1.2"), "agent-run_1.2")
        with self.assertRaisesRegex(ValueError, "Invalid run_id"):
            validate_uid("../run", "run_id")


class MysqlFoundationTests(unittest.TestCase):
    def test_table_names_apply_the_configured_prefix_and_are_quoted(self):
        config = {"table_prefix": "platform_"}

        self.assertEqual(
            mysql.platform_table_name(config, "jobs"),
            "platform_jobs",
        )
        self.assertEqual(
            mysql.platform_table_sql(config, "jobs"),
            "`platform_jobs`",
        )

    def test_invalid_identifiers_are_rejected(self):
        for identifier in ("", "jobs; DROP TABLE jobs", "schema.jobs", "jobs`"):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(
                RuntimeError,
                "Invalid MySQL identifier",
            ):
                mysql.quote_mysql_identifier(identifier)

    def test_connection_requires_enabled_configuration_before_import(self):
        with patch.object(mysql, "pymysql", None):
            with self.assertRaisesRegex(RuntimeError, "未启用"):
                with mysql.platform_mysql_connection({"enabled": False}):
                    pass

            with self.assertRaisesRegex(RuntimeError, "缺少 PyMySQL"):
                with mysql.platform_mysql_connection({"enabled": True}):
                    pass


class ProjectContextTests(unittest.TestCase):
    def tearDown(self):
        project_context.PROJECT_CONTEXT.project = None
        project_context.AUTHOR_CONTEXT.author = None

    def test_nested_contexts_restore_the_previous_values(self):
        outer_project = {"playwright_project_root": "/outer"}
        inner_project = {"playwright_project_root": "/inner"}

        with (
            project_context.use_project_context(outer_project),
            project_context.use_author_context("outer-author"),
        ):
            self.assertIs(project_context.current_context_project(), outer_project)
            self.assertEqual(project_context.current_author(), "outer-author")
            with (
                project_context.use_project_context(inner_project),
                project_context.use_author_context("inner-author"),
            ):
                self.assertIs(
                    project_context.current_context_project(),
                    inner_project,
                )
                self.assertEqual(project_context.current_author(), "inner-author")

            self.assertIs(project_context.current_context_project(), outer_project)
            self.assertEqual(project_context.current_author(), "outer-author")

        self.assertIsNone(project_context.current_context_project())
        self.assertEqual(project_context.current_author(), "platform")

    def test_project_directory_helpers_use_resolved_project_data(self):
        project = {
            "playwright_project_root": "/workspace/demo",
            "specs_dir": "plans",
            "tests_dir": "checks",
        }

        self.assertEqual(
            project_context.project_specs_dir(project),
            Path("/workspace/demo/plans"),
        )
        self.assertEqual(
            project_context.project_tests_dir(project),
            Path("/workspace/demo/checks"),
        )
        self.assertEqual(
            project_context.project_relative_path(
                project,
                "/workspace/demo/checks/login.spec.ts",
            ),
            Path("checks/login.spec.ts"),
        )

    def test_relative_path_rejects_files_outside_the_project(self):
        with self.assertRaisesRegex(ValueError, "outside project root"):
            project_context.path_relative_to_root("/workspace/demo", "/tmp/file")


if __name__ == "__main__":
    unittest.main()
