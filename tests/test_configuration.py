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
    parse_target_system_config,
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

    def test_auth_secrets_use_environment_and_mark_file_migration(self):
        session_secret = "environment-session-secret-" + ("x" * 32)
        admin_password = "Environment-Password-123!"
        with patch.dict(
            os.environ,
            {
                "PLATFORM_SESSION_SECRET": session_secret,
                "PLATFORM_ADMIN_PASSWORD": admin_password,
            },
        ):
            auth = parse_auth_config(
                {
                    "session_secret": "file-secret",
                    "initial_admin_password": "file-password",
                }
            )

        self.assertEqual(auth["session_secret"], session_secret)
        self.assertEqual(auth["initial_admin_password"], admin_password)
        self.assertTrue(auth["credentials_migration_required"])
        self.assertEqual(
            auth["legacy_secret_fields"],
            ["session_secret", "initial_admin_password"],
        )

    def test_enabled_authentication_rejects_missing_and_placeholder_secrets(self):
        with (
            patch.dict(
                os.environ,
                {
                    "PLATFORM_SESSION_SECRET": "",
                    "PLATFORM_ADMIN_PASSWORD": "",
                },
                clear=False,
            ),
            self.assertRaisesRegex(ValueError, "SESSION_SECRET"),
        ):
            parse_auth_config({"enabled": True})

        with (
            patch.dict(
                os.environ,
                {
                    "PLATFORM_SESSION_SECRET": (
                        "test-plan-viewer-change-me"
                    ),
                    "PLATFORM_ADMIN_PASSWORD": (
                        "Strong-Password-123!"
                    ),
                },
                clear=False,
            ),
            self.assertRaisesRegex(
                ValueError,
                "insecure placeholder",
            ),
        ):
            parse_auth_config(
                {
                    "enabled": True,
                }
            )

        with (
            patch.dict(
                os.environ,
                {
                    "PLATFORM_SESSION_SECRET": "",
                    "PLATFORM_ADMIN_PASSWORD": "",
                },
                clear=False,
            ),
            self.assertRaisesRegex(
                ValueError,
                "plaintext was ignored.*PLATFORM_SESSION_SECRET",
            ),
        ):
            parse_auth_config(
                {
                    "enabled": True,
                    "session_secret": (
                        "file-session-secret-" + ("x" * 32)
                    ),
                    "initial_admin_password": (
                        "File-Password-123!"
                    ),
                }
            )

    def test_disabled_authentication_does_not_require_secrets(self):
        with patch.dict(
            os.environ,
            {
                "PLATFORM_SESSION_SECRET": "",
                "PLATFORM_ADMIN_PASSWORD": "",
            },
            clear=False,
        ):
            auth = parse_auth_config({"enabled": False})

        self.assertFalse(auth["enabled"])
        self.assertFalse(auth["session_secret"])
        self.assertFalse(auth["initial_admin_password"])

    def test_config_path_can_be_selected_at_runtime(self):
        with patch.dict(
            os.environ,
            {"PLATFORM_CONFIG_PATH": "/tmp/platform-config.json"},
        ):
            self.assertEqual(
                resolve_config_path(),
                Path("/tmp/platform-config.json"),
            )

    def test_target_system_uses_secret_references_not_literal_credentials(self):
        target = parse_target_system_config(
            {
                "base_url": "https://example.test",
                "username": "legacy-user",
                "password": "legacy-password",
                "username_env": "TARGET_DEMO_USERNAME",
                "password_env": "TARGET_DEMO_PASSWORD",
            }
        )

        self.assertNotIn("username", target)
        self.assertNotIn("password", target)
        self.assertEqual(
            target["username_env"],
            "TARGET_DEMO_USERNAME",
        )
        self.assertEqual(
            target["password_env"],
            "TARGET_DEMO_PASSWORD",
        )
        self.assertTrue(target["credentials_migration_required"])

    def test_target_system_rejects_platform_secret_aliases(self):
        with self.assertRaisesRegex(ValueError, "TARGET_"):
            parse_target_system_config(
                {
                    "username_env": "PLATFORM_ADMIN_PASSWORD",
                    "password_env": "TARGET_SYSTEM_PASSWORD",
                }
            )

    def test_service_urls_reject_embedded_credentials(self):
        with self.assertRaisesRegex(
            ValueError,
            "embedded credentials",
        ):
            parse_target_system_config(
                {
                    "base_url": "https://user:secret@example.test",
                }
            )

        with self.assertRaisesRegex(ValueError, "HTTP"):
            parse_target_system_config(
                {
                    "base_url": "file:///etc/passwd",
                }
            )

    def test_target_urls_reject_credential_query_parameters(self):
        examples = (
            {
                "base_url": (
                    "https://example.test/?"
                    "access_token=query-secret"
                )
            },
            {
                "base_url": "https://example.test",
                "login_url": "/login?token=query-secret",
            },
            {
                "base_url": (
                    "https://example.test/?"
                    "dbPassword=query-secret"
                )
            },
        )

        for example in examples:
            with self.subTest(example=example):
                with self.assertRaisesRegex(
                    ValueError,
                    "query parameters",
                ):
                    parse_target_system_config(example)

    def test_enabled_mysql_configuration_requires_a_database_name(self):
        with self.assertRaisesRegex(ValueError, "database is required"):
            parse_platform_database_config({"enabled": True})

    def test_command_database_baseline_is_rejected(self):
        from test_plan_viewer.configuration import (
            parse_database_baseline_config,
        )

        with self.assertRaisesRegex(ValueError, "no longer supported"):
            parse_database_baseline_config(
                {
                    "enabled": True,
                    "mode": "command",
                    "backup_command": ["backup"],
                    "restore_command": ["restore"],
                }
            )

    def test_database_password_prefers_environment(self):
        with patch.dict(
            os.environ,
            {"PLATFORM_DB_PASSWORD": "runtime-database-secret"},
        ):
            database = parse_platform_database_config(
                {
                    "enabled": True,
                    "database": "platform",
                    "user": "platform",
                    "password": "file-secret",
                }
            )

        self.assertEqual(
            database["password"],
            "runtime-database-secret",
        )
        self.assertTrue(
            database["credentials_migration_required"]
        )
        self.assertEqual(
            database["legacy_secret_fields"],
            ["password"],
        )

    def test_database_plaintext_is_ignored_without_environment(self):
        with (
            patch.dict(
                os.environ,
                {"PLATFORM_DB_PASSWORD": ""},
                clear=False,
            ),
            self.assertRaisesRegex(
                ValueError,
                "plaintext was ignored.*PLATFORM_DB_PASSWORD",
            ),
        ):
            parse_platform_database_config(
                {
                    "enabled": True,
                    "database": "platform",
                    "user": "platform",
                    "password": "file-secret",
                }
            )

    def test_load_config_never_returns_global_opencode_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "playwright_project_root": "D:/tests",
                        "opencode_password": "file-opencode-secret",
                        "platform_database": {"enabled": False},
                        "auth": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"OPENCODE_SERVER_PASSWORD": ""},
                clear=False,
            ):
                config = load_config(config_path)

        self.assertIsNone(config["error"])
        self.assertNotIn("opencode_password", config)
        self.assertTrue(
            config[
                "opencode_credentials_migration_required"
            ]
        )
        self.assertNotIn(
            "file-opencode-secret",
            repr(config),
        )

    def test_public_config_examples_only_reference_secret_environments(self):
        for config_path in (
            APP_DIR / "config.example.json",
            APP_DIR / "deploy" / "config.example.json",
        ):
            with self.subTest(config_path=config_path):
                config = json.loads(
                    config_path.read_text(encoding="utf-8")
                )
                self.assertNotIn("opencode_password", config)
                self.assertNotIn(
                    "session_secret",
                    config.get("auth", {}),
                )
                self.assertNotIn(
                    "initial_admin_password",
                    config.get("auth", {}),
                )
                self.assertNotIn(
                    "password",
                    config.get("platform_database", {}),
                )
                for project in config.get("projects", []):
                    self.assertNotIn(
                        "opencode_password",
                        project.get("opencode_config", {}),
                    )


if __name__ == "__main__":
    unittest.main()
