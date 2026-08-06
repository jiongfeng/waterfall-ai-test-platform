import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class BrandIdentityTests(unittest.TestCase):
    def test_public_identity_is_waterfall_ai(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        index = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        login = (ROOT / "templates/login.html").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("# Waterfall AI", readme)
        self.assertIn("Agent-driven test automation platform", readme)
        self.assertIn("not affiliated with, sponsored by, or endorsed", readme)
        self.assertIn("Waterfall AI", index)
        self.assertIn("Agent-driven test automation platform", index)
        self.assertIn("Waterfall AI", login)
        self.assertIn('name = "waterfall-ai-test-platform"', project)

    def test_runtime_compatibility_identifiers_are_preserved(self):
        config = json.loads(
            (ROOT / "deploy/config.example.json").read_text(encoding="utf-8")
        )
        dockerfile = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")

        self.assertEqual(config["platform_database"]["database"], "playwright_platform")
        self.assertIn("/opt/playwright-platform/app", dockerfile)
        self.assertTrue((ROOT / "test_plan_viewer").is_dir())


if __name__ == "__main__":
    unittest.main()
