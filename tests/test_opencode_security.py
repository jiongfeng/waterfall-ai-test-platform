import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


APP_DIR = Path(__file__).resolve().parents[1]
OPENCODE_CONFIG_PATH = APP_DIR / "project-template" / "opencode.json"
PLAYWRIGHT_MCP_WRAPPER_PATH = (
    APP_DIR
    / "project-template"
    / ".opencode"
    / "run-playwright-mcp.sh"
)


class OpenCodeSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(
            OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")
        )

    def test_every_agent_denies_external_directory_access(self):
        agents = self.config.get("agent") or {}
        self.assertTrue(agents)

        for agent_name, agent in agents.items():
            with self.subTest(agent=agent_name):
                self.assertEqual(
                    (agent.get("permission") or {}).get(
                        "external_directory"
                    ),
                    "deny",
                )

    def test_unsafe_browser_execution_tools_are_not_enabled(self):
        forbidden_fragments = {
            "browser_evaluate",
            "browser_run_code_unsafe",
        }
        for agent_name, agent in (
            self.config.get("agent") or {}
        ).items():
            enabled_tools = {
                name
                for name, enabled in (agent.get("tools") or {}).items()
                if enabled
            }
            with self.subTest(agent=agent_name):
                self.assertFalse(
                    any(
                        fragment in tool
                        for fragment in forbidden_fragments
                        for tool in enabled_tools
                    )
                )

    def test_global_raw_playwright_tools_stay_disabled(self):
        self.assertFalse(
            (self.config.get("tools") or {}).get("playwright*")
        )

    def test_playwright_mcp_uses_an_environment_allowlist_wrapper(self):
        command = (
            self.config["mcp"]["playwright-test"]["command"]
        )
        self.assertEqual(
            command,
            [
                "bash",
                ".opencode/run-playwright-mcp.sh",
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            fake_npx = Path(directory) / "npx"
            fake_npx.write_text(
                "#!/bin/sh\nexec env\n",
                encoding="utf-8",
            )
            fake_npx.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{directory}:{os.environ['PATH']}",
                "OPENCODE_SERVER_PASSWORD": (
                    "unknown-opencode-server-secret"
                ),
                "MODEL_PROVIDER_API_KEY": (
                    "unknown-model-provider-secret"
                ),
            }

            completed = subprocess.run(
                ["bash", str(PLAYWRIGHT_MCP_WRAPPER_PATH)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )

        self.assertNotIn(
            "OPENCODE_SERVER_PASSWORD",
            completed.stdout,
        )
        self.assertNotIn(
            "MODEL_PROVIDER_API_KEY",
            completed.stdout,
        )
        self.assertIn(
            "PLAYWRIGHT_BROWSERS_PATH=",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
