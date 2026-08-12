"""Project persistence with application-owned dependencies."""

import json
from dataclasses import dataclass
from typing import Callable

from test_plan_viewer.configuration import (
    DEFAULT_COVERAGE_PROFILE,
    DEFAULT_PROJECT_LANGUAGE,
    DEFAULT_TARGET_SYSTEM_CONFIG,
    DISABLED_DATABASE_BASELINE_CONFIG,
    PROJECT_STATUS_ACTIVE,
    normalize_project_language,
)


@dataclass(frozen=True)
class ProjectRepositoryDependencies:
    """Runtime collaborators supplied by the application composition root."""

    get_platform_database_config: Callable
    ensure_platform_database_schema: Callable
    get_platform_projects_table: Callable
    platform_mysql_connection: Callable
    get_config_projects: Callable
    get_config_default_project: Callable
    serialize_project_row: Callable
    parse_plan_generation_config: Callable
    current_time_ms: Callable
    platform_table_sql: Callable = lambda _config, name: f"`{name}`"


PROJECT_SCOPED_TABLES_DELETE_ORDER = (
    "agent_item_retry_flows",
    "agent_run_attempts",
    "agent_run_events",
    "agent_run_steps",
    "agent_runs",
    "requirement_module_plans",
    "requirement_modules",
    "requirements",
    "setup_runs",
    "setup_bindings",
    "setup_scripts",
    "test_suite_items",
    "test_suites",
    "test_run_artifacts",
    "test_run_results",
    "test_runs",
    "job_artifacts",
    "test_jobs",
    "platform_jobs",
    "platform_records",
    "page_inventory",
)

PROJECT_ACTIVE_STATUS_TABLES = {
    "platform_jobs": ("queued", "running", "cancelling"),
    "test_jobs": ("queued", "running", "cancelling"),
    "test_runs": ("queued", "running", "cancelling"),
    "setup_runs": ("queued", "running", "cancelling"),
    "agent_runs": (
        "queued",
        "running",
        "cancelling",
        "awaiting_script_action",
    ),
    "agent_item_retry_flows": (
        "queued",
        "running",
        "finalizing",
        "cancelling",
    ),
}


def seed_platform_projects(cursor, config, dependencies):
    """Upsert configured projects while preserving database-owned settings."""

    projects_table = dependencies.get_platform_projects_table(config)
    now_ms = dependencies.current_time_ms()
    projects = dependencies.get_config_projects()
    default_project = dependencies.get_config_default_project()

    for project in projects:
        cursor.execute(
            f"""
            INSERT INTO {projects_table}
              (project_key, name, description, playwright_project_root, specs_dir, tests_dir,
               opencode_config_json, target_system_json, database_baseline_json, plan_generation_json,
               language_code, status, is_default, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              name = VALUES(name),
              description = VALUES(description),
              playwright_project_root = VALUES(playwright_project_root),
              specs_dir = VALUES(specs_dir),
              tests_dir = VALUES(tests_dir),
              opencode_config_json = VALUES(opencode_config_json),
              target_system_json = COALESCE(VALUES(target_system_json), target_system_json),
              database_baseline_json = VALUES(database_baseline_json),
              plan_generation_json = COALESCE(VALUES(plan_generation_json), plan_generation_json),
              status = VALUES(status),
              is_default = VALUES(is_default),
              updated_at = VALUES(updated_at)
            """,
            (
                project["project_key"],
                project["name"],
                project.get("description") or "",
                project["playwright_project_root"],
                project.get("specs_dir") or "specs",
                project.get("tests_dir") or "tests",
                (
                    _compact_json(project.get("opencode_config"))
                    if project.get("opencode_config")
                    else None
                ),
                (
                    _compact_json(project.get("target_system"))
                    if project.get("target_system")
                    else None
                ),
                (
                    _compact_json(project.get("database_baseline"))
                    if project.get("database_baseline")
                    else None
                ),
                _compact_json(
                    dependencies.parse_plan_generation_config(
                        project.get("plan_generation")
                    )
                ),
                normalize_project_language(
                    project.get("language")
                    or project.get("language_code")
                ),
                project.get("status") or PROJECT_STATUS_ACTIVE,
                int(
                    project["project_key"]
                    == default_project["project_key"]
                ),
                now_ms,
                now_ms,
            ),
        )

    cursor.execute(
        f"""
        UPDATE {projects_table}
        SET is_default = CASE WHEN project_key = %s THEN 1 ELSE 0 END,
            updated_at = %s
        """,
        (default_project["project_key"], now_ms),
    )


def get_default_project_id_from_cursor(cursor, config, dependencies):
    """Resolve the active default project id within an existing transaction."""

    projects_table = dependencies.get_platform_projects_table(config)
    cursor.execute(
        f"""
        SELECT project_id
        FROM {projects_table}
        WHERE status = %s
        ORDER BY is_default DESC, project_id ASC
        LIMIT 1
        """,
        (PROJECT_STATUS_ACTIVE,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("未找到默认项目。")
    return int(row["project_id"])


def list_projects(dependencies):
    """List active projects from MySQL or the configuration fallback."""

    config = dependencies.get_platform_database_config()
    if not config.get("enabled"):
        return [dependencies.get_config_default_project()]
    dependencies.ensure_platform_database_schema(config)
    projects_table = dependencies.get_platform_projects_table(config)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {projects_table}
                WHERE status = %s
                ORDER BY is_default DESC, name ASC, project_id ASC
                """,
                (PROJECT_STATUS_ACTIVE,),
            )
            rows = cursor.fetchall()
    return [dependencies.serialize_project_row(row) for row in rows]


def assert_project_key_available(config, project_key, dependencies):
    """Reject an already-persisted project key."""

    projects_table = dependencies.get_platform_projects_table(config)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"SELECT project_id FROM {projects_table} "
                    "WHERE project_key = %s LIMIT 1"
                ),
                (project_key,),
            )
            if cursor.fetchone():
                raise ValueError(f"项目标识已存在：{project_key}")


def create_project_record(
    config,
    project,
    project_root,
    dependencies,
):
    """Insert one already-scaffolded project and return its resolved row."""

    projects_table = dependencies.get_platform_projects_table(config)
    now_ms = dependencies.current_time_ms()
    project_key = project["project_key"]
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"SELECT project_id FROM {projects_table} "
                    "WHERE project_key = %s LIMIT 1"
                ),
                (project_key,),
            )
            if cursor.fetchone():
                raise ValueError(f"项目标识已存在：{project_key}")

            cursor.execute(
                f"""
                INSERT INTO {projects_table}
                  (project_key, name, description, playwright_project_root, specs_dir, tests_dir,
                   opencode_config_json, target_system_json, database_baseline_json, plan_generation_json,
                   language_code, status, is_default, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, 0, %s, %s)
                """,
                (
                    project_key,
                    project["name"],
                    project["description"],
                    str(project_root),
                    project["specs_dir"],
                    project["tests_dir"],
                    _compact_json(DEFAULT_TARGET_SYSTEM_CONFIG),
                    _compact_json(DISABLED_DATABASE_BASELINE_CONFIG),
                    _compact_json(
                        {
                            "default_coverage_profile": (
                                DEFAULT_COVERAGE_PROFILE
                            )
                        }
                    ),
                    normalize_project_language(
                        project.get("language"),
                        default=DEFAULT_PROJECT_LANGUAGE,
                    ),
                    PROJECT_STATUS_ACTIVE,
                    now_ms,
                    now_ms,
                ),
            )
            cursor.execute(
                f"SELECT * FROM {projects_table} "
                "WHERE project_key = %s LIMIT 1",
                (project_key,),
            )
            created_project = dependencies.serialize_project_row(
                cursor.fetchone()
            )
        connection.commit()
    return created_project


def get_project_by_key(project_key, dependencies):
    """Resolve an active project by key, with configuration fallback."""

    requested_key = str(project_key or "").strip()
    config = dependencies.get_platform_database_config()
    if not config.get("enabled"):
        projects = dependencies.get_config_projects()
        if requested_key:
            for project in projects:
                if project["project_key"] == requested_key:
                    return {**project, "project_id": None}
        return dependencies.get_config_default_project()

    dependencies.ensure_platform_database_schema(config)
    projects_table = dependencies.get_platform_projects_table(config)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            if requested_key:
                cursor.execute(
                    (
                        f"SELECT * FROM {projects_table} "
                        "WHERE project_key = %s AND status = %s LIMIT 1"
                    ),
                    (requested_key, PROJECT_STATUS_ACTIVE),
                )
            else:
                cursor.execute(
                    (
                        f"SELECT * FROM {projects_table} "
                        "WHERE status = %s "
                        "ORDER BY is_default DESC, project_id ASC LIMIT 1"
                    ),
                    (PROJECT_STATUS_ACTIVE,),
                )
            row = cursor.fetchone()
    if row:
        return dependencies.serialize_project_row(row)
    if requested_key:
        raise ValueError(f"项目不存在或已禁用：{requested_key}")
    return dependencies.get_config_default_project()


def update_project_settings(
    config,
    project_key,
    target_system,
    database_baseline,
    plan_generation,
    dependencies,
):
    """Persist settings for one active project."""

    projects_table = dependencies.get_platform_projects_table(config)
    now_ms = dependencies.current_time_ms()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {projects_table}
                SET target_system_json = %s,
                    database_baseline_json = %s,
                    plan_generation_json = %s,
                    updated_at = %s
                WHERE project_key = %s AND status = %s
                """,
                (
                    _compact_json(target_system),
                    _compact_json(database_baseline),
                    _compact_json(plan_generation),
                    now_ms,
                    project_key,
                    PROJECT_STATUS_ACTIVE,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"项目不存在或已禁用：{project_key}"
                )
            cursor.execute(
                f"SELECT * FROM {projects_table} "
                "WHERE project_key = %s LIMIT 1",
                (project_key,),
            )
            updated_project = dependencies.serialize_project_row(
                cursor.fetchone()
            )
        connection.commit()
    return updated_project


def update_project_metadata(
    config,
    project_key,
    metadata,
    dependencies,
):
    """Update the user-editable name and description for one project."""

    projects_table = dependencies.get_platform_projects_table(config)
    now_ms = dependencies.current_time_ms()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {projects_table}
                SET name = %s, description = %s, updated_at = %s
                WHERE project_key = %s AND status = %s
                """,
                (
                    metadata["name"],
                    metadata["description"],
                    now_ms,
                    project_key,
                    PROJECT_STATUS_ACTIVE,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"项目不存在或已禁用：{project_key}")
            cursor.execute(
                f"SELECT * FROM {projects_table} "
                "WHERE project_key = %s LIMIT 1",
                (project_key,),
            )
            updated_project = dependencies.serialize_project_row(
                cursor.fetchone()
            )
        connection.commit()
    return updated_project


def _assert_project_has_no_active_work(
    cursor,
    config,
    project_id,
    dependencies,
):
    for table_name, statuses in PROJECT_ACTIVE_STATUS_TABLES.items():
        placeholders = ", ".join("%s" for _status in statuses)
        cursor.execute(
            f"SELECT COUNT(*) AS total FROM "
            f"{dependencies.platform_table_sql(config, table_name)} "
            f"WHERE project_id = %s AND status IN ({placeholders})",
            (project_id, *statuses),
        )
        if int((cursor.fetchone() or {}).get("total") or 0) > 0:
            raise ValueError("项目仍有运行中或待处理的任务，暂不能删除。")


def delete_project_data(config, project_key, dependencies):
    """Permanently delete one project and every project-scoped DB row."""

    projects_table = dependencies.get_platform_projects_table(config)
    assets_table = dependencies.platform_table_sql(config, "test_assets")
    revisions_table = dependencies.platform_table_sql(
        config,
        "test_asset_revisions",
    )
    deleted_counts = {}

    with dependencies.platform_mysql_connection(config) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {projects_table} "
                    "WHERE project_key = %s AND status = %s "
                    "LIMIT 1 FOR UPDATE",
                    (project_key, PROJECT_STATUS_ACTIVE),
                )
                project_row = cursor.fetchone()
                if not project_row:
                    raise ValueError(
                        f"项目不存在或已禁用：{project_key}"
                    )
                if bool(project_row.get("is_default")):
                    raise ValueError("默认项目不能删除。")

                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {projects_table} "
                    "WHERE status = %s",
                    (PROJECT_STATUS_ACTIVE,),
                )
                if int((cursor.fetchone() or {}).get("total") or 0) <= 1:
                    raise ValueError("至少需要保留一个有效项目。")

                project_id = int(project_row["project_id"])
                _assert_project_has_no_active_work(
                    cursor,
                    config,
                    project_id,
                    dependencies,
                )

                cursor.execute(
                    f"""
                    DELETE FROM {revisions_table}
                    WHERE asset_id IN (
                      SELECT asset_id FROM {assets_table}
                      WHERE project_id = %s
                    )
                    """,
                    (project_id,),
                )
                deleted_counts["test_asset_revisions"] = cursor.rowcount

                for table_name in PROJECT_SCOPED_TABLES_DELETE_ORDER:
                    cursor.execute(
                        "DELETE FROM "
                        f"{dependencies.platform_table_sql(config, table_name)} "
                        "WHERE project_id = %s",
                        (project_id,),
                    )
                    deleted_counts[table_name] = cursor.rowcount

                cursor.execute(
                    f"DELETE FROM {assets_table} WHERE project_id = %s",
                    (project_id,),
                )
                deleted_counts["test_assets"] = cursor.rowcount
                cursor.execute(
                    f"DELETE FROM {projects_table} "
                    "WHERE project_id = %s AND is_default = 0",
                    (project_id,),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("项目主记录删除失败。")
                deleted_counts["platform_projects"] = cursor.rowcount
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "project_id": int(project_row["project_id"]),
        "project_key": project_row["project_key"],
        "name": project_row.get("name") or project_row["project_key"],
        "deleted_records": sum(
            max(int(value or 0), 0)
            for value in deleted_counts.values()
        ),
        "deleted_counts": deleted_counts,
    }


def update_project_language(config, project_key, language, dependencies):
    """Persist the shared UI and prompt language for one active project."""

    language = normalize_project_language(language)
    projects_table = dependencies.get_platform_projects_table(config)
    now_ms = dependencies.current_time_ms()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {projects_table}
                SET language_code = %s, updated_at = %s
                WHERE project_key = %s AND status = %s
                """,
                (language, now_ms, project_key, PROJECT_STATUS_ACTIVE),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"项目不存在或已禁用：{project_key}")
            cursor.execute(
                f"SELECT * FROM {projects_table} "
                "WHERE project_key = %s LIMIT 1",
                (project_key,),
            )
            updated_project = dependencies.serialize_project_row(
                cursor.fetchone()
            )
        connection.commit()
    return updated_project


def _compact_json(value):
    if value is None:
        return None
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
