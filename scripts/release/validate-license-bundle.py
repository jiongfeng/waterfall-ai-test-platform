#!/usr/bin/env python3
"""Validate human-reviewed license material for distributed image bytes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from license_payload import validate_payload


def fail(message: str) -> None:
    raise ValueError(message)


def validate_component(
    review: dict[str, object],
    key: str,
    reference: str,
    config_digest: str,
    license_root: Path,
) -> None:
    value = review.get(key)
    if not isinstance(value, dict):
        fail(f"license review must define {key}")
    if value.get("reference") != reference:
        fail(f"{key} license review is not bound to {reference}")
    if value.get("complete") is not True:
        fail(f"{key} final-image license inventory is not approved as complete")
    evidence = value.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        fail(f"{key} license review evidence is required")
    component_directory = license_root / key
    if not component_directory.is_dir():
        fail(f"missing final-image license directory: {component_directory}")
    if {path.name for path in component_directory.iterdir()} != {
        "LICENSE-FILES.json", "files", "final-image.spdx.json"
    }:
        fail(f"{key} final-image license payload has missing or unknown top-level entries")
    try:
        spdx = json.loads((component_directory / "final-image.spdx.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {key} SPDX inventory: {exc}")
    if not isinstance(spdx, dict) or not str(spdx.get("spdxVersion", "")).startswith("SPDX-"):
        fail(f"{key} final-image SPDX inventory is invalid")
    validate_payload(
        component_directory / "LICENSE-FILES.json",
        component_directory,
        reference,
        config_digest,
    )


def validate(
    license_root: Path,
    platform_reference: str,
    platform_config_digest: str,
    mysql_reference: str,
    mysql_config_digest: str,
    require_mysql: bool,
) -> None:
    review_path = license_root / "LICENSE-REVIEW.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {review_path}: {exc}")
    if not isinstance(review, dict) or review.get("schemaVersion") != 1:
        fail("license review schemaVersion must be 1")
    validate_component(
        review,
        "platformImage",
        platform_reference,
        platform_config_digest,
        license_root,
    )
    if require_mysql:
        validate_component(
            review,
            "mysqlImage",
            mysql_reference,
            mysql_config_digest,
            license_root,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("license_root", type=Path)
    parser.add_argument("--platform-image", required=True)
    parser.add_argument("--platform-config-digest", required=True)
    parser.add_argument("--mysql-image", required=True)
    parser.add_argument("--mysql-config-digest", required=True)
    parser.add_argument("--require-mysql", action="store_true")
    args = parser.parse_args()
    try:
        validate(
            args.license_root,
            args.platform_image,
            args.platform_config_digest,
            args.mysql_image,
            args.mysql_config_digest,
            args.require_mysql,
        )
    except ValueError as exc:
        print(f"final-image license validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
