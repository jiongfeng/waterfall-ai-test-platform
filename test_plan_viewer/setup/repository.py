import uuid
from dataclasses import dataclass, field

from test_plan_viewer.setup.validation import SETUP_BINDING_TARGET_TYPES


@dataclass(frozen=True)
class SetupRepositoryDependencies:
    get_platform_database_config: callable
    ensure_platform_database_schema: callable
    get_setup_tables: callable
    get_current_project_id: callable
    get_current_project: callable
    platform_mysql_connection: callable
    get_setup_scripts_table: callable
    get_setup_bindings_table: callable
    get_setup_runs_table: callable
    get_setup_script_row: callable
    list_setup_bindings: callable
    validate_setup_uid: callable
    normalize_setup_script_payload: callable
    normalize_setup_binding_payload: callable
    serialize_setup_script: callable
    serialize_setup_binding: callable
    serialize_setup_run: callable
    current_time_ms: callable
    current_platform_author: callable
    compact_json_dumps: callable
    redact_setup_snapshot: callable
    redact_setup_text: callable
    target_types: object = field(
        default_factory=lambda: set(SETUP_BINDING_TARGET_TYPES)
    )
    uid_factory: callable = lambda: uuid.uuid4().hex


def get_setup_tables(dependencies):
    config = dependencies.get_platform_database_config()
    if not config.get("enabled"):
        raise RuntimeError("准备脚本需要启用平台 MySQL 持久化。")
    dependencies.ensure_platform_database_schema(config)
    return config


def get_setup_script_row(
    cursor,
    config,
    project_id,
    script_uid,
    dependencies,
):
    cursor.execute(
        (
            f"SELECT * FROM {dependencies.get_setup_scripts_table(config)} "
            "WHERE project_id=%s AND script_uid=%s LIMIT 1"
        ),
        (project_id, script_uid),
    )
    return cursor.fetchone()


def list_setup_scripts(include_disabled, dependencies):
    config = dependencies.get_setup_tables()
    table = dependencies.get_setup_scripts_table(config)
    project_id = dependencies.get_current_project_id()
    where = (
        "project_id=%s"
        if include_disabled
        else "project_id=%s AND enabled=1"
    )
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"SELECT * FROM {table} WHERE {where} "
                    "ORDER BY updated_at DESC,script_id DESC"
                ),
                (project_id,),
            )
            return [
                dependencies.serialize_setup_script(row)
                for row in cursor.fetchall()
            ]


def get_setup_script(script_uid, dependencies):
    script_uid = dependencies.validate_setup_uid(
        script_uid,
        "script uid",
    )
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            row = dependencies.get_setup_script_row(
                cursor,
                config,
                project_id,
                script_uid,
            )
            return dependencies.serialize_setup_script(row)


def save_setup_script(payload, script_uid, dependencies):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    table = dependencies.get_setup_scripts_table(config)
    now_ms = dependencies.current_time_ms()
    author = dependencies.current_platform_author()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            existing_row = None
            if script_uid:
                script_uid = dependencies.validate_setup_uid(
                    script_uid,
                    "script uid",
                )
                existing_row = dependencies.get_setup_script_row(
                    cursor,
                    config,
                    project_id,
                    script_uid,
                )
                if not existing_row:
                    return None
            script = dependencies.normalize_setup_script_payload(
                payload,
                (
                    dependencies.serialize_setup_script(existing_row)
                    if existing_row
                    else None
                ),
            )
            if existing_row:
                script["uid"] = script_uid
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET name=%s,description=%s,script_content=%s,
                        working_directory=%s,environment_json=%s,
                        timeout_seconds=%s,concurrency_key=%s,enabled=%s,
                        updated_by=%s,updated_at=%s
                    WHERE project_id=%s AND script_uid=%s
                    """,
                    (
                        script["name"],
                        script["description"],
                        script["script_content"],
                        script["working_directory"],
                        dependencies.compact_json_dumps(
                            script["environment_overrides"]
                        ),
                        script["timeout_seconds"],
                        script["concurrency_key"],
                        int(script["enabled"]),
                        author,
                        now_ms,
                        project_id,
                        script_uid,
                    ),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {table}
                      (project_id,script_uid,name,description,script_content,
                       working_directory,environment_json,timeout_seconds,
                       concurrency_key,enabled,created_by,updated_by,
                       created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        project_id,
                        script["uid"],
                        script["name"],
                        script["description"],
                        script["script_content"],
                        script["working_directory"],
                        dependencies.compact_json_dumps(
                            script["environment_overrides"]
                        ),
                        script["timeout_seconds"],
                        script["concurrency_key"],
                        int(script["enabled"]),
                        author,
                        author,
                        now_ms,
                        now_ms,
                    ),
                )
            connection.commit()
            row = dependencies.get_setup_script_row(
                cursor,
                config,
                project_id,
                script["uid"],
            )
            return dependencies.serialize_setup_script(row)


def delete_setup_script(script_uid, dependencies):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    script_uid = dependencies.validate_setup_uid(
        script_uid,
        "script uid",
    )
    scripts = dependencies.get_setup_scripts_table(config)
    bindings = dependencies.get_setup_bindings_table(config)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            row = dependencies.get_setup_script_row(
                cursor,
                config,
                project_id,
                script_uid,
            )
            if not row:
                return False
            cursor.execute(
                (
                    f"DELETE FROM {bindings} "
                    "WHERE project_id=%s AND script_id=%s"
                ),
                (project_id, row["script_id"]),
            )
            cursor.execute(
                (
                    f"DELETE FROM {scripts} "
                    "WHERE project_id=%s AND script_id=%s"
                ),
                (project_id, row["script_id"]),
            )
        connection.commit()
    return True


def list_setup_bindings(include_disabled, dependencies):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    bindings = dependencies.get_setup_bindings_table(config)
    scripts = dependencies.get_setup_scripts_table(config)
    enabled_clause = (
        "" if include_disabled else " AND b.enabled=1 AND s.enabled=1"
    )
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    "SELECT b.*,s.script_uid,s.name AS script_name,"
                    f"s.enabled AS script_enabled FROM {bindings} b "
                    f"JOIN {scripts} s ON s.project_id=b.project_id "
                    "AND s.script_id=b.script_id "
                    f"WHERE b.project_id=%s{enabled_clause} "
                    "ORDER BY b.updated_at DESC,b.binding_id DESC"
                ),
                (project_id,),
            )
            return [
                dependencies.serialize_setup_binding(row)
                for row in cursor.fetchall()
            ]


def save_setup_binding(payload, binding_uid, dependencies):
    existing = None
    if binding_uid:
        existing = next(
            (
                item
                for item in dependencies.list_setup_bindings()
                if item["uid"] == binding_uid
            ),
            None,
        )
        if not existing:
            return None
    binding = dependencies.normalize_setup_binding_payload(
        payload,
        existing,
    )
    if binding_uid:
        binding["uid"] = dependencies.validate_setup_uid(
            binding_uid,
            "binding uid",
        )
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    bindings = dependencies.get_setup_bindings_table(config)
    scripts = dependencies.get_setup_scripts_table(config)
    now_ms = dependencies.current_time_ms()
    author = dependencies.current_platform_author()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"SELECT script_id FROM {scripts} "
                    "WHERE project_id=%s AND script_uid=%s"
                ),
                (project_id, binding["script_uid"]),
            )
            script = cursor.fetchone()
            if not script:
                raise ValueError(
                    f"准备脚本不存在：{binding['script_uid']}"
                )
            cursor.execute(
                (
                    f"SELECT binding_id FROM {bindings} "
                    "WHERE project_id=%s AND binding_uid=%s"
                ),
                (project_id, binding["uid"]),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    (
                        f"UPDATE {bindings} SET script_id=%s,scope_type=%s,"
                        "scope_key=%s,scope_label=%s,priority=%s,enabled=%s,"
                        "updated_by=%s,updated_at=%s "
                        "WHERE project_id=%s AND binding_id=%s"
                    ),
                    (
                        script["script_id"],
                        binding["scope_type"],
                        binding["scope_key"],
                        binding["scope_label"],
                        binding["priority"],
                        int(binding["enabled"]),
                        author,
                        now_ms,
                        project_id,
                        row["binding_id"],
                    ),
                )
            else:
                cursor.execute(
                    (
                        f"INSERT INTO {bindings} "
                        "(project_id,binding_uid,script_id,scope_type,"
                        "scope_key,scope_label,priority,enabled,created_by,"
                        "updated_by,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    ),
                    (
                        project_id,
                        binding["uid"],
                        script["script_id"],
                        binding["scope_type"],
                        binding["scope_key"],
                        binding["scope_label"],
                        binding["priority"],
                        int(binding["enabled"]),
                        author,
                        author,
                        now_ms,
                        now_ms,
                    ),
                )
        connection.commit()
    return next(
        item
        for item in dependencies.list_setup_bindings()
        if item["uid"] == binding["uid"]
    )


def delete_setup_binding(binding_uid, dependencies):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    binding_uid = dependencies.validate_setup_uid(
        binding_uid,
        "binding uid",
    )
    table = dependencies.get_setup_bindings_table(config)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"DELETE FROM {table} "
                    "WHERE project_id=%s AND binding_uid=%s"
                ),
                (project_id, binding_uid),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
    return deleted


def create_setup_run_record(
    parent_run_id,
    resolution,
    target_override,
    dependencies,
):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    runs = dependencies.get_setup_runs_table(config)
    run_uid = f"setup-{dependencies.uid_factory()}"
    script = resolution.get("script") or resolution.get("profile")
    if not script:
        raise ValueError("准备脚本解析结果无效。")
    binding = resolution.get("binding") or {}
    target = target_override or resolution.get("target") or {
        "scope_type": "project",
        "scope_key": (
            dependencies.get_current_project().get("project_key")
            or "default"
        ),
    }
    target_key = str(target.get("scope_key") or "").strip()
    if (
        target.get("scope_type") not in dependencies.target_types
        or not target_key
    ):
        raise ValueError("准备脚本执行目标无效。")
    if len(target_key) > 512:
        raise ValueError("准备脚本执行目标过长。")
    target = {**target, "scope_key": target_key}
    now_ms = dependencies.current_time_ms()
    snapshot = dependencies.redact_setup_snapshot(
        {
            "script": script,
            "binding": binding,
            "target": target,
        }
    )
    snapshot["script"]["script_content"] = dependencies.redact_setup_text(
        script.get("script_content") or "",
        script,
        limit=None,
    )
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            binding_id = None
            if binding.get("uid"):
                cursor.execute(
                    (
                        "SELECT binding_id FROM "
                        f"{dependencies.get_setup_bindings_table(config)} "
                        "WHERE project_id=%s AND binding_uid=%s"
                    ),
                    (project_id, binding["uid"]),
                )
                row = cursor.fetchone()
                binding_id = (
                    row.get("binding_id") if row else None
                )
            cursor.execute(
                (
                    "SELECT script_id FROM "
                    f"{dependencies.get_setup_scripts_table(config)} "
                    "WHERE project_id=%s AND script_uid=%s"
                ),
                (project_id, script["uid"]),
            )
            row = cursor.fetchone()
            script_id = row.get("script_id") if row else None
            cursor.execute(
                f"""
                INSERT INTO {runs}
                  (project_id,run_uid,parent_run_id,binding_id,script_id,
                   script_uid,script_name,target_type,target_key,status,
                   exit_code,duration_ms,output_summary,error,
                   script_snapshot_json,started_at,finished_at,created_at,
                   updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',NULL,NULL,
                        NULL,NULL,%s,%s,NULL,%s,%s)
                """,
                (
                    project_id,
                    run_uid,
                    parent_run_id or None,
                    binding_id,
                    script_id,
                    script["uid"],
                    script["name"],
                    target["scope_type"],
                    str(target["scope_key"]),
                    dependencies.compact_json_dumps(snapshot),
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            setup_run_id = cursor.lastrowid
        connection.commit()
    return {
        "setup_run_id": setup_run_id,
        "uid": run_uid,
        "started_at": now_ms,
        "target": target,
        "script": script,
    }


def finish_setup_run_record(setup_run, execution_result, dependencies):
    config = dependencies.get_setup_tables()
    now_ms = dependencies.current_time_ms()
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {dependencies.get_setup_runs_table(config)}
                SET status=%s,exit_code=%s,duration_ms=%s,
                    output_summary=%s,error=%s,finished_at=%s,updated_at=%s
                WHERE project_id=%s AND setup_run_id=%s
                """,
                (
                    execution_result["status"],
                    execution_result.get("exit_code"),
                    execution_result.get("duration_ms"),
                    execution_result.get("output_summary") or "",
                    execution_result.get("error") or None,
                    now_ms,
                    now_ms,
                    dependencies.get_current_project_id(),
                    setup_run["setup_run_id"],
                ),
            )
        connection.commit()
    return now_ms


def list_setup_runs(limit, script_uid, dependencies):
    config = dependencies.get_setup_tables()
    project_id = dependencies.get_current_project_id()
    try:
        limit = min(max(int(limit), 1), 100)
    except (TypeError, ValueError):
        limit = 50
    params = [project_id]
    where = "project_id=%s"
    if script_uid:
        script_uid = dependencies.validate_setup_uid(
            script_uid,
            "script uid",
        )
        where += " AND script_uid=%s"
        params.append(script_uid)
    params.append(limit)
    with dependencies.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    f"SELECT * FROM {dependencies.get_setup_runs_table(config)} "
                    f"WHERE {where} "
                    "ORDER BY started_at DESC,setup_run_id DESC LIMIT %s"
                ),
                tuple(params),
            )
            rows = cursor.fetchall()
    return [
        dependencies.serialize_setup_run(row)
        for row in rows
    ]
