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
            "16,600",
            "迁移与维护规则",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, architecture)

    def test_legacy_entry_files_stay_within_migration_budgets(self):
        budgets = {
            "app.py": 16_600,
            "static/app.js": 4_100,
            "templates/index.html": 650,
            "static/styles.css": 2_600,
        }

        for relative_path, maximum_lines in budgets.items():
            with self.subTest(relative_path=relative_path):
                source = (
                    ROOT / relative_path
                ).read_text(encoding="utf-8")
                line_count = len(source.splitlines())
                self.assertLessEqual(
                    line_count,
                    maximum_lines,
                    (
                        f"{relative_path} has grown to "
                        f"{line_count} lines; move the new "
                        "responsibility into its domain/feature "
                        "module or intentionally revise the "
                        "architecture budget."
                    ),
                )

    def test_frontend_feature_files_remain_bounded(self):
        feature_root = ROOT / "static" / "js" / "features"
        oversized = {
            path.name: len(
                path.read_text(encoding="utf-8").splitlines()
            )
            for path in feature_root.glob("*.js")
            if len(
                path.read_text(
                    encoding="utf-8"
                ).splitlines()
            )
            > 3_000
        }

        self.assertEqual(
            oversized,
            {},
            "Split a feature by cohesive sub-capability "
            "before it becomes another frontend monolith.",
        )


if __name__ == "__main__":
    unittest.main()
