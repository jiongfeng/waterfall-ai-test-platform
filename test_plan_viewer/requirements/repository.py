"""MySQL persistence for requirements and candidate modules."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RequirementRepositoryDependencies:
    """Application-owned database capabilities."""

    require_platform_database: Callable[[], dict]
    get_requirements_table: Callable[[dict], str]
    get_requirement_modules_table: Callable[[dict], str]
    get_agent_runs_table: Callable[[dict], str]
    get_current_project_id: Callable[[], int]
    platform_mysql_connection: Callable[[dict], object]
    validate_uid: Callable[[object, str], str]
    current_time_ms: Callable[[], int]
    compact_json_dumps: Callable[[object], str]
    get_requirement_by_uid: Callable[[str], dict]
    get_requirement_module: Callable[[int, str], dict]


class RequirementRepository:
    """Project-scoped requirement persistence."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            RequirementRepositoryDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "RequirementRepositoryDependencies instance"
            )
        self.dependencies = dependencies

    def get_requirement(
        self,
        requirement_uid,
        *,
        include_deleted=False,
    ):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirements_table(config)
        project_id = self.dependencies.get_current_project_id()
        requirement_uid = self.dependencies.validate_uid(
            requirement_uid,
            "requirement_uid",
        )
        status_filter = (
            "" if include_deleted else "AND status != 'deleted'"
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {table}
                    WHERE project_id = %s
                      AND requirement_uid = %s
                      {status_filter}
                    LIMIT 1
                    """,
                    (project_id, requirement_uid),
                )
                return cursor.fetchone()

    def list_requirements(self):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirements_table(config)
        modules_table = (
            self.dependencies.get_requirement_modules_table(
                config
            )
        )
        project_id = self.dependencies.get_current_project_id()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT r.*,
                           COUNT(
                             CASE WHEN m.status NOT IN (
                               'deleted', 'superseded'
                             ) THEN 1 END
                           ) AS module_count
                    FROM {table} r
                    LEFT JOIN {modules_table} m
                      ON m.project_id = r.project_id
                     AND m.requirement_id = r.id
                    WHERE r.project_id = %s
                      AND r.status != 'deleted'
                    GROUP BY r.id
                    ORDER BY r.updated_at DESC, r.id DESC
                    """,
                    (project_id,),
                )
                return cursor.fetchall()

    def create_uploaded_requirement(self, record):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirements_table(config)
        project_id = self.dependencies.get_current_project_id()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {table}
                      (project_id, requirement_uid, title, filename,
                       file_path, content_sha256, status, source_type,
                       created_by, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'active',
                            'upload', %s, %s, %s)
                    """,
                    (
                        project_id,
                        record["requirement_uid"],
                        record["title"],
                        record["filename"],
                        record["file_path"],
                        record["content_sha256"],
                        record["created_by"],
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
                connection.commit()
        return self.dependencies.get_requirement_by_uid(
            record["requirement_uid"]
        )

    def delete_requirement(self, requirement_uid):
        config = self.dependencies.require_platform_database()
        requirements_table = (
            self.dependencies.get_requirements_table(config)
        )
        modules_table = (
            self.dependencies.get_requirement_modules_table(
                config
            )
        )
        agent_runs_table = self.dependencies.get_agent_runs_table(
            config
        )
        project_id = self.dependencies.get_current_project_id()
        requirement_uid = self.dependencies.validate_uid(
            requirement_uid,
            "requirement_uid",
        )
        now_ms = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT run_id
                    FROM {agent_runs_table}
                    WHERE project_id = %s
                      AND requirement_uid = %s
                      AND status IN (
                        'queued', 'running', 'cancelling',
                        'awaiting_script_action'
                      )
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (project_id, requirement_uid),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "该需求存在运行中或等待处理的 Agent 任务，"
                        "请先完成或取消任务后再删除。"
                    )
                cursor.execute(
                    f"""
                    UPDATE {requirements_table}
                    SET status = 'deleted', updated_at = %s
                    WHERE project_id = %s
                      AND requirement_uid = %s
                      AND status != 'deleted'
                    """,
                    (now_ms, project_id, requirement_uid),
                )
                affected = cursor.rowcount
                cursor.execute(
                    f"""
                    UPDATE {modules_table} m
                    JOIN {requirements_table} r
                      ON r.id = m.requirement_id
                    SET m.status = 'deleted', m.updated_at = %s
                    WHERE m.project_id = %s
                      AND r.requirement_uid = %s
                    """,
                    (now_ms, project_id, requirement_uid),
                )
                connection.commit()
        return affected > 0

    def list_modules(
        self,
        requirement_id,
        include_superseded=False,
    ):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirement_modules_table(
            config
        )
        project_id = self.dependencies.get_current_project_id()
        status_filter = (
            ""
            if include_superseded
            else "AND status != 'superseded'"
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {table}
                    WHERE project_id = %s
                      AND requirement_id = %s
                      AND status != 'deleted'
                      {status_filter}
                    ORDER BY id ASC
                    """,
                    (project_id, requirement_id),
                )
                return cursor.fetchall()

    def get_module(self, requirement_id, module_uid):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirement_modules_table(
            config
        )
        project_id = self.dependencies.get_current_project_id()
        module_uid = self.dependencies.validate_uid(
            module_uid,
            "module_uid",
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {table}
                    WHERE project_id = %s
                      AND requirement_id = %s
                      AND module_uid = %s
                      AND status != 'deleted'
                    LIMIT 1
                    """,
                    (
                        project_id,
                        requirement_id,
                        module_uid,
                    ),
                )
                return cursor.fetchone()

    def update_module(
        self,
        requirement_id,
        module_uid,
        normalized,
        status,
    ):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirement_modules_table(
            config
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        module_uid = self.dependencies.validate_uid(
            module_uid,
            "module_uid",
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET module_name = %s,
                        plan_name = %s,
                        status = %s,
                        confidence = %s,
                        business_goal = %s,
                        requirement_refs_json = %s,
                        test_points_json = %s,
                        matched_inventory_json = %s,
                        open_questions_json = %s,
                        baseline_required = %s,
                        write_risk = %s,
                        planner_prompt = %s,
                        updated_at = %s
                    WHERE project_id = %s
                      AND requirement_id = %s
                      AND module_uid = %s
                    """,
                    (
                        normalized["module_name"],
                        normalized["plan_name"],
                        status,
                        normalized["confidence"],
                        normalized["business_goal"],
                        self.dependencies.compact_json_dumps(
                            normalized["requirement_refs"]
                        ),
                        self.dependencies.compact_json_dumps(
                            normalized["test_points"]
                        ),
                        self.dependencies.compact_json_dumps(
                            normalized["matched_inventory"]
                        ),
                        self.dependencies.compact_json_dumps(
                            normalized["open_questions"]
                        ),
                        int(normalized["baseline_required"]),
                        int(normalized["write_risk"]),
                        normalized["planner_prompt"],
                        now_ms,
                        project_id,
                        requirement_id,
                        module_uid,
                    ),
                )
                connection.commit()
        return self.dependencies.get_requirement_module(
            requirement_id,
            module_uid,
        )

    def delete_module(self, requirement_id, module_uid):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_requirement_modules_table(
            config
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        module_uid = self.dependencies.validate_uid(
            module_uid,
            "module_uid",
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'deleted', updated_at = %s
                    WHERE project_id = %s
                      AND requirement_id = %s
                      AND module_uid = %s
                    """,
                    (
                        now_ms,
                        project_id,
                        requirement_id,
                        module_uid,
                    ),
                )
                affected = cursor.rowcount
                connection.commit()
        return affected > 0
