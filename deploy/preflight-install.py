#!/usr/bin/env python3
"""Read-only, fail-closed install/upgrade policy check.

Run this before an installer writes to its destination. A missing or empty
destination is a fresh install. A non-empty destination is an upgrade source
and must contain release metadata plus an exact path in upgrade-matrix.json.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOW = 0
INVALID_INPUT = 2
POLICY_DENIED = 10
METADATA_NAME = "RELEASE-METADATA.json"
DEFAULT_COMPOSE_PROJECT = "waterfall-ai-test-platform"


class PreflightError(ValueError):
    """Raised when policy or metadata cannot be interpreted safely."""


@dataclass(frozen=True)
class ReleaseIdentity:
    version: str
    revision: str
    deployment_contract_version: int


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read valid {description}: {path}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{description} must be a JSON object: {path}")
    return value


def load_matrix(path: Path) -> dict[str, Any]:
    matrix = _load_json(path, "upgrade matrix")
    if set(matrix) != {"schema_version", "policy", "upgrade_paths"}:
        raise PreflightError(
            "upgrade matrix must contain only schema_version, policy, and upgrade_paths"
        )
    if matrix.get("schema_version") != 1:
        raise PreflightError("upgrade matrix schema_version must be 1")

    policy = matrix.get("policy")
    paths = matrix.get("upgrade_paths")
    if not isinstance(policy, dict) or not isinstance(paths, list):
        raise PreflightError("upgrade matrix must define policy and upgrade_paths")
    expected_policy_names = {
        "fresh_install",
        "legacy_internal_installation",
        "unknown_source",
        "unlisted_upgrade",
    }
    if set(policy) != expected_policy_names:
        raise PreflightError("upgrade matrix policy set is incomplete or contains unknown entries")

    expected_decisions = {
        "fresh_install": "allow",
        "legacy_internal_installation": "deny",
        "unknown_source": "deny",
        "unlisted_upgrade": "deny",
    }
    for policy_name, expected in expected_decisions.items():
        entry = policy.get(policy_name)
        if not isinstance(entry, dict) or entry.get("decision") != expected:
            raise PreflightError(
                f"upgrade matrix policy {policy_name!r} must be {expected!r}"
            )
    if policy["fresh_install"].get("requires_empty_target") is not True:
        raise PreflightError("fresh installs must require an empty target")
    if set(policy["fresh_install"]) != {"decision", "requires_empty_target"}:
        raise PreflightError("fresh_install policy contains unknown fields")
    if policy["legacy_internal_installation"].get("status") != "retired":
        raise PreflightError("legacy internal installations must remain retired")
    if set(policy["legacy_internal_installation"]) != {"decision", "status"}:
        raise PreflightError("legacy_internal_installation policy contains unknown fields")
    for policy_name in ("unknown_source", "unlisted_upgrade"):
        if set(policy[policy_name]) != {"decision"}:
            raise PreflightError(f"{policy_name} policy contains unknown fields")

    for index, entry in enumerate(paths):
        if not isinstance(entry, dict):
            raise PreflightError(f"upgrade_paths[{index}] must be an object")
        if set(entry) != {"from", "to", "mode", "decision"}:
            raise PreflightError(
                f"upgrade_paths[{index}] must contain only from, to, mode, and decision"
            )
        if entry["mode"] != "in_place" or entry["decision"] != "allow":
            raise PreflightError(
                f"upgrade_paths[{index}] must be an allowed in_place path"
            )
        _identity_from_matrix(entry["from"], f"upgrade_paths[{index}].from")
        _identity_from_matrix(entry["to"], f"upgrade_paths[{index}].to")
    return matrix


def _release_identity(
    version: Any,
    revision: Any,
    contract_version: Any,
    description: str,
) -> ReleaseIdentity:
    if not isinstance(version, str) or not version.strip():
        raise PreflightError(f"{description} has no non-empty version")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PreflightError(f"{description} revision must be a lowercase 40-character SHA")
    if (
        not isinstance(contract_version, int)
        or isinstance(contract_version, bool)
        or contract_version < 1
    ):
        raise PreflightError(
            f"{description} deployment contract version must be a positive integer"
        )
    return ReleaseIdentity(version.strip(), revision, contract_version)


def _identity_from_matrix(value: Any, description: str) -> ReleaseIdentity:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "revision",
        "deployment_contract_version",
    }:
        raise PreflightError(
            f"{description} must contain only version, revision, and "
            "deployment_contract_version"
        )
    return _release_identity(
        value["version"],
        value["revision"],
        value["deployment_contract_version"],
        description,
    )


def identity_from_metadata(path: Path, description: str) -> ReleaseIdentity:
    if not path.is_file():
        raise PreflightError(f"{description} not found: {path}")
    metadata = _load_json(path, description)
    return _release_identity(
        metadata.get("version"),
        metadata.get("revision"),
        metadata.get("deploymentContractVersion"),
        description,
    )


def require_clean_compose_project(project: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", project):
        raise PreflightError("Compose project name is invalid")

    commands = (
        ("containers", ["docker", "container", "ls", "--all", "--quiet"]),
        ("volumes", ["docker", "volume", "ls", "--quiet"]),
        ("networks", ["docker", "network", "ls", "--quiet"]),
    )
    label_filter = f"label=com.docker.compose.project={project}"
    for resource_name, command in commands:
        try:
            result = subprocess.run(
                [*command, "--filter", label_filter],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreflightError(
                "cannot inspect Docker Compose state; source is unknown"
            ) from exc
        if result.returncode != 0:
            raise PreflightError(
                "cannot inspect Docker Compose state; source is unknown"
            )
        if result.stdout.strip():
            raise PreflightError(
                f"Compose project {project!r} already has {resource_name}; "
                "source is unknown"
            )


def evaluate(
    target: Path,
    release_metadata: Path | None,
    matrix: dict[str, Any],
    compose_project: str,
) -> str:
    if target.is_symlink():
        raise PreflightError("install target must not be a symbolic link")
    if target.exists() and not target.is_dir():
        raise PreflightError("install target exists but is not a directory")

    try:
        is_empty = not target.exists() or next(target.iterdir(), None) is None
    except OSError as exc:
        raise PreflightError("cannot inspect install target") from exc

    if is_empty:
        if release_metadata is not None:
            identity_from_metadata(release_metadata, "target release metadata")
        require_clean_compose_project(compose_project)
        return "fresh install into a missing or empty target"

    source_metadata = target / METADATA_NAME
    if not source_metadata.is_file():
        raise PreflightError(
            f"non-empty target has no {METADATA_NAME}; source is unknown"
        )
    source = identity_from_metadata(source_metadata, "source release metadata")
    if release_metadata is None:
        raise PreflightError(
            "non-empty target requires --release-metadata and an explicitly listed upgrade path"
        )
    destination = identity_from_metadata(release_metadata, "target release metadata")

    for entry in matrix["upgrade_paths"]:
        allowed_source = _identity_from_matrix(entry["from"], "upgrade path source")
        allowed_destination = _identity_from_matrix(entry["to"], "upgrade path target")
        if allowed_source == source and allowed_destination == destination:
            return f"listed in-place upgrade {source.version} -> {destination.version}"

    raise PreflightError(
        f"in-place upgrade {source.version} -> {destination.version} is not supported"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check an install target against the public upgrade policy without modifying it."
        )
    )
    parser.add_argument("--target", required=True, type=Path, help="destination to inspect")
    parser.add_argument(
        "--release-metadata",
        type=Path,
        help=(
            "RELEASE-METADATA.json for the release being installed; required when "
            "the destination is non-empty"
        ),
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(__file__).with_name("upgrade-matrix.json"),
        help="upgrade policy matrix (defaults to the file beside this script)",
    )
    parser.add_argument(
        "--compose-project",
        default=DEFAULT_COMPOSE_PROJECT,
        help=(
            "Compose project whose existing containers, volumes, and networks must "
            f"block a fresh install (default: {DEFAULT_COMPOSE_PROJECT})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        matrix = load_matrix(args.matrix)
        explanation = evaluate(
            args.target,
            args.release_metadata,
            matrix,
            args.compose_project,
        )
    except PreflightError as exc:
        print(f"DENY: {exc}", file=sys.stderr)
        print(
            "No files were changed. Use an empty destination for this public Beta or "
            "follow a separately documented export/import migration.",
            file=sys.stderr,
        )
        return POLICY_DENIED

    print(f"ALLOW: {explanation}.")
    return ALLOW


if __name__ == "__main__":
    raise SystemExit(main())
