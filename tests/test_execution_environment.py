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


if __name__ == "__main__":
    unittest.main()
