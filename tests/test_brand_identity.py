import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class BrandIdentityTests(unittest.TestCase):
    def test_public_identity_is_waterfall_ai(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        index = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        login = (ROOT / "templates/login.html").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('<h1 align="center">Waterfall AI Test</h1>', readme)
        self.assertIn(
            "Open-source visual test workbench for Playwright Test Agents",
            readme,
        )
        self.assertIn(
            "基于 Playwright Test Agents 的开源可视化测试工作台",
            readme_zh,
        )
        self.assertIn(
            "## A visual workbench for Playwright Test Agents",
            readme,
        )
        self.assertIn(
            "## Playwright Test Agents 的可视化工作台",
            readme_zh,
        )
        self.assertIn(
            "| Conversation-driven Playwright Test Agents | Waterfall AI Test |\n"
            "| --- | --- |\n",
            readme,
        )
        self.assertIn(
            "| Playwright Test Agents 的对话式使用方式 | Waterfall AI Test |\n"
            "| --- | --- |\n",
            readme_zh,
        )
        self.assertNotRegex(readme, r"Waterfall AI(?! Test)")
        self.assertNotRegex(readme_zh, r"Waterfall AI(?! Test)")
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
