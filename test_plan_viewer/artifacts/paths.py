import re
import uuid
from pathlib import Path


def ensure_path_within_root(root, path, error_message):
    resolved_root = Path(root).resolve(strict=False)
    resolved_path = Path(path).resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(error_message) from exc
    return path


def build_plan_file(specs_dir, module_name, plan_filename):
    specs_dir = Path(specs_dir)
    plan_file = specs_dir / module_name / plan_filename
    return ensure_path_within_root(
        specs_dir,
        plan_file,
        "Resolved plan path is outside specs directory.",
    )


def build_script_module_dir(tests_dir, module_name):
    tests_dir = Path(tests_dir)
    module_dir = tests_dir / module_name
    return ensure_path_within_root(
        tests_dir,
        module_dir,
        "Resolved script directory is outside tests directory.",
    )


def build_script_file(tests_dir, module_name, filename):
    tests_dir = Path(tests_dir)
    script_file = tests_dir / module_name / filename
    return ensure_path_within_root(
        tests_dir,
        script_file,
        "Resolved script path is outside tests directory.",
    )


def build_generation_workspace_dir(
    project_root,
    helper_dir_name,
    generation_dir_name,
):
    return Path(project_root) / helper_dir_name / generation_dir_name


def build_script_generation_candidate_file(
    candidate_root,
    module_name,
    filename,
    job_id,
):
    candidate_root = Path(candidate_root)
    safe_job_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        str(job_id or "").strip(),
    ).strip(".-")
    if not safe_job_id:
        safe_job_id = uuid.uuid4().hex

    candidate_file = candidate_root / safe_job_id / module_name / filename
    return ensure_path_within_root(
        candidate_root,
        candidate_file,
        "Resolved candidate script path is outside generation directory.",
    )


def build_script_generation_backup_dir(backup_root, module_name):
    backup_root = Path(backup_root)
    backup_dir = backup_root / module_name
    return ensure_path_within_root(
        backup_root,
        backup_dir,
        "Resolved backup path is outside generation backup directory.",
    )


def workspace_relative_path(project_root, path):
    resolved_root = Path(project_root).resolve(strict=False)
    resolved_path = Path(path).expanduser().resolve(strict=False)
    return resolved_path.relative_to(resolved_root).as_posix()
