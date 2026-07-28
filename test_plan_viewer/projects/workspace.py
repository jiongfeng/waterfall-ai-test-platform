"""Filesystem scaffolding for newly-created Playwright projects."""

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ProjectWorkspaceDependencies:
    """Explicit configuration, template, and command collaborators."""

    load_config: Callable
    template_dir: Path
    dependency_dirs: tuple
    text_suffixes: frozenset
    subprocess_run: Callable
    get_project_workspace_root_text: Callable
    get_project_template_dependency_source_text: Callable
    get_project_dependency_source_root_for_create: Callable
    template_relative_target_path: Callable
    render_project_template_text: Callable
    copy_project_template_files: Callable
    copy_project_template_dependencies: Callable
    run_project_git_command: Callable
    initialize_created_project_git_repo: Callable


def get_project_workspace_root_text(dependencies):
    config = dependencies.load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    return str(config.get("project_workspace_root") or "").strip()


def get_project_template_dependency_source_text(dependencies):
    config = dependencies.load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    return str(
        config.get("project_template_dependency_source_root")
        or config.get("playwright_project_root")
        or ""
    ).strip()


def get_project_workspace_root_for_create(dependencies):
    workspace_root_text = dependencies.get_project_workspace_root_text()
    if not workspace_root_text:
        raise ValueError(
            "config.json project_workspace_root is required before "
            "creating projects."
        )
    return Path(workspace_root_text).expanduser().resolve(strict=False)


def get_project_dependency_source_root_for_create(dependencies):
    source_root_text = (
        dependencies.get_project_template_dependency_source_text()
    )
    if not source_root_text:
        raise ValueError(
            "config.json project_template_dependency_source_root or "
            "playwright_project_root is required."
        )
    source_root = Path(source_root_text).expanduser().resolve(strict=False)
    if not source_root.is_dir():
        raise ValueError(f"项目模板依赖源目录不存在：{source_root}")
    return source_root


def get_created_project_root(workspace_root, project_key):
    if project_key in {".", ".."}:
        raise ValueError("项目标识不能是 '.' 或 '..'。")
    workspace_root = Path(workspace_root)
    project_root = (workspace_root / project_key).resolve(strict=False)
    try:
        project_root.relative_to(workspace_root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(
            "项目目录必须位于 project_workspace_root 内。"
        ) from exc
    return project_root


def template_relative_target_path(relative_path, specs_dir, tests_dir):
    parts = Path(relative_path).parts
    if parts and parts[0] == "specs":
        return Path(specs_dir).joinpath(*parts[1:])
    if parts and parts[0] == "tests":
        return Path(tests_dir).joinpath(*parts[1:])
    return Path(relative_path)


def npm_package_name_from_project_key(project_key):
    package_name = re.sub(
        r"[^a-z0-9._-]+",
        "-",
        project_key.lower(),
    ).strip("._-")
    return package_name or "playwright-tests"


def render_project_template_text(
    text,
    project_key,
    name,
    specs_dir,
    tests_dir,
    *,
    npm_package_name=npm_package_name_from_project_key,
):
    replacements = {
        "{{PROJECT_KEY}}": project_key,
        "{{PROJECT_NAME}}": name,
        "{{PACKAGE_NAME}}": npm_package_name(project_key),
        "{{SPECS_DIR}}": specs_dir,
        "{{TESTS_DIR}}": tests_dir,
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def mark_generated_workspace_unlicensed(project_root):
    """Remove the source-template marker and set generated manifests private."""

    project_root = Path(project_root)
    package_path = project_root / "package.json"
    if not package_path.is_file():
        return False

    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not package.pop("x-playwright-platform-template", False):
        return False
    package["private"] = True
    package["license"] = "UNLICENSED"
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )

    lock_path = project_root / "package-lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        root_package = (lock.get("packages") or {}).get("")
        if isinstance(root_package, dict):
            root_package["license"] = "UNLICENSED"
        lock_path.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
    return True


def copy_project_template_files(
    project_root,
    project_key,
    name,
    specs_dir,
    tests_dir,
    dependencies,
):
    template_dir = Path(dependencies.template_dir)
    if not template_dir.is_dir():
        raise ValueError(f"项目模板目录不存在：{template_dir}")
    dependency_dirs = tuple(
        Path(relative_dir)
        for relative_dir in dependencies.dependency_dirs
    )

    for source_file in template_dir.rglob("*"):
        relative_path = source_file.relative_to(template_dir)
        if any(
            relative_path == dependency_dir
            or dependency_dir in relative_path.parents
            for dependency_dir in dependency_dirs
        ):
            continue
        if not source_file.is_file():
            continue
        target_file = (
            Path(project_root)
            / dependencies.template_relative_target_path(
                relative_path,
                specs_dir,
                tests_dir,
            )
        )
        target_file.parent.mkdir(parents=True, exist_ok=True)

        if source_file.suffix.lower() in dependencies.text_suffixes:
            try:
                content = source_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(source_file, target_file)
                continue
            content = dependencies.render_project_template_text(
                content,
                project_key,
                name,
                specs_dir,
                tests_dir,
            )
            target_file.write_text(
                content,
                encoding="utf-8",
                newline="",
            )
        else:
            shutil.copy2(source_file, target_file)

    (Path(project_root) / specs_dir).mkdir(
        parents=True,
        exist_ok=True,
    )
    (Path(project_root) / tests_dir).mkdir(
        parents=True,
        exist_ok=True,
    )
    mark_generated_workspace_unlicensed(project_root)


def copy_project_template_dependencies(
    source_root,
    project_root,
    dependencies,
):
    for relative_dir in dependencies.dependency_dirs:
        source_dir = Path(source_root) / relative_dir
        if not source_dir.is_dir():
            raise ValueError(f"项目模板依赖源缺少目录：{source_dir}")
        target_dir = Path(project_root) / relative_dir
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir, symlinks=True)


def run_project_git_command(project_root, args, dependencies):
    completed = dependencies.subprocess_run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "diff.external=",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"Git 命令失败：git {' '.join(args)}\n{stderr}"
        )
    return completed


def initialize_created_project_git_repo(
    project_root,
    dependencies,
):
    dependencies.run_project_git_command(project_root, ["init"])
    dependencies.run_project_git_command(project_root, ["add", "."])
    dependencies.run_project_git_command(
        project_root,
        [
            "-c",
            "user.name=Test Plan Viewer",
            "-c",
            "user.email=test-plan-viewer@local",
            "commit",
            "-m",
            "chore: scaffold playwright project",
        ],
    )


def initialize_created_project_directory(
    project_root,
    project_key,
    name,
    specs_dir,
    tests_dir,
    dependencies,
):
    dependencies.copy_project_template_files(
        project_root,
        project_key,
        name,
        specs_dir,
        tests_dir,
    )
    dependencies.copy_project_template_dependencies(
        dependencies.get_project_dependency_source_root_for_create(),
        project_root,
    )
    dependencies.initialize_created_project_git_repo(project_root)
