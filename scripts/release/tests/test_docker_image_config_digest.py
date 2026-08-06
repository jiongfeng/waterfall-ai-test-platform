#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

from docker_image_config_digest import (  # noqa: E402
    extract_config_digest_from_archive,
    resolve_config_digest,
    verify_container_identity,
)


CONFIG = "sha256:" + "a" * 64
MANIFEST = "sha256:" + "b" * 64


def tar_bytes(files: dict[str, bytes]) -> io.BytesIO:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    output.seek(0)
    return output


class DockerImageConfigDigestTests(unittest.TestCase):
    def test_classic_store_uses_semantic_config_id(self) -> None:
        self.assertEqual(resolve_config_digest("image", inspected={"Id": CONFIG}), CONFIG)

    def test_containerd_store_uses_valid_config_annotation(self) -> None:
        inspected = {
            "Id": MANIFEST,
            "Descriptor": {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": MANIFEST,
                "annotations": {"config.digest": CONFIG},
            },
        }
        resolver = mock.Mock(return_value=CONFIG)
        self.assertEqual(
            resolve_config_digest("image", inspected=inspected, save_resolver=resolver),
            CONFIG,
        )
        resolver.assert_called_once_with(MANIFEST, MANIFEST)

    def test_missing_annotation_uses_save_fallback(self) -> None:
        inspected = {
            "Id": MANIFEST,
            "Descriptor": {
                "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "digest": MANIFEST,
            },
        }
        resolver = mock.Mock(return_value=CONFIG)
        self.assertEqual(
            resolve_config_digest("image", inspected=inspected, save_resolver=resolver),
            CONFIG,
        )
        resolver.assert_called_once_with(MANIFEST, MANIFEST)

    def test_malformed_or_inconsistent_descriptor_is_rejected(self) -> None:
        base = {
            "Id": MANIFEST,
            "Descriptor": {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": MANIFEST,
                "annotations": {"config.digest": CONFIG},
            },
        }
        invalid_annotation = json.loads(json.dumps(base))
        invalid_annotation["Descriptor"]["annotations"]["config.digest"] = "../../secret"
        with self.assertRaisesRegex(ValueError, "annotation is invalid"):
            resolve_config_digest("image", inspected=invalid_annotation)
        inconsistent = json.loads(json.dumps(base))
        inconsistent["Id"] = "sha256:" + "c" * 64
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            resolve_config_digest("image", inspected=inconsistent)
        same_digest = json.loads(json.dumps(base))
        same_digest["Descriptor"]["annotations"]["config.digest"] = MANIFEST
        with self.assertRaisesRegex(ValueError, "equals its manifest"):
            resolve_config_digest("image", inspected=same_digest)
        index = json.loads(json.dumps(base))
        index["Descriptor"]["mediaType"] = "application/vnd.oci.image.index.v1+json"
        with self.assertRaisesRegex(ValueError, "multi-platform index"):
            resolve_config_digest("image", inspected=index)
        forged = json.loads(json.dumps(base))
        resolver = mock.Mock(return_value="sha256:" + "d" * 64)
        with self.assertRaisesRegex(ValueError, "inconsistent with docker save"):
            resolve_config_digest("image", inspected=forged, save_resolver=resolver)

    def test_container_identity_is_store_aware_and_tag_independent(self) -> None:
        classic = {"Image": CONFIG}
        verify_container_identity(
            "container",
            inspected=classic,
            expected_manifest=MANIFEST,
            expected_config=CONFIG,
        )
        with self.assertRaisesRegex(ValueError, "classic Docker container config digest mismatch"):
            verify_container_identity(
                "container",
                inspected={"Image": "sha256:" + "d" * 64},
                expected_manifest=MANIFEST,
                expected_config=CONFIG,
            )
        child_manifest = "sha256:" + "c" * 64
        containerd = {
            "Image": MANIFEST,
            "Config": {"Image": "mutable:tag"},
            "ImageManifestDescriptor": {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": child_manifest,
            },
        }
        verify_container_identity(
            "container",
            inspected=containerd,
            expected_manifest=child_manifest,
            expected_config=CONFIG,
        )
        with self.assertRaisesRegex(ValueError, "manifest digest mismatch"):
            verify_container_identity(
                "container",
                inspected=containerd,
                expected_manifest=MANIFEST,
                expected_config=CONFIG,
            )
        annotated = json.loads(json.dumps(containerd))
        annotated["ImageManifestDescriptor"]["annotations"] = {
            "config.digest": "sha256:" + "d" * 64
        }
        with self.assertRaisesRegex(ValueError, "annotation mismatch"):
            verify_container_identity(
                "container",
                inspected=annotated,
                expected_manifest=child_manifest,
                expected_config=CONFIG,
            )
        annotated["ImageManifestDescriptor"]["annotations"]["config.digest"] = "bad"
        with self.assertRaisesRegex(ValueError, "annotation is invalid"):
            verify_container_identity(
                "container",
                inspected=annotated,
                expected_manifest=child_manifest,
                expected_config=CONFIG,
            )

    def test_oci_save_fallback_binds_descriptor_blob(self) -> None:
        manifest_value = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": CONFIG, "size": 2},
            "layers": [{"digest": "sha256:" + "c" * 64, "size": 1}],
        }
        content = json.dumps(manifest_value, separators=(",", ":")).encode()
        descriptor = "sha256:" + hashlib.sha256(content).hexdigest()
        config_content = b"{}"
        config_digest = "sha256:" + hashlib.sha256(config_content).hexdigest()
        manifest_value["config"]["digest"] = config_digest
        content = json.dumps(manifest_value, separators=(",", ":")).encode()
        descriptor = "sha256:" + hashlib.sha256(content).hexdigest()
        archive = tar_bytes({
            f"blobs/sha256/{descriptor[7:]}": content,
            f"blobs/sha256/{config_digest[7:]}": config_content,
        })
        self.assertEqual(extract_config_digest_from_archive(archive, descriptor), config_digest)

    def test_legacy_save_fallback_rejects_ambiguity_and_unsafe_config(self) -> None:
        config_content = b'{"architecture":"amd64"}'
        config_hex = hashlib.sha256(config_content).hexdigest()
        manifest = json.dumps([{"Config": f"{config_hex}.json", "RepoTags": ["x:y"]}]).encode()
        archive = tar_bytes({f"{config_hex}.json": config_content, "manifest.json": manifest})
        self.assertEqual(
            extract_config_digest_from_archive(archive, MANIFEST),
            "sha256:" + config_hex,
        )
        nested_name = f"blobs/sha256/{config_hex}"
        nested_manifest = json.dumps([
            {"Config": nested_name, "RepoTags": ["x:y"]}
        ]).encode()
        nested_archive = tar_bytes({
            nested_name: config_content,
            "manifest.json": nested_manifest,
        })
        self.assertEqual(
            extract_config_digest_from_archive(nested_archive, MANIFEST),
            "sha256:" + config_hex,
        )
        ambiguous = tar_bytes({"manifest.json": json.dumps([
            {"Config": f"{config_hex}.json"}, {"Config": f"{config_hex}.json"}
        ]).encode()})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            extract_config_digest_from_archive(ambiguous, MANIFEST)
        unsafe = tar_bytes({"manifest.json": b'[{"Config":"../secret.json"}]'})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            extract_config_digest_from_archive(unsafe, MANIFEST)
        duplicate = tar_bytes({
            f"{config_hex}.json": config_content,
            "manifest.json": manifest,
        })
        # tar_bytes cannot express duplicate dictionary keys, so append a duplicate member.
        duplicate = io.BytesIO()
        with tarfile.open(fileobj=duplicate, mode="w") as archive:
            for _ in range(2):
                member = tarfile.TarInfo(f"{config_hex}.json")
                member.size = len(config_content)
                archive.addfile(member, io.BytesIO(config_content))
            member = tarfile.TarInfo("manifest.json")
            member.size = len(manifest)
            archive.addfile(member, io.BytesIO(manifest))
        duplicate.seek(0)
        with self.assertRaisesRegex(ValueError, "duplicate config blob"):
            extract_config_digest_from_archive(duplicate, MANIFEST)


if __name__ == "__main__":
    unittest.main()
