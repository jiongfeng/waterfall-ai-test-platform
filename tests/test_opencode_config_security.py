import os
import unittest
from unittest.mock import patch

import app


class OpenCodeConfigSecurityTests(unittest.TestCase):
    def test_project_password_is_resolved_from_named_environment_variable(self):
        with (
            patch.object(
                app,
                "load_config",
                return_value={
                    "error": None,
                    "opencode_server_url": "http://global:4096",
                    "opencode_username": "global-user",
                    "opencode_password_env": "GLOBAL_OPENCODE_PASSWORD",
                    "opencode_password": "global-secret",
                },
            ),
            patch.object(
                app,
                "get_current_project",
                return_value={
                    "opencode_config": {
                        "opencode_server_url": "http://project:4096",
                        "opencode_username": "project-user",
                        "opencode_password_env": (
                            "PROJECT_OPENCODE_PASSWORD"
                        ),
                        "opencode_password": "legacy-project-secret",
                    }
                },
            ),
            patch.dict(
                os.environ,
                {"PROJECT_OPENCODE_PASSWORD": "runtime-project-secret"},
            ),
        ):
            config = app.get_opencode_config()

        self.assertEqual(
            config["opencode_server_url"],
            "http://project:4096",
        )
        self.assertEqual(
            config["opencode_password_env"],
            "PROJECT_OPENCODE_PASSWORD",
        )
        self.assertEqual(
            config["opencode_password"],
            "runtime-project-secret",
        )
        self.assertNotEqual(
            config["opencode_password"],
            "legacy-project-secret",
        )
        self.assertTrue(
            config["credentials_migration_required"]
        )

    def test_plaintext_password_never_falls_back_when_env_is_missing(self):
        with self.assertRaisesRegex(
            ValueError,
            "plaintext password configuration was ignored.*"
            "PROJECT_OPENCODE_PASSWORD",
        ):
            app.resolve_opencode_runtime_config(
                {
                    "opencode_server_url": "http://global:4096",
                    "opencode_password": "global-file-secret",
                },
                {
                    "opencode_config": {
                        "opencode_password_env": (
                            "PROJECT_OPENCODE_PASSWORD"
                        ),
                        "opencode_password": (
                            "project-file-secret"
                        ),
                    }
                },
                environment={},
            )

        with self.assertRaisesRegex(
            ValueError,
            "plaintext password configuration was ignored.*"
            "PROJECT_OPENCODE_PASSWORD",
        ):
            app.resolve_opencode_runtime_config(
                {
                    "opencode_server_url": "http://global:4096",
                },
                {
                    "opencode_config": {
                        "opencode_password_env": (
                            "PROJECT_OPENCODE_PASSWORD"
                        ),
                        "opencode_password": (
                            "project-file-secret"
                        ),
                    }
                },
                environment={
                    "PROJECT_OPENCODE_PASSWORD": "",
                },
            )


if __name__ == "__main__":
    unittest.main()
