"""Framework-independent project and author context primitives.

Request parsing and project persistence deliberately remain in ``app.py``.
This module only owns thread-local overrides and pure helpers that operate on
already-resolved project data.
"""

from contextlib import contextmanager
from pathlib import Path
import threading


PROJECT_CONTEXT = threading.local()
AUTHOR_CONTEXT = threading.local()


def current_context_project():
    """Return the project override for this thread, if one is active."""

    return getattr(PROJECT_CONTEXT, "project", None)


def current_author(default="platform"):
    """Return the author override for this thread or the supplied default."""

    return getattr(AUTHOR_CONTEXT, "author", None) or default


@contextmanager
def use_project_context(project):
    """Temporarily bind a resolved project to the current thread."""

    previous_project = getattr(PROJECT_CONTEXT, "project", None)
    PROJECT_CONTEXT.project = project
    try:
        yield
    finally:
        PROJECT_CONTEXT.project = previous_project


@contextmanager
def use_author_context(author):
    """Temporarily bind an audit author to the current thread."""

    previous_author = getattr(AUTHOR_CONTEXT, "author", None)
    AUTHOR_CONTEXT.author = author
    try:
        yield
    finally:
        AUTHOR_CONTEXT.author = previous_author


def project_root(project):
    """Return the expanded Playwright root for resolved project data."""

    return Path(project["playwright_project_root"]).expanduser()


def project_specs_dir(project):
    """Return the configured specs directory for resolved project data."""

    return project_root(project) / (project.get("specs_dir") or "specs")


def project_tests_dir(project):
    """Return the configured tests directory for resolved project data."""

    return project_root(project) / (project.get("tests_dir") or "tests")


def path_relative_to_root(root, file_path):
    """Return ``file_path`` relative to ``root``, rejecting path escapes."""

    resolved_root = Path(root).resolve(strict=False)
    resolved_file = Path(file_path).resolve(strict=False)
    try:
        return resolved_file.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Path is outside project root.") from exc


def project_relative_path(project, file_path):
    """Return ``file_path`` relative to the root in resolved project data."""

    return path_relative_to_root(project_root(project), file_path)
