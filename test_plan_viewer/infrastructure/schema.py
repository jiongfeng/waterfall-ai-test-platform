"""Platform database schema bootstrap and migrations.

The module is deliberately independent from :mod:`app`.  Runtime collaborators
are supplied by the composition root so existing monkeypatch-based integrations
continue to resolve the current app-level helpers on every call.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class SchemaDependencies:
    get_platform_database_config: Callable[..., Any]
    quote_mysql_identifier: Callable[..., Any]
    platform_mysql_connection: Callable[..., Any]
    platform_table_sql: Callable[..., Any]
    ensure_mysql_column: Callable[..., Any]
    ensure_mysql_column_type: Callable[..., Any]
    ensure_mysql_index: Callable[..., Any]
    mysql_primary_key_columns: Callable[..., Any]
    mysql_table_exists: Callable[..., Any]
    mysql_table_has_columns: Callable[..., Any]
    get_agent_item_retry_flows_table: Callable[..., Any]
    get_agent_run_attempts_table: Callable[..., Any]
    get_agent_run_events_table: Callable[..., Any]
    get_agent_run_steps_table: Callable[..., Any]
    get_agent_runs_table: Callable[..., Any]
    get_page_inventory_table: Callable[..., Any]
    get_platform_projects_table: Callable[..., Any]
    get_requirement_module_plans_table: Callable[..., Any]
    get_requirement_modules_table: Callable[..., Any]
    get_requirements_table: Callable[..., Any]
    get_setup_bindings_table: Callable[..., Any]
    get_setup_runs_table: Callable[..., Any]
    get_setup_scripts_table: Callable[..., Any]
    get_test_suite_items_table: Callable[..., Any]
    get_test_suites_table: Callable[..., Any]
    get_default_project_id_from_cursor: Callable[..., Any]
    migrate_legacy_test_suites: Callable[..., Any]
    seed_auth_defaults: Callable[..., Any]
    seed_platform_projects: Callable[..., Any]
    process_started_at_ms: int


@dataclass
class SchemaState:
    lock: Any = field(default_factory=threading.Lock)
    ready: bool = False
    signature: tuple[Any, ...] | None = None


PLATFORM_DATABASE_SCHEMA_STATE = SchemaState()


def ensure_platform_database_schema(config=None, *, dependencies, state=None):
    """Create or migrate platform tables once for a database signature."""
    if state is None:
        state = PLATFORM_DATABASE_SCHEMA_STATE

    get_platform_database_config = dependencies.get_platform_database_config
    quote_mysql_identifier = dependencies.quote_mysql_identifier
    platform_mysql_connection = dependencies.platform_mysql_connection
    platform_table_sql = dependencies.platform_table_sql
    ensure_mysql_column = dependencies.ensure_mysql_column
    ensure_mysql_column_type = dependencies.ensure_mysql_column_type
    ensure_mysql_index = dependencies.ensure_mysql_index
    mysql_primary_key_columns = dependencies.mysql_primary_key_columns
    mysql_table_exists = dependencies.mysql_table_exists
    mysql_table_has_columns = dependencies.mysql_table_has_columns
    get_agent_item_retry_flows_table = dependencies.get_agent_item_retry_flows_table
    get_agent_run_attempts_table = dependencies.get_agent_run_attempts_table
    get_agent_run_events_table = dependencies.get_agent_run_events_table
    get_agent_run_steps_table = dependencies.get_agent_run_steps_table
    get_agent_runs_table = dependencies.get_agent_runs_table
    get_page_inventory_table = dependencies.get_page_inventory_table
    get_platform_projects_table = dependencies.get_platform_projects_table
    get_requirement_module_plans_table = dependencies.get_requirement_module_plans_table
    get_requirement_modules_table = dependencies.get_requirement_modules_table
    get_requirements_table = dependencies.get_requirements_table
    get_setup_bindings_table = dependencies.get_setup_bindings_table
    get_setup_runs_table = dependencies.get_setup_runs_table
    get_setup_scripts_table = dependencies.get_setup_scripts_table
    get_test_suite_items_table = dependencies.get_test_suite_items_table
    get_test_suites_table = dependencies.get_test_suites_table
    get_default_project_id_from_cursor = dependencies.get_default_project_id_from_cursor
    migrate_legacy_test_suites = dependencies.migrate_legacy_test_suites
    seed_auth_defaults = dependencies.seed_auth_defaults
    seed_platform_projects = dependencies.seed_platform_projects
    PROCESS_STARTED_AT_MS = dependencies.process_started_at_ms

    config = config or get_platform_database_config()
    if not config.get("enabled"):
        raise RuntimeError("未启用平台 MySQL 持久化，请在 config.json 配置 platform_database。")

    signature = (
        config["host"],
        config["port"],
        config["user"],
        config["database"],
        config["table_prefix"],
    )
    with state.lock:
        if state.ready and state.signature == signature:
            return

        database_sql = quote_mysql_identifier(config["database"])
        if config.get("create_database", True):
            with platform_mysql_connection(config, use_database=False) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"CREATE DATABASE IF NOT EXISTS {database_sql} "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
                connection.commit()

        projects_table = get_platform_projects_table(config)
        records_table = platform_table_sql(config, "platform_records")
        jobs_table = platform_table_sql(config, "platform_jobs")
        assets_table = platform_table_sql(config, "test_assets")
        revisions_table = platform_table_sql(config, "test_asset_revisions")
        test_jobs_table = platform_table_sql(config, "test_jobs")
        job_artifacts_table = platform_table_sql(config, "job_artifacts")
        runs_table = platform_table_sql(config, "test_runs")
        run_results_table = platform_table_sql(config, "test_run_results")
        run_artifacts_table = platform_table_sql(config, "test_run_artifacts")
        setup_scripts_table = get_setup_scripts_table(config)
        setup_bindings_table = get_setup_bindings_table(config)
        setup_runs_table = get_setup_runs_table(config)
        suites_table = get_test_suites_table(config)
        suite_items_table = get_test_suite_items_table(config)
        requirements_table = get_requirements_table(config)
        requirement_modules_table = get_requirement_modules_table(config)
        requirement_module_plans_table = get_requirement_module_plans_table(config)
        page_inventory_table = get_page_inventory_table(config)
        agent_runs_table = get_agent_runs_table(config)
        agent_run_steps_table = get_agent_run_steps_table(config)
        agent_run_events_table = get_agent_run_events_table(config)
        agent_run_attempts_table = get_agent_run_attempts_table(config)
        agent_item_retry_flows_table = get_agent_item_retry_flows_table(config)
        users_table = platform_table_sql(config, "platform_users")
        roles_table = platform_table_sql(config, "platform_roles")
        permissions_table = platform_table_sql(config, "platform_permissions")
        user_roles_table = platform_table_sql(config, "platform_user_roles")
        role_permissions_table = platform_table_sql(config, "platform_role_permissions")
        with platform_mysql_connection(config, use_database=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {projects_table} (
                      project_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_key VARCHAR(64) NOT NULL,
                      name VARCHAR(128) NOT NULL,
                      description VARCHAR(512) NOT NULL DEFAULT '',
                      playwright_project_root TEXT NOT NULL,
                      specs_dir VARCHAR(255) NOT NULL DEFAULT 'specs',
                      tests_dir VARCHAR(255) NOT NULL DEFAULT 'tests',
                      opencode_config_json LONGTEXT NULL,
                      target_system_json LONGTEXT NULL,
                      database_baseline_json LONGTEXT NULL,
                      plan_generation_json LONGTEXT NULL,
                      language_code VARCHAR(16) NOT NULL DEFAULT 'en',
                      status VARCHAR(32) NOT NULL DEFAULT 'active',
                      is_default TINYINT(1) NOT NULL DEFAULT 0,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (project_id),
                      UNIQUE KEY uk_project_key (project_key),
                      INDEX idx_status_default (status, is_default)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                ensure_mysql_column(cursor, config, "platform_projects", "target_system_json", "target_system_json LONGTEXT NULL")
                ensure_mysql_column(cursor, config, "platform_projects", "plan_generation_json", "plan_generation_json LONGTEXT NULL")
                ensure_mysql_column(cursor, config, "platform_projects", "language_code", "language_code VARCHAR(16) NOT NULL DEFAULT 'en'")
                cursor.execute(
                    f"ALTER TABLE {projects_table} MODIFY language_code VARCHAR(16) NOT NULL DEFAULT 'en'"
                )
                cursor.execute(
                    f"UPDATE {projects_table} SET language_code = 'en' "
                    "WHERE language_code IS NULL OR language_code = ''"
                )
                seed_platform_projects(cursor, config)
                default_project_id = get_default_project_id_from_cursor(cursor, config)
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {records_table} (
                      project_id BIGINT NOT NULL,
                      bucket VARCHAR(64) NOT NULL,
                      record_key VARCHAR(512) NOT NULL,
                      record_json LONGTEXT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (project_id, bucket, record_key),
                      INDEX idx_bucket_updated_at (bucket, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {jobs_table} (
                      job_id VARCHAR(64) NOT NULL,
                      project_id BIGINT NOT NULL,
                      job_type VARCHAR(64) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      module_name VARCHAR(255) NOT NULL,
                      plan_filename VARCHAR(255) NOT NULL,
                      target_path TEXT NOT NULL,
                      logs LONGTEXT NOT NULL,
                      error TEXT NULL,
                      payload_json LONGTEXT NULL,
                      created_at DOUBLE NOT NULL,
                      updated_at DOUBLE NOT NULL,
                      PRIMARY KEY (job_id),
                      INDEX idx_job_type_updated_at (job_type, updated_at),
                      INDEX idx_status_updated_at (status, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {assets_table} (
                      asset_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      asset_type VARCHAR(16) NOT NULL,
                      module_name VARCHAR(255) NOT NULL,
                      title VARCHAR(255) NOT NULL,
                      current_path TEXT NOT NULL,
                      current_revision_id BIGINT NULL,
                      from_plan_asset_id BIGINT NULL,
                      source_job_id VARCHAR(128) NULL,
                      status VARCHAR(32) NOT NULL DEFAULT 'active',
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      deleted_at BIGINT NULL,
                      PRIMARY KEY (asset_id),
                      INDEX idx_project_asset_type_module (project_id, asset_type, module_name),
                      INDEX idx_from_plan_asset_id (from_plan_asset_id),
                      INDEX idx_current_revision_id (current_revision_id),
                      INDEX idx_source_job_id (source_job_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {revisions_table} (
                      revision_id BIGINT NOT NULL AUTO_INCREMENT,
                      asset_id BIGINT NOT NULL,
                      version_no INT NOT NULL,
                      file_path TEXT NOT NULL,
                      git_commit_sha VARCHAR(64) NOT NULL,
                      content_sha256 VARCHAR(64) NOT NULL,
                      change_source VARCHAR(32) NOT NULL,
                      source_job_id VARCHAR(64) NULL,
                      author VARCHAR(255) NULL,
                      message TEXT NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (revision_id),
                      UNIQUE KEY uniq_asset_version (asset_id, version_no),
                      INDEX idx_asset_created (asset_id, created_at),
                      INDEX idx_git_commit_sha (git_commit_sha),
                      INDEX idx_source_job_id (source_job_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {test_jobs_table} (
                      job_id VARCHAR(64) NOT NULL,
                      project_id BIGINT NOT NULL,
                      job_type VARCHAR(32) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      target_asset_id BIGINT NULL,
                      source_asset_id BIGINT NULL,
                      prompt LONGTEXT NULL,
                      coverage_profile VARCHAR(32) NOT NULL DEFAULT 'core',
                      prompt_customized TINYINT(1) NOT NULL DEFAULT 0,
                      prompt_context_json LONGTEXT NULL,
                      cancel_requested TINYINT(1) NOT NULL DEFAULT 0,
                      opencode_session_id VARCHAR(128) NULL,
                      log_path TEXT NULL,
                      log_tail LONGTEXT NULL,
                      log_size BIGINT NOT NULL DEFAULT 0,
                      error TEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (job_id),
                      INDEX idx_job_type_updated (job_type, updated_at),
                      INDEX idx_status_updated (status, updated_at),
                      INDEX idx_target_asset_id (target_asset_id),
                      INDEX idx_source_asset_id (source_asset_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {job_artifacts_table} (
                      artifact_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      job_id VARCHAR(64) NOT NULL,
                      artifact_type VARCHAR(32) NOT NULL,
                      path TEXT NOT NULL,
                      relative_path TEXT NULL,
                      url TEXT NULL,
                      size BIGINT NULL,
                      sha256 VARCHAR(64) NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (artifact_id),
                      INDEX idx_job_id (job_id),
                      INDEX idx_artifact_type (artifact_type)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {runs_table} (
                      run_id VARCHAR(64) NOT NULL,
                      project_id BIGINT NOT NULL,
                      run_type VARCHAR(32) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      execution_mode VARCHAR(32) NOT NULL,
                      database_reset_mode VARCHAR(32) NOT NULL,
                      triggered_by VARCHAR(255) NULL,
                      trigger_source VARCHAR(32) NOT NULL DEFAULT 'platform',
                      suite_id VARCHAR(64) NULL,
                      module_name VARCHAR(255) NULL,
                      target_asset_id BIGINT NULL,
                      command TEXT NULL,
                      git_commit_sha VARCHAR(64) NULL,
                      env_json LONGTEXT NULL,
                      summary_json LONGTEXT NULL,
                      total_files INT NOT NULL DEFAULT 0,
                      completed_files INT NOT NULL DEFAULT 0,
                      error TEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (run_id),
                      INDEX idx_project_run_type_updated (project_id, run_type, updated_at),
                      INDEX idx_status_updated (status, updated_at),
                      INDEX idx_suite_id (suite_id),
                      INDEX idx_module_name (module_name),
                      INDEX idx_target_asset_id (target_asset_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {run_results_table} (
                      result_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      order_index INT NOT NULL DEFAULT 0,
                      script_asset_id BIGINT NOT NULL,
                      script_revision_id BIGINT NULL,
                      plan_asset_id BIGINT NULL,
                      plan_revision_id BIGINT NULL,
                      module_name VARCHAR(255) NOT NULL,
                      script_path TEXT NOT NULL,
                      script_title VARCHAR(255) NOT NULL,
                      command TEXT NULL,
                      playwright_project VARCHAR(255) NULL,
                      browser_name VARCHAR(64) NULL,
                      status VARCHAR(32) NOT NULL,
                      duration_ms BIGINT NULL,
                      retry_count INT NOT NULL DEFAULT 0,
                      database_reset_status VARCHAR(32) NULL,
                      database_reset_started_at BIGINT NULL,
                      database_reset_finished_at BIGINT NULL,
                      database_reset_error TEXT NULL,
                      error_message TEXT NULL,
                      error_stack LONGTEXT NULL,
                      stdout_tail LONGTEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (result_id),
                      INDEX idx_project_run_order (project_id, run_id, order_index),
                      INDEX idx_script_asset_finished (script_asset_id, finished_at),
                      INDEX idx_plan_asset_finished (plan_asset_id, finished_at),
                      INDEX idx_status (status),
                      INDEX idx_script_revision_id (script_revision_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {run_artifacts_table} (
                      artifact_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      result_id BIGINT NULL,
                      artifact_type VARCHAR(32) NOT NULL,
                      path TEXT NOT NULL,
                      relative_path TEXT NULL,
                      url TEXT NULL,
                      size BIGINT NULL,
                      sha256 VARCHAR(64) NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (artifact_id),
                      INDEX idx_run_id (run_id),
                      INDEX idx_result_id (result_id),
                      INDEX idx_artifact_type (artifact_type)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                setup_schema_columns = {
                    "setup_scripts": {
                        "script_id", "project_id", "script_uid", "name", "script_content",
                        "working_directory", "environment_json", "timeout_seconds",
                        "concurrency_key", "enabled", "created_at", "updated_at",
                    },
                    "setup_bindings": {
                        "binding_id", "project_id", "binding_uid", "script_id", "scope_type",
                        "scope_key", "priority", "enabled", "created_at", "updated_at",
                    },
                    "setup_runs": {
                        "setup_run_id", "project_id", "run_uid", "parent_run_id", "binding_id",
                        "script_id", "script_uid", "script_name", "target_type", "target_key",
                        "status", "exit_code", "output_summary", "script_snapshot_json",
                        "started_at", "finished_at", "created_at", "updated_at",
                    },
                }
                setup_schema_presence = {
                    table_name: mysql_table_exists(cursor, config, table_name)
                    for table_name in setup_schema_columns
                }
                rebuild_setup_schema = any(setup_schema_presence.values()) and any(
                    not setup_schema_presence[table_name]
                    or not mysql_table_has_columns(cursor, config, table_name, required_columns)
                    for table_name, required_columns in setup_schema_columns.items()
                )
                if rebuild_setup_schema:
                    # 不迁移旧准备数据；只重建互相依赖的三张准备表，不触碰其他业务表。
                    for setup_table_name in ("setup_runs", "setup_bindings", "setup_scripts"):
                        cursor.execute(f"DROP TABLE IF EXISTS {platform_table_sql(config, setup_table_name)}")
                for obsolete_table in ("setup_step_runs", "setup_profile_steps", "setup_profiles", "setup_actions"):
                    cursor.execute(f"DROP TABLE IF EXISTS {platform_table_sql(config, obsolete_table)}")

                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {setup_scripts_table} (
                      script_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      script_uid VARCHAR(64) NOT NULL,
                      name VARCHAR(255) NOT NULL,
                      description VARCHAR(1024) NOT NULL DEFAULT '',
                      script_content LONGTEXT NOT NULL,
                      working_directory TEXT NULL,
                      environment_json LONGTEXT NULL,
                      timeout_seconds INT NOT NULL DEFAULT 300,
                      concurrency_key VARCHAR(255) NULL,
                      enabled TINYINT(1) NOT NULL DEFAULT 1,
                      created_by VARCHAR(255) NULL,
                      updated_by VARCHAR(255) NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (script_id),
                      UNIQUE KEY uk_project_script_uid (project_id, script_uid),
                      INDEX idx_project_script_enabled (project_id, enabled, updated_at),
                      INDEX idx_project_script_name (project_id, name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {setup_bindings_table} (
                      binding_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      binding_uid VARCHAR(64) NOT NULL,
                      script_id BIGINT NOT NULL,
                      scope_type VARCHAR(16) NOT NULL,
                      scope_key VARCHAR(512) NOT NULL,
                      scope_label VARCHAR(255) NOT NULL DEFAULT '',
                      priority INT NOT NULL DEFAULT 0,
                      enabled TINYINT(1) NOT NULL DEFAULT 1,
                      created_by VARCHAR(255) NULL,
                      updated_by VARCHAR(255) NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (binding_id),
                      UNIQUE KEY uk_project_binding_uid (project_id, binding_uid),
                      INDEX idx_project_scope (project_id, scope_type, scope_key(191), enabled, priority),
                      INDEX idx_project_script (project_id, script_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {setup_runs_table} (
                      setup_run_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_uid VARCHAR(64) NOT NULL,
                      parent_run_id VARCHAR(64) NULL,
                      binding_id BIGINT NULL,
                      script_id BIGINT NULL,
                      script_uid VARCHAR(64) NOT NULL,
                      script_name VARCHAR(255) NOT NULL DEFAULT '',
                      target_type VARCHAR(16) NOT NULL,
                      target_key VARCHAR(512) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      exit_code INT NULL,
                      duration_ms BIGINT NULL,
                      output_summary LONGTEXT NULL,
                      error TEXT NULL,
                      script_snapshot_json LONGTEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (setup_run_id),
                      UNIQUE KEY uk_project_run_uid (project_id, run_uid),
                      INDEX idx_project_parent_run (project_id, parent_run_id),
                      INDEX idx_project_setup_updated (project_id, updated_at),
                      INDEX idx_project_script_run (project_id, script_id, updated_at),
                      INDEX idx_project_script_uid_run (project_id, script_uid, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {suites_table} (
                      suite_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      suite_uid VARCHAR(64) NOT NULL,
                      name VARCHAR(255) NOT NULL,
                      description VARCHAR(1024) NOT NULL DEFAULT '',
                      status VARCHAR(32) NOT NULL DEFAULT 'active',
                      created_by VARCHAR(255) NULL,
                      updated_by VARCHAR(255) NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      deleted_at BIGINT NULL,
                      PRIMARY KEY (suite_id),
                      UNIQUE KEY uk_project_suite_uid (project_id, suite_uid),
                      INDEX idx_project_status_updated (project_id, status, updated_at),
                      INDEX idx_project_name (project_id, name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {suite_items_table} (
                      item_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      suite_id BIGINT NOT NULL,
                      script_asset_id BIGINT NULL,
                      module_name VARCHAR(255) NOT NULL,
                      filename VARCHAR(255) NOT NULL,
                      display_name VARCHAR(255) NOT NULL,
                      script_path TEXT NULL,
                      sort_order INT NOT NULL DEFAULT 0,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (item_id),
                      UNIQUE KEY uk_suite_script (suite_id, module_name, filename),
                      INDEX idx_project_suite_order (project_id, suite_id, sort_order),
                      INDEX idx_script_asset_id (script_asset_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {requirements_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      requirement_uid VARCHAR(64) NOT NULL,
                      title VARCHAR(255) NOT NULL,
                      filename VARCHAR(255) NOT NULL,
                      file_path TEXT NOT NULL,
                      content_sha256 CHAR(64) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      source_type VARCHAR(32) NOT NULL,
                      created_by VARCHAR(255) NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_requirement_uid (project_id, requirement_uid),
                      INDEX idx_project_status_updated (project_id, status, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {requirement_modules_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      requirement_id BIGINT NOT NULL,
                      module_uid VARCHAR(64) NOT NULL,
                      module_name VARCHAR(255) NOT NULL,
                      plan_name VARCHAR(255) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      confidence DECIMAL(5, 4) NULL,
                      business_goal TEXT NULL,
                      requirement_refs_json LONGTEXT NULL,
                      test_points_json LONGTEXT NULL,
                      matched_inventory_json LONGTEXT NULL,
                      open_questions_json LONGTEXT NULL,
                      baseline_required TINYINT(1) NOT NULL DEFAULT 0,
                      write_risk TINYINT(1) NOT NULL DEFAULT 0,
                      planner_prompt LONGTEXT NULL,
                      source_job_id VARCHAR(128) NULL,
                      generated_plan_asset_id BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_requirement_module_uid (project_id, module_uid),
                      INDEX idx_requirement_status (project_id, requirement_id, status),
                      INDEX idx_generated_plan_asset_id (generated_plan_asset_id),
                      INDEX idx_source_job_id (source_job_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {requirement_module_plans_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      requirement_id BIGINT NOT NULL,
                      requirement_module_id BIGINT NOT NULL,
                      plan_asset_id BIGINT NOT NULL,
                      source_job_id VARCHAR(128) NULL,
                      coverage_profile VARCHAR(32) NOT NULL DEFAULT 'core',
                      prompt_customized TINYINT(1) NOT NULL DEFAULT 0,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_requirement_module_plan (project_id, requirement_module_id, plan_asset_id),
                      INDEX idx_requirement_module_created (project_id, requirement_module_id, created_at),
                      INDEX idx_plan_asset_id (plan_asset_id),
                      INDEX idx_source_job_id (source_job_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {requirement_module_plans_table}
                      (project_id, requirement_id, requirement_module_id, plan_asset_id, source_job_id,
                       coverage_profile, prompt_customized, created_at)
                    SELECT project_id, requirement_id, id, generated_plan_asset_id, source_job_id,
                           'core', 0, updated_at
                    FROM {requirement_modules_table}
                    WHERE generated_plan_asset_id IS NOT NULL
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {page_inventory_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      inventory_uid VARCHAR(64) NOT NULL,
                      page_name VARCHAR(255) NOT NULL,
                      url VARCHAR(512) NULL,
                      menu_path_json LONGTEXT NULL,
                      roles_json LONGTEXT NULL,
                      accounts_json LONGTEXT NULL,
                      stable_selectors_json LONGTEXT NULL,
                      actions_json LONGTEXT NULL,
                      read_only_actions_json LONGTEXT NULL,
                      write_actions_json LONGTEXT NULL,
                      sample_data_json LONGTEXT NULL,
                      write_risk TINYINT(1) NOT NULL DEFAULT 0,
                      baseline_required TINYINT(1) NOT NULL DEFAULT 0,
                      notes TEXT NULL,
                      source VARCHAR(32) NOT NULL,
                      confidence DECIMAL(5, 4) NULL,
                      snapshot_hash CHAR(64) NULL,
                      last_scanned_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_page_inventory_uid (project_id, inventory_uid),
                      INDEX idx_project_page (project_id, page_name),
                      INDEX idx_project_source_updated (project_id, source, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {agent_runs_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      requirement_id BIGINT NULL,
                      requirement_uid VARCHAR(64) NULL,
                      requirement_title VARCHAR(255) NOT NULL DEFAULT '',
                      status VARCHAR(32) NOT NULL,
                      current_step VARCHAR(64) NOT NULL DEFAULT '',
                      suite_uid VARCHAR(64) NULL,
                      summary_json LONGTEXT NULL,
                      plan_generation_json LONGTEXT NULL,
                      error TEXT NULL,
                      created_by VARCHAR(255) NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_project_run_id (project_id, run_id),
                      INDEX idx_project_status_updated (project_id, status, updated_at),
                      INDEX idx_requirement_uid (project_id, requirement_uid)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {agent_run_steps_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      step_key VARCHAR(64) NOT NULL,
                      step_name VARCHAR(128) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      input_json LONGTEXT NULL,
                      output_json LONGTEXT NULL,
                      counts_json LONGTEXT NULL,
                      error TEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_project_run_step (project_id, run_id, step_key),
                      INDEX idx_project_run_updated (project_id, run_id, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {agent_run_events_table} (
                      event_id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      step_key VARCHAR(64) NULL,
                      event_type VARCHAR(32) NOT NULL,
                      message TEXT NULL,
                      payload_json LONGTEXT NULL,
                      job_id VARCHAR(64) NULL,
                      asset_id BIGINT NULL,
                      test_run_id VARCHAR(64) NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (event_id),
                      INDEX idx_project_run_event (project_id, run_id, event_id),
                      INDEX idx_project_run_created (project_id, run_id, created_at),
                      INDEX idx_job_id (job_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {agent_run_attempts_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      attempt_id VARCHAR(64) NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      step_key VARCHAR(64) NOT NULL,
                      attempt_no INT NOT NULL DEFAULT 1,
                      previous_attempt_id VARCHAR(64) NULL,
                      retry_flow_id VARCHAR(64) NULL,
                      parent_attempt_id VARCHAR(64) NULL,
                      item_type VARCHAR(32) NOT NULL,
                      item_key VARCHAR(512) NOT NULL,
                      module_uid VARCHAR(64) NULL,
                      module_name VARCHAR(255) NULL,
                      plan_filename VARCHAR(255) NULL,
                      filename VARCHAR(255) NULL,
                      status VARCHAR(32) NOT NULL,
                      outcome_type VARCHAR(32) NULL,
                      verification_status VARCHAR(32) NULL,
                      job_id VARCHAR(64) NULL,
                      test_run_id VARCHAR(64) NULL,
                      result_id BIGINT NULL,
                      asset_id BIGINT NULL,
                      revision_id BIGINT NULL,
                      source_asset_id BIGINT NULL,
                      error_type VARCHAR(32) NULL,
                      error_message TEXT NULL,
                      error_stack LONGTEXT NULL,
                      input_snapshot_json LONGTEXT NULL,
                      output_summary_json LONGTEXT NULL,
                      artifact_refs_json LONGTEXT NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_project_attempt_id (project_id, attempt_id),
                      INDEX idx_project_run_step (project_id, run_id, step_key),
                      INDEX idx_project_run_item (project_id, run_id, item_key(191)),
                      INDEX idx_retry_flow_id (project_id, retry_flow_id),
                      INDEX idx_job_id (job_id),
                      INDEX idx_test_run_id (test_run_id),
                      INDEX idx_result_id (result_id),
                      INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                ensure_mysql_column(
                    cursor,
                    config,
                    "agent_run_attempts",
                    "retry_flow_id",
                    "retry_flow_id VARCHAR(64) NULL AFTER previous_attempt_id",
                )
                ensure_mysql_column(
                    cursor,
                    config,
                    "agent_run_attempts",
                    "parent_attempt_id",
                    "parent_attempt_id VARCHAR(64) NULL AFTER retry_flow_id",
                )
                ensure_mysql_index(
                    cursor,
                    config,
                    "agent_run_attempts",
                    "idx_retry_flow_id",
                    "INDEX idx_retry_flow_id (project_id, retry_flow_id)",
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {agent_item_retry_flows_table} (
                      id BIGINT NOT NULL AUTO_INCREMENT,
                      project_id BIGINT NOT NULL,
                      retry_flow_id VARCHAR(64) NOT NULL,
                      run_id VARCHAR(64) NOT NULL,
                      root_attempt_id VARCHAR(64) NOT NULL,
                      item_type VARCHAR(32) NOT NULL DEFAULT 'script',
                      item_key VARCHAR(512) NOT NULL,
                      active_item_key VARCHAR(512) NULL,
                      module_name VARCHAR(255) NULL,
                      plan_filename VARCHAR(255) NULL,
                      filename VARCHAR(255) NULL,
                      status VARCHAR(32) NOT NULL,
                      current_phase VARCHAR(32) NOT NULL,
                      progress_message TEXT NULL,
                      auto_repair TINYINT(1) NOT NULL DEFAULT 1,
                      generation_attempt_id VARCHAR(64) NULL,
                      execution_attempt_id VARCHAR(64) NULL,
                      repair_attempt_id VARCHAR(64) NULL,
                      verification_attempt_id VARCHAR(64) NULL,
                      result_json LONGTEXT NULL,
                      error TEXT NULL,
                      cancel_requested TINYINT(1) NOT NULL DEFAULT 0,
                      created_by VARCHAR(255) NULL,
                      started_at BIGINT NULL,
                      finished_at BIGINT NULL,
                      acknowledged_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_project_retry_flow_id (project_id, retry_flow_id),
                      UNIQUE KEY uk_project_run_active_item (project_id, run_id, active_item_key),
                      INDEX idx_project_run_status (project_id, run_id, status, updated_at),
                      INDEX idx_project_status_updated (project_id, status, updated_at),
                      INDEX idx_root_attempt_id (project_id, root_attempt_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                # Retry workers are in-process daemon threads. Any active row
                # created before this process boot cannot still have a live
                # owner, so release it instead of permanently blocking the
                # project after a restart.
                cursor.execute(
                    f"""
                    UPDATE {agent_item_retry_flows_table}
                    SET progress_message = CASE
                          WHEN status = 'cancelling' THEN '服务重启，本次重试已取消。'
                          ELSE '服务重启中断了本次重试，请重新发起。'
                        END,
                        error = CASE
                          WHEN status = 'cancelling' THEN '服务重启前取消尚未完成。'
                          ELSE '单项重试因服务重启而中断。'
                        END,
                        status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'failed' END,
                        active_item_key = NULL,
                        finished_at = COALESCE(finished_at, %s),
                        updated_at = %s
                    WHERE status IN ('queued', 'running', 'finalizing', 'cancelling')
                      AND created_at < %s
                    """,
                    (PROCESS_STARTED_AT_MS, PROCESS_STARTED_AT_MS, PROCESS_STARTED_AT_MS),
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {users_table} (
                      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                      username VARCHAR(64) NOT NULL,
                      password_hash VARCHAR(255) NOT NULL,
                      display_name VARCHAR(128) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      last_login_at BIGINT NULL,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_username (username),
                      INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {roles_table} (
                      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                      code VARCHAR(64) NOT NULL,
                      name VARCHAR(128) NOT NULL,
                      description VARCHAR(512) NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      is_system TINYINT(1) NOT NULL DEFAULT 0,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_code (code),
                      INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {permissions_table} (
                      code VARCHAR(64) NOT NULL,
                      name VARCHAR(128) NOT NULL,
                      permission_type VARCHAR(32) NOT NULL,
                      sort_order INT NOT NULL DEFAULT 0,
                      created_at BIGINT NOT NULL,
                      updated_at BIGINT NOT NULL,
                      PRIMARY KEY (code),
                      INDEX idx_type_sort (permission_type, sort_order)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {user_roles_table} (
                      user_id BIGINT UNSIGNED NOT NULL,
                      role_id BIGINT UNSIGNED NOT NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (user_id, role_id),
                      INDEX idx_role_id (role_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {role_permissions_table} (
                      role_id BIGINT UNSIGNED NOT NULL,
                      permission_code VARCHAR(64) NOT NULL,
                      created_at BIGINT NOT NULL,
                      PRIMARY KEY (role_id, permission_code),
                      INDEX idx_permission_code (permission_code)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                project_column_sql = f"project_id BIGINT NOT NULL DEFAULT {default_project_id}"
                for table_name in (
                    "platform_records",
                    "platform_jobs",
                    "test_assets",
                    "test_jobs",
                    "job_artifacts",
                    "test_runs",
                    "test_run_results",
                    "test_run_artifacts",
                ):
                    ensure_mysql_column(cursor, config, table_name, "project_id", project_column_sql)

                if mysql_primary_key_columns(cursor, config, "platform_records") != [
                    "project_id",
                    "bucket",
                    "record_key",
                ]:
                    cursor.execute(
                        f"ALTER TABLE {records_table} DROP PRIMARY KEY, ADD PRIMARY KEY (project_id, bucket, record_key)"
                    )

                ensure_mysql_index(
                    cursor,
                    config,
                    "platform_records",
                    "idx_project_bucket_updated_at",
                    "INDEX idx_project_bucket_updated_at (project_id, bucket, updated_at)",
                )
                ensure_mysql_index(
                    cursor,
                    config,
                    "platform_jobs",
                    "idx_project_job_type_updated",
                    "INDEX idx_project_job_type_updated (project_id, job_type, updated_at)",
                )
                ensure_mysql_index(
                    cursor,
                    config,
                    "test_assets",
                    "idx_project_asset_type_module",
                    "INDEX idx_project_asset_type_module (project_id, asset_type, module_name)",
                )
                ensure_mysql_column_type(
                    cursor,
                    config,
                    "test_jobs",
                    "prompt",
                    "longtext",
                    "prompt LONGTEXT NULL",
                )
                ensure_mysql_column(cursor, config, "test_jobs", "coverage_profile", "coverage_profile VARCHAR(32) NOT NULL DEFAULT 'core'")
                ensure_mysql_column(cursor, config, "test_jobs", "prompt_customized", "prompt_customized TINYINT(1) NOT NULL DEFAULT 0")
                ensure_mysql_column(cursor, config, "test_jobs", "prompt_context_json", "prompt_context_json LONGTEXT NULL")
                ensure_mysql_column(cursor, config, "test_jobs", "cancel_requested", "cancel_requested TINYINT(1) NOT NULL DEFAULT 0")
                ensure_mysql_column(cursor, config, "test_jobs", "opencode_session_id", "opencode_session_id VARCHAR(128) NULL")
                ensure_mysql_column(cursor, config, "agent_runs", "plan_generation_json", "plan_generation_json LONGTEXT NULL")
                ensure_mysql_index(
                    cursor,
                    config,
                    "test_runs",
                    "idx_project_run_type_updated",
                    "INDEX idx_project_run_type_updated (project_id, run_type, updated_at)",
                )
                ensure_mysql_index(
                    cursor,
                    config,
                    "test_run_results",
                    "idx_project_run_order",
                    "INDEX idx_project_run_order (project_id, run_id, order_index)",
                )
                seed_auth_defaults(cursor, config)
                migrate_legacy_test_suites(cursor, config, default_project_id)
            connection.commit()

        state.ready = True
        state.signature = signature


__all__ = [
    "PLATFORM_DATABASE_SCHEMA_STATE",
    "SchemaDependencies",
    "SchemaState",
    "ensure_platform_database_schema",
]
