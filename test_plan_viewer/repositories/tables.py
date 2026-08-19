"""Canonical platform database table-name helpers."""

from test_plan_viewer.infrastructure.mysql import platform_table_sql


def get_platform_projects_table(config):
    return platform_table_sql(config, "platform_projects")


def get_test_suites_table(config):
    return platform_table_sql(config, "test_suites")


def get_test_suite_items_table(config):
    return platform_table_sql(config, "test_suite_items")


def get_requirements_table(config):
    return platform_table_sql(config, "requirements")


def get_requirement_modules_table(config):
    return platform_table_sql(config, "requirement_modules")


def get_requirement_module_plans_table(config):
    return platform_table_sql(config, "requirement_module_plans")


def get_page_inventory_table(config):
    return platform_table_sql(config, "page_inventory")


def get_agent_runs_table(config):
    return platform_table_sql(config, "agent_runs")


def get_agent_run_steps_table(config):
    return platform_table_sql(config, "agent_run_steps")


def get_agent_run_events_table(config):
    return platform_table_sql(config, "agent_run_events")


def get_agent_run_attempts_table(config):
    return platform_table_sql(config, "agent_run_attempts")


def get_agent_item_retry_flows_table(config):
    return platform_table_sql(config, "agent_item_retry_flows")


def get_script_preparation_runs_table(config):
    return platform_table_sql(config, "script_preparation_runs")


def get_test_assets_table(config):
    return platform_table_sql(config, "test_assets")


def get_test_asset_revisions_table(config):
    return platform_table_sql(config, "test_asset_revisions")


def get_test_jobs_table(config):
    return platform_table_sql(config, "test_jobs")


def get_job_artifacts_table(config):
    return platform_table_sql(config, "job_artifacts")


def get_test_runs_table(config):
    return platform_table_sql(config, "test_runs")


def get_test_run_results_table(config):
    return platform_table_sql(config, "test_run_results")


def get_test_run_artifacts_table(config):
    return platform_table_sql(config, "test_run_artifacts")


def get_setup_scripts_table(config):
    return platform_table_sql(config, "setup_scripts")


def get_setup_bindings_table(config):
    return platform_table_sql(config, "setup_bindings")


def get_setup_runs_table(config):
    return platform_table_sql(config, "setup_runs")
