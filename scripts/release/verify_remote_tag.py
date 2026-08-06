#!/usr/bin/env python3
"""Resolve a current GitHub tag ref and require its peeled commit identity."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_identity(value: object) -> tuple[str, str]:
    require(isinstance(value, dict), "GitHub tag response must be an object")
    object_value = value.get("object")
    require(isinstance(object_value, dict), "GitHub tag response has no object identity")
    object_type = object_value.get("type")
    sha = object_value.get("sha")
    require(object_type in {"commit", "tag"}, "GitHub tag points to an unsupported object type")
    require(isinstance(sha, str) and SHA_RE.fullmatch(sha) is not None,
            "GitHub tag object SHA is invalid")
    return object_type, sha


def gh_json(endpoint: str) -> object:
    result = subprocess.run(
        ["gh", "api", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def resolve(repository: str, tag: str) -> str:
    object_type, sha = object_identity(
        gh_json(f"repos/{repository}/git/ref/tags/{tag}")
    )
    seen: set[str] = set()
    for _ in range(16):
        if object_type == "commit":
            return sha
        require(sha not in seen, "GitHub tag object chain contains a cycle")
        seen.add(sha)
        object_type, sha = object_identity(
            gh_json(f"repos/{repository}/git/tags/{sha}")
        )
    raise ValueError("GitHub tag object chain is too deep")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        require(REPOSITORY_RE.fullmatch(args.repository) is not None, "repository is invalid")
        require(TAG_RE.fullmatch(args.tag) is not None, "tag is invalid")
        require(SHA_RE.fullmatch(args.expected_commit) is not None, "expected commit is invalid")
        actual = resolve(args.repository, args.tag)
        require(actual == args.expected_commit,
                f"remote tag moved: expected {args.expected_commit}, got {actual}")
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"remote tag verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified refs/tags/{args.tag} -> {args.expected_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
