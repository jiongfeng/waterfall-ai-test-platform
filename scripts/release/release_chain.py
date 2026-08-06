#!/usr/bin/env python3
"""Validate the immutable candidate -> human approval -> release chain.

The repository policy files intentionally remain NO-GO defaults. A release is
authorized only by a separately signed approval artifact produced behind the
``release-legal`` GitHub environment. This module validates the content and
hash bindings; Minisign signatures are verified by the calling workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from license_payload import validate_payload


VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_NAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+$")
IMAGE_REFERENCE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
SOURCE_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+$")
WORKFLOW_REF_RE = re.compile(r"^refs/(?:heads|tags)/[^\s]+$")
EVIDENCE_RE = re.compile(r"^(?:https://|urn:)[^\s]+$")

CANDIDATE_KEYS = {
    "schemaVersion",
    "kind",
    "version",
    "revision",
    "sourceUrl",
    "sourceDateEpoch",
    "architecture",
    "targetImage",
    "platformDigest",
    "platformConfigDigest",
    "mysqlImage",
    "mysqlParentIndex",
    "mysqlConfigDigest",
    "artifacts",
    "workflow",
}
APPROVAL_KEYS = {
    "schemaVersion",
    "kind",
    "candidateManifestSha256",
    "candidate",
    "decisions",
    "review",
    "artifacts",
}
SBOM_NAMES = {
    "platform.spdx.json",
    "platform.cdx.json",
    "mysql.spdx.json",
    "mysql.cdx.json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def file_record(path: Path, relative: str) -> dict[str, str]:
    require(path.is_file() and not path.is_symlink(), f"artifact must be a regular file: {path}")
    require(path.stat().st_size > 0, f"artifact must not be empty: {path}")
    return {"path": relative, "sha256": sha256(path)}


def validate_file_record(name: str, value: object) -> dict[str, str]:
    require(isinstance(value, dict), f"{name} must be an object")
    require(set(value) == {"path", "sha256"}, f"{name} has missing or unknown fields")
    path = value["path"]
    checksum = value["sha256"]
    require(isinstance(path, str) and path and not path.startswith("/") and ".." not in Path(path).parts,
            f"{name}.path must be a safe relative path")
    require(isinstance(checksum, str) and SHA256_RE.fullmatch(checksum) is not None,
            f"{name}.sha256 is invalid")
    return value


def verify_file_record(root: Path, name: str, value: object) -> None:
    record = validate_file_record(name, value)
    path = root / record["path"]
    require(path.is_file() and not path.is_symlink(), f"missing regular artifact: {record['path']}")
    require(sha256(path) == record["sha256"], f"artifact checksum mismatch: {record['path']}")


def validate_workflow(value: object, *, expected_name: str, revision_must_equal: str | None = None) -> dict[str, Any]:
    require(isinstance(value, dict), "workflow must be an object")
    require(set(value) == {"name", "repository", "runId", "runAttempt", "ref", "sha"},
            "workflow has missing or unknown fields")
    require(value["name"] == expected_name, f"workflow.name must be {expected_name}")
    require(isinstance(value["repository"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]),
            "workflow.repository is invalid")
    require(isinstance(value["runId"], int) and value["runId"] > 0, "workflow.runId must be positive")
    require(isinstance(value["runAttempt"], int) and value["runAttempt"] > 0,
            "workflow.runAttempt must be positive")
    require(isinstance(value["ref"], str) and WORKFLOW_REF_RE.fullmatch(value["ref"]),
            "workflow.ref is invalid")
    require(isinstance(value["sha"], str) and REVISION_RE.fullmatch(value["sha"]),
            "workflow.sha is invalid")
    if revision_must_equal is not None:
        require(value["sha"] == revision_must_equal,
                "candidate must be built by workflow code at the candidate revision")
    return value


def validate_candidate(path: Path, root: Path | None = None) -> dict[str, Any]:
    value = load_json(path)
    require(isinstance(value, dict), "candidate manifest must be an object")
    require(set(value) == CANDIDATE_KEYS, "candidate manifest has missing or unknown fields")
    require(value["schemaVersion"] == 1, "candidate schemaVersion must be 1")
    require(value["kind"] == "playwright-test-platform-release-candidate",
            "candidate kind is invalid")
    require(isinstance(value["version"], str) and VERSION_RE.fullmatch(value["version"]),
            "candidate version is invalid")
    require(isinstance(value["revision"], str) and REVISION_RE.fullmatch(value["revision"]),
            "candidate revision is invalid")
    require(isinstance(value["sourceUrl"], str) and SOURCE_RE.fullmatch(value["sourceUrl"]),
            "candidate sourceUrl is invalid")
    require(isinstance(value["sourceDateEpoch"], int) and value["sourceDateEpoch"] > 0,
            "candidate sourceDateEpoch must be positive")
    require(value["architecture"] == "linux/amd64", "candidate architecture must be linux/amd64")
    require(isinstance(value["targetImage"], str) and IMAGE_NAME_RE.fullmatch(value["targetImage"]),
            "candidate targetImage is invalid")
    require(isinstance(value["platformDigest"], str) and DIGEST_RE.fullmatch(value["platformDigest"]),
            "candidate platformDigest is invalid")
    require(isinstance(value["platformConfigDigest"], str) and DIGEST_RE.fullmatch(value["platformConfigDigest"]),
            "candidate platformConfigDigest is invalid")
    require(isinstance(value["mysqlImage"], str) and IMAGE_REFERENCE_RE.fullmatch(value["mysqlImage"]),
            "candidate mysqlImage is invalid")
    require(isinstance(value["mysqlParentIndex"], str) and IMAGE_REFERENCE_RE.fullmatch(value["mysqlParentIndex"]),
            "candidate mysqlParentIndex is invalid")
    require(value["mysqlParentIndex"].split("@", 1)[0] == value["mysqlImage"].split("@", 1)[0],
            "candidate MySQL parent index and child must use the same tagged repository name")
    require(value["mysqlParentIndex"] != value["mysqlImage"],
            "candidate MySQL parent index and child digests must differ")
    require(isinstance(value["mysqlConfigDigest"], str) and DIGEST_RE.fullmatch(value["mysqlConfigDigest"]),
            "candidate mysqlConfigDigest is invalid")
    validate_workflow(value["workflow"], expected_name="prepare-release.yml",
                      revision_must_equal=value["revision"])

    artifacts = value["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) == {
        "platformArchive", "sboms", "licensePayloads"
    },
            "candidate artifacts contract is invalid")
    validate_file_record("artifacts.platformArchive", artifacts["platformArchive"])
    sboms = artifacts["sboms"]
    require(isinstance(sboms, dict) and set(sboms) == SBOM_NAMES,
            "candidate must bind all four final-image SBOMs")
    for name, record in sboms.items():
        validate_file_record(f"artifacts.sboms.{name}", record)
    license_payloads = artifacts["licensePayloads"]
    require(isinstance(license_payloads, dict) and set(license_payloads) == {
        "platformImage", "mysqlImage"
    }, "candidate must bind both final-image license payload manifests")
    for name, record in license_payloads.items():
        validate_file_record(f"artifacts.licensePayloads.{name}", record)
    if root is not None:
        verify_file_record(root, "artifacts.platformArchive", artifacts["platformArchive"])
        for name, record in sboms.items():
            verify_file_record(root, f"artifacts.sboms.{name}", record)
        expected_payloads = {
            "platformImage": (
                f"{value['targetImage']}@{value['platformDigest']}",
                value["platformConfigDigest"],
            ),
            "mysqlImage": (value["mysqlImage"], value["mysqlConfigDigest"]),
        }
        for name, record in license_payloads.items():
            verify_file_record(root, f"artifacts.licensePayloads.{name}", record)
            manifest_path = root / record["path"]
            reference, config_digest = expected_payloads[name]
            validate_payload(
                manifest_path,
                manifest_path.parent,
                reference,
                config_digest,
            )
    return value


def validate_decision(value: object, name: str, field: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"decisions.{name} must be an object")
    require(set(value) == {field, "evidence"}, f"decisions.{name} has missing or unknown fields")
    approved = value[field]
    evidence = value["evidence"]
    require(isinstance(approved, bool), f"decisions.{name}.{field} must be a boolean")
    if approved:
        require(isinstance(evidence, str) and EVIDENCE_RE.fullmatch(evidence),
                f"decisions.{name}.evidence must be an https:// or urn: reference")
    else:
        require(evidence is None, f"unapproved decisions.{name} must leave evidence null")
    return value


def validate_approval(path: Path, candidate_path: Path, root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = validate_candidate(candidate_path)
    value = load_json(path)
    require(isinstance(value, dict), "approval manifest must be an object")
    require(set(value) == APPROVAL_KEYS, "approval manifest has missing or unknown fields")
    require(value["schemaVersion"] == 1, "approval schemaVersion must be 1")
    require(value["kind"] == "playwright-test-platform-release-approval", "approval kind is invalid")
    require(value["candidateManifestSha256"] == sha256(candidate_path),
            "approval is not bound to the exact candidate manifest bytes")
    identity = value["candidate"]
    require(isinstance(identity, dict) and set(identity) == {
        "version", "revision", "targetImage", "platformDigest", "platformConfigDigest",
        "mysqlImage", "mysqlParentIndex", "mysqlConfigDigest"
    }, "approval candidate identity is invalid")
    for key in identity:
        require(identity[key] == candidate[key], f"approval candidate.{key} mismatch")

    decisions = value["decisions"]
    require(isinstance(decisions, dict) and set(decisions) == {
        "platformDistribution", "platformLicense", "mysqlOfflineRedistribution", "mysqlLicense"
    }, "approval decisions contract is invalid")
    platform_distribution = validate_decision(
        decisions["platformDistribution"], "platformDistribution", "approved"
    )
    platform_license = validate_decision(decisions["platformLicense"], "platformLicense", "complete")
    mysql_distribution = validate_decision(
        decisions["mysqlOfflineRedistribution"], "mysqlOfflineRedistribution", "approved"
    )
    mysql_license = validate_decision(decisions["mysqlLicense"], "mysqlLicense", "complete")
    require(platform_distribution["approved"] is True,
            "platform distribution remains NO-GO")
    require(platform_license["complete"] is True,
            "platform final-image license review remains NO-GO")
    require(mysql_distribution["approved"] == mysql_license["complete"],
            "MySQL redistribution and final-image license decisions must agree")

    review = value["review"]
    require(isinstance(review, dict) and set(review) == {
        "environment", "reviewedBy", "reviewedAt", "workflow"
    }, "approval review contract is invalid")
    require(review["environment"] == "release-legal",
            "approval must come from the release-legal environment")
    require(isinstance(review["reviewedBy"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", review["reviewedBy"]),
            "approval reviewedBy is invalid")
    require(isinstance(review["reviewedAt"], str) and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", review["reviewedAt"]
    ), "approval reviewedAt must be an RFC3339 UTC timestamp")
    approval_workflow = validate_workflow(review["workflow"], expected_name="approve-release.yml")
    require(approval_workflow["sha"] == candidate["revision"],
            "approval workflow code must be the exact candidate revision")

    artifacts = value["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) == {
        "thirdPartyManifest", "licenseReview", "licenseInventories"
    }, "approval artifacts contract is invalid")
    validate_file_record("artifacts.thirdPartyManifest", artifacts["thirdPartyManifest"])
    validate_file_record("artifacts.licenseReview", artifacts["licenseReview"])
    inventories = artifacts["licenseInventories"]
    expected_inventory_names = {"platformImage"}
    if mysql_distribution["approved"]:
        expected_inventory_names.add("mysqlImage")
    require(isinstance(inventories, dict) and set(inventories) == expected_inventory_names,
            "approval license inventories do not match approved distribution scope")
    for name, inventory in inventories.items():
        require(isinstance(inventory, dict) and set(inventory) == {
            "spdx", "licenseFilesManifest"
        }, f"artifacts.licenseInventories.{name} is invalid")
        validate_file_record(f"artifacts.licenseInventories.{name}.spdx", inventory["spdx"])
        validate_file_record(
            f"artifacts.licenseInventories.{name}.licenseFilesManifest",
            inventory["licenseFilesManifest"],
        )

    if root is not None:
        if any("NO-GO" in item.name.upper() for item in root.rglob("*")):
            raise ValueError("approval artifact contains a NO-GO marker")
        verify_file_record(root, "artifacts.thirdPartyManifest", artifacts["thirdPartyManifest"])
        verify_file_record(root, "artifacts.licenseReview", artifacts["licenseReview"])
        for name, inventory in inventories.items():
            verify_file_record(
                root,
                f"artifacts.licenseInventories.{name}.spdx",
                inventory["spdx"],
            )
            verify_file_record(
                root,
                f"artifacts.licenseInventories.{name}.licenseFilesManifest",
                inventory["licenseFilesManifest"],
            )
        _validate_approval_material(root, candidate, value)
    return candidate, value


def _validate_approval_material(root: Path, candidate: dict[str, Any], approval: dict[str, Any]) -> None:
    artifacts = approval["artifacts"]
    policy = load_json(root / artifacts["thirdPartyManifest"]["path"])
    platform_decision = approval["decisions"]["platformDistribution"]
    mysql_decision = approval["decisions"]["mysqlOfflineRedistribution"]
    require(policy.get("schemaVersion") == 1, "approved third-party manifest schemaVersion must be 1")
    require(policy.get("platformImageDistribution") == {
        "approved": True,
        "evidence": platform_decision["evidence"],
        "reviewedBy": approval["review"]["reviewedBy"],
        "reviewedAt": approval["review"]["reviewedAt"],
    }, "approved platform distribution policy does not match approval")
    mysql = policy.get("images", {}).get("mysql", {})
    require(mysql.get("reference") == candidate["mysqlImage"], "approved MySQL reference mismatch")
    require(mysql.get("parentIndexReference") == candidate["mysqlParentIndex"],
            "approved MySQL parent index mismatch")
    require(mysql.get("platform") == "linux/amd64", "approved MySQL platform mismatch")
    require(mysql.get("offlineRedistribution") == {
        "approved": mysql_decision["approved"],
        "evidence": mysql_decision["evidence"],
        "reviewedBy": approval["review"]["reviewedBy"] if mysql_decision["approved"] else None,
        "reviewedAt": approval["review"]["reviewedAt"] if mysql_decision["approved"] else None,
    }, "approved MySQL redistribution policy does not match approval")

    review = load_json(root / artifacts["licenseReview"]["path"])
    expected_platform_reference = f"{candidate['targetImage']}@{candidate['platformDigest']}"
    platform_license = approval["decisions"]["platformLicense"]
    mysql_license = approval["decisions"]["mysqlLicense"]
    require(review.get("schemaVersion") == 1, "approved license review schemaVersion must be 1")
    require(review.get("platformImage") == {
        "reference": expected_platform_reference,
        "complete": True,
        "evidence": platform_license["evidence"],
    }, "approved platform license review does not match approval")
    require(review.get("mysqlImage") == {
        "reference": candidate["mysqlImage"] if mysql_license["complete"] else None,
        "complete": mysql_license["complete"],
        "evidence": mysql_license["evidence"],
    }, "approved MySQL license review does not match approval")

    platform_inventory = artifacts["licenseInventories"]["platformImage"]
    platform_sbom = candidate["artifacts"]["sboms"]["platform.spdx.json"]
    require(platform_inventory["spdx"]["sha256"] == platform_sbom["sha256"],
            "reviewed platform license inventory is not the candidate platform SPDX SBOM")
    platform_payload = candidate["artifacts"]["licensePayloads"]["platformImage"]
    require(platform_inventory["licenseFilesManifest"]["sha256"] == platform_payload["sha256"],
            "reviewed platform license files are not the candidate payload")
    platform_manifest = root / platform_inventory["licenseFilesManifest"]["path"]
    validate_payload(
        platform_manifest,
        platform_manifest.parent,
        expected_platform_reference,
        candidate["platformConfigDigest"],
    )
    if mysql_license["complete"]:
        mysql_inventory = artifacts["licenseInventories"]["mysqlImage"]
        mysql_sbom = candidate["artifacts"]["sboms"]["mysql.spdx.json"]
        require(mysql_inventory["spdx"]["sha256"] == mysql_sbom["sha256"],
                "reviewed MySQL license inventory is not the candidate MySQL SPDX SBOM")
        mysql_payload = candidate["artifacts"]["licensePayloads"]["mysqlImage"]
        require(mysql_inventory["licenseFilesManifest"]["sha256"] == mysql_payload["sha256"],
                "reviewed MySQL license files are not the candidate payload")
        mysql_manifest = root / mysql_inventory["licenseFilesManifest"]["path"]
        validate_payload(
            mysql_manifest,
            mysql_manifest.parent,
            candidate["mysqlImage"],
            candidate["mysqlConfigDigest"],
        )


def validate_chain(
    candidate_path: Path,
    approval_path: Path,
    *,
    candidate_root: Path | None = None,
    approval_root: Path | None = None,
    require_offline: bool = False,
    expected_version: str | None = None,
    expected_revision: str | None = None,
    expected_target_image: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = validate_candidate(candidate_path, candidate_root)
    approved_candidate, approval = validate_approval(approval_path, candidate_path, approval_root)
    require(candidate == approved_candidate, "candidate changed while validating approval")
    if require_offline:
        require(approval["decisions"]["mysqlOfflineRedistribution"]["approved"] is True,
                "offline release remains NO-GO")
        require(approval["decisions"]["mysqlLicense"]["complete"] is True,
                "MySQL final-image license review remains NO-GO")
    if expected_version is not None:
        require(candidate["version"] == expected_version, "candidate version mismatch")
    if expected_revision is not None:
        require(candidate["revision"] == expected_revision, "candidate revision mismatch")
    if expected_target_image is not None:
        require(candidate["targetImage"] == expected_target_image, "candidate target image mismatch")
    return candidate, approval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("approval", type=Path, nargs="?")
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--approval-root", type=Path)
    parser.add_argument("--require-offline", action="store_true")
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-target-image")
    parser.add_argument("--print-offline-status", action="store_true")
    args = parser.parse_args()
    try:
        if args.approval is None:
            validate_candidate(args.candidate, args.candidate_root)
            return 0
        _, approval = validate_chain(
            args.candidate,
            args.approval,
            candidate_root=args.candidate_root,
            approval_root=args.approval_root,
            require_offline=args.require_offline,
            expected_version=args.expected_version,
            expected_revision=args.expected_revision,
            expected_target_image=args.expected_target_image,
        )
    except (OSError, ValueError) as exc:
        print(f"release chain validation failed: {exc}", file=sys.stderr)
        return 1
    if args.print_offline_status:
        approved = approval["decisions"]["mysqlOfflineRedistribution"]["approved"]
        print("approved" if approved else "pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
