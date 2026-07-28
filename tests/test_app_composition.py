import unittest
from unittest.mock import patch

import app


class ApplicationCompositionTests(unittest.TestCase):
    def test_core_routes_are_registered_by_domain_blueprints(self):
        rules = {
            (rule.rule, method): rule.endpoint
            for rule in app.app.url_map.iter_rules()
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }

        self.assertEqual(rules[("/", "GET")], "index.index")
        self.assertEqual(
            rules[("/api/auth/me", "GET")],
            "auth.auth_me",
        )
        self.assertEqual(
            rules[("/api/projects", "GET")],
            "projects.list_projects",
        )
        self.assertEqual(
            rules[("/api/projects/export", "GET")],
            "project_archive.export_project",
        )
        self.assertEqual(
            rules[("/api/projects/import", "POST")],
            "project_archive.import_project",
        )
        self.assertEqual(
            rules[("/api/setup-scripts", "GET")],
            "setup.list_setup_scripts",
        )
        self.assertEqual(
            rules[("/api/page-inventory", "GET")],
            "page_inventory.list_page_inventory",
        )
        self.assertEqual(
            rules[("/api/page-inventory", "POST")],
            "page_inventory.create_page_inventory",
        )
        self.assertEqual(
            rules[("/api/test-suites", "GET")],
            "test_suites.list_test_suites",
        )
        self.assertEqual(
            rules[("/api/platform-records", "GET")],
            "platform_records.get_platform_records",
        )
        self.assertEqual(
            rules[
                (
                    "/api/platform-records/<bucket>/<path:record_key>",
                    "PUT",
                )
            ],
            "platform_records.save_platform_record",
        )

    def test_platform_record_blueprint_resolves_patched_composition_dependencies(self):
        client = app.app.test_client()
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(
                app,
                "get_platform_database_config",
                return_value={"enabled": False},
            ) as get_database_config,
        ):
            response = client.get("/api/platform-records")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["enabled"])
        get_database_config.assert_called_once_with()

    def test_platform_record_save_blueprint_keeps_app_monkeypatch_compatibility(self):
        client = app.app.test_client()
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "save_platform_record_to_mysql") as save_record,
        ):
            response = client.put(
                "/api/platform-records/view_state/default",
                json={"record": {"activeSection": "plans"}},
            )

        self.assertEqual(response.status_code, 200)
        save_record.assert_called_once_with(
            "view_state",
            "default",
            {"activeSection": "plans"},
        )


if __name__ == "__main__":
    unittest.main()
