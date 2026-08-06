#!/usr/bin/env python3
"""Verify the exact asset set bound by a signed Release manifest."""

from __future__ import annotations

import argparse
import hashlib
import re
import stat
import sys
from pathlib import Path

from release_manifest import MANIFEST_NAME, validate as validate_manifest


CHECKSUM_NAME = "RELEASE-ASSET-SHA256SUMS"
SIGNATURE_NAME = f"{MANIFEST_NAME}.minisig"
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  \./([A-Za-z0-9][A-Za-z0-9._-]*)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(root: Path) -> None:
    require(root.is_dir() and not root.is_symlink(), "release asset root must be a directory")
    actual: dict[str, Path] = {}
    for path in root.iterdir():
        require(ASSET_NAME_RE.fullmatch(path.name) is not None,
                f"release contains an unsafe asset name: {path.name!r}")
        require(path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode),
                f"release asset is not a regular file: {path.name}")
        require(path.name not in actual, f"release contains a duplicate asset: {path.name}")
        actual[path.name] = path

    manifest_path = actual.get(MANIFEST_NAME)
    require(manifest_path is not None, f"release is missing {MANIFEST_NAME}")
    signature_path = actual.get(SIGNATURE_NAME)
    require(signature_path is not None, f"release is missing {SIGNATURE_NAME}")
    require(signature_path.stat().st_size > 0, "release manifest signature is empty")
    manifest = validate_manifest(manifest_path, root)
    manifest_assets = {item["name"]: item["sha256"] for item in manifest["assets"]}

    checksum_path = actual.get(CHECKSUM_NAME)
    require(checksum_path is not None, f"release is missing {CHECKSUM_NAME}")
    records: dict[str, str] = {}
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read release checksum root: {exc}") from exc
    require(lines, "release checksum root is empty")
    for number, line in enumerate(lines, 1):
        match = CHECKSUM_LINE_RE.fullmatch(line)
        require(match is not None, f"invalid release checksum line {number}")
        checksum, name = match.groups()
        require(name != CHECKSUM_NAME, "release checksum root must not list itself")
        require(name not in records, f"duplicate release checksum record: {name}")
        records[name] = checksum

    require(set(records) == set(manifest_assets) - {CHECKSUM_NAME},
            "release checksum root does not match signed manifest assets")
    for name, expected in records.items():
        require(manifest_assets[name] == expected,
                f"release checksum root differs from signed manifest: {name}")

    expected_names = set(manifest_assets) | {MANIFEST_NAME, SIGNATURE_NAME}
    actual_names = set(actual)
    require(actual_names == expected_names,
            "release asset set mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"unexpected={sorted(actual_names - expected_names)}")
    for name, expected in manifest_assets.items():
        require(sha256(actual[name]) == expected, f"release asset checksum mismatch: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset_root", type=Path)
    args = parser.parse_args()
    try:
        validate(args.asset_root)
    except (OSError, ValueError) as exc:
        print(f"release asset set validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
