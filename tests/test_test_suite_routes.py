import unittest
from unittest.mock import Mock, patch

from flask import Flask

import app
from test_plan_viewer.web.test_suites import (
    TestSuiteWebServices,
    create_test_suites_blueprint,
)


def make_services(**overrides):
    values = {
        "list_test_suites": Mock(return_value=[]),
        "create_test_suite": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
        "get_test_suite": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
        "update_test_suite": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
        "delete_test_suite": Mock(return_value=True),
        "add_test_suite_items": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
        "delete_test_suite_item": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
        "reorder_test_suite_items": Mock(
            return_value={"suite_uid": "suite-1"}
        ),
    }
    values.update(overrides)
    return TestSuiteWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_test_suites_blueprint(services)
    )
    return application


class TestSuiteBlueprintTests(unittest.TestCase):
    def test_crud_success_contracts_are_preserved(self):
        services = make_services()
        with make_app(services).test_client() as client:
            listed = client.get("/api/test-suites")
            created = client.post(
                "/api/test-suites",
                json={"name": "冒烟", "description": "说明"},
            )
            fetched = client.get("/api/test-suites/suite-1")
            updated = client.put(
                "/api/test-suites/suite-1",
                json={"description": ""},
            )
            deleted = client.delete("/api/test-suites/suite-1")
            added = client.post(
                "/api/test-suites/suite-1/items",
                json={"items": [{"filename": "脚本.spec.ts"}]},
            )
            removed = client.delete(
                "/api/test-suites/suite-1/items/8"
            )
            reordered = client.put(
                "/api/test-suites/suite-1/items/reorder",
                json={"item_ids": [8]},
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(added.status_code, 200)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(reordered.status_code, 200)
        services.create_test_suite.assert_called_once_with(
            "冒烟",
            "说明",
        )
        services.update_test_suite.assert_called_once_with(
            "suite-1",
            name=None,
            description="",
        )
        services.delete_test_suite_item.assert_called_once_with(
            "suite-1",
            8,
        )

    def test_not_found_and_validation_statuses_are_preserved(self):
        duplicate = Mock(
            side_effect=ValueError("测试集名字不能重复。")
        )
        missing_script = Mock(
            side_effect=FileNotFoundError(
                "Script file not found: missing"
            )
        )
        services = make_services(
            create_test_suite=duplicate,
            get_test_suite=Mock(return_value=None),
            delete_test_suite=Mock(return_value=False),
            add_test_suite_items=missing_script,
            reorder_test_suite_items=Mock(
                side_effect=ValueError(
                    "item_ids must be a non-empty list."
                )
            ),
        )

        with make_app(services).test_client() as client:
            conflict = client.post(
                "/api/test-suites",
                json={"name": "重复"},
            )
            missing = client.get("/api/test-suites/missing")
            missing_delete = client.delete(
                "/api/test-suites/missing"
            )
            missing_file = client.post(
                "/api/test-suites/suite-1/items",
                json={"items": [{}]},
            )
            invalid_order = client.put(
                "/api/test-suites/suite-1/items/reorder",
                json={"item_ids": []},
            )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing_delete.status_code, 404)
        self.assertEqual(missing_file.status_code, 404)
        self.assertEqual(invalid_order.status_code, 400)

    def test_unexpected_failures_keep_operation_specific_messages(self):
        services = make_services(
            list_test_suites=Mock(
                side_effect=RuntimeError("database offline")
            ),
            delete_test_suite_item=Mock(
                side_effect=RuntimeError("write failed")
            ),
        )

        with make_app(services).test_client() as client:
            listed = client.get("/api/test-suites")
            removed = client.delete(
                "/api/test-suites/suite-1/items/1"
            )

        self.assertEqual(listed.status_code, 500)
        self.assertEqual(listed.json["suites"], [])
        self.assertIn("读取测试集失败", listed.json["error"])
        self.assertEqual(removed.status_code, 500)
        self.assertIn(
            "移除测试集脚本失败",
            removed.json["error"],
        )


class AppTestSuiteCompositionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_blueprint_services_resolve_patched_app_wrappers(self):
        suites = [{"suite_uid": "patched-suite"}]
        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "list_test_suites_from_mysql",
                return_value=suites,
            ) as list_suites,
            patch.object(
                app,
                "create_test_suite_in_mysql",
                return_value=suites[0],
            ) as create_suite,
        ):
            listed = self.client.get("/api/test-suites")
            created = self.client.post(
                "/api/test-suites",
                json={"name": "Patched"},
            )

        self.assertEqual(listed.json["suites"], suites)
        self.assertEqual(created.json["suite"], suites[0])
        list_suites.assert_called_once_with()
        create_suite.assert_called_once_with("Patched", "")

    def test_repository_wrapper_resolves_patched_collaborators(self):
        expected = {"suite_uid": "patched-suite"}
        with (
            patch.object(
                app,
                "get_test_suite_tables",
                return_value=(
                    {"enabled": True},
                    "`suites`",
                    "`items`",
                ),
            ),
            patch.object(
                app,
                "get_current_project_id",
                return_value=4,
            ),
            patch.object(
                app,
                "sanitize_suite_uid",
                return_value="patched-suite",
            ),
            patch.object(
                app,
                "validate_suite_name",
                return_value="Patched",
            ) as validate_name,
            patch.object(
                app,
                "validate_suite_description",
                return_value="",
            ),
            patch.object(
                app,
                "current_time_ms",
                return_value=55,
            ),
            patch.object(
                app,
                "current_platform_author",
                return_value="tester",
            ),
            patch.object(
                app,
                "ensure_test_suite_name_available",
            ),
            patch.object(
                app,
                "get_test_suite_payload",
                return_value=expected,
            ),
            patch.object(
                app,
                "platform_mysql_connection",
            ) as connect,
        ):
            cursor = Mock()
            cursor_context = Mock()
            cursor_context.__enter__ = Mock(
                return_value=cursor
            )
            cursor_context.__exit__ = Mock(return_value=False)
            connection = Mock()
            connection.cursor.return_value = cursor_context
            connection_context = Mock()
            connection_context.__enter__ = Mock(
                return_value=connection
            )
            connection_context.__exit__ = Mock(
                return_value=False
            )
            connect.return_value = connection_context

            created = app.create_test_suite_in_mysql(" raw ")

        self.assertEqual(created, expected)
        validate_name.assert_called_once_with(" raw ")
        connection.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
