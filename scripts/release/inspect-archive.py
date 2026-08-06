#!/usr/bin/env python3
"""Inspect a zstd-compressed tar without extracting or trusting bundle code."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tarfile


MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBERS = 20_000
SAFE_NAME = re.compile(r"^[A-Za-z0-9._/+:-]+$")
SAFE_ROOT = re.compile(r"^playwright-test-platform-[0-9A-Za-z.-]+-linux-amd64$")


def inspect_archive(path: pathlib.Path) -> str:
    stat = path.lstat()
    if not path.is_file() or path.is_symlink():
        raise ValueError("bundle must be a regular, non-symlink file")
    if stat.st_size <= 0 or stat.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("compressed bundle size is outside the supported boundary")

    process = subprocess.Popen(
        ["zstd", "--decompress", "--stdout", "--", os.fspath(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    names: set[str] = set()
    roots: set[str] = set()
    expanded_size = 0
    count = 0
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                count += 1
                if count > MAX_MEMBERS:
                    raise ValueError("bundle contains too many members")
                name = member.name.rstrip("/")
                if not name or not SAFE_NAME.fullmatch(name):
                    raise ValueError(f"unsafe archive member name: {member.name!r}")
                pure_path = pathlib.PurePosixPath(name)
                if pure_path.is_absolute() or ".." in pure_path.parts:
                    raise ValueError(f"unsafe archive path: {member.name!r}")
                if name in names:
                    raise ValueError(f"duplicate archive member: {name}")
                names.add(name)
                roots.add(pure_path.parts[0])
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"links and special members are forbidden: {name}")
                if member.mode & 0o7000:
                    raise ValueError(f"setuid, setgid, and sticky modes are forbidden: {name}")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member is too large: {name}")
                expanded_size += member.size
                if expanded_size > MAX_EXPANDED_BYTES:
                    raise ValueError("expanded bundle exceeds the supported size boundary")
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError(f"invalid tar stream: {exc}") from exc
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise ValueError(f"zstd decompression failed: {stderr.strip()}")
    if len(roots) != 1:
        raise ValueError("bundle must contain exactly one top-level directory")
    root = roots.pop()
    if not SAFE_ROOT.fullmatch(root):
        raise ValueError(f"unexpected bundle root: {root}")
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=pathlib.Path)
    args = parser.parse_args()
    try:
        print(inspect_archive(args.archive))
    except (OSError, ValueError) as exc:
        print(f"archive inspection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
