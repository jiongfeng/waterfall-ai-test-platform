import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask

from test_plan_viewer.web import create_application, index_blueprint


class WebFoundationTests(unittest.TestCase):
    def test_application_factory_registers_page_blueprints_and_security_defaults(self):
        application = create_application("test-application")

        self.assertIn("index.index", application.view_functions)
        self.assertTrue(application.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(application.config["SESSION_COOKIE_NAME"], "session")
        self.assertEqual(application.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(application.config["SESSION_COOKIE_SECURE"])
        self.assertNotEqual(
            application.secret_key,
            "test-plan-viewer-change-me",
        )
        self.assertEqual(application.template_folder, str(Path(__file__).resolve().parents[1] / "templates"))
        self.assertEqual(application.static_folder, str(Path(__file__).resolve().parents[1] / "static"))

    def test_application_factory_accepts_an_isolated_session_cookie_name(self):
        with patch.dict(
            os.environ,
            {
                "PLATFORM_SESSION_COOKIE_NAME": (
                    "playwright_platform_5001_session"
                )
            },
        ):
            application = create_application("isolated-cookie")

        self.assertEqual(
            application.config["SESSION_COOKIE_NAME"],
            "playwright_platform_5001_session",
        )

    def test_application_factory_can_disable_secure_cookie_for_local_http(self):
        with patch.dict(
            os.environ,
            {"PLATFORM_COOKIE_SECURE": "false"},
        ):
            application = create_application("local-http")

        self.assertFalse(application.config["SESSION_COOKIE_SECURE"])

    def test_package_modules_do_not_import_legacy_app_entrypoint(self):
        package_root = Path(__file__).resolve().parents[1] / "test_plan_viewer"
        violations = []

        for source_file in package_root.rglob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(alias.name == "app" for alias in node.names):
                    violations.append(str(source_file.relative_to(package_root)))
                if isinstance(node, ast.ImportFrom) and node.module == "app":
                    violations.append(str(source_file.relative_to(package_root)))

        self.assertEqual(
            violations,
            [],
            "Package modules must depend inward and may not import the compatibility app.py entrypoint.",
        )

    def test_only_web_delivery_modules_import_flask(self):
        package_root = Path(__file__).resolve().parents[1] / "test_plan_viewer"
        violations = []

        for source_file in package_root.rglob("*.py"):
            relative_file = source_file.relative_to(package_root)
            if relative_file.parts[0] == "web":
                continue
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imports_flask = (
                    isinstance(node, ast.Import)
                    and any(alias.name == "flask" for alias in node.names)
                ) or (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "flask"
                )
                if imports_flask:
                    violations.append(str(relative_file))

        self.assertEqual(
            violations,
            [],
            "Flask request/response concerns belong in test_plan_viewer.web only.",
        )

    def test_index_blueprint_renders_configured_template(self):
        app = Flask(
            __name__,
            template_folder="../templates",
        )
        app.register_blueprint(index_blueprint)

        with app.test_client() as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        rendered = response.get_data(as_text=True)
        self.assertIn("Waterfall AI", rendered)
        self.assertIn("Agent-driven test automation platform", rendered)


if __name__ == "__main__":
    unittest.main()
