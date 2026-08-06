#!/usr/bin/env python3
"""Validate immutable third-party image inputs and redistribution approval."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise ValueError(message)


def validate(
    path: Path,
    require_platform_distribution: bool,
    require_offline_redistribution: bool,
) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read third-party image manifest {path}: {exc}")

    if document.get("schemaVersion") != 1:
        fail("third-party image manifest schemaVersion must be 1")
    platform_approval = document.get("platformImageDistribution")
    if not isinstance(platform_approval, dict):
        fail("platformImageDistribution approval is required")
    validate_approval_shape(platform_approval, "platform image distribution")
    if require_platform_distribution:
        validate_approval(platform_approval, "platform image distribution")
    mysql = document.get("images", {}).get("mysql")
    if not isinstance(mysql, dict):
        fail("third-party image manifest must define images.mysql")
    reference = mysql.get("reference")
    if not isinstance(reference, str) or not IMAGE_RE.fullmatch(reference):
        fail("images.mysql.reference must be an immutable sha256 reference")
    parent_reference = mysql.get("parentIndexReference")
    if not isinstance(parent_reference, str) or not IMAGE_RE.fullmatch(parent_reference):
        fail("images.mysql.parentIndexReference must be an immutable sha256 reference")
    if parent_reference.split("@", 1)[0] != reference.split("@", 1)[0]:
        fail("MySQL parent index and child references must use the same tagged repository name")
    if parent_reference == reference:
        fail("MySQL parent index and amd64 child digests must differ")
    if mysql.get("platform") != "linux/amd64":
        fail("images.mysql.platform must be linux/amd64")
    if not isinstance(mysql.get("licenseExpression"), str) or not mysql["licenseExpression"]:
        fail("images.mysql.licenseExpression is required")

    approval = mysql.get("offlineRedistribution")
    if not isinstance(approval, dict):
        fail("images.mysql.offlineRedistribution is required")
    validate_approval_shape(approval, "MySQL offline redistribution")
    if require_offline_redistribution:
        validate_approval(approval, "MySQL offline redistribution")
    return document


def validate_approval(approval: dict[str, object], name: str) -> None:
    if approval.get("approved") is not True:
        fail(f"{name} is not approved; keep the release blocked until reviewed legal evidence is committed")
    for field in ("evidence", "reviewedBy", "reviewedAt"):
        value = approval.get(field)
        if not isinstance(value, str) or not value.strip():
            fail(f"{name} approval requires {field}")


def validate_approval_shape(approval: dict[str, object], name: str) -> None:
    expected = {"approved", "evidence", "reviewedBy", "reviewedAt"}
    if set(approval) != expected:
        fail(f"{name} approval has missing or unknown fields")
    if not isinstance(approval.get("approved"), bool):
        fail(f"{name} approved must be a boolean")
    if approval["approved"]:
        validate_approval(approval, name)
    else:
        for field in ("evidence", "reviewedBy", "reviewedAt"):
            if approval.get(field) is not None:
                fail(f"unapproved {name} must leave {field} null")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--require-platform-distribution", action="store_true")
    parser.add_argument("--require-offline-redistribution", action="store_true")
    parser.add_argument("--print-mysql-reference", action="store_true")
    parser.add_argument("--print-mysql-parent-reference", action="store_true")
    parser.add_argument("--print-mysql-offline-status", action="store_true")
    args = parser.parse_args()
    try:
        document = validate(
            args.manifest,
            args.require_platform_distribution,
            args.require_offline_redistribution,
        )
    except ValueError as exc:
        print(f"release input validation failed: {exc}", file=sys.stderr)
        return 1
    if args.print_mysql_reference:
        print(document["images"]["mysql"]["reference"])
    if args.print_mysql_parent_reference:
        print(document["images"]["mysql"]["parentIndexReference"])
    if args.print_mysql_offline_status:
        approved = document["images"]["mysql"]["offlineRedistribution"]["approved"]
        print("approved" if approved else "pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
