from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureDocumentationTests(unittest.TestCase):
    def test_readme_links_the_maintained_architecture_guide(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (
            ROOT / "ARCHITECTURE.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "[ARCHITECTURE.md](./ARCHITECTURE.md)",
            readme,
        )
        for required_text in (
            "app:app",
            "test_plan_viewer.web",
            "不得导入旧入口 `app`",
            "agent/",
            "agent/script_preparation.py",
            "agent_script_preparation",
            "page_inventory/",
            "`project_archive`",
            "static/js/features/",
            "agent-script-preparation",
            "Agent 七阶段主流程",
            "awaiting_script_action",
            "api-client → sse → timers",
            "稳定契约",
            "迁移与维护规则",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, architecture)


if __name__ == "__main__":
    unittest.main()
