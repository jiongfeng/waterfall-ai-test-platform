#!/usr/bin/env python3
"""Fail-closed validation for a release bundle's identity and contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from release_chain import sha256 as chain_sha256
from release_chain import validate_approval
from license_payload import validate_payload


VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REFERENCE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
SOURCE_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+$")
ROOT_KEYS = {
    "schemaVersion",
    "project",
    "version",
    "tag",
    "revision",
    "deploymentContractVersion",
    "sourceUrl",
    "sourceDateEpoch",
    "architecture",
    "bundleType",
    "images",
    "artifacts",
}
IMAGE_KEYS = {
    "reference",
    "digest",
    "configDigest",
    "runtimeReference",
    "archive",
    "archiveSha256",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def validate_image(name: str, image: object, bundle_type: str, root: Path | None) -> None:
    require(isinstance(image, dict), f"images.{name} must be an object")
    require(set(image) == IMAGE_KEYS, f"images.{name} has missing or unknown fields")
    reference = image["reference"]
    digest = image["digest"]
    require(isinstance(reference, str) and REFERENCE_RE.fullmatch(reference) is not None,
            f"images.{name}.reference must be an immutable sha256 reference")
    require(isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
            f"images.{name}.digest is invalid")
    require(reference.endswith(f"@{digest}"), f"images.{name} reference/digest mismatch")
    config_digest = image["configDigest"]
    require(isinstance(config_digest, str) and DIGEST_RE.fullmatch(config_digest) is not None,
            f"images.{name}.configDigest is invalid")
    runtime_reference = image["runtimeReference"]
    require(isinstance(runtime_reference, str), f"images.{name}.runtimeReference must be a string")

    archive = image["archive"]
    archive_sha = image["archiveSha256"]
    if bundle_type == "online":
        require(runtime_reference == reference,
                f"online images.{name}.runtimeReference must equal its registry digest reference")
        require(archive is None and archive_sha is None,
                f"online bundle must not claim images.{name} archive")
        return
    digest_hex = digest.split(":", 1)[1]
    expected_runtime = f"playwright-test-platform.local/{name}:sha256-{digest_hex}"
    require(runtime_reference == expected_runtime,
            f"offline images.{name}.runtimeReference must be {expected_runtime}")
    expected_archive = f"images/{name}-linux-amd64.tar.zst"
    require(archive == expected_archive, f"images.{name}.archive must be {expected_archive}")
    require(isinstance(archive_sha, str) and re.fullmatch(r"[0-9a-f]{64}", archive_sha) is not None,
            f"images.{name}.archiveSha256 is invalid")
    if root is not None:
        archive_path = root / archive
        require(archive_path.is_file(), f"missing image archive {archive}")
        require(sha256(archive_path) == archive_sha, f"image archive checksum mismatch: {archive}")


def validate_sboms(root: Path) -> None:
    expected = {
        "sbom/platform.spdx.json": ("spdxVersion", "SPDX-"),
        "sbom/platform.cdx.json": ("bomFormat", "CycloneDX"),
        "sbom/mysql.spdx.json": ("spdxVersion", "SPDX-"),
        "sbom/mysql.cdx.json": ("bomFormat", "CycloneDX"),
    }
    for relative, (field, prefix) in expected.items():
        value = load_json(root / relative)
        require(isinstance(value, dict), f"{relative} must be a JSON object")
        require(str(value.get(field, "")).startswith(prefix), f"{relative} is not the expected SBOM format")


def validate_environment(root: Path, metadata: dict[str, object]) -> None:
    values: dict[str, str] = {}
    path = root / ".env.images"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    for line in lines:
        if not line or line.startswith("#"):
            continue
        require("=" in line, f"invalid .env.images line: {line!r}")
        key, value = line.split("=", 1)
        require(key not in values, f"duplicate .env.images key: {key}")
        values[key] = value
    require(set(values) == {"PLATFORM_IMAGE", "MYSQL_IMAGE"},
            ".env.images may only contain PLATFORM_IMAGE and MYSQL_IMAGE")
    require(values["PLATFORM_IMAGE"] == metadata["images"]["platform"]["runtimeReference"],
            ".env.images PLATFORM_IMAGE does not match metadata")
    require(values["MYSQL_IMAGE"] == metadata["images"]["mysql"]["runtimeReference"],
            ".env.images MYSQL_IMAGE does not match metadata")


def validate_assembly(root: Path, metadata: dict[str, object]) -> None:
    assembly = load_json(root / "assembly/bundle-manifest.json")
    require(isinstance(assembly, dict), "assembly manifest must be an object")
    require(assembly.get("schemaVersion") == 1, "assembly manifest schemaVersion must be 1")
    require(assembly.get("kind") == "playwright-test-platform-release-bundle-assembly",
            "assembly manifest kind is invalid")
    require(assembly.get("bundleType") == metadata["bundleType"],
            "assembly manifest bundleType mismatch")
    require(assembly.get("tag") == metadata["tag"], "assembly manifest tag mismatch")
    require(assembly.get("revision") == metadata["revision"],
            "assembly manifest revision mismatch")
    require(assembly.get("sourceUrl") == metadata["sourceUrl"],
            "assembly manifest sourceUrl mismatch")
    require(assembly.get("architecture") == metadata["architecture"],
            "assembly manifest architecture mismatch")
    subjects = assembly.get("subjects")
    require(isinstance(subjects, list), "assembly subjects must be an array")
    subject_digests = {
        subject.get("digest", {}).get("sha256")
        for subject in subjects
        if isinstance(subject, dict)
    }
    for image in metadata["images"].values():
        require(image["digest"].split(":", 1)[1] in subject_digests,
                "assembly manifest does not cover every image digest")
        require(image["configDigest"].split(":", 1)[1] in subject_digests,
                "assembly manifest does not cover every image config digest")


def validate_final_image_licenses(root: Path, metadata: dict[str, object]) -> None:
    license_root = root / "licenses"
    review = load_json(license_root / "LICENSE-REVIEW.json")
    require(isinstance(review, dict) and review.get("schemaVersion") == 1,
            "license review schemaVersion must be 1")
    components = [("platformImage", metadata["images"]["platform"]["reference"])]
    if metadata["bundleType"] == "offline":
        components.append(("mysqlImage", metadata["images"]["mysql"]["reference"]))
    for name, reference in components:
        value = review.get(name)
        require(isinstance(value, dict), f"license review must define {name}")
        require(value.get("reference") == reference,
                f"{name} license review is not bound to its image reference")
        require(value.get("complete") is True,
                f"{name} final-image license inventory is not approved as complete")
        require(isinstance(value.get("evidence"), str) and value["evidence"].strip(),
                f"{name} final-image license evidence is required")
        component_root = license_root / name
        require(component_root.is_dir() and not component_root.is_symlink(),
                f"missing final-image license directory: {name}")
        require({path.name for path in component_root.iterdir()} == {
            "LICENSE-FILES.json", "files", "final-image.spdx.json"
        }, f"{name} final-image license payload has missing or unknown top-level entries")
        spdx = load_json(component_root / "final-image.spdx.json")
        require(isinstance(spdx, dict) and str(spdx.get("spdxVersion", "")).startswith("SPDX-"),
                f"{name} final-image SPDX inventory is invalid")
        image_key = "platform" if name == "platformImage" else "mysql"
        validate_payload(
            component_root / "LICENSE-FILES.json",
            component_root,
            metadata["images"][image_key]["reference"],
            metadata["images"][image_key]["configDigest"],
        )


def validate_release_provenance(root: Path, metadata: dict[str, object]) -> None:
    candidate_path = root / "provenance/candidate/RELEASE-CANDIDATE.json"
    approval_root = root / "provenance/approval"
    approval_path = approval_root / "RELEASE-APPROVAL.json"
    candidate, approval = validate_approval(approval_path, candidate_path, approval_root)
    require(candidate["version"] == metadata["version"], "candidate version does not match metadata")
    require(candidate["revision"] == metadata["revision"], "candidate revision does not match metadata")
    require(candidate["targetImage"] == metadata["images"]["platform"]["reference"].split("@", 1)[0],
            "candidate target image does not match metadata")
    require(candidate["platformDigest"] == metadata["images"]["platform"]["digest"],
            "candidate platform digest does not match metadata")
    require(candidate["platformConfigDigest"] == metadata["images"]["platform"]["configDigest"],
            "candidate platform config digest does not match metadata")
    require(candidate["mysqlImage"] == metadata["images"]["mysql"]["reference"],
            "candidate MySQL image does not match metadata")
    require(candidate["mysqlConfigDigest"] == metadata["images"]["mysql"]["configDigest"],
            "candidate MySQL config digest does not match metadata")
    if metadata["bundleType"] == "offline":
        require(approval["decisions"]["mysqlOfflineRedistribution"]["approved"] is True,
                "offline bundle lacks MySQL redistribution approval")
        require(approval["decisions"]["mysqlLicense"]["complete"] is True,
                "offline bundle lacks complete MySQL final-image license review")

    for name, record in candidate["artifacts"]["sboms"].items():
        require(chain_sha256(root / "sbom" / name) == record["sha256"],
                f"bundle SBOM does not match candidate: {name}")
    approval_artifacts = approval["artifacts"]
    require(chain_sha256(root / "licenses/third-party-images.json") ==
            approval_artifacts["thirdPartyManifest"]["sha256"],
            "bundle third-party policy does not match approval")
    require(chain_sha256(root / "licenses/LICENSE-REVIEW.json") ==
            approval_artifacts["licenseReview"]["sha256"],
            "bundle license review does not match approval")
    for name, inventory in approval_artifacts["licenseInventories"].items():
        for artifact_name, record in inventory.items():
            relative = Path(record["path"]).relative_to("licenses")
            require(chain_sha256(root / "licenses" / relative) == record["sha256"],
                    f"bundle reviewed license inventory does not match approval: "
                    f"{name}.{artifact_name}")


def validate(
    path: Path,
    root: Path | None = None,
    expected_tag: str | None = None,
    expected_revision: str | None = None,
    expected_source_url: str | None = None,
) -> dict[str, object]:
    document = load_json(path)
    require(isinstance(document, dict), "release metadata must be an object")
    require(set(document) == ROOT_KEYS, "release metadata has missing or unknown fields")
    require(document["schemaVersion"] == 1, "schemaVersion must be 1")
    require(document["project"] == "playwright-test-platform", "project is invalid")
    version = document["version"]
    require(isinstance(version, str) and VERSION_RE.fullmatch(version) is not None, "version is invalid")
    require(document["tag"] == f"v{version}", "tag must equal v + version")
    require(isinstance(document["revision"], str) and REVISION_RE.fullmatch(document["revision"]) is not None,
            "revision must be a complete lowercase Git SHA")
    require(document["deploymentContractVersion"] == 1,
            "deploymentContractVersion must be 1")
    require(isinstance(document["sourceUrl"], str) and SOURCE_RE.fullmatch(document["sourceUrl"]) is not None,
            "sourceUrl must be a GitHub repository URL")
    if expected_tag is not None:
        require(document["tag"] == expected_tag, "metadata tag does not match signed source ref")
    if expected_revision is not None:
        require(document["revision"] == expected_revision,
                "metadata revision does not match signed source digest")
    if expected_source_url is not None:
        require(document["sourceUrl"] == expected_source_url,
                "metadata sourceUrl does not match signed repository")
    require(isinstance(document["sourceDateEpoch"], int) and document["sourceDateEpoch"] > 0,
            "sourceDateEpoch must be a positive integer")
    require(document["architecture"] == "linux/amd64", "architecture must be linux/amd64")
    require(document["bundleType"] in {"online", "offline"}, "bundleType is invalid")
    require(isinstance(document["images"], dict) and set(document["images"]) == {"platform", "mysql"},
            "images must contain exactly platform and mysql")
    for name in ("platform", "mysql"):
        validate_image(name, document["images"][name], document["bundleType"], root)
    require(document["artifacts"] == {
        "checksums": "SHA256SUMS",
        "sbomDirectory": "sbom",
        "assemblyManifest": "assembly/bundle-manifest.json",
    }, "artifacts contract is invalid")

    if root is not None:
        require(path.resolve() == (root / "RELEASE-METADATA.json").resolve(),
                "metadata path must be RELEASE-METADATA.json at bundle root")
        validate_sboms(root)
        validate_environment(root, document)
        validate_assembly(root, document)
        validate_final_image_licenses(root, document)
        validate_release_provenance(root, document)
        compose = (root / "deploy/compose.yaml").read_text(encoding="utf-8")
        require("${PLATFORM_IMAGE" in compose, "release Compose must consume PLATFORM_IMAGE")
        require("${MYSQL_IMAGE" in compose, "release Compose must consume MYSQL_IMAGE")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--bundle-root", type=Path)
    parser.add_argument("--expected-tag")
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-source-url")
    args = parser.parse_args()
    try:
        validate(
            args.metadata,
            args.bundle_root,
            args.expected_tag,
            args.expected_revision,
            args.expected_source_url,
        )
    except (OSError, ValueError) as exc:
        print(f"release metadata validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
