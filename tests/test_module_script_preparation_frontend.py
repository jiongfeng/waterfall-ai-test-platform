import shutil
import subprocess
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for frontend VM tests")
class ModuleScriptPreparationFrontendTests(unittest.TestCase):
    def run_vm_test(self, filename, success_message):
        result = subprocess.run(
            [
                NODE,
                str(
                    APP_DIR
                    / "tests"
                    / "js"
                    / filename
                ),
            ],
            cwd=APP_DIR,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn(
            success_message,
            result.stdout,
        )

    def test_agent_and_module_workbenches_share_one_isolated_factory(self):
        self.run_vm_test(
            "module-script-preparation.vm.js",
            "module script preparation shared VM smoke: ok",
        )

    def test_module_adapter_opens_run_and_cleans_polling(self):
        self.run_vm_test(
            "module-script-preparation-adapter.vm.js",
            "module script preparation adapter VM smoke: ok",
        )

    def test_view_state_persists_module_preparation_identity(self):
        source = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")

        expected_contracts = (
            r'PREPARATION\s*:\s*"preparation"',
            r"preparationRunId\s*:\s*typeof\s+parsed\.preparationRunId",
            r"preparationModule\s*:\s*typeof\s+parsed\.preparationModule",
            r"preparationRunId\s*:\s*initialViewState\.preparationRunId",
            r"preparationModule\s*:\s*initialViewState\.preparationModule",
            r"preparationRunId\s*:\s*state\.scripts\.preparationRunId",
            r"preparationModule\s*:\s*state\.scripts\.preparationModule",
            r"preparationRuns\s*:\s*normalizeModuleScriptPreparationRuns\(parsed\.preparationRuns",
            r"preparationRuns\s*:\s*initialViewState\.preparationRuns",
            r"preparationRuns\s*:\s*state\.scripts\.preparationRuns",
            r"createModuleScriptPreparationFeature\s*\(\s*{",
        )
        for contract in expected_contracts:
            with self.subTest(contract=contract):
                self.assertRegex(source, contract)

    def test_script_editor_put_uses_the_revision_captured_when_editing_opens(self):
        source = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")

        self.assertRegex(
            source,
            r"editBaselineRevisionId\s*=\s*state\.activeSection\s*===\s*SECTION\.SCRIPTS\s*\?\s*state\.scripts\.asset\?\.current_revision_id",
        )
        self.assertRegex(
            source,
            r"JSON\.stringify\(\{\s*content:\s*nextContent,\s*expected_revision_id:\s*state\.scripts\.editBaselineRevisionId\s*\}\)",
        )
        self.assertNotRegex(
            source,
            r"JSON\.stringify\(\{\s*content:\s*nextContent,\s*expected_revision_id:\s*state\.scripts\.asset",
        )

    def test_revision_restore_posts_the_revision_captured_when_the_button_renders(self):
        source = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")

        self.assertRegex(
            source,
            r"const\s+expectedRevisionId\s*=\s*asset\.current_revision_id;[\s\S]*?restoreAssetRevision\(asset\.asset_id,\s*revision\.revision_id,\s*expectedRevisionId\)",
        )
        self.assertRegex(
            source,
            r"restoreAssetRevision\(assetId,\s*revisionId,\s*expectedRevisionId\)[\s\S]*?JSON\.stringify\(\{\s*expected_revision_id:\s*expectedRevisionId\s*\}\)",
        )


if __name__ == "__main__":
    unittest.main()
