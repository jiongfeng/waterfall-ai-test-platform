#!/usr/bin/env python3
"""Create a candidate-bound approval artifact after protected review."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from release_chain import (
    file_record,
    sha256,
    validate_approval,
    validate_candidate,
    write_json,
)


def parse_bool(value: str) -> bool:
    if value not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return value == "true"


def decision(value: bool, evidence: str | None, field: str) -> dict[str, object]:
    return {field: value, "evidence": evidence if value else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--platform-distribution-approved", type=parse_bool, required=True)
    parser.add_argument("--platform-distribution-evidence")
    parser.add_argument("--platform-license-complete", type=parse_bool, required=True)
    parser.add_argument("--platform-license-evidence")
    parser.add_argument("--mysql-offline-approved", type=parse_bool, required=True)
    parser.add_argument("--mysql-offline-evidence")
    parser.add_argument("--mysql-license-complete", type=parse_bool, required=True)
    parser.add_argument("--mysql-license-evidence")
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--workflow-run-attempt", type=int, required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    args = parser.parse_args()

    candidate = validate_candidate(args.candidate, args.candidate_root)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    policy_path = output / "third-party-images.approved.json"
    license_root = output / "licenses"
    review_path = license_root / "LICENSE-REVIEW.json"

    platform_spdx = args.candidate_root / candidate["artifacts"]["sboms"]["platform.spdx.json"]["path"]
    platform_payload_manifest = args.candidate_root / candidate["artifacts"]["licensePayloads"]["platformImage"]["path"]
    platform_component = license_root / "platformImage"
    shutil.copytree(platform_payload_manifest.parent, platform_component)
    platform_inventory = platform_component / "final-image.spdx.json"
    shutil.copyfile(platform_spdx, platform_inventory)

    inventories = {
        "platformImage": {
            "spdx": file_record(platform_inventory, platform_inventory.relative_to(output).as_posix()),
            "licenseFilesManifest": file_record(
                platform_component / "LICENSE-FILES.json",
                (platform_component / "LICENSE-FILES.json").relative_to(output).as_posix(),
            ),
        }
    }
    if args.mysql_offline_approved:
        mysql_spdx = args.candidate_root / candidate["artifacts"]["sboms"]["mysql.spdx.json"]["path"]
        mysql_payload_manifest = args.candidate_root / candidate["artifacts"]["licensePayloads"]["mysqlImage"]["path"]
        mysql_component = license_root / "mysqlImage"
        shutil.copytree(mysql_payload_manifest.parent, mysql_component)
        mysql_inventory = mysql_component / "final-image.spdx.json"
        shutil.copyfile(mysql_spdx, mysql_inventory)
        inventories["mysqlImage"] = {
            "spdx": file_record(mysql_inventory, mysql_inventory.relative_to(output).as_posix()),
            "licenseFilesManifest": file_record(
                mysql_component / "LICENSE-FILES.json",
                (mysql_component / "LICENSE-FILES.json").relative_to(output).as_posix(),
            ),
        }

    reviewed_at = args.reviewed_at
    reviewed_by = args.reviewed_by
    policy = {
        "schemaVersion": 1,
        "platformImageDistribution": {
            "approved": args.platform_distribution_approved,
            "evidence": args.platform_distribution_evidence if args.platform_distribution_approved else None,
            "reviewedBy": reviewed_by if args.platform_distribution_approved else None,
            "reviewedAt": reviewed_at if args.platform_distribution_approved else None,
        },
        "images": {
            "mysql": {
                "reference": candidate["mysqlImage"],
                "parentIndexReference": candidate["mysqlParentIndex"],
                "platform": "linux/amd64",
                "licenseExpression": "GPL-2.0-only",
                "offlineRedistribution": {
                    "approved": args.mysql_offline_approved,
                    "evidence": args.mysql_offline_evidence if args.mysql_offline_approved else None,
                    "reviewedBy": reviewed_by if args.mysql_offline_approved else None,
                    "reviewedAt": reviewed_at if args.mysql_offline_approved else None,
                },
            }
        },
    }
    write_json(policy_path, policy)
    review = {
        "schemaVersion": 1,
        "platformImage": {
            "reference": f"{candidate['targetImage']}@{candidate['platformDigest']}"
            if args.platform_license_complete else None,
            "complete": args.platform_license_complete,
            "evidence": args.platform_license_evidence if args.platform_license_complete else None,
        },
        "mysqlImage": {
            "reference": candidate["mysqlImage"] if args.mysql_license_complete else None,
            "complete": args.mysql_license_complete,
            "evidence": args.mysql_license_evidence if args.mysql_license_complete else None,
        },
    }
    write_json(review_path, review)
    approval_path = output / "RELEASE-APPROVAL.json"
    approval = {
        "schemaVersion": 1,
        "kind": "waterfall-ai-test-platform-release-approval",
        "candidateManifestSha256": sha256(args.candidate),
        "candidate": {
            key: candidate[key]
            for key in (
                "version", "revision", "targetImage", "platformDigest", "platformConfigDigest",
                "mysqlImage", "mysqlParentIndex", "mysqlConfigDigest"
            )
        },
        "decisions": {
            "platformDistribution": decision(
                args.platform_distribution_approved, args.platform_distribution_evidence, "approved"
            ),
            "platformLicense": decision(
                args.platform_license_complete, args.platform_license_evidence, "complete"
            ),
            "mysqlOfflineRedistribution": decision(
                args.mysql_offline_approved, args.mysql_offline_evidence, "approved"
            ),
            "mysqlLicense": decision(
                args.mysql_license_complete, args.mysql_license_evidence, "complete"
            ),
        },
        "review": {
            "environment": "release-legal",
            "reviewedBy": reviewed_by,
            "reviewedAt": reviewed_at,
            "workflow": {
                "name": "approve-release.yml",
                "repository": args.workflow_repository,
                "runId": args.workflow_run_id,
                "runAttempt": args.workflow_run_attempt,
                "ref": args.workflow_ref,
                "sha": args.workflow_sha,
            },
        },
        "artifacts": {
            "thirdPartyManifest": file_record(
                policy_path, policy_path.relative_to(output).as_posix()
            ),
            "licenseReview": file_record(review_path, review_path.relative_to(output).as_posix()),
            "licenseInventories": inventories,
        },
    }
    write_json(approval_path, approval)
    validate_approval(approval_path, args.candidate, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
