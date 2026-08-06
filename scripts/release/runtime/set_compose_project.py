#!/usr/bin/env python3
"""Set exactly one controlled COMPOSE_PROJECT_NAME in an install environment."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")


def update(path: Path, project: str) -> None:
    if not PROJECT_RE.fullmatch(project):
        raise ValueError("compose project name is invalid")
    lines = path.read_text(encoding="utf-8").splitlines()
    indexes = [
        index
        for index, line in enumerate(lines)
        if line.partition("=")[0].strip() == "COMPOSE_PROJECT_NAME" and not line.lstrip().startswith("#")
    ]
    if len(indexes) > 1:
        raise ValueError("environment contains duplicate COMPOSE_PROJECT_NAME entries")
    replacement = f"COMPOSE_PROJECT_NAME={project}"
    if indexes:
        lines[indexes[0]] = replacement
    else:
        if lines and lines[-1]:
            lines.append("")
        lines.extend(["# Immutable installation identity selected by bin/install.", replacement])
    content = "\n".join(lines) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", type=Path)
    parser.add_argument("project")
    args = parser.parse_args()
    try:
        update(args.environment, args.project)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
