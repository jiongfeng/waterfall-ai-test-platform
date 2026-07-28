import unittest
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.web.page_inventory import (
    PageInventoryWebServices,
    create_page_inventory_blueprint,
)


def make_services(**overrides):
    item = {
        "inventory_uid": "inventory-1",
        "page_name": "登录页",
    }
    values = {
        "list_rows": Mock(return_value=[item]),
        "serialize_page_inventory": Mock(
            side_effect=lambda row: dict(row)
        ),
        "upsert_page_inventory": Mock(
            return_value=item
        ),
        "get_page_inventory_by_uid": Mock(
            return_value=item
        ),
        "delete_page_inventory": Mock(
            return_value=True
        ),
        "import_page_inventory_from_doc": Mock(
            return_value=[item]
        ),
    }
    values.update(overrides)
    return PageInventoryWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_page_inventory_blueprint(services)
    )
    return application


class PageInventoryBlueprintTests(unittest.TestCase):
    def test_crud_and_import_success_contracts(self):
        services = make_services()
        with make_app(services).test_client() as client:
            listed = client.get("/api/page-inventory")
            created = client.post(
                "/api/page-inventory",
                json={
                    "page_name": "登录页",
                    "url": "/login",
                },
            )
            updated = client.put(
                "/api/page-inventory/inventory-1",
                json={"notes": "只更新说明"},
            )
            deleted = client.delete(
                "/api/page-inventory/inventory-1"
            )
            imported = client.post(
                "/api/page-inventory/import-from-doc",
                json={"content": "table"},
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            listed.json,
            {
                "items": [
                    {
                        "inventory_uid": "inventory-1",
                        "page_name": "登录页",
                    }
                ],
                "error": None,
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            deleted.json,
            {"ok": True, "error": None},
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json["count"], 1)
        self.assertIsNone(imported.json["error"])
        services.upsert_page_inventory.assert_any_call(
            {
                "page_name": "登录页",
                "url": "/login",
            }
        )
        services.upsert_page_inventory.assert_any_call(
            {"notes": "只更新说明"},
            inventory_uid="inventory-1",
        )

    def test_not_found_and_validation_statuses(self):
        services = make_services(
            get_page_inventory_by_uid=Mock(
                return_value=None
            ),
            delete_page_inventory=Mock(
                return_value=False
            ),
        )
        with make_app(services).test_client() as client:
            missing_update = client.put(
                "/api/page-inventory/missing",
                json={"page_name": "不存在"},
            )
            missing_delete = client.delete(
                "/api/page-inventory/missing"
            )

        self.assertEqual(missing_update.status_code, 404)
        self.assertEqual(
            missing_update.json["error"],
            "页面 inventory 不存在。",
        )
        self.assertEqual(missing_delete.status_code, 404)

        invalid_services = make_services(
            upsert_page_inventory=Mock(
                side_effect=ValueError(
                    "页面名称不能为空。"
                )
            ),
            delete_page_inventory=Mock(
                side_effect=ValueError(
                    "Invalid inventory_uid."
                )
            ),
        )
        with make_app(invalid_services).test_client() as client:
            invalid_create = client.post(
                "/api/page-inventory",
                json={},
            )
            invalid_delete = client.delete(
                "/api/page-inventory/bad"
            )

        self.assertEqual(invalid_create.status_code, 400)
        self.assertEqual(invalid_delete.status_code, 400)

    def test_import_file_not_found_and_operation_errors(self):
        missing_file_services = make_services(
            import_page_inventory_from_doc=Mock(
                side_effect=FileNotFoundError(
                    "inventory.md"
                )
            )
        )
        with make_app(
            missing_file_services
        ).test_client() as client:
            missing_file = client.post(
                "/api/page-inventory/import-from-doc",
                json={"path": "inventory.md"},
            )

        self.assertEqual(missing_file.status_code, 404)
        self.assertEqual(
            missing_file.json["error"],
            "inventory.md",
        )

        failed_services = make_services(
            list_rows=Mock(
                side_effect=RuntimeError(
                    "database offline"
                )
            ),
            upsert_page_inventory=Mock(
                side_effect=RuntimeError("write failed")
            ),
            delete_page_inventory=Mock(
                side_effect=RuntimeError("delete failed")
            ),
            import_page_inventory_from_doc=Mock(
                side_effect=RuntimeError("parse failed")
            ),
        )
        with make_app(failed_services).test_client() as client:
            failed_list = client.get(
                "/api/page-inventory"
            )
            failed_create = client.post(
                "/api/page-inventory",
                json={"page_name": "登录页"},
            )
            failed_delete = client.delete(
                "/api/page-inventory/inventory-1"
            )
            failed_import = client.post(
                "/api/page-inventory/import-from-doc",
                json={},
            )

        self.assertEqual(failed_list.status_code, 500)
        self.assertEqual(failed_list.json["items"], [])
        self.assertIn(
            "读取页面 inventory 失败",
            failed_list.json["error"],
        )
        self.assertEqual(failed_create.status_code, 500)
        self.assertIn(
            "保存页面 inventory 失败",
            failed_create.json["error"],
        )
        self.assertEqual(failed_delete.status_code, 500)
        self.assertIn(
            "删除页面 inventory 失败",
            failed_delete.json["error"],
        )
        self.assertEqual(failed_import.status_code, 500)
        self.assertIn(
            "导入页面 inventory 失败",
            failed_import.json["error"],
        )

    def test_blueprint_registers_exactly_five_routes(self):
        application = make_app(make_services())
        routes = {
            (method, rule.rule)
            for rule in application.url_map.iter_rules()
            if rule.endpoint != "static"
            for method in rule.methods
            if method in {"GET", "POST", "PUT", "DELETE"}
        }

        self.assertEqual(
            routes,
            {
                ("GET", "/api/page-inventory"),
                ("POST", "/api/page-inventory"),
                (
                    "PUT",
                    (
                        "/api/page-inventory/"
                        "<inventory_uid>"
                    ),
                ),
                (
                    "DELETE",
                    (
                        "/api/page-inventory/"
                        "<inventory_uid>"
                    ),
                ),
                (
                    "POST",
                    (
                        "/api/page-inventory/"
                        "import-from-doc"
                    ),
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
