import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock

from test_plan_viewer.test_suites import model
from test_plan_viewer.test_suites import repository
from test_plan_viewer.test_suites import service


class FakeCursor:
    def __init__(self, *, fetchones=(), fetchalls=()):
        self.fetchones = deque(fetchones)
        self.fetchalls = deque(fetchalls)
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parameters=()):
        self.executions.append((sql, parameters))

    def fetchone(self):
        return self.fetchones.popleft() if self.fetchones else None

    def fetchall(self):
        return self.fetchalls.popleft() if self.fetchalls else []


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_count += 1


def make_repository_dependencies(connection=None, **overrides):
    connection = connection or FakeConnection(FakeCursor())
    values = {
        "get_platform_database_config": lambda: {
            "enabled": True,
        },
        "ensure_platform_database_schema": Mock(),
        "get_test_suites_table": lambda _config: "`test_suites`",
        "get_test_suite_items_table": (
            lambda _config: "`test_suite_items`"
        ),
        "get_test_suite_tables": lambda: (
            {"enabled": True},
            "`test_suites`",
            "`test_suite_items`",
        ),
        "get_current_project_id": lambda: 7,
        "platform_mysql_connection": lambda _config: connection,
        "validate_suite_name": model.validate_suite_name,
        "validate_suite_description": (
            model.validate_suite_description
        ),
        "serialize_test_suite_item": (
            lambda row: model.serialize_test_suite_item(
                row,
                strip_spec_suffix=lambda filename: filename.removesuffix(
                    ".spec.ts"
                ),
            )
        ),
        "serialize_test_suite": model.serialize_test_suite,
        "list_test_suite_items_by_suite_ids": Mock(
            return_value={}
        ),
        "get_test_suite_row_by_uid": (
            repository.TestSuiteRepository.get_row_by_uid
        ),
        "ensure_test_suite_name_available": (
            repository.TestSuiteRepository.ensure_name_available
        ),
        "get_test_suite_payload": Mock(
            return_value={"suite_uid": "suite-fixed"}
        ),
        "sanitize_suite_uid": lambda: "suite-fixed",
        "current_time_ms": lambda: 1234,
        "current_platform_author": lambda: "tester",
        "normalize_suite_item_input": Mock(),
        "sync_script_asset": Mock(),
    }
    values.update(overrides)
    return repository.TestSuiteRepositoryDependencies(**values)


class TestSuiteModelTests(unittest.TestCase):
    def test_validation_normalizes_values_and_enforces_limits(self):
        self.assertEqual(
            model.validate_suite_name(" 回归测试 "),
            "回归测试",
        )
        self.assertEqual(
            model.validate_suite_description(" 说明 "),
            "说明",
        )
        with self.assertRaisesRegex(ValueError, "不能为空"):
            model.validate_suite_name(" ")
        with self.assertRaisesRegex(ValueError, "255"):
            model.validate_suite_name("x" * 256)
        with self.assertRaisesRegex(ValueError, "1024"):
            model.validate_suite_description("x" * 1025)

    def test_serialization_preserves_the_browser_contract(self):
        item = model.serialize_test_suite_item(
            {
                "item_id": 3,
                "module_name": "登录",
                "filename": "登录成功.spec.ts",
                "script_path": "/tests/登录/登录成功.spec.ts",
                "sort_order": "2",
            },
            strip_spec_suffix=lambda filename: filename.removesuffix(
                ".spec.ts"
            ),
        )
        suite = model.serialize_test_suite(
            {
                "suite_id": 9,
                "suite_uid": "suite-9",
                "name": "冒烟",
            },
            [item],
        )

        self.assertEqual(item["id"], 3)
        self.assertEqual(item["display_name"], "登录成功")
        self.assertEqual(item["path"], item["script_path"])
        self.assertEqual(suite["id"], "suite-9")
        self.assertEqual(suite["status"], "active")
        self.assertEqual(suite["items"], [item])


class TestSuiteServiceTests(unittest.TestCase):
    def test_item_normalization_uses_supplied_project_services(self):
        script_file = Mock()
        script_file.exists.return_value = True
        dependencies = service.TestSuiteItemDependencies(
            validate_module_name=lambda value: f"module:{value}",
            validate_script_filename=lambda value: f"file:{value}",
            get_script_file=lambda module_name, filename: (
                script_file
                if (
                    module_name,
                    filename,
                )
                == ("module:登录", "file:成功.spec.ts")
                else None
            ),
            strip_spec_suffix=lambda _filename: "默认标题",
        )

        normalized = service.normalize_suite_item_input(
            {
                "module_name": " 登录 ",
                "filename": " 成功.spec.ts ",
                "display_name": " ",
            },
            dependencies,
        )

        self.assertEqual(normalized["module_name"], "module:登录")
        self.assertEqual(
            normalized["filename"],
            "file:成功.spec.ts",
        )
        self.assertEqual(normalized["display_name"], "默认标题")
        self.assertIs(normalized["script_file"], script_file)

    def test_item_normalization_rejects_missing_script(self):
        missing_file = Path("/missing/script.spec.ts")
        dependencies = service.TestSuiteItemDependencies(
            validate_module_name=lambda value: value,
            validate_script_filename=lambda value: value,
            get_script_file=lambda _module, _filename: missing_file,
            strip_spec_suffix=lambda filename: filename,
        )

        with self.assertRaisesRegex(
            FileNotFoundError,
            "Script file not found",
        ):
            service.normalize_suite_item_input(
                {
                    "module_name": "登录",
                    "filename": "脚本.spec.ts",
                },
                dependencies,
            )


class TestSuiteRepositoryTests(unittest.TestCase):
    def test_disabled_persistence_fails_before_schema_bootstrap(self):
        ensure_schema = Mock()
        dependencies = make_repository_dependencies(
            get_platform_database_config=lambda: {
                "enabled": False,
            },
            ensure_platform_database_schema=ensure_schema,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "未启用平台 MySQL",
        ):
            repository.TestSuiteRepository(
                dependencies
            ).get_tables()

        ensure_schema.assert_not_called()

    def test_list_groups_serialized_items_by_suite(self):
        rows = [
            {
                "suite_id": 2,
                "suite_uid": "suite-2",
                "name": "完整回归",
            },
            {
                "suite_id": 1,
                "suite_uid": "suite-1",
                "name": "冒烟",
            },
        ]
        cursor = FakeCursor(fetchalls=[rows])
        connection = FakeConnection(cursor)
        list_items = Mock(
            return_value={
                1: [{"id": 11}],
                2: [{"id": 21}],
            }
        )
        dependencies = make_repository_dependencies(
            connection,
            list_test_suite_items_by_suite_ids=list_items,
        )

        suites = repository.TestSuiteRepository(
            dependencies
        ).list()

        self.assertEqual(
            [suite["suite_uid"] for suite in suites],
            ["suite-2", "suite-1"],
        )
        self.assertEqual(suites[0]["items"], [{"id": 21}])
        list_items.assert_called_once_with(
            cursor,
            "`test_suite_items`",
            7,
            [2, 1],
        )
        query, parameters = cursor.executions[0]
        self.assertIn("status = 'active'", query)
        self.assertEqual(parameters, (7,))

    def test_create_uses_validation_adapters_and_returns_payload(self):
        cursor = FakeCursor(fetchones=[None])
        connection = FakeConnection(cursor)
        validate_name = Mock(
            side_effect=lambda value: str(value).strip()
        )
        validate_description = Mock(
            side_effect=lambda value: str(value).strip()
        )
        get_payload = Mock(
            return_value={
                "suite_uid": "suite-fixed",
                "name": "冒烟",
            }
        )
        dependencies = make_repository_dependencies(
            connection,
            validate_suite_name=validate_name,
            validate_suite_description=validate_description,
            get_test_suite_payload=get_payload,
        )

        suite = repository.TestSuiteRepository(
            dependencies
        ).create(" 冒烟 ", " 说明 ")

        self.assertEqual(suite["suite_uid"], "suite-fixed")
        self.assertEqual(connection.commit_count, 1)
        validate_name.assert_called_once_with(" 冒烟 ")
        validate_description.assert_called_once_with(" 说明 ")
        get_payload.assert_called_once_with("suite-fixed")
        insert = next(
            execution
            for execution in cursor.executions
            if "INSERT INTO" in execution[0]
        )
        self.assertEqual(
            insert[1],
            (
                7,
                "suite-fixed",
                "冒烟",
                "说明",
                "tester",
                "tester",
                1234,
                1234,
            ),
        )

    def test_reorder_deduplicates_ids_and_rejects_foreign_items(self):
        cursor = FakeCursor(
            fetchalls=[[{"item_id": 1}]],
        )
        connection = FakeConnection(cursor)
        dependencies = make_repository_dependencies(
            connection,
            get_test_suite_row_by_uid=Mock(
                return_value={"suite_id": 9}
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "outside the current suite",
        ):
            repository.TestSuiteRepository(
                dependencies
            ).reorder_items(
                "suite-9",
                [1, 2, 1],
            )

        self.assertEqual(connection.commit_count, 0)


if __name__ == "__main__":
    unittest.main()
