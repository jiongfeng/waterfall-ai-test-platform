#!/usr/bin/env python3
"""Fail-closed contracts for release workflow test setup."""

from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_candidate_tests_use_the_checked_in_safe_configuration(self) -> None:
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "prepare-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            """    env:
      PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION: \"false\"
      PLATFORM_ALLOW_TEST_EXECUTION: \"false\"
      PLATFORM_CONFIG_PATH: config.example.json
""",
            workflow,
        )
        self.assertIn("python -m unittest discover -s tests", workflow)

    def test_release_chain_uses_minisign_without_github_attestations(self) -> None:
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                REPOSITORY_ROOT / ".github" / "workflows" / "prepare-release.yml",
                REPOSITORY_ROOT / ".github" / "workflows" / "approve-release.yml",
                REPOSITORY_ROOT / ".github" / "workflows" / "release.yml",
                REPOSITORY_ROOT / ".github" / "workflows" / "publish-release.yml",
            )
        )
        self.assertNotIn("attest-build-provenance", workflows)
        self.assertNotIn("gh attestation", workflows)
        self.assertNotIn("attestations:", workflows)
        self.assertIn("MINISIGN_SECRET_KEY", workflows)
        self.assertIn("RELEASE-MANIFEST.json.minisig", workflows)

    def test_publication_has_a_protected_single_state_transition(self) -> None:
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "publish-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("environment: release-publication", workflow)
        self.assertEqual(workflow.count("gh release edit"), 1)
        self.assertIn('--draft=false', workflow)
        self.assertIn("smoke-bundle.sh", workflow)
        self.assertIn("DOCKER_CONFIG", workflow)

    def test_approval_timestamp_comes_from_environment_deployment_status(self) -> None:
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "approve-release.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("'.created_at // empty'", workflow)
        self.assertIn("approval-deployment-statuses.json", workflow)
        self.assertIn('.state == "queued"', workflow)


if __name__ == "__main__":
    unittest.main()
