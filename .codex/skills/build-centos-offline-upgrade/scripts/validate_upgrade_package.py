#!/usr/bin/env python3
"""Validate a Waterfall AI CentOS offline upgrade package."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile


REQUIRED_FILES = {
    "README-zh-CN.md",
    "SHA256SUMS",
    "SOURCE-SNAPSHOT.md",
    "TARGET-METADATA.env",
    "VERIFICATION.md",
    "upgrade.sh",
    "verify-installed.sh",
    "rollback.sh",
    "finalize-checksums.sh",
    "prepare-runtime.sh",
    "build/Dockerfile",
    "build/RUNTIME-SHA256SUMS",
    "build/runtime/app.py",
    "build/runtime/requirements.txt",
    "build/wheelhouse/WHEELHOUSE-SHA256SUMS",
}
RUNTIME_ROOTS = (
    "app.py",
    "requirements.txt",
    "test_plan_viewer",
    "static",
    "templates",
    "project-template",
)
PRESERVATION_TOKENS = (
    "verify_only_platform_image_changed",
    "collect_opencode_hashes",
    "collect_secret_hashes",
    "snapshot_setup_state",
    "verify_dm_secret_mounts",
    "--no-deps --force-recreate platform",
    "OPENCODE_CONTAINER_BEFORE",
    "OPENCODE_CONTAINER_AFTER",
    "--pull=false",
    "--network=none",
)
FORBIDDEN_UPGRADE_PATTERNS = (
    r"docker-compose[^\n]*\bdown\b",
    r"docker\s+volume\s+(rm|prune)\b",
    r"\bTRUNCATE\s+(?:TABLE\s+)?",
    r"\bDELETE\s+FROM\b",
    r"\bDROP\s+TABLE\b",
)


class ValidationError(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        command,
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = result.stdout.decode("utf-8", errors="replace").strip()
        raise ValidationError(f"command failed ({' '.join(command)}): {detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as error:
            raise ValidationError(f"invalid manifest line {path}:{number}") from error
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError(f"invalid SHA-256 at {path}:{number}")
        if relative in entries:
            raise ValidationError(f"duplicate manifest path: {relative}")
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValidationError(f"unsafe manifest path: {relative}")
        entries[relative] = digest
    return entries


def verify_manifest(manifest: Path, base: Path, *, exact: bool) -> None:
    entries = read_manifest(manifest)
    for relative, expected in entries.items():
        target = base / relative
        if not target.is_file():
            raise ValidationError(f"manifest file missing: {target}")
        actual = sha256(target)
        if actual != expected:
            raise ValidationError(f"SHA-256 mismatch: {target}")
    if exact:
        actual_files = {
            path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()
        }
        if actual_files != set(entries):
            missing = sorted(set(entries) - actual_files)
            extra = sorted(actual_files - set(entries))
            raise ValidationError(f"manifest set mismatch missing={missing} extra={extra}")


def resolve_revision(repo: Path, revision: str) -> str:
    return run(["git", "rev-parse", f"{revision}^{{commit}}"], cwd=repo).stdout.decode().strip()


def parse_metadata(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValidationError(f"invalid metadata line: {line}")
        key, value = line.split("=", 1)
        result[key] = value
    return result


def runtime_source_prefix(repo: Path, target_revision: str) -> str:
    for prefix in ("", "test-plan-viewer/"):
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{target_revision}:{prefix}app.py"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return prefix
    raise ValidationError("target revision has neither public nor legacy runtime layout")


def verify_runtime(package: Path, repo: Path, target_revision: str) -> None:
    runtime = package / "build/runtime"
    manifest = package / "build/RUNTIME-SHA256SUMS"
    verify_manifest(manifest, runtime, exact=True)
    prefix = runtime_source_prefix(repo, target_revision)
    git_roots = [f"{prefix}{relative}" for relative in RUNTIME_ROOTS]
    command = [
        "git",
        "ls-tree",
        "-r",
        "--name-only",
        target_revision,
        "--",
        *git_roots,
    ]
    tracked = {
        line.removeprefix(prefix)
        for line in run(command, cwd=repo).stdout.decode().splitlines()
        if line
    }
    runtime_files = {
        path.relative_to(runtime).as_posix() for path in runtime.rglob("*") if path.is_file()
    }
    if runtime_files != tracked:
        raise ValidationError(
            "runtime is not the exact committed tree: "
            f"missing={sorted(tracked - runtime_files)} extra={sorted(runtime_files - tracked)}"
        )
    for relative in sorted(tracked):
        committed = run(
            ["git", "show", f"{target_revision}:{prefix}{relative}"],
            cwd=repo,
        ).stdout
        if (runtime / relative).read_bytes() != committed:
            raise ValidationError(f"runtime differs from committed source: {relative}")


def extract_python_heredocs(shell_source: str) -> list[str]:
    lines = shell_source.splitlines()
    blocks: list[str] = []
    index = 0
    marker = re.compile(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
    while index < len(lines):
        match = marker.search(lines[index])
        if not match:
            index += 1
            continue
        terminator = match.group(1)
        block: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip() != terminator:
            block.append(lines[index])
            index += 1
        if terminator == "PY":
            blocks.append("\n".join(block) + "\n")
        index += 1
    return blocks


def image_smoke_block(upgrade_source: str) -> str:
    candidates = [
        block
        for block in extract_python_heredocs(upgrade_source)
        if "import app" in block and "app.app.url_map" in block
    ]
    safe = [block for block in candidates if "platform_mysql_connection" not in block]
    if len(safe) != 1:
        raise ValidationError(
            f"expected one database-independent embedded image smoke block, found {len(safe)}"
        )
    return safe[0]


def verify_scripts(package: Path) -> str:
    scripts = sorted(package.glob("*.sh"))
    for script in scripts:
        run(["bash", "-n", str(script)])
        for number, block in enumerate(
            extract_python_heredocs(script.read_text(encoding="utf-8")), 1
        ):
            try:
                compile(block, f"{script.name}:python-heredoc-{number}", "exec")
            except SyntaxError as error:
                raise ValidationError(str(error)) from error
    if shutil.which("shellcheck"):
        run(["shellcheck", "-e", "SC1091", *[str(path) for path in scripts]])

    upgrade = (package / "upgrade.sh").read_text(encoding="utf-8")
    for token in PRESERVATION_TOKENS:
        if token not in upgrade:
            raise ValidationError(f"upgrade preservation token missing: {token}")
    for pattern in FORBIDDEN_UPGRADE_PATTERNS:
        if re.search(pattern, upgrade, flags=re.IGNORECASE):
            raise ValidationError(f"forbidden upgrade behavior matched: {pattern}")
    return image_smoke_block(upgrade)


def verify_package_manifest(package: Path) -> None:
    manifest = package / "SHA256SUMS"
    entries = read_manifest(manifest)
    actual = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path != manifest
    }
    if set(entries) != actual:
        raise ValidationError(
            f"package manifest set mismatch missing={sorted(actual - set(entries))} "
            f"extra={sorted(set(entries) - actual)}"
        )
    for relative, expected in entries.items():
        if sha256(package / relative) != expected:
            raise ValidationError(f"package SHA-256 mismatch: {relative}")


def verify_archive(archive: Path, package_name: str) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if not members:
            raise ValidationError("archive is empty")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValidationError(f"unsafe archive path: {member.name}")
            if not path.parts or path.parts[0] != package_name:
                raise ValidationError(f"unexpected archive root: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise ValidationError(f"unsupported archive member: {member.name}")
            if any(part == "__MACOSX" or part.startswith("._") for part in path.parts):
                raise ValidationError(f"macOS metadata in archive: {member.name}")
    sidecar = archive.with_name(archive.name + ".sha256")
    if not sidecar.is_file():
        raise ValidationError(f"archive checksum sidecar missing: {sidecar}")
    entries = read_manifest(sidecar)
    if entries != {archive.name: sha256(archive)}:
        raise ValidationError("archive checksum sidecar is incorrect")


def verify_image(image: str, revision: str, package: Path, smoke: str) -> str:
    output = (
        run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{.Id}}|{{.Os}}/{{.Architecture}}|{{.Config.User}}|"
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            ]
        )
        .stdout.decode()
        .strip()
    )
    image_id, architecture, user, image_revision = output.split("|", 3)
    if architecture != "linux/amd64":
        raise ValidationError(f"target image architecture is {architecture}")
    if user != "pwuser":
        raise ValidationError(f"target image user is {user}")
    if image_revision != revision:
        raise ValidationError(f"target image revision is {image_revision}")
    manifest = (package / "build/RUNTIME-SHA256SUMS").read_bytes()
    run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "-i",
            "--entrypoint",
            "bash",
            image,
            "-lc",
            "cd /opt/playwright-platform/app && sha256sum -c - >/dev/null",
        ],
        input_bytes=manifest,
    )
    smoke_result = run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "-i",
            "--entrypoint",
            "python3",
            image,
            "-",
        ],
        input_bytes=smoke.encode("utf-8"),
    )
    sys.stdout.write(smoke_result.stdout.decode("utf-8", errors="replace"))
    return image_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--image")
    parser.add_argument("--archive", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package = args.package_dir.resolve()
    repo = args.repo.resolve()
    if not package.is_dir() or not repo.is_dir():
        raise ValidationError("package directory or repository does not exist")
    missing = sorted(path for path in REQUIRED_FILES if not (package / path).is_file())
    if missing:
        raise ValidationError(f"required package files missing: {missing}")

    base = resolve_revision(repo, args.base_revision)
    target = resolve_revision(repo, args.target_revision)
    metadata = parse_metadata(package / "TARGET-METADATA.env")
    if metadata.get("TARGET_FULL_REVISION") != target:
        raise ValidationError("TARGET-METADATA.env target revision mismatch")
    if args.image and metadata.get("TARGET_IMAGE") != args.image:
        raise ValidationError("TARGET-METADATA.env target image mismatch")
    combined_text = "\n".join(
        (package / name).read_text(encoding="utf-8", errors="replace")
        for name in ("README-zh-CN.md", "SOURCE-SNAPSHOT.md", "VERIFICATION.md", "upgrade.sh")
    )
    if base not in combined_text or target not in combined_text:
        raise ValidationError("base or target full revision is absent from package metadata")

    verify_runtime(package, repo, target)
    verify_manifest(
        package / "build/wheelhouse/WHEELHOUSE-SHA256SUMS",
        package / "build/wheelhouse",
        exact=False,
    )
    wheel_files = {path.name for path in (package / "build/wheelhouse").glob("*.whl")}
    wheel_entries = set(read_manifest(package / "build/wheelhouse/WHEELHOUSE-SHA256SUMS"))
    if wheel_files != wheel_entries:
        raise ValidationError("wheelhouse manifest does not exactly cover all wheels")
    smoke = verify_scripts(package)
    verify_package_manifest(package)
    if args.archive:
        verify_archive(args.archive.resolve(), package.name)
    image_id = "not-requested"
    if args.image:
        image_id = verify_image(args.image, target, package, smoke)

    print(f"base_revision={base}")
    print(f"target_revision={target}")
    print(f"target_image_id={image_id}")
    print("runtime_git_snapshot=PASS")
    print("wheelhouse_checksums=PASS")
    print("embedded_python_and_shell=PASS")
    print("preservation_contract=PASS")
    print("package_and_archive=PASS")
    print("OFFLINE_UPGRADE_PACKAGE_VALIDATION=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as error:
        print(f"VALIDATION_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
