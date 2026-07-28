"""Safe requirement-file storage and recovery."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet


@dataclass(frozen=True)
class RequirementStorageDependencies:
    """Filesystem capabilities supplied by the application."""

    validate_uid: Callable[[object, str], str]
    get_project_root: Callable[[], Path]
    app_dir: Path
    get_cwd: Callable[[], Path]
    walk: Callable
    sha256_file: Callable[[Path], str]
    write_file_atomically: Callable[[Path, bytes], None]
    recovery_excluded_dirs: FrozenSet[str]
    recovery_max_candidates: int


class RequirementStorage:
    """Project-scoped Markdown storage with bounded recovery search."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            RequirementStorageDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "RequirementStorageDependencies instance"
            )
        self.dependencies = dependencies

    @staticmethod
    def validate_filename(filename):
        filename = Path(str(filename or "")).name.strip()
        if not filename or filename in {".", ".."}:
            raise ValueError("需求文件名不能为空。")
        if (
            "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise ValueError("需求文件名不能包含路径分隔符。")
        if not filename.lower().endswith(".md"):
            raise ValueError(
                "第一阶段只支持上传 Markdown .md 文件。"
            )
        return filename

    def get_requirements_dir(self):
        return self.dependencies.get_project_root() / "requirements"

    def get_storage_file(self, requirement_uid, filename):
        requirement_uid = self.dependencies.validate_uid(
            requirement_uid,
            "requirement_uid",
        )
        filename = self.validate_filename(filename)
        root = self.get_requirements_dir()
        target = root / requirement_uid / filename
        try:
            target.resolve(strict=False).relative_to(
                root.resolve(strict=False)
            )
        except ValueError as exc:
            raise ValueError(
                "需求文件路径必须位于 requirements 目录内。"
            ) from exc
        return target

    def get_recovery_roots(self):
        project_root = self.dependencies.get_project_root()
        app_dir = Path(self.dependencies.app_dir)
        roots = [
            self.get_requirements_dir(),
            project_root,
            project_root.parent,
            app_dir,
            app_dir.parent,
            self.dependencies.get_cwd(),
        ]
        resolved_roots = []
        seen = set()
        for root in roots:
            try:
                resolved = Path(root).expanduser().resolve(
                    strict=False
                )
            except OSError:
                continue
            key = str(resolved)
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            resolved_roots.append(resolved)
        return resolved_roots

    def iter_recovery_candidates(self, filename):
        filename = self.validate_filename(filename)
        yielded = 0
        for root in self.get_recovery_roots():
            for dirpath, dirnames, filenames in (
                self.dependencies.walk(root)
            ):
                dirnames[:] = [
                    dirname
                    for dirname in dirnames
                    if dirname
                    not in self.dependencies.recovery_excluded_dirs
                ]
                if filename not in filenames:
                    continue
                yield Path(dirpath) / filename
                yielded += 1
                if (
                    yielded
                    >= self.dependencies.recovery_max_candidates
                ):
                    return

    def recover_missing_file(self, row, target_path):
        expected_sha = str(
            row.get("content_sha256") or ""
        ).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            return None

        filename = self.validate_filename(
            row.get("filename") or Path(target_path).name
        )
        target_path = Path(target_path)
        try:
            requirements_root = self.get_requirements_dir().resolve(
                strict=False
            )
            target_path.resolve(strict=False).relative_to(
                requirements_root
            )
        except (OSError, ValueError):
            return None

        try:
            target_resolved = target_path.resolve(strict=False)
        except OSError:
            target_resolved = target_path

        for candidate in self.iter_recovery_candidates(filename):
            try:
                candidate_resolved = candidate.resolve(strict=False)
            except OSError:
                continue
            if (
                candidate_resolved == target_resolved
                or not candidate.is_file()
            ):
                continue
            if (
                self.dependencies.sha256_file(candidate)
                != expected_sha
            ):
                continue
            self.dependencies.write_file_atomically(
                target_path,
                candidate.read_bytes(),
            )
            return target_path
        return None

    def read_markdown(self, row):
        path = Path(row.get("file_path") or "")
        if not path.exists():
            recovered_path = self.recover_missing_file(row, path)
            if recovered_path:
                path = recovered_path
        if not path.exists():
            raise FileNotFoundError(f"需求文件不存在：{path}")
        return path.read_text(encoding="utf-8")


def validate_requirement_filename(filename):
    """Compatibility-friendly entry point for filename validation."""

    return RequirementStorage.validate_filename(filename)


def default_storage_dependencies(
    *,
    validate_uid,
    get_project_root,
    app_dir,
    sha256_file,
    write_file_atomically,
    recovery_excluded_dirs,
    recovery_max_candidates,
):
    """Build the production-shaped dependency record."""

    return RequirementStorageDependencies(
        validate_uid=validate_uid,
        get_project_root=get_project_root,
        app_dir=Path(app_dir),
        get_cwd=Path.cwd,
        walk=os.walk,
        sha256_file=sha256_file,
        write_file_atomically=write_file_atomically,
        recovery_excluded_dirs=frozenset(
            recovery_excluded_dirs
        ),
        recovery_max_candidates=int(
            recovery_max_candidates
        ),
    )
