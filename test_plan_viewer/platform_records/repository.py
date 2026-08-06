"""MySQL repository for browser records and legacy generation jobs."""

from dataclasses import dataclass
import json
import time
from typing import Callable


@dataclass(frozen=True)
class PlatformRecordRepositoryDependencies:
    """Infrastructure supplied by the application composition root."""

    get_database_config: Callable[[], dict]
    ensure_schema: Callable[[dict], None]
    table_sql: Callable[[dict, str], str]
    get_project_id: Callable[[], int]
    mysql_connection: Callable
    get_default_plan_filename: Callable[[str], str]
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000)


def validate_platform_record_bucket(bucket, allowed_buckets):
    if bucket not in allowed_buckets:
        raise ValueError("Unsupported platform record bucket.")
    return bucket


def validate_platform_record_key(record_key):
    record_key = str(record_key or "").strip()
    if not record_key or "\x00" in record_key:
        raise ValueError("Invalid platform record key.")
    if len(record_key) > 512:
        raise ValueError("Platform record key is too long.")
    return record_key


def record_updated_at_ms(record, now_ms=None):
    if isinstance(record, dict):
        try:
            value = int(record.get("updated_at") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    clock = now_ms or (lambda: int(time.time() * 1000))
    return int(clock())


def compact_json_dumps(value):
    return json.dumps(value if value is not None else None, ensure_ascii=False, separators=(",", ":"))


def load_json_column(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class PlatformRecordRepository:
    """Persist project-scoped browser records and compatibility jobs."""

    def __init__(self, dependencies, allowed_buckets):
        if not isinstance(dependencies, PlatformRecordRepositoryDependencies):
            raise TypeError("dependencies must be PlatformRecordRepositoryDependencies")
        self.dependencies = dependencies
        self.allowed_buckets = frozenset(allowed_buckets)

    def load_records(self):
        deps = self.dependencies
        config = deps.get_database_config()
        deps.ensure_schema(config)
        records_table = deps.table_sql(config, "platform_records")
        project_id = deps.get_project_id()
        buckets = {bucket: {} for bucket in self.allowed_buckets}

        with deps.mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT bucket, record_key, record_json
                    FROM {records_table}
                    WHERE project_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (project_id,),
                )
                rows = cursor.fetchall()

        for row in rows:
            bucket = row.get("bucket")
            if bucket not in buckets:
                continue
            try:
                value = json.loads(row.get("record_json") or "{}")
            except json.JSONDecodeError:
                continue
            buckets[bucket][row.get("record_key")] = value

        return buckets

    def save_record(self, bucket, record_key, record):
        bucket = validate_platform_record_bucket(bucket, self.allowed_buckets)
        record_key = validate_platform_record_key(record_key)
        if not isinstance(record, dict):
            raise ValueError("Platform record must be an object.")

        deps = self.dependencies
        config = deps.get_database_config()
        deps.ensure_schema(config)
        records_table = deps.table_sql(config, "platform_records")
        project_id = deps.get_project_id()
        record_json = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        updated_at = record_updated_at_ms(record, deps.now_ms)

        with deps.mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {records_table} (project_id, bucket, record_key, record_json, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      record_json = VALUES(record_json),
                      updated_at = VALUES(updated_at)
                    """,
                    (project_id, bucket, record_key, record_json, updated_at),
                )
            connection.commit()

    def save_job(self, job, job_type="plan_generation"):
        deps = self.dependencies
        config = deps.get_database_config()
        if not config.get("enabled"):
            return

        deps.ensure_schema(config)
        jobs_table = deps.table_sql(config, "platform_jobs")
        project_id = deps.get_project_id()
        payload = {
            key: value
            for key, value in job.items()
            if key
            not in {
                "id",
                "status",
                "module_name",
                "plan_filename",
                "target_path",
                "logs",
                "error",
                "created_at",
                "updated_at",
            }
        }
        with deps.mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {jobs_table}
                      (job_id, project_id, job_type, status, module_name, plan_filename, target_path, logs, error,
                       payload_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      project_id = VALUES(project_id),
                      job_type = VALUES(job_type),
                      status = VALUES(status),
                      module_name = VALUES(module_name),
                      plan_filename = VALUES(plan_filename),
                      target_path = VALUES(target_path),
                      logs = VALUES(logs),
                      error = VALUES(error),
                      payload_json = VALUES(payload_json),
                      updated_at = VALUES(updated_at)
                    """,
                    (
                        job["id"],
                        project_id,
                        job_type,
                        job["status"],
                        job["module_name"],
                        job.get("plan_filename") or deps.get_default_plan_filename(job["module_name"]),
                        job["target_path"],
                        json.dumps(job.get("logs") or [], ensure_ascii=False, separators=(",", ":")),
                        job.get("error"),
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None,
                        job["created_at"],
                        job["updated_at"],
                    ),
                )
            connection.commit()

    def load_job(self, job_id):
        deps = self.dependencies
        config = deps.get_database_config()
        if not config.get("enabled"):
            return None

        deps.ensure_schema(config)
        jobs_table = deps.table_sql(config, "platform_jobs")
        project_id = deps.get_project_id()
        with deps.mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {jobs_table} WHERE project_id = %s AND job_id = %s",
                    (project_id, job_id),
                )
                row = cursor.fetchone()

        if not row:
            return None

        logs = load_json_column(row.get("logs"), [])
        if not isinstance(logs, list):
            logs = []
        payload = load_json_column(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            payload = {}

        return {
            **payload,
            "id": row["job_id"],
            "status": row["status"],
            "module_name": row["module_name"],
            "plan_filename": row["plan_filename"],
            "target_path": row["target_path"],
            "logs": logs,
            "error": row.get("error"),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def require_database(self):
        config = self.dependencies.get_database_config()
        if not config.get("enabled"):
            raise RuntimeError("需求管理需要启用 platform_database。")
        self.dependencies.ensure_schema(config)
        return config
