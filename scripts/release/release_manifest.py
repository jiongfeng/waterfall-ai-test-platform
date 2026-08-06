#!/usr/bin/env python3
"""Create and verify the signed, candidate-bound public Release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

from release_chain import load_json, sha256, validate_chain, write_json


MANIFEST_NAME = "RELEASE-MANIFEST.json"
CHECKSUM_NAME = "RELEASE-ASSET-SHA256SUMS"
KIND = "playwright-test-platform-release-manifest"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TOP_KEYS = {
    "schemaVersion", "kind", "project", "version", "tag", "revision",
    "sourceUrl", "sourceDateEpoch", "architecture", "scope", "images",
    "authorization", "signing", "assets",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def regular(path: Path, description: str) -> None:
    require(path.is_file() and not path.is_symlink(), f"{description} must be a regular file")
    require(stat.S_ISREG(path.stat().st_mode) and path.stat().st_size > 0,
            f"{description} must not be empty")


def asset_record(path: Path) -> dict[str, Any]:
    regular(path, path.name)
    require(ASSET_RE.fullmatch(path.name) is not None, f"unsafe asset name: {path.name}")
    return {"name": path.name, "sha256": sha256(path), "size": path.stat().st_size}


def validate(path: Path, asset_root: Path | None = None) -> dict[str, Any]:
    regular(path, "release manifest")
    value = load_json(path)
    require(isinstance(value, dict) and set(value) == TOP_KEYS,
            "release manifest has missing or unknown fields")
    require(value["schemaVersion"] == 1 and value["kind"] == KIND,
            "release manifest schema or kind is invalid")
    require(value["project"] == "playwright-test-platform", "project is invalid")
    require(isinstance(value["version"], str) and VERSION_RE.fullmatch(value["version"]) is not None,
            "version is invalid")
    require(value["tag"] == f"v{value['version']}", "tag must equal v + version")
    require(isinstance(value["revision"], str) and REVISION_RE.fullmatch(value["revision"]) is not None,
            "revision is invalid")
    require(isinstance(value["sourceUrl"], str) and re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["sourceUrl"]
    ) is not None,
            "source URL is invalid")
    require(isinstance(value["sourceDateEpoch"], int) and value["sourceDateEpoch"] > 0,
            "sourceDateEpoch is invalid")
    require(value["architecture"] == "linux/amd64" and value["scope"] == "online-only",
            "only the reviewed online linux/amd64 scope may be published")

    images = value["images"]
    require(isinstance(images, dict) and set(images) == {"platform", "mysql"},
            "images contract is invalid")
    for name in ("platform", "mysql"):
        image = images[name]
        require(isinstance(image, dict) and set(image) == {
            "reference", "manifestDigest", "configDigest", "redistributed"
        }, f"{name} image contract is invalid")
        require(isinstance(image["reference"], str) and "@sha256:" in image["reference"],
                f"{name} image reference is invalid")
        require(DIGEST_RE.fullmatch(image["manifestDigest"]) is not None,
                f"{name} manifest digest is invalid")
        require(DIGEST_RE.fullmatch(image["configDigest"]) is not None,
                f"{name} config digest is invalid")
    require(images["platform"]["redistributed"] is True,
            "platform image distribution must be approved")
    require(images["mysql"]["redistributed"] is False,
            "online-only release must not redistribute MySQL image bytes")

    authorization = value["authorization"]
    require(isinstance(authorization, dict) and set(authorization) == {
        "candidateManifestSha256", "approvalManifestSha256", "reviewedBy", "reviewedAt",
        "platformDistributionEvidence", "platformLicenseEvidence",
        "mysqlOfflineRedistributionApproved",
    }, "authorization contract is invalid")
    require(SHA256_RE.fullmatch(authorization["candidateManifestSha256"]) is not None,
            "candidate manifest checksum is invalid")
    require(SHA256_RE.fullmatch(authorization["approvalManifestSha256"]) is not None,
            "approval manifest checksum is invalid")
    require(isinstance(authorization["reviewedBy"], str) and authorization["reviewedBy"],
            "reviewedBy is invalid")
    require(isinstance(authorization["reviewedAt"], str) and authorization["reviewedAt"].endswith("Z"),
            "reviewedAt is invalid")
    for field in ("platformDistributionEvidence", "platformLicenseEvidence"):
        require(isinstance(authorization[field], str) and
                (authorization[field].startswith("https://") or authorization[field].startswith("urn:")),
                f"{field} is invalid")
    require(authorization["mysqlOfflineRedistributionApproved"] is False,
            "online-only release must not claim MySQL redistribution approval")

    signing = value["signing"]
    require(isinstance(signing, dict) and set(signing) == {"algorithm", "publicKeySha256"},
            "signing contract is invalid")
    require(signing["algorithm"] == "minisign-ed25519-blake2b",
            "signing algorithm is invalid")
    require(SHA256_RE.fullmatch(signing["publicKeySha256"]) is not None,
            "public key checksum is invalid")

    assets = value["assets"]
    require(isinstance(assets, list) and assets, "assets must be a non-empty list")
    names: set[str] = set()
    for item in assets:
        require(isinstance(item, dict) and set(item) == {"name", "sha256", "size"},
                "asset record is invalid")
        require(isinstance(item["name"], str) and ASSET_RE.fullmatch(item["name"]) is not None,
                "asset name is invalid")
        require(item["name"] not in {MANIFEST_NAME, f"{MANIFEST_NAME}.minisig"},
                "manifest must not list itself")
        require(item["name"] not in names, "duplicate asset name")
        names.add(item["name"])
        require(SHA256_RE.fullmatch(item["sha256"]) is not None,
                f"asset checksum is invalid: {item['name']}")
        require(isinstance(item["size"], int) and item["size"] > 0,
                f"asset size is invalid: {item['name']}")
        if asset_root is not None:
            asset_path = asset_root / item["name"]
            regular(asset_path, item["name"])
            require(asset_path.stat().st_size == item["size"], f"asset size mismatch: {item['name']}")
            require(sha256(asset_path) == item["sha256"], f"asset checksum mismatch: {item['name']}")
    require(CHECKSUM_NAME in names, f"assets must include {CHECKSUM_NAME}")
    require(any(name.endswith("-online.tar.zst") for name in names), "online bundle is missing")
    require(not any(name.endswith("-offline.tar.zst") for name in names),
            "online-only release cannot include an offline bundle")
    return value


def write(args: argparse.Namespace) -> None:
    candidate, approval = validate_chain(
        args.candidate, args.approval,
        candidate_root=args.candidate_root,
        approval_root=args.approval_root,
        expected_version=args.version,
        expected_revision=args.revision,
        expected_target_image=args.target_image,
    )
    require(approval["decisions"]["mysqlOfflineRedistribution"]["approved"] is False,
            "this public release is online-only")
    require(approval["decisions"]["mysqlLicense"]["complete"] is False,
            "online-only release must not claim a MySQL final-image license review")
    regular(args.public_key, "Minisign public key")
    asset_paths = sorted(
        (path for path in args.asset_root.iterdir()
         if path.name not in {MANIFEST_NAME, f"{MANIFEST_NAME}.minisig"}),
        key=lambda path: path.name,
    )
    require(asset_paths, "release asset directory is empty")
    document = {
        "schemaVersion": 1,
        "kind": KIND,
        "project": "playwright-test-platform",
        "version": candidate["version"],
        "tag": f"v{candidate['version']}",
        "revision": candidate["revision"],
        "sourceUrl": candidate["sourceUrl"],
        "sourceDateEpoch": candidate["sourceDateEpoch"],
        "architecture": candidate["architecture"],
        "scope": "online-only",
        "images": {
            "platform": {
                "reference": f"{candidate['targetImage']}@{candidate['platformDigest']}",
                "manifestDigest": candidate["platformDigest"],
                "configDigest": candidate["platformConfigDigest"],
                "redistributed": True,
            },
            "mysql": {
                "reference": candidate["mysqlImage"],
                "manifestDigest": candidate["mysqlImage"].rsplit("@", 1)[1],
                "configDigest": candidate["mysqlConfigDigest"],
                "redistributed": False,
            },
        },
        "authorization": {
            "candidateManifestSha256": sha256(args.candidate),
            "approvalManifestSha256": sha256(args.approval),
            "reviewedBy": approval["review"]["reviewedBy"],
            "reviewedAt": approval["review"]["reviewedAt"],
            "platformDistributionEvidence": approval["decisions"]["platformDistribution"]["evidence"],
            "platformLicenseEvidence": approval["decisions"]["platformLicense"]["evidence"],
            "mysqlOfflineRedistributionApproved": False,
        },
        "signing": {
            "algorithm": "minisign-ed25519-blake2b",
            "publicKeySha256": sha256(args.public_key),
        },
        "assets": [asset_record(path) for path in asset_paths],
    }
    write_json(args.output, document)
    validate(args.output, args.asset_root)


def verify_asset(args: argparse.Namespace) -> None:
    value = validate(args.manifest, args.asset_root)
    records = {item["name"]: item for item in value["assets"]}
    regular(args.asset, "release asset")
    record = records.get(args.asset.name)
    require(record is not None, "asset is not listed in the signed release manifest")
    require(sha256(args.asset) == record["sha256"] and args.asset.stat().st_size == record["size"],
            "asset does not match the signed release manifest")
    if args.expected_tag:
        require(value["tag"] == args.expected_tag, "release tag mismatch")
    if args.expected_revision:
        require(value["revision"] == args.expected_revision, "release revision mismatch")
    if args.expected_source_url:
        require(value["sourceUrl"] == args.expected_source_url, "release source URL mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    writer = commands.add_parser("write")
    writer.add_argument("--output", type=Path, required=True)
    writer.add_argument("--asset-root", type=Path, required=True)
    writer.add_argument("--candidate", type=Path, required=True)
    writer.add_argument("--candidate-root", type=Path)
    writer.add_argument("--approval", type=Path, required=True)
    writer.add_argument("--approval-root", type=Path, required=True)
    writer.add_argument("--public-key", type=Path, required=True)
    writer.add_argument("--version", required=True)
    writer.add_argument("--revision", required=True)
    writer.add_argument("--target-image", required=True)
    verifier = commands.add_parser("verify-asset")
    verifier.add_argument("--manifest", type=Path, required=True)
    verifier.add_argument("--asset-root", type=Path, required=True)
    verifier.add_argument("--asset", type=Path, required=True)
    verifier.add_argument("--expected-tag")
    verifier.add_argument("--expected-revision")
    verifier.add_argument("--expected-source-url")
    args = parser.parse_args()
    try:
        if args.command == "write":
            write(args)
        else:
            verify_asset(args)
    except (OSError, ValueError) as exc:
        print(f"release manifest validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
