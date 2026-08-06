import ast
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

import app
from test_plan_viewer.agent import failure_handling as failure_domain


class AgentFailureAnalysisTests(unittest.TestCase):
    """Keep generic failure-analysis safeguards independent of the retired UI."""

    def test_failure_analyst_entrypoint_keeps_targeted_evidence_redaction(self):
        with patch.object(app, "call_agent_json_agent", return_value={}) as call:
            app.call_agent_failure_analyst(
                "run-1",
                "prepare_scripts",
                "分析失败",
                {"error": "执行失败"},
            )

        self.assertIs(
            call.call_args.args[-1],
            failure_domain.redact_agent_failure_value,
        )

    def test_failure_analysis_uses_dedicated_agent_without_reviewer_bias(self):
        prompt_path = (
            app.APP_DIR
            / "project-template"
            / ".opencode"
            / "prompts"
            / "test-platform-failure-analyst.md"
        )
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertNotIn("prefer keep", prompt.lower())
        self.assertIn("Do not return reviewer decisions", prompt)

    def test_failure_analyst_has_no_project_file_tools(self):
        config = json.loads(
            (app.APP_DIR / "project-template" / "opencode.json").read_text(
                encoding="utf-8"
            )
        )
        analyst = config["agent"]["test-platform-failure-analyst"]

        self.assertEqual(
            analyst["permission"],
            {"*": "deny", "external_directory": "deny"},
        )
        self.assertEqual(analyst["tools"], {"*": False})

    @unittest.skipUnless(shutil.which("opencode"), "opencode CLI is unavailable")
    def test_failure_analyst_effective_permissions_end_in_wildcard_deny(self):
        result = subprocess.run(
            ["opencode", "agent", "list", "--pure"],
            cwd=app.APP_DIR / "project-template",
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        marker = "test-platform-failure-analyst (subagent)"
        section = result.stdout.split(marker, 1)[1].lstrip()
        permissions, _ = json.JSONDecoder().raw_decode(section)
        wildcard_indexes = [
            index
            for index, item in enumerate(permissions)
            if item.get("permission") == "*"
        ]

        self.assertTrue(wildcard_indexes)
        final_wildcard_index = wildcard_indexes[-1]
        self.assertEqual(permissions[final_wildcard_index]["action"], "deny")
        dangerous = {
            "*",
            "bash",
            "edit",
            "write",
            "patch",
            "read",
            "task",
            "webfetch",
            "websearch",
        }
        self.assertFalse(
            any(
                item.get("permission") in dangerous
                and item.get("action") == "allow"
                for item in permissions[final_wildcard_index + 1 :]
            )
        )

    def test_failure_handling_domain_does_not_import_flask_or_app(self):
        source_path = (
            app.APP_DIR
            / "test_plan_viewer"
            / "agent"
            / "failure_handling.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertTrue({"app", "flask"}.isdisjoint(imported_roots), imported_roots)

    def test_failure_evidence_redaction_fails_closed(self):
        secret = "Bearer platform-secret-token"
        with patch.object(
            failure_domain.agent_diagnostics,
            "redact_diagnostic_value",
            side_effect=RuntimeError("redactor unavailable"),
        ):
            redacted_mapping = failure_domain.redact_agent_failure_value(
                {"authorization": secret}
            )
            redacted_list = failure_domain.redact_agent_failure_value(
                [{"authorization": secret}]
            )
            redacted_text = failure_domain.redact_agent_failure_value(secret)

        self.assertEqual(redacted_mapping, {"redaction_failed": True})
        self.assertEqual(redacted_list, [])
        self.assertEqual(redacted_text, "[已隐藏]")
        self.assertNotIn(
            secret,
            repr((redacted_mapping, redacted_list, redacted_text)),
        )

    def test_failure_analysis_normalization_clamps_and_filters_values(self):
        parsed = {
            "summary": "  定位完成  ",
            "root_cause_category": "NOT-A-CATEGORY",
            "confidence": 3.5,
            "facts": [" 已确认 ", "", 42],
            "recommended_action": "regenerate",
            "suggestion": "  收窄生成范围  ",
        }
        with patch.object(failure_domain, "current_time_ms", return_value=1234):
            result = failure_domain.normalize_agent_failure_analysis(
                parsed,
                {"source_type": "repair", "error": "执行失败"},
            )

        self.assertEqual(result["summary"], "定位完成")
        self.assertEqual(result["root_cause_category"], "unknown")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["facts"], ["已确认", "42"])
        self.assertEqual(result["recommended_action"], "regenerate")
        self.assertEqual(result["suggestion"], "收窄生成范围")
        self.assertEqual(result["generated_at"], 1234)

    def test_failure_analysis_normalization_uses_source_specific_fallback(self):
        with patch.object(failure_domain, "current_time_ms", return_value=5678):
            generation = failure_domain.normalize_agent_failure_analysis(
                None,
                {"source_type": "generation", "error": "生成失败"},
            )
            repair = failure_domain.normalize_agent_failure_analysis(
                {"confidence": "invalid"},
                {"source_type": "repair", "error": "修复失败"},
            )

        self.assertEqual(generation["summary"], "生成失败")
        self.assertEqual(generation["recommended_action"], "regenerate")
        self.assertEqual(repair["summary"], "修复失败")
        self.assertEqual(repair["recommended_action"], "repair")
        self.assertEqual(repair["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
