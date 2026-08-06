import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "deploy" / "preflight-install.py"
MATRIX = ROOT / "deploy" / "upgrade-matrix.json"


class InstallPreflightTests(unittest.TestCase):
    def run_preflight(
        self,
        *arguments: str,
        docker_has_resources: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            docker = Path(temporary_directory) / "docker"
            docker.write_text(
                "#!/bin/sh\n" + ("printf 'resource-id\\n'\n" if docker_has_resources else ""),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = temporary_directory + os.pathsep + environment.get(
                "PATH", ""
            )
            return subprocess.run(
                [sys.executable, str(PREFLIGHT), *arguments],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

    def test_checked_in_matrix_is_fail_closed(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))

        self.assertEqual(matrix["schema_version"], 1)
        self.assertEqual(matrix["policy"]["fresh_install"]["decision"], "allow")
        self.assertTrue(
            matrix["policy"]["fresh_install"]["requires_empty_target"]
        )
        self.assertEqual(matrix["policy"]["unknown_source"]["decision"], "deny")
        self.assertEqual(matrix["policy"]["unlisted_upgrade"]["decision"], "deny")
        self.assertEqual(matrix["upgrade_paths"], [])

    def test_missing_target_is_allowed_as_fresh_install(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "new-install"
            result = self.run_preflight("--target", str(target))
            target_was_created = target.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fresh install", result.stdout)
        self.assertFalse(target_was_created)

    def test_empty_target_is_allowed_without_modification(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "empty"
            target.mkdir()
            before = tuple(target.iterdir())
            result = self.run_preflight("--target", str(target))
            after = tuple(target.iterdir())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)

    def test_unknown_non_empty_target_is_denied_without_modification(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "existing"
            target.mkdir()
            marker = target / "compose.yaml"
            marker.write_text("services: {}\n", encoding="utf-8")
            before = marker.read_bytes()
            result = self.run_preflight("--target", str(target))
            after = marker.read_bytes()

        self.assertEqual(result.returncode, 10)
        self.assertIn("source is unknown", result.stderr)
        self.assertIn("No files were changed", result.stderr)
        self.assertEqual(before, after)

    def test_unlisted_version_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "existing"
            target.mkdir()
            metadata = target / "RELEASE-METADATA.json"
            metadata.write_text(
                json.dumps(
                    {
                        "version": "0.0.1",
                        "revision": "1" * 40,
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )
            release_metadata = root / "target-release.json"
            release_metadata.write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "revision": "2" * 40,
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_preflight(
                "--target",
                str(target),
                "--release-metadata",
                str(release_metadata),
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("is not supported", result.stderr)

    def test_source_metadata_requires_revision_and_contract_version(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "existing"
            target.mkdir()
            (target / "RELEASE-METADATA.json").write_text(
                json.dumps({"version": "0.1.0", "revision": "1" * 40}),
                encoding="utf-8",
            )
            release_metadata = root / "target-release.json"
            release_metadata.write_text(
                json.dumps(
                    {
                        "version": "0.1.1",
                        "revision": "2" * 40,
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_preflight(
                "--target",
                str(target),
                "--release-metadata",
                str(release_metadata),
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("deployment contract version", result.stderr)

    def test_fresh_install_validates_target_release_identity_when_provided(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "empty"
            target.mkdir()
            release_metadata = root / "target-release.json"
            release_metadata.write_text(
                json.dumps(
                    {
                        "version": "0.1.1",
                        "revision": "not-a-full-sha",
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_preflight(
                "--target",
                str(target),
                "--release-metadata",
                str(release_metadata),
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("40-character SHA", result.stderr)

    def test_exact_explicit_upgrade_path_can_be_allowed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "existing"
            target.mkdir()
            (target / "RELEASE-METADATA.json").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "revision": "1" * 40,
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )
            release_metadata = root / "target-release.json"
            release_metadata.write_text(
                json.dumps(
                    {
                        "version": "0.1.1",
                        "revision": "2" * 40,
                        "deploymentContractVersion": 2,
                    }
                ),
                encoding="utf-8",
            )
            matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
            matrix["upgrade_paths"] = [
                {
                    "from": {
                        "version": "0.1.0",
                        "revision": "1" * 40,
                        "deployment_contract_version": 1,
                    },
                    "to": {
                        "version": "0.1.1",
                        "revision": "2" * 40,
                        "deployment_contract_version": 2,
                    },
                    "mode": "in_place",
                    "decision": "allow",
                }
            ]
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            result = self.run_preflight(
                "--target",
                str(target),
                "--release-metadata",
                str(release_metadata),
                "--matrix",
                str(matrix_path),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("listed in-place upgrade", result.stdout)

    def test_same_version_with_different_revision_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "existing"
            target.mkdir()
            (target / "RELEASE-METADATA.json").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "revision": "3" * 40,
                        "deploymentContractVersion": 1,
                    }
                ),
                encoding="utf-8",
            )
            release_metadata = root / "target-release.json"
            release_metadata.write_text(
                json.dumps(
                    {
                        "version": "0.1.1",
                        "revision": "2" * 40,
                        "deploymentContractVersion": 2,
                    }
                ),
                encoding="utf-8",
            )
            matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
            matrix["upgrade_paths"] = [
                {
                    "from": {
                        "version": "0.1.0",
                        "revision": "1" * 40,
                        "deployment_contract_version": 1,
                    },
                    "to": {
                        "version": "0.1.1",
                        "revision": "2" * 40,
                        "deployment_contract_version": 2,
                    },
                    "mode": "in_place",
                    "decision": "allow",
                }
            ]
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            result = self.run_preflight(
                "--target",
                str(target),
                "--release-metadata",
                str(release_metadata),
                "--matrix",
                str(matrix_path),
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("is not supported", result.stderr)

    def test_symlink_target_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            real_target = root / "real"
            real_target.mkdir()
            linked_target = root / "linked"
            linked_target.symlink_to(real_target, target_is_directory=True)
            result = self.run_preflight("--target", str(linked_target))

        self.assertEqual(result.returncode, 10)
        self.assertIn("symbolic link", result.stderr)

    def test_existing_compose_resources_deny_an_empty_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "empty"
            target.mkdir()
            result = self.run_preflight(
                "--target",
                str(target),
                docker_has_resources=True,
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("already has containers", result.stderr)
        self.assertIn("source is unknown", result.stderr)

    def test_invalid_compose_project_identity_is_denied_before_install(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "empty"
            target.mkdir()
            result = self.run_preflight(
                "--target",
                str(target),
                "--compose-project",
                "Invalid Project",
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("Compose project name is invalid", result.stderr)

    def test_permissive_or_unknown_matrix_policy_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "empty"
            target.mkdir()
            matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
            matrix["policy"]["unknown_source"]["decision"] = "allow"
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            result = self.run_preflight(
                "--target",
                str(target),
                "--matrix",
                str(matrix_path),
            )

        self.assertEqual(result.returncode, 10)
        self.assertIn("unknown_source", result.stderr)


if __name__ == "__main__":
    unittest.main()
