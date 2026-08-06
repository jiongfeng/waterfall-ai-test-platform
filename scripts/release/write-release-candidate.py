#!/usr/bin/env python3
"""Write a canonical release-candidate manifest bound to candidate bytes."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_chain import file_record, validate_candidate, write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--platform-digest", required=True)
    parser.add_argument("--platform-config-digest", required=True)
    parser.add_argument("--platform-archive", type=Path, required=True)
    parser.add_argument("--mysql-image", required=True)
    parser.add_argument("--mysql-parent-index", required=True)
    parser.add_argument("--mysql-config-digest", required=True)
    parser.add_argument("--sbom-dir", type=Path, required=True)
    parser.add_argument("--license-payload-dir", type=Path, required=True)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--workflow-run-attempt", type=int, required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    args = parser.parse_args()

    root = args.output.parent
    archive_relative = args.platform_archive.relative_to(root).as_posix()
    sboms = {}
    for name in ("platform.spdx.json", "platform.cdx.json", "mysql.spdx.json", "mysql.cdx.json"):
        path = args.sbom_dir / name
        sboms[name] = file_record(path, path.relative_to(root).as_posix())
    license_payloads = {}
    for name in ("platformImage", "mysqlImage"):
        path = args.license_payload_dir / name / "LICENSE-FILES.json"
        license_payloads[name] = file_record(path, path.relative_to(root).as_posix())
    document = {
        "schemaVersion": 1,
        "kind": "waterfall-ai-test-platform-release-candidate",
        "version": args.version,
        "revision": args.revision,
        "sourceUrl": args.source_url,
        "sourceDateEpoch": args.source_date_epoch,
        "architecture": "linux/amd64",
        "targetImage": args.target_image,
        "platformDigest": args.platform_digest,
        "platformConfigDigest": args.platform_config_digest,
        "mysqlImage": args.mysql_image,
        "mysqlParentIndex": args.mysql_parent_index,
        "mysqlConfigDigest": args.mysql_config_digest,
        "artifacts": {
            "platformArchive": file_record(args.platform_archive, archive_relative),
            "sboms": sboms,
            "licensePayloads": license_payloads,
        },
        "workflow": {
            "name": "prepare-release.yml",
            "repository": args.workflow_repository,
            "runId": args.workflow_run_id,
            "runAttempt": args.workflow_run_attempt,
            "ref": args.workflow_ref,
            "sha": args.workflow_sha,
        },
    }
    write_json(args.output, document)
    validate_candidate(args.output, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
