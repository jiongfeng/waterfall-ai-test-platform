#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

from verify_release_assets import validate  # noqa: E402


class ReleaseAssetSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.asset = self.root / "playwright-test-platform-1.2.3-beta.1-linux-amd64-online.tar.zst"
        self.asset.write_bytes(b"bundle\n")
        checksum = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        (self.root / "RELEASE-ASSET-SHA256SUMS").write_text(
            f"{checksum}  ./{self.asset.name}\n",
            encoding="utf-8",
        )
        checksum_root = self.root / "RELEASE-ASSET-SHA256SUMS"
        assets = []
        for path in (checksum_root, self.asset):
            assets.append({
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            })
        manifest = {
            "schemaVersion": 1,
            "kind": "playwright-test-platform-release-manifest",
            "project": "playwright-test-platform",
            "version": "1.2.3-beta.1",
            "tag": "v1.2.3-beta.1",
            "revision": "a" * 40,
            "sourceUrl": "https://github.com/jiongfeng/playwright-test-platform",
            "sourceDateEpoch": 1700000000,
            "architecture": "linux/amd64",
            "scope": "online-only",
            "images": {
                "platform": {
                    "reference": "ghcr.io/jiongfeng/playwright-test-platform@sha256:" + "b" * 64,
                    "manifestDigest": "sha256:" + "b" * 64,
                    "configDigest": "sha256:" + "c" * 64,
                    "redistributed": True,
                },
                "mysql": {
                    "reference": "docker.io/library/mysql@sha256:" + "d" * 64,
                    "manifestDigest": "sha256:" + "d" * 64,
                    "configDigest": "sha256:" + "e" * 64,
                    "redistributed": False,
                },
            },
            "authorization": {
                "candidateManifestSha256": "f" * 64,
                "approvalManifestSha256": "1" * 64,
                "reviewedBy": "reviewer",
                "reviewedAt": "2026-08-06T00:00:00Z",
                "platformDistributionEvidence": "urn:test:distribution",
                "platformLicenseEvidence": "urn:test:license",
                "mysqlOfflineRedistributionApproved": False,
            },
            "signing": {
                "algorithm": "minisign-ed25519-blake2b",
                "publicKeySha256": "2" * 64,
            },
            "assets": assets,
        }
        (self.root / "RELEASE-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (self.root / "RELEASE-MANIFEST.json.minisig").write_text(
            "untrusted comment: signature\ntrusted comment: test\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_regular_asset_set_passes(self) -> None:
        validate(self.root)

    def test_unexpected_asset_is_rejected(self) -> None:
        (self.root / "unreviewed.txt").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"unexpected=\['unreviewed.txt'\]"):
            validate(self.root)

    def test_symlink_asset_is_rejected(self) -> None:
        link = self.root / "unreviewed.txt"
        try:
            link.symlink_to(self.asset.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            validate(self.root)


if __name__ == "__main__":
    unittest.main()
