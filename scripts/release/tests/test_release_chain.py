#!/usr/bin/env python3
"""Unit tests for immutable candidate and protected approval bindings."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

from release_chain import validate_chain  # noqa: E402


class ReleaseChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate_root = self.root / "candidate"
        self.sbom_root = self.candidate_root / "sbom"
        self.sbom_root.mkdir(parents=True)
        for name, value in {
            "platform.spdx.json": {"spdxVersion": "SPDX-2.3"},
            "platform.cdx.json": {"bomFormat": "CycloneDX"},
            "mysql.spdx.json": {"spdxVersion": "SPDX-2.3"},
            "mysql.cdx.json": {"bomFormat": "CycloneDX"},
        }.items():
            (self.sbom_root / name).write_text(json.dumps(value) + "\n", encoding="utf-8")
        self.archive = self.candidate_root / "platform-linux-amd64.oci.tar"
        self.archive.write_bytes(b"fixture OCI archive\n")
        self.revision = "1" * 40
        self.platform_digest = "sha256:" + "a" * 64
        self.platform_config_digest = "sha256:" + "d" * 64
        self.mysql_image = "docker.io/library/mysql:8.4@sha256:" + "b" * 64
        self.mysql_parent = "docker.io/library/mysql:8.4@sha256:" + "c" * 64
        self.mysql_config_digest = "sha256:" + "e" * 64
        self.license_payload_root = self.candidate_root / "license-payloads"
        for name, reference, config_digest in (
            (
                "platformImage",
                f"ghcr.io/example/project@{self.platform_digest}",
                self.platform_config_digest,
            ),
            ("mysqlImage", self.mysql_image, self.mysql_config_digest),
        ):
            source_path = "/usr/share/common-licenses/MIT"
            artifact_name = hashlib.sha256(source_path.encode()).hexdigest() + ".license"
            component = self.license_payload_root / name
            files = component / "files"
            files.mkdir(parents=True)
            content = f"fixture license for {name}\n".encode()
            (files / artifact_name).write_bytes(content)
            manifest = {
                "schemaVersion": 1,
                "kind": "playwright-test-platform-final-image-license-files",
                "selectionPolicy": "license-notice-filenames-v1",
                "imageReference": reference,
                "configDigest": config_digest,
                "fileCount": 1,
                "totalBytes": len(content),
                "files": [{
                    "sourcePath": source_path,
                    "artifactPath": f"files/{artifact_name}",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }],
            }
            (component / "LICENSE-FILES.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        self.candidate = self.candidate_root / "RELEASE-CANDIDATE.json"
        self.run_script(
            RELEASE_DIR / "write-release-candidate.py",
            "--output", self.candidate,
            "--version", "1.2.3-beta.1",
            "--revision", self.revision,
            "--source-url", "https://github.com/example/project",
            "--source-date-epoch", "1700000000",
            "--target-image", "ghcr.io/example/project",
            "--platform-digest", self.platform_digest,
            "--platform-config-digest", self.platform_config_digest,
            "--platform-archive", self.archive,
            "--mysql-image", self.mysql_image,
            "--mysql-parent-index", self.mysql_parent,
            "--mysql-config-digest", self.mysql_config_digest,
            "--sbom-dir", self.sbom_root,
            "--license-payload-dir", self.license_payload_root,
            "--workflow-repository", "example/project",
            "--workflow-run-id", "100",
            "--workflow-run-attempt", "1",
            "--workflow-ref", "refs/heads/main",
            "--workflow-sha", self.revision,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, script: Path, *arguments: object, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *(str(value) for value in arguments)],
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_approval(self, name: str, *, offline: bool = True, platform: bool = True) -> Path:
        output = self.root / name
        args: list[object] = [
            "--candidate", self.candidate,
            "--candidate-root", self.candidate_root,
            "--output-dir", output,
            "--platform-distribution-approved", str(platform).lower(),
            "--platform-license-complete", str(platform).lower(),
            "--mysql-offline-approved", str(offline).lower(),
            "--mysql-license-complete", str(offline).lower(),
            "--reviewed-by", "authorized-reviewer",
            "--reviewed-at", "2026-08-05T01:02:03Z",
            "--workflow-repository", "example/project",
            "--workflow-run-id", "200",
            "--workflow-run-attempt", "1",
            "--workflow-ref", "refs/heads/main",
            "--workflow-sha", self.revision,
        ]
        if platform:
            args.extend([
                "--platform-distribution-evidence", "https://example.invalid/platform-distribution",
                "--platform-license-evidence", "urn:example:platform-license",
            ])
        if offline:
            args.extend([
                "--mysql-offline-evidence", "https://example.invalid/mysql-distribution",
                "--mysql-license-evidence", "urn:example:mysql-license",
            ])
        self.run_script(RELEASE_DIR / "write-release-approval.py", *args)
        return output

    def test_online_approval_may_keep_offline_scope_pending(self) -> None:
        approval = self.write_approval("online", offline=False)
        validate_chain(
            self.candidate,
            approval / "RELEASE-APPROVAL.json",
            candidate_root=self.candidate_root,
            approval_root=approval,
        )
        with self.assertRaisesRegex(ValueError, "offline release remains NO-GO"):
            validate_chain(
                self.candidate,
                approval / "RELEASE-APPROVAL.json",
                candidate_root=self.candidate_root,
                approval_root=approval,
                require_offline=True,
            )

    def test_offline_approval_binds_both_image_inventories(self) -> None:
        approval = self.write_approval("offline")
        validate_chain(
            self.candidate,
            approval / "RELEASE-APPROVAL.json",
            candidate_root=self.candidate_root,
            approval_root=approval,
            require_offline=True,
        )

    def test_tampered_candidate_is_rejected(self) -> None:
        approval = self.write_approval("tamper")
        self.archive.write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "artifact checksum mismatch"):
            validate_chain(
                self.candidate,
                approval / "RELEASE-APPROVAL.json",
                candidate_root=self.candidate_root,
                approval_root=approval,
            )

    def test_tampered_license_payload_is_rejected(self) -> None:
        approval = self.write_approval("license-tamper")
        payload_file = next((self.license_payload_root / "platformImage" / "files").iterdir())
        payload_file.write_text("changed license text\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "license payload file (?:size|checksum) mismatch"):
            validate_chain(
                self.candidate,
                approval / "RELEASE-APPROVAL.json",
                candidate_root=self.candidate_root,
                approval_root=approval,
            )

    def test_no_go_approval_cannot_be_generated(self) -> None:
        result = self.run_script(
            RELEASE_DIR / "write-release-approval.py",
            "--candidate", self.candidate,
            "--candidate-root", self.candidate_root,
            "--output-dir", self.root / "no-go",
            "--platform-distribution-approved", "false",
            "--platform-license-complete", "false",
            "--mysql-offline-approved", "false",
            "--mysql-license-complete", "false",
            "--reviewed-by", "authorized-reviewer",
            "--reviewed-at", "2026-08-05T01:02:03Z",
            "--workflow-repository", "example/project",
            "--workflow-run-id", "200",
            "--workflow-run-attempt", "1",
            "--workflow-ref", "refs/heads/main",
            "--workflow-sha", self.revision,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("platform distribution remains NO-GO", result.stderr)

    def test_no_go_marker_in_approval_artifact_is_rejected(self) -> None:
        approval = self.write_approval("marker")
        (approval / "NO-GO.md").write_text("not approved\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "NO-GO marker"):
            validate_chain(
                self.candidate,
                approval / "RELEASE-APPROVAL.json",
                candidate_root=self.candidate_root,
                approval_root=approval,
            )

    def test_spdx_only_license_inventory_is_rejected(self) -> None:
        approval = self.write_approval("spdx-only")
        files = approval / "licenses" / "platformImage" / "files"
        files.rename(approval / "licenses" / "platformImage" / "omitted-license-files")
        with self.assertRaisesRegex(
            ValueError,
            "license payload (?:root contains|file is missing)",
        ):
            validate_chain(
                self.candidate,
                approval / "RELEASE-APPROVAL.json",
                candidate_root=self.candidate_root,
                approval_root=approval,
            )


if __name__ == "__main__":
    unittest.main()
