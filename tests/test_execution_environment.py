import unittest

from test_plan_viewer.execution import environment


class ExecutionEnvironmentTests(unittest.TestCase):
    def test_test_execution_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            environment.require_test_execution_enabled({})

        self.assertIsNone(
            environment.require_test_execution_enabled(
                {"PLATFORM_ALLOW_TEST_EXECUTION": "true"}
            )
        )

    def test_playwright_receives_only_declared_target_credentials(self):
        source = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/demo",
            "LC_ALL": "C.UTF-8",
            "PLATFORM_SESSION_SECRET": "platform-session-secret",
            "PLATFORM_DB_PASSWORD": "platform-database-secret",
            "OPENCODE_SERVER_PASSWORD": "opencode-secret",
            "TARGET_DEMO_USERNAME": "demo-user",
            "TARGET_DEMO_PASSWORD": "demo-password",
            "UNRELATED_VALUE": "not-needed",
        }

        child_environment = environment.build_playwright_environment(
            source,
            {
                "base_url": "https://demo.example",
                "username_env": "TARGET_DEMO_USERNAME",
                "password_env": "TARGET_DEMO_PASSWORD",
            },
            extra={
                "TEST_PLAN_VIEWER_OUTPUT_DIR": "/tmp/results",
            },
        )

        self.assertEqual(child_environment["PATH"], "/usr/bin")
        self.assertEqual(
            child_environment["PLAYWRIGHT_BASE_URL"],
            "https://demo.example",
        )
        self.assertEqual(
            child_environment["TARGET_DEMO_USERNAME"],
            "demo-user",
        )
        self.assertEqual(
            child_environment["TARGET_DEMO_PASSWORD"],
            "demo-password",
        )
        self.assertNotIn("PLATFORM_SESSION_SECRET", child_environment)
        self.assertNotIn("PLATFORM_DB_PASSWORD", child_environment)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", child_environment)
        self.assertNotIn("UNRELATED_VALUE", child_environment)

    def test_unknown_child_environment_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            environment.build_playwright_environment(
                {"PATH": "/usr/bin"},
                {},
                extra={"PLATFORM_SESSION_SECRET": "exposed"},
            )

    def test_output_redaction_uses_environment_and_references(self):
        source = {
            "PLATFORM_SESSION_SECRET": "platform-session-secret",
            "TARGET_DEMO_USERNAME": "demo-user",
            "TARGET_DEMO_PASSWORD": "demo-password",
        }
        output = environment.redact_concrete_secrets(
            (
                "platform-session-secret demo-user "
                "demo-password visible"
            ),
            (
                {
                    "username_env": "TARGET_DEMO_USERNAME",
                    "password_env": "TARGET_DEMO_PASSWORD",
                },
            ),
            source,
        )

        self.assertEqual(output, "****** ****** ****** visible")


if __name__ == "__main__":
    unittest.main()
