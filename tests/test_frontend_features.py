import shutil
import subprocess
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for frontend VM tests")
class FrontendFeatureVmTests(unittest.TestCase):
    def run_vm_test(self, filename, success_message):
        result = subprocess.run(
            [NODE, str(APP_DIR / "tests" / "js" / filename)],
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
        self.assertIn(success_message, result.stdout)

    def test_test_suite_feature_state_and_stream_contracts(self):
        self.run_vm_test(
            "test-suites.vm.js",
            "test-suite feature VM smoke: ok",
        )

    def test_api_client_adds_the_page_csrf_token_to_requests(self):
        self.run_vm_test(
            "api-client.vm.js",
            "api client CSRF VM smoke: ok",
        )

    def test_requirements_feature_state_payload_and_stream_contracts(self):
        self.run_vm_test(
            "requirements.vm.js",
            "requirements feature VM smoke: ok",
        )

    def test_generation_feature_coverage_stream_timer_and_persistence_contracts(self):
        self.run_vm_test(
            "generation.vm.js",
            "generation feature VM smoke: ok",
        )

    def test_script_repair_feature_stream_timer_and_persistence_contracts(self):
        self.run_vm_test(
            "script-repair.vm.js",
            "script-repair feature VM smoke: ok",
        )

    def test_module_execution_feature_stream_cancel_timer_and_record_contracts(self):
        self.run_vm_test(
            "module-execution.vm.js",
            "module-execution feature VM smoke: ok",
        )

    def test_module_plan_generation_feature_state_and_stream_contracts(self):
        self.run_vm_test(
            "module-plan-generation.vm.js",
            "module plan generation feature VM smoke: ok",
        )

    def test_assembled_frontend_bootstrap_contract(self):
        self.run_vm_test(
            "app-bootstrap.vm.js",
            "assembled frontend bootstrap VM smoke: ok",
        )

    def test_admin_feature_menu_and_repository_contracts(self):
        self.run_vm_test(
            "admin.vm.js",
            "admin feature VM smoke: ok",
        )

    def test_projects_feature_lifecycle_contracts(self):
        self.run_vm_test(
            "projects.vm.js",
            "projects feature VM smoke: ok",
        )

    def test_plan_transfer_selection_download_and_import_contracts(self):
        self.run_vm_test(
            "plan-transfer.vm.js",
            "plan transfer feature VM smoke: ok",
        )

    def test_project_settings_feature_payload_and_stream_contracts(self):
        self.run_vm_test(
            "project-settings.vm.js",
            "project-settings feature VM smoke: ok",
        )

    def test_agent_script_preparation_list_history_and_actions(self):
        self.run_vm_test(
            "agent-script-preparation.vm.js",
            "agent script preparation VM smoke: ok",
        )


if __name__ == "__main__":
    unittest.main()
