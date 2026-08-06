#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

from license_payload import (  # noqa: E402
    resolve_link_source,
    resolve_link_target,
    selected,
    validate_payload,
)


class LicensePayloadTests(unittest.TestCase):
    def test_selection_includes_distribution_and_nested_license_directories(self) -> None:
        self.assertTrue(selected(PurePosixPath("usr/share/common-licenses/GPL-2")))
        self.assertTrue(selected(PurePosixPath("app/LICENSES/MIT.txt")))
        self.assertTrue(selected(PurePosixPath("node_modules/pkg/LICENSE (MIT)")))
        self.assertTrue(selected(PurePosixPath("usr/share/doc/pkg/copyright")))
        self.assertTrue(selected(PurePosixPath("etc/legal/THIRD_PARTY.txt")))
        self.assertTrue(selected(PurePosixPath("etc/legal")))
        self.assertFalse(selected(PurePosixPath("app/package.json")))

    def test_selected_symlink_targets_are_resolved_safely(self) -> None:
        target = resolve_link_target(
            PurePosixPath("usr/share/doc/git/contrib/subtree/COPYING"),
            "../../../../common-licenses/GPL-2",
            hardlink=False,
        )
        self.assertEqual(target, "/usr/share/common-licenses/GPL-2")
        resolved = resolve_link_source(
            "/licenses/COPYING",
            {"/licenses/COPYING": "/usr/share/common-licenses/GPL-2"},
            {"/usr/share/common-licenses/GPL-2"},
        )
        self.assertEqual(resolved, "/usr/share/common-licenses/GPL-2")

    def test_selected_symlink_escape_and_cycle_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "escapes the image root"):
            resolve_link_target(
                PurePosixPath("LICENSE"),
                "../../host-secret",
                hardlink=False,
            )
        with self.assertRaisesRegex(ValueError, "contains a cycle"):
            resolve_link_source(
                "/licenses/A",
                {"/licenses/A": "/licenses/B", "/licenses/B": "/licenses/A"},
                set(),
            )

    def test_manifest_uses_safe_hashed_artifact_name_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = "/app/LICENSES/许可证 (MIT).txt"
            artifact_name = hashlib.sha256(source_path.encode()).hexdigest() + ".license"
            files = root / "files"
            files.mkdir()
            content = b"fixture license\n"
            artifact = files / artifact_name
            artifact.write_bytes(content)
            reference = "ghcr.io/example/project@sha256:" + "a" * 64
            config_digest = "sha256:" + "b" * 64
            manifest = {
                "schemaVersion": 1,
                "kind": "waterfall-ai-test-platform-final-image-license-files",
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
            manifest_path = root / "LICENSE-FILES.json"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            validate_payload(manifest_path, root, reference, config_digest)
            artifact.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(ValueError, "license payload file size mismatch"):
                validate_payload(manifest_path, root, reference, config_digest)


if __name__ == "__main__":
    unittest.main()
