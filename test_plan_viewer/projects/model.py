"""Pure project payload normalization and serialization."""

import json

from test_plan_viewer.configuration import (
    PROJECT_STATUS_ACTIVE,
    parse_plan_generation_config,
    parse_target_system_config,
)


def normalize_create_project_payload(
    payload,
    *,
    parse_project_key,
    parse_project_path_segment,
):
    """Validate and normalize the stable project-creation payload."""

    if not isinstance(payload, dict):
        raise ValueError("Request body must be an object.")

    project_key = parse_project_key(
        payload.get("project_key") or payload.get("key"),
        "project_key",
    )
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("项目名称不能为空。")
    if len(name) > 128:
        raise ValueError("项目名称不能超过 128 个字符。")

    return {
        "project_key": project_key,
        "name": name,
        "description": str(payload.get("description") or "").strip()[:512],
        "specs_dir": parse_project_path_segment(
            payload.get("specs_dir"),
            "specs",
            "specs_dir",
        ),
        "tests_dir": parse_project_path_segment(
            payload.get("tests_dir"),
            "tests",
            "tests_dir",
        ),
    }


def serialize_project(
    project,
    include_sensitive=False,
    *,
    parse_target_system=parse_target_system_config,
    parse_plan_generation=parse_plan_generation_config,
):
    """Serialize resolved project data for the public HTTP contract."""

    if not project:
        return None
    target_system = parse_target_system(project.get("target_system"))
    if not include_sensitive:
        target_system = {**target_system, "password": ""}
    project_key = project.get("project_key") or project.get("key")
    return {
        "project_id": project.get("project_id"),
        "project_key": project_key,
        "key": project_key,
        "name": project.get("name") or project_key,
        "description": project.get("description") or "",
        "playwright_project_root": project.get("playwright_project_root") or "",
        "specs_dir": project.get("specs_dir") or "specs",
        "tests_dir": project.get("tests_dir") or "tests",
        "target_system": target_system,
        "database_baseline": project.get("database_baseline"),
        "opencode_config": project.get("opencode_config"),
        "plan_generation": parse_plan_generation(
            project.get("plan_generation")
        ),
        "status": project.get("status") or PROJECT_STATUS_ACTIVE,
        "is_default": bool(project.get("is_default")),
    }


def serialize_project_row(
    row,
    *,
    parse_target_system=parse_target_system_config,
    parse_plan_generation=parse_plan_generation_config,
):
    """Convert a database row into resolved project data."""

    if not row:
        return None
    project = {
        "project_id": row.get("project_id"),
        "project_key": row.get("project_key"),
        "name": row.get("name") or row.get("project_key"),
        "description": row.get("description") or "",
        "playwright_project_root": row.get("playwright_project_root") or "",
        "specs_dir": row.get("specs_dir") or "specs",
        "tests_dir": row.get("tests_dir") or "tests",
        "status": row.get("status") or PROJECT_STATUS_ACTIVE,
        "is_default": bool(row.get("is_default")),
    }
    for source_key, target_key in (
        ("opencode_config_json", "opencode_config"),
        ("target_system_json", "target_system"),
        ("database_baseline_json", "database_baseline"),
        ("plan_generation_json", "plan_generation"),
    ):
        raw_value = row.get(source_key)
        if raw_value:
            try:
                project[target_key] = json.loads(raw_value)
            except json.JSONDecodeError:
                project[target_key] = None
        else:
            project[target_key] = None
    project["target_system"] = parse_target_system(
        project.get("target_system")
    )
    project["plan_generation"] = parse_plan_generation(
        project.get("plan_generation")
    )
    return project
