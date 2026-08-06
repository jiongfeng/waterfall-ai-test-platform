#!/usr/bin/env python3
"""Collect and validate candidate-bound license/NOTICE files from a final image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from docker_image_config_digest import inspect_image, resolve_config_digest


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REFERENCE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
LICENSE_BASENAME_RE = re.compile(
    r"^(?:legal|licen[cs]es?|copying|notice|copyright|"
    r"third[-_. ]party[-_. ](?:notices?|licen[cs]es?))"
    r"(?:[._ -].*)?$",
    re.IGNORECASE,
)
SELECTION_POLICY = "license-notice-filenames-v1"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 50_000


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_source_path(name: str) -> PurePosixPath:
    normalized = name.removeprefix("./")
    path = PurePosixPath(normalized)
    require(normalized and not path.is_absolute(), f"unsafe exported image path: {name!r}")
    require(".." not in path.parts and "." not in path.parts,
            f"unsafe exported image path: {name!r}")
    return path


def selected(path: PurePosixPath) -> bool:
    lower_parts = tuple(part.lower() for part in path.parts)
    if LICENSE_BASENAME_RE.fullmatch(path.name):
        return True
    if lower_parts[:3] == ("usr", "share", "licenses"):
        return True
    if lower_parts[:3] == ("usr", "share", "common-licenses"):
        return True
    if any(
        part in {"legal", "license", "licenses", "licence", "licences"}
        for part in lower_parts[:-1]
    ):
        return True
    return (
        len(lower_parts) >= 4
        and lower_parts[:3] == ("usr", "share", "doc")
        and lower_parts[-1].startswith("copyright")
    )


def resolve_link_target(source: PurePosixPath, linkname: str, *, hardlink: bool) -> str:
    require(linkname and "\x00" not in linkname, "selected license link has an invalid target")
    if hardlink or linkname.startswith("/"):
        candidate = linkname.lstrip("/")
    else:
        candidate = posixpath.join(source.parent.as_posix(), linkname)
    normalized = posixpath.normpath(candidate).removeprefix("./")
    require(normalized not in {"", "."} and not normalized.startswith("../"),
            f"selected license link escapes the image root: /{source.as_posix()} -> {linkname}")
    target = safe_source_path(normalized)
    require(selected(target),
            f"selected license link target is outside the selection policy: "
            f"/{source.as_posix()} -> /{target.as_posix()}")
    require(target != source, f"selected license link points to itself: /{source.as_posix()}")
    return "/" + target.as_posix()


def resolve_link_source(
    source_name: str,
    link_targets: dict[str, str],
    regular_sources: set[str],
    chain: tuple[str, ...] = (),
) -> str:
    require(source_name not in chain,
            "selected license link chain contains a cycle: " + " -> ".join((*chain, source_name)))
    if source_name in regular_sources:
        return source_name
    require(source_name in link_targets,
            f"selected license link target is missing: {source_name}")
    return resolve_link_source(
        link_targets[source_name],
        link_targets,
        regular_sources,
        (*chain, source_name),
    )


def create_container(image: str) -> str:
    result = subprocess.run(
        [
            "docker", "container", "create", "--platform", "linux/amd64",
            "--entrypoint", "/bin/true", image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    container = result.stdout.strip()
    require(re.fullmatch(r"[0-9a-f]{12,64}", container) is not None,
            "docker create returned an invalid container ID")
    return container


def export_stream(container: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["docker", "container", "export", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def collect(
    image: str,
    source_reference: str,
    expected_config_digest: str,
    output: Path,
) -> None:
    require(REFERENCE_RE.fullmatch(source_reference) is not None,
            "source reference must be an immutable sha256 reference")
    require(DIGEST_RE.fullmatch(expected_config_digest) is not None,
            "expected config digest is invalid")
    require(not output.exists() and not output.is_symlink(),
            "license payload output must not already exist")
    inspected = inspect_image(image)
    require(resolve_config_digest(image, inspected=inspected) == expected_config_digest,
            "local final image ID does not match the candidate config digest")
    require(f"{inspected.get('Os')}/{inspected.get('Architecture')}" == "linux/amd64",
            "license payload source image must be linux/amd64")

    output.mkdir(parents=True, mode=0o755)
    files_root = output / "files"
    files_root.mkdir(mode=0o755)
    entries: list[dict[str, object]] = []
    total_bytes = 0
    seen: set[str] = set()
    source_artifacts: dict[str, Path] = {}
    link_targets: dict[str, str] = {}

    def add_content(source_name: str, content: bytes) -> None:
        nonlocal total_bytes
        require(source_name not in source_artifacts,
                f"exported image contains a duplicate license path: {source_name}")
        require(0 < len(content) <= MAX_FILE_BYTES,
                f"selected license file has an invalid size: {source_name}")
        require(len(entries) < MAX_FILES, "selected license payload has too many files")
        require(total_bytes + len(content) <= MAX_TOTAL_BYTES,
                "selected license payload exceeds the size limit")
        source_path_hash = hashlib.sha256(source_name.encode("utf-8")).hexdigest()
        artifact_relative = PurePosixPath("files") / f"{source_path_hash}.license"
        destination = output / Path(*artifact_relative.parts)
        with destination.open("xb") as target:
            target.write(content)
        os.chmod(destination, 0o644)
        entries.append({
            "sourcePath": source_name,
            "artifactPath": artifact_relative.as_posix(),
            "size": len(content),
            "sha256": sha256_bytes(content),
        })
        total_bytes += len(content)
        source_artifacts[source_name] = destination

    container = create_container(image)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = export_stream(container)
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
            for member in archive:
                if not (member.isreg() or member.issym() or member.islnk()):
                    continue
                source_path = safe_source_path(member.name)
                if not selected(source_path):
                    continue
                source_name = "/" + source_path.as_posix()
                require(source_name not in seen,
                        f"exported image contains a duplicate license path: {source_name}")
                seen.add(source_name)
                if member.issym() or member.islnk():
                    link_targets[source_name] = resolve_link_target(
                        source_path,
                        member.linkname,
                        hardlink=member.islnk(),
                    )
                    continue
                stream: BinaryIO | None = archive.extractfile(member)
                require(stream is not None, f"cannot read selected license file: {source_name}")
                content = stream.read(MAX_FILE_BYTES + 1)
                require(len(content) == member.size,
                        f"selected license file changed while exporting: {source_name}")
                add_content(source_name, content)
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait()
        require(return_code == 0, f"docker export failed: {stderr.strip()}")

        regular_sources = set(source_artifacts)
        for source_name in sorted(link_targets):
            target_source = resolve_link_source(
                link_targets[source_name],
                link_targets,
                regular_sources,
                (source_name,),
            )
            target_artifact = source_artifacts[target_source]
            add_content(source_name, target_artifact.read_bytes())
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        subprocess.run(
            ["docker", "container", "rm", "--force", container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    entries.sort(key=lambda item: str(item["sourcePath"]))
    require(entries, "final image contains no collected license or NOTICE files")
    manifest = {
        "schemaVersion": 1,
        "kind": "playwright-test-platform-final-image-license-files",
        "selectionPolicy": SELECTION_POLICY,
        "imageReference": source_reference,
        "configDigest": expected_config_digest,
        "fileCount": len(entries),
        "totalBytes": total_bytes,
        "files": entries,
    }
    (output / "LICENSE-FILES.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_payload(
        output / "LICENSE-FILES.json",
        output,
        source_reference,
        expected_config_digest,
    )


def validate_payload(
    manifest_path: Path,
    root: Path,
    expected_reference: str | None = None,
    expected_config_digest: str | None = None,
) -> dict[str, object]:
    require(root.is_dir() and not root.is_symlink(),
            "license payload root must be a non-symlink directory")
    require(manifest_path.parent == root and manifest_path.name == "LICENSE-FILES.json",
            "license payload manifest must be LICENSE-FILES.json at the payload root")
    root_names = {path.name for path in root.iterdir()}
    require(
        root_names in (
            {"LICENSE-FILES.json", "files"},
            {"LICENSE-FILES.json", "files", "final-image.spdx.json"},
        ),
        "license payload root contains missing or unmanifested entries",
    )
    if "final-image.spdx.json" in root_names:
        spdx_path = root / "final-image.spdx.json"
        require(spdx_path.is_file() and not spdx_path.is_symlink(),
                "license payload SPDX inventory must be a regular file")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read license payload manifest: {exc}") from exc
    require(isinstance(value, dict), "license payload manifest must be an object")
    require(set(value) == {
        "schemaVersion", "kind", "selectionPolicy", "imageReference", "configDigest",
        "fileCount", "totalBytes", "files",
    }, "license payload manifest has missing or unknown fields")
    require(value["schemaVersion"] == 1, "license payload schemaVersion must be 1")
    require(value["kind"] == "playwright-test-platform-final-image-license-files",
            "license payload kind is invalid")
    require(value["selectionPolicy"] == SELECTION_POLICY,
            "license payload selection policy is invalid")
    reference = value["imageReference"]
    config_digest = value["configDigest"]
    require(isinstance(reference, str) and REFERENCE_RE.fullmatch(reference) is not None,
            "license payload imageReference is invalid")
    require(isinstance(config_digest, str) and DIGEST_RE.fullmatch(config_digest) is not None,
            "license payload configDigest is invalid")
    if expected_reference is not None:
        require(reference == expected_reference, "license payload imageReference mismatch")
    if expected_config_digest is not None:
        require(config_digest == expected_config_digest, "license payload configDigest mismatch")
    entries = value["files"]
    require(isinstance(entries, list) and entries, "license payload files must be non-empty")
    require(value["fileCount"] == len(entries), "license payload fileCount mismatch")
    expected_paths: set[str] = set()
    total_bytes = 0
    previous_source = ""
    for index, entry in enumerate(entries):
        require(isinstance(entry, dict) and set(entry) == {
            "sourcePath", "artifactPath", "size", "sha256"
        }, f"license payload files[{index}] is invalid")
        source_path = entry["sourcePath"]
        artifact_path = entry["artifactPath"]
        size = entry["size"]
        checksum = entry["sha256"]
        require(isinstance(source_path, str) and source_path.startswith("/"),
                f"license payload files[{index}].sourcePath is invalid")
        relative_source = safe_source_path(source_path[1:])
        require(selected(relative_source),
                f"license payload files[{index}] is outside the selection policy")
        source_path_hash = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
        expected_artifact = f"files/{source_path_hash}.license"
        require(artifact_path == expected_artifact,
                f"license payload files[{index}].artifactPath mismatch")
        require(isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_FILE_BYTES,
                f"license payload files[{index}].size is invalid")
        require(isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum) is not None,
                f"license payload files[{index}].sha256 is invalid")
        require(source_path > previous_source,
                "license payload files must be strictly sorted with no duplicates")
        previous_source = source_path
        artifact = root / Path(*PurePosixPath(artifact_path).parts)
        require(artifact.is_file() and not artifact.is_symlink(),
                f"license payload file is missing or not regular: {artifact_path}")
        require(stat.S_ISREG(artifact.stat().st_mode),
                f"license payload file is not regular: {artifact_path}")
        require(artifact.stat().st_size == size,
                f"license payload file size mismatch: {artifact_path}")
        require(sha256_file(artifact) == checksum,
                f"license payload file checksum mismatch: {artifact_path}")
        expected_paths.add(artifact_path)
        total_bytes += size
    require(value["totalBytes"] == total_bytes, "license payload totalBytes mismatch")
    actual_paths: set[str] = set()
    files_root = root / "files"
    require(files_root.is_dir() and not files_root.is_symlink(),
            "license payload files directory is missing")
    for path in files_root.rglob("*"):
        require(not path.is_symlink(), "license payload must not contain symbolic links")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
        else:
            require(path.is_dir(), "license payload contains a special filesystem object")
    require(actual_paths == expected_paths,
            "license payload contains missing or unmanifested files")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--image", required=True)
    collect_parser.add_argument("--source-reference", required=True)
    collect_parser.add_argument("--expected-config-digest", required=True)
    collect_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("manifest", type=Path)
    validate_parser.add_argument("--root", type=Path, required=True)
    validate_parser.add_argument("--expected-reference")
    validate_parser.add_argument("--expected-config-digest")
    args = parser.parse_args()
    try:
        if args.command == "collect":
            require(shutil.which("docker") is not None, "docker is required")
            collect(
                args.image,
                args.source_reference,
                args.expected_config_digest,
                args.output,
            )
        else:
            validate_payload(
                args.manifest,
                args.root,
                args.expected_reference,
                args.expected_config_digest,
            )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"license payload validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
