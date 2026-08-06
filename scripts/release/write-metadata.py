#!/usr/bin/env python3
"""Create canonical release metadata and an untrusted assembly manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest_from_reference(reference: str) -> str:
    return reference.rsplit("@", 1)[1]


def runtime_reference(name: str, source_reference: str, bundle_type: str) -> str:
    if bundle_type == "online":
        return source_reference
    digest_hex = digest_from_reference(source_reference).split(":", 1)[1]
    return f"playwright-test-platform.local/{name}:sha256-{digest_hex}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_record(path: Path | None, relative_path: str | None) -> tuple[str | None, str | None]:
    if path is None:
        return None, None
    return relative_path, sha256(path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assembly-output", type=Path, required=True)
    parser.add_argument("--environment-output", type=Path, required=True)
    parser.add_argument("--bundle-type", choices=("online", "offline"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--platform-image", required=True)
    parser.add_argument("--platform-config-digest", required=True)
    parser.add_argument("--mysql-image", required=True)
    parser.add_argument("--mysql-config-digest", required=True)
    parser.add_argument("--platform-archive", type=Path)
    parser.add_argument("--mysql-archive", type=Path)
    args = parser.parse_args()

    platform_archive, platform_archive_sha = archive_record(
        args.platform_archive, "images/platform-linux-amd64.tar.zst"
    )
    mysql_archive, mysql_archive_sha = archive_record(
        args.mysql_archive, "images/mysql-linux-amd64.tar.zst"
    )
    metadata = {
        "schemaVersion": 1,
        "project": "playwright-test-platform",
        "version": args.version,
        "tag": args.tag,
        "revision": args.revision,
        "deploymentContractVersion": 1,
        "sourceUrl": args.source_url,
        "sourceDateEpoch": args.source_date_epoch,
        "architecture": "linux/amd64",
        "bundleType": args.bundle_type,
        "images": {
            "platform": {
                "reference": args.platform_image,
                "digest": digest_from_reference(args.platform_image),
                "configDigest": args.platform_config_digest,
                "runtimeReference": runtime_reference("platform", args.platform_image, args.bundle_type),
                "archive": platform_archive,
                "archiveSha256": platform_archive_sha,
            },
            "mysql": {
                "reference": args.mysql_image,
                "digest": digest_from_reference(args.mysql_image),
                "configDigest": args.mysql_config_digest,
                "runtimeReference": runtime_reference("mysql", args.mysql_image, args.bundle_type),
                "archive": mysql_archive,
                "archiveSha256": mysql_archive_sha,
            },
        },
        "artifacts": {
            "checksums": "SHA256SUMS",
            "sbomDirectory": "sbom",
            "assemblyManifest": "assembly/bundle-manifest.json",
        },
    }
    write_json(args.output, metadata)
    args.environment_output.write_text(
        "PLATFORM_IMAGE=" + metadata["images"]["platform"]["runtimeReference"] + "\n"
        "MYSQL_IMAGE=" + metadata["images"]["mysql"]["runtimeReference"] + "\n",
        encoding="utf-8",
    )

    subjects = [
        {
            "name": args.platform_image.split("@", 1)[0],
            "digest": {"sha256": digest_from_reference(args.platform_image).split(":", 1)[1]},
        },
        {
            "name": args.platform_image.split("@", 1)[0] + "#config",
            "digest": {"sha256": args.platform_config_digest.split(":", 1)[1]},
        },
        {
            "name": args.mysql_image.split("@", 1)[0],
            "digest": {"sha256": digest_from_reference(args.mysql_image).split(":", 1)[1]},
        },
        {
            "name": args.mysql_image.split("@", 1)[0] + "#config",
            "digest": {"sha256": args.mysql_config_digest.split(":", 1)[1]},
        },
    ]
    if args.platform_archive:
        subjects.append({"name": platform_archive, "digest": {"sha256": platform_archive_sha}})
    if args.mysql_archive:
        subjects.append({"name": mysql_archive, "digest": {"sha256": mysql_archive_sha}})
    assembly = {
        "schemaVersion": 1,
        "kind": "playwright-test-platform-release-bundle-assembly",
        "bundleType": args.bundle_type,
        "tag": args.tag,
        "revision": args.revision,
        "sourceUrl": args.source_url,
        "architecture": "linux/amd64",
        "subjects": subjects,
    }
    write_json(args.assembly_output, assembly)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
