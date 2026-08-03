import hashlib
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock

import app
from test_plan_viewer.page_inventory import model
from test_plan_viewer.page_inventory import repository
from test_plan_viewer.page_inventory import service


def make_model_dependencies(**overrides):
    values = {
        "load_json_column": app.load_json_column,
        "normalize_confidence": app.normalize_confidence,
        "normalize_string_list": app.normalize_string_list,
        "normalize_json_object_or_array": (
            app.normalize_json_object_or_array
        ),
    }
    values.update(overrides)
    return model.PageInventoryModelDependencies(**values)


class FakeCursor:
    def __init__(
        self,
        *,
        fetchones=(),
        fetchalls=(),
        rowcounts=(),
    ):
        self.fetchones = deque(fetchones)
        self.fetchalls = deque(fetchalls)
        self.rowcounts = deque(rowcounts)
        self.executions = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parameters=()):
        self.executions.append((sql, parameters))
        if self.rowcounts:
            self.rowcount = self.rowcounts.popleft()

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
        "require_platform_database": lambda: {
            "enabled": True,
        },
        "get_page_inventory_table": (
            lambda _config: "`page_inventory`"
        ),
        "get_current_project_id": lambda: 7,
        "platform_mysql_connection": (
            lambda _config: connection
        ),
        "validate_uid": app.validate_uid,
        "compact_json_dumps": (
            lambda value: json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        "current_time_ms": lambda: 1234,
        "new_inventory_uid": lambda: "inventory-new",
        "get_page_inventory_by_uid": Mock(
            return_value={
                "inventory_uid": "inventory-new",
            }
        ),
    }
    values.update(overrides)
    return repository.PageInventoryRepositoryDependencies(
        **values
    )


def normalized_item(**overrides):
    item = {
        "page_name": "登录页",
        "url": "/login",
        "menu_path": ["系统", "登录"],
        "roles": ["管理员"],
        "accounts": [
            {
                "username": "admin",
                "password_ref": "ADMIN_PASSWORD",
                "purpose": "登录",
            }
        ],
        "stable_selectors": ["#username"],
        "actions": ["登录"],
        "read_only_actions": [],
        "write_actions": ["登录"],
        "sample_data": {"username": "admin"},
        "write_risk": True,
        "baseline_required": True,
        "notes": "保存会写库",
        "source": "manual",
        "confidence": 0.9,
        "snapshot_hash": "abc",
        "last_scanned_at": 99,
    }
    item.update(overrides)
    return item


class PageInventoryModelParityTests(unittest.TestCase):
    def test_serialization_matches_legacy_contract(self):
        row = {
            "id": 3,
            "inventory_uid": "inventory-3",
            "page_name": "登录页",
            "url": "/login",
            "menu_path_json": '["系统","登录"]',
            "roles_json": '["管理员"]',
            "accounts_json": (
                '[{"username":"admin","password_ref":"ADMIN"}]'
            ),
            "stable_selectors_json": '["#username"]',
            "actions_json": '["登录"]',
            "read_only_actions_json": "[]",
            "write_actions_json": '["登录"]',
            "sample_data_json": '{"username":"admin"}',
            "write_risk": 1,
            "baseline_required": 1,
            "notes": "说明",
            "source": "scanner",
            "confidence": "1.2",
            "snapshot_hash": "abc",
            "last_scanned_at": 11,
            "created_at": 12,
            "updated_at": 13,
        }

        self.assertEqual(
            model.serialize_page_inventory(
                row,
                make_model_dependencies(),
            ),
            app.serialize_page_inventory(row),
        )

    def test_account_and_payload_normalization_match_legacy(self):
        dependencies = make_model_dependencies()
        account_samples = [
            "admin, viewer",
            [
                {
                    "username": " admin ",
                    "password_ref": " ADMIN_PASSWORD ",
                    "purpose": " login ",
                },
                " `viewer` ",
                {},
            ],
            None,
        ]
        for value in account_samples:
            with self.subTest(value=value):
                self.assertEqual(
                    model.normalize_accounts(
                        value,
                        dependencies,
                    ),
                    app.normalize_accounts(value),
                )

        payload_samples = [
            {
                "page_name": " 登录页 ",
                "url": " /login ",
                "accounts": "admin、viewer",
                "write_risk": True,
                "source": "invalid",
                "confidence": 2,
                "sample_data": '{"user":"admin"}',
            },
            {
                "page_name": "详情页",
                "roles": [{"name": "管理员"}],
                "last_scanned_at": "not-an-int",
            },
        ]
        for payload in payload_samples:
            with self.subTest(page=payload["page_name"]):
                self.assertEqual(
                    model.normalize_page_inventory_payload(
                        payload,
                        dependencies,
                    ),
                    app.normalize_page_inventory_payload(
                        payload
                    ),
                )

    def test_markdown_parser_matches_legacy_table_rules(self):
        markdown_text = """
说明

| 页面 | 路径 | 推荐账号 | 关键控件 / 操作 | 写库风险 |
| --- | --- | --- | --- | --- |
| 登录页 | `/login` | admin、viewer | `#username`、查询、保存 | 保存会写库 |
| 详情页 | `/detail` | viewer | `.panel`、查看、导出 Excel | 只读 |

下一节
"""
        self.assertEqual(
            model.parse_page_inventory_from_markdown(
                markdown_text,
                make_model_dependencies(),
            ),
            app.parse_page_inventory_from_markdown(
                markdown_text
            ),
        )


class PageInventoryRepositoryTests(unittest.TestCase):
    def test_list_and_get_are_scoped_to_current_project(self):
        listed = [{"inventory_uid": "inventory-1"}]
        fetched = {"inventory_uid": "inventory-2"}
        cursor = FakeCursor(
            fetchalls=[listed],
            fetchones=[fetched],
        )
        repo = repository.PageInventoryRepository(
            make_repository_dependencies(
                connection=FakeConnection(cursor)
            )
        )

        self.assertEqual(repo.list_rows(limit=5), listed)
        self.assertEqual(
            repo.get_by_uid("inventory-2"),
            fetched,
        )

        list_sql, list_params = cursor.executions[0]
        get_sql, get_params = cursor.executions[1]
        self.assertIn("WHERE project_id = %s", list_sql)
        self.assertIn("LIMIT %s", list_sql)
        self.assertEqual(list_params, (7, 5))
        self.assertIn("WHERE project_id = %s", get_sql)
        self.assertEqual(
            get_params,
            (7, "inventory-2"),
        )

    def test_upsert_serializes_json_and_returns_project_lookup(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        get_item = Mock(
            return_value={
                "inventory_uid": "inventory-new"
            }
        )
        repo = repository.PageInventoryRepository(
            make_repository_dependencies(
                connection=connection,
                get_page_inventory_by_uid=get_item,
            )
        )

        result = repo.upsert(normalized_item())

        self.assertEqual(
            result["inventory_uid"],
            "inventory-new",
        )
        sql, parameters = cursor.executions[0]
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertEqual(parameters[0], 7)
        self.assertEqual(parameters[1], "inventory-new")
        self.assertEqual(parameters[4], '["系统","登录"]')
        self.assertEqual(parameters[12], 1)
        self.assertEqual(parameters[13], 1)
        self.assertEqual(connection.commit_count, 1)
        get_item.assert_called_once_with("inventory-new")

    def test_delete_uses_project_and_validated_uid(self):
        cursor = FakeCursor(rowcounts=[1])
        connection = FakeConnection(cursor)
        repo = repository.PageInventoryRepository(
            make_repository_dependencies(
                connection=connection
            )
        )

        self.assertTrue(repo.delete("inventory-1"))

        sql, parameters = cursor.executions[0]
        self.assertIn("WHERE project_id = %s", sql)
        self.assertEqual(
            parameters,
            (7, "inventory-1"),
        )
        self.assertEqual(connection.commit_count, 1)


class PageInventoryServiceTests(unittest.TestCase):
    def test_update_normalizes_only_payload_like_legacy(self):
        normalize_payload = Mock(
            side_effect=lambda payload: payload
        )
        upsert_normalized = Mock(
            return_value={
                "inventory_uid": "inventory-1"
            }
        )
        services = service.PageInventoryService(
            service.PageInventoryServiceDependencies(
                list_rows=Mock(),
                get_by_uid=Mock(
                    return_value={
                        "inventory_uid": "inventory-1"
                    }
                ),
                upsert_normalized=upsert_normalized,
                delete_by_uid=Mock(),
                serialize_page_inventory=Mock(
                    return_value={
                        "page_name": "旧名称",
                        "url": "/kept",
                        "roles": ["管理员"],
                        "write_risk": True,
                    }
                ),
                normalize_page_inventory_payload=(
                    normalize_payload
                ),
                parse_page_inventory_from_markdown=Mock(),
                app_dir=Path("/application"),
            )
        )

        services.upsert(
            {"page_name": "新名称"},
            inventory_uid="inventory-1",
        )

        normalized_input = normalize_payload.call_args.args[0]
        self.assertEqual(
            normalized_input,
            {"page_name": "新名称"},
        )
        services.dependencies.get_by_uid.assert_not_called()
        services.dependencies.serialize_page_inventory.assert_not_called()
        upsert_normalized.assert_called_once_with(
            normalized_input,
            inventory_uid="inventory-1",
        )

    def test_import_uses_stable_uid_and_serialized_result(self):
        row = {
            "page_name": "登录页",
            "url": "/login",
        }
        upsert_normalized = Mock(
            side_effect=lambda item, **_kwargs: item
        )
        services = service.PageInventoryService(
            service.PageInventoryServiceDependencies(
                list_rows=Mock(),
                get_by_uid=Mock(return_value=None),
                upsert_normalized=upsert_normalized,
                delete_by_uid=Mock(),
                serialize_page_inventory=Mock(
                    side_effect=lambda item: dict(item)
                ),
                normalize_page_inventory_payload=Mock(
                    side_effect=lambda item: dict(item)
                ),
                parse_page_inventory_from_markdown=Mock(
                    return_value=[row]
                ),
                app_dir=Path("/application"),
            )
        )

        imported = services.import_from_doc(
            {"content": "| ignored |"}
        )

        expected_uid = hashlib.sha256(
            "登录页|/login".encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(imported, [row])
        self.assertEqual(
            upsert_normalized.call_args.kwargs[
                "inventory_uid"
            ],
            expected_uid,
        )

    def test_import_resolves_relative_path_from_app_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            document = app_dir / "inventory.md"
            document.write_text("document body", encoding="utf-8")
            parse_markdown = Mock(return_value=[])
            services = service.PageInventoryService(
                service.PageInventoryServiceDependencies(
                    list_rows=Mock(),
                    get_by_uid=Mock(),
                    upsert_normalized=Mock(),
                    delete_by_uid=Mock(),
                    serialize_page_inventory=Mock(),
                    normalize_page_inventory_payload=Mock(),
                    parse_page_inventory_from_markdown=(
                        parse_markdown
                    ),
                    app_dir=app_dir,
                )
            )

            self.assertEqual(
                services.import_from_doc(
                    {"path": "inventory.md"}
                ),
                [],
            )

        parse_markdown.assert_called_once_with(
            "document body"
        )


if __name__ == "__main__":
    unittest.main()
