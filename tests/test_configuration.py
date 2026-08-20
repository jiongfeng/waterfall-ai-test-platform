import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_plan_viewer.configuration import (
    APP_DIR,
    CONFIG_PATH,
    load_config,
    parse_auth_config,
    parse_platform_database_config,
    parse_projects_config,
    resolve_config_path,
)


class ConfigurationTests(unittest.TestCase):
    def test_default_paths_point_to_the_deployment_root(self):
        self.assertEqual(APP_DIR, Path(__file__).resolve().parents[1])
        self.assertEqual(CONFIG_PATH, APP_DIR / "config.json")

    def test_legacy_project_root_is_normalized_as_default_project(self):
        projects, default_key = parse_projects_config(
            {"playwright_project_root": "D:/tests"}
        )

        self.assertEqual(default_key, "default")
        self.assertEqual(projects[0]["project_key"], "default")
        self.assertEqual(projects[0]["playwright_project_root"], "D:/tests")
        self.assertTrue(projects[0]["is_default"])

    def test_load_config_accepts_an_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "playwright_project_root": "D:/tests",
                        "platform_database": {"enabled": False},
                        "auth": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertIsNone(config["error"])
        self.assertEqual(config["default_project_key"], "default")
        self.assertFalse(config["platform_database"]["enabled"])
        self.assertFalse(config["auth"]["enabled"])
        self.assertEqual(config["default_project_language"], "en")
        self.assertEqual(config["projects"][0]["language"], "en")

    def test_default_project_language_is_normalized_for_config_projects(self):
        projects, default_key = parse_projects_config(
            {
                "default_project_language": "zh-cn",
                "projects": [
                    {
                        "key": "demo",
                        "name": "Demo",
                        "playwright_project_root": "D:/tests",
                    }
                ],
            }
        )

        self.assertEqual(default_key, "demo")
        self.assertEqual(projects[0]["language"], "zh-CN")

    def test_invalid_default_project_language_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "playwright_project_root": "D:/tests",
                        "default_project_language": "fr",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertIn("Unsupported project language", config["error"])

    def test_auth_secrets_prefer_environment_over_file_values(self):
        with patch.dict(
            os.environ,
            {
                "PLATFORM_SESSION_SECRET": "environment-secret",
                "PLATFORM_ADMIN_PASSWORD": "environment-password",
            },
        ):
            auth = parse_auth_config(
                {
                    "session_secret": "file-secret",
                    "initial_admin_password": "file-password",
                }
            )

        self.assertEqual(auth["session_secret"], "environment-secret")
        self.assertEqual(auth["initial_admin_password"], "environment-password")

    def test_config_path_can_be_selected_at_runtime(self):
        with patch.dict(
            os.environ,
            {"PLATFORM_CONFIG_PATH": "/tmp/platform-config.json"},
        ):
            self.assertEqual(
                resolve_config_path(),
                Path("/tmp/platform-config.json"),
            )

    def test_enabled_mysql_configuration_requires_a_database_name(self):
        with self.assertRaisesRegex(ValueError, "database is required"):
            parse_platform_database_config({"enabled": True})


if __name__ == "__main__":
    unittest.main()
