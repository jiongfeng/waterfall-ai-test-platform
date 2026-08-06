#!/usr/bin/env python3
"""Resolve a Docker image config digest across classic and containerd stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from pathlib import PurePosixPath
from typing import BinaryIO, Callable


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
INDEX_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
MAX_JSON_BYTES = 16 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def inspect_image(image: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict),
            "docker image inspect returned an unexpected document")
    return value[0]


def inspect_container(container: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "container", "inspect", container],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict),
            "docker container inspect returned an unexpected document")
    return value[0]


def read_member_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    require(member.isreg(), f"docker save metadata is not regular: {member.name}")
    require(0 < member.size <= MAX_JSON_BYTES,
            f"docker save metadata has an invalid size: {member.name}")
    stream = archive.extractfile(member)
    require(stream is not None, f"cannot read docker save metadata: {member.name}")
    content = stream.read(MAX_JSON_BYTES + 1)
    require(len(content) == member.size, f"truncated docker save metadata: {member.name}")
    return content


def read_json_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[object, bytes]:
    content = read_member_bytes(archive, member)
    try:
        return json.loads(content), content
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in docker save metadata: {member.name}") from exc


def legacy_config_name(value: object) -> tuple[str, str]:
    require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict),
            "docker save manifest.json must contain exactly one image")
    config = value[0].get("Config")
    require(isinstance(config, str), "docker save manifest.json Config is missing")
    path = PurePosixPath(config)
    require(not path.is_absolute() and ".." not in path.parts and "." not in path.parts,
            "docker save manifest.json Config path is unsafe")
    if len(path.parts) == 1 and path.name.endswith(".json"):
        digest_hex = path.name.removesuffix(".json")
    elif len(path.parts) == 3 and path.parts[:2] == ("blobs", "sha256"):
        digest_hex = path.parts[2]
    else:
        raise ValueError("docker save manifest.json Config path is unsupported")
    require(HEX_RE.fullmatch(digest_hex) is not None,
            "docker save manifest.json Config digest is invalid")
    return config, digest_hex


def extract_config_digest_from_archive(
    stream: BinaryIO,
    descriptor_digest: str,
) -> str:
    """Read Docker save output without extracting it to the filesystem."""

    require(DIGEST_RE.fullmatch(descriptor_digest) is not None,
            "image descriptor digest is invalid")
    descriptor_hex = descriptor_digest.split(":", 1)[1]
    descriptor_member = f"blobs/sha256/{descriptor_hex}"
    descriptor_config: str | None = None
    legacy_manifest: object | None = None
    json_blob_candidates: dict[str, tuple[str, bool]] = {}
    seen_blob_names: set[str] = set()

    try:
        with tarfile.open(fileobj=stream, mode="r|*") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                blob_match = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", name)
                if blob_match is not None:
                    require(name not in seen_blob_names,
                            "docker save archive contains a duplicate content blob")
                    seen_blob_names.add(name)
                    if member.size <= 0 or member.size > MAX_JSON_BYTES or not member.isreg():
                        continue
                    content = read_member_bytes(archive, member)
                    actual_hash = hashlib.sha256(content).hexdigest()
                    try:
                        parsed = json.loads(content)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parsed = None
                    json_blob_candidates[name] = (actual_hash, isinstance(parsed, dict))
                    if name != descriptor_member:
                        continue
                    require(descriptor_config is None,
                            "docker save archive contains a duplicate descriptor blob")
                    value = parsed
                    require(hashlib.sha256(content).hexdigest() == descriptor_hex,
                            "docker save descriptor blob checksum mismatch")
                    require(isinstance(value, dict), "docker save descriptor must be an object")
                    require(value.get("mediaType") in MANIFEST_MEDIA_TYPES,
                            "docker save descriptor is not a single image manifest")
                    config = value.get("config")
                    require(isinstance(config, dict), "docker save manifest config is missing")
                    digest = config.get("digest")
                    require(isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
                            "docker save manifest config digest is invalid")
                    descriptor_config = digest
                    continue
                if name == "manifest.json":
                    require(legacy_manifest is None,
                            "docker save archive contains duplicate manifest.json")
                    legacy_manifest, _ = read_json_member(archive, member)
                    continue
                if "/" not in name and name.endswith(".json"):
                    digest_hex = name.removesuffix(".json")
                    if HEX_RE.fullmatch(digest_hex) is not None:
                        require(name not in json_blob_candidates,
                                "docker save archive contains a duplicate config blob")
                        value, content = read_json_member(archive, member)
                        json_blob_candidates[name] = (
                            hashlib.sha256(content).hexdigest(),
                            isinstance(value, dict),
                        )
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"cannot read docker save archive: {exc}") from exc

    if descriptor_config is not None:
        config_name = "blobs/sha256/" + descriptor_config.split(":", 1)[1]
        require(config_name in json_blob_candidates,
                "docker save descriptor config blob is missing or oversized")
        actual_digest, is_object = json_blob_candidates[config_name]
        require(is_object, "docker save descriptor config blob must be a JSON object")
        require(actual_digest == descriptor_config.split(":", 1)[1],
                "docker save descriptor config blob checksum mismatch")
        return descriptor_config
    require(legacy_manifest is not None,
            "docker save archive contains neither the descriptor blob nor manifest.json")
    config_name, digest_hex = legacy_config_name(legacy_manifest)
    require(config_name in json_blob_candidates, "docker save config blob is missing")
    actual_digest, is_object = json_blob_candidates[config_name]
    require(is_object, "docker save config blob must be a JSON object")
    require(actual_digest == digest_hex, "docker save config blob checksum mismatch")
    return "sha256:" + digest_hex


def extract_config_digest_from_save(image: str, descriptor_digest: str) -> str:
    process = subprocess.Popen(
        ["docker", "image", "save", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        digest = extract_config_digest_from_archive(process.stdout, descriptor_digest)
    except BaseException:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    else:
        process.stdout.close()
        try:
            _, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
        require(process.returncode == 0,
                "docker image save failed: " + stderr.decode("utf-8", errors="replace").strip())
    return digest


def resolve_descriptor_config_digest(
    immutable_image_id: str,
    descriptor: object,
    *,
    save_resolver: Callable[[str, str], str],
) -> str:
    require(isinstance(descriptor, dict), "Docker manifest Descriptor is invalid")
    media_type = descriptor.get("mediaType")
    descriptor_digest = descriptor.get("digest")
    require(media_type not in INDEX_MEDIA_TYPES,
            "Docker manifest Descriptor resolves to a multi-platform index")
    require(media_type in MANIFEST_MEDIA_TYPES,
            "Docker manifest Descriptor mediaType is unsupported")
    require(isinstance(descriptor_digest, str)
            and DIGEST_RE.fullmatch(descriptor_digest) is not None,
            "Docker manifest Descriptor digest is invalid")
    annotations = descriptor.get("annotations")
    annotated_config: str | None = None
    if annotations is not None:
        require(isinstance(annotations, dict), "Docker manifest Descriptor annotations are invalid")
        if "config.digest" in annotations:
            annotation = annotations["config.digest"]
            require(isinstance(annotation, str) and DIGEST_RE.fullmatch(annotation) is not None,
                    "Docker manifest Descriptor config.digest annotation is invalid")
            require(annotation != descriptor_digest,
                    "Docker manifest Descriptor config.digest equals its manifest digest")
            annotated_config = annotation
    saved_config = save_resolver(immutable_image_id, descriptor_digest)
    require(DIGEST_RE.fullmatch(saved_config) is not None,
            "docker image save returned an invalid config digest")
    require(saved_config != descriptor_digest,
            "docker image config digest equals its manifest digest")
    if annotated_config is not None:
        require(annotated_config == saved_config,
                "Docker manifest Descriptor config.digest annotation is inconsistent with docker save")
    return saved_config


def resolve_config_digest(
    image: str,
    *,
    inspected: dict[str, object] | None = None,
    save_resolver: Callable[[str, str], str] = extract_config_digest_from_save,
) -> str:
    value = inspect_image(image) if inspected is None else inspected
    image_id = value.get("Id")
    require(isinstance(image_id, str) and DIGEST_RE.fullmatch(image_id) is not None,
            "docker image inspect Id is invalid")
    descriptor = value.get("Descriptor")
    if descriptor is None:
        # The classic graphdriver image store documents Id as the config JSON digest.
        return image_id
    require(isinstance(descriptor, dict), "docker image Descriptor is invalid")
    require(image_id == descriptor.get("digest"),
            "docker image Id and Descriptor digest are inconsistent")
    return resolve_descriptor_config_digest(
        image_id,
        descriptor,
        save_resolver=save_resolver,
    )


def verify_container_identity(
    container: str,
    *,
    expected_manifest: str,
    expected_config: str,
    inspected: dict[str, object] | None = None,
) -> None:
    require(DIGEST_RE.fullmatch(expected_manifest) is not None,
            "expected container manifest digest is invalid")
    require(DIGEST_RE.fullmatch(expected_config) is not None,
            "expected container config digest is invalid")
    value = inspect_container(container) if inspected is None else inspected
    image_id = value.get("Image")
    require(isinstance(image_id, str) and DIGEST_RE.fullmatch(image_id) is not None,
            "docker container inspect Image is invalid")
    descriptor = value.get("ImageManifestDescriptor")
    if descriptor is None:
        # Classic graphdriver containers bind .Image directly to the config digest.
        require(image_id == expected_config,
                "classic Docker container config digest mismatch")
        return
    require(isinstance(descriptor, dict),
            "docker container ImageManifestDescriptor is invalid")
    media_type = descriptor.get("mediaType")
    manifest_digest = descriptor.get("digest")
    require(media_type in MANIFEST_MEDIA_TYPES,
            "docker container ImageManifestDescriptor is not a single image manifest")
    require(isinstance(manifest_digest, str) and DIGEST_RE.fullmatch(manifest_digest) is not None,
            "docker container ImageManifestDescriptor digest is invalid")
    require(manifest_digest == expected_manifest,
            "containerd Docker container manifest digest mismatch")
    annotations = descriptor.get("annotations")
    if annotations is not None:
        require(isinstance(annotations, dict),
                "docker container ImageManifestDescriptor annotations are invalid")
        if "config.digest" in annotations:
            annotation = annotations["config.digest"]
            require(isinstance(annotation, str) and DIGEST_RE.fullmatch(annotation) is not None,
                    "docker container config.digest annotation is invalid")
            require(annotation == expected_config,
                    "docker container config.digest annotation mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--container", action="store_true")
    parser.add_argument("--expected-manifest")
    parser.add_argument("--expected-config")
    args = parser.parse_args()
    try:
        if args.container:
            require(args.expected_manifest is not None and args.expected_config is not None,
                    "container verification requires --expected-manifest and --expected-config")
            verify_container_identity(
                args.target,
                expected_manifest=args.expected_manifest,
                expected_config=args.expected_config,
            )
            print("verified")
        else:
            require(args.expected_manifest is None and args.expected_config is None,
                    "expected digests are only valid with --container")
            print(resolve_config_digest(args.target))
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"docker image config digest resolution failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
