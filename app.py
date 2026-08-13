import base64
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from flask import Response, g, has_request_context, jsonify, render_template, request, send_file, session, stream_with_context
from test_plan_viewer.agent import diagnostics as agent_diagnostics
from test_plan_viewer.agent import failure_handling as agent_failure_handling
from test_plan_viewer.agent import localization as agent_localization
from test_plan_viewer.agent import script_preparation as agent_script_preparation
from test_plan_viewer.agent import stream_consumer as agent_stream_consumer
from test_plan_viewer.agent.output_buffer import AgentOutputBatcher
from test_plan_viewer.artifacts import naming as artifact_naming
from test_plan_viewer.artifacts import paths as artifact_paths
from test_plan_viewer.artifacts import snapshots as artifact_snapshots
from test_plan_viewer.auth import model as auth_model
from test_plan_viewer.auth import repository as auth_repository
from test_plan_viewer.auth import service as auth_service
from test_plan_viewer.configuration import (
    APP_DIR,
    CONFIG_PATH,
    COVERAGE_PROFILES,
    coverage_profiles_for_language,
    DEFAULT_COVERAGE_PROFILE,
    DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS,
    DEFAULT_OPENCODE_TASK_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_TARGET_SYSTEM_CONFIG,
    DISABLED_DATABASE_BASELINE_CONFIG,
    MYSQL_IDENTIFIER_PATTERN,
    PROJECT_KEY_PATTERN,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_DISABLED,
    format_timeout_seconds,
    load_config as load_configuration,
    parse_auth_config,
    parse_boolean,
    parse_database_baseline_config,
    parse_plan_generation_config,
    parse_platform_database_config,
    parse_project_entry,
    parse_project_key,
    parse_project_opencode_config,
    parse_project_path_segment,
    parse_projects_config,
    parse_target_system_config,
    parse_timeout_seconds,
    validate_coverage_profile,
    normalize_project_language,
)
from test_plan_viewer.core.validation import (
    normalize_confidence,
    normalize_json_object_or_array,
    normalize_string_list,
    validate_uid,
)
from test_plan_viewer.execution import evidence as execution_evidence
from test_plan_viewer.execution import environment as execution_environment
from test_plan_viewer.execution import playwright as execution_playwright
from test_plan_viewer.execution import results as execution_results
from test_plan_viewer.execution import streaming as execution_streaming
from test_plan_viewer.generation import cases as generation_cases
from test_plan_viewer.generation import cancellation as generation_cancellation
from test_plan_viewer.generation import opencode as generation_opencode
from test_plan_viewer.generation.completion import PlanCompletionProbe
from test_plan_viewer.generation.event_stream import BoundedSseReader
from test_plan_viewer.i18n import localize_platform_error
from test_plan_viewer.generation import prompts as generation_prompts
from test_plan_viewer.generation.opencode import (
    build_opencode_prompt_parts,
    build_opencode_prompt_payload,
    build_opencode_session_payload,
    split_opencode_prompt,
)
from test_plan_viewer.infrastructure.mysql import (
    ensure_mysql_column,
    ensure_mysql_column_type,
    ensure_mysql_index,
    mysql_column_exists,
    mysql_column_type,
    mysql_index_exists,
    mysql_primary_key_columns,
    mysql_table_exists,
    mysql_table_has_columns,
    platform_mysql_connection,
    platform_table_name,
    platform_table_sql,
    quote_mysql_identifier,
)
from test_plan_viewer.infrastructure.job_logs import BufferedJobLogWriter, JobLogSnapshot
from test_plan_viewer.infrastructure.schema import (
    PLATFORM_DATABASE_SCHEMA_STATE,
    SchemaDependencies,
    ensure_platform_database_schema as bootstrap_platform_database_schema,
)
from test_plan_viewer.page_inventory import model as page_inventory_model
from test_plan_viewer.page_inventory import repository as page_inventory_repository
from test_plan_viewer.page_inventory import service as page_inventory_service
from test_plan_viewer.platform_records import (
    PlatformRecordRepository,
    PlatformRecordRepositoryDependencies,
    compact_json_dumps as serialize_compact_json,
    load_json_column as parse_json_column,
    record_updated_at_ms as resolve_record_updated_at_ms,
    validate_platform_record_bucket as validate_record_bucket,
    validate_platform_record_key as validate_record_key,
)
from test_plan_viewer.plans import workbook as plan_workbook
from test_plan_viewer.process_output import (
    PROCESS_OUTPUT_ENCODING_FALLBACKS,
    PROCESS_OUTPUT_MOJIBAKE_MARKERS,
    decode_process_output,
    get_console_text_encoding,
    get_process_output_encoding_candidates,
    normalize_process_output,
    score_decoded_process_output,
    summarize_process_output,
)
from test_plan_viewer.projects import archive as project_archive
from test_plan_viewer.projects import archive_service as project_archive_service
from test_plan_viewer.projects import model as project_model
from test_plan_viewer.projects import repository as project_repository
from test_plan_viewer.projects import service as project_service
from test_plan_viewer.projects import workspace as project_workspace
from test_plan_viewer.projects.context import (
    AUTHOR_CONTEXT,
    PROJECT_CONTEXT,
    current_author as current_context_author,
    current_context_project,
    path_relative_to_root,
    project_root as resolve_project_root,
    project_specs_dir as resolve_project_specs_dir,
    project_tests_dir as resolve_project_tests_dir,
    use_author_context,
    use_project_context,
)
from test_plan_viewer.repositories.tables import (
    get_agent_item_retry_flows_table,
    get_agent_run_attempts_table,
    get_agent_run_events_table,
    get_agent_run_steps_table,
    get_agent_runs_table,
    get_job_artifacts_table,
    get_page_inventory_table,
    get_platform_projects_table,
    get_requirement_module_plans_table,
    get_requirement_modules_table,
    get_requirements_table,
    get_setup_bindings_table,
    get_setup_runs_table,
    get_setup_scripts_table,
    get_test_asset_revisions_table,
    get_test_assets_table,
    get_test_jobs_table,
    get_test_run_artifacts_table,
    get_test_run_results_table,
    get_test_runs_table,
    get_test_suite_items_table,
    get_test_suites_table,
)
from test_plan_viewer.requirements import model as requirement_model
from test_plan_viewer.requirements import analysis_stream as requirement_analysis_stream
from test_plan_viewer.requirements import repository as requirement_repository
from test_plan_viewer.requirements import service as requirement_service
from test_plan_viewer.requirements import storage as requirement_storage
from test_plan_viewer.security import markdown as markdown_security
from test_plan_viewer.setup import model as setup_model
from test_plan_viewer.setup import repository as setup_repository
from test_plan_viewer.setup import runner as setup_runner
from test_plan_viewer.setup import service as setup_service
from test_plan_viewer.setup import validation as setup_validation
from test_plan_viewer.test_suites import model as test_suite_model
from test_plan_viewer.test_suites import repository as test_suite_repository
from test_plan_viewer.test_suites import service as test_suite_service
from test_plan_viewer.web import (
    AgentScriptPreparationWebServices,
    AuthWebServices,
    PageInventoryWebServices,
    PlanWorkbookWebServices,
    PlatformRecordServices,
    ProjectArchiveWebServices,
    ProjectWebServices,
    RequirementWebServices,
    SetupWebServices,
    TestSuiteWebServices,
    create_application,
    create_agent_script_preparation_blueprint,
    create_auth_blueprint,
    create_page_inventory_blueprint,
    create_plan_workbook_blueprint,
    create_platform_records_blueprint,
    create_project_archive_blueprint,
    create_projects_blueprint,
    create_requirements_blueprint,
    create_setup_blueprint,
    create_test_suites_blueprint,
)
from test_plan_viewer.web.projects import (
    create_project_response,
    get_project_settings_response,
    list_projects_response,
    save_project_settings_response,
)
from test_plan_viewer.web.project_archive import (
    export_project_response,
    import_project_response,
)
from test_plan_viewer.web.test_suites import (
    add_test_suite_items_response,
    create_test_suite_response,
    delete_test_suite_item_response,
    delete_test_suite_response,
    get_test_suite_response,
    list_test_suites_response,
    reorder_test_suite_items_response,
    update_test_suite_response,
)
from test_plan_viewer.web.sse import (
    iter_sse_events,
    sse_payload,
)
from werkzeug.security import check_password_hash, generate_password_hash

PROJECT_TEMPLATE_DIR = APP_DIR / "project-template"
PROJECT_TEMPLATE_DEPENDENCY_DIRS = (
    Path("node_modules"),
    Path(".opencode") / "node_modules",
)
PROJECT_TEMPLATE_TEXT_SUFFIXES = {".cjs", ".js", ".json", ".md", ".mjs", ".ts", ".txt", ""}
PROJECT_TEMPLATE_OPENCODE_PROMPTS = (
    "requirement-analyst.md",
    "test-platform-failure-analyst.md",
    "test-platform-reviewer.md",
    "plan-markdown-splitter.md",
    "playwright-test-generator.md",
    "playwright-test-healer.md",
    "playwright-test-planner.md",
)
PROJECT_EXPORT_FORMAT_VERSION = (
    project_archive.PROJECT_EXPORT_FORMAT_VERSION
)
PROJECT_IMPORT_MAX_BYTES = project_archive.PROJECT_IMPORT_MAX_BYTES
PROJECT_IMPORT_MAX_FILES = project_archive.PROJECT_IMPORT_MAX_FILES
PROJECT_IMPORT_MAX_UNCOMPRESSED_BYTES = (
    project_archive.PROJECT_IMPORT_MAX_UNCOMPRESSED_BYTES
)
PROJECT_IMPORT_MANIFEST_MAX_BYTES = (
    project_archive.PROJECT_IMPORT_MANIFEST_MAX_BYTES
)
SEED_MODULE_NAME = "seed"
SEED_SCRIPT_FILENAME = "seed.spec.ts"
DEFAULT_PLAN_PROMPT_MODULE_PLACEHOLDER = "<模块名>"
COVERAGE_POLICY_START = "<<<COVERAGE_POLICY_START>>>"
COVERAGE_POLICY_END = "<<<COVERAGE_POLICY_END>>>"
ABSOLUTE_PLAN_MAX_CASES = (
    generation_prompts.ABSOLUTE_PLAN_MAX_CASES
)
PLAN_GENERATION_JOBS = {}
PLAN_GENERATION_LOCK = threading.Lock()
OPENCODE_TASKS = {}
OPENCODE_TASK_LOCK = threading.Lock()
AGENT_RUN_TASKS = {}
AGENT_RUN_TASK_LOCK = threading.Lock()
AGENT_ITEM_RETRY_TASKS = {}
AGENT_ITEM_RETRY_TASK_LOCK = threading.RLock()
AGENT_PROJECT_OPERATION_LOCK = threading.RLock()
AGENT_ITEM_RETRY_CONTEXT = threading.local()
AGENT_RETRY_STEP_MERGE_LOCK = threading.Lock()
PROCESS_STARTED_AT_MS = int(time.time() * 1000)
SETUP_CONCURRENCY_LOCKS = setup_runner.SETUP_CONCURRENCY_LOCKS
SETUP_CONCURRENCY_LOCKS_GUARD = (
    setup_runner.SETUP_CONCURRENCY_LOCKS_GUARD
)
VIDEO_SUFFIXES = execution_evidence.VIDEO_SUFFIXES
SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TRACE_SUFFIXES = {".zip"}
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OPENCODE_TOOL_STATUS_ERROR_PATTERN = re.compile(r'"tool"\s*:\s*"([^"]+)".*?"status"\s*:\s*"running"', re.S)
JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
CJK_NAME_PATTERN = artifact_naming.CJK_NAME_PATTERN
ASCII_LETTER_PATTERN = artifact_naming.ASCII_LETTER_PATTERN
ARTIFACT_FILENAME_UNSAFE_PATTERN = artifact_naming.ARTIFACT_FILENAME_UNSAFE_PATTERN
PLAYWRIGHT_AGGREGATE_TEST_STATUSES = (
    execution_results.PLAYWRIGHT_AGGREGATE_TEST_STATUSES
)
PLAYWRIGHT_RESULT_STATUSES = (
    execution_results.PLAYWRIGHT_RESULT_STATUSES
)
PLAYWRIGHT_FAILURE_RESULT_STATUSES = (
    execution_results.PLAYWRIGHT_FAILURE_RESULT_STATUSES
)
CHINESE_ARTIFACT_NAMING_NOTICE_KEY = (
    generation_prompts.CHINESE_ARTIFACT_NAMING_NOTICE_KEY
)
CHINESE_ARTIFACT_NAMING_NOTICE = (
    generation_prompts.CHINESE_ARTIFACT_NAMING_NOTICE
)
PLATFORM_RECORD_BUCKETS = {
    "view_state",
    "script_run_records",
    "script_repair_records",
    "module_execution_records",
    "module_repair_batches",
    "plan_generation_records",
    "requirement_plan_generation_batches",
    "plan_script_generation_batches",
    "script_generation_records",
    "test_suites",
    "test_suite_execution_records",
}
AUTH_USER_STATUS_ACTIVE = auth_model.AUTH_USER_STATUS_ACTIVE
AUTH_USER_STATUS_DISABLED = auth_model.AUTH_USER_STATUS_DISABLED
AUTH_VALID_USER_STATUSES = auth_model.AUTH_VALID_USER_STATUSES
AUTH_MENU_PERMISSIONS = auth_model.AUTH_MENU_PERMISSIONS
AUTH_MENU_PERMISSION_CODES = auth_model.AUTH_MENU_PERMISSION_CODES
AUTH_MENU_SECTION_BY_PERMISSION = {
    permission["code"]: permission["section"]
    for permission in AUTH_MENU_PERMISSIONS
}
AUTH_ROLE_CODE_PATTERN = auth_model.AUTH_ROLE_CODE_PATTERN
REQUIREMENT_RECOVERY_EXCLUDED_DIRS = {
    ".git",
    ".opencode",
    ".test-plan-viewer",
    "__pycache__",
    "node_modules",
    "playwright-report",
    "test-results",
}
REQUIREMENT_RECOVERY_MAX_CANDIDATES = 200


class OpencodeTaskCancelled(RuntimeError):
    pass


class AgentItemRetryConflict(RuntimeError):
    def __init__(self, message, flow=None):
        super().__init__(message)
        self.flow = flow


class AgentItemFailure(RuntimeError):
    def __init__(
        self,
        message,
        *,
        job_id=None,
        test_run_id=None,
        result_id=None,
        asset_id=None,
        error_type=None,
        partial_artifacts=None,
    ):
        super().__init__(message)
        self.job_id = job_id or ""
        self.test_run_id = test_run_id or ""
        self.result_id = result_id
        self.asset_id = asset_id
        self.error_type = error_type or ""
        self.partial_artifacts = list(partial_artifacts or [])


class AgentStreamCommitAmbiguous(RuntimeError):
    """The server may have committed a stream batch despite client failure."""


SetupPreparationError = setup_model.SetupPreparationError


PLAYWRIGHT_REPORT_DIR_NAME = (
    execution_evidence.PLAYWRIGHT_REPORT_DIR_NAME
)
PLAYWRIGHT_CONFIG_FILENAMES = (
    execution_playwright.PLAYWRIGHT_CONFIG_FILENAMES
)
RUN_ARTIFACTS_DIR_NAME = "test-plan-viewer-runs"
DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS = (
    setup_validation.DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS
)
MAX_SETUP_SCRIPT_TIMEOUT_SECONDS = (
    setup_validation.MAX_SETUP_SCRIPT_TIMEOUT_SECONDS
)
SETUP_SCRIPT_OUTPUT_CAPTURE_BYTES = (
    setup_runner.SETUP_SCRIPT_OUTPUT_CAPTURE_BYTES
)
DATABASE_BASELINE_HELPER_DIR_NAME = ".test-plan-viewer"
DATABASE_BASELINE_RUNTIME_CONFIG_FILENAME = "database-baseline.config.json"
DATABASE_BASELINE_GLOBAL_SETUP_FILENAME = "database-baseline-global-setup.cjs"
DATABASE_BASELINE_PLAYWRIGHT_CONFIG_FILENAME = "playwright.baseline.config.ts"
DATABASE_BASELINE_LOCK_DIR_NAME = "baseline.restore.lock"
SCRIPT_GENERATION_DIR_NAME = "script-generation"
SCRIPT_GENERATION_CANDIDATE_DIR_NAME = "candidates"
SCRIPT_GENERATION_BACKUP_DIR_NAME = "backups"
EXECUTION_MODE_BATCH = execution_results.EXECUTION_MODE_BATCH
EXECUTION_MODE_BATCH_ONCE = execution_results.EXECUTION_MODE_BATCH_ONCE
EXECUTION_MODE_SERIAL_PER_FILE = (
    execution_results.EXECUTION_MODE_SERIAL_PER_FILE
)
VALID_EXECUTION_MODES = execution_results.VALID_EXECUTION_MODES
SETUP_BINDING_TARGET_TYPES = setup_validation.SETUP_BINDING_TARGET_TYPES
SETUP_BINDING_PRECEDENCE = setup_model.SETUP_BINDING_PRECEDENCE
DATABASE_RESET_ONCE_PER_RUN = (
    execution_results.DATABASE_RESET_ONCE_PER_RUN
)
DATABASE_RESET_BEFORE_EACH_FILE = (
    execution_results.DATABASE_RESET_BEFORE_EACH_FILE
)
JOB_LOG_TAIL_LIMIT = 100000
JOB_LOG_STORAGE_DIR_NAME = "jobs"
TEST_ASSET_TYPES = {"plan", "script"}
TEST_JOB_TYPES = {"requirement_analysis", "planner", "generator", "healer", "execution", "agent_review"}
TEST_JOB_STATUSES = {"queued", "running", "cancelling", "succeeded", "failed", "cancelled"}
AGENT_RUN_STATUSES = {
    "queued",
    "running",
    "awaiting_script_action",
    "succeeded",
    "succeeded_with_unresolved",
    "failed",
    "cancelled",
    "cancelling",
}
AGENT_STEP_STATUSES = {"queued", "running", "awaiting_action", "succeeded", "failed", "cancelled", "skipped"}
AGENT_ATTEMPT_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "skipped"}
AGENT_ITEM_RETRY_STATUSES = {
    "queued",
    "running",
    "finalizing",
    "succeeded",
    "failed",
    "blocked",
    "cancelling",
    "cancelled",
}
AGENT_ITEM_RETRY_ACTIVE_STATUSES = {"queued", "running", "finalizing", "cancelling"}
AGENT_ITEM_RETRY_PHASES = {"queued", "generating", "executing", "repairing", "verifying", "completed"}
AGENT_TERMINAL_STATUSES = {"succeeded", "succeeded_with_unresolved", "failed", "cancelled"}
AGENT_ACTIVE_STATUSES = {"queued", "running", "cancelling"}
AGENT_PAUSED_STATUSES = {"awaiting_script_action"}
CURRENT_AGENT_PIPELINE_VERSION = 3
AGENT_STEP_ORDER = [
    ("upload_requirement", "需求"),
    ("analyze_requirement", "需求解析"),
    ("review_modules", "模块审查"),
    ("generate_plans", "计划生成"),
    ("prepare_scripts", "脚本准备"),
    ("create_suite", "测试集"),
    ("run_suite", "执行"),
]
AGENT_STEP_KEYS = [step_key for step_key, _ in AGENT_STEP_ORDER]
AGENT_STEP_INDEX_BY_KEY = {step_key: index for index, step_key in enumerate(AGENT_STEP_KEYS)}
REQUIREMENT_UPLOAD_MAX_BYTES = 2 * 1024 * 1024
DIAGNOSTIC_BUNDLE_FORMAT_VERSION = 1
DIAGNOSTIC_TEXT_FILE_MAX_BYTES = 10 * 1024 * 1024
DIAGNOSTIC_BUNDLE_MAX_BYTES = 50 * 1024 * 1024
MODULE_PLAN_MAX_CASES = generation_prompts.MODULE_PLAN_MAX_CASES
REQUIREMENT_STATUSES = {"active", "deleted"}
REQUIREMENT_MODULE_STATUSES = (
    requirement_model.REQUIREMENT_MODULE_STATUSES
)
PAGE_INVENTORY_SOURCES = set(
    page_inventory_model.PAGE_INVENTORY_SOURCES
)

app = create_application(__name__)

PLATFORM_RECORD_REPOSITORY = PlatformRecordRepository(
    PlatformRecordRepositoryDependencies(
        get_database_config=lambda: get_platform_database_config(),
        ensure_schema=lambda config: ensure_platform_database_schema(config),
        table_sql=lambda config, table_name: platform_table_sql(config, table_name),
        get_project_id=lambda: get_current_project_id(),
        mysql_connection=lambda config: platform_mysql_connection(config),
        get_default_plan_filename=lambda module_name: get_default_plan_filename(module_name),
        now_ms=lambda: int(time.time() * 1000),
    ),
    PLATFORM_RECORD_BUCKETS,
)


def serialize_coverage_profiles():
    profiles = coverage_profiles_for_language(get_current_project_language())
    return [dict(profiles[key]) for key in ("core", "standard", "comprehensive")]


def get_coverage_profile(value=None):
    profile = validate_coverage_profile(value or get_plan_generation_config().get("default_coverage_profile"))
    return dict(coverage_profiles_for_language(get_current_project_language())[profile])


def build_coverage_policy_block(coverage_prompt):
    coverage_prompt = str(coverage_prompt or "").strip()
    if not coverage_prompt:
        return ""
    return f"{COVERAGE_POLICY_START}\n{coverage_prompt}\n{COVERAGE_POLICY_END}"


def compose_editable_plan_prompt(base_prompt, coverage_prompt):
    parts = [str(base_prompt or "").strip(), build_coverage_policy_block(coverage_prompt)]
    return "\n\n".join(part for part in parts if part).strip()


def normalize_plan_generation_request(value=None):
    value = value if isinstance(value, dict) else {}
    default_profile = get_plan_generation_config().get("default_coverage_profile", DEFAULT_COVERAGE_PROFILE)
    profile = validate_coverage_profile(value.get("coverage_profile"), default_profile)
    template_prompt = get_coverage_profile(profile)["template_prompt"]
    if "coverage_prompt" in value:
        coverage_prompt = str(value.get("coverage_prompt") or "").strip()
    else:
        coverage_prompt = template_prompt
    raw_customized = value.get("prompt_customized")
    if isinstance(raw_customized, str):
        explicit_customized = raw_customized.strip().lower() in {"1", "true", "yes", "on"}
    else:
        explicit_customized = bool(raw_customized)
    customized = explicit_customized or coverage_prompt != template_prompt
    return {
        "coverage_profile": profile,
        "coverage_prompt": coverage_prompt,
        "prompt_customized": customized,
    }


def build_plan_prompt_context(base_prompt, coverage_prompt, user_prompt, execution_prompt, profile, customized):
    return {
        "template_source": profile,
        "base_prompt": str(base_prompt or "").strip(),
        "coverage_prompt": str(coverage_prompt or "").strip(),
        "user_prompt": str(user_prompt or "").strip(),
        "platform_constraints": execution_prompt[len(str(user_prompt or "").strip()):].strip()
        if str(execution_prompt or "").startswith(str(user_prompt or "").strip())
        else "",
        "prompt_customized": bool(customized),
    }


def strip_legacy_coverage_notices(prompt):
    return generation_prompts.strip_legacy_coverage_notices(prompt)


def load_config():
    return load_configuration()


def get_auth_config():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    return config.get("auth") or parse_auth_config({})


def initialize_auth_secret():
    try:
        auth = get_auth_config()
    except RuntimeError:
        return
    if auth.get("session_secret"):
        app.secret_key = auth["session_secret"]


initialize_auth_secret()


def get_opencode_task_timeout_seconds():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])

    return config["opencode_task_timeout_seconds"]


def get_script_execution_timeout_seconds():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])

    return config["script_execution_timeout_seconds"]


def get_current_target_system_config():
    project = get_current_project()
    return parse_target_system_config(project.get("target_system"))


def get_plan_generation_config():
    project = get_current_project()
    return parse_plan_generation_config(project.get("plan_generation"))


def build_target_login_url(target_system=None):
    target_system = parse_target_system_config(target_system or get_current_target_system_config())
    login_url = target_system.get("login_url") or "/login"
    if urlparse.urlparse(login_url).scheme:
        return login_url

    base_url = (target_system.get("base_url") or "").rstrip("/")
    if not base_url:
        return login_url
    return f"{base_url}/{login_url.lstrip('/')}"


def get_playwright_base_url():
    return get_current_target_system_config().get("base_url", "")


def get_playwright_execution_env(extra=None):
    execution_environment.require_test_execution_enabled(os.environ)
    env = os.environ.copy()
    base_url = get_playwright_base_url()
    if base_url:
        env["PLAYWRIGHT_BASE_URL"] = base_url
    if extra:
        env.update({key: str(value) for key, value in extra.items() if value is not None})
    return env


def build_execution_env_metadata(extra=None):
    metadata = dict(extra or {})
    base_url = get_playwright_base_url()
    if base_url:
        metadata["base_url"] = base_url
    return metadata


def get_seed_script_relative_path():
    tests_dir = get_current_project().get("tests_dir") or "tests"
    return f"{tests_dir}/{SEED_MODULE_NAME}/{SEED_SCRIPT_FILENAME}"


def get_seed_script_file():
    return get_script_file(SEED_MODULE_NAME, SEED_SCRIPT_FILENAME)


def build_default_plan_prompt_template():
    target_system = get_current_target_system_config()
    seed_path = get_seed_script_relative_path()
    login_url = build_target_login_url(target_system)
    if get_current_project_language() == "en":
        username = target_system.get("username") or "<login username>"
        password = target_system.get("password") or "<login password>"
        return (
            "@playwright-test-planner\n"
            f"Use {seed_path} as the entry point. Open {login_url}, then sign in with username {username} and password {password} to explore the target module.\n"
            f"Module name: {DEFAULT_PLAN_PROMPT_MODULE_PLACEHOLDER}\n"
            "Design scenarios from the test scope confirmed in the generation dialog.\n"
            "Requirements: record the navigation path and prefer stable selectors."
        )
    username = target_system.get("username") or "<登录用户名>"
    password = target_system.get("password") or "<登录密码>"
    return (
        "@playwright-test-planner\n"
        f"请以 {seed_path} 作为入口，打开 {login_url}，"
        f"使用账号 {username}、密码 {password} 登录系统并探索目标模块。\n"
        f"模块名：{DEFAULT_PLAN_PROMPT_MODULE_PLACEHOLDER}\n"
        "请根据用户在生成弹窗中确认的测试范围设计测试场景。\n"
        "要求：记录进入该界面的导航路径；优先使用稳定定位器。"
    )


def get_specs_dir():
    return resolve_project_specs_dir(get_current_project())


def get_tests_dir():
    return resolve_project_tests_dir(get_current_project())


def get_project_root():
    return resolve_project_root(get_current_project())


def validate_module_name(module_name):
    return artifact_naming.validate_module_name(module_name)


def has_chinese_text(value):
    return artifact_naming.has_chinese_text(value, pattern=CJK_NAME_PATTERN)


def has_ascii_letters(value):
    return artifact_naming.has_ascii_letters(value, pattern=ASCII_LETTER_PATTERN)


def is_chinese_artifact_stem(stem):
    return artifact_naming.is_chinese_artifact_stem(
        stem,
        has_chinese=has_chinese_text,
        has_ascii=has_ascii_letters,
    )


def strip_artifact_suffix(value, suffix):
    return artifact_naming.strip_artifact_suffix(value, suffix)


def stable_numeric_suffix(value):
    return artifact_naming.stable_numeric_suffix(value)


def sanitize_chinese_artifact_stem(value, fallback="测试用例", unique_key=None):
    return artifact_naming.sanitize_chinese_artifact_stem(
        value,
        fallback=fallback,
        unique_key=unique_key,
        strip_suffix=strip_artifact_suffix,
        is_chinese_stem=is_chinese_artifact_stem,
        numeric_suffix=stable_numeric_suffix,
        unsafe_pattern=ARTIFACT_FILENAME_UNSAFE_PATTERN,
        ascii_pattern=ASCII_LETTER_PATTERN,
    )


def validate_chinese_artifact_stem(stem, label):
    return artifact_naming.validate_chinese_artifact_stem(
        stem,
        label,
        is_chinese_stem=is_chinese_artifact_stem,
    )


def validate_plan_filename(filename):
    return artifact_naming.validate_plan_filename(filename)


def validate_chinese_plan_filename(filename):
    return artifact_naming.validate_chinese_plan_filename(
        filename,
        validate_plan=validate_plan_filename,
        validate_chinese_stem=validate_chinese_artifact_stem,
    )


def get_default_plan_filename(module_name):
    return artifact_naming.get_default_plan_filename(
        module_name,
        validate_module=validate_module_name,
    )


def get_plan_filename_from_name(plan_name, module_name):
    return artifact_naming.get_plan_filename_from_name(
        plan_name,
        module_name,
        validate_plan=validate_plan_filename,
    )


def get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=None, unique_key=None):
    return artifact_naming.get_chinese_plan_filename_from_name(
        plan_name,
        module_name,
        fallback_stem=fallback_stem,
        unique_key=unique_key,
        plan_filename_from_name=get_plan_filename_from_name,
        is_chinese_stem=is_chinese_artifact_stem,
        has_chinese=has_chinese_text,
        sanitize_stem=sanitize_chinese_artifact_stem,
        validate_chinese_plan=validate_chinese_plan_filename,
    )


def get_case_plan_filename_from_title(filename, title, index=None):
    return artifact_naming.get_case_plan_filename_from_title(
        filename,
        title,
        index=index,
        strip_suffix=strip_artifact_suffix,
        is_chinese_stem=is_chinese_artifact_stem,
        sanitize_stem=sanitize_chinese_artifact_stem,
        validate_chinese_plan=validate_chinese_plan_filename,
    )


def validate_script_filename(filename):
    return artifact_naming.validate_script_filename(filename)


def script_stem_from_filename(filename):
    return artifact_naming.script_stem_from_filename(
        filename,
        strip_suffix=strip_artifact_suffix,
    )


def validate_chinese_script_filename(filename):
    return artifact_naming.validate_chinese_script_filename(
        filename,
        validate_script=validate_script_filename,
        script_stem=script_stem_from_filename,
        validate_chinese_stem=validate_chinese_artifact_stem,
    )


def get_plan_file(module_name, plan_filename=None):
    module_name = validate_module_name(module_name)
    plan_filename = validate_plan_filename(plan_filename or get_default_plan_filename(module_name))
    return artifact_paths.build_plan_file(
        get_specs_dir(),
        module_name,
        plan_filename,
    )


def get_module_file(module_name):
    return get_plan_file(module_name)


def get_plan_target_path(module_name, plan_filename=None):
    return get_plan_file(module_name, plan_filename)


def is_plan_index_filename(filename):
    return artifact_naming.is_plan_index_filename(filename)


def plan_payload(plan_file, module_name):
    return artifact_naming.plan_payload(
        plan_file,
        module_name,
        default_plan_filename=get_default_plan_filename,
        plan_index_filename=is_plan_index_filename,
    )


def get_script_module_dir(module_name):
    module_name = validate_module_name(module_name)
    return artifact_paths.build_script_module_dir(get_tests_dir(), module_name)


def get_script_filename_from_plan_filename(plan_filename):
    return artifact_naming.get_script_filename_from_plan_filename(
        plan_filename,
        validate_plan=validate_plan_filename,
        validate_script=validate_script_filename,
    )


def get_generated_script_filename_from_plan_filename(plan_filename):
    if get_current_project_language() == "en":
        return get_script_filename_from_plan_filename(plan_filename)
    return artifact_naming.get_generated_script_filename_from_plan_filename(
        plan_filename,
        validate_plan=validate_plan_filename,
        is_chinese_stem=is_chinese_artifact_stem,
        validate_chinese_script=validate_chinese_script_filename,
        sanitize_stem=sanitize_chinese_artifact_stem,
    )


def get_generation_workspace_dir():
    return artifact_paths.build_generation_workspace_dir(
        get_project_root(),
        DATABASE_BASELINE_HELPER_DIR_NAME,
        SCRIPT_GENERATION_DIR_NAME,
    )


def get_script_generation_candidate_file(module_name, plan_filename, job_id):
    module_name = validate_module_name(module_name)
    filename = get_generated_script_filename_from_plan_filename(plan_filename)
    candidate_root = get_generation_workspace_dir() / SCRIPT_GENERATION_CANDIDATE_DIR_NAME
    return artifact_paths.build_script_generation_candidate_file(
        candidate_root,
        module_name,
        filename,
        job_id,
    )


def get_script_generation_backup_dir(module_name):
    module_name = validate_module_name(module_name)
    backup_root = get_generation_workspace_dir() / SCRIPT_GENERATION_BACKUP_DIR_NAME
    return artifact_paths.build_script_generation_backup_dir(
        backup_root,
        module_name,
    )


def sha256_bytes(content):
    return artifact_snapshots.sha256_bytes(content)


def read_file_bytes(path):
    return artifact_snapshots.read_file_bytes(path)


def managed_file_snapshot(paths):
    return artifact_snapshots.managed_file_snapshot(
        paths,
        read_bytes=read_file_bytes,
        digest=sha256_bytes,
    )


def iter_generation_managed_files():
    yield from artifact_snapshots.iter_generation_managed_files(
        get_specs_dir(),
        get_tests_dir(),
    )


def collect_generation_managed_files(module_name, plan_file, target_file):
    return artifact_snapshots.collect_generation_managed_files(
        plan_file,
        target_file,
        iter_generation_managed_files(),
    )


def file_hash(path):
    return artifact_snapshots.file_hash(
        path,
        read_bytes=read_file_bytes,
        digest=sha256_bytes,
    )


def get_workspace_relative_path(path):
    return artifact_paths.workspace_relative_path(get_project_root(), path)


DATABASE_BASELINE_WRITE_OPERATION_NOTICE = (
    generation_prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE
)
DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY = (
    generation_prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY
)


def _generation_prompt_dependencies():
    return generation_prompts.PromptDependencies(
        get_database_baseline_config=lambda: get_database_baseline_config(),
        get_workspace_relative_path=lambda path: get_workspace_relative_path(path),
        parse_target_system_config=lambda value: parse_target_system_config(value),
        build_target_login_url=lambda target_system: build_target_login_url(target_system),
        get_seed_script_relative_path=lambda: get_seed_script_relative_path(),
        get_script_test_relative_path=lambda module_name, filename: (
            get_script_test_relative_path(module_name, filename)
        ),
        get_project_language=lambda: get_current_project_language(),
    )


def _generation_case_dependencies():
    return generation_cases.CaseDependencies(
        get_specs_dir=lambda: get_specs_dir(),
        validate_module_name=lambda module_name: validate_module_name(module_name),
        get_plan_file=lambda module_name, filename: get_plan_file(module_name, filename),
        plan_payload=lambda path, module_name: plan_payload(path, module_name),
        ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
        file_exists=lambda path: path.exists(),
        read_text=lambda path: path.read_text(encoding="utf-8"),
        write_text=lambda path, value: path.write_text(
            value,
            encoding="utf-8",
            newline="",
        ),
        get_project_language=lambda: agent_project_language(),
    )


def dedupe_prompt_notice(prompt, notice):
    return generation_prompts.dedupe_prompt_notice(prompt, notice)


def append_prompt_notice_once(prompt, notice, key):
    return generation_prompts.append_prompt_notice_once(prompt, notice, key)


def get_database_baseline_write_operation_notice():
    return generation_prompts.get_database_baseline_write_operation_notice(
        _generation_prompt_dependencies()
    )


def append_database_baseline_write_operation_notice(prompt):
    return generation_prompts.append_database_baseline_write_operation_notice(
        prompt,
        _generation_prompt_dependencies(),
    )


def append_chinese_artifact_naming_notice(prompt):
    return generation_prompts.append_chinese_artifact_naming_notice(prompt)


def dedupe_chinese_artifact_naming_notice(prompt):
    return generation_prompts.dedupe_chinese_artifact_naming_notice(prompt)


def build_generation_prompt(prompt, target_path):
    return generation_prompts.build_generation_prompt(
        prompt,
        target_path,
        _generation_prompt_dependencies(),
    )


def build_multiple_plan_generation_prompt(prompt, module_name, target_path):
    return generation_prompts.build_multiple_plan_generation_prompt(
        prompt,
        module_name,
        target_path,
        _generation_prompt_dependencies(),
    )


def build_markdown_plan_split_prompt(module_name, source_plan_file, markdown_text):
    return generation_prompts.build_markdown_plan_split_prompt(
        module_name,
        source_plan_file,
        markdown_text,
        _generation_prompt_dependencies(),
    )


def build_script_generation_prompt(
    prompt,
    module_name,
    plan_file,
    script_dir,
    target_file=None,
    candidate_file=None,
):
    return generation_prompts.build_script_generation_prompt(
        prompt,
        module_name,
        plan_file,
        script_dir,
        _generation_prompt_dependencies(),
        target_file=target_file,
        candidate_file=candidate_file,
    )


def build_seed_generation_prompt(target_system, target_file):
    return generation_prompts.build_seed_generation_prompt(
        target_system,
        target_file,
        _generation_prompt_dependencies(),
    )


def build_script_run_prompt(prompt, module_name, filename, script_file):
    return generation_prompts.build_script_run_prompt(
        prompt,
        module_name,
        filename,
        script_file,
        _generation_prompt_dependencies(),
    )


def normalize_case_filename(value, title, index=None, *, language="zh-CN"):
    return generation_cases.normalize_case_filename(
        value,
        title,
        index=index,
        language=language,
    )


def extract_case_index(markdown_text):
    return generation_cases.extract_case_index(markdown_text, language=agent_project_language())


def normalize_case_steps(value):
    return generation_cases.normalize_case_steps(value)


def list_text_items(value):
    return generation_cases.list_text_items(value)


def case_to_markdown(module_name, source_filename, case):
    return generation_cases.case_to_markdown(module_name, source_filename, case)


def normalize_case_index_cases(data):
    return generation_cases.normalize_case_index_cases(data, language=agent_project_language())


def validate_multiple_plan_artifact(path):
    """Validate an intermediate multi-case plan without mutating it."""

    source_path = Path(path)
    if not source_path.exists() or not source_path.is_file() or source_path.stat().st_size <= 0:
        raise ValueError(agent_message("plan_missing"))
    content = source_path.read_text(encoding="utf-8")
    payload = extract_case_index(content)
    cases = normalize_case_index_cases(payload)
    if len(cases) > generation_prompts.ABSOLUTE_PLAN_MAX_CASES:
        raise ValueError(agent_message("plan_too_many", count=len(cases), limit=generation_prompts.ABSOLUTE_PLAN_MAX_CASES))
    filenames = []
    filename_keys = set()
    source_key = artifact_naming.plan_filename_collision_key(source_path.name)
    language = agent_project_language()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(agent_message("case_not_object", index=index))
        raw_filename = str(case.get("filename") or "").strip()
        title = str(case.get("title") or case.get("name") or "").strip()
        filename = normalize_case_filename(
            raw_filename,
            title,
            index=index,
            language=language,
        )
        validate_plan_filename(filename)
        raw_filename_key = artifact_naming.plan_filename_collision_key(raw_filename)
        filename_key = artifact_naming.plan_filename_collision_key(filename)
        if (
            raw_filename_key == source_key
            or raw_filename.startswith("_")
            or filename_key == source_key
            or filename.startswith("_")
            or filename_key in filename_keys
        ):
            raise ValueError(agent_message("case_filename_invalid", filename=filename))
        filenames.append(filename)
        filename_keys.add(filename_key)
    return {"payload": payload, "cases": cases, "filenames": filenames}


def split_case_index_cases(
    module_name,
    source_filename,
    cases,
    overwrite=False,
    source_plan_file=None,
):
    return generation_cases.split_case_index_cases(
        module_name,
        source_filename,
        cases,
        _generation_case_dependencies(),
        overwrite=overwrite,
        source_plan_file=source_plan_file,
    )


def split_case_index_plan(module_name, source_plan_file, overwrite=False):
    return generation_cases.split_case_index_plan(
        module_name,
        source_plan_file,
        _generation_case_dependencies(),
        overwrite=overwrite,
    )


def call_markdown_plan_splitter(module_name, source_plan_file, parent_job_id=None, run_id=None, step_key=None):
    ensure_plan_markdown_splitter_agent()
    markdown_text = source_plan_file.read_text(encoding="utf-8")
    prompt = build_markdown_plan_split_prompt(module_name, source_plan_file, markdown_text)
    job_id = f"plan-split-{uuid.uuid4().hex}"
    create_test_job("agent_review", job_id=job_id, status="running", prompt=prompt)
    if parent_job_id:
        append_test_job_log(parent_job_id, f"{agent_message('split_fallback', path=source_plan_file)}\n")
    if run_id and step_key:
        append_agent_event(
            run_id,
            step_key,
            "status",
            agent_message("splitting_markdown", filename=source_plan_file.name),
            {"job_id": job_id, "source": str(source_plan_file)},
            job_id=job_id,
        )
        agent_set_current_job(run_id, job_id)
    try:
        response = send_opencode_prompt(prompt, default_agent="plan-markdown-splitter")
        output_text = collect_opencode_response_text(response)
        append_test_job_log(job_id, output_text[-JOB_LOG_TAIL_LIMIT:])
        if parent_job_id:
            append_test_job_log(parent_job_id, f"{agent_message('split_json_returned', job_id=job_id)}\n")
        parsed = extract_json_object_from_text(output_text)
        cases = normalize_case_index_cases(parsed)
        finish_test_job(job_id, "succeeded")
        if run_id and step_key:
            append_agent_event(
                run_id,
                step_key,
                "status",
                agent_message("split_completed", count=len(cases)),
                {"job_id": job_id, "case_count": len(cases)},
                job_id=job_id,
            )
        return {"job_id": job_id, "cases": cases}
    except Exception as exc:
        failure_message = agent_message("split_failed", error=exc)
        append_test_job_log(job_id, f"{failure_message}\n")
        finish_test_job(job_id, "failed", error=failure_message)
        if run_id and step_key:
            append_agent_event(
                run_id,
                step_key,
                "error",
                failure_message,
                {"error": failure_message},
                job_id=job_id,
            )
        raise
    finally:
        if run_id and step_key:
            agent_set_current_job(run_id, "")


def split_or_repair_multiple_plan(module_name, source_plan_file, overwrite=False, job_id=None, run_id=None, step_key=None):
    try:
        result = split_case_index_plan(module_name, source_plan_file, overwrite=overwrite)
        result["repair_used"] = False
        return result
    except Exception as split_error:
        retry_message = agent_message("split_retry", error=split_error)
        if job_id:
            append_test_job_log(job_id, f"{retry_message}\n")
        if run_id and step_key:
            append_agent_event(
                run_id,
                step_key,
                "log",
                retry_message,
                {"error": str(split_error)},
            )
        repair = call_markdown_plan_splitter(
            module_name,
            source_plan_file,
            parent_job_id=job_id,
            run_id=run_id,
            step_key=step_key,
        )
        result = split_case_index_cases(
            module_name,
            source_plan_file.name,
            repair["cases"],
            overwrite=overwrite,
            source_plan_file=source_plan_file,
        )
        result["repair_used"] = True
        result["repair_job_id"] = repair.get("job_id")
        result["original_split_error"] = str(split_error)
        return result


def delete_intermediate_plan_file(module_name, plan_file, message):
    if not plan_file.exists():
        return None
    try:
        return delete_plan_asset(module_name, plan_file.name, message=message)
    except Exception:
        plan_file.unlink(missing_ok=True)
        asset = get_test_asset_by_path("plan", plan_file)
        deleted_asset = mark_test_asset_deleted(asset)
        return {
            "ok": True,
            "module": module_name,
            "plan_filename": plan_file.name,
            "archive": {"archived": False, "path": str(plan_file), "reason": "直接删除中间计划文件。"},
            "asset": deleted_asset,
            "error": None,
        }


def finalize_multiple_plan_files(
    module_name,
    source_plan_file,
    job_id,
    source_message,
    split_message_prefix,
    requirement=None,
    requirement_module_uid=None,
    run_id=None,
    step_key=None,
    coverage_profile=DEFAULT_COVERAGE_PROFILE,
    prompt_customized=False,
):
    split_result = split_or_repair_multiple_plan(
        module_name,
        source_plan_file,
        overwrite=False,
        job_id=job_id,
        run_id=run_id,
        step_key=step_key,
    )
    conflicts = split_result.get("conflicts") or []
    if conflicts:
        error = agent_localization.plan_conflict_error(agent_project_language(), conflicts)
        if job_id:
            append_test_job_log(job_id, f"{error}\n")
        raise RuntimeError(error)

    source_asset = sync_plan_asset(
        module_name,
        source_plan_file,
        change_source="planner",
        source_job_id=job_id,
        message=source_message,
    )

    plans = []
    available_plans = [
        *(split_result.get("created") or []),
        *(split_result.get("reused") or []),
    ]
    for available_plan in available_plans:
        created_file = get_plan_file(module_name, available_plan["filename"])
        asset = sync_plan_asset(
            module_name,
            created_file,
            change_source="planner",
            source_job_id=job_id,
            message=f"{split_message_prefix}: {module_name}/{available_plan['filename']}",
        )
        plans.append(
            {
                "module_name": module_name,
                "plan_filename": available_plan["filename"],
                "path": str(created_file),
                "asset": serialize_asset(asset),
            }
        )

    if not plans:
        raise RuntimeError("多计划模式没有生成任何可用的单用例计划。")

    updated_module = None
    first_asset = plans[0].get("asset") if plans else None
    if requirement and requirement_module_uid and first_asset:
        for plan in reversed(plans):
            plan_asset = plan.get("asset") or {}
            updated_module = link_requirement_module_plan(
                requirement["id"],
                requirement_module_uid,
                plan_asset.get("asset_id"),
                job_id,
                coverage_profile=coverage_profile,
                prompt_customized=prompt_customized,
            )

    # Keep the validated source recoverable until every generated/reused plan
    # has been registered and linked successfully.
    delete_result = delete_intermediate_plan_file(
        module_name,
        source_plan_file,
        f"delete intermediate multiple plan: {module_name}/{source_plan_file.name}",
    )
    if job_id and delete_result:
        append_test_job_log(job_id, f"{agent_message('multiple_source_deleted', filename=source_plan_file.name)}\n")
    deleted_source_asset = (delete_result or {}).get("asset") if isinstance(delete_result, dict) else None

    return {
        "plan_filename": source_plan_file.name,
        "plan_name": source_plan_file.stem,
        "generation_mode": "multiple",
        "asset": first_asset,
        "source_asset": serialize_asset(deleted_source_asset) or serialize_asset(source_asset),
        "plans": plans,
        "split": split_result,
        "deleted_source": delete_result,
        "requirement_module": serialize_requirement_module(updated_module) if updated_module else None,
        "revisions": (
            [serialize_revision(item) for item in list_asset_revisions(first_asset["asset_id"], 10)]
            if first_asset
            else []
        ),
    }


def get_project_relative_path(file_path):
    return path_relative_to_root(get_project_root(), file_path)


def _execution_evidence_dependencies():
    return execution_evidence.EvidenceDependencies(
        get_project_root=lambda: get_project_root(),
        get_project_relative_path=lambda path: get_project_relative_path(path),
        resolve_path=lambda path: Path(path).resolve(strict=False),
        path_exists=lambda path: path.exists(),
        path_is_file=lambda path: path.is_file(),
        path_is_dir=lambda path: path.is_dir(),
        stat_path=lambda path: path.stat(),
        rglob=lambda path, pattern: path.rglob(pattern),
    )


def get_run_video_file(relative_path):
    return execution_evidence.get_run_video_file(
        relative_path,
        _execution_evidence_dependencies(),
    )


def serialize_run_video(video_file):
    return execution_evidence.serialize_run_video(
        video_file,
        _execution_evidence_dependencies(),
    )


def get_playwright_report_file(relative_path):
    return execution_evidence.get_playwright_report_file(
        relative_path,
        _execution_evidence_dependencies(),
    )


def serialize_playwright_report(report_file):
    return execution_evidence.serialize_playwright_report(
        report_file,
        _execution_evidence_dependencies(),
    )


def find_latest_playwright_report(started_at, report_dir=None):
    return execution_evidence.find_latest_playwright_report(
        started_at,
        _execution_evidence_dependencies(),
        report_dir=report_dir,
    )


def find_latest_run_video(started_at, results_dir=None):
    return execution_evidence.find_latest_run_video(
        started_at,
        _execution_evidence_dependencies(),
        results_dir=results_dir,
    )


def build_run_video_result(started_at, results_dir=None):
    return execution_evidence.build_run_video_result(
        started_at,
        _execution_evidence_dependencies(),
        results_dir=results_dir,
    )


def build_playwright_report_result(started_at, report_dir=None):
    return execution_evidence.build_playwright_report_result(
        started_at,
        _execution_evidence_dependencies(),
        report_dir=report_dir,
    )


def get_script_test_relative_path(module_name, filename):
    module_name = validate_module_name(module_name)
    filename = validate_script_filename(filename)
    tests_dir = get_current_project().get("tests_dir") or "tests"
    return f"{tests_dir}/{module_name}/{filename}"


def get_database_baseline_config():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    baseline_config = config.get("database_baseline") or {"enabled": False}
    project = get_current_project()
    project_baseline_config = project.get("database_baseline") if isinstance(project, dict) else None
    if isinstance(project_baseline_config, dict):
        return project_baseline_config
    return baseline_config


def redact_sensitive_text(value, *configs, limit=None):
    text = normalize_process_output(value)
    passwords = set()
    for config in configs:
        if not isinstance(config, dict):
            continue
        password = str(config.get("password") or "")
        if password:
            passwords.add(password)
    for password in sorted(passwords, key=len, reverse=True):
        if len(password) >= 2:
            text = text.replace(password, "******")

    text = re.sub(r"(?i)(password|passwd|pwd)\s*([=:])\s*([^\s'\";,]+)", r"\1\2******", text)
    text = re.sub(r"(?i)(//[^:\s/@]+:)([^@\s/]+)(@)", r"\1******\3", text)
    text = re.sub(r"([A-Za-z0-9_.$-]+)/(\"[^\"]+\"|'[^']+'|[^\s@]+)@", r"\1/******@", text)
    if limit and len(text) > limit:
        omitted = len(text) - limit
        return f"{text[:limit]}\n...[已截断 {omitted} 个字符]"
    return text


def redact_database_messages(messages, baseline_config=None):
    baseline_config = baseline_config or get_database_baseline_config()
    target_system = get_current_target_system_config()
    return [redact_sensitive_text(message, baseline_config, target_system, limit=4000) for message in messages]


def ensure_command_executable_available(command, label):
    if not command:
        raise RuntimeError(f"{label} 未配置。")
    if isinstance(command, str):
        return f"{label} 使用 shell 命令，跳过可执行文件探测。"
    if not isinstance(command, list) or not command:
        raise RuntimeError(f"{label} 必须是字符串命令或非空数组命令。")

    executable = str(command[0] or "").strip()
    if not executable:
        raise RuntimeError(f"{label} 缺少可执行程序。")
    executable_path = Path(executable).expanduser()
    if executable_path.is_absolute() or len(executable_path.parts) > 1:
        if not executable_path.exists():
            raise RuntimeError(f"{label} 可执行程序不存在：{executable}")
        return f"{label} 可执行程序存在：{executable}"
    resolved = shutil.which(executable)
    if not resolved:
        raise RuntimeError(f"{label} 可执行程序不在 PATH 中：{executable}")
    return f"{label} 可执行程序存在：{resolved}"


def run_database_test_command(command, working_directory, timeout_seconds, baseline_config):
    cwd = resolve_optional_path(working_directory) if working_directory else None
    shell = isinstance(command, str)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        shell=shell,
        capture_output=True,
        timeout=timeout_seconds,
    )
    output = summarize_process_output(completed.stdout, completed.stderr, limit=8000)
    output = redact_sensitive_text(output, baseline_config, get_current_target_system_config(), limit=8000)
    if completed.returncode != 0:
        raise RuntimeError(f"数据库连接测试命令失败，退出码：{completed.returncode}\n{output}".strip())
    return output


def test_file_database_baseline_connection(config):
    database_path = resolve_optional_path(config.get("database_path"))
    baseline_path = resolve_optional_path(config.get("baseline_path"))
    if not database_path:
        raise RuntimeError("文件数据库基线未配置 database_path。")
    if not baseline_path:
        raise RuntimeError("文件数据库基线未配置 baseline_path。")
    if not database_path.is_file():
        raise RuntimeError(f"运行数据库文件不存在：{database_path}")

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = baseline_path.parent / f".database-baseline-test-{uuid.uuid4().hex}.tmp"
    try:
        shutil.copy2(database_path, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return [
        f"运行数据库文件可读：{database_path}",
        f"基线目录可写：{baseline_path.parent}",
    ]


def test_command_database_baseline_connection(config):
    timeout_seconds = config.get("timeout_seconds") or DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS
    working_directory = config.get("working_directory")
    cwd = resolve_optional_path(working_directory) if working_directory else None
    if cwd and not cwd.exists():
        raise RuntimeError(f"数据库基线工作目录不存在：{cwd}")

    messages = []
    if cwd:
        messages.append(f"工作目录存在：{cwd}")
    messages.append(ensure_command_executable_available(config.get("backup_command"), "备份命令"))
    messages.append(ensure_command_executable_available(config.get("restore_command"), "恢复命令"))
    test_command = config.get("test_command")
    if test_command:
        messages.append(ensure_command_executable_available(test_command, "连接测试命令"))
        output = run_database_test_command(test_command, working_directory, timeout_seconds, config)
        messages.append("连接测试命令执行成功。")
        if output:
            messages.append(output)
    else:
        messages.append("未配置 test_command，已完成工作目录和命令可执行性检查。")
    return messages


def test_database_baseline_connection(config=None):
    config = config or get_database_baseline_config()
    if not config.get("enabled"):
        raise RuntimeError("当前项目未启用数据库基线。")
    mode = config.get("mode")
    if mode == "file":
        return test_file_database_baseline_connection(config)
    if mode == "command":
        return test_command_database_baseline_connection(config)
    raise RuntimeError(f"不支持的数据库基线模式：{mode}")


def get_opencode_config():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    opencode_config = {
        "opencode_server_url": config["opencode_server_url"],
        "opencode_username": config.get("opencode_username", "opencode") or "opencode",
        "opencode_password": config.get("opencode_password", ""),
    }
    project = get_current_project()
    project_opencode_config = project.get("opencode_config") if isinstance(project, dict) else None
    if isinstance(project_opencode_config, dict):
        for key in ("opencode_server_url", "opencode_username", "opencode_password"):
            if key in project_opencode_config:
                opencode_config[key] = project_opencode_config[key]

    opencode_config["opencode_server_url"] = (
        str(opencode_config.get("opencode_server_url") or "http://127.0.0.1:4096").strip()
        or "http://127.0.0.1:4096"
    )
    opencode_config["opencode_username"] = str(opencode_config.get("opencode_username") or "opencode").strip() or "opencode"
    opencode_config["opencode_password"] = str(opencode_config.get("opencode_password", ""))
    return opencode_config


def get_platform_database_config():
    config = load_config()
    if config["error"]:
        raise RuntimeError(config["error"])
    return config.get("platform_database") or {"enabled": False, "type": "mysql"}


def current_time_ms():
    return int(time.time() * 1000)


def _project_repository_dependencies():
    return project_repository.ProjectRepositoryDependencies(
        get_platform_database_config=get_platform_database_config,
        ensure_platform_database_schema=ensure_platform_database_schema,
        get_platform_projects_table=get_platform_projects_table,
        platform_mysql_connection=platform_mysql_connection,
        get_config_projects=get_config_projects,
        get_config_default_project=get_config_default_project,
        serialize_project_row=serialize_project_row,
        parse_plan_generation_config=parse_plan_generation_config,
        current_time_ms=current_time_ms,
        platform_table_sql=platform_table_sql,
    )


def _project_workspace_dependencies():
    return project_workspace.ProjectWorkspaceDependencies(
        load_config=load_config,
        template_dir=PROJECT_TEMPLATE_DIR,
        dependency_dirs=tuple(PROJECT_TEMPLATE_DEPENDENCY_DIRS),
        text_suffixes=frozenset(PROJECT_TEMPLATE_TEXT_SUFFIXES),
        subprocess_run=subprocess.run,
        get_project_workspace_root_text=get_project_workspace_root_text,
        get_project_template_dependency_source_text=(
            get_project_template_dependency_source_text
        ),
        get_project_dependency_source_root_for_create=(
            get_project_dependency_source_root_for_create
        ),
        template_relative_target_path=template_relative_target_path,
        render_project_template_text=render_project_template_text,
        copy_project_template_files=copy_project_template_files,
        copy_project_template_dependencies=(
            copy_project_template_dependencies
        ),
        run_project_git_command=run_project_git_command,
        initialize_created_project_git_repo=(
            initialize_created_project_git_repo
        ),
    )


def _project_service_dependencies():
    return project_service.ProjectServiceDependencies(
        load_config=load_config,
        parse_project_key=parse_project_key,
        parse_project_path_segment=parse_project_path_segment,
        get_platform_database_config=get_platform_database_config,
        ensure_platform_database_schema=ensure_platform_database_schema,
        assert_project_key_available=assert_project_key_available,
        get_project_workspace_root_for_create=(
            get_project_workspace_root_for_create
        ),
        get_created_project_root=get_created_project_root,
        initialize_created_project_directory=(
            initialize_created_project_directory
        ),
        create_project_record=lambda config, project, project_root: (
            project_repository.create_project_record(
                config,
                project,
                project_root,
                _project_repository_dependencies(),
            )
        ),
        current_context_project=current_context_project,
        get_project_by_key=get_project_by_key,
        get_current_project=get_current_project,
        update_project_settings=(
            lambda config, project_key, target_system,
            database_baseline, plan_generation: (
                project_repository.update_project_settings(
                    config,
                    project_key,
                    target_system,
                    database_baseline,
                    plan_generation,
                    _project_repository_dependencies(),
                )
            )
        ),
        update_project_metadata=lambda config, project_key, metadata: project_repository.update_project_metadata(
            config, project_key, metadata, _project_repository_dependencies()
        ),
        delete_project_data=lambda config, project_key: project_repository.delete_project_data(
            config, project_key, _project_repository_dependencies()
        ),
        update_project_language=(
            lambda config, project_key, language: (
                project_repository.update_project_language(
                    config,
                    project_key,
                    language,
                    _project_repository_dependencies(),
                )
            )
        ),
        remove_tree=shutil.rmtree,
        uuid_hex=lambda: uuid.uuid4().hex,
    )


def _project_service():
    return project_service.ProjectService(
        _project_service_dependencies()
    )


def _project_web_services():
    return ProjectWebServices(
        list_projects=lambda: list_projects_from_mysql(),
        serialize_project=(
            lambda project, include_sensitive=False: serialize_project(
                project,
                include_sensitive=include_sensitive,
            )
        ),
        get_current_project=lambda: get_current_project(),
        get_project_workspace_root_text=(
            lambda: get_project_workspace_root_text()
        ),
        create_project=lambda payload: create_project_in_mysql(payload),
        update_project=lambda project_key, payload: update_project_in_mysql(project_key, payload),
        delete_project=lambda project_key, confirmation_name, current_project_key: delete_project_in_mysql(
            project_key, confirmation_name, current_project_key
        ),
        get_config_project_keys=(
            lambda: {
                project.get("project_key")
                for project in get_config_projects()
                if project.get("project_key")
            }
        ),
        get_default_project_language=(
            lambda: get_config_default_project_language()
        ),
        parse_target_system_config=(
            lambda value: parse_target_system_config(value)
        ),
        get_database_baseline_config=(
            lambda: get_database_baseline_config()
        ),
        get_plan_generation_config=lambda: get_plan_generation_config(),
        parse_database_baseline_config=(
            lambda value: parse_database_baseline_config(value)
        ),
        parse_plan_generation_config=(
            lambda value: parse_plan_generation_config(value)
        ),
        update_project_settings=(
            lambda target_system, database_baseline, plan_generation: (
                update_current_project_settings_in_mysql(
                    target_system,
                    database_baseline,
                    plan_generation,
                )
            )
        ),
        update_project_language=(
            lambda language: update_current_project_language_in_mysql(
                language
            )
        ),
        can_manage_project_language=lambda: current_user_is_admin(),
        serialize_coverage_profiles=lambda: serialize_coverage_profiles(),
        get_seed_script_relative_path=(
            lambda: get_seed_script_relative_path()
        ),
    )


def get_config_projects():
    return _project_service().get_config_projects()


def get_config_default_project():
    return _project_service().get_config_default_project()


def get_config_default_project_language():
    return _project_service().get_config_default_project_language()


def serialize_project(project, include_sensitive=False):
    return project_model.serialize_project(
        project,
        include_sensitive=include_sensitive,
        parse_target_system=parse_target_system_config,
        parse_plan_generation=parse_plan_generation_config,
    )


def seed_auth_defaults(cursor, config):
    auth = get_auth_config()
    now_ms = current_time_ms()
    permissions_table = platform_table_sql(config, "platform_permissions")
    roles_table = platform_table_sql(config, "platform_roles")
    users_table = platform_table_sql(config, "platform_users")
    role_permissions_table = platform_table_sql(config, "platform_role_permissions")
    user_roles_table = platform_table_sql(config, "platform_user_roles")

    for permission in AUTH_MENU_PERMISSIONS:
        cursor.execute(
            f"""
            INSERT INTO {permissions_table} (code, name, permission_type, sort_order, created_at, updated_at)
            VALUES (%s, %s, 'menu', %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              name = VALUES(name),
              permission_type = VALUES(permission_type),
              sort_order = VALUES(sort_order),
              updated_at = VALUES(updated_at)
            """,
            (permission["code"], permission["name"], permission["sort_order"], now_ms, now_ms),
        )

    cursor.execute(
        f"""
        INSERT INTO {roles_table} (code, name, description, status, is_system, created_at, updated_at)
        VALUES ('admin', '管理员', '系统内置管理员角色，拥有全部菜单权限。', 'active', 1, %s, %s)
        ON DUPLICATE KEY UPDATE
          name = VALUES(name),
          description = VALUES(description),
          is_system = VALUES(is_system),
          updated_at = VALUES(updated_at)
        """,
        (now_ms, now_ms),
    )
    cursor.execute(f"SELECT id FROM {roles_table} WHERE code = 'admin'")
    admin_role = cursor.fetchone()
    admin_role_id = admin_role["id"] if admin_role else None

    if admin_role_id:
        for permission in AUTH_MENU_PERMISSIONS:
            cursor.execute(
                f"""
                INSERT IGNORE INTO {role_permissions_table} (role_id, permission_code, created_at)
                VALUES (%s, %s, %s)
                """,
                (admin_role_id, permission["code"], now_ms),
            )

    cursor.execute(f"SELECT COUNT(*) AS total FROM {users_table}")
    user_count = int((cursor.fetchone() or {}).get("total") or 0)
    if auth.get("enabled") and user_count == 0:
        password_hash = generate_password_hash(auth["initial_admin_password"])
        cursor.execute(
            f"""
            INSERT INTO {users_table}
              (username, password_hash, display_name, status, last_login_at, created_at, updated_at)
            VALUES (%s, %s, '管理员', 'active', NULL, %s, %s)
            """,
            (auth["initial_admin_username"], password_hash, now_ms, now_ms),
        )
        admin_user_id = cursor.lastrowid
        if admin_role_id:
            cursor.execute(
                f"""
                INSERT IGNORE INTO {user_roles_table} (user_id, role_id, created_at)
                VALUES (%s, %s, %s)
                """,
                (admin_user_id, admin_role_id, now_ms),
            )


def is_platform_database_enabled():
    try:
        return bool(get_platform_database_config().get("enabled"))
    except RuntimeError:
        return False


def seed_platform_projects(cursor, config):
    return project_repository.seed_platform_projects(
        cursor,
        config,
        _project_repository_dependencies(),
    )


def get_default_project_id_from_cursor(cursor, config):
    return project_repository.get_default_project_id_from_cursor(
        cursor,
        config,
        _project_repository_dependencies(),
    )


def sanitize_suite_uid(value=None):
    raw_value = str(value or "").strip()
    if raw_value and PROJECT_KEY_PATTERN.match(raw_value):
        return raw_value
    return f"suite-{uuid.uuid4().hex}"


def strip_spec_suffix(filename):
    text = str(filename or "")
    return text[: -len(".spec.ts")] if text.endswith(".spec.ts") else Path(text).stem


def migrate_legacy_test_suites(cursor, config, default_project_id):
    records_table = platform_table_sql(config, "platform_records")
    suites_table = get_test_suites_table(config)
    suite_items_table = get_test_suite_items_table(config)
    cursor.execute(
        f"""
        SELECT record_json
        FROM {records_table}
        WHERE project_id = %s AND bucket = 'test_suites' AND record_key = 'default'
        LIMIT 1
        """,
        (default_project_id,),
    )
    row = cursor.fetchone()
    if not row:
        return
    try:
        record = json.loads(row.get("record_json") or "{}")
    except json.JSONDecodeError:
        return
    suites = record.get("suites")
    if not isinstance(suites, list):
        return

    now_ms = current_time_ms()
    for raw_suite in suites:
        if not isinstance(raw_suite, dict):
            continue
        suite_name = str(raw_suite.get("name") or "").strip()
        if not suite_name:
            continue
        suite_uid = sanitize_suite_uid(raw_suite.get("id"))
        created_at = int(raw_suite.get("created_at") or now_ms)
        cursor.execute(
            f"""
            INSERT INTO {suites_table}
              (project_id, suite_uid, name, description, status, created_by, updated_by,
               created_at, updated_at, deleted_at)
            VALUES (%s, %s, %s, '', 'active', NULL, NULL, %s, %s, NULL)
            ON DUPLICATE KEY UPDATE
              name = VALUES(name),
              updated_at = VALUES(updated_at),
              deleted_at = NULL,
              status = 'active'
            """,
            (default_project_id, suite_uid, suite_name, created_at, now_ms),
        )
        cursor.execute(
            f"SELECT suite_id FROM {suites_table} WHERE project_id = %s AND suite_uid = %s",
            (default_project_id, suite_uid),
        )
        suite_row = cursor.fetchone()
        if not suite_row:
            continue
        suite_id = suite_row["suite_id"]
        for index, raw_item in enumerate(raw_suite.get("items") or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            module_name = str(raw_item.get("module_name") or "").strip()
            filename = str(raw_item.get("filename") or "").strip()
            if not module_name or not filename:
                continue
            display_name = str(raw_item.get("display_name") or "").strip() or strip_spec_suffix(filename)
            script_path = str(raw_item.get("path") or "").strip() or None
            cursor.execute(
                f"""
                INSERT INTO {suite_items_table}
                  (project_id, suite_id, script_asset_id, module_name, filename, display_name,
                   script_path, sort_order, created_at, updated_at)
                VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  display_name = VALUES(display_name),
                  script_path = VALUES(script_path),
                  sort_order = VALUES(sort_order),
                  updated_at = VALUES(updated_at)
                """,
                (
                    default_project_id,
                    suite_id,
                    module_name,
                    filename,
                    display_name[:255],
                    script_path,
                    index,
                    now_ms,
                    now_ms,
                ),
            )


def serialize_project_row(row):
    return project_model.serialize_project_row(
        row,
        parse_target_system=parse_target_system_config,
        parse_plan_generation=parse_plan_generation_config,
    )


def list_projects_from_mysql():
    return project_repository.list_projects(
        _project_repository_dependencies()
    )


def get_project_workspace_root_text():
    return project_workspace.get_project_workspace_root_text(
        _project_workspace_dependencies()
    )


def get_project_template_dependency_source_text():
    return project_workspace.get_project_template_dependency_source_text(
        _project_workspace_dependencies()
    )


def get_project_workspace_root_for_create():
    return project_workspace.get_project_workspace_root_for_create(
        _project_workspace_dependencies()
    )


def get_project_dependency_source_root_for_create():
    return project_workspace.get_project_dependency_source_root_for_create(
        _project_workspace_dependencies()
    )


def get_created_project_root(workspace_root, project_key):
    return project_workspace.get_created_project_root(
        workspace_root,
        project_key,
    )


def template_relative_target_path(relative_path, specs_dir, tests_dir):
    return project_workspace.template_relative_target_path(
        relative_path,
        specs_dir,
        tests_dir,
    )


def npm_package_name_from_project_key(project_key):
    return project_workspace.npm_package_name_from_project_key(
        project_key
    )


def render_project_template_text(text, project_key, name, specs_dir, tests_dir):
    return project_workspace.render_project_template_text(
        text,
        project_key,
        name,
        specs_dir,
        tests_dir,
        npm_package_name=npm_package_name_from_project_key,
    )


def copy_project_template_files(project_root, project_key, name, specs_dir, tests_dir):
    return project_workspace.copy_project_template_files(
        project_root,
        project_key,
        name,
        specs_dir,
        tests_dir,
        _project_workspace_dependencies(),
    )


def copy_project_template_dependencies(source_root, project_root):
    return project_workspace.copy_project_template_dependencies(
        source_root,
        project_root,
        _project_workspace_dependencies(),
    )


def run_project_git_command(project_root, args):
    return project_workspace.run_project_git_command(
        project_root,
        args,
        _project_workspace_dependencies(),
    )


def initialize_created_project_git_repo(project_root):
    return project_workspace.initialize_created_project_git_repo(
        project_root,
        _project_workspace_dependencies(),
    )


def initialize_created_project_directory(project_root, project_key, name, specs_dir, tests_dir):
    return project_workspace.initialize_created_project_directory(
        project_root,
        project_key,
        name,
        specs_dir,
        tests_dir,
        _project_workspace_dependencies(),
    )


def assert_project_key_available(config, project_key):
    return project_repository.assert_project_key_available(
        config,
        project_key,
        _project_repository_dependencies(),
    )


def create_project_in_mysql(payload):
    return _project_service().create_project(payload)


def update_project_in_mysql(project_key, payload):
    return _project_service().update_project(project_key, payload)


def delete_project_in_mysql(project_key, confirmation_name, current_project_key):
    return _project_service().delete_project(project_key, confirmation_name, current_project_key)


def get_project_by_key(project_key=None):
    return project_repository.get_project_by_key(
        project_key,
        _project_repository_dependencies(),
    )


def get_requested_project_key():
    if not has_request_context():
        return ""
    return (
        request.headers.get("X-Project-Key")
        or request.args.get("project_key")
        or session.get("project_key")
        or ""
    ).strip()


def get_current_project():
    project = _project_service().resolve_current_project(
        get_requested_project_key()
    )
    if has_request_context() and project.get("project_key"):
        session["project_key"] = project["project_key"]
    return project


def get_current_project_language():
    """Return the locale of the request or the agent's captured project."""

    project = current_context_project()
    if not project:
        try:
            project = get_current_project()
        except RuntimeError:
            return "en"
    return normalize_project_language(project.get("language"))


def project_copy(english, chinese):
    """Return first-party UI copy in the captured project's language."""
    return agent_localization.select(agent_project_language(), english, chinese)


def agent_project_language():
    context_project = current_context_project()
    if context_project:
        return normalize_project_language(context_project.get("language"))
    if not has_request_context():
        # Pure helpers and legacy callers have no project identity to resolve.
        # Agent workers bind their project explicitly before generating copy.
        return "zh-CN"
    try:
        return get_current_project_language()
    except Exception:
        return "zh-CN"


def agent_message(key, **values):
    return agent_localization.message(agent_project_language(), key, **values)


@app.after_request
def localize_first_party_api_errors(response):
    """Localize recognized platform API errors without touching third-party logs."""

    if (
        not request.path.startswith("/api/")
        or response.is_streamed
        or not response.is_json
        # Project-scoped API calls from the application always carry this
        # header.  Keeping the response hook off legacy/headerless calls
        # preserves their compatibility and avoids resolving a project merely
        # to format an error response.
        or not request.headers.get("X-Project-Key")
    ):
        return response
    payload = response.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
        return response
    try:
        language = get_current_project_language()
    except Exception:
        language = "en"
    translated = localize_platform_error(payload["error"], language)
    if translated == payload["error"]:
        return response
    payload["error"] = translated
    response.set_data(json.dumps(payload, ensure_ascii=False))
    response.headers["Content-Length"] = str(len(response.get_data()))
    return response


def update_current_project_settings_in_mysql(target_system, database_baseline, plan_generation):
    updated_project = _project_service().update_current_project_settings(
        target_system,
        database_baseline,
        plan_generation,
    )
    if has_request_context():
        session["project_key"] = updated_project["project_key"]
    return updated_project


def update_current_project_language_in_mysql(language):
    updated_project = _project_service().update_current_project_language(
        language
    )
    if has_request_context():
        session["project_key"] = updated_project["project_key"]
    return updated_project


def get_current_project_id():
    return _project_service().get_current_project_id()


def current_platform_author():
    request_author = session.get("username") if has_request_context() else None
    return current_context_author(request_author or "platform")


def project_relative_path(path):
    return get_project_relative_path(path).as_posix()


def sha256_file(path):
    content = read_file_bytes(Path(path))
    return sha256_bytes(content) if content is not None else ""


def get_job_log_dir():
    job_dir = get_project_root() / DATABASE_BASELINE_HELPER_DIR_NAME / JOB_LOG_STORAGE_DIR_NAME
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def sanitize_job_id(job_id):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(job_id or "").strip()).strip(".-")
    return value or uuid.uuid4().hex


def get_job_log_path(job_id):
    safe_job_id = sanitize_job_id(job_id)
    return get_job_log_dir() / f"{safe_job_id}.log"


def read_file_tail(path, limit=JOB_LOG_TAIL_LIMIT):
    try:
        with Path(path).open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - limit))
            data = file.read()
    except OSError:
        return "", 0
    return decode_process_output(data), size


def append_job_log_file(job_id, text, *, writer=None):
    if not job_id or not text:
        return "", 0, ""

    log_path = get_job_log_path(job_id)
    if writer is not None:
        snapshot = writer.append(text)
    else:
        with BufferedJobLogWriter(log_path, tail_bytes=JOB_LOG_TAIL_LIMIT) as owned_writer:
            snapshot = owned_writer.append(text)
    return snapshot.tail, snapshot.size, snapshot.path


def safe_git_commit_message(message):
    text = str(message or "").strip()
    return text or "chore: update test asset"


def run_git_command(args, check=True):
    project_root = get_project_root()
    completed = subprocess.run(
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
    if check and completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Git 命令失败：git {' '.join(args)}\n{stderr}")
    return completed


def has_git_head():
    return run_git_command(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0


def get_git_head_sha():
    completed = run_git_command(["rev-parse", "HEAD"], check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def ensure_playwright_asset_git_repo():
    project_root = get_project_root()
    project_root.mkdir(parents=True, exist_ok=True)
    if not (project_root / ".git").exists():
        run_git_command(["init"])

    gitignore_path = project_root / ".gitignore"
    required_entries = [
        "node_modules/",
        "test-results/",
        "playwright-report/",
        ".test-plan-viewer/",
        "*.log",
    ]
    existing = ""
    if gitignore_path.exists():
        try:
            existing = gitignore_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            existing = gitignore_path.read_text(encoding="utf-8", errors="replace")
    lines = existing.splitlines()
    normalized = {line.strip() for line in lines}
    changed = False
    for entry in required_entries:
        if entry not in normalized:
            lines.append(entry)
            changed = True
    if changed:
        gitignore_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="")
        run_git_command(["add", ".gitignore"])
        if run_git_command(["diff", "--cached", "--quiet", "--", ".gitignore"], check=False).returncode != 0:
            run_git_command(
                [
                    "-c",
                    "user.name=Test Plan Viewer",
                    "-c",
                    "user.email=test-plan-viewer@local",
                    "commit",
                    "-m",
                    "chore: configure test asset ignores",
                ]
            )


def ensure_git_commit_for_path(path, message):
    ensure_playwright_asset_git_repo()
    relative_path = project_relative_path(path)
    run_git_command(["add", "--", relative_path])
    staged = run_git_command(["diff", "--cached", "--quiet", "--", relative_path], check=False).returncode != 0
    if staged or not has_git_head():
        run_git_command(
            [
                "-c",
                "user.name=Test Plan Viewer",
                "-c",
                "user.email=test-plan-viewer@local",
                "commit",
                "-m",
                safe_git_commit_message(message),
            ]
        )
    return get_git_head_sha()


def ensure_git_commit_for_removed_path(path, message):
    ensure_playwright_asset_git_repo()
    relative_path = project_relative_path(path)
    completed = run_git_command(["add", "-u", "--", relative_path], check=False)
    if completed.returncode != 0:
        return get_git_head_sha()
    staged = run_git_command(["diff", "--cached", "--quiet", "--", relative_path], check=False).returncode != 0
    if staged:
        run_git_command(
            [
                "-c",
                "user.name=Test Plan Viewer",
                "-c",
                "user.email=test-plan-viewer@local",
                "commit",
                "-m",
                safe_git_commit_message(message),
            ]
        )
    return get_git_head_sha()


def git_show_file(commit_sha, file_path):
    relative_path = project_relative_path(file_path)
    completed = run_git_command(["show", f"{commit_sha}:{relative_path}"], check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "读取 Git 版本内容失败。").strip())
    return completed.stdout


def git_diff_file(commit_sha, file_path):
    relative_path = project_relative_path(file_path)
    completed = run_git_command(["diff", commit_sha, "--", relative_path], check=False)
    if completed.returncode not in {0, 1}:
        raise RuntimeError((completed.stderr or completed.stdout or "读取 Git diff 失败。").strip())
    return completed.stdout


def normalize_asset_path(path):
    resolved = Path(path).expanduser().resolve(strict=False)
    project_root = get_project_root().resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("测试资产路径必须位于 Playwright 项目目录内。") from exc
    return str(resolved)


def infer_plan_asset_for_script(module_name, filename):
    plan_filename = f"{Path(filename).stem}.md"
    plan_file = get_plan_file(module_name, plan_filename)
    if not plan_file.exists():
        default_plan = get_plan_file(module_name, get_default_plan_filename(module_name))
        plan_file = default_plan if default_plan.exists() else None
    if not plan_file:
        return None
    return sync_plan_asset(module_name, plan_file, change_source="manual", message=f"sync plan: {module_name}/{plan_file.name}")


def upsert_test_asset(asset_type, module_name, title, file_path, from_plan_asset_id=None, source_job_id=None):
    if asset_type not in TEST_ASSET_TYPES:
        raise ValueError("Unsupported asset type.")
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None

    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    project_id = get_current_project_id()
    current_path = normalize_asset_path(file_path)
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {assets_table}
                WHERE project_id = %s AND asset_type = %s AND current_path = %s AND deleted_at IS NULL
                ORDER BY asset_id DESC
                LIMIT 1
                """,
                (project_id, asset_type, current_path),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    f"""
                    UPDATE {assets_table}
                    SET module_name = %s,
                        title = %s,
                        from_plan_asset_id = %s,
                        source_job_id = COALESCE(%s, source_job_id),
                        status = 'active',
                        updated_at = %s
                    WHERE asset_id = %s
                    """,
                    (module_name, title, from_plan_asset_id, source_job_id, now_ms, row["asset_id"]),
                )
                asset_id = row["asset_id"]
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {assets_table}
                      (project_id, asset_type, module_name, title, current_path, current_revision_id, from_plan_asset_id,
                       source_job_id, status, created_at, updated_at, deleted_at)
                    VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, 'active', %s, %s, NULL)
                    """,
                    (project_id, asset_type, module_name, title, current_path, from_plan_asset_id, source_job_id, now_ms, now_ms),
                )
                asset_id = cursor.lastrowid
            connection.commit()
            cursor.execute(f"SELECT * FROM {assets_table} WHERE asset_id = %s", (asset_id,))
            return cursor.fetchone()


def get_test_asset_by_id(asset_id):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {assets_table} WHERE project_id = %s AND asset_id = %s",
                (project_id, asset_id),
            )
            return cursor.fetchone()


def get_test_asset_by_path(asset_type, file_path):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    project_id = get_current_project_id()
    current_path = normalize_asset_path(file_path)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {assets_table}
                WHERE project_id = %s AND asset_type = %s AND current_path = %s AND deleted_at IS NULL
                ORDER BY asset_id DESC
                LIMIT 1
                """,
                (project_id, asset_type, current_path),
            )
            return cursor.fetchone()


def create_asset_revision(asset, file_path, change_source, source_job_id=None, message=None):
    if not Path(file_path).exists():
        return None

    commit_sha = ensure_git_commit_for_path(file_path, message)
    content_sha = sha256_file(file_path)
    if not asset:
        return None

    config = get_platform_database_config()
    if not config.get("enabled"):
        return None

    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    revisions_table = get_test_asset_revisions_table(config)
    file_path_value = normalize_asset_path(file_path)
    now_ms = current_time_ms()

    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {revisions_table}
                WHERE asset_id = %s
                ORDER BY version_no DESC
                LIMIT 1
                """,
                (asset["asset_id"],),
            )
            latest = cursor.fetchone()
            if latest and latest.get("content_sha256") == content_sha and latest.get("file_path") == file_path_value:
                cursor.execute(
                    f"""
                    UPDATE {assets_table}
                    SET current_revision_id = %s, updated_at = %s
                    WHERE asset_id = %s
                    """,
                    (latest["revision_id"], now_ms, asset["asset_id"]),
                )
                connection.commit()
                return latest

            version_no = int(latest["version_no"]) + 1 if latest else 1
            cursor.execute(
                f"""
                INSERT INTO {revisions_table}
                  (asset_id, version_no, file_path, git_commit_sha, content_sha256, change_source,
                   source_job_id, author, message, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    asset["asset_id"],
                    version_no,
                    file_path_value,
                    commit_sha,
                    content_sha,
                    change_source,
                    source_job_id,
                    current_platform_author(),
                    message,
                    now_ms,
                ),
            )
            revision_id = cursor.lastrowid
            cursor.execute(
                f"""
                UPDATE {assets_table}
                SET current_revision_id = %s, updated_at = %s
                WHERE asset_id = %s
                """,
                (revision_id, now_ms, asset["asset_id"]),
            )
            connection.commit()
            cursor.execute(f"SELECT * FROM {revisions_table} WHERE revision_id = %s", (revision_id,))
            return cursor.fetchone()


def sync_plan_asset(module_name, plan_file, change_source="manual", source_job_id=None, message=None):
    asset = upsert_test_asset("plan", module_name, plan_file.stem, plan_file, source_job_id=source_job_id)
    if Path(plan_file).exists():
        create_asset_revision(asset, plan_file, change_source, source_job_id=source_job_id, message=message)
    return get_test_asset_by_path("plan", plan_file) or asset


def sync_script_asset(module_name, script_file, change_source="manual", source_job_id=None, from_plan_asset_id=None, message=None):
    if from_plan_asset_id is None:
        plan_asset = infer_plan_asset_for_script(module_name, Path(script_file).name)
        from_plan_asset_id = plan_asset.get("asset_id") if plan_asset else None
    title = Path(script_file).name[: -len(".spec.ts")] if Path(script_file).name.endswith(".spec.ts") else Path(script_file).stem
    asset = upsert_test_asset(
        "script",
        module_name,
        title,
        script_file,
        from_plan_asset_id=from_plan_asset_id,
        source_job_id=source_job_id,
    )
    if Path(script_file).exists():
        create_asset_revision(asset, script_file, change_source, source_job_id=source_job_id, message=message)
    return get_test_asset_by_path("script", script_file) or asset


def mark_test_asset_deleted(asset):
    if not asset:
        return None

    config = get_platform_database_config()
    if not config.get("enabled"):
        return None

    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {assets_table}
                SET status = 'deleted', deleted_at = %s, updated_at = %s
                WHERE project_id = %s AND asset_id = %s
                """,
                (now_ms, now_ms, project_id, asset["asset_id"]),
            )
            connection.commit()
            cursor.execute(f"SELECT * FROM {assets_table} WHERE project_id = %s AND asset_id = %s", (project_id, asset["asset_id"]))
            return cursor.fetchone()


def get_manual_trash_relative_path(source_file, asset_type, module_name):
    try:
        return get_project_relative_path(source_file)
    except ValueError:
        return Path(asset_type) / validate_module_name(module_name) / Path(source_file).name


def archive_manual_asset_file(source_file, asset_type, module_name, reason):
    if asset_type not in TEST_ASSET_TYPES:
        raise ValueError("Unsupported asset type.")

    source_file = Path(source_file)
    if not source_file.exists():
        return {"archived": False, "path": str(source_file), "reason": "文件不存在"}

    asset = get_test_asset_by_path(asset_type, source_file)
    project_root = get_project_root()
    batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    trash_root = project_root / DATABASE_BASELINE_HELPER_DIR_NAME / "manual-trash" / batch_id
    relative_path = get_manual_trash_relative_path(source_file, asset_type, module_name)
    trash_file = trash_root / relative_path

    try:
        trash_file.resolve(strict=False).relative_to(trash_root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("Resolved trash path is outside manual trash directory.") from exc

    trash_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_file), str(trash_file))

    git_commit_sha = ""
    git_error = ""
    try:
        git_commit_sha = ensure_git_commit_for_removed_path(source_file, reason)
    except Exception as exc:
        git_error = str(exc)

    deleted_asset = mark_test_asset_deleted(asset)
    return {
        "archived": True,
        "from": str(source_file),
        "to": str(trash_file),
        "relative_path": relative_path.as_posix(),
        "trash_relative_path": project_relative_path(trash_file),
        "git_commit_sha": git_commit_sha,
        "git_error": git_error,
        "asset": deleted_asset,
        "reason": reason,
    }


def delete_plan_asset(module_name, plan_filename, message=None):
    plan_file = get_plan_file(module_name, plan_filename)
    if not plan_file.exists():
        raise FileNotFoundError(f"Markdown file not found: {plan_file}")

    archive_info = archive_manual_asset_file(
        plan_file,
        "plan",
        module_name,
        message or f"manual delete plan: {module_name}/{plan_filename}",
    )
    deleted_asset = archive_info.pop("asset", None)
    return {
        "ok": True,
        "module": module_name,
        "plan_filename": plan_filename,
        "archive": archive_info,
        "asset": deleted_asset,
        "error": None,
    }


def delete_script_asset(module_name, filename, message=None):
    script_file = get_script_file(module_name, filename)
    if not script_file.exists():
        raise FileNotFoundError(f"Script file not found: {script_file}")

    archive_info = archive_manual_asset_file(
        script_file,
        "script",
        module_name,
        message or f"manual delete script: {module_name}/{filename}",
    )
    deleted_asset = archive_info.pop("asset", None)
    return {
        "ok": True,
        "module": module_name,
        "filename": filename,
        "archive": archive_info,
        "asset": deleted_asset,
        "error": None,
    }


def list_asset_revisions(asset_id, limit=20):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return []
    ensure_platform_database_schema(config)
    revisions_table = get_test_asset_revisions_table(config)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {revisions_table}
                WHERE asset_id = %s
                ORDER BY version_no DESC
                LIMIT %s
                """,
                (asset_id, int(limit)),
            )
            return cursor.fetchall()


def get_asset_revision(asset_id, revision_id):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    revisions_table = get_test_asset_revisions_table(config)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {revisions_table} WHERE asset_id = %s AND revision_id = %s",
                (asset_id, revision_id),
            )
            return cursor.fetchone()


def serialize_asset(asset):
    if not asset:
        return None
    return {
        "asset_id": asset.get("asset_id"),
        "project_id": asset.get("project_id"),
        "asset_type": asset.get("asset_type"),
        "module_name": asset.get("module_name"),
        "title": asset.get("title"),
        "current_path": asset.get("current_path"),
        "current_revision_id": asset.get("current_revision_id"),
        "from_plan_asset_id": asset.get("from_plan_asset_id"),
        "source_job_id": asset.get("source_job_id"),
        "status": asset.get("status"),
        "created_at": asset.get("created_at"),
        "updated_at": asset.get("updated_at"),
    }


def serialize_related_script(asset):
    payload = serialize_asset(asset) or {}
    payload["last_status"] = asset.get("last_status")
    payload["last_run_at"] = asset.get("last_run_at")
    return payload


def serialize_revision(revision):
    if not revision:
        return None
    return {
        "revision_id": revision.get("revision_id"),
        "asset_id": revision.get("asset_id"),
        "version_no": revision.get("version_no"),
        "file_path": revision.get("file_path"),
        "git_commit_sha": revision.get("git_commit_sha"),
        "content_sha256": revision.get("content_sha256"),
        "change_source": revision.get("change_source"),
        "source_job_id": revision.get("source_job_id"),
        "author": revision.get("author"),
        "message": revision.get("message"),
        "created_at": revision.get("created_at"),
    }


def create_test_job(
    job_type,
    job_id=None,
    status="queued",
    target_asset_id=None,
    source_asset_id=None,
    prompt=None,
    coverage_profile=DEFAULT_COVERAGE_PROFILE,
    prompt_customized=False,
    prompt_context=None,
):
    if job_type not in TEST_JOB_TYPES:
        raise ValueError("Unsupported job type.")
    job_id = sanitize_job_id(job_id or f"{job_type}-{uuid.uuid4().hex}")
    log_path = get_job_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")
    config = get_platform_database_config()
    if not config.get("enabled"):
        return {"job_id": job_id, "job_type": job_type, "status": status, "log_path": str(log_path)}

    ensure_platform_database_schema(config)
    jobs_table = get_test_jobs_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    started_at = now_ms if status == "running" else None
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {jobs_table}
                  (job_id, project_id, job_type, status, target_asset_id, source_asset_id, prompt,
                   coverage_profile, prompt_customized, prompt_context_json, cancel_requested,
                   opencode_session_id, log_path, log_tail,
                   log_size, error, started_at, finished_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, NULL, %s, '', 0, NULL, %s, NULL, %s, %s)
                ON DUPLICATE KEY UPDATE
                  project_id = VALUES(project_id),
                  job_type = VALUES(job_type),
                  status = VALUES(status),
                  target_asset_id = VALUES(target_asset_id),
                  source_asset_id = VALUES(source_asset_id),
                  prompt = VALUES(prompt),
                  coverage_profile = VALUES(coverage_profile),
                  prompt_customized = VALUES(prompt_customized),
                  prompt_context_json = VALUES(prompt_context_json),
                  cancel_requested = 0,
                  opencode_session_id = NULL,
                  log_path = VALUES(log_path),
                  started_at = COALESCE(started_at, VALUES(started_at)),
                  updated_at = VALUES(updated_at)
                """,
                (
                    job_id,
                    project_id,
                    job_type,
                    status,
                    target_asset_id,
                    source_asset_id,
                    prompt,
                    validate_coverage_profile(coverage_profile),
                    int(bool(prompt_customized)),
                    compact_json_dumps(prompt_context or {}),
                    str(log_path),
                    started_at,
                    now_ms,
                    now_ms,
                ),
            )
        connection.commit()
    register_job_artifact(job_id, "log", log_path)
    return get_test_job(job_id) or {"job_id": job_id, "job_type": job_type, "status": status, "log_path": str(log_path)}


def update_test_job(job_id, *, fetch=True, **updates):
    config = get_platform_database_config()
    if not config.get("enabled") or not job_id:
        return None
    ensure_platform_database_schema(config)
    jobs_table = get_test_jobs_table(config)
    project_id = get_current_project_id()
    allowed = {
        "status",
        "target_asset_id",
        "source_asset_id",
        "prompt",
        "coverage_profile",
        "prompt_customized",
        "prompt_context_json",
        "cancel_requested",
        "opencode_session_id",
        "log_path",
        "log_tail",
        "log_size",
        "error",
        "started_at",
        "finished_at",
    }
    fields = []
    values = []
    for key, value in updates.items():
        if key in allowed:
            fields.append(f"{key} = %s")
            values.append(value)
    if not fields:
        return get_test_job(job_id) if fetch else None
    fields.append("updated_at = %s")
    values.append(current_time_ms())
    values.extend([project_id, job_id])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {jobs_table} SET {', '.join(fields)} WHERE project_id = %s AND job_id = %s", values)
        connection.commit()
    return get_test_job(job_id) if fetch else None


def persist_test_job_log_snapshot(job_id, snapshot, *, fetch=False):
    if not job_id or not snapshot or not is_platform_database_enabled():
        return None
    if isinstance(snapshot, JobLogSnapshot):
        updates = snapshot.as_updates()
    else:
        updates = {
            "log_path": snapshot.get("log_path") or snapshot.get("path") or "",
            "log_tail": snapshot.get("log_tail") or snapshot.get("tail") or "",
            "log_size": int(snapshot.get("log_size") or snapshot.get("size") or 0),
        }
    return update_test_job(job_id, fetch=fetch, **updates)


def append_test_job_log(
    job_id,
    text,
    *,
    writer=None,
    persist_snapshot=True,
    force_snapshot=False,
):
    if not job_id or not text:
        return ""
    tail, size, log_path = append_job_log_file(job_id, text, writer=writer)
    if not is_platform_database_enabled() or not persist_snapshot:
        return tail

    should_persist = writer is None or writer.snapshot_due(force=force_snapshot)
    if should_persist:
        snapshot = JobLogSnapshot(
            path=log_path,
            tail=tail,
            size=size,
            captured_at=time.monotonic(),
        )
        persist_test_job_log_snapshot(job_id, snapshot)
        if writer is not None:
            writer.mark_snapshot_persisted(snapshot)
    return tail


def finish_test_job(job_id, status, error=None, target_asset_id=None, *, log_writer=None):
    if status not in TEST_JOB_STATUSES:
        status = "failed"
    updates = {"status": status, "finished_at": current_time_ms(), "error": error}
    if target_asset_id:
        updates["target_asset_id"] = target_asset_id
    snapshot = log_writer.snapshot() if log_writer is not None else None
    if snapshot is not None:
        updates.update(snapshot.as_updates())
    result = update_test_job(job_id, fetch=False, **updates)
    if log_writer is not None and snapshot is not None:
        log_writer.mark_snapshot_persisted(snapshot)
    return result


def get_test_job(job_id):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    jobs_table = get_test_jobs_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {jobs_table} WHERE project_id = %s AND job_id = %s", (project_id, job_id))
            return cursor.fetchone()


def serialize_job(job):
    if not job:
        return None
    return {
        "job_id": job.get("job_id"),
        "project_id": job.get("project_id"),
        "job_type": job.get("job_type"),
        "status": job.get("status"),
        "target_asset_id": job.get("target_asset_id"),
        "source_asset_id": job.get("source_asset_id"),
        "prompt": job.get("prompt"),
        "coverage_profile": job.get("coverage_profile") or DEFAULT_COVERAGE_PROFILE,
        "prompt_customized": bool(job.get("prompt_customized")),
        "prompt_context": load_json_column(job.get("prompt_context_json"), {}),
        "cancel_requested": bool(job.get("cancel_requested")),
        "opencode_session_id": job.get("opencode_session_id") or "",
        "log_path": job.get("log_path"),
        "log_tail": job.get("log_tail") or "",
        "log_size": job.get("log_size") or 0,
        "error": job.get("error"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "log_url": f"/api/jobs/{urlparse.quote(str(job.get('job_id')))}/log",
        "log_download_url": f"/api/jobs/{urlparse.quote(str(job.get('job_id')))}/log/download",
    }


def register_job_artifact(job_id, artifact_type, path, url=None):
    if not job_id or not path or not Path(path).exists() or not is_platform_database_enabled():
        return None
    config = get_platform_database_config()
    ensure_platform_database_schema(config)
    artifacts_table = get_job_artifacts_table(config)
    project_id = get_current_project_id()
    path = Path(path)
    size = path.stat().st_size if path.is_file() else None
    file_sha = sha256_file(path) if path.is_file() else None
    relative_path = ""
    try:
        relative_path = project_relative_path(path)
    except ValueError:
        pass
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {artifacts_table}
                  (project_id, job_id, artifact_type, path, relative_path, url, size, sha256, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (project_id, job_id, artifact_type, str(path), relative_path, url, size, file_sha, now_ms),
            )
            connection.commit()
            return cursor.lastrowid


def list_job_artifacts(job_id):
    if not job_id or not is_platform_database_enabled():
        return []
    config = get_platform_database_config()
    ensure_platform_database_schema(config)
    table = get_job_artifacts_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE project_id = %s AND job_id = %s ORDER BY artifact_id ASC",
                (project_id, str(job_id)),
            )
            return cursor.fetchall()


def _execution_result_dependencies():
    return execution_results.ResultDependencies(
        get_project_root=lambda: get_project_root(),
        get_script_test_relative_path=lambda module_name, filename: (
            get_script_test_relative_path(module_name, filename)
        ),
        resolve_path=lambda path: Path(path).resolve(strict=False),
        read_text=lambda path: Path(path).read_text(encoding="utf-8"),
    )


def db_execution_mode(value):
    return execution_results.db_execution_mode(value)


def execution_database_reset_mode(value):
    return execution_results.execution_database_reset_mode(value)


def db_run_status(status):
    return execution_results.db_run_status(status)


def db_result_status(status):
    return execution_results.db_result_status(status)


def is_completed_script_result_status(status):
    return execution_results.is_completed_script_result_status(status)


def finalize_script_results_after_error(
    keys,
    script_results,
    unresolved_status="failed",
):
    return execution_results.finalize_script_results_after_error(
        keys,
        script_results,
        unresolved_status=unresolved_status,
    )


def get_asset_current_revision(asset):
    if not asset or not asset.get("current_revision_id"):
        return None
    return get_asset_revision(asset["asset_id"], asset["current_revision_id"])


def get_plan_asset_for_script_asset(script_asset):
    if not script_asset or not script_asset.get("from_plan_asset_id"):
        return None
    return get_test_asset_by_id(script_asset["from_plan_asset_id"])


def create_test_run(
    run_id,
    run_type,
    execution_mode,
    module_name=None,
    suite_id=None,
    target_asset_id=None,
    command=None,
    env=None,
    total_files=0,
):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    runs_table = get_test_runs_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    env_json = json.dumps(env or {}, ensure_ascii=False, separators=(",", ":")) if env is not None else None
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {runs_table}
                  (run_id, project_id, run_type, status, execution_mode, database_reset_mode, triggered_by, trigger_source,
                   suite_id, module_name, target_asset_id, command, git_commit_sha, env_json, summary_json,
                   total_files, completed_files, error, started_at, finished_at, created_at, updated_at)
                VALUES (%s, %s, %s, 'running', %s, %s, %s, 'platform', %s, %s, %s, %s, %s, %s, NULL,
                        %s, 0, NULL, %s, NULL, %s, %s)
                ON DUPLICATE KEY UPDATE
                  project_id = VALUES(project_id),
                  status = VALUES(status),
                  execution_mode = VALUES(execution_mode),
                  database_reset_mode = VALUES(database_reset_mode),
                  command = VALUES(command),
                  env_json = VALUES(env_json),
                  total_files = VALUES(total_files),
                  updated_at = VALUES(updated_at)
                """,
                (
                    run_id,
                    project_id,
                    run_type,
                    db_execution_mode(execution_mode),
                    execution_database_reset_mode(execution_mode),
                    current_platform_author(),
                    suite_id,
                    module_name,
                    target_asset_id,
                    command,
                    get_git_head_sha(),
                    env_json,
                    int(total_files or 0),
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
        connection.commit()
    return get_test_run(run_id)


def get_test_run(run_id):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    runs_table = get_test_runs_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {runs_table} WHERE project_id = %s AND run_id = %s", (project_id, run_id))
            return cursor.fetchone()


def update_test_run(run_id, status=None, summary=None, completed_files=None, error=None, finished=False):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    runs_table = get_test_runs_table(config)
    project_id = get_current_project_id()
    fields = ["updated_at = %s"]
    values = [current_time_ms()]
    if status is not None:
        fields.append("status = %s")
        values.append(db_run_status(status))
    if summary is not None:
        fields.append("summary_json = %s")
        values.append(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if completed_files is not None:
        fields.append("completed_files = %s")
        values.append(int(completed_files))
    if error is not None:
        fields.append("error = %s")
        values.append(error)
    if finished:
        fields.append("finished_at = %s")
        values.append(current_time_ms())
    values.extend([project_id, run_id])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {runs_table} SET {', '.join(fields)} WHERE project_id = %s AND run_id = %s", values)
        connection.commit()
    return get_test_run(run_id)


def build_execution_summary(script_results, returncode=None):
    return execution_results.build_execution_summary(
        script_results,
        returncode=returncode,
    )


def create_run_result_for_script(run_id, order_index, module_name, filename, command=None, status="unknown"):
    script_file = get_script_file(module_name, filename)
    script_asset = sync_script_asset(module_name, script_file, change_source="manual")
    if not script_asset:
        return None
    script_revision = get_asset_current_revision(script_asset)
    plan_asset = get_plan_asset_for_script_asset(script_asset)
    plan_revision = get_asset_current_revision(plan_asset)
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    results_table = get_test_run_results_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {results_table}
                  (project_id, run_id, order_index, script_asset_id, script_revision_id, plan_asset_id, plan_revision_id,
                   module_name, script_path, script_title, command, playwright_project, browser_name, status,
                   duration_ms, retry_count, database_reset_status, database_reset_started_at,
                   database_reset_finished_at, database_reset_error, error_message, error_stack, stdout_tail,
                   started_at, finished_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s,
                        NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, %s, NULL, %s, %s)
                """,
                (
                    project_id,
                    run_id,
                    order_index,
                    script_asset["asset_id"],
                    script_revision.get("revision_id") if script_revision else None,
                    plan_asset.get("asset_id") if plan_asset else None,
                    plan_revision.get("revision_id") if plan_revision else None,
                    module_name,
                    str(script_file),
                    Path(filename).stem,
                    command,
                    db_result_status(status),
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            result_id = cursor.lastrowid
        connection.commit()
    return get_run_result(result_id)


def get_run_result(result_id):
    config = get_platform_database_config()
    if not config.get("enabled"):
        return None
    ensure_platform_database_schema(config)
    results_table = get_test_run_results_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {results_table} WHERE project_id = %s AND result_id = %s",
                (project_id, result_id),
            )
            return cursor.fetchone()


def update_run_result(result_id, status=None, stdout_tail=None, error_message=None, command=None, database_reset_status=None, finished=True):
    config = get_platform_database_config()
    if not config.get("enabled") or not result_id:
        return None
    ensure_platform_database_schema(config)
    results_table = get_test_run_results_table(config)
    project_id = get_current_project_id()
    fields = ["updated_at = %s"]
    values = [current_time_ms()]
    if status is not None:
        fields.append("status = %s")
        values.append(db_result_status(status))
    if stdout_tail is not None:
        fields.append("stdout_tail = %s")
        values.append(stdout_tail[-4000:])
    if error_message is not None:
        fields.append("error_message = %s")
        values.append(error_message)
    if command is not None:
        fields.append("command = %s")
        values.append(command)
    if database_reset_status is not None:
        fields.append("database_reset_status = %s")
        values.append(database_reset_status)
    if finished:
        fields.append("finished_at = %s")
        values.append(current_time_ms())
    values.extend([project_id, result_id])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {results_table} SET {', '.join(fields)} WHERE project_id = %s AND result_id = %s",
                values,
            )
        connection.commit()
    return get_run_result(result_id)


def register_run_artifact(run_id, artifact_type, path, result_id=None, url=None):
    if not run_id or not path or not Path(path).exists() or not is_platform_database_enabled():
        return None
    config = get_platform_database_config()
    ensure_platform_database_schema(config)
    artifacts_table = get_test_run_artifacts_table(config)
    project_id = get_current_project_id()
    path = Path(path)
    size = path.stat().st_size if path.is_file() else None
    file_sha = sha256_file(path) if path.is_file() else None
    relative_path = ""
    try:
        relative_path = project_relative_path(path)
    except ValueError:
        pass
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {artifacts_table}
                  (project_id, run_id, result_id, artifact_type, path, relative_path, url, size, sha256, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (project_id, run_id, result_id, artifact_type, str(path), relative_path, url, size, file_sha, now_ms),
            )
            connection.commit()
            return cursor.lastrowid


def list_run_artifacts(run_id, result_id=None):
    if not run_id or not is_platform_database_enabled():
        return []
    config = get_platform_database_config()
    ensure_platform_database_schema(config)
    table = get_test_run_artifacts_table(config)
    project_id = get_current_project_id()
    params = [project_id, str(run_id)]
    result_clause = ""
    if result_id is not None:
        result_clause = " AND (result_id = %s OR result_id IS NULL)"
        params.append(result_id)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM {table}
                WHERE project_id = %s AND run_id = %s{result_clause}
                ORDER BY artifact_id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()


def serialize_run_artifact_payload(row):
    if not row:
        return None

    artifact_type = row.get("artifact_type") or ""
    relative_path = row.get("relative_path") or ""
    url = row.get("url") or ""
    if not url and relative_path:
        quoted_path = urlparse.quote(relative_path)
        if artifact_type == "video":
            url = f"/api/run-videos/{quoted_path}"
        elif artifact_type == "html_report":
            url = f"/api/playwright-reports/{quoted_path}"

    return {
        "artifact_id": row.get("artifact_id"),
        "artifact_type": artifact_type,
        "path": row.get("path") or "",
        "relative_path": relative_path,
        "url": url,
        "size": row.get("size"),
        "created_at": row.get("created_at"),
    }


def get_execution_context_relative_path_keys(context, result_ids):
    if not isinstance(context, dict):
        return {}

    raw_relative_path_keys = context.get("relative_path_keys")
    if isinstance(raw_relative_path_keys, dict):
        return {
            str(relative_path).replace("\\", "/").lstrip("./"): str(key)
            for relative_path, key in raw_relative_path_keys.items()
            if relative_path and key
        }

    module_name = context.get("module_name")
    filenames = context.get("filenames")
    if module_name and isinstance(filenames, list):
        return {
            get_script_test_relative_path(module_name, filename).replace("\\", "/").lstrip("./"): str(filename)
            for filename in filenames
            if filename
        }

    relative_script_path = context.get("relative_script_path")
    if relative_script_path:
        key = next(iter(result_ids.keys()), Path(str(relative_script_path)).name) if result_ids else Path(str(relative_script_path)).name
        return {str(relative_script_path).replace("\\", "/").lstrip("./"): str(key)}

    return {}


def resolve_playwright_attachment_path(raw_path, project_root, json_report_file):
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    raw_path = raw_path.strip()
    candidate_paths = []
    attachment_path = Path(raw_path)
    if attachment_path.is_absolute():
        candidate_paths.append(attachment_path)
    else:
        candidate_paths.extend([project_root / attachment_path, Path(json_report_file).parent / attachment_path])

    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if resolved.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved

    return None


def resolve_playwright_evidence_path(raw_path, project_root, json_report_file, allowed_suffixes):
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    attachment_path = Path(raw_path.strip())
    candidates = [attachment_path] if attachment_path.is_absolute() else [project_root / attachment_path, Path(json_report_file).parent / attachment_path]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if resolved.suffix.lower() not in allowed_suffixes or not resolved.exists() or not resolved.is_file():
            continue
        try:
            resolved.relative_to(project_root.resolve(strict=False))
        except ValueError:
            continue
        return resolved
    return None


def collect_playwright_evidence_artifacts_by_key(json_report_file, relative_path_keys):
    json_report_path = Path(json_report_file) if json_report_file else None
    if not json_report_path or not json_report_path.exists() or not relative_path_keys:
        return []
    project_root = get_project_root().resolve(strict=False)
    normalized_keys = {
        str(relative_path).replace("\\", "/").lstrip("./"): str(key)
        for relative_path, key in relative_path_keys.items()
        if relative_path and key
    }
    filename_to_relative_path = {Path(relative_path).name: relative_path for relative_path in normalized_keys}
    try:
        report = json.loads(json_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    artifacts = []
    seen = set()

    def match_report_file(raw_file, current_file=""):
        node_file = normalize_report_file_path(raw_file, project_root)
        if not node_file:
            return current_file
        for relative_path in normalized_keys:
            if node_file == relative_path or node_file.endswith(f"/{relative_path}"):
                return relative_path
        return filename_to_relative_path.get(Path(node_file).name, current_file)

    def register_attachment(attachment, effective_file):
        if not isinstance(attachment, dict) or not effective_file:
            return
        name = str(attachment.get("name") or "").lower()
        content_type = str(attachment.get("contentType") or "").lower()
        raw_path = attachment.get("path")
        suffix = Path(str(raw_path or "")).suffix.lower()
        artifact_type = ""
        allowed_suffixes = set()
        if content_type.startswith("image/") or suffix in SCREENSHOT_SUFFIXES or "screenshot" in name:
            artifact_type = "screenshot"
            allowed_suffixes = SCREENSHOT_SUFFIXES
        elif name == "trace" or "trace" in name or (suffix in TRACE_SUFFIXES and "zip" in content_type):
            artifact_type = "trace"
            allowed_suffixes = TRACE_SUFFIXES
        if not artifact_type:
            return
        path = resolve_playwright_evidence_path(raw_path, project_root, json_report_path, allowed_suffixes)
        if not path:
            return
        key = normalized_keys.get(effective_file)
        identity = (artifact_type, str(path), key)
        if identity in seen:
            return
        seen.add(identity)
        artifacts.append({"artifact_type": artifact_type, "path": path, "key": key})

    def collect_from_node(node, current_file=""):
        if not isinstance(node, dict):
            return
        effective_file = match_report_file(node.get("file"), current_file)
        for spec in node.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            for test in spec.get("tests") or []:
                if not isinstance(test, dict):
                    continue
                for result in test.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    for attachment in result.get("attachments") or []:
                        register_attachment(attachment, effective_file)
        for suite in node.get("suites") or []:
            collect_from_node(suite, effective_file)

    for suite in report.get("suites") or []:
        collect_from_node(suite)
    return artifacts


def collect_playwright_video_artifacts_by_key(json_report_file, relative_path_keys):
    json_report_path = Path(json_report_file) if json_report_file else None
    if not json_report_path or not json_report_path.exists() or not relative_path_keys:
        return {}

    project_root = get_project_root().resolve(strict=False)
    normalized_keys = {
        str(relative_path).replace("\\", "/").lstrip("./"): str(key)
        for relative_path, key in relative_path_keys.items()
        if relative_path and key
    }
    filename_to_relative_path = {Path(relative_path).name: relative_path for relative_path in normalized_keys}
    if not normalized_keys:
        return {}

    try:
        report = json.loads(json_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    videos_by_key = {}

    def match_report_file(raw_file, current_file=""):
        node_file = normalize_report_file_path(raw_file, project_root)
        if not node_file:
            return current_file

        for relative_path in normalized_keys:
            if node_file == relative_path or node_file.endswith(f"/{relative_path}"):
                return relative_path

        return filename_to_relative_path.get(Path(node_file).name, current_file)

    def register_attachment_video(attachment, effective_file):
        if not isinstance(attachment, dict) or not effective_file:
            return
        name = str(attachment.get("name") or "").lower()
        content_type = str(attachment.get("contentType") or "").lower()
        raw_path = attachment.get("path")
        if name != "video" and not content_type.startswith("video/"):
            suffix = Path(str(raw_path or "")).suffix.lower()
            if suffix not in VIDEO_SUFFIXES:
                return

        video_file = resolve_playwright_attachment_path(raw_path, project_root, json_report_path)
        if not video_file:
            return

        key = normalized_keys.get(effective_file)
        if key and key not in videos_by_key:
            try:
                videos_by_key[key] = serialize_run_video(video_file)
            except (OSError, RuntimeError, ValueError):
                return

    def collect_from_node(node, current_file=""):
        if not isinstance(node, dict):
            return

        effective_file = match_report_file(node.get("file"), current_file)
        for spec in node.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            for test in spec.get("tests") or []:
                if not isinstance(test, dict):
                    continue
                for result in test.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    for attachment in result.get("attachments") or []:
                        register_attachment_video(attachment, effective_file)

        for suite in node.get("suites") or []:
            collect_from_node(suite, effective_file)

    for suite in report.get("suites") or []:
        collect_from_node(suite)

    return videos_by_key


def collect_test_suite_execution_video_fallbacks(result_rows, json_reports_by_run):
    if not result_rows:
        return {}

    project_root = get_project_root().resolve(strict=False)
    rows_by_run = {}
    for row in result_rows:
        run_id = row.get("run_id")
        result_id = row.get("result_id")
        script_path = row.get("script_path") or ""
        if not run_id or not result_id or not script_path:
            continue

        normalized_path = str(script_path).replace("\\", "/")
        try:
            path = Path(script_path)
            if path.is_absolute():
                normalized_path = path.resolve(strict=False).relative_to(project_root).as_posix()
        except (OSError, ValueError):
            pass
        rows_by_run.setdefault(run_id, []).append(
            {
                "result_id": int(result_id),
                "order_index": int(row.get("order_index") or 0),
                "script_path": normalized_path.lstrip("./"),
            }
        )

    videos_by_result = {}
    for run_id, rows in rows_by_run.items():
        json_report_file = json_reports_by_run.get(run_id)
        if json_report_file:
            relative_path_keys = {row["script_path"]: row["result_id"] for row in rows}
            recovered = collect_playwright_video_artifacts_by_key(json_report_file, relative_path_keys)
            for result_id, video in recovered.items():
                try:
                    normalized_result_id = int(result_id)
                except (TypeError, ValueError):
                    continue
                videos_by_result.setdefault(normalized_result_id, video)

        missing_rows_by_order = {
            row["order_index"]: row
            for row in rows
            if row["order_index"] > 0 and row["result_id"] not in videos_by_result
        }
        if not missing_rows_by_order:
            continue

        results_dir = project_root / "test-results" / RUN_ARTIFACTS_DIR_NAME / run_id
        if not results_dir.is_dir():
            continue

        candidates_by_order = {}
        try:
            video_files = results_dir.rglob("*")
            for video_file in video_files:
                if not video_file.is_file() or video_file.suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                try:
                    first_part = video_file.relative_to(results_dir).parts[0]
                except (OSError, ValueError, IndexError):
                    continue
                match = re.fullmatch(r"part-(\d+)", first_part)
                if not match:
                    continue
                order_index = int(match.group(1))
                if order_index not in missing_rows_by_order:
                    continue
                try:
                    modified_at = video_file.stat().st_mtime
                except OSError:
                    continue
                candidates_by_order.setdefault(order_index, []).append((modified_at, video_file))
        except OSError:
            continue

        for order_index, candidates in candidates_by_order.items():
            candidates.sort(key=lambda item: item[0], reverse=True)
            try:
                video = serialize_run_video(candidates[0][1])
            except (OSError, RuntimeError, ValueError):
                continue
            videos_by_result.setdefault(missing_rows_by_order[order_index]["result_id"], video)

    return videos_by_result


def register_script_video_artifact(run_id, result_id, started_at, results_dir):
    result = build_run_video_result(started_at, results_dir)
    video = result.get("video") if isinstance(result, dict) else None
    if not video or not video.get("path"):
        return None
    return register_run_artifact(run_id, "video", video["path"], result_id=result_id, url=video.get("url"))


def register_execution_artifacts(run_id, context, run_result=None, result_ids=None):
    result_ids = result_ids or {}
    report = run_result.get("report") if isinstance(run_result, dict) else None
    if report and report.get("path"):
        register_run_artifact(run_id, "html_report", report["path"], url=report.get("url"))
    json_report_file = context.get("json_report_file")
    if json_report_file and Path(json_report_file).exists():
        register_run_artifact(run_id, "json_report", json_report_file)

    relative_path_keys = get_execution_context_relative_path_keys(context, result_ids)
    videos_by_key = collect_playwright_video_artifacts_by_key(json_report_file, relative_path_keys)
    registered_video = False
    for key, video in videos_by_key.items():
        if video and video.get("path"):
            register_run_artifact(run_id, "video", video["path"], result_id=result_ids.get(key), url=video.get("url"))
            registered_video = True

    video = run_result.get("video") if isinstance(run_result, dict) else None
    if not registered_video and video and video.get("path"):
        result_id = None
        if len(result_ids) == 1:
            result_id = next(iter(result_ids.values()))
        register_run_artifact(run_id, "video", video["path"], result_id=result_id, url=video.get("url"))

    for evidence in collect_playwright_evidence_artifacts_by_key(json_report_file, relative_path_keys):
        register_run_artifact(
            run_id,
            evidence["artifact_type"],
            evidence["path"],
            result_id=result_ids.get(evidence.get("key")),
        )


def list_related_scripts_for_plan(plan_asset_id):
    config = get_platform_database_config()
    if not config.get("enabled") or not plan_asset_id:
        return []
    ensure_platform_database_schema(config)
    assets_table = get_test_assets_table(config)
    results_table = get_test_run_results_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.*,
                       (
                         SELECT r.status
                         FROM {results_table} r
                         WHERE r.project_id = %s AND r.script_asset_id = s.asset_id
                         ORDER BY r.finished_at DESC, r.updated_at DESC
                         LIMIT 1
                       ) AS last_status,
                       (
                         SELECT r.finished_at
                         FROM {results_table} r
                         WHERE r.project_id = %s AND r.script_asset_id = s.asset_id
                         ORDER BY r.finished_at DESC, r.updated_at DESC
                         LIMIT 1
                       ) AS last_run_at
                FROM {assets_table} s
                WHERE s.project_id = %s
                  AND s.asset_type = 'script'
                  AND s.from_plan_asset_id = %s
                  AND s.deleted_at IS NULL
                ORDER BY s.updated_at DESC
                """,
                (project_id, project_id, project_id, plan_asset_id),
            )
            return cursor.fetchall()


def list_recent_script_results(script_asset_id, limit=20):
    config = get_platform_database_config()
    if not config.get("enabled") or not script_asset_id:
        return []
    ensure_platform_database_schema(config)
    results_table = get_test_run_results_table(config)
    runs_table = get_test_runs_table(config)
    artifacts_table = get_test_run_artifacts_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT r.*, tr.run_type, tr.execution_mode, tr.database_reset_mode, tr.summary_json
                FROM {results_table} r
                LEFT JOIN {runs_table} tr ON tr.project_id = r.project_id AND tr.run_id = r.run_id
                WHERE r.project_id = %s AND r.script_asset_id = %s
                ORDER BY r.finished_at DESC, r.updated_at DESC
                LIMIT %s
                """,
                (project_id, script_asset_id, int(limit)),
            )
            result_rows = cursor.fetchall()
            run_ids = list(dict.fromkeys(row.get("run_id") for row in result_rows if row.get("run_id")))
            if not run_ids:
                return result_rows

            placeholders = ",".join(["%s"] * len(run_ids))
            cursor.execute(
                f"""
                SELECT *
                FROM {artifacts_table}
                WHERE project_id = %s
                  AND run_id IN ({placeholders})
                  AND artifact_type IN ('html_report', 'json_report', 'video')
                ORDER BY run_id ASC,
                         CASE WHEN result_id IS NULL THEN 0 ELSE 1 END,
                         created_at ASC,
                         artifact_id ASC
                """,
                (project_id, *run_ids),
            )
            artifact_rows = cursor.fetchall()

    reports_by_run = {}
    json_reports_by_run = {}
    videos_by_result = {}
    for artifact in artifact_rows:
        payload = serialize_run_artifact_payload(artifact)
        if not payload:
            continue
        artifact_type = artifact.get("artifact_type")
        if artifact_type == "html_report":
            reports_by_run.setdefault(artifact.get("run_id"), payload)
        elif artifact_type == "json_report" and artifact.get("path"):
            json_reports_by_run.setdefault(artifact.get("run_id"), artifact["path"])
        elif artifact_type == "video" and artifact.get("result_id"):
            videos_by_result.setdefault(int(artifact["result_id"]), payload)

    recovered_videos = collect_test_suite_execution_video_fallbacks(result_rows, json_reports_by_run)
    for result_id, video in recovered_videos.items():
        videos_by_result.setdefault(result_id, video)

    return [
        {
            **row,
            "report": reports_by_run.get(row.get("run_id")),
            "video": videos_by_result.get(int(row["result_id"])) if row.get("result_id") else None,
        }
        for row in result_rows
    ]


def serialize_run_result(result):
    if not result:
        return None
    return {
        "result_id": result.get("result_id"),
        "run_id": result.get("run_id"),
        "run_type": result.get("run_type"),
        "execution_mode": result.get("execution_mode"),
        "database_reset_mode": result.get("database_reset_mode"),
        "order_index": result.get("order_index"),
        "script_asset_id": result.get("script_asset_id"),
        "script_revision_id": result.get("script_revision_id"),
        "plan_asset_id": result.get("plan_asset_id"),
        "plan_revision_id": result.get("plan_revision_id"),
        "module_name": result.get("module_name"),
        "script_path": result.get("script_path"),
        "script_title": result.get("script_title"),
        "command": result.get("command"),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "database_reset_status": result.get("database_reset_status"),
        "error_message": result.get("error_message"),
        "stdout_tail": result.get("stdout_tail") or "",
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "created_at": result.get("created_at"),
        "updated_at": result.get("updated_at"),
        "report": result.get("report"),
        "video": result.get("video"),
    }


def serialize_test_suite_execution_result(row, report=None, video=None):
    if not row:
        return None

    script_path = row.get("script_path") or ""
    filename = Path(script_path.replace("\\", "/")).name if script_path else ""
    script_title = row.get("script_title") or (Path(filename).stem if filename else "")
    module_name = row.get("module_name") or ""

    return {
        "result_id": row.get("result_id"),
        "run_id": row.get("run_id"),
        "order_index": row.get("order_index"),
        "script_asset_id": row.get("script_asset_id"),
        "script_revision_id": row.get("script_revision_id"),
        "plan_asset_id": row.get("plan_asset_id"),
        "plan_revision_id": row.get("plan_revision_id"),
        "module_name": module_name,
        "filename": filename,
        "script_key": f"{module_name}/{filename}" if module_name and filename else "",
        "script_path": script_path,
        "script_name": script_title,
        "command": row.get("command") or "",
        "status": row.get("status") or "unknown",
        "duration_ms": row.get("duration_ms"),
        "database_reset_status": row.get("database_reset_status"),
        "error_message": row.get("error_message") or "",
        "stdout_tail": row.get("stdout_tail") or "",
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "report": report,
        "video": video,
    }


def serialize_test_suite_execution_run(row, results=None, report=None):
    if not row:
        return None

    return {
        "run_id": row.get("run_id"),
        "run_type": row.get("run_type"),
        "status": row.get("status") or "",
        "execution_mode": row.get("execution_mode") or "",
        "database_reset_mode": row.get("database_reset_mode") or "",
        "triggered_by": row.get("triggered_by") or "",
        "trigger_source": row.get("trigger_source") or "",
        "suite_id": row.get("suite_id") or "",
        "command": row.get("command") or "",
        "git_commit_sha": row.get("git_commit_sha") or "",
        "summary": load_json_column(row.get("summary_json"), {}),
        "total_files": row.get("total_files") or 0,
        "completed_files": row.get("completed_files") or 0,
        "error": row.get("error") or "",
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "report": report,
        "results": results or [],
    }


def list_test_suite_execution_records_from_mysql(suite_uid, limit=20):
    suite_uid = validate_uid(suite_uid, "suite_uid")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = min(max(limit, 1), 50)

    config, suites_table, _suite_items_table = get_test_suite_tables()
    runs_table = get_test_runs_table(config)
    results_table = get_test_run_results_table(config)
    artifacts_table = get_test_run_artifacts_table(config)
    project_id = get_current_project_id()

    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            suite = get_test_suite_row_by_uid(cursor, suites_table, project_id, suite_uid)
            if not suite:
                return None

            cursor.execute(
                f"""
                SELECT *
                FROM {runs_table}
                WHERE project_id = %s
                  AND run_type = 'test_suite'
                  AND suite_id = %s
                ORDER BY COALESCE(started_at, created_at, updated_at) DESC, updated_at DESC
                LIMIT %s
                """,
                (project_id, suite_uid, limit),
            )
            runs = cursor.fetchall()
            run_ids = [row["run_id"] for row in runs]
            if not run_ids:
                return []

            placeholders = ",".join(["%s"] * len(run_ids))
            cursor.execute(
                f"""
                SELECT *
                FROM {results_table}
                WHERE project_id = %s AND run_id IN ({placeholders})
                ORDER BY run_id ASC, order_index ASC, result_id ASC
                """,
                (project_id, *run_ids),
            )
            result_rows = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT *
                FROM {artifacts_table}
                WHERE project_id = %s
                  AND run_id IN ({placeholders})
                  AND artifact_type IN ('html_report', 'json_report', 'video')
                ORDER BY run_id ASC,
                         CASE WHEN result_id IS NULL THEN 0 ELSE 1 END,
                         created_at ASC,
                         artifact_id ASC
                """,
                (project_id, *run_ids),
            )
            artifact_rows = cursor.fetchall()

    reports_by_run = {}
    json_reports_by_run = {}
    videos_by_result = {}
    for artifact in artifact_rows:
        payload = serialize_run_artifact_payload(artifact)
        if not payload:
            continue
        artifact_type = artifact.get("artifact_type")
        if artifact_type == "html_report":
            reports_by_run.setdefault(artifact.get("run_id"), payload)
        elif artifact_type == "json_report" and artifact.get("path"):
            json_reports_by_run.setdefault(artifact.get("run_id"), artifact["path"])
        elif artifact_type == "video" and artifact.get("result_id"):
            videos_by_result.setdefault(int(artifact["result_id"]), payload)

    recovered_videos = collect_test_suite_execution_video_fallbacks(result_rows, json_reports_by_run)
    for result_id, video in recovered_videos.items():
        videos_by_result.setdefault(result_id, video)

    results_by_run = {run_id: [] for run_id in run_ids}
    for row in result_rows:
        run_report = reports_by_run.get(row.get("run_id"))
        video = videos_by_result.get(int(row["result_id"])) if row.get("result_id") else None
        result = serialize_test_suite_execution_result(row, report=run_report, video=video)
        if result:
            results_by_run.setdefault(row.get("run_id"), []).append(result)

    return [
        serialize_test_suite_execution_run(
            row,
            results=results_by_run.get(row.get("run_id"), []),
            report=reports_by_run.get(row.get("run_id")),
        )
        for row in runs
    ]


def ensure_platform_database_schema(config=None):
    return bootstrap_platform_database_schema(
        config,
        dependencies=SchemaDependencies(
            get_platform_database_config=get_platform_database_config,
            quote_mysql_identifier=quote_mysql_identifier,
            platform_mysql_connection=platform_mysql_connection,
            platform_table_sql=platform_table_sql,
            ensure_mysql_column=ensure_mysql_column,
            ensure_mysql_column_type=ensure_mysql_column_type,
            ensure_mysql_index=ensure_mysql_index,
            mysql_primary_key_columns=mysql_primary_key_columns,
            mysql_table_exists=mysql_table_exists,
            mysql_table_has_columns=mysql_table_has_columns,
            get_agent_item_retry_flows_table=get_agent_item_retry_flows_table,
            get_agent_run_attempts_table=get_agent_run_attempts_table,
            get_agent_run_events_table=get_agent_run_events_table,
            get_agent_run_steps_table=get_agent_run_steps_table,
            get_agent_runs_table=get_agent_runs_table,
            get_page_inventory_table=get_page_inventory_table,
            get_platform_projects_table=get_platform_projects_table,
            get_requirement_module_plans_table=get_requirement_module_plans_table,
            get_requirement_modules_table=get_requirement_modules_table,
            get_requirements_table=get_requirements_table,
            get_setup_bindings_table=get_setup_bindings_table,
            get_setup_runs_table=get_setup_runs_table,
            get_setup_scripts_table=get_setup_scripts_table,
            get_test_suite_items_table=get_test_suite_items_table,
            get_test_suites_table=get_test_suites_table,
            get_default_project_id_from_cursor=get_default_project_id_from_cursor,
            migrate_legacy_test_suites=migrate_legacy_test_suites,
            seed_auth_defaults=seed_auth_defaults,
            seed_platform_projects=seed_platform_projects,
            process_started_at_ms=PROCESS_STARTED_AT_MS,
        ),
        state=PLATFORM_DATABASE_SCHEMA_STATE,
    )


def _setup_validation_dependencies():
    return setup_validation.SetupValidationDependencies(
        resolve_working_directory=resolve_setup_working_directory,
        validate_uid=validate_setup_uid,
        normalize_name=normalize_setup_name,
        normalize_string_map=normalize_setup_string_map,
        normalize_timeout=normalize_setup_timeout,
    )


def _setup_repository_dependencies():
    return setup_repository.SetupRepositoryDependencies(
        get_platform_database_config=get_platform_database_config,
        ensure_platform_database_schema=ensure_platform_database_schema,
        get_setup_tables=get_setup_tables,
        get_current_project_id=get_current_project_id,
        get_current_project=get_current_project,
        platform_mysql_connection=platform_mysql_connection,
        get_setup_scripts_table=get_setup_scripts_table,
        get_setup_bindings_table=get_setup_bindings_table,
        get_setup_runs_table=get_setup_runs_table,
        get_setup_script_row=get_setup_script_row,
        list_setup_bindings=list_setup_bindings_from_mysql,
        validate_setup_uid=validate_setup_uid,
        normalize_setup_script_payload=normalize_setup_script_payload,
        normalize_setup_binding_payload=normalize_setup_binding_payload,
        serialize_setup_script=serialize_setup_script,
        serialize_setup_binding=serialize_setup_binding,
        serialize_setup_run=serialize_setup_run,
        current_time_ms=current_time_ms,
        current_platform_author=current_platform_author,
        compact_json_dumps=compact_json_dumps,
        redact_setup_snapshot=redact_setup_snapshot,
        redact_setup_text=redact_setup_text,
        target_types=SETUP_BINDING_TARGET_TYPES,
        uid_factory=lambda: uuid.uuid4().hex,
    )


def _setup_runner_dependencies():
    return setup_runner.SetupRunnerDependencies(
        resolve_working_directory=resolve_setup_working_directory,
        normalize_process_output=normalize_process_output,
        redact_setup_text=redact_setup_text,
        read_process_output=read_setup_process_output,
        close_process_output=close_setup_process_output,
        kill_process=kill_setup_process,
        output_buffer_factory=SetupOutputRingBuffer,
        popen=subprocess.Popen,
        clock=time.time,
        thread_factory=threading.Thread,
        environment_factory=lambda: os.environ.copy(),
        os_name=os.name,
    )


def _setup_service_instance():
    return setup_service.SetupService(
        setup_service.SetupServiceDependencies(
            get_current_project=get_current_project,
            is_platform_database_enabled=is_platform_database_enabled,
            list_setup_bindings=list_setup_bindings_from_mysql,
            select_setup_binding=select_setup_binding,
            get_setup_script=get_setup_script_from_mysql,
            create_setup_run_record=create_setup_run_record,
            execute_setup_script_once=execute_setup_script_once,
            finish_setup_run_record=finish_setup_run_record,
            redact_setup_text=redact_setup_text,
            normalize_process_output=normalize_process_output,
            resolve_setup_profile=resolve_setup_profile,
            execute_setup_profile=execute_setup_profile,
            clock=time.time,
            timeout_expired=subprocess.TimeoutExpired,
            preparation_error=SetupPreparationError,
        )
    )


def _save_setup_script_for_web(payload, script_uid=None):
    if script_uid is None:
        return save_setup_script_in_mysql(payload)
    return save_setup_script_in_mysql(payload, script_uid)


def _save_setup_binding_for_web(payload, binding_uid=None):
    if binding_uid is None:
        return save_setup_binding_in_mysql(payload)
    return save_setup_binding_in_mysql(payload, binding_uid)


def _setup_web_services():
    return SetupWebServices(
        list_scripts=lambda: list_setup_scripts_from_mysql(),
        save_script=_save_setup_script_for_web,
        delete_script=lambda script_uid: (
            delete_setup_script_in_mysql(script_uid)
        ),
        list_bindings=lambda: list_setup_bindings_from_mysql(),
        save_binding=_save_setup_binding_for_web,
        delete_binding=lambda binding_uid: (
            delete_setup_binding_in_mysql(binding_uid)
        ),
        list_runs=lambda limit, script_uid=None: (
            list_setup_runs_from_mysql(limit, script_uid)
        ),
        get_script=lambda script_uid: (
            get_setup_script_from_mysql(script_uid)
        ),
        get_current_project=lambda: get_current_project(),
        execute_profile=lambda resolution, **kwargs: (
            execute_setup_profile(resolution, **kwargs)
        ),
        preparation_error_type=SetupPreparationError,
        binding_target_types=SETUP_BINDING_TARGET_TYPES,
    )


def validate_setup_uid(value, field_name="uid", generate=False):
    return setup_validation.validate_setup_uid(
        value,
        field_name,
        generate,
        pattern=PROJECT_KEY_PATTERN,
        uid_factory=lambda: uuid.uuid4().hex,
    )


def normalize_setup_name(value, field_name="name"):
    return setup_validation.normalize_setup_name(value, field_name)


def normalize_setup_string_map(value, field_name):
    return setup_validation.normalize_setup_string_map(value, field_name)


def normalize_setup_timeout(value, fallback=DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS):
    return setup_validation.normalize_setup_timeout(
        value,
        fallback,
        maximum=MAX_SETUP_SCRIPT_TIMEOUT_SECONDS,
    )


def normalize_setup_script_payload(payload, existing=None):
    return setup_validation.normalize_setup_script_payload(
        payload,
        existing,
        _setup_validation_dependencies(),
    )


def serialize_setup_script(row):
    return setup_model.serialize_setup_script(row, load_json_column)


def get_setup_tables():
    return setup_repository.get_setup_tables(
        _setup_repository_dependencies()
    )


def get_setup_script_row(cursor, config, project_id, script_uid):
    return setup_repository.get_setup_script_row(
        cursor,
        config,
        project_id,
        script_uid,
        _setup_repository_dependencies(),
    )


def list_setup_scripts_from_mysql(include_disabled=True):
    return setup_repository.list_setup_scripts(
        include_disabled,
        _setup_repository_dependencies(),
    )


def get_setup_script_from_mysql(script_uid):
    return setup_repository.get_setup_script(
        script_uid,
        _setup_repository_dependencies(),
    )


def save_setup_script_in_mysql(payload, script_uid=None):
    return setup_repository.save_setup_script(
        payload,
        script_uid,
        _setup_repository_dependencies(),
    )


def delete_setup_script_in_mysql(script_uid):
    return setup_repository.delete_setup_script(
        script_uid,
        _setup_repository_dependencies(),
    )


def normalize_setup_binding_payload(payload, existing=None):
    return setup_validation.normalize_setup_binding_payload(
        payload,
        existing,
        _setup_validation_dependencies(),
        target_types=SETUP_BINDING_TARGET_TYPES,
    )


def serialize_setup_binding(row):
    return setup_model.serialize_setup_binding(row)


def list_setup_bindings_from_mysql(include_disabled=True):
    return setup_repository.list_setup_bindings(
        include_disabled,
        _setup_repository_dependencies(),
    )


def save_setup_binding_in_mysql(payload, binding_uid=None):
    return setup_repository.save_setup_binding(
        payload,
        binding_uid,
        _setup_repository_dependencies(),
    )


def delete_setup_binding_in_mysql(binding_uid):
    return setup_repository.delete_setup_binding(
        binding_uid,
        _setup_repository_dependencies(),
    )


def select_setup_binding(bindings, targets):
    return setup_model.select_setup_binding(
        bindings,
        targets,
        precedence=SETUP_BINDING_PRECEDENCE,
    )


def build_setup_targets(module_name=None, filename=None, suite_uid=None, filenames=None, items=None):
    return setup_model.build_setup_targets(
        get_current_project(),
        module_name=module_name,
        filename=filename,
        suite_uid=suite_uid,
        filenames=filenames,
        items=items,
    )


def resolve_setup_profile(targets):
    return _setup_service_instance().resolve_setup_profile(targets)


def resolve_setup_working_directory(value):
    return setup_validation.resolve_setup_working_directory(
        value,
        get_project_root(),
    )


def setup_secret_values(script):
    return setup_model.setup_secret_values(script)


def redact_setup_text(value, script=None, limit=4000):
    return setup_model.redact_setup_text(
        value,
        script,
        limit,
        redact_sensitive_text=redact_sensitive_text,
        secret_values=setup_secret_values,
    )


def redact_setup_snapshot(value, parent_key=""):
    return setup_model.redact_setup_snapshot(
        value,
        parent_key,
        redact_text=redact_setup_text,
    )


SetupOutputRingBuffer = setup_runner.SetupOutputRingBuffer


def read_setup_process_output(stream, output_buffer):
    return setup_runner.read_setup_process_output(
        stream,
        output_buffer,
    )


def close_setup_process_output(process, reader):
    return setup_runner.close_setup_process_output(process, reader)


def kill_setup_process(process):
    return setup_runner.kill_setup_process(
        process,
        os_name=os.name,
        kill_process_group=getattr(os, "killpg", None),
        sigkill=getattr(signal, "SIGKILL", signal.SIGTERM),
        timeout_expired=subprocess.TimeoutExpired,
    )


def _execute_setup_script_once_unlocked(script, timeout_seconds):
    if not parse_boolean(
        os.environ.get("PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION"),
        False,
    ):
        raise RuntimeError(
            "宿主机测试准备脚本默认禁用。仅在隔离执行器或受信任容器中设置 "
            "PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION=true 后启用。"
        )
    return setup_runner.execute_setup_script_once_unlocked(
        script,
        timeout_seconds,
        _setup_runner_dependencies(),
    )


def execute_setup_script_once(script, timeout_seconds):
    return setup_runner.execute_setup_script_once(
        script,
        timeout_seconds,
        execute_unlocked=_execute_setup_script_once_unlocked,
        concurrency_locks=SETUP_CONCURRENCY_LOCKS,
        concurrency_guard=SETUP_CONCURRENCY_LOCKS_GUARD,
        lock_factory=threading.Lock,
    )


def create_setup_run_record(parent_run_id, resolution, target_override=None):
    return setup_repository.create_setup_run_record(
        parent_run_id,
        resolution,
        target_override,
        _setup_repository_dependencies(),
    )


def finish_setup_run_record(setup_run, execution_result):
    return setup_repository.finish_setup_run_record(
        setup_run,
        execution_result,
        _setup_repository_dependencies(),
    )


def execute_setup_profile(resolution, parent_run_id=None, emit_log=None, target_override=None):
    return _setup_service_instance().execute_setup_profile(
        resolution,
        parent_run_id=parent_run_id,
        emit_log=emit_log,
        target_override=target_override,
    )


execute_setup_script = execute_setup_profile


def prepare_bound_setup(parent_run_id, targets, emit_log=None):
    return _setup_service_instance().prepare_bound_setup(
        parent_run_id,
        targets,
        emit_log=emit_log,
    )


def serialize_setup_run(row):
    return setup_model.serialize_setup_run(row, load_json_column)


def list_setup_runs_from_mysql(limit=50, script_uid=None):
    return setup_repository.list_setup_runs(
        limit,
        script_uid,
        _setup_repository_dependencies(),
    )


def validate_platform_record_bucket(bucket):
    return validate_record_bucket(bucket, PLATFORM_RECORD_BUCKETS)


def validate_platform_record_key(record_key):
    return validate_record_key(record_key)


def record_updated_at_ms(record):
    return resolve_record_updated_at_ms(record, lambda: int(time.time() * 1000))


def load_platform_records_from_mysql():
    return PLATFORM_RECORD_REPOSITORY.load_records()


def save_platform_record_to_mysql(bucket, record_key, record):
    return PLATFORM_RECORD_REPOSITORY.save_record(bucket, record_key, record)


def save_platform_job_to_mysql(job, job_type="plan_generation"):
    return PLATFORM_RECORD_REPOSITORY.save_job(job, job_type)


def load_platform_job_from_mysql(job_id):
    return PLATFORM_RECORD_REPOSITORY.load_job(job_id)


def require_platform_database():
    return PLATFORM_RECORD_REPOSITORY.require_database()


def compact_json_dumps(value):
    return serialize_compact_json(value)


def load_json_column(value, fallback):
    return parse_json_column(value, fallback)


def validate_agent_status(status, fallback="queued"):
    status = str(status or "").strip()
    return status if status in AGENT_RUN_STATUSES else fallback


def validate_agent_step_status(status, fallback="queued"):
    status = str(status or "").strip()
    return status if status in AGENT_STEP_STATUSES else fallback


def validate_agent_step_key(step_key):
    step_key = str(step_key or "").strip()
    if step_key not in AGENT_STEP_INDEX_BY_KEY:
        raise ValueError("不支持的 Agent 步骤。")
    return step_key


def agent_step_name(step_key):
    return agent_localization.step_name(agent_project_language(), step_key)


def serialize_agent_run(row):
    if not row:
        return None
    summary = load_json_column(row.get("summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "run_id": row.get("run_id"),
        "requirement_id": row.get("requirement_id"),
        "requirement_uid": row.get("requirement_uid") or "",
        "requirement_title": row.get("requirement_title") or "",
        "status": row.get("status"),
        "current_step": row.get("current_step") or "",
        "suite_uid": row.get("suite_uid") or "",
        "summary": summary,
        "pipeline_version": int(summary.get("pipeline_version") or 1),
        "plan_generation": load_json_column(
            row.get("plan_generation_json"),
            {
                "coverage_profile": DEFAULT_COVERAGE_PROFILE,
                "coverage_prompt": COVERAGE_PROFILES[DEFAULT_COVERAGE_PROFILE]["template_prompt"],
                "prompt_customized": False,
            },
        ),
        "error": row.get("error") or "",
        "created_by": row.get("created_by") or "",
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def serialize_agent_step(row):
    if not row:
        return None
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "run_id": row.get("run_id"),
        "step_key": row.get("step_key"),
        "step_name": row.get("step_name") or agent_step_name(row.get("step_key")),
        "status": row.get("status"),
        "input": load_json_column(row.get("input_json"), {}),
        "output": load_json_column(row.get("output_json"), {}),
        "counts": load_json_column(row.get("counts_json"), {}),
        "error": row.get("error") or "",
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def serialize_agent_event(row):
    if not row:
        return None
    message = row.get("message") or ""
    payload = load_json_column(row.get("payload_json"), {})
    if isinstance(payload, dict) and payload.get("batched") and "text" not in payload:
        payload = {**payload, "text": message}
    return {
        "event_id": row.get("event_id"),
        "project_id": row.get("project_id"),
        "run_id": row.get("run_id"),
        "step_key": row.get("step_key") or "",
        "event_type": row.get("event_type"),
        "message": message,
        "payload": payload,
        "job_id": row.get("job_id") or "",
        "asset_id": row.get("asset_id"),
        "test_run_id": row.get("test_run_id") or "",
        "created_at": row.get("created_at"),
    }


def serialize_agent_attempt(row):
    if not row:
        return None
    status = row.get("status") or "queued"
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "attempt_id": row.get("attempt_id") or "",
        "run_id": row.get("run_id") or "",
        "step_key": row.get("step_key") or "",
        "attempt_no": int(row.get("attempt_no") or 1),
        "previous_attempt_id": row.get("previous_attempt_id") or "",
        "retry_flow_id": row.get("retry_flow_id") or "",
        "parent_attempt_id": row.get("parent_attempt_id") or "",
        "item_type": row.get("item_type") or "",
        "item_key": row.get("item_key") or "",
        "module_uid": row.get("module_uid") or "",
        "module_name": row.get("module_name") or "",
        "plan_filename": row.get("plan_filename") or "",
        "filename": row.get("filename") or "",
        "status": status,
        "outcome_type": row.get("outcome_type") or "",
        "verification_status": row.get("verification_status") or "",
        "job_id": row.get("job_id") or "",
        "test_run_id": row.get("test_run_id") or "",
        "result_id": row.get("result_id"),
        "asset_id": row.get("asset_id"),
        "revision_id": row.get("revision_id"),
        "source_asset_id": row.get("source_asset_id"),
        "error_type": row.get("error_type") or "",
        "error": row.get("error_message") or "",
        "error_stack": row.get("error_stack") or "",
        "input_snapshot": load_json_column(row.get("input_snapshot_json"), {}),
        "output_summary": load_json_column(row.get("output_summary_json"), {}),
        "artifact_refs": load_json_column(row.get("artifact_refs_json"), []),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "failed_at": row.get("finished_at") if status == "failed" else None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def validate_agent_attempt_status(status):
    status = str(status or "").strip()
    if status not in AGENT_ATTEMPT_STATUSES:
        raise ValueError("不支持的 Agent 项目状态。")
    return status


def classify_agent_attempt_error(error):
    message = str(error or "").lower()
    if isinstance(error, OpencodeTaskCancelled) or "取消" in message or "cancel" in message:
        return "cancelled"
    if "超时" in message or "timeout" in message or "timed out" in message:
        return "timeout"
    if "数据库" in message or "baseline" in message:
        return "environment"
    if "产物" in message or "artifact" in message or "未找到目标" in message or "未生成目标" in message:
        return "artifact"
    if "playwright" in message or "脚本执行" in message or "退出码" in message:
        return "execution"
    if "工具" in message or "tool" in message:
        return "tool"
    if "opencode" in message or "agent" in message:
        return "agent"
    return "unknown"


def start_agent_attempt(
    run_id,
    step_key,
    item_type,
    item_key,
    *,
    module_uid=None,
    module_name=None,
    plan_filename=None,
    filename=None,
    input_snapshot=None,
    attempt_id=None,
    started_at=None,
    retry_flow_id=None,
    parent_attempt_id=None,
):
    config = require_platform_database()
    table = get_agent_run_attempts_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    step_key = validate_agent_step_key(step_key)
    item_key = str(item_key or "").strip()[:512]
    if not item_key:
        raise ValueError("Agent 项目标识不能为空。")
    item_type = str(item_type or "item").strip()[:32] or "item"
    attempt_id = validate_uid(attempt_id, "attempt_id") if attempt_id else f"attempt-{uuid.uuid4().hex}"
    existing = get_agent_attempt(run_id, attempt_id)
    if existing:
        return existing
    now_ms = current_time_ms()
    started_at = int(started_at or now_ms)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT attempt_id, attempt_no
                FROM {table}
                WHERE project_id = %s AND run_id = %s AND step_key = %s AND item_key = %s
                ORDER BY attempt_no DESC, id DESC
                LIMIT 1
                """,
                (project_id, run_id, step_key, item_key),
            )
            previous = cursor.fetchone() or {}
            attempt_no = int(previous.get("attempt_no") or 0) + 1
            cursor.execute(
                f"""
                INSERT INTO {table}
                  (project_id, attempt_id, run_id, step_key, attempt_no, previous_attempt_id,
                   retry_flow_id, parent_attempt_id,
                   item_type, item_key, module_uid, module_name, plan_filename, filename,
                   status, outcome_type, verification_status, job_id, test_run_id, result_id,
                   asset_id, revision_id, source_asset_id, error_type, error_message, error_stack,
                   input_snapshot_json, output_summary_json, artifact_refs_json,
                   started_at, finished_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        'running', NULL, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL, NULL, NULL,
                        %s, NULL, NULL,
                        %s, NULL, %s, %s)
                """,
                (
                    project_id,
                    attempt_id,
                    run_id,
                    step_key,
                    attempt_no,
                    previous.get("attempt_id") or None,
                    str(retry_flow_id or "")[:64] or None,
                    str(parent_attempt_id or "")[:64] or None,
                    item_type,
                    item_key,
                    str(module_uid or "")[:64] or None,
                    str(module_name or "")[:255] or None,
                    str(plan_filename or "")[:255] or None,
                    str(filename or "")[:255] or None,
                    compact_json_dumps(input_snapshot or {}),
                    started_at,
                    now_ms,
                    now_ms,
                ),
            )
        connection.commit()
    return get_agent_attempt(run_id, attempt_id)


def finish_agent_attempt(
    run_id,
    attempt_id,
    status,
    *,
    outcome_type=None,
    verification_status=None,
    job_id=None,
    test_run_id=None,
    result_id=None,
    asset_id=None,
    revision_id=None,
    source_asset_id=None,
    error_type=None,
    error_message=None,
    error_stack=None,
    output_summary=None,
    artifact_refs=None,
    finished_at=None,
):
    config = require_platform_database()
    table = get_agent_run_attempts_table(config)
    project_id = get_current_project_id()
    status = validate_agent_attempt_status(status)
    now_ms = current_time_ms()
    finished_at = int(finished_at or now_ms)
    fields = ["status = %s", "finished_at = %s", "updated_at = %s"]
    values = [status, finished_at, now_ms]
    optional_values = {
        "outcome_type": str(outcome_type or "")[:32] or None,
        "verification_status": str(verification_status or "")[:32] or None,
        "job_id": str(job_id or "")[:64] or None,
        "test_run_id": str(test_run_id or "")[:64] or None,
        "result_id": result_id,
        "asset_id": asset_id,
        "revision_id": revision_id,
        "source_asset_id": source_asset_id,
        "error_type": str(error_type or "")[:32] or None,
        "error_message": str(error_message or "") or None,
        "error_stack": str(error_stack or "") or None,
    }
    for field, value in optional_values.items():
        if value is not None:
            fields.append(f"{field} = %s")
            values.append(value)
    if output_summary is not None:
        fields.append("output_summary_json = %s")
        values.append(compact_json_dumps(output_summary))
    if artifact_refs is not None:
        fields.append("artifact_refs_json = %s")
        values.append(compact_json_dumps(artifact_refs))
    values.extend([project_id, validate_uid(run_id, "run_id"), validate_uid(attempt_id, "attempt_id")])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET {', '.join(fields)} WHERE project_id = %s AND run_id = %s AND attempt_id = %s",
                values,
            )
        connection.commit()
    return get_agent_attempt(run_id, attempt_id)


def get_agent_attempt(run_id, attempt_id):
    config = require_platform_database()
    table = get_agent_run_attempts_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s AND attempt_id = %s",
                (project_id, validate_uid(run_id, "run_id"), validate_uid(attempt_id, "attempt_id")),
            )
            return cursor.fetchone()


def list_agent_attempts(run_id, step_key=None):
    config = require_platform_database()
    table = get_agent_run_attempts_table(config)
    project_id = get_current_project_id()
    params = [project_id, validate_uid(run_id, "run_id")]
    step_clause = ""
    if step_key:
        step_clause = " AND step_key = %s"
        params.append(validate_agent_step_key(step_key))
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s AND run_id = %s{step_clause}
                ORDER BY started_at ASC, id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()


def serialize_agent_item_retry_flow(row):
    if not row:
        return None
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "retry_flow_id": row.get("retry_flow_id") or "",
        "run_id": row.get("run_id") or "",
        "root_attempt_id": row.get("root_attempt_id") or "",
        "item_type": row.get("item_type") or "script",
        "item_key": row.get("item_key") or "",
        "module_name": row.get("module_name") or "",
        "plan_filename": row.get("plan_filename") or "",
        "filename": row.get("filename") or "",
        "status": row.get("status") or "queued",
        "current_phase": row.get("current_phase") or "queued",
        "progress_message": row.get("progress_message") or "",
        "auto_repair": bool(row.get("auto_repair")),
        "generation_attempt_id": row.get("generation_attempt_id") or "",
        "execution_attempt_id": row.get("execution_attempt_id") or "",
        "repair_attempt_id": row.get("repair_attempt_id") or "",
        "verification_attempt_id": row.get("verification_attempt_id") or "",
        "result": load_json_column(row.get("result_json"), {}),
        "error": row.get("error") or "",
        "cancel_requested": bool(row.get("cancel_requested")),
        "created_by": row.get("created_by") or "",
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "acknowledged_at": row.get("acknowledged_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def validate_agent_item_retry_status(status):
    status = str(status or "").strip()
    if status not in AGENT_ITEM_RETRY_STATUSES:
        raise ValueError("不支持的单项重试状态。")
    return status


def validate_agent_item_retry_phase(phase):
    phase = str(phase or "").strip()
    if phase not in AGENT_ITEM_RETRY_PHASES:
        raise ValueError("不支持的单项重试阶段。")
    return phase


def get_agent_item_retry_flow(run_id, retry_flow_id):
    config = require_platform_database()
    table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s AND retry_flow_id = %s",
                (
                    project_id,
                    validate_uid(run_id, "run_id"),
                    validate_uid(retry_flow_id, "retry_flow_id"),
                ),
            )
            return cursor.fetchone()


def list_agent_item_retry_flows(run_id=None, active_only=False, limit=200):
    config = require_platform_database()
    table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    clauses = ["project_id = %s"]
    params = [project_id]
    if run_id:
        clauses.append("run_id = %s")
        params.append(validate_uid(run_id, "run_id"))
    if active_only:
        clauses.append("status IN ('queued', 'running', 'finalizing', 'cancelling')")
    params.append(min(max(int(limit or 200), 1), 1000))
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return cursor.fetchall()


def get_active_agent_item_retry_flow(run_id, item_key):
    config = require_platform_database()
    table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s AND run_id = %s AND active_item_key = %s
                  AND status IN ('queued', 'running', 'finalizing', 'cancelling')
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_id, validate_uid(run_id, "run_id"), str(item_key or "")[:512]),
            )
            return cursor.fetchone()


def create_agent_item_retry_flow(run_id, root_attempt, auto_repair=True, created_by=None):
    config = require_platform_database()
    table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    root_attempt = root_attempt if isinstance(root_attempt, dict) else {}
    root_attempt_id = validate_uid(root_attempt.get("attempt_id"), "attempt_id")
    item_key = str(root_attempt.get("item_key") or "").strip()[:512]
    if not item_key:
        raise ValueError("失败记录缺少项目标识，不能单项重试。")

    # The process lock avoids duplicate workers in one server process. The unique
    # active_item_key index is the cross-process/idempotency backstop.
    with AGENT_PROJECT_OPERATION_LOCK, AGENT_ITEM_RETRY_TASK_LOCK:
        active_run = get_active_agent_run_row()
        if active_run:
            raise AgentItemRetryConflict("当前项目有 Agent 主任务正在运行。")
        active_flows = list_agent_item_retry_flows(active_only=True, limit=1)
        if active_flows:
            existing = active_flows[0]
            if existing.get("run_id") == run_id and existing.get("item_key") == item_key:
                return existing, False
            raise AgentItemRetryConflict("当前项目已有脚本正在重试并验证。", existing)

        retry_flow_id = f"retry-{uuid.uuid4().hex}"
        now_ms = current_time_ms()
        try:
            with platform_mysql_connection(config) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {table}
                          (project_id, retry_flow_id, run_id, root_attempt_id, item_type, item_key,
                           active_item_key, module_name, plan_filename, filename, status, current_phase,
                           progress_message, auto_repair, result_json, error, cancel_requested, created_by,
                           started_at, finished_at, acknowledged_at, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, 'queued', 'queued',
                                %s, %s, %s, '', 0, %s,
                                NULL, NULL, NULL, %s, %s)
                        """,
                        (
                            project_id,
                            retry_flow_id,
                            run_id,
                            root_attempt_id,
                            str(root_attempt.get("item_type") or "script")[:32],
                            item_key,
                            item_key,
                            str(root_attempt.get("module_name") or "")[:255] or None,
                            str(root_attempt.get("plan_filename") or "")[:255] or None,
                            str(root_attempt.get("filename") or "")[:255] or None,
                            "等待重新生成脚本。",
                            1 if auto_repair else 0,
                            compact_json_dumps({"root_attempt_id": root_attempt_id}),
                            str(created_by or current_platform_author())[:255] or None,
                            now_ms,
                            now_ms,
                        ),
                    )
                connection.commit()
        except Exception:
            existing = get_active_agent_item_retry_flow(run_id, item_key)
            if existing:
                return existing, False
            raise
    return get_agent_item_retry_flow(run_id, retry_flow_id), True


def update_agent_item_retry_flow(run_id, retry_flow_id, *, expected_statuses=None, **updates):
    config = require_platform_database()
    table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    retry_flow_id = validate_uid(retry_flow_id, "retry_flow_id")
    allowed = {
        "status",
        "current_phase",
        "progress_message",
        "auto_repair",
        "generation_attempt_id",
        "execution_attempt_id",
        "repair_attempt_id",
        "verification_attempt_id",
        "result",
        "error",
        "cancel_requested",
        "acknowledged_at",
        "started_at",
        "finished_at",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"不支持的单项重试更新字段：{', '.join(sorted(unknown))}")
    if "status" in updates:
        updates["status"] = validate_agent_item_retry_status(updates["status"])
    if "current_phase" in updates:
        updates["current_phase"] = validate_agent_item_retry_phase(updates["current_phase"])
    if expected_statuses is not None:
        expected_statuses = {validate_agent_item_retry_status(item) for item in expected_statuses}
        if not expected_statuses:
            raise ValueError("单项重试条件状态不能为空。")

    fields = ["updated_at = %s"]
    values = [current_time_ms()]
    column_by_key = {
        "result": "result_json",
    }
    json_fields = {"result"}
    bool_fields = {"auto_repair", "cancel_requested"}
    for key, value in updates.items():
        column = column_by_key.get(key, key)
        if key in json_fields:
            value = compact_json_dumps(value if isinstance(value, dict) else {})
        elif key in bool_fields:
            value = 1 if value else 0
        elif key in {"generation_attempt_id", "execution_attempt_id", "repair_attempt_id", "verification_attempt_id"}:
            value = str(value or "")[:64] or None
        elif key in {"progress_message", "error"}:
            value = str(value or "")
        fields.append(f"{column} = %s")
        values.append(value)

    status = updates.get("status")
    if status == "running":
        fields.append("started_at = COALESCE(started_at, %s)")
        values.append(current_time_ms())
    if status and status not in AGENT_ITEM_RETRY_ACTIVE_STATUSES:
        fields.extend(["active_item_key = NULL", "finished_at = COALESCE(finished_at, %s)"])
        values.append(current_time_ms())

    values.extend([project_id, run_id, retry_flow_id])
    status_clause = ""
    if expected_statuses is not None:
        status_clause = f" AND status IN ({', '.join(['%s'] * len(expected_statuses))})"
        values.extend(sorted(expected_statuses))
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET {', '.join(fields)} "
                f"WHERE project_id = %s AND run_id = %s AND retry_flow_id = %s{status_clause}",
                tuple(values),
            )
        connection.commit()
    return get_agent_item_retry_flow(run_id, retry_flow_id)


def begin_agent_item_retry_finalization(run_id, retry_flow_id, *, current_phase, progress_message, result, error=""):
    flow = update_agent_item_retry_flow(
        run_id,
        retry_flow_id,
        expected_statuses={"queued", "running"},
        status="finalizing",
        current_phase=current_phase,
        progress_message=progress_message,
        result=result,
        error=error,
    )
    if flow and flow.get("status") == "finalizing":
        return flow
    if flow and (flow.get("cancel_requested") or flow.get("status") in {"cancelling", "cancelled"}):
        raise OpencodeTaskCancelled("单项重试已在结果写回前取消。")
    raise RuntimeError("单项重试状态已变化，无法开始写回最终结果。")


def complete_agent_item_retry_flow(
    run_id,
    retry_flow_id,
    status,
    *,
    current_phase,
    progress_message,
    result,
    error="",
    event_message=None,
    event_type="status",
    event_step_key=None,
    flow=None,
):
    if status in AGENT_ITEM_RETRY_ACTIVE_STATUSES:
        raise ValueError("完成单项重试时必须提供终态。")
    if event_message is not None:
        flow = terminalize_agent_item_retry_flow(
            run_id,
            retry_flow_id,
            status,
            expected_statuses={"finalizing"},
            current_phase=current_phase,
            progress_message=progress_message,
            result=result,
            error=error,
            event_message=event_message,
            event_type=event_type,
            event_step_key=event_step_key,
            flow=flow,
        )
    else:
        flow = update_agent_item_retry_flow(
            run_id,
            retry_flow_id,
            expected_statuses={"finalizing"},
            status=status,
            current_phase=current_phase,
            progress_message=progress_message,
            result=result,
            error=error,
        )
    if flow and flow.get("status") == status:
        return flow
    if flow and (flow.get("cancel_requested") or flow.get("status") in {"cancelling", "cancelled"}):
        raise OpencodeTaskCancelled("单项重试已取消。")
    raise RuntimeError("单项重试最终状态写入失败。")


def agent_attempt_failure_context(error):
    return {
        "job_id": getattr(error, "job_id", "") or "",
        "test_run_id": getattr(error, "test_run_id", "") or "",
        "result_id": getattr(error, "result_id", None),
        "asset_id": getattr(error, "asset_id", None),
        "error_type": getattr(error, "error_type", "") or classify_agent_attempt_error(error),
        "partial_artifacts": list(getattr(error, "partial_artifacts", []) or []),
    }


LEGACY_AGENT_FAILURE_STEP_CONFIG = {
    "generate_plans": {"item_type": "plan", "input_field": "modules"},
    "generate_scripts": {"item_type": "script", "input_field": "plans"},
    "execute_scripts": {"item_type": "script_execution", "input_field": "scripts"},
    "repair_scripts": {"item_type": "script_repair", "input_field": "scripts"},
}


def legacy_agent_failure_identity(step_key, item):
    config = LEGACY_AGENT_FAILURE_STEP_CONFIG.get(step_key)
    if not config:
        raise ValueError("该 Agent 阶段不支持历史失败诊断包。")
    item = item if isinstance(item, dict) else {}
    module_uid = str(item.get("module_uid") or "").strip()
    module_name = str(item.get("module_name") or "").strip()
    plan_filename = str(item.get("plan_filename") or "").strip()
    filename = str(item.get("filename") or "").strip()
    if step_key == "generate_plans":
        item_key = f"{module_name}/{module_uid or module_name}"
    elif step_key == "generate_scripts":
        item_key = f"{module_name}/{plan_filename or filename}"
        if not filename and plan_filename:
            filename = f"{Path(plan_filename).stem}.spec.ts"
    else:
        item_key = f"{module_name}/{filename or plan_filename}"
    if not module_name or not item_key.rstrip("/"):
        raise ValueError("历史失败记录缺少模块或文件标识。")
    return {
        "item_type": config["item_type"],
        "item_key": item_key[:512],
        "module_uid": module_uid,
        "module_name": module_name,
        "plan_filename": plan_filename,
        "filename": filename,
    }


def legacy_agent_failure_matches(item, selector):
    if not isinstance(item, dict):
        return False
    fields = ("module_uid", "module_name", "plan_filename", "filename")
    selected = {field: str(selector.get(field) or "").strip() for field in fields}
    if not any(selected.values()):
        return False
    for field, expected in selected.items():
        if expected and str(item.get(field) or "").strip() != expected:
            return False
    return True


def list_agent_step_job_events(run_id, step_key):
    config = require_platform_database()
    table = get_agent_run_events_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT event_id, job_id, message, payload_json, created_at
                FROM {table}
                WHERE project_id = %s AND run_id = %s AND step_key = %s AND job_id IS NOT NULL
                ORDER BY event_id DESC
                """,
                (project_id, validate_uid(run_id, "run_id"), validate_agent_step_key(step_key)),
            )
            return cursor.fetchall()


def find_legacy_agent_failure_job_event(run_id, step_key, failure, preferred_job_id=""):
    events = list_agent_step_job_events(run_id, step_key)
    preferred_job_id = str(preferred_job_id or failure.get("job_id") or "").strip()
    if preferred_job_id:
        preferred = next((event for event in events if event.get("job_id") == preferred_job_id), None)
        if preferred:
            return preferred

    identity = legacy_agent_failure_identity(step_key, failure)
    identifiers = [identity.get("plan_filename"), identity.get("filename")]
    identifiers = [item for item in identifiers if item]
    module_name = identity.get("module_name") or ""
    best_event = None
    best_score = 0
    for event in events:
        payload = load_json_column(event.get("payload_json"), {})
        searchable = "\n".join(
            [
                str(event.get("message") or ""),
                compact_json_dumps(payload),
            ]
        )
        score = sum(100 for identifier in identifiers if identifier in searchable)
        if module_name and module_name in searchable:
            score += 10
        if score > best_score:
            best_event = event
            best_score = score
    if identifiers and best_score < 100:
        return None
    return best_event if best_score else None


def create_legacy_agent_failure_attempt(run_id, selector):
    selector = selector if isinstance(selector, dict) else {}
    step_key = validate_agent_step_key(selector.get("step_key"))
    if step_key not in LEGACY_AGENT_FAILURE_STEP_CONFIG:
        raise ValueError("该 Agent 阶段不支持历史失败诊断包。")
    step_row = get_agent_step_row(run_id, step_key)
    if not step_row:
        raise FileNotFoundError("Agent 阶段记录不存在。")
    output = load_json_column(step_row.get("output_json"), {})
    failures = output.get("failures") if isinstance(output, dict) else None
    if not isinstance(failures, list):
        raise FileNotFoundError("该阶段没有可诊断的历史失败记录。")
    matching_indexes = [index for index, item in enumerate(failures) if legacy_agent_failure_matches(item, selector)]
    if not matching_indexes:
        raise FileNotFoundError("未找到对应的历史失败记录。")
    if len(matching_indexes) > 1:
        raise ValueError("历史失败记录标识不唯一，无法安全生成诊断包。")

    failure_index = matching_indexes[0]
    failure = dict(failures[failure_index])
    if failure.get("attempt_id") or failure.get("failure_id"):
        return str(failure.get("attempt_id") or failure.get("failure_id"))

    identity = legacy_agent_failure_identity(step_key, failure)
    event = find_legacy_agent_failure_job_event(run_id, step_key, failure, selector.get("job_id"))
    job_id = str((event or {}).get("job_id") or failure.get("job_id") or "").strip()
    failed_at = int(failure.get("failed_at") or (event or {}).get("created_at") or step_row.get("finished_at") or current_time_ms())
    attempt_seed = f"{get_current_project_id()}:{run_id}:{step_key}:{identity['item_key']}:failed"
    attempt_id = f"attempt-legacy-{uuid.uuid5(uuid.NAMESPACE_URL, attempt_seed).hex}"

    step_input = load_json_column(step_row.get("input_json"), {})
    input_items = step_input.get(LEGACY_AGENT_FAILURE_STEP_CONFIG[step_key]["input_field"], []) if isinstance(step_input, dict) else []
    input_snapshot = next(
        (item for item in input_items if legacy_agent_failure_matches(item, failure)),
        failure,
    )
    job_row = get_test_job(job_id) if job_id else None
    started_at = int((job_row or {}).get("created_at") or step_row.get("started_at") or failed_at)
    error_message = str(failure.get("error") or step_row.get("error") or "历史 Agent 项目失败。")
    error_type = str(failure.get("error_type") or classify_agent_attempt_error(error_message))
    raw_partial_artifacts = failure.get("partial_artifacts") or []
    if isinstance(raw_partial_artifacts, str):
        raw_partial_artifacts = [raw_partial_artifacts]
    partial_artifacts = [str(path) for path in raw_partial_artifacts if str(path or "").strip()]
    failure.update(
        {
            "attempt_id": attempt_id,
            "failure_id": attempt_id,
            "job_id": job_id,
            "error_type": error_type,
            "failed_at": failed_at,
            "partial_artifacts": partial_artifacts,
        }
    )

    attempt = get_agent_attempt(run_id, attempt_id)
    if not attempt:
        start_agent_attempt(
            run_id,
            step_key,
            identity["item_type"],
            identity["item_key"],
            module_uid=identity["module_uid"],
            module_name=identity["module_name"],
            plan_filename=identity["plan_filename"],
            filename=identity["filename"],
            input_snapshot=input_snapshot,
            attempt_id=attempt_id,
            started_at=started_at,
        )
        asset = failure.get("asset") if isinstance(failure.get("asset"), dict) else {}
        is_source_asset = step_key == "generate_scripts"
        finish_agent_attempt(
            run_id,
            attempt_id,
            "failed",
            verification_status="failed" if step_key in {"execute_scripts", "repair_scripts"} else None,
            job_id=job_id,
            test_run_id=failure.get("test_run_id") or failure.get("execution_run_id"),
            result_id=failure.get("result_id"),
            asset_id=None if is_source_asset else asset.get("asset_id"),
            revision_id=None if is_source_asset else asset.get("current_revision_id"),
            source_asset_id=asset.get("asset_id") if is_source_asset else asset.get("from_plan_asset_id"),
            error_type=error_type,
            error_message=error_message,
            output_summary=failure,
            artifact_refs=[{"source": "partial", "path": path} for path in partial_artifacts],
            finished_at=failed_at,
        )

    failures[failure_index] = failure
    output["failures"] = failures
    update_agent_step(run_id, step_key, output_data=output)
    return attempt_id


DIAGNOSTIC_SENSITIVE_KEY_PATTERN = (
    agent_diagnostics.DIAGNOSTIC_SENSITIVE_KEY_PATTERN
)
collect_diagnostic_secret_values = (
    agent_diagnostics.collect_diagnostic_secret_values
)
normalize_diagnostic_member_name = (
    agent_diagnostics.normalize_diagnostic_member_name
)
diagnostic_safe_filename = agent_diagnostics.diagnostic_safe_filename


def _diagnostic_builder_dependencies():
    return agent_diagnostics.DiagnosticBuilderDependencies(
        get_current_project=lambda: get_current_project(),
        get_platform_database_config=lambda: get_platform_database_config(),
        redact_sensitive_text=lambda *args, **kwargs: redact_sensitive_text(
            *args,
            **kwargs,
        ),
        get_project_root=lambda: get_project_root(),
        get_home_path=lambda: Path.home(),
        text_file_max_bytes=DIAGNOSTIC_TEXT_FILE_MAX_BYTES,
        bundle_max_bytes=DIAGNOSTIC_BUNDLE_MAX_BYTES,
    )


def _agent_diagnostic_dependencies():
    return agent_diagnostics.AgentDiagnosticDependencies(
        builder=_diagnostic_builder_dependencies(),
        load_json_column=lambda *args, **kwargs: load_json_column(
            *args,
            **kwargs,
        ),
        get_requirement_by_uid=lambda value: get_requirement_by_uid(value, True),
        read_requirement_markdown=lambda value: read_requirement_markdown(value),
        get_plan_target_path=lambda *args, **kwargs: get_plan_target_path(
            *args,
            **kwargs,
        ),
        get_script_file=lambda *args, **kwargs: get_script_file(
            *args,
            **kwargs,
        ),
        get_asset_revision=lambda *args, **kwargs: get_asset_revision(
            *args,
            **kwargs,
        ),
        git_show_file=lambda *args, **kwargs: git_show_file(*args, **kwargs),
        git_diff_file=lambda *args, **kwargs: git_diff_file(*args, **kwargs),
        list_job_artifacts=lambda value: list_job_artifacts(value),
        list_run_artifacts=lambda *args, **kwargs: list_run_artifacts(
            *args,
            **kwargs,
        ),
        serialize_run_artifact_payload=lambda value: (
            serialize_run_artifact_payload(value)
        ),
        get_agent_run_row=lambda value: get_agent_run_row(value),
        get_agent_attempt=lambda *args, **kwargs: get_agent_attempt(
            *args,
            **kwargs,
        ),
        serialize_agent_run=lambda value: serialize_agent_run(value),
        serialize_agent_attempt=lambda value: serialize_agent_attempt(value),
        get_agent_step_row=lambda *args, **kwargs: get_agent_step_row(
            *args,
            **kwargs,
        ),
        serialize_agent_step=lambda value: serialize_agent_step(value),
        get_test_job=lambda value: get_test_job(value),
        serialize_job=lambda value: serialize_job(value),
        agent_step_name=lambda value: agent_step_name(value),
        list_agent_events=lambda *args, **kwargs: list_agent_events(
            *args,
            **kwargs,
        ),
        serialize_agent_event=lambda value: serialize_agent_event(value),
        get_job_log_path=lambda value: get_job_log_path(value),
        get_test_run=lambda value: get_test_run(value),
        serialize_test_suite_execution_run=lambda value: (
            serialize_test_suite_execution_run(value)
        ),
        get_run_result=lambda value: get_run_result(value),
        serialize_run_result=lambda value: serialize_run_result(value),
        get_git_head_sha=lambda: get_git_head_sha(),
        current_time_ms=lambda: current_time_ms(),
        platform_version=lambda: platform.platform(),
        python_version=sys.version,
        run_process=lambda *args, **kwargs: subprocess.run(*args, **kwargs),
        format_timestamp=lambda value: time.strftime(value),
        bundle_format_version=DIAGNOSTIC_BUNDLE_FORMAT_VERSION,
        playwright_config_filenames=PLAYWRIGHT_CONFIG_FILENAMES,
    )


def diagnostic_redaction_context():
    return agent_diagnostics.diagnostic_redaction_context(
        _diagnostic_builder_dependencies()
    )


def redact_diagnostic_text(value, *, limit=None, context=None):
    return agent_diagnostics.redact_diagnostic_text(
        value,
        dependencies=_diagnostic_builder_dependencies(),
        limit=limit,
        context=context,
    )


def redact_diagnostic_value(value, *, context=None, key=""):
    return agent_diagnostics.redact_diagnostic_value(
        value,
        dependencies=_diagnostic_builder_dependencies(),
        context=context,
        key=key,
    )


class DiagnosticBundleBuilder(agent_diagnostics.DiagnosticBundleBuilder):
    def __init__(self, *, redaction_context=None):
        super().__init__(
            _diagnostic_builder_dependencies(),
            redaction_context=redaction_context,
        )


def diagnostic_event_matches_attempt(event, attempt):
    return agent_diagnostics.diagnostic_event_matches_attempt(
        event,
        attempt,
        _agent_diagnostic_dependencies(),
    )


def diagnostic_source_snapshot(builder, attempt, run, step):
    return agent_diagnostics.diagnostic_source_snapshot(
        builder,
        attempt,
        run,
        step,
        _agent_diagnostic_dependencies(),
    )


def collect_diagnostic_artifacts(builder, attempt):
    return agent_diagnostics.collect_diagnostic_artifacts(
        builder,
        attempt,
        _agent_diagnostic_dependencies(),
    )


def build_agent_attempt_diagnostic_bundle(run_id, attempt_id):
    return agent_diagnostics.build_agent_attempt_diagnostic_bundle(
        run_id,
        attempt_id,
        _agent_diagnostic_dependencies(),
    )


def get_agent_run_row(run_id):
    config = require_platform_database()
    table = get_agent_runs_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s",
                (project_id, validate_uid(run_id, "run_id")),
            )
            return cursor.fetchone()


def list_agent_run_rows(limit=None):
    config = require_platform_database()
    table = get_agent_runs_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            query = f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s
                ORDER BY created_at DESC
            """
            params = [project_id]
            if limit is not None:
                query += " LIMIT %s"
                params.append(max(1, int(limit)))
            cursor.execute(query, tuple(params))
            return cursor.fetchall()


def get_active_agent_run_row():
    config = require_platform_database()
    table = get_agent_runs_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s
                  AND status IN ('queued', 'running', 'cancelling', 'awaiting_script_action')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id,),
            )
            return cursor.fetchone()


def create_agent_run(requirement, created_by, plan_generation=None):
    with AGENT_PROJECT_OPERATION_LOCK:
        active_run = get_active_agent_run_row()
        if active_run:
            raise AgentItemRetryConflict("当前项目已有 Agent 任务正在运行。")
        active_retry_flows = list_agent_item_retry_flows(active_only=True, limit=1)
        if active_retry_flows:
            raise AgentItemRetryConflict("当前项目有脚本正在重试并验证。", active_retry_flows[0])
        return create_agent_run_record(requirement, created_by, plan_generation=plan_generation)


def create_agent_run_record(requirement, created_by, plan_generation=None):
    config = require_platform_database()
    runs_table = get_agent_runs_table(config)
    steps_table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    run_id = f"agent-{uuid.uuid4().hex}"
    now_ms = current_time_ms()
    plan_generation = normalize_plan_generation_request(plan_generation or {})
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {runs_table}
                  (project_id, run_id, requirement_id, requirement_uid, requirement_title, status, current_step,
                   suite_uid, summary_json, plan_generation_json, error, created_by,
                   started_at, finished_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'queued', 'upload_requirement',
                        NULL, %s, %s, NULL, %s, NULL, NULL, %s, %s)
                """,
                (
                    project_id,
                    run_id,
                    requirement.get("id"),
                    requirement.get("requirement_uid"),
                    requirement.get("title") or requirement.get("filename") or "",
                    compact_json_dumps(
                        {
                            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
                            "language": get_current_project_language(),
                        }
                    ),
                    compact_json_dumps(plan_generation),
                    created_by,
                    now_ms,
                    now_ms,
                ),
            )
            for step_key, _step_name in AGENT_STEP_ORDER:
                step_name = agent_step_name(step_key)
                cursor.execute(
                    f"""
                    INSERT INTO {steps_table}
                      (project_id, run_id, step_key, step_name, status, input_json, output_json, counts_json,
                       error, started_at, finished_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'queued', NULL, NULL, NULL, NULL, NULL, NULL, %s, %s)
                    """,
                    (project_id, run_id, step_key, step_name, now_ms, now_ms),
                )
        connection.commit()
    append_agent_event(
        run_id,
        "upload_requirement",
        "status",
        agent_message("task_created"),
        {"status": "queued"},
    )
    return get_agent_run_row(run_id)


def ensure_agent_run_step_rows(run_id):
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT step_key
                FROM {table}
                WHERE project_id = %s AND run_id = %s
                """,
                (project_id, run_id),
            )
            existing = {row.get("step_key") for row in cursor.fetchall()}
            for step_key, _step_name in AGENT_STEP_ORDER:
                if step_key in existing:
                    continue
                step_name = agent_step_name(step_key)
                cursor.execute(
                    f"""
                    INSERT INTO {table}
                      (project_id, run_id, step_key, step_name, status, input_json, output_json, counts_json,
                       error, started_at, finished_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'queued', NULL, NULL, NULL, '', NULL, NULL, %s, %s)
                    """,
                    (project_id, run_id, step_key, step_name, now_ms, now_ms),
                )
        connection.commit()


def update_agent_run(run_id, status=None, current_step=None, suite_uid=None, summary=None, error=None, finished=False, reopened=False):
    config = require_platform_database()
    table = get_agent_runs_table(config)
    project_id = get_current_project_id()
    fields = ["updated_at = %s"]
    values = [current_time_ms()]
    if status is not None:
        fields.append("status = %s")
        values.append(validate_agent_status(status))
        if status == "running":
            fields.append("started_at = COALESCE(started_at, %s)")
            values.append(current_time_ms())
    if current_step is not None:
        fields.append("current_step = %s")
        values.append(str(current_step or "")[:64])
    if suite_uid is not None:
        fields.append("suite_uid = %s")
        values.append(str(suite_uid or "")[:64] or None)
    if summary is not None:
        fields.append("summary_json = %s")
        values.append(compact_json_dumps(summary))
    if error is not None:
        fields.append("error = %s")
        values.append(str(error or ""))
    if finished:
        fields.append("finished_at = %s")
        values.append(current_time_ms())
    elif reopened:
        fields.append("finished_at = NULL")
    values.extend([project_id, validate_uid(run_id, "run_id")])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE project_id = %s AND run_id = %s", values)
        connection.commit()
    return get_agent_run_row(run_id)


def update_agent_step(run_id, step_key, status=None, input_data=None, output_data=None, counts=None, error=None, started=False, finished=False, reopened=False):
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    fields = ["updated_at = %s"]
    values = [current_time_ms()]
    if status is not None:
        fields.append("status = %s")
        values.append(validate_agent_step_status(status))
    if input_data is not None:
        fields.append("input_json = %s")
        values.append(compact_json_dumps(input_data))
    if output_data is not None:
        fields.append("output_json = %s")
        values.append(compact_json_dumps(output_data))
    if counts is not None:
        fields.append("counts_json = %s")
        values.append(compact_json_dumps(counts))
    if error is not None:
        fields.append("error = %s")
        values.append(str(error or ""))
    if started:
        fields.append("started_at = COALESCE(started_at, %s)")
        values.append(current_time_ms())
    if finished:
        fields.append("finished_at = %s")
        values.append(current_time_ms())
    elif reopened:
        fields.append("finished_at = NULL")
    values.extend([project_id, validate_uid(run_id, "run_id"), str(step_key or "")[:64]])
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET {', '.join(fields)} WHERE project_id = %s AND run_id = %s AND step_key = %s",
                values,
            )
        connection.commit()


def list_agent_steps(run_id):
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s AND run_id = %s
                ORDER BY FIELD(step_key, {','.join(['%s'] * len(AGENT_STEP_ORDER))}), id ASC
                """,
                (project_id, validate_uid(run_id, "run_id"), *[step[0] for step in AGENT_STEP_ORDER]),
            )
            return cursor.fetchall()


def get_agent_step_row(run_id, step_key):
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s AND run_id = %s AND step_key = %s
                """,
                (project_id, validate_uid(run_id, "run_id"), validate_agent_step_key(step_key)),
            )
            return cursor.fetchone()


def get_agent_step_output(run_id, step_key):
    step_key = validate_agent_step_key(step_key)
    row = get_agent_step_row(run_id, step_key)
    if not row:
        raise RuntimeError(f"恢复任务缺少步骤记录：{agent_step_name(step_key)}。")
    if row.get("status") != "succeeded":
        raise RuntimeError(f"恢复任务需要先完成步骤：{agent_step_name(step_key)}。")
    output = load_json_column(row.get("output_json"), {})
    if not isinstance(output, dict):
        raise RuntimeError(f"步骤输出格式无效：{agent_step_name(step_key)}。")
    return output


def get_optional_agent_step_output(run_id, step_key):
    step_key = validate_agent_step_key(step_key)
    row = get_agent_step_row(run_id, step_key)
    if not row or row.get("status") != "succeeded":
        return None
    output = load_json_column(row.get("output_json"), {})
    return output if isinstance(output, dict) else None


def agent_retry_item_identity(item):
    item = item if isinstance(item, dict) else {}
    module_name = str(item.get("module_name") or "").strip()
    plan_filename = str(item.get("plan_filename") or "").strip()
    filename = str(item.get("filename") or "").strip()
    return module_name, plan_filename or filename


def agent_retry_flow_matches_item(flow, item):
    flow = flow if isinstance(flow, dict) else {}
    item = item if isinstance(item, dict) else {}
    if flow.get("retry_flow_id") and item.get("retry_flow_id") == flow.get("retry_flow_id"):
        return True
    module_name = str(flow.get("module_name") or "").strip()
    item_module_name = str(item.get("module_name") or "").strip()
    if module_name and item_module_name and module_name != item_module_name:
        return False
    plan_filename = str(flow.get("plan_filename") or "").strip()
    filename = str(flow.get("filename") or "").strip()
    item_plan_filename = str(item.get("plan_filename") or "").strip()
    item_filename = str(item.get("filename") or "").strip()
    if plan_filename and item_plan_filename:
        return plan_filename == item_plan_filename
    if filename and item_filename:
        return filename == item_filename
    if plan_filename and item_filename:
        try:
            return get_generated_script_filename_from_plan_filename(plan_filename) == item_filename
        except Exception:
            return False
    if filename and item_plan_filename:
        try:
            return filename == get_generated_script_filename_from_plan_filename(item_plan_filename)
        except Exception:
            return False
    return bool(flow.get("item_key") and item.get("item_key") == flow.get("item_key"))


def agent_retry_resolved_failure(failure, flow):
    return {
        **(failure if isinstance(failure, dict) else {}),
        "resolved_by_retry_flow_id": flow.get("retry_flow_id") or "",
        "resolved_at": current_time_ms(),
        "resolution_status": "resolved",
    }


def append_unique_agent_resolved_failure(resolved_failures, failure):
    identifier = str(
        failure.get("attempt_id")
        or failure.get("failure_id")
        or f"{agent_retry_item_identity(failure)}:{failure.get('failed_at')}:{failure.get('error')}"
    )
    if any(
        str(
            item.get("attempt_id")
            or item.get("failure_id")
            or f"{agent_retry_item_identity(item)}:{item.get('failed_at')}:{item.get('error')}"
        )
        == identifier
        for item in resolved_failures
        if isinstance(item, dict)
    ):
        return
    resolved_failures.append(failure)


def recalculate_agent_retry_step_counts(step_key, output, existing_counts=None):
    scripts = output.get("scripts") if isinstance(output.get("scripts"), list) else []
    failures = output.get("failures") if isinstance(output.get("failures"), list) else []
    retrying = output.get("retrying") if isinstance(output.get("retrying"), list) else []
    resolved = output.get("resolved_failures") if isinstance(output.get("resolved_failures"), list) else []
    identities = {
        agent_retry_item_identity(item)
        for item in [*scripts, *failures, *retrying]
        if isinstance(item, dict)
    }
    counts = dict(existing_counts or {})
    if step_key == "generate_scripts":
        counts.update(
            {
                "generated": len(scripts),
                "failed": len(failures),
                "retrying": len(retrying),
                "resolved": len(resolved),
                "plans": max(int(counts.get("plans") or 0), len(identities)),
            }
        )
    elif step_key == "execute_scripts":
        counts.update(
            {
                "passed": len(scripts),
                "failed": len(failures),
                "retrying": len(retrying),
                "resolved": len(resolved),
                "scripts": len(identities),
            }
        )
    elif step_key == "repair_scripts":
        counts.update(
            {
                "repaired": len(scripts),
                "failed": len(failures),
                "retrying": len(retrying),
                "resolved": len(resolved),
                "scripts": len(identities),
            }
        )
    return counts


def merge_agent_retry_step_result(
    run_id,
    step_key,
    flow,
    state,
    *,
    script_item=None,
    failure_item=None,
    remove_matching_script=False,
):
    if step_key not in {"generate_scripts", "execute_scripts", "repair_scripts"}:
        raise ValueError("该阶段不支持单项重试结果合并。")
    if state not in {"retrying", "produced", "succeeded", "failed", "blocked", "cancelled"}:
        raise ValueError("不支持的单项重试合并状态。")
    flow = serialize_agent_item_retry_flow(flow) if flow and "result_json" in flow else dict(flow or {})
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    step_key = validate_agent_step_key(step_key)

    with AGENT_RETRY_STEP_MERGE_LOCK:
        with platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT output_json, counts_json
                    FROM {table}
                    WHERE project_id = %s AND run_id = %s AND step_key = %s
                    FOR UPDATE
                    """,
                    (project_id, run_id, step_key),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                output = load_json_column(row.get("output_json"), {})
                output = output if isinstance(output, dict) else {}
                scripts = list(output.get("scripts") if isinstance(output.get("scripts"), list) else [])
                failures = list(output.get("failures") if isinstance(output.get("failures"), list) else [])
                retrying = list(output.get("retrying") if isinstance(output.get("retrying"), list) else [])
                resolved_failures = list(
                    output.get("resolved_failures") if isinstance(output.get("resolved_failures"), list) else []
                )

                retrying = [item for item in retrying if not agent_retry_flow_matches_item(flow, item)]
                marker = {
                    "retry_flow_id": flow.get("retry_flow_id") or "",
                    "root_attempt_id": flow.get("root_attempt_id") or "",
                    "item_key": flow.get("item_key") or "",
                    "module_name": flow.get("module_name") or "",
                    "plan_filename": flow.get("plan_filename") or "",
                    "filename": flow.get("filename") or "",
                    "retry_status": flow.get("status") or "running",
                    "retry_phase": flow.get("current_phase") or "queued",
                    "progress_message": flow.get("progress_message") or "",
                    "updated_at": flow.get("updated_at") or current_time_ms(),
                }

                if state in {"retrying", "produced"}:
                    retrying.append(marker)

                if state in {"produced", "succeeded"}:
                    matching_failures = [item for item in failures if agent_retry_flow_matches_item(flow, item)]
                    failures = [item for item in failures if not agent_retry_flow_matches_item(flow, item)]
                    for failure in matching_failures:
                        append_unique_agent_resolved_failure(
                            resolved_failures,
                            agent_retry_resolved_failure(failure, flow),
                        )

                if remove_matching_script:
                    scripts = [item for item in scripts if not agent_retry_flow_matches_item(flow, item)]

                if script_item is not None:
                    scripts = [item for item in scripts if not agent_retry_flow_matches_item(flow, item)]
                    scripts.append(
                        {
                            **script_item,
                            "retry_flow_id": flow.get("retry_flow_id") or "",
                            "retry_status": flow.get("status") or state,
                            "retry_phase": flow.get("current_phase") or "",
                        }
                    )

                if state in {"failed", "blocked"} and failure_item is not None:
                    failures = [item for item in failures if not agent_retry_flow_matches_item(flow, item)]
                    failures.append(
                        {
                            **failure_item,
                            "retry_flow_id": flow.get("retry_flow_id") or "",
                            "retry_status": flow.get("status") or state,
                            "retry_phase": flow.get("current_phase") or "",
                        }
                    )

                output.update(
                    {
                        "scripts": scripts,
                        "failures": failures,
                        "resolved_failures": resolved_failures,
                        "retrying": retrying,
                    }
                )
                counts = recalculate_agent_retry_step_counts(
                    step_key,
                    output,
                    load_json_column(row.get("counts_json"), {}),
                )
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET output_json = %s, counts_json = %s, updated_at = %s
                    WHERE project_id = %s AND run_id = %s AND step_key = %s
                    """,
                    (
                        compact_json_dumps(output),
                        compact_json_dumps(counts),
                        current_time_ms(),
                        project_id,
                        run_id,
                        step_key,
                    ),
                )
            connection.commit()
    return {"output": output, "counts": counts}


def clear_agent_retry_step_markers(run_id, flow, step_keys=None):
    for step_key in step_keys or ("generate_scripts", "execute_scripts", "repair_scripts"):
        try:
            merge_agent_retry_step_result(run_id, step_key, flow, "cancelled")
        except Exception:
            continue


def require_agent_step_list_output(run_id, step_key, field_name):
    output = get_agent_step_output(run_id, step_key)
    value = output.get(field_name)
    if not isinstance(value, list):
        raise RuntimeError(f"步骤 {agent_step_name(step_key)} 缺少可恢复的 {field_name} 输出。")
    return value


def get_agent_plan_resume_output(run_id):
    row = get_agent_step_row(run_id, "generate_plans")
    if not row:
        return None
    output = load_json_column(row.get("output_json"), {})
    if not isinstance(output, dict) or not isinstance(output.get("failures"), list) or not output.get("failures"):
        return None
    return {
        "plans": output.get("plans") if isinstance(output.get("plans"), list) else [],
        "failures": output["failures"],
        "skipped": output.get("skipped") if isinstance(output.get("skipped"), list) else [],
    }


def resolve_agent_resume_step(run_id, requested_step):
    requested_step = validate_agent_step_key(requested_step)
    if AGENT_STEP_INDEX_BY_KEY[requested_step] > AGENT_STEP_INDEX_BY_KEY["generate_plans"]:
        if get_agent_plan_resume_output(run_id):
            return "generate_plans"
    return requested_step


def reset_agent_run_for_resume(run_id, from_step):
    with AGENT_PROJECT_OPERATION_LOCK:
        active_retry_flows = list_agent_item_retry_flows(active_only=True, limit=1)
        if active_retry_flows:
            raise AgentItemRetryConflict("当前项目有脚本正在重试并验证。", active_retry_flows[0])
        active_run = get_active_agent_run_row()
        if active_run and active_run.get("run_id") != run_id:
            raise AgentItemRetryConflict("当前项目已有 Agent 任务正在运行。")
        return reset_agent_run_for_resume_record(run_id, from_step)


def reset_agent_run_for_resume_record(run_id, from_step):
    from_step = validate_agent_step_key(from_step)
    ensure_agent_run_step_rows(run_id)
    config = require_platform_database()
    runs_table = get_agent_runs_table(config)
    steps_table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    now_ms = current_time_ms()
    resume_index = AGENT_STEP_INDEX_BY_KEY[from_step]
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {runs_table}
                SET status = 'queued',
                    current_step = %s,
                    error = '',
                    finished_at = NULL,
                    updated_at = %s
                WHERE project_id = %s AND run_id = %s
                """,
                (from_step, now_ms, project_id, run_id),
            )
            for step_key in AGENT_STEP_KEYS[resume_index:]:
                cursor.execute(
                    f"""
                    UPDATE {steps_table}
                    SET status = 'queued',
                        input_json = NULL,
                        output_json = NULL,
                        counts_json = NULL,
                        error = '',
                        started_at = NULL,
                        finished_at = NULL,
                        updated_at = %s
                    WHERE project_id = %s AND run_id = %s AND step_key = %s
                    """,
                    (now_ms, project_id, run_id, step_key),
                )
        connection.commit()
    append_agent_event(
        run_id,
        from_step,
        "status",
        agent_message("task_resumed", step=agent_step_name(from_step)),
        {"from_step": from_step},
    )
    return get_agent_run_row(run_id)


def insert_agent_event_row(
    cursor,
    table,
    project_id,
    run_id,
    step_key,
    event_type,
    message="",
    payload=None,
    job_id=None,
    asset_id=None,
    test_run_id=None,
    created_at=None,
):
    cursor.execute(
        f"""
        INSERT INTO {table}
          (project_id, run_id, step_key, event_type, message, payload_json, job_id, asset_id, test_run_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            project_id,
            validate_uid(run_id, "run_id"),
            str(step_key or "")[:64] or None,
            str(event_type or "log")[:32],
            str(message or ""),
            compact_json_dumps(payload or {}),
            job_id,
            asset_id,
            test_run_id,
            current_time_ms() if created_at is None else created_at,
        ),
    )
    return cursor.lastrowid


def append_agent_event(run_id, step_key, event_type, message="", payload=None, job_id=None, asset_id=None, test_run_id=None):
    message = agent_localization.event_message(agent_project_language(), message)
    config = require_platform_database()
    table = get_agent_run_events_table(config)
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            event_id = insert_agent_event_row(
                cursor,
                table,
                project_id,
                run_id,
                step_key,
                event_type,
                message,
                payload,
                job_id,
                asset_id,
                test_run_id,
            )
        connection.commit()
    return event_id


def persist_agent_stream_batch(
    run_id,
    step_key,
    job_id,
    text,
    metadata,
    *,
    job_log_snapshot=None,
):
    """Persist one aggregated model-output event and optional log checkpoint."""

    if not text:
        return None
    config = require_platform_database()
    events_table = get_agent_run_events_table(config)
    jobs_table = get_test_jobs_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    payload = dict(metadata or {})
    payload.pop("text", None)
    payload.update({"batched": True, "stream_kind": payload.get("stream_kind") or "model-output"})
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            if job_id and isinstance(job_log_snapshot, dict):
                snapshot_size = int(job_log_snapshot.get("log_size") or 0)
                cursor.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET log_path = %s, log_tail = %s, log_size = %s, updated_at = %s
                    WHERE project_id = %s AND job_id = %s
                      AND COALESCE(log_size, 0) <= %s
                    """,
                    (
                        job_log_snapshot.get("log_path") or "",
                        job_log_snapshot.get("log_tail") or "",
                        snapshot_size,
                        now_ms,
                        project_id,
                        job_id,
                        snapshot_size,
                    ),
                )
                if getattr(cursor, "rowcount", 1) == 0:
                    cursor.execute(
                        f"SELECT log_size FROM {jobs_table} WHERE project_id = %s AND job_id = %s",
                        (project_id, job_id),
                    )
                    if cursor.fetchone() is None:
                        raise RuntimeError(f"Agent 输出批次关联的任务不存在：{job_id}")
            event_id = insert_agent_event_row(
                cursor,
                events_table,
                project_id,
                run_id,
                step_key,
                "log",
                text,
                payload,
                job_id=job_id,
                created_at=now_ms,
            )
        try:
            connection.commit()
        except Exception as exc:
            raise AgentStreamCommitAmbiguous(
                "Agent 输出批次提交结果未知；为避免重复事件，不会自动重试。"
            ) from exc
    return event_id


def append_agent_item_retry_event(run_id, flow, message, event_type="status", step_key=None, **extra):
    serialized = serialize_agent_item_retry_flow(flow) if flow and "result_json" in flow else dict(flow or {})
    step_by_phase = {
        "queued": "generate_scripts",
        "generating": "generate_scripts",
        "executing": "execute_scripts",
        "repairing": "repair_scripts",
        "verifying": "execute_scripts",
        "completed": "execute_scripts",
    }
    payload = {
        "retry_flow_progress": True,
        "retry_flow": serialized,
        **extra,
    }
    return append_agent_event(
        run_id,
        step_key or step_by_phase.get(serialized.get("current_phase")) or "generate_scripts",
        event_type,
        message,
        payload,
    )


def append_agent_item_retry_terminal_event(
    run_id,
    flow,
    status,
    message,
    *,
    current_phase,
    progress_message,
    result,
    error="",
    cancel_requested=None,
    event_type="status",
    step_key=None,
):
    """Publish a projected terminal retry event before hiding the active flow."""

    projected = dict(flow or {})
    projected.update(
        {
            "status": validate_agent_item_retry_status(status),
            "current_phase": validate_agent_item_retry_phase(current_phase),
            "progress_message": str(progress_message or ""),
            "result_json": compact_json_dumps(result if isinstance(result, dict) else {}),
            "error": str(error or ""),
            "finished_at": current_time_ms(),
            "updated_at": current_time_ms(),
        }
    )
    if cancel_requested is not None:
        projected["cancel_requested"] = bool(cancel_requested)
    return append_agent_item_retry_event(
        run_id,
        projected,
        message,
        event_type=event_type,
        step_key=step_key,
    )


def terminalize_agent_item_retry_flow(
    run_id,
    retry_flow_id,
    status,
    *,
    expected_statuses,
    current_phase,
    progress_message,
    result,
    event_message,
    error="",
    cancel_requested=None,
    event_type="status",
    event_step_key=None,
    flow=None,
):
    """Publish the final event before the flow stops blocking stream completion."""

    status = validate_agent_item_retry_status(status)
    if status in AGENT_ITEM_RETRY_ACTIVE_STATUSES:
        raise ValueError("终结单项重试时必须提供终态。")
    current_phase = validate_agent_item_retry_phase(current_phase)
    expected_statuses = {validate_agent_item_retry_status(item) for item in expected_statuses}
    if not expected_statuses:
        raise ValueError("单项重试条件状态不能为空。")
    current_flow = flow or get_agent_item_retry_flow(run_id, retry_flow_id)
    if not current_flow:
        return None
    if current_flow.get("status") not in expected_statuses:
        return current_flow
    append_agent_item_retry_terminal_event(
        run_id,
        current_flow,
        status,
        event_message,
        current_phase=current_phase,
        progress_message=progress_message,
        result=result,
        error=error,
        cancel_requested=cancel_requested,
        event_type=event_type,
        step_key=event_step_key,
    )
    updates = {
        "status": status,
        "current_phase": current_phase,
        "progress_message": progress_message,
        "result": result,
        "error": error,
    }
    if cancel_requested is not None:
        updates["cancel_requested"] = cancel_requested
    return update_agent_item_retry_flow(
        run_id,
        retry_flow_id,
        expected_statuses=expected_statuses,
        **updates,
    )


def supersede_agent_failed_script_review(run_id, flow, final_script):
    del run_id, flow, final_script
    return None


def mark_agent_suite_stale_after_item_retry(run_id, flow, final_script):
    run = get_agent_run_row(run_id) or {}
    summary = load_json_column(run.get("summary_json"), {})
    summary = summary if isinstance(summary, dict) else {}
    if not (run.get("suite_uid") or summary.get("suite")):
        return summary
    summary.update(
        {
            "suite_stale": True,
            "needs_suite_rerun": True,
            "suite_stale_at": current_time_ms(),
            "suite_stale_reason": "单项脚本已重新生成并验证通过，现有测试集结果需要手动重新执行。",
            "latest_retry_flow_id": flow.get("retry_flow_id") or "",
            "latest_retry_script": {
                "module_name": final_script.get("module_name") or "",
                "plan_filename": final_script.get("plan_filename") or "",
                "filename": final_script.get("filename") or "",
                "asset": final_script.get("asset") if isinstance(final_script.get("asset"), dict) else None,
            },
        }
    )
    update_agent_run(run_id, summary=summary)
    return summary


def dedupe_agent_scripts(scripts):
    deduped = []
    index_by_key = {}
    for source_index, script in enumerate(scripts or []):
        if not isinstance(script, dict):
            continue
        module_name = str(script.get("module_name") or "").strip()
        filename = str(script.get("filename") or script.get("plan_filename") or "").strip()
        key = (module_name, filename)
        if not any(key):
            asset_id = script.get("asset", {}).get("asset_id") if isinstance(script.get("asset"), dict) else None
            key = ("asset", str(asset_id)) if asset_id is not None else ("unknown", str(source_index))
        if key in index_by_key:
            deduped[index_by_key[key]] = script
        else:
            index_by_key[key] = len(deduped)
            deduped.append(script)
    return deduped


def list_agent_events(run_id, after_id=0, limit=500, tail=False):
    config = require_platform_database()
    table = get_agent_run_events_table(config)
    project_id = get_current_project_id()
    limit = int(limit or 500)
    after_id = int(after_id or 0)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            if tail and after_id <= 0:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {table}
                    WHERE project_id = %s AND run_id = %s
                    ORDER BY event_id DESC
                    LIMIT %s
                    """,
                    (project_id, validate_uid(run_id, "run_id"), limit),
                )
                return list(reversed(cursor.fetchall()))
            cursor.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE project_id = %s AND run_id = %s AND event_id > %s
                ORDER BY event_id ASC
                LIMIT %s
                """,
                (project_id, validate_uid(run_id, "run_id"), after_id, limit),
            )
            return cursor.fetchall()


def read_agent_event_stream_page(run_id, after_id, limit):
    """Read one event page and its caught-up state in one short snapshot."""

    config = require_platform_database()
    events_table = get_agent_run_events_table(config)
    runs_table = get_agent_runs_table(config)
    retry_table = get_agent_item_retry_flows_table(config)
    project_id = get_current_project_id()
    validated_run_id = validate_uid(run_id, "run_id")
    page_size = min(max(int(limit or 200), 1), 1000)
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {events_table}
                WHERE project_id = %s AND run_id = %s AND event_id > %s
                ORDER BY event_id ASC
                LIMIT %s
                """,
                (project_id, validated_run_id, int(after_id or 0), page_size),
            )
            rows = cursor.fetchall()
            if len(rows) == page_size:
                return rows, None, None

            cursor.execute(
                f"SELECT * FROM {runs_table} WHERE project_id = %s AND run_id = %s",
                (project_id, validated_run_id),
            )
            run = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT *
                FROM {retry_table}
                WHERE project_id = %s AND run_id = %s
                  AND status IN ('queued', 'running', 'finalizing', 'cancelling')
                ORDER BY created_at DESC, id DESC
                LIMIT 200
                """,
                (project_id, validated_run_id),
            )
            retry_flows = cursor.fetchall()
    return rows, run, retry_flows


def _requirement_storage_dependencies():
    return requirement_storage.RequirementStorageDependencies(
        validate_uid=lambda value, field_name: validate_uid(
            value,
            field_name,
        ),
        get_project_root=lambda: get_project_root(),
        app_dir=APP_DIR,
        get_cwd=lambda: Path.cwd(),
        walk=lambda root: os.walk(root),
        sha256_file=lambda path: sha256_file(path),
        write_file_atomically=lambda path, content: (
            write_file_atomically(path, content)
        ),
        recovery_excluded_dirs=frozenset(
            REQUIREMENT_RECOVERY_EXCLUDED_DIRS
        ),
        recovery_max_candidates=(
            REQUIREMENT_RECOVERY_MAX_CANDIDATES
        ),
    )


def _requirement_storage():
    return requirement_storage.RequirementStorage(
        _requirement_storage_dependencies()
    )


def _requirement_serialization_dependencies():
    return requirement_model.RequirementSerializationDependencies(
        read_requirement_markdown=lambda row: (
            read_requirement_markdown(row)
        ),
        render_markdown=lambda value: render_markdown(value),
    )


def _requirement_module_model_dependencies():
    return requirement_model.RequirementModuleModelDependencies(
        validate_module_name=lambda value: validate_module_name(value),
        get_chinese_plan_filename_from_name=(
            lambda plan_name, module_name, **kwargs: (
                get_chinese_plan_filename_from_name(
                    plan_name,
                    module_name,
                    **kwargs,
                )
            )
        ),
        get_project_language=lambda: get_current_project_language(),
        normalize_confidence=lambda value: normalize_confidence(value),
        normalize_string_list=lambda value: normalize_string_list(value),
        normalize_json_object_or_array=lambda value, fallback: (
            normalize_json_object_or_array(value, fallback)
        ),
        get_seed_script_relative_path=lambda: (
            get_seed_script_relative_path()
        ),
        strip_legacy_coverage_notices=lambda prompt: (
            strip_legacy_coverage_notices(prompt)
        ),
        append_database_baseline_write_operation_notice=(
            lambda prompt: (
                append_database_baseline_write_operation_notice(
                    prompt
                )
            )
        ),
        load_json_column=lambda value, fallback: (
            load_json_column(value, fallback)
        ),
        list_requirement_module_plans=lambda module_id: (
            list_requirement_module_plans(module_id)
        ),
        get_test_asset_by_id=lambda asset_id: (
            get_test_asset_by_id(asset_id)
        ),
        serialize_asset=lambda asset: serialize_asset(asset),
        dedupe_chinese_artifact_naming_notice=lambda prompt: (
            dedupe_chinese_artifact_naming_notice(prompt)
        ),
    )


def _requirement_repository_dependencies():
    return requirement_repository.RequirementRepositoryDependencies(
        require_platform_database=lambda: (
            require_platform_database()
        ),
        get_requirements_table=lambda config: (
            get_requirements_table(config)
        ),
        get_requirement_modules_table=lambda config: (
            get_requirement_modules_table(config)
        ),
        get_agent_runs_table=lambda config: get_agent_runs_table(
            config
        ),
        get_current_project_id=lambda: get_current_project_id(),
        platform_mysql_connection=lambda config: (
            platform_mysql_connection(config)
        ),
        validate_uid=lambda value, field_name: validate_uid(
            value,
            field_name,
        ),
        current_time_ms=lambda: current_time_ms(),
        compact_json_dumps=lambda value: compact_json_dumps(value),
        get_requirement_by_uid=lambda requirement_uid: (
            get_requirement_by_uid(requirement_uid)
        ),
        get_requirement_module=lambda requirement_id, module_uid: (
            get_requirement_module(requirement_id, module_uid)
        ),
    )


def _requirement_repository():
    return requirement_repository.RequirementRepository(
        _requirement_repository_dependencies()
    )


def _requirement_service_dependencies():
    return requirement_service.RequirementServiceDependencies(
        validate_requirement_filename=lambda filename: (
            validate_requirement_filename(filename)
        ),
        get_requirement_storage_file=lambda requirement_uid, filename: (
            get_requirement_storage_file(requirement_uid, filename)
        ),
        write_file_atomically=lambda path, content: (
            write_file_atomically(path, content)
        ),
        extract_requirement_title=lambda markdown_text, filename: (
            extract_requirement_title(markdown_text, filename)
        ),
        sha256_bytes=lambda content: sha256_bytes(content),
        current_time_ms=lambda: current_time_ms(),
        current_platform_author=lambda: current_platform_author(),
        uuid_hex=lambda: uuid.uuid4().hex,
        create_uploaded_requirement=lambda record: (
            _requirement_repository().create_uploaded_requirement(
                record
            )
        ),
        get_requirement_module=lambda requirement_id, module_uid: (
            get_requirement_module(requirement_id, module_uid)
        ),
        serialize_requirement_module=lambda row: (
            serialize_requirement_module(row)
        ),
        normalize_requirement_module_candidate=(
            lambda raw, **kwargs: (
                normalize_requirement_module_candidate(
                    raw,
                    **kwargs,
                )
            )
        ),
        update_requirement_module=(
            lambda requirement_id, module_uid, normalized, status: (
                _requirement_repository().update_module(
                    requirement_id,
                    module_uid,
                    normalized,
                    status,
                )
            )
        ),
        requirement_module_statuses=frozenset(
            REQUIREMENT_MODULE_STATUSES
        ),
        upload_max_bytes=REQUIREMENT_UPLOAD_MAX_BYTES,
    )


def _requirement_service():
    return requirement_service.RequirementService(
        _requirement_service_dependencies()
    )


def _requirement_web_services():
    return RequirementWebServices(
        list_requirements=lambda: list_requirements_from_mysql(),
        serialize_requirement=lambda row, **kwargs: (
            serialize_requirement(row, **kwargs)
        ),
        create_requirement=lambda file_storage, **kwargs: (
            create_requirement_from_upload(
                file_storage,
                **kwargs,
            )
        ),
        get_requirement=lambda requirement_uid: (
            get_requirement_by_uid(requirement_uid)
        ),
        delete_requirement=lambda requirement_uid: (
            delete_requirement_by_uid(requirement_uid)
        ),
        list_modules=lambda requirement_id: (
            list_requirement_modules(requirement_id)
        ),
        get_module=lambda requirement_id, module_uid: (
            get_requirement_module(requirement_id, module_uid)
        ),
        serialize_module=lambda row: (
            serialize_requirement_module(row)
        ),
        build_planner_prompt=lambda module_data, **kwargs: (
            build_planner_prompt_from_requirement_module(
                module_data,
                **kwargs,
            )
        ),
        update_module=lambda requirement_id, module_uid, payload: (
            update_requirement_module(
                requirement_id,
                module_uid,
                payload,
            )
        ),
        delete_module=lambda requirement_id, module_uid: (
            delete_requirement_module(
                requirement_id,
                module_uid,
            )
        ),
    )


def validate_requirement_filename(filename):
    return requirement_storage.validate_requirement_filename(
        filename
    )


def get_requirements_dir():
    return _requirement_storage().get_requirements_dir()


def get_requirement_storage_file(requirement_uid, filename):
    return _requirement_storage().get_storage_file(
        requirement_uid,
        filename,
    )


def extract_requirement_title(markdown_text, filename):
    return requirement_model.extract_requirement_title(
        markdown_text,
        filename,
    )


def get_requirement_recovery_roots():
    return _requirement_storage().get_recovery_roots()


def iter_requirement_recovery_candidates(filename):
    yield from _requirement_storage().iter_recovery_candidates(
        filename
    )


def recover_missing_requirement_file(row, target_path):
    return _requirement_storage().recover_missing_file(
        row,
        target_path,
    )


def read_requirement_markdown(row):
    return _requirement_storage().read_markdown(row)


def get_requirement_by_uid(requirement_uid, include_deleted=False):
    return _requirement_repository().get_requirement(
        requirement_uid,
        include_deleted=include_deleted,
    )


def list_requirements_from_mysql():
    return _requirement_repository().list_requirements()


def serialize_requirement(row, include_content=False):
    return requirement_model.serialize_requirement(
        row,
        include_content=include_content,
        dependencies=_requirement_serialization_dependencies(),
    )


def create_requirement_from_upload(file_storage, title=None):
    return _requirement_service().create_from_upload(
        file_storage,
        title=title,
    )


def delete_requirement_by_uid(requirement_uid):
    with AGENT_PROJECT_OPERATION_LOCK:
        return _requirement_repository().delete_requirement(
            requirement_uid
        )


def build_planner_prompt_from_requirement_module(
    module_data,
    requirement=None,
):
    return requirement_model.build_planner_prompt_from_requirement_module(
        module_data,
        requirement=requirement,
        dependencies=_requirement_module_model_dependencies(),
    )


def normalize_requirement_module_candidate(
    raw,
    requirement=None,
):
    return requirement_model.normalize_requirement_module_candidate(
        raw,
        requirement=requirement,
        dependencies=_requirement_module_model_dependencies(),
    )


def serialize_requirement_module(row):
    return requirement_model.serialize_requirement_module(
        row,
        dependencies=_requirement_module_model_dependencies(),
    )


def list_requirement_modules(
    requirement_id,
    include_superseded=False,
):
    return _requirement_repository().list_modules(
        requirement_id,
        include_superseded=include_superseded,
    )


def get_requirement_module(requirement_id, module_uid):
    return _requirement_repository().get_module(
        requirement_id,
        module_uid,
    )


def save_requirement_modules_from_analysis(requirement, modules, job_id):
    normalized = [normalize_requirement_module_candidate(item, requirement=requirement) for item in modules]
    config = require_platform_database()
    table = get_requirement_modules_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                DELETE FROM {table}
                WHERE project_id = %s
                  AND requirement_id = %s
                  AND generated_plan_asset_id IS NULL
                """,
                (project_id, requirement["id"]),
            )
            cursor.execute(
                f"""
                UPDATE {table}
                SET status = 'superseded', updated_at = %s
                WHERE project_id = %s
                  AND requirement_id = %s
                  AND status NOT IN ('generated', 'deleted')
                """,
                (now_ms, project_id, requirement["id"]),
            )
            for item in normalized:
                cursor.execute(
                    f"""
                    INSERT INTO {table}
                      (project_id, requirement_id, module_uid, module_name, plan_name, status, confidence,
                       business_goal, requirement_refs_json, test_points_json, matched_inventory_json,
                       open_questions_json, baseline_required, write_risk, planner_prompt, source_job_id,
                       generated_plan_asset_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'candidate', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                    """,
                    (
                        project_id,
                        requirement["id"],
                        uuid.uuid4().hex,
                        item["module_name"],
                        item["plan_name"],
                        item["confidence"],
                        item["business_goal"],
                        compact_json_dumps(item["requirement_refs"]),
                        compact_json_dumps(item["test_points"]),
                        compact_json_dumps(item["matched_inventory"]),
                        compact_json_dumps(item["open_questions"]),
                        int(item["baseline_required"]),
                        int(item["write_risk"]),
                        item["planner_prompt"],
                        job_id,
                        now_ms,
                        now_ms,
                    ),
                )
            cursor.execute(
                f"UPDATE {get_requirements_table(config)} SET updated_at = %s WHERE project_id = %s AND id = %s",
                (now_ms, project_id, requirement["id"]),
            )
            connection.commit()
    return list_requirement_modules(requirement["id"])


def update_requirement_module(
    requirement_id,
    module_uid,
    payload,
):
    return _requirement_service().update_module(
        requirement_id,
        module_uid,
        payload,
    )


def delete_requirement_module(requirement_id, module_uid):
    return _requirement_repository().delete_module(
        requirement_id,
        module_uid,
    )


def link_requirement_module_plan(
    requirement_id,
    module_uid,
    asset_id,
    job_id,
    coverage_profile=DEFAULT_COVERAGE_PROFILE,
    prompt_customized=False,
):
    config = require_platform_database()
    table = get_requirement_modules_table(config)
    project_id = get_current_project_id()
    now_ms = current_time_ms()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {table} WHERE project_id = %s AND requirement_id = %s AND module_uid = %s LIMIT 1",
                (project_id, requirement_id, validate_uid(module_uid, "module_uid")),
            )
            module_row = cursor.fetchone()
            if not module_row:
                raise ValueError("候选模块不存在。")
            cursor.execute(
                f"""
                UPDATE {table}
                SET generated_plan_asset_id = %s,
                    source_job_id = COALESCE(%s, source_job_id),
                    status = 'generated',
                    updated_at = %s
                WHERE project_id = %s AND requirement_id = %s AND module_uid = %s
                """,
                (asset_id, job_id, now_ms, project_id, requirement_id, validate_uid(module_uid, "module_uid")),
            )
            if asset_id:
                cursor.execute(
                    f"""
                    INSERT INTO {get_requirement_module_plans_table(config)}
                      (project_id, requirement_id, requirement_module_id, plan_asset_id, source_job_id,
                       coverage_profile, prompt_customized, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      source_job_id = VALUES(source_job_id),
                      coverage_profile = VALUES(coverage_profile),
                      prompt_customized = VALUES(prompt_customized)
                    """,
                    (
                        project_id,
                        requirement_id,
                        module_row["id"],
                        asset_id,
                        job_id,
                        validate_coverage_profile(coverage_profile),
                        int(bool(prompt_customized)),
                        now_ms,
                    ),
                )
            connection.commit()
    return get_requirement_module(requirement_id, module_uid)


def list_requirement_module_plans(requirement_module_id):
    if not requirement_module_id or not is_platform_database_enabled():
        return []
    config = require_platform_database()
    project_id = get_current_project_id()
    with platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM {get_requirement_module_plans_table(config)}
                WHERE project_id = %s AND requirement_module_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (project_id, requirement_module_id),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        asset = get_test_asset_by_id(row.get("plan_asset_id"))
        if not asset:
            continue
        result.append(
            {
                "asset": serialize_asset(asset),
                "module_name": asset.get("module_name"),
                "plan_filename": Path(asset.get("current_path") or "").name,
                "path": asset.get("current_path") or "",
                "coverage_profile": row.get("coverage_profile") or DEFAULT_COVERAGE_PROFILE,
                "prompt_customized": bool(row.get("prompt_customized")),
                "source_job_id": row.get("source_job_id") or "",
                "created_at": row.get("created_at"),
            }
        )
    return result


def collect_opencode_response_text(response):
    if not response:
        return ""
    parts = response.get("parts")
    if isinstance(parts, list):
        texts = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        if texts:
            return "\n".join(texts)
    raw = response.get("raw")
    if isinstance(raw, str):
        return raw
    return json.dumps(response, ensure_ascii=False)


def extract_json_object_from_text(text):
    candidates = JSON_FENCE_PATTERN.findall(text or "")
    candidates.append(text or "")
    decoder = json.JSONDecoder()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        for index, character in enumerate(candidate):
            if character not in "{[":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
    raise ValueError("OpenCode 未输出可解析的 JSON。")


def normalize_analysis_json(parsed):
    if isinstance(parsed, list):
        modules = parsed
    elif isinstance(parsed, dict):
        modules = parsed.get("modules")
    else:
        modules = None
    if not isinstance(modules, list):
        raise ValueError("需求解析 JSON 顶层必须包含 modules 数组。")
    return modules


def summarize_page_inventory_for_prompt(limit=40):
    rows = list_page_inventory_rows(limit=limit)
    if not rows:
        if get_current_project_language() == "en":
            return "No page inventory is available. Record unmatched pages in open_questions; do not invent URLs."
        return "暂无页面 inventory。无法匹配真实页面时，请在 open_questions 中说明，不要臆造 URL。"
    lines = []
    for row in rows:
        item = serialize_page_inventory(row)
        if get_current_project_language() == "en":
            parts = [
                f"Page: {item['page_name']}",
                f"URL: {item['url'] or 'unknown'}",
                f"Menu: {' / '.join(item['menu_path']) if item['menu_path'] else 'unknown'}",
                f"Account: {', '.join(account.get('username', '') for account in item['accounts'] if isinstance(account, dict)) or ', '.join(item['roles']) or 'unknown'}",
                f"Selectors: {', '.join(item['stable_selectors'][:8]) or 'unknown'}",
                f"Write risk: {'yes' if item['write_risk'] else 'no'}",
            ]
            if item.get("notes"):
                parts.append(f"Notes: {item['notes']}")
            lines.append("- " + "; ".join(parts))
            continue
        parts = [
            f"页面：{item['page_name']}",
            f"URL：{item['url'] or '未知'}",
            f"菜单：{' / '.join(item['menu_path']) if item['menu_path'] else '未知'}",
            f"账号：{', '.join(account.get('username', '') for account in item['accounts'] if isinstance(account, dict)) or ', '.join(item['roles']) or '未知'}",
            f"控件：{', '.join(item['stable_selectors'][:8]) or '未知'}",
            f"写库风险：{'是' if item['write_risk'] else '否'}",
        ]
        if item.get("notes"):
            parts.append(f"备注：{item['notes']}")
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)


def summarize_existing_plans_for_prompt(limit=80):
    try:
        specs_dir = get_specs_dir()
    except Exception:
        if get_current_project_language() == "en":
            return "Existing test plans could not be read."
        return "无法读取现有测试计划。"
    if not specs_dir.exists():
        if get_current_project_language() == "en":
            return "No existing test plans."
        return "暂无现有测试计划。"
    lines = []
    for plan_file in sorted(specs_dir.glob("*/*.md"), key=lambda item: item.as_posix().lower()):
        if len(lines) >= limit:
            break
        try:
            module_name = plan_file.parent.name
            lines.append(f"- {module_name}/{plan_file.name}")
        except Exception:
            continue
    if lines:
        return "\n".join(lines)
    return "No existing test plans." if get_current_project_language() == "en" else "暂无现有测试计划。"


def build_requirement_analysis_prompt(requirement, markdown_text):
    if get_current_project_language() == "en":
        return (
            "@requirement-analyst\n"
            "Read the requirement Markdown, page-inventory summary, and existing-plan summary. Generate module candidates.\n\n"
            "Requirements:\n1. Analyze only; do not use the browser or write specs/tests.\n"
            "2. Output JSON only, with a top-level modules array.\n"
            "3. Each module includes module_name, plan_name, business_goal, requirement_refs, test_points, matched_inventory, write_risk, baseline_required, confidence, open_questions, and planner_prompt.\n"
            "4. Generate English business names for module_name and plan_name by default.\n"
            "5. Extract explicit positive, error, boundary, role, and permission points without inventing unrelated coverage.\n"
            "6. Keep open_questions when no real page can be matched.\n\n"
            f"Requirement title: {requirement.get('title')}\n\nRequirement Markdown:\n{markdown_text}\n\n"
            f"Page inventory summary:\n{summarize_page_inventory_for_prompt()}\n\n"
            f"Existing test-plan summary:\n{summarize_existing_plans_for_prompt()}\n"
        )
    return (
        "@requirement-analyst\n"
        "你是测试需求分析助手。请读取下面的需求 Markdown、页面 inventory 摘要和已有测试计划摘要，生成模块候选。\n\n"
        "要求：\n"
        "1. 只做需求分析，不操作浏览器，不写入 specs 或 tests。\n"
        "2. 只输出 JSON，对象顶层包含 modules 数组，不要输出额外解释。\n"
        "3. 每个模块包含 module_name、plan_name、business_goal、requirement_refs、test_points、matched_inventory、"
        "write_risk、baseline_required、confidence、open_questions、planner_prompt。\n"
        "4. module_name 和 plan_name 必须使用中文业务名称；不要使用英文、拼音或技术标识符命名。\n"
        "5. 完整提取需求明确描述的正向、异常、边界、角色和权限测试点；不要主动补充需求未提及的兼容性、安全或低频场景。\n"
        "6. 如果需求无法匹配真实页面，保留 open_questions，不要臆造 URL。\n"
        "7. planner_prompt 只提供覆盖中立的模块上下文，提醒 planner 登录系统复核页面；不要写入覆盖档位、场景类型筛选或用例数量限制。\n\n"
        f"需求标题：{requirement.get('title')}\n\n"
        "需求 Markdown：\n"
        f"{markdown_text}\n\n"
        "页面 inventory 摘要：\n"
        f"{summarize_page_inventory_for_prompt()}\n\n"
        "已有测试计划摘要：\n"
        f"{summarize_existing_plans_for_prompt()}\n"
    )


def stream_requirement_analysis(requirement, job_id=None):
    deps = requirement_analysis_stream.RequirementAnalysisDependencies(
        sanitize_job_id=sanitize_job_id, read_markdown=read_requirement_markdown,
        build_prompt=build_requirement_analysis_prompt, create_job=create_test_job,
        update_job=update_test_job, get_job=get_test_job, serialize_job=serialize_job,
        append_log=append_test_job_log, finish_job=finish_test_job,
        current_time_ms=current_time_ms, message=agent_message,
        send_prompt=send_opencode_prompt_cancellable, collect_response_text=collect_opencode_response_text,
        extract_json=extract_json_object_from_text, normalize_analysis=normalize_analysis_json,
        save_modules=save_requirement_modules_from_analysis, serialize_module=serialize_requirement_module,
        sse_payload=sse_payload, cancelled_exception=OpencodeTaskCancelled,
        log_tail_limit=JOB_LOG_TAIL_LIMIT,
    )
    return requirement_analysis_stream.stream_requirement_analysis(requirement, job_id, deps)


def agent_register_task(run_id):
    with AGENT_RUN_TASK_LOCK:
        existing = AGENT_RUN_TASKS.get(run_id) or {}
        AGENT_RUN_TASKS[run_id] = {
            "cancel_requested": bool(existing.get("cancel_requested")),
            "current_job_id": existing.get("current_job_id") or "",
            "last_db_check": float(existing.get("last_db_check") or 0),
            "updated_at": time.time(),
        }


def agent_cleanup_task(run_id):
    with AGENT_RUN_TASK_LOCK:
        AGENT_RUN_TASKS.pop(run_id, None)


def agent_set_current_job(run_id, job_id):
    retry_flow_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "retry_flow_id", "") or ""
    retry_run_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "run_id", "") or ""
    if retry_flow_id and retry_run_id == run_id:
        with AGENT_ITEM_RETRY_TASK_LOCK:
            task = AGENT_ITEM_RETRY_TASKS.get(retry_flow_id) or {
                "run_id": run_id,
                "cancel_requested": False,
            }
            task["current_job_id"] = job_id or ""
            task["updated_at"] = time.time()
            AGENT_ITEM_RETRY_TASKS[retry_flow_id] = task
            return bool(task.get("cancel_requested"))

    with AGENT_RUN_TASK_LOCK:
        task = AGENT_RUN_TASKS.get(run_id) or {"cancel_requested": False}
        task["current_job_id"] = job_id or ""
        task["updated_at"] = time.time()
        AGENT_RUN_TASKS[run_id] = task
        return bool(task.get("cancel_requested"))


def agent_request_cancel(run_id):
    with AGENT_RUN_TASK_LOCK:
        task = AGENT_RUN_TASKS.get(run_id) or {}
        task["cancel_requested"] = True
        task["updated_at"] = time.time()
        current_job_id = task.get("current_job_id") or ""
        AGENT_RUN_TASKS[run_id] = task

    aborted = False
    if current_job_id:
        try:
            cancel_opencode_task(current_job_id)
            aborted = True
        except Exception:
            aborted = False
    return {"cancel_requested": True, "current_job_id": current_job_id, "aborted": aborted}


def agent_is_cancelled(run_id, *, force=False):
    retry_flow_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "retry_flow_id", "") or ""
    retry_run_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "run_id", "") or ""
    if retry_flow_id and retry_run_id == run_id:
        now = time.time()
        with AGENT_ITEM_RETRY_TASK_LOCK:
            task = AGENT_ITEM_RETRY_TASKS.get(retry_flow_id) or {"run_id": run_id}
            if task.get("cancel_requested"):
                return True
            last_check = float(task.get("last_db_check") or 0)
            if not force and now - last_check < 0.5:
                return False
            task["last_db_check"] = now
            AGENT_ITEM_RETRY_TASKS[retry_flow_id] = task
        flow = get_agent_item_retry_flow(run_id, retry_flow_id) or {}
        cancelled = bool(flow.get("cancel_requested")) or flow.get("status") in {"cancelling", "cancelled"}
        if cancelled:
            with AGENT_ITEM_RETRY_TASK_LOCK:
                task = AGENT_ITEM_RETRY_TASKS.get(retry_flow_id) or {"run_id": run_id}
                task["cancel_requested"] = True
                AGENT_ITEM_RETRY_TASKS[retry_flow_id] = task
        return cancelled

    now = time.monotonic()
    with AGENT_RUN_TASK_LOCK:
        task = AGENT_RUN_TASKS.get(run_id) or {"cancel_requested": False}
        if task.get("cancel_requested"):
            return True
        last_check = float(task.get("last_db_check") or 0)
        if not force and now - last_check < 0.5:
            return False
        task["last_db_check"] = now
        AGENT_RUN_TASKS[run_id] = task
    row = get_agent_run_row(run_id)
    cancelled = bool(row and row.get("status") in {"cancelling", "cancelled"})
    if cancelled:
        with AGENT_RUN_TASK_LOCK:
            task = AGENT_RUN_TASKS.get(run_id) or {}
            task["cancel_requested"] = True
            AGENT_RUN_TASKS[run_id] = task
    return cancelled


def agent_raise_if_cancelled(run_id, *, force=False):
    if agent_is_cancelled(run_id, force=force):
        raise OpencodeTaskCancelled(
            agent_message("task_cancelled")
        )


@contextmanager
def use_agent_item_retry_context(run_id, retry_flow_id):
    previous_run_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "run_id", None)
    previous_retry_flow_id = getattr(AGENT_ITEM_RETRY_CONTEXT, "retry_flow_id", None)
    AGENT_ITEM_RETRY_CONTEXT.run_id = run_id
    AGENT_ITEM_RETRY_CONTEXT.retry_flow_id = retry_flow_id
    try:
        yield
    finally:
        AGENT_ITEM_RETRY_CONTEXT.run_id = previous_run_id
        AGENT_ITEM_RETRY_CONTEXT.retry_flow_id = previous_retry_flow_id


def register_agent_item_retry_task(run_id, retry_flow_id):
    with AGENT_ITEM_RETRY_TASK_LOCK:
        existing = AGENT_ITEM_RETRY_TASKS.get(retry_flow_id) or {}
        AGENT_ITEM_RETRY_TASKS[retry_flow_id] = {
            "run_id": run_id,
            "cancel_requested": bool(existing.get("cancel_requested")),
            "current_job_id": existing.get("current_job_id") or "",
            "last_db_check": 0,
            "updated_at": time.time(),
        }


def cleanup_agent_item_retry_task(retry_flow_id):
    with AGENT_ITEM_RETRY_TASK_LOCK:
        AGENT_ITEM_RETRY_TASKS.pop(retry_flow_id, None)


def request_agent_item_retry_cancel(run_id, retry_flow_id):
    flow = update_agent_item_retry_flow(
        run_id,
        retry_flow_id,
        expected_statuses={"queued", "running"},
        status="cancelling",
        cancel_requested=True,
        progress_message="正在取消本次重试。",
    )
    if not flow or flow.get("status") != "cancelling":
        return flow, {
            "cancel_requested": False,
            "current_job_id": "",
            "aborted": False,
            "reason": "单项重试正在收尾或已经结束。",
        }
    with AGENT_ITEM_RETRY_TASK_LOCK:
        task = AGENT_ITEM_RETRY_TASKS.get(retry_flow_id) or {"run_id": run_id}
        task["cancel_requested"] = True
        task["updated_at"] = time.time()
        current_job_id = task.get("current_job_id") or ""
        AGENT_ITEM_RETRY_TASKS[retry_flow_id] = task
    aborted = False
    if current_job_id:
        try:
            cancel_opencode_task(current_job_id)
            aborted = True
        except Exception:
            aborted = False
    return flow, {"cancel_requested": True, "current_job_id": current_job_id, "aborted": aborted}


def agent_start_step(run_id, step_key, input_data=None):
    agent_raise_if_cancelled(run_id)
    update_agent_run(run_id, status="running", current_step=step_key)
    update_agent_step(run_id, step_key, status="running", input_data=input_data, error="", started=True)
    step_name = agent_step_name(step_key)
    append_agent_event(
        run_id,
        step_key,
        "status",
        agent_message("step_started", step=step_name),
        {"status": "running"},
    )


def agent_finish_step(run_id, step_key, output_data=None, counts=None):
    update_agent_step(
        run_id,
        step_key,
        status="succeeded",
        output_data=output_data or {},
        counts=counts or {},
        error="",
        finished=True,
    )
    append_agent_event(
        run_id,
        step_key,
        "status",
        agent_message("step_completed", step=agent_step_name(step_key)),
        {"status": "succeeded", "counts": counts or {}},
    )


def agent_update_step_progress(run_id, step_key, output_data=None, counts=None, input_data=None):
    updates = {}
    if input_data is not None:
        updates["input_data"] = input_data
    if output_data is not None:
        updates["output_data"] = output_data
    if counts is not None:
        updates["counts"] = counts
    if updates:
        update_agent_step(run_id, step_key, **updates)


def append_agent_artifact_progress(
    run_id,
    step_key,
    item_status,
    message,
    *,
    artifact_type,
    output_data=None,
    counts=None,
    input_data=None,
    item=None,
    event_type="status",
    job_id=None,
    asset_id=None,
    **extra,
):
    if input_data is not None or output_data is not None or counts is not None:
        agent_update_step_progress(run_id, step_key, output_data=output_data, counts=counts, input_data=input_data)

    payload = {
        "artifact_progress": True,
        "artifact_type": artifact_type,
        "item_status": item_status,
        "step_status": "running",
        **extra,
    }
    if counts is not None:
        payload["counts"] = counts
    if input_data is not None:
        payload["step_input"] = input_data
    if output_data is not None:
        payload["step_output"] = output_data
    if item is not None:
        payload["item"] = item
    append_agent_event(run_id, step_key, event_type, message, payload, job_id=job_id, asset_id=asset_id)


def agent_fail_step(run_id, step_key, error):
    update_agent_step(run_id, step_key, status="failed", error=str(error), finished=True)
    append_agent_event(
        run_id,
        step_key,
        "error",
        agent_message("step_failed", step=agent_step_name(step_key), error=error),
        {"error": str(error)},
    )


def parse_sse_text_blocks(text):
    event_name = None
    data_lines = []
    for line in str(text or "").replace("\r\n", "\n").splitlines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    payload = {"raw": data}
                yield event_name, payload
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        data = "\n".join(data_lines)
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"raw": data}
        yield event_name, payload


def consume_agent_sse_generator(
    run_id,
    step_key,
    generator,
    log_limit=2000,
    *,
    generator_handles_cancellation=False,
):
    dependencies = agent_stream_consumer.AgentStreamConsumerDependencies(
        parse_sse_text_blocks=parse_sse_text_blocks,
        persist_agent_stream_batch=persist_agent_stream_batch,
        append_agent_event=append_agent_event,
        agent_raise_if_cancelled=agent_raise_if_cancelled,
        ambiguous_commit_error=AgentStreamCommitAmbiguous,
        cancelled_error=OpencodeTaskCancelled,
        log_tail_limit=JOB_LOG_TAIL_LIMIT,
        sleep=time.sleep,
        batcher_factory=AgentOutputBatcher,
        project_copy=project_copy,
    )
    return agent_stream_consumer.consume_agent_sse_generator(
        run_id,
        step_key,
        generator,
        dependencies,
        log_limit,
        generator_handles_cancellation=generator_handles_cancellation,
    )


def ensure_test_platform_reviewer_agent():
    project_root = get_project_root()
    prompt_dir = project_root / ".opencode" / "prompts"
    prompt_file = prompt_dir / "test-platform-reviewer.md"
    prompt_source = """You are a read-only reviewer for the Waterfall AI test automation platform.

You inspect modules, prompts, Markdown test plans, scripts, and task logs.
Return only valid JSON. Do not wrap JSON in Markdown fences.
Do not create, edit, delete, move files, run commands, or use browser tools.
Prefer keep when the input is reasonable. Use delete only when the item is clearly duplicate, unsafe, empty, or unrelated.
When action is update, include the full replacement fields/content needed by the platform.

Accepted action values: keep, update, delete, exclude.
Every decision must include reason.
"""
    prompt_changed = write_text_if_changed(prompt_file, prompt_source)

    opencode_file = project_root / "opencode.json"
    config_changed = False
    if opencode_file.exists():
        try:
            config_data = json.loads(opencode_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config_data = None
        if isinstance(config_data, dict):
            agents = config_data.setdefault("agent", {})
            if "test-platform-reviewer" not in agents:
                agents["test-platform-reviewer"] = {
                    "description": "Use this agent when the test platform needs read-only review decisions as JSON",
                    "mode": "subagent",
                    "prompt": "{file:.opencode/prompts/test-platform-reviewer.md}",
                    "permission": {"external_directory": "allow"},
                    "tools": {"ls": True, "glob": True, "grep": True, "read": True},
                }
                opencode_file.write_text(json.dumps(config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                config_changed = True
    return {"prompt_file": str(prompt_file), "prompt_changed": prompt_changed, "config_changed": config_changed}


def ensure_test_platform_failure_analyst_agent():
    project_root = get_project_root()
    prompt_file = project_root / ".opencode" / "prompts" / "test-platform-failure-analyst.md"
    prompt_source = """You are a read-only failure analyst for the Waterfall AI test automation platform.

Analyze only the supplied failure evidence and return valid JSON matching the response_schema in the input.
Do not return reviewer decisions such as keep, update, delete, or exclude.
Separate confirmed facts from hypotheses, cite evidence_id values, identify missing evidence, and give a practical retry prompt patch.
Do not create, edit, delete, or move files, run commands, or use browser tools.
Never expose secrets found in evidence. Do not wrap JSON in Markdown fences.
"""
    prompt_changed = write_text_if_changed(prompt_file, prompt_source)
    opencode_file = project_root / "opencode.json"
    config_changed = False
    if opencode_file.exists():
        try:
            config_data = json.loads(opencode_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config_data = None
        if isinstance(config_data, dict):
            agents = config_data.setdefault("agent", {})
            analyst = {
                "description": "Analyze supplied Playwright failure evidence as structured JSON",
                "mode": "subagent",
                "prompt": "{file:.opencode/prompts/test-platform-failure-analyst.md}",
                "permission": {"*": "deny", "external_directory": "deny"},
                "tools": {"*": False},
            }
            if agents.get("test-platform-failure-analyst") != analyst:
                agents["test-platform-failure-analyst"] = analyst
                opencode_file.write_text(json.dumps(config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                config_changed = True
    return {"prompt_file": str(prompt_file), "prompt_changed": prompt_changed, "config_changed": config_changed}


def ensure_project_opencode_prompt_files():
    project_prompt_dir = get_project_root() / ".opencode" / "prompts"
    template_prompt_dir = PROJECT_TEMPLATE_DIR / ".opencode" / "prompts"
    restored = []
    missing_sources = []

    for filename in PROJECT_TEMPLATE_OPENCODE_PROMPTS:
        target_file = project_prompt_dir / filename
        if target_file.exists():
            continue
        source_file = template_prompt_dir / filename
        if not source_file.is_file():
            missing_sources.append(str(source_file))
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(source_file.read_bytes())
        restored.append(str(target_file))

    if missing_sources:
        raise RuntimeError(f"平台内置 OpenCode prompt 模板缺失：{', '.join(missing_sources)}")
    return restored


def ensure_plan_markdown_splitter_agent():
    project_root = get_project_root()
    prompt_dir = project_root / ".opencode" / "prompts"
    prompt_file = prompt_dir / "plan-markdown-splitter.md"
    prompt_source = agent_localization.splitter_prompt(agent_project_language())
    prompt_changed = write_text_if_changed(prompt_file, prompt_source)

    opencode_file = project_root / "opencode.json"
    config_changed = False
    if opencode_file.exists():
        try:
            config_data = json.loads(opencode_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config_data = None
        if isinstance(config_data, dict):
            agents = config_data.setdefault("agent", {})
            if "plan-markdown-splitter" not in agents:
                agents["plan-markdown-splitter"] = {
                    "description": "Use this agent when the platform needs to split a generated Markdown test plan into cases JSON",
                    "mode": "subagent",
                    "prompt": "{file:.opencode/prompts/plan-markdown-splitter.md}",
                    "permission": {"external_directory": "allow"},
                    "tools": {"ls": True, "glob": True, "grep": True, "read": True},
                }
                opencode_file.write_text(json.dumps(config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                config_changed = True
    return {"prompt_file": str(prompt_file), "prompt_changed": prompt_changed, "config_changed": config_changed}


def call_agent_json_agent(run_id, step_key, title, payload, agent_name, ensure_agent, result_redactor=None):
    ensure_agent()
    job_id = f"agent-review-{uuid.uuid4().hex}"
    prompt = (
        f"@{agent_name}\n"
        f"{title}\n\n"
        f"{project_copy('Return JSON only. Input:', '请只输出 JSON。输入如下：')}\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )
    create_test_job("agent_review", job_id=job_id, status="running", prompt=prompt)
    agent_set_current_job(run_id, job_id)
    append_agent_event(
        run_id,
        step_key,
        "status",
        agent_message("calling_agent", agent=agent_name),
        {"job_id": job_id},
        job_id=job_id,
    )
    try:
        response = send_opencode_prompt(prompt, default_agent=agent_name)
        text = collect_opencode_response_text(response)
        persisted_text = result_redactor(text) if result_redactor else text
        append_test_job_log(job_id, str(persisted_text)[-JOB_LOG_TAIL_LIMIT:])
        parsed = extract_json_object_from_text(text)
        persisted_parsed = result_redactor(parsed) if result_redactor else parsed
        finish_test_job(job_id, "succeeded")
        append_agent_event(
            run_id,
            step_key,
            "decision",
            agent_message("model_structured"),
            persisted_parsed,
            job_id=job_id,
        )
        return persisted_parsed
    except Exception as exc:
        safe_error = result_redactor(str(exc)) if result_redactor else str(exc)
        failure_message = agent_message("model_failed", error=safe_error)
        append_test_job_log(job_id, f"{failure_message}\n")
        finish_test_job(job_id, "failed", error=str(safe_error))
        append_agent_event(
            run_id,
            step_key,
            "error",
            failure_message,
            {"error": safe_error},
            job_id=job_id,
        )
        raise
    finally:
        agent_set_current_job(run_id, "")


def call_agent_reviewer(run_id, step_key, title, payload):
    return call_agent_json_agent(run_id, step_key, title, payload, "test-platform-reviewer", ensure_test_platform_reviewer_agent)


def call_agent_failure_analyst(run_id, step_key, title, payload):
    return call_agent_json_agent(
        run_id, step_key, title, payload, "test-platform-failure-analyst",
        ensure_test_platform_failure_analyst_agent, agent_failure_handling.redact_agent_failure_value
    )


def normalize_reviewer_decisions(parsed, collection_keys):
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = None
        for key in collection_keys:
            if isinstance(parsed.get(key), list):
                items = parsed[key]
                break
        if items is None and isinstance(parsed.get("decisions"), list):
            items = parsed["decisions"]
        if items is None and "action" in parsed:
            items = [parsed]
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError(agent_message("reviewer_invalid"))

    decisions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "keep").strip().lower()
        if action not in {"keep", "update", "delete", "exclude"}:
            action = "keep"
        decisions.append({**item, "action": action, "reason": str(item.get("reason") or "")})
    return decisions


def agent_analyze_requirement(run_id, requirement):
    step_key = "analyze_requirement"
    agent_start_step(run_id, step_key, {"requirement_uid": requirement.get("requirement_uid")})
    job_id = f"requirement-analysis-{uuid.uuid4().hex}"
    markdown_text = read_requirement_markdown(requirement)
    full_prompt = build_requirement_analysis_prompt(requirement, markdown_text)
    create_test_job("requirement_analysis", job_id=job_id, status="running", prompt=full_prompt)
    agent_set_current_job(run_id, job_id)
    append_agent_event(
        run_id,
        step_key,
        "status",
        agent_message("calling_agent", agent="requirement-analyst"),
        {"job_id": job_id},
        job_id=job_id,
    )
    try:
        response = send_opencode_prompt(full_prompt, default_agent="requirement-analyst")
        output_text = collect_opencode_response_text(response)
        append_test_job_log(job_id, output_text[-JOB_LOG_TAIL_LIMIT:])
        modules = normalize_analysis_json(extract_json_object_from_text(output_text))
        saved_modules = save_requirement_modules_from_analysis(requirement, modules, job_id)
        serialized = [serialize_requirement_module(item) for item in saved_modules]
        finish_test_job(job_id, "succeeded")
        counts = {"generated": len(serialized)}
        agent_finish_step(run_id, step_key, {"modules": serialized, "job_id": job_id}, counts)
        return serialized
    except Exception as exc:
        failure_message = agent_message("analysis_failed", error=exc)
        append_test_job_log(job_id, f"{failure_message}\n")
        finish_test_job(job_id, "failed", error=failure_message)
        raise RuntimeError(failure_message) from exc
    finally:
        agent_set_current_job(run_id, "")


def agent_review_modules(run_id, requirement, modules):
    step_key = "review_modules"
    agent_start_step(run_id, step_key, {"module_count": len(modules)})
    payload = {
        "kind": "requirement_modules",
        "requirement": serialize_requirement(requirement, include_content=False),
        "modules": modules,
        "instructions": (
            "Review each module and its coverage-neutral planner_prompt. Do not add coverage profiles, scenario-type filters, "
            "or test-count limits. Return decisions with module_uid, action, reason, and optional fields for update."
        ),
    }
    decisions = normalize_reviewer_decisions(
        call_agent_reviewer(
            run_id,
            step_key,
            agent_message("review_modules"),
            payload,
        ),
        ["modules"],
    )
    decisions_by_uid = {str(item.get("module_uid") or ""): item for item in decisions}
    kept = []
    counts = {"generated": len(modules), "kept": 0, "updated": 0, "deleted": 0}
    for module_item in modules:
        module_uid = module_item.get("module_uid")
        decision = decisions_by_uid.get(module_uid) or {
            "action": "keep",
            "reason": agent_message("reviewer_missing"),
        }
        action = decision.get("action")
        reason = decision.get("reason") or ""
        if action == "delete":
            delete_requirement_module(requirement["id"], module_uid)
            counts["deleted"] += 1
            append_agent_event(
                run_id,
                step_key,
                "decision",
                agent_message("module_deleted", module=module_item.get("module_name"), reason=reason),
                decision,
            )
            continue
        if action == "update":
            payload_update = {
                "module_name": decision.get("module_name") or module_item.get("module_name"),
                "plan_name": decision.get("plan_name") or module_item.get("plan_name"),
                "business_goal": decision.get("business_goal") or module_item.get("business_goal"),
                "test_points": decision.get("test_points") or module_item.get("test_points") or [],
                "planner_prompt": decision.get("planner_prompt") or module_item.get("planner_prompt"),
                "confidence": decision.get("confidence", module_item.get("confidence")),
                "status": "confirmed",
                "baseline_required": decision.get("baseline_required", module_item.get("baseline_required")),
                "write_risk": decision.get("write_risk", module_item.get("write_risk")),
            }
            updated = update_requirement_module(requirement["id"], module_uid, payload_update)
            module_item = serialize_requirement_module(updated)
            counts["updated"] += 1
            append_agent_event(
                run_id,
                step_key,
                "decision",
                agent_message("module_updated", module=module_item.get("module_name"), reason=reason),
                decision,
            )
        else:
            update_requirement_module(requirement["id"], module_uid, {"status": "confirmed"})
            append_agent_event(
                run_id,
                step_key,
                "decision",
                agent_message("module_kept", module=module_item.get("module_name"), reason=reason),
                decision,
            )
        kept.append(module_item)
    counts["kept"] = len(kept)
    agent_finish_step(run_id, step_key, {"modules": kept, "decisions": decisions}, counts)
    return kept


def find_legacy_agent_plan_job(run_id, module_name):
    """Return the latest prior planner job for an Agent run that predates strategy snapshots."""
    for event in reversed(list_agent_events(run_id, 0, 1000, tail=True)):
        if event.get("step_key") != "generate_plans" or not event.get("job_id"):
            continue
        payload = load_json_column(event.get("payload_json"), {})
        if str(payload.get("module_name") or "").strip() != str(module_name or "").strip():
            continue
        job = get_test_job(event.get("job_id"))
        if job and str(job.get("prompt") or "").strip():
            return job
    return None


def agent_generate_plan_for_module(
    run_id,
    step_key,
    requirement,
    module_item,
    *,
    resume_failure=None,
):
    module_name = validate_module_name(module_item.get("module_name"))
    plan_name = module_item.get("plan_name") or module_name
    run_row = get_agent_run_row(run_id) or {}
    run = serialize_agent_run(run_row) or {}
    plan_generation = normalize_plan_generation_request(run.get("plan_generation") or {})
    profile = plan_generation["coverage_profile"]
    coverage_prompt = plan_generation["coverage_prompt"]
    base_prompt = str(module_item.get("planner_prompt") or "").strip()
    if not base_prompt:
        raise RuntimeError(f"模块缺少 planner prompt：{module_name}")
    user_prompt = compose_editable_plan_prompt(base_prompt, coverage_prompt)
    plan_filename = get_plan_filename_from_name(plan_name, module_name) if get_current_project_language() == "en" else get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=module_name)
    target_file = get_plan_target_path(module_name, plan_filename)
    recovered_artifact = None
    resume_failure = resume_failure if isinstance(resume_failure, dict) else None
    if target_file.exists() and resume_failure:
        try:
            recovered_artifact = validate_multiple_plan_artifact(target_file)
        except (OSError, UnicodeError, ValueError) as exc:
            archive_dir = target_file.parent / ".agent-partials"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archived_file = archive_dir / f"{target_file.stem}-{current_time_ms()}-{uuid.uuid4().hex[:8]}.md"
            target_file.replace(archived_file)
            append_agent_event(
                run_id,
                step_key,
                "decision",
                f"恢复任务发现不完整的源计划，已保留为诊断产物：{archived_file.name}",
                {
                    "recovery_decision": "archive_incomplete",
                    "module_name": module_name,
                    "plan_filename": plan_filename,
                    "archived_path": str(archived_file),
                    "validation_error": str(exc),
                },
                job_id=resume_failure.get("job_id"),
            )
    elif target_file.exists():
        profile_label = get_coverage_profile(profile)["label"]
        plan_name = f"{plan_name}-{profile_label}"
        plan_filename = get_plan_filename_from_name(plan_name, module_name) if get_current_project_language() == "en" else get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=plan_name)
        target_file = get_plan_target_path(module_name, plan_filename)
        if target_file.exists():
            append_agent_event(run_id, step_key, "log", f"计划已存在，跳过生成：{module_name}/{plan_filename}")
            return {"status": "skipped", "module_name": module_name, "plan_filename": plan_filename, "plans": []}
    job_id = f"planner-{uuid.uuid4().hex}"
    has_plan_generation_snapshot = bool(load_json_column(run_row.get("plan_generation_json"), {}))
    legacy_job = find_legacy_agent_plan_job(run_id, module_name) if not has_plan_generation_snapshot else None
    if legacy_job:
        full_prompt = str(legacy_job.get("prompt") or "").strip()
        prompt_context = load_json_column(legacy_job.get("prompt_context_json"), {}) or {
            "template_source": legacy_job.get("coverage_profile") or DEFAULT_COVERAGE_PROFILE,
            "base_prompt": base_prompt,
            "coverage_prompt": coverage_prompt,
            "user_prompt": full_prompt,
            "platform_constraints": "",
            "prompt_customized": bool(legacy_job.get("prompt_customized")),
            "legacy_resume_prompt": True,
        }
    else:
        full_prompt = build_multiple_plan_generation_prompt(user_prompt, module_name, target_file)
        prompt_context = build_plan_prompt_context(
            base_prompt,
            coverage_prompt,
            user_prompt,
            full_prompt,
            profile,
            plan_generation["prompt_customized"],
        )
    create_test_job(
        "planner",
        job_id=job_id,
        status="queued",
        prompt=full_prompt,
        coverage_profile=profile,
        prompt_customized=plan_generation["prompt_customized"],
        prompt_context=prompt_context,
    )
    agent_set_current_job(run_id, job_id)
    append_agent_artifact_progress(
        run_id,
        step_key,
        "running",
        agent_message("generating_plan", module=module_name),
        artifact_type="plan",
        module_uid=module_item.get("module_uid"),
        module_name=module_name,
        plan_filename=plan_filename,
        job_id=job_id,
    )

    if recovered_artifact:
        try:
            append_test_job_log(job_id, f"恢复任务接管已验证的源计划：{target_file}\n")
            append_agent_event(
                run_id,
                step_key,
                "decision",
                f"恢复任务接管已验证的源计划：{module_name}/{plan_filename}",
                {
                    "recovery_decision": "reuse_valid_partial",
                    "module_name": module_name,
                    "plan_filename": plan_filename,
                    "case_count": len(recovered_artifact.get("cases") or []),
                    "source_path": str(target_file),
                },
                job_id=job_id,
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "running",
                f"源计划已恢复，正在拆分：{module_name}/{plan_filename}。",
                artifact_type="plan",
                module_uid=module_item.get("module_uid"),
                module_name=module_name,
                plan_filename=plan_filename,
                plan_phase="splitting",
                job_id=job_id,
            )
            try:
                multiple_result = finalize_multiple_plan_files(
                    module_name,
                    target_file,
                    job_id,
                    source_message=f"agent planner recovered: {module_name}/{plan_filename}",
                    split_message_prefix="agent split recovered plan",
                    requirement=requirement,
                    requirement_module_uid=module_item["module_uid"],
                    run_id=run_id,
                    step_key=step_key,
                    coverage_profile=profile,
                    prompt_customized=plan_generation["prompt_customized"],
                )
            except Exception as exc:
                finish_test_job(job_id, "failed", error=str(exc))
                raise AgentItemFailure(
                    f"拆分恢复计划失败：{exc}",
                    job_id=job_id,
                    error_type="artifact",
                    partial_artifacts=[str(target_file)] if target_file.exists() else [],
                ) from exc
            finish_test_job(job_id, "succeeded")
            return {
                "status": "succeeded",
                "module_name": module_name,
                "plan_filename": plan_filename,
                "plans": multiple_result.get("plans") or [],
                "job_id": job_id,
                "split": multiple_result.get("split"),
                "deleted_source": multiple_result.get("deleted_source"),
                "recovered": True,
            }
        finally:
            agent_set_current_job(run_id, "")

    def finalize_payload():
        return {
            "plan_filename": plan_filename,
            "generation_mode": PLAN_GENERATION_MODE_MULTIPLE,
        }

    try:
        result = consume_agent_sse_generator(
            run_id,
            step_key,
            stream_plan_generation(
                module_name,
                full_prompt,
                target_file,
                default_agent="playwright-test-planner",
                setup_targets=build_setup_targets(),
                setup_parent_run_id=run_id,
                success_payload_factory=finalize_payload,
                session_title=agent_message("agent_plan_title", module=module_name),
                success_message=agent_message("agent_plan_success", target=target_file),
                cancel_job_id=job_id,
                job_id=job_id,
                agent_stream=True,
                finish_job_on_success=False,
                validate_plan_completion=True,
                agent_cancel_check=lambda: agent_raise_if_cancelled(run_id),
            ),
            generator_handles_cancellation=True,
        )
    finally:
        agent_set_current_job(run_id, "")
    if result.get("ok") is False or result.get("status") == "failed":
        raise AgentItemFailure(
            result.get("error") or f"生成计划失败：{module_name}",
            job_id=result.get("job_id") or job_id,
            asset_id=(result.get("asset") or {}).get("asset_id") if isinstance(result.get("asset"), dict) else None,
            error_type=classify_agent_attempt_error(result.get("error") or ""),
            partial_artifacts=[str(target_file)] if target_file.exists() else [],
        )

    if not target_file.exists():
        finish_test_job(job_id, "failed", error=f"未找到目标文件：{target_file}")
        raise AgentItemFailure(
            f"生成计划完成但未找到目标文件：{target_file}",
            job_id=job_id,
            error_type="artifact",
        )

    append_agent_artifact_progress(
        run_id,
        step_key,
        "running",
        agent_message("source_plan_splitting", target=f"{module_name}/{plan_filename}"),
        artifact_type="plan",
        module_uid=module_item.get("module_uid"),
        module_name=module_name,
        plan_filename=plan_filename,
        plan_phase="splitting",
        job_id=job_id,
    )
    try:
        multiple_result = finalize_multiple_plan_files(
            module_name,
            target_file,
            job_id,
            source_message=f"agent planner: {module_name}/{plan_filename}",
            split_message_prefix="agent split plan",
            requirement=requirement,
            requirement_module_uid=module_item["module_uid"],
            run_id=run_id,
            step_key=step_key,
            coverage_profile=profile,
            prompt_customized=plan_generation["prompt_customized"],
        )
    except Exception as exc:
        finish_test_job(job_id, "failed", error=str(exc))
        failure_message = agent_message("split_plan_failed", error=exc)
        append_agent_event(run_id, step_key, "error", failure_message, {"error": failure_message})
        raise AgentItemFailure(
            failure_message,
            job_id=job_id,
            error_type="artifact",
            partial_artifacts=[str(target_file)] if target_file.exists() else [],
        ) from exc

    primary_asset = multiple_result.get("asset") if isinstance(multiple_result, dict) else None
    finish_test_job(
        job_id,
        "succeeded",
        target_asset_id=primary_asset.get("asset_id") if isinstance(primary_asset, dict) else None,
    )
    return {
        "status": "succeeded",
        "module_name": module_name,
        "plan_filename": plan_filename,
        "plans": multiple_result.get("plans") or [],
        "job_id": job_id,
        "split": multiple_result.get("split"),
        "deleted_source": multiple_result.get("deleted_source"),
    }


PLAN_GENERATION_MODE_MULTIPLE = "multiple"


def agent_plan_key(plan):
    if not isinstance(plan, dict):
        return ("", "")
    return (str(plan.get("module_name") or "").strip(), str(plan.get("plan_filename") or "").strip())


def merge_agent_plans(existing_plans, generated_plans):
    merged = []
    indexes = {}
    for plan in [*(existing_plans or []), *(generated_plans or [])]:
        if not isinstance(plan, dict):
            continue
        key = agent_plan_key(plan)
        if not all(key):
            continue
        if key in indexes:
            merged[indexes[key]] = plan
        else:
            indexes[key] = len(merged)
            merged.append(plan)
    return merged


def select_agent_plan_retry_modules(modules, failures):
    failed_uids = {
        str(item.get("module_uid") or "").strip()
        for item in failures or []
        if isinstance(item, dict) and str(item.get("module_uid") or "").strip()
    }
    failed_names = {
        str(item.get("module_name") or "").strip()
        for item in failures or []
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    }
    return [
        item
        for item in modules
        if str(item.get("module_uid") or "").strip() in failed_uids
        or str(item.get("module_name") or "").strip() in failed_names
    ]


def agent_plan_failure_matches_module(failure, module):
    if not isinstance(failure, dict) or not isinstance(module, dict):
        return False
    failure_uid = str(failure.get("module_uid") or "").strip()
    module_uid = str(module.get("module_uid") or "").strip()
    if failure_uid and module_uid and failure_uid == module_uid:
        return True
    failure_name = str(failure.get("module_name") or "").strip()
    module_name = str(module.get("module_name") or "").strip()
    return bool(failure_name and module_name and failure_name == module_name)


def agent_generate_plans(run_id, requirement, modules, resume_output=None):
    step_key = "generate_plans"
    run = serialize_agent_run(get_agent_run_row(run_id)) or {}
    resume_output = resume_output if isinstance(resume_output, dict) else {}
    previous_plans = resume_output.get("plans") if isinstance(resume_output.get("plans"), list) else []
    previous_failures = resume_output.get("failures") if isinstance(resume_output.get("failures"), list) else []
    previous_skipped = resume_output.get("skipped") if isinstance(resume_output.get("skipped"), list) else []
    retry_modules = select_agent_plan_retry_modules(modules, previous_failures) if previous_failures else list(modules)
    step_input = {
        "module_count": len(modules),
        "modules": modules,
        "plan_generation": normalize_plan_generation_request(run.get("plan_generation") or {}),
        "resume": bool(previous_failures),
        "retry_module_count": len(retry_modules),
    }
    agent_start_step(run_id, step_key, step_input)
    generated_plans = merge_agent_plans(previous_plans, [])
    failures = [item for item in previous_failures if isinstance(item, dict)]
    skipped = list(previous_skipped)
    def progress_output():
        return {"plans": generated_plans, "failures": failures, "skipped": skipped}

    def progress_counts():
        return {"generated": len(generated_plans), "failed": len(failures), "skipped": len(skipped), "modules": len(modules)}

    queue_message = (
        agent_message("plan_resume_queue", plans=len(generated_plans), modules=len(retry_modules))
        if previous_failures
        else agent_message("plan_queue", count=len(modules))
    )
    append_agent_artifact_progress(
        run_id,
        step_key,
        "queued",
        queue_message,
        artifact_type="plan",
        input_data=step_input,
        output_data=progress_output(),
        counts=progress_counts(),
    )
    for module_item in retry_modules:
        agent_raise_if_cancelled(run_id)
        module_name = module_item.get("module_name") or ""
        attempt = start_agent_attempt(
            run_id,
            step_key,
            "plan",
            f"{module_name}/{module_item.get('module_uid') or module_name}",
            module_uid=module_item.get("module_uid"),
            module_name=module_name,
            input_snapshot=module_item,
        )
        attempt_id = attempt["attempt_id"]
        try:
            resume_failure = next(
                (
                    item
                    for item in failures
                    if agent_plan_failure_matches_module(item, module_item)
                ),
                None,
            )
            recovery_options = {"resume_failure": resume_failure} if resume_failure else {}
            result = agent_generate_plan_for_module(
                run_id,
                step_key,
                requirement,
                module_item,
                **recovery_options,
            )
            result = {**result, "attempt_id": attempt_id}
            plans = result.get("plans") or []
            failures[:] = [item for item in failures if not agent_plan_failure_matches_module(item, module_item)]
            if result.get("status") == "skipped":
                skipped_item = {
                    "attempt_id": attempt_id,
                    "module_uid": module_item.get("module_uid"),
                    "module_name": result.get("module_name") or module_item.get("module_name"),
                    "plan_filename": result.get("plan_filename"),
                    "status": "skipped",
                }
                skipped.append(skipped_item)
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "skipped",
                    outcome_type="skipped",
                    output_summary=skipped_item,
                )
                append_agent_artifact_progress(
                    run_id,
                    step_key,
                    "skipped",
                    f"计划已存在，跳过生成：{skipped_item.get('module_name')}/{skipped_item.get('plan_filename')}。",
                    artifact_type="plan",
                    output_data=progress_output(),
                    counts=progress_counts(),
                    item=skipped_item,
                    module_uid=skipped_item.get("module_uid"),
                    module_name=skipped_item.get("module_name"),
                    plan_filename=skipped_item.get("plan_filename"),
                    attempt_id=attempt_id,
                )
                continue
            generated_plans[:] = merge_agent_plans(generated_plans, plans)
            asset_refs = [
                {
                    "source": "test_assets",
                    "artifact_type": "plan",
                    "asset_id": (plan.get("asset") or {}).get("asset_id"),
                    "revision_id": (plan.get("asset") or {}).get("current_revision_id"),
                }
                for plan in plans
                if isinstance(plan, dict) and isinstance(plan.get("asset"), dict) and (plan.get("asset") or {}).get("asset_id")
            ]
            primary_asset = next(
                ((plan.get("asset") or {}) for plan in plans if isinstance(plan, dict) and isinstance(plan.get("asset"), dict)),
                {},
            )
            finish_agent_attempt(
                run_id,
                attempt_id,
                "succeeded",
                outcome_type="generated",
                job_id=result.get("job_id"),
                asset_id=primary_asset.get("asset_id"),
                revision_id=primary_asset.get("current_revision_id"),
                output_summary=result,
                artifact_refs=asset_refs,
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "succeeded",
                agent_message("plan_completed", module=module_item.get("module_name"), count=len(plans)),
                artifact_type="plan",
                output_data=progress_output(),
                counts=progress_counts(),
                item=result,
                module_uid=module_item.get("module_uid"),
                module_name=module_item.get("module_name"),
                plan_filename=result.get("plan_filename"),
                job_id=result.get("job_id"),
                attempt_id=attempt_id,
            )
        except Exception as exc:
            failure_context = agent_attempt_failure_context(exc)
            if failure_context["error_type"] == "cancelled":
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "cancelled",
                    job_id=failure_context["job_id"],
                    error_type="cancelled",
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                )
                raise OpencodeTaskCancelled(str(exc)) from exc
            failed_at = current_time_ms()
            failures[:] = [item for item in failures if not agent_plan_failure_matches_module(item, module_item)]
            failure = {
                "attempt_id": attempt_id,
                "failure_id": attempt_id,
                "module_uid": module_item.get("module_uid"),
                "module_name": module_item.get("module_name"),
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "failed_at": failed_at,
                "partial_artifacts": failure_context["partial_artifacts"],
            }
            failures.append(failure)
            finish_agent_attempt(
                run_id,
                attempt_id,
                "failed",
                job_id=failure_context["job_id"],
                test_run_id=failure_context["test_run_id"],
                result_id=failure_context["result_id"],
                asset_id=failure_context["asset_id"],
                error_type=failure_context["error_type"],
                error_message=str(exc),
                error_stack=traceback.format_exc(),
                output_summary=failure,
                artifact_refs=[{"source": "partial", "path": path} for path in failure_context["partial_artifacts"]],
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "failed",
                agent_message("module_plan_failed", module=module_item.get("module_name"), error=exc),
                artifact_type="plan",
                output_data=progress_output(),
                counts=progress_counts(),
                item=failure,
                event_type="error",
                module_uid=module_item.get("module_uid"),
                module_name=module_item.get("module_name"),
                job_id=failure_context["job_id"],
                attempt_id=attempt_id,
            )
    counts = progress_counts()
    if failures:
        raise RuntimeError(agent_message("plans_still_failed", count=len(failures), error=failures[0]["error"]))
    agent_finish_step(run_id, step_key, progress_output(), counts)
    return generated_plans


def build_agent_script_generation_prompt(plan):
    module_name = validate_module_name(plan["module_name"])
    plan_filename = validate_plan_filename(plan["plan_filename"])
    if get_current_project_language() == "en":
        return (
            "@playwright-test-generator\n"
            f"Generate a Playwright test file from specs/{module_name}/{plan_filename}.\n"
            "Each file must contain exactly one test. Use an English business test name by default.\n"
            "The platform supplies a candidate output path; write only there and do not modify the production tests file directly.\n"
            "Implement real code beneath each STEP whenever possible; otherwise explain why it cannot be implemented."
        )
    return (
        "@playwright-test-generator\n"
        f"请根据 specs/{module_name}/{plan_filename} 生成Playwright测试文件。\n"
        "每个测试文件里面只能有一个测试，测试文件名字必须为中文业务测试名.spec.ts，文件名主体不能包含英文字母。\n"
        "平台会在提交任务时提供候选脚本路径，请只把生成结果写入候选路径；不要直接修改正式 tests 文件。\n"
        "注意：每个Step下面尽量生成实际代码，如果实在没有代码，需要说明为什么。"
    )


def agent_generate_script_for_plan(
    run_id,
    step_key,
    plan,
    *,
    original_prompt=None,
    supplemental_prompt="",
):
    module_name = validate_module_name(plan["module_name"])
    plan_filename = validate_plan_filename(plan["plan_filename"])
    plan_file = get_plan_target_path(module_name, plan_filename)
    if not plan_file.exists():
        raise FileNotFoundError(f"测试计划不存在：{plan_file}")
    script_dir = get_script_module_dir(module_name)
    existing_script_names = {item.name for item in script_dir.glob("*.spec.ts") if item.is_file()} if script_dir.exists() else set()
    script_filename = get_generated_script_filename_from_plan_filename(plan_filename)
    target_file = get_script_file(module_name, script_filename)
    plan_asset = sync_plan_asset(module_name, plan_file, change_source="manual", message=f"agent sync plan: {module_name}/{plan_filename}")
    job_id = f"generator-{uuid.uuid4().hex}"
    prompt = str(original_prompt or build_agent_script_generation_prompt(plan)).strip()
    prompt = agent_localization.append_supplemental_prompt(agent_project_language(), prompt, supplemental_prompt, "generation")
    create_test_job("generator", job_id=job_id, status="queued", source_asset_id=plan_asset.get("asset_id") if plan_asset else None, prompt=prompt)
    candidate_file = get_script_generation_candidate_file(module_name, plan_filename, job_id)
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot = managed_file_snapshot(collect_generation_managed_files(module_name, plan_file, target_file))
    target_snapshot = snapshot.get(str(target_file.resolve(strict=False)), {})
    original_target_hash = target_snapshot.get("hash", "")
    agent_set_current_job(run_id, job_id)
    append_agent_artifact_progress(
        run_id,
        step_key,
        "running",
        f"正在生成脚本：{module_name}/{plan_filename}。",
        artifact_type="script",
        module_name=module_name,
        plan_filename=plan_filename,
        filename=script_filename,
        job_id=job_id,
    )

    def has_generated_script_output():
        if candidate_file.exists() and candidate_file.is_file() and candidate_file.stat().st_size > 0:
            return True
        if target_file.exists() and file_hash(target_file) != original_target_hash:
            return True
        return bool(get_new_generated_script_files(script_dir, existing_script_names))

    def finalize_payload():
        payload = finalize_script_generation(
            module_name,
            plan_filename,
            plan_file,
            target_file,
            candidate_file,
            snapshot,
            existing_script_names,
        )
        script_asset = sync_script_asset(
            module_name,
            target_file,
            change_source="generator",
            source_job_id=job_id,
            from_plan_asset_id=plan_asset.get("asset_id") if plan_asset else None,
            message=f"agent generator: {module_name}/{target_file.name}",
        )
        payload["asset"] = serialize_asset(script_asset)
        payload["source_plan_asset"] = serialize_asset(plan_asset)
        return payload

    full_prompt = build_script_generation_prompt(prompt, module_name, plan_file, script_dir, target_file, candidate_file)
    try:
        result = consume_agent_sse_generator(
            run_id,
            step_key,
            stream_plan_generation(
                module_name,
                full_prompt,
                target_file,
                completion_check=has_generated_script_output,
                target_label=str(target_file),
                session_title=agent_message("script_generation_title", target=f"{module_name}/{Path(plan_filename).stem}"),
                success_message=agent_message("script_generation_success", target=target_file),
                default_agent="playwright-test-generator",
                setup_targets=build_setup_targets(
                    module_name=module_name,
                    filename=script_filename,
                ),
                setup_parent_run_id=run_id,
                success_payload_factory=finalize_payload,
                cancel_job_id=job_id,
                job_id=job_id,
                agent_stream=True,
                agent_cancel_check=lambda: agent_raise_if_cancelled(run_id),
            ),
            generator_handles_cancellation=True,
        )
    finally:
        agent_set_current_job(run_id, "")
    if result.get("ok") is False or result.get("status") == "failed":
        partial_artifacts = [
            str(path)
            for path in (candidate_file, target_file)
            if path.exists() and path.is_file()
        ]
        raise AgentItemFailure(
            result.get("error") or f"生成脚本失败：{module_name}/{plan_filename}",
            job_id=result.get("job_id") or job_id,
            asset_id=(result.get("asset") or {}).get("asset_id") if isinstance(result.get("asset"), dict) else None,
            error_type=classify_agent_attempt_error(result.get("error") or ""),
            partial_artifacts=partial_artifacts,
        )
    return {
        "module_name": module_name,
        "plan_filename": plan_filename,
        "filename": result.get("script_filename") or target_file.name,
        "path": result.get("target_path") or str(target_file),
        "asset": result.get("asset"),
        "job_id": job_id,
    }


def agent_generate_scripts(run_id, plans):
    step_key = "generate_scripts"
    step_input = {"plan_count": len(plans), "plans": plans}
    agent_start_step(run_id, step_key, step_input)
    scripts = []
    failures = []

    def progress_output():
        return {"scripts": scripts, "failures": failures}

    def progress_counts():
        return {"generated": len(scripts), "failed": len(failures), "plans": len(plans)}

    append_agent_artifact_progress(
        run_id,
        step_key,
        "queued",
        agent_message("script_generation_queue", count=len(plans)),
        artifact_type="script",
        input_data=step_input,
        output_data=progress_output(),
        counts=progress_counts(),
    )
    for plan in plans:
        agent_raise_if_cancelled(run_id)
        module_name = plan.get("module_name") or ""
        plan_filename = plan.get("plan_filename") or plan.get("filename") or ""
        expected_filename = get_generated_script_filename_from_plan_filename(plan_filename) if plan_filename else ""
        attempt = start_agent_attempt(
            run_id,
            step_key,
            "script",
            f"{module_name}/{plan_filename}",
            module_name=module_name,
            plan_filename=plan_filename,
            filename=expected_filename,
            input_snapshot=plan,
        )
        attempt_id = attempt["attempt_id"]
        try:
            script = agent_generate_script_for_plan(run_id, step_key, plan)
            script = {**script, "attempt_id": attempt_id}
            scripts.append(script)
            asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
            source_asset = script.get("source_plan_asset") if isinstance(script.get("source_plan_asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                attempt_id,
                "succeeded",
                outcome_type="generated",
                job_id=script.get("job_id"),
                asset_id=asset.get("asset_id"),
                revision_id=asset.get("current_revision_id"),
                source_asset_id=source_asset.get("asset_id") or asset.get("from_plan_asset_id"),
                output_summary=script,
                artifact_refs=[
                    {
                        "source": "test_assets",
                        "artifact_type": "script",
                        "asset_id": asset.get("asset_id"),
                        "revision_id": asset.get("current_revision_id"),
                    }
                ] if asset.get("asset_id") else [],
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "succeeded",
                f"脚本生成完成：{script.get('module_name')}/{script.get('filename')}。",
                artifact_type="script",
                output_data=progress_output(),
                counts=progress_counts(),
                item=script,
                module_name=script.get("module_name"),
                plan_filename=script.get("plan_filename"),
                filename=script.get("filename"),
                job_id=script.get("job_id"),
                asset_id=(script.get("asset") or {}).get("asset_id") if isinstance(script.get("asset"), dict) else None,
                attempt_id=attempt_id,
            )
        except Exception as exc:
            failure_context = agent_attempt_failure_context(exc)
            if failure_context["error_type"] == "cancelled":
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "cancelled",
                    job_id=failure_context["job_id"],
                    error_type="cancelled",
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                )
                raise OpencodeTaskCancelled(str(exc)) from exc
            failure = {
                **plan,
                "attempt_id": attempt_id,
                "failure_id": attempt_id,
                "filename": expected_filename,
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "failed_at": current_time_ms(),
                "partial_artifacts": failure_context["partial_artifacts"],
            }
            failures.append(failure)
            finish_agent_attempt(
                run_id,
                attempt_id,
                "failed",
                job_id=failure_context["job_id"],
                test_run_id=failure_context["test_run_id"],
                result_id=failure_context["result_id"],
                asset_id=failure_context["asset_id"],
                source_asset_id=((plan.get("asset") or {}).get("asset_id") if isinstance(plan.get("asset"), dict) else None),
                error_type=failure_context["error_type"],
                error_message=str(exc),
                error_stack=traceback.format_exc(),
                output_summary=failure,
                artifact_refs=[{"source": "partial", "path": path} for path in failure_context["partial_artifacts"]],
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "failed",
                f"脚本生成失败：{plan.get('module_name')}/{plan.get('plan_filename')}，{exc}",
                artifact_type="script",
                output_data=progress_output(),
                counts=progress_counts(),
                item=failure,
                event_type="error",
                module_name=plan.get("module_name"),
                plan_filename=plan.get("plan_filename"),
                filename=expected_filename,
                job_id=failure_context["job_id"],
                attempt_id=attempt_id,
            )
    counts = progress_counts()
    agent_finish_step(run_id, step_key, progress_output(), counts)
    return scripts, failures


def summarize_agent_execution_result(result):
    summary = {}
    for key in (
        "ok",
        "status",
        "returncode",
        "error",
        "run_id",
        "job_id",
        "result_id",
        "output",
        "video",
        "video_error",
        "report",
        "report_error",
    ):
        if key in result:
            summary[key] = result.get(key)
    return summary


def agent_execute_generated_script(run_id, step_key, script):
    module_name = validate_module_name(script["module_name"])
    filename = validate_script_filename(script["filename"])
    setup_targets = build_setup_targets(module_name=module_name, filename=filename)
    setup_resolution = resolve_setup_profile(setup_targets)
    context = build_script_execution_context(module_name, filename, include_database_global_setup=False)
    context["setup_targets"] = setup_targets
    context["setup_resolution"] = setup_resolution
    append_agent_event(run_id, step_key, "status", f"正在执行脚本：{module_name}/{filename}。", {"script": script})
    result = consume_agent_sse_generator(
        run_id,
        step_key,
        stream_script_execution(module_name, filename, context, agent_stream=True),
    )
    execution = summarize_agent_execution_result(result)
    item = {
        **script,
        "execution": execution,
        "execution_run_id": result.get("run_id"),
        "execution_job_id": result.get("job_id"),
    }
    if result.get("ok") is False or result.get("status") == "failed":
        item["error"] = result.get("error") or "脚本执行失败。"
    return item


def agent_execute_generated_scripts(run_id, scripts):
    step_key = "execute_scripts"
    agent_start_step(run_id, step_key, {"script_count": len(scripts), "scripts": scripts})
    passed = []
    failures = []
    for script in scripts:
        agent_raise_if_cancelled(run_id)
        module_name = script.get("module_name") or ""
        filename = script.get("filename") or script.get("plan_filename") or ""
        attempt = start_agent_attempt(
            run_id,
            step_key,
            "script_execution",
            f"{module_name}/{filename}",
            module_name=module_name,
            plan_filename=script.get("plan_filename"),
            filename=filename,
            input_snapshot=script,
        )
        attempt_id = attempt["attempt_id"]
        try:
            result = agent_execute_generated_script(run_id, step_key, script)
            result = {**result, "attempt_id": attempt_id}
            execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
            asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
            execution_refs = [
                {
                    "source": "test_runs",
                    "test_run_id": result.get("execution_run_id"),
                    "result_id": execution.get("result_id"),
                }
            ]
            if result.get("error"):
                failure = {
                    **result,
                    "failure_id": attempt_id,
                    "job_id": result.get("execution_job_id") or execution.get("job_id") or "",
                    "test_run_id": result.get("execution_run_id") or execution.get("run_id") or "",
                    "result_id": execution.get("result_id"),
                    "error_type": classify_agent_attempt_error(result.get("error")),
                    "failed_at": current_time_ms(),
                    "partial_artifacts": [],
                }
                result = failure
                failures.append(result)
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "failed",
                    verification_status="failed",
                    job_id=failure["job_id"],
                    test_run_id=failure["test_run_id"],
                    result_id=failure["result_id"],
                    asset_id=asset.get("asset_id"),
                    revision_id=asset.get("current_revision_id"),
                    error_type=failure["error_type"],
                    error_message=result.get("error"),
                    output_summary=result,
                    artifact_refs=execution_refs,
                )
                append_agent_event(
                    run_id,
                    step_key,
                    "error",
                    f"脚本执行失败，进入修复：{script.get('module_name')}/{script.get('filename')}，{result.get('error')}",
                    result,
                    job_id=failure["job_id"],
                    asset_id=asset.get("asset_id"),
                    test_run_id=failure["test_run_id"],
                )
            else:
                passed.append(result)
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "succeeded",
                    outcome_type="passed",
                    verification_status="passed",
                    job_id=result.get("execution_job_id") or execution.get("job_id"),
                    test_run_id=result.get("execution_run_id") or execution.get("run_id"),
                    result_id=execution.get("result_id"),
                    asset_id=asset.get("asset_id"),
                    revision_id=asset.get("current_revision_id"),
                    output_summary=result,
                    artifact_refs=execution_refs,
                )
                append_agent_event(
                    run_id,
                    step_key,
                    "status",
                    f"脚本执行通过：{script.get('module_name')}/{script.get('filename')}。",
                    result,
                    job_id=result.get("execution_job_id") or execution.get("job_id"),
                    asset_id=asset.get("asset_id"),
                    test_run_id=result.get("execution_run_id") or execution.get("run_id"),
                )
        except Exception as exc:
            failure_context = agent_attempt_failure_context(exc)
            if failure_context["error_type"] == "cancelled":
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "cancelled",
                    job_id=failure_context["job_id"],
                    test_run_id=failure_context["test_run_id"],
                    result_id=failure_context["result_id"],
                    error_type="cancelled",
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                )
                raise OpencodeTaskCancelled(str(exc)) from exc
            failure = {
                **script,
                "attempt_id": attempt_id,
                "failure_id": attempt_id,
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "failed_at": current_time_ms(),
                "partial_artifacts": failure_context["partial_artifacts"],
                "execution": {"status": "failed", "error": str(exc)},
            }
            failures.append(failure)
            asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                attempt_id,
                "failed",
                verification_status="failed",
                job_id=failure_context["job_id"],
                test_run_id=failure_context["test_run_id"],
                result_id=failure_context["result_id"],
                asset_id=asset.get("asset_id") or failure_context["asset_id"],
                revision_id=asset.get("current_revision_id"),
                error_type=failure_context["error_type"],
                error_message=str(exc),
                error_stack=traceback.format_exc(),
                output_summary=failure,
                artifact_refs=[{"source": "partial", "path": path} for path in failure_context["partial_artifacts"]],
            )
            append_agent_event(
                run_id,
                step_key,
                "error",
                f"脚本执行异常，进入修复：{script.get('module_name')}/{script.get('filename')}，{exc}",
                failure,
                job_id=failure_context["job_id"],
                asset_id=asset.get("asset_id") or failure_context["asset_id"],
                test_run_id=failure_context["test_run_id"],
            )
    counts = {"passed": len(passed), "failed": len(failures), "scripts": len(scripts)}
    agent_finish_step(run_id, step_key, {"scripts": passed, "failures": failures}, counts)
    return passed, failures


def build_agent_script_repair_prompt(item, failure=None):
    item = item if isinstance(item, dict) else {}
    module_name = validate_module_name(item["module_name"])
    filename = validate_script_filename(item["filename"])
    plan_filename = validate_plan_filename(
        item.get("plan_filename") or f"{module_name}.md"
    )
    if get_current_project_language() == "en":
        prompt = (
            "@playwright-test-healer\n"
            f"Use test plan specs/{module_name}/{plan_filename} to repair tests/{module_name}/{filename}.\n"
            "Requirements:\n1. Do not delete or comment out any STEP.\n2. Preserve the execution video."
        )
    else:
        prompt = (
            "@playwright-test-healer\n"
            f"请根据测试计划 specs/{module_name}/{plan_filename}，修复 tests/{module_name}/{filename}\n"
            "要求：\n"
            "1. 不允许删除或注释任何 STEP。\n"
            "2. 保留执行视频。"
        )
    if isinstance(failure, dict) and failure:
        prompt += (
            "\n\nPrevious failure details:\n"
            if get_current_project_language() == "en"
            else "\n\n上次失败信息：\n"
        ) + json.dumps(
            failure, ensure_ascii=False, indent=2
        )
    return prompt


def agent_repair_script(
    run_id,
    step_key,
    script,
    *,
    failure=None,
    original_prompt=None,
    supplemental_prompt="",
):
    module_name = validate_module_name(script["module_name"])
    filename = validate_script_filename(script["filename"])
    script_file = get_script_file(module_name, filename)
    if not script_file.exists():
        raise FileNotFoundError(f"测试脚本不存在：{script_file}")
    prompt = str(original_prompt or build_agent_script_repair_prompt(script, failure)).strip()
    prompt = agent_localization.append_supplemental_prompt(agent_project_language(), prompt, supplemental_prompt, "repair")
    script_asset = sync_script_asset(module_name, script_file, change_source="manual", message=f"agent sync script: {module_name}/{filename}")
    job_id = f"healer-{uuid.uuid4().hex}"
    create_test_job("healer", job_id=job_id, status="queued", target_asset_id=script_asset.get("asset_id") if script_asset else None, prompt=prompt)
    started_at = time.time()
    agent_set_current_job(run_id, job_id)
    append_agent_artifact_progress(
        run_id,
        step_key,
        "running",
        f"正在修复脚本：{module_name}/{filename}。",
        artifact_type="script",
        module_name=module_name,
        filename=filename,
        job_id=job_id,
    )

    def finalize_payload():
        result = build_run_video_result(started_at)
        updated_asset = sync_script_asset(
            module_name,
            script_file,
            change_source="healer",
            source_job_id=job_id,
            message=f"agent healer: {module_name}/{filename}",
        )
        result["asset"] = serialize_asset(updated_asset)
        return result

    full_prompt = build_script_run_prompt(prompt, module_name, filename, script_file)
    try:
        result = consume_agent_sse_generator(
            run_id,
            step_key,
            stream_plan_generation(
                module_name,
                full_prompt,
                script_file,
                completion_check=lambda: False,
                completion_required=False,
                target_label=str(script_file),
                session_title=agent_message("script_repair_title", target=filename),
                success_message=agent_message("script_repair_success", target=script_file),
                success_payload_factory=finalize_payload,
                default_agent="playwright-test-healer",
                setup_targets=build_setup_targets(
                    module_name=module_name,
                    filename=filename,
                ),
                setup_parent_run_id=run_id,
                cancel_job_id=job_id,
                job_id=job_id,
                agent_stream=True,
                agent_cancel_check=lambda: agent_raise_if_cancelled(run_id),
            ),
            generator_handles_cancellation=True,
        )
    finally:
        agent_set_current_job(run_id, "")
    if result.get("ok") is False or result.get("status") == "failed":
        raise AgentItemFailure(
            result.get("error") or f"修复脚本失败：{module_name}/{filename}",
            job_id=result.get("job_id") or job_id,
            test_run_id=result.get("run_id"),
            result_id=result.get("result_id"),
            asset_id=(result.get("asset") or {}).get("asset_id") if isinstance(result.get("asset"), dict) else None,
            error_type=classify_agent_attempt_error(result.get("error") or ""),
            partial_artifacts=[str(script_file)] if script_file.exists() else [],
        )
    return {
        **script,
        "asset": result.get("asset") or script.get("asset"),
        "repair_job_id": job_id,
        "repair_test_run_id": result.get("run_id") or "",
        "repair_result_id": result.get("result_id"),
    }


def agent_repair_scripts(run_id, scripts):
    step_key = "repair_scripts"
    step_input = {"script_count": len(scripts), "scripts": scripts}
    agent_start_step(run_id, step_key, step_input)
    repaired = []
    failures = []

    def progress_output():
        return {"scripts": repaired, "failures": failures}

    def progress_counts():
        return {"repaired": len(repaired), "failed": len(failures), "scripts": len(scripts)}

    append_agent_artifact_progress(
        run_id,
        step_key,
        "queued",
        agent_message("script_repair_queue", count=len(scripts)),
        artifact_type="script",
        input_data=step_input,
        output_data=progress_output(),
        counts=progress_counts(),
    )
    for script in scripts:
        agent_raise_if_cancelled(run_id)
        module_name = script.get("module_name") or ""
        filename = script.get("filename") or script.get("plan_filename") or ""
        attempt = start_agent_attempt(
            run_id,
            step_key,
            "script_repair",
            f"{module_name}/{filename}",
            module_name=module_name,
            plan_filename=script.get("plan_filename"),
            filename=filename,
            input_snapshot=script,
        )
        attempt_id = attempt["attempt_id"]
        try:
            repaired_script = agent_repair_script(run_id, step_key, script)
            repaired_script = {**repaired_script, "attempt_id": attempt_id}
            repaired.append(repaired_script)
            asset = repaired_script.get("asset") if isinstance(repaired_script.get("asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                attempt_id,
                "succeeded",
                outcome_type="repaired",
                verification_status="not_run",
                job_id=repaired_script.get("repair_job_id"),
                test_run_id=repaired_script.get("repair_test_run_id"),
                result_id=repaired_script.get("repair_result_id"),
                asset_id=asset.get("asset_id"),
                revision_id=asset.get("current_revision_id"),
                source_asset_id=asset.get("from_plan_asset_id"),
                output_summary=repaired_script,
                artifact_refs=[
                    {
                        "source": "test_assets",
                        "artifact_type": "script",
                        "asset_id": asset.get("asset_id"),
                        "revision_id": asset.get("current_revision_id"),
                    }
                ] if asset.get("asset_id") else [],
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "repaired",
                f"脚本修复完成：{repaired_script.get('module_name')}/{repaired_script.get('filename')}。",
                artifact_type="script",
                output_data=progress_output(),
                counts=progress_counts(),
                item=repaired_script,
                module_name=repaired_script.get("module_name"),
                filename=repaired_script.get("filename"),
                job_id=repaired_script.get("repair_job_id"),
                asset_id=(repaired_script.get("asset") or {}).get("asset_id") if isinstance(repaired_script.get("asset"), dict) else None,
                attempt_id=attempt_id,
            )
        except Exception as exc:
            failure_context = agent_attempt_failure_context(exc)
            if failure_context["error_type"] == "cancelled":
                finish_agent_attempt(
                    run_id,
                    attempt_id,
                    "cancelled",
                    job_id=failure_context["job_id"],
                    test_run_id=failure_context["test_run_id"],
                    result_id=failure_context["result_id"],
                    error_type="cancelled",
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                )
                raise OpencodeTaskCancelled(str(exc)) from exc
            failure = {
                **script,
                "attempt_id": attempt_id,
                "failure_id": attempt_id,
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "failed_at": current_time_ms(),
                "partial_artifacts": failure_context["partial_artifacts"],
            }
            failures.append(failure)
            source_asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                attempt_id,
                "failed",
                verification_status="failed",
                job_id=failure_context["job_id"],
                test_run_id=failure_context["test_run_id"],
                result_id=failure_context["result_id"],
                asset_id=failure_context["asset_id"] or source_asset.get("asset_id"),
                revision_id=source_asset.get("current_revision_id"),
                source_asset_id=source_asset.get("from_plan_asset_id"),
                error_type=failure_context["error_type"],
                error_message=str(exc),
                error_stack=traceback.format_exc(),
                output_summary=failure,
                artifact_refs=[{"source": "partial", "path": path} for path in failure_context["partial_artifacts"]],
            )
            append_agent_artifact_progress(
                run_id,
                step_key,
                "failed",
                f"脚本修复失败：{script.get('module_name')}/{script.get('filename')}，{exc}",
                artifact_type="script",
                output_data=progress_output(),
                counts=progress_counts(),
                item=failure,
                event_type="error",
                module_name=script.get("module_name"),
                filename=script.get("filename"),
                job_id=failure_context["job_id"],
                attempt_id=attempt_id,
            )
    counts = progress_counts()
    agent_finish_step(run_id, step_key, progress_output(), counts)
    return repaired, failures


def agent_execute_single_script_for_review(run_id, step_key, script):
    module_name = validate_module_name(script["module_name"])
    filename = validate_script_filename(script["filename"])
    setup_targets = build_setup_targets(module_name=module_name, filename=filename)
    setup_resolution = resolve_setup_profile(setup_targets)
    context = build_script_execution_context(module_name, filename, include_database_global_setup=False)
    context["setup_targets"] = setup_targets
    context["setup_resolution"] = setup_resolution
    result = consume_agent_sse_generator(
        run_id,
        step_key,
        stream_script_execution(module_name, filename, context, agent_stream=True),
    )
    if result.get("ok") is False or result.get("status") == "failed":
        raise RuntimeError(result.get("error") or "脚本验证失败。")
    return result


def get_agent_script_preparation_output(run_id, step_key):
    row = get_agent_step_row(run_id, step_key)
    if not row:
        return None
    output = load_json_column(row.get("output_json"), None)
    return output if isinstance(output, dict) else None


def get_agent_script_preparation_item_for_web(run_id, item_id):
    item = agent_script_preparation.get_script_preparation_item(run_id, item_id)
    script = item.get("current_script") if isinstance(item.get("current_script"), dict) else None
    if script:
        script_file = get_script_file(item["module_name"], item["filename"])
        if script_file.is_file():
            item["current_script"] = {
                **script,
                "content": script_file.read_text(encoding="utf-8"),
            }
    return item


def save_agent_prepared_script(run_id, item, content, expected_revision_id=None):
    del run_id, expected_revision_id
    module_name = validate_module_name(item["module_name"])
    filename = validate_script_filename(item["filename"])
    script_file = get_script_file(module_name, filename)
    script_file.parent.mkdir(parents=True, exist_ok=True)
    script_file.write_text(str(content), encoding="utf-8", newline="")
    asset = sync_script_asset(
        module_name,
        script_file,
        change_source="manual",
        message=f"agent manual edit: {module_name}/{filename}",
    )
    return {
        "module_name": module_name,
        "plan_filename": item.get("plan_filename") or "",
        "filename": filename,
        "path": str(script_file),
        "asset": serialize_asset(asset),
    }


def analyze_agent_script_preparation_failure(run_id, step_key, payload):
    return call_agent_failure_analyst(
        run_id,
        step_key,
        agent_message("failure_analysis_instruction"),
        payload,
    )


def resolve_agent_script_preparation_dependency(name):
    dependencies = {
        "load_step_output": get_agent_script_preparation_output,
        "get_agent_run": get_agent_run_row,
        "update_agent_step": update_agent_step,
        "update_agent_run": update_agent_run,
        "append_agent_event": append_agent_event,
        "generate_script": agent_generate_script_for_plan,
        "execute_script": agent_execute_generated_script,
        "repair_script": agent_repair_script,
        "analyze_failure": analyze_agent_script_preparation_failure,
        "save_script": save_agent_prepared_script,
        "build_generation_prompt": build_agent_script_generation_prompt,
        "build_repair_prompt": build_agent_script_repair_prompt,
        "resolve_script_filename": lambda plan: get_generated_script_filename_from_plan_filename(
            plan["plan_filename"]
        ),
        "current_time_ms": current_time_ms,
        "redact_value": lambda value: value,
        "is_cancelled_error": lambda error: isinstance(error, OpencodeTaskCancelled),
        "make_id": lambda prefix: f"{prefix}-{uuid.uuid4().hex}",
        "waiting_run_status": "awaiting_script_action",
        "get_project_language": agent_project_language,
    }
    return dependencies[name]


agent_script_preparation.configure_script_preparation(
    agent_script_preparation.script_preparation_dependencies_from_resolver(
        resolve_agent_script_preparation_dependency
    )
)


def agent_create_suite(run_id, requirement, scripts):
    step_key = "create_suite"
    agent_start_step(run_id, step_key, {"script_count": len(scripts)})
    unique = {}
    for script in scripts:
        module_name = script.get("module_name")
        filename = script.get("filename")
        if module_name and filename:
            unique[(module_name, filename)] = script
    if not unique:
        raise RuntimeError("没有可加入测试集的有效脚本。")
    title = re.sub(r"\s+", "-", (requirement.get("title") or requirement.get("filename") or "requirement").strip())
    title = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", title).strip("-")[:48] or "requirement"
    suite_name = f"Agent-{title}-{time.strftime('%Y%m%d-%H%M')}"
    suite = create_test_suite_in_mysql(suite_name, f"Agent run {run_id} 自动创建。")
    items = [
        {
            "module_name": module_name,
            "filename": filename,
            "display_name": Path(filename).stem,
            "path": str(get_script_file(module_name, filename)),
        }
        for module_name, filename in unique
    ]
    suite = add_test_suite_items_in_mysql(suite["id"], items)
    update_agent_run(run_id, suite_uid=suite.get("id"))
    counts = {"scripts": len(items), "suite_count": 1}
    append_agent_event(run_id, step_key, "decision", agent_message("suite_created", name=suite.get("name"), count=len(items)), {"suite": suite})
    agent_finish_step(run_id, step_key, {"suite": suite}, counts)
    return suite


def agent_run_suite(run_id, suite):
    step_key = "run_suite"
    items = suite.get("items") or []
    agent_start_step(run_id, step_key, {"suite_uid": suite.get("id"), "script_count": len(items)})
    if not items:
        raise RuntimeError("测试集没有可执行脚本。")
    setup_targets = build_setup_targets(suite_uid=suite["id"], items=items)
    setup_resolution = resolve_setup_profile(setup_targets)
    context = build_test_suite_execution_context(
        items,
        EXECUTION_MODE_SERIAL_PER_FILE,
        include_database_global_setup=False,
    )
    context["setup_targets"] = setup_targets
    context["setup_resolution"] = setup_resolution
    result = consume_agent_sse_generator(
        run_id,
        step_key,
        stream_test_suite_execution(
            suite["id"],
            suite["name"],
            context["items"],
            context,
            agent_stream=True,
        ),
    )
    summary = build_execution_summary(result.get("script_results") or {}, result.get("returncode"))
    counts = {
        "total": summary.get("total", 0),
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "skipped": summary.get("skipped", 0),
        "unknown": summary.get("unknown", 0),
    }
    output = {"result": result, "summary": summary}
    if result.get("ok") is False or result.get("status") == "failed":
        agent_finish_step(run_id, step_key, output, counts)
        raise RuntimeError(result.get("error") or "测试集执行失败。")
    agent_finish_step(run_id, step_key, output, counts)
    return output


def finish_agent_after_script_preparation(
    run_id,
    requirement,
    modules,
    plans,
    scripts,
    preparation,
    *,
    suite=None,
    resumed_from_step="",
):
    counts = preparation.get("counts") if isinstance(preparation, dict) else {}
    if scripts:
        suite = suite or agent_create_suite(run_id, requirement, scripts)
        execution = agent_run_suite(run_id, suite)
        execution_summary = execution.get("summary") or {}
        final_status = "succeeded"
    else:
        skip_reason = "所有脚本均已放弃，没有脚本进入测试集。"
        for step_key, output, skipped_counts in (
            ("create_suite", {"suite": None}, {"scripts": 0, "skipped": 1}),
            ("run_suite", {"summary": {}}, {"total": 0, "skipped": 1}),
        ):
            update_agent_step(
                run_id, step_key, status="skipped",
                input_data={"script_count": 0},
                output_data={**output, "reason": skip_reason},
                counts=skipped_counts, error="", started=True, finished=True,
            )
        suite = None
        execution_summary = {}
        final_status = "succeeded_with_unresolved"
    final_summary = {
        "requirement": serialize_requirement(requirement, include_content=False),
        "module_count": len(modules),
        "plan_count": len(plans),
        "script_count": len(scripts),
        "abandoned_script_count": int((counts or {}).get("abandoned") or 0),
        "suite": suite,
        "execution": execution_summary,
        "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
    }
    if resumed_from_step:
        final_summary["resumed_from_step"] = resumed_from_step
    append_agent_event(run_id, "run_suite", "status", "Agent 全流程执行完成。", final_summary)
    update_agent_run(
        run_id,
        status=final_status,
        current_step="run_suite",
        summary=final_summary,
        error="",
        finished=True,
    )


def claim_agent_script_preparation_continue(run_id):
    config = require_platform_database()
    return agent_script_preparation.claim_script_preparation_continue_record(
        connection_factory=lambda: platform_mysql_connection(config),
        runs_table=get_agent_runs_table(config),
        steps_table=get_agent_run_steps_table(config),
        project_id=get_current_project_id(),
        run_id=validate_uid(run_id, "run_id"),
        now_ms=current_time_ms(),
    )


def get_prepared_scripts(preparation):
    return [
        item["current_script"]
        for item in (preparation or {}).get("items") or []
        if item.get("status") == "ready"
        and item.get("included_in_suite")
        and isinstance(item.get("current_script"), dict)
    ]


def mark_agent_workflow_cancelled(run_id, error):
    steps = [serialize_agent_step(row) for row in list_agent_steps(run_id)]
    for step in steps:
        if step.get("status") == "running":
            update_agent_step(
                run_id,
                step["step_key"],
                status="cancelled",
                error=str(error),
                finished=True,
            )
    append_agent_event(run_id, "", "status", "Agent 任务已取消。", {"error": str(error)})
    update_agent_run(run_id, status="cancelled", error=str(error), finished=True)


def mark_agent_workflow_failed(run_id, error, fallback_step=""):
    current_step = (get_agent_run_row(run_id) or {}).get("current_step") or fallback_step
    if current_step:
        agent_fail_step(run_id, current_step, error)
    append_agent_event(
        run_id,
        current_step,
        "error",
        f"Agent 任务失败：{error}",
        {"error": str(error)},
    )
    update_agent_run(run_id, status="failed", error=str(error), finished=True)


def run_agent_workflow(run_id, project, author):
    agent_register_task(run_id)
    with use_project_context(project), use_author_context(f"agent:{author or 'platform'}"):
        try:
            run = get_agent_run_row(run_id)
            if not run:
                return
            language = load_json_column(run.get("summary_json"), {}).get("language")
            if language:
                PROJECT_CONTEXT.project = {**project, "language": normalize_project_language(language)}
            requirement = get_requirement_by_uid(run.get("requirement_uid"))
            if not requirement:
                raise RuntimeError("需求不存在。")
            update_agent_run(run_id, status="running", current_step="upload_requirement")
            update_agent_step(
                run_id,
                "upload_requirement",
                status="succeeded",
                output_data={"requirement": serialize_requirement(requirement, include_content=False)},
                counts={"uploaded": 1},
                started=True,
                finished=True,
            )
            append_agent_event(
                run_id,
                "upload_requirement",
                "status",
                "需求已准备完成。",
                {"requirement_uid": requirement.get("requirement_uid")},
            )
            modules = agent_review_modules(
                run_id,
                requirement,
                agent_analyze_requirement(run_id, requirement),
            )
            plans = agent_generate_plans(run_id, requirement, modules)
            preparation = agent_script_preparation.run_agent_script_preparation(run_id, plans)
            if preparation.get("paused"):
                return
            finish_agent_after_script_preparation(
                run_id,
                requirement,
                modules,
                plans,
                preparation.get("final_scripts") or [],
                preparation,
            )
        except OpencodeTaskCancelled as exc:
            mark_agent_workflow_cancelled(run_id, exc)
        except Exception as exc:
            mark_agent_workflow_failed(run_id, exc)
        finally:
            agent_set_current_job(run_id, "")
            agent_cleanup_task(run_id)


def run_agent_script_preparation_continue_workflow(run_id, project, author):
    agent_register_task(run_id)
    with use_project_context(project), use_author_context(f"agent:{author or 'platform'}"):
        try:
            run = get_agent_run_row(run_id)
            if not run or run.get("status") in AGENT_TERMINAL_STATUSES:
                return
            language = load_json_column(run.get("summary_json"), {}).get("language")
            if language:
                PROJECT_CONTEXT.project = {**project, "language": normalize_project_language(language)}
            requirement = get_requirement_by_uid(run.get("requirement_uid"), True)
            if not requirement:
                raise RuntimeError("需求不存在。")
            modules = require_agent_step_list_output(run_id, "review_modules", "modules")
            plans = require_agent_step_list_output(run_id, "generate_plans", "plans")
            preparation = agent_script_preparation.get_script_preparation_snapshot(run_id)
            update_agent_run(run_id, status="running", current_step="create_suite", error="")
            finish_agent_after_script_preparation(
                run_id,
                requirement,
                modules,
                plans,
                get_prepared_scripts(preparation),
                preparation,
            )
        except OpencodeTaskCancelled as exc:
            mark_agent_workflow_cancelled(run_id, exc)
        except Exception as exc:
            mark_agent_workflow_failed(run_id, exc, "prepare_scripts")
        finally:
            agent_set_current_job(run_id, "")
            agent_cleanup_task(run_id)


def run_agent_resume_workflow(run_id, project, author, from_step, resume_context=None):
    from_step = validate_agent_step_key(from_step)
    resume_context = resume_context if isinstance(resume_context, dict) else {}
    resume_index = AGENT_STEP_INDEX_BY_KEY[from_step]
    agent_register_task(run_id)
    with use_project_context(project), use_author_context(f"agent:{author or 'platform'}"):
        try:
            run = get_agent_run_row(run_id)
            if not run:
                return
            language = load_json_column(run.get("summary_json"), {}).get("language")
            if language:
                PROJECT_CONTEXT.project = {**project, "language": normalize_project_language(language)}
            requirement = get_requirement_by_uid(run.get("requirement_uid"), True)
            if not requirement:
                raise RuntimeError("需求不存在。")
            append_agent_event(
                run_id,
                from_step,
                "status",
                f"开始从步骤继续执行：{agent_step_name(from_step)}。",
                {"from_step": from_step},
            )
            if resume_index == 0:
                update_agent_run(run_id, status="running", current_step="upload_requirement")
                update_agent_step(
                    run_id,
                    "upload_requirement",
                    status="succeeded",
                    output_data={"requirement": serialize_requirement(requirement, include_content=False)},
                    counts={"uploaded": 1},
                    started=True,
                    finished=True,
                )
            modules = (
                agent_analyze_requirement(run_id, requirement)
                if resume_index <= AGENT_STEP_INDEX_BY_KEY["analyze_requirement"]
                else require_agent_step_list_output(run_id, "analyze_requirement", "modules")
            )
            if resume_index <= AGENT_STEP_INDEX_BY_KEY["review_modules"]:
                modules = agent_review_modules(run_id, requirement, modules)
            else:
                modules = require_agent_step_list_output(run_id, "review_modules", "modules")
            plans = (
                agent_generate_plans(
                    run_id,
                    requirement,
                    modules,
                    resume_output=resume_context.get("generate_plans"),
                )
                if resume_index <= AGENT_STEP_INDEX_BY_KEY["generate_plans"]
                else require_agent_step_list_output(run_id, "generate_plans", "plans")
            )
            if resume_index <= AGENT_STEP_INDEX_BY_KEY["prepare_scripts"]:
                preparation = agent_script_preparation.run_agent_script_preparation(run_id, plans)
                scripts = preparation.get("final_scripts") or []
            else:
                preparation = agent_script_preparation.get_script_preparation_snapshot(run_id)
                scripts = get_prepared_scripts(preparation)
            if preparation.get("paused"):
                return
            suite = None
            if resume_index > AGENT_STEP_INDEX_BY_KEY["create_suite"]:
                suite = get_agent_step_output(run_id, "create_suite").get("suite")
                if not isinstance(suite, dict):
                    raise RuntimeError("测试集步骤输出格式无效，无法恢复。")
            finish_agent_after_script_preparation(
                run_id,
                requirement,
                modules,
                plans,
                scripts,
                preparation,
                suite=suite,
                resumed_from_step=from_step,
            )
        except OpencodeTaskCancelled as exc:
            mark_agent_workflow_cancelled(run_id, exc)
        except Exception as exc:
            mark_agent_workflow_failed(run_id, exc, from_step)
        finally:
            agent_set_current_job(run_id, "")
            agent_cleanup_task(run_id)


def start_agent_thread(run_id, project, author):
    thread = threading.Thread(target=run_agent_workflow, args=(run_id, project, author), daemon=True)
    thread.start()
    return thread


def start_agent_script_preparation_continue_thread(run_id, project, author):
    thread = threading.Thread(
        target=run_agent_script_preparation_continue_workflow,
        args=(run_id, project, author),
        daemon=True,
    )
    thread.start()
    return thread


def start_agent_resume_thread(run_id, project, author, from_step, resume_context=None):
    thread = threading.Thread(
        target=run_agent_resume_workflow,
        args=(run_id, project, author, from_step, resume_context),
        daemon=True,
    )
    thread.start()
    return thread


def get_agent_retry_plan_from_attempt(attempt):
    attempt = attempt if isinstance(attempt, dict) else {}
    input_snapshot = load_json_column(attempt.get("input_snapshot_json"), {})
    output_summary = load_json_column(attempt.get("output_summary_json"), {})
    plan = {}
    if isinstance(output_summary, dict):
        plan.update(output_summary)
    if isinstance(input_snapshot, dict):
        plan.update(input_snapshot)
    plan["module_name"] = str(attempt.get("module_name") or plan.get("module_name") or "").strip()
    plan["plan_filename"] = str(
        attempt.get("plan_filename") or plan.get("plan_filename") or plan.get("filename") or ""
    ).strip()
    if not plan["module_name"] or not plan["plan_filename"]:
        raise ValueError("失败记录缺少模块或计划文件信息，无法重新生成脚本。")
    plan["module_name"] = validate_module_name(plan["module_name"])
    plan["plan_filename"] = validate_plan_filename(plan["plan_filename"])
    return plan


def is_current_agent_generation_failure(run_id, attempt):
    attempt = attempt if isinstance(attempt, dict) else {}
    attempt_id = str(attempt.get("attempt_id") or "")
    if not attempt_id:
        return False
    step = get_agent_step_row(run_id, "generate_scripts")
    output = load_json_column((step or {}).get("output_json"), {})
    failures = output.get("failures") if isinstance(output, dict) else []
    return any(
        str(item.get("attempt_id") or item.get("failure_id") or "") == attempt_id
        for item in failures
        if isinstance(item, dict)
    )


def build_agent_retry_failure_item(flow, attempt_id, source, error, error_context=None, **extra):
    context = error_context if isinstance(error_context, dict) else agent_attempt_failure_context(error)
    return {
        **(source if isinstance(source, dict) else {}),
        "attempt_id": attempt_id,
        "failure_id": attempt_id,
        "retry_flow_id": flow.get("retry_flow_id") or "",
        "item_key": flow.get("item_key") or "",
        "module_name": flow.get("module_name") or (source or {}).get("module_name") or "",
        "plan_filename": flow.get("plan_filename") or (source or {}).get("plan_filename") or "",
        "filename": flow.get("filename") or (source or {}).get("filename") or "",
        "job_id": context.get("job_id") or "",
        "test_run_id": context.get("test_run_id") or "",
        "result_id": context.get("result_id"),
        "error_type": context.get("error_type") or classify_agent_attempt_error(error),
        "error": str(error or "单项重试失败。"),
        "failed_at": current_time_ms(),
        "partial_artifacts": list(context.get("partial_artifacts") or []),
        **extra,
    }


def agent_retry_execution_artifact_refs(execution_item):
    execution = execution_item.get("execution") if isinstance(execution_item.get("execution"), dict) else {}
    test_run_id = execution_item.get("execution_run_id") or execution.get("run_id") or ""
    result_id = execution.get("result_id")
    if not test_run_id and result_id is None:
        return []
    return [{"source": "test_runs", "test_run_id": test_run_id, "result_id": result_id}]


def classify_agent_retry_execution_error(error, execution=None):
    message = str(error or "")
    lowered = message.lower()
    if "数据库" in lowered or "baseline" in lowered:
        return "environment"
    execution = execution if isinstance(execution, dict) else {}
    if execution.get("returncode") not in (None, 0, "0"):
        return "execution"
    error_type = classify_agent_attempt_error(message)
    if error_type == "unknown" and execution.get("status") in {"failed", "timedOut", "interrupted"}:
        return "execution"
    return error_type


def execute_agent_retry_script(run_id, step_key, script):
    """Execute one retry script while preserving structured failure context.

    The regular execution pipeline can raise before it has a result object (for
    example when the database baseline or OpenCode gateway is unavailable).
    Converting that exception into the same item shape as a normal failed run
    lets the retry flow update the execution artifact instead of falling through
    to an opaque workflow-level error.
    """
    try:
        return agent_execute_generated_script(run_id, step_key, script)
    except OpencodeTaskCancelled:
        raise
    except Exception as exc:
        context = agent_attempt_failure_context(exc)
        if context["error_type"] == "cancelled":
            raise OpencodeTaskCancelled(str(exc)) from exc
        execution = {
            "ok": False,
            "status": "failed",
            "error": str(exc),
        }
        if context["job_id"]:
            execution["job_id"] = context["job_id"]
        if context["test_run_id"]:
            execution["run_id"] = context["test_run_id"]
        if context["result_id"] is not None:
            execution["result_id"] = context["result_id"]
        return {
            **script,
            "execution": execution,
            "execution_run_id": context["test_run_id"],
            "execution_job_id": context["job_id"],
            "error": str(exc),
            "_failure_context": context,
            "_error_stack": traceback.format_exc(),
        }


def run_agent_item_retry_workflow(run_id, retry_flow_id, project, author):
    register_agent_item_retry_task(run_id, retry_flow_id)
    current_attempt_id = ""
    current_phase = "queued"
    flow_result = {}
    touched_steps = set()

    with (
        use_project_context(project),
        use_author_context(f"agent-retry:{author or 'platform'}"),
        use_agent_item_retry_context(run_id, retry_flow_id),
    ):
        try:
            flow_row = get_agent_item_retry_flow(run_id, retry_flow_id)
            if not flow_row or flow_row.get("status") not in AGENT_ITEM_RETRY_ACTIVE_STATUSES:
                return
            root_attempt = get_agent_attempt(run_id, flow_row.get("root_attempt_id"))
            if not root_attempt:
                raise RuntimeError("原始脚本生成失败记录不存在。")
            plan = get_agent_retry_plan_from_attempt(root_attempt)
            expected_filename = get_generated_script_filename_from_plan_filename(plan["plan_filename"])
            flow_result = load_json_column(flow_row.get("result_json"), {})
            flow_result = flow_result if isinstance(flow_result, dict) else {}
            flow_result.update(
                {
                    "root_attempt_id": root_attempt.get("attempt_id") or "",
                    "plan": plan,
                    "auto_repair": bool(flow_row.get("auto_repair")),
                }
            )

            current_phase = "generating"
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                expected_statuses={"queued"},
                status="running",
                current_phase=current_phase,
                progress_message="正在重新生成脚本。",
                result=flow_result,
                error="",
            )
            if not flow_row or flow_row.get("status") != "running":
                agent_raise_if_cancelled(run_id)
                raise RuntimeError("单项重试未能进入运行状态。")
            merge_agent_retry_step_result(run_id, "generate_scripts", flow_row, "retrying")
            touched_steps.add("generate_scripts")
            append_agent_item_retry_event(run_id, flow_row, "开始重新生成单个脚本。")
            agent_raise_if_cancelled(run_id)

            generation_attempt = start_agent_attempt(
                run_id,
                "generate_scripts",
                "script",
                flow_row.get("item_key"),
                module_name=plan["module_name"],
                plan_filename=plan["plan_filename"],
                filename=expected_filename,
                input_snapshot=plan,
                retry_flow_id=retry_flow_id,
                parent_attempt_id=root_attempt.get("attempt_id"),
            )
            current_attempt_id = generation_attempt["attempt_id"]
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                generation_attempt_id=current_attempt_id,
            )
            try:
                generated_script = agent_generate_script_for_plan(run_id, "generate_scripts", plan)
            except Exception as exc:
                context = agent_attempt_failure_context(exc)
                if context["error_type"] == "cancelled":
                    raise OpencodeTaskCancelled(str(exc)) from exc
                failure = build_agent_retry_failure_item(flow_row, current_attempt_id, plan, exc, context)
                finish_agent_attempt(
                    run_id,
                    current_attempt_id,
                    "failed",
                    job_id=context["job_id"],
                    test_run_id=context["test_run_id"],
                    result_id=context["result_id"],
                    asset_id=context["asset_id"],
                    source_asset_id=((plan.get("asset") or {}).get("asset_id") if isinstance(plan.get("asset"), dict) else None),
                    error_type=context["error_type"],
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                    output_summary=failure,
                    artifact_refs=[{"source": "partial", "path": path} for path in context["partial_artifacts"]],
                )
                current_attempt_id = ""
                flow_result["generation"] = failure
                flow_row = begin_agent_item_retry_finalization(
                    run_id,
                    retry_flow_id,
                    current_phase="generating",
                    progress_message="正在保存脚本重新生成失败结果。",
                    result=flow_result,
                    error=str(exc),
                )
                merge_agent_retry_step_result(
                    run_id,
                    "generate_scripts",
                    flow_row,
                    "failed",
                    failure_item=failure,
                )
                flow_row = complete_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    "failed",
                    current_phase="generating",
                    progress_message="脚本重新生成失败。",
                    result=flow_result,
                    error=str(exc),
                    event_message=f"脚本重新生成失败：{exc}",
                    event_type="error",
                    flow=flow_row,
                )
                return

            generated_script = {
                **generated_script,
                "attempt_id": current_attempt_id,
                "retry_flow_id": retry_flow_id,
                "verification_status": "pending",
            }
            generated_asset = generated_script.get("asset") if isinstance(generated_script.get("asset"), dict) else {}
            source_plan_asset = plan.get("asset") if isinstance(plan.get("asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                current_attempt_id,
                "succeeded",
                outcome_type="generated",
                verification_status="not_run",
                job_id=generated_script.get("job_id"),
                asset_id=generated_asset.get("asset_id"),
                revision_id=generated_asset.get("current_revision_id"),
                source_asset_id=source_plan_asset.get("asset_id") or generated_asset.get("from_plan_asset_id"),
                output_summary=generated_script,
                artifact_refs=(
                    [
                        {
                            "source": "test_assets",
                            "artifact_type": "script",
                            "asset_id": generated_asset.get("asset_id"),
                            "revision_id": generated_asset.get("current_revision_id"),
                        }
                    ]
                    if generated_asset.get("asset_id")
                    else []
                ),
            )
            generation_attempt_id = current_attempt_id
            current_attempt_id = ""
            flow_result["generation"] = generated_script

            current_phase = "executing"
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                current_phase=current_phase,
                progress_message="脚本已重新生成，正在执行验证。",
                result=flow_result,
            )
            merge_agent_retry_step_result(
                run_id,
                "generate_scripts",
                flow_row,
                "retrying",
            )
            merge_agent_retry_step_result(run_id, "execute_scripts", flow_row, "retrying")
            touched_steps.add("execute_scripts")
            append_agent_item_retry_event(run_id, flow_row, "脚本重新生成完成，开始执行。")
            agent_raise_if_cancelled(run_id)

            execution_attempt = start_agent_attempt(
                run_id,
                "execute_scripts",
                "script_execution",
                flow_row.get("item_key"),
                module_name=generated_script.get("module_name"),
                plan_filename=generated_script.get("plan_filename"),
                filename=generated_script.get("filename"),
                input_snapshot=generated_script,
                retry_flow_id=retry_flow_id,
                parent_attempt_id=generation_attempt_id,
            )
            current_attempt_id = execution_attempt["attempt_id"]
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                execution_attempt_id=current_attempt_id,
            )
            execution_item = execute_agent_retry_script(run_id, "execute_scripts", generated_script)
            execution_item = {
                **execution_item,
                "attempt_id": current_attempt_id,
                "retry_flow_id": retry_flow_id,
            }
            execution_failure_context = execution_item.pop("_failure_context", {})
            execution_error_stack = execution_item.pop("_error_stack", "")
            execution = execution_item.get("execution") if isinstance(execution_item.get("execution"), dict) else {}
            execution_asset = execution_item.get("asset") if isinstance(execution_item.get("asset"), dict) else {}
            execution_error = str(execution_item.get("error") or "")
            if not execution_error:
                execution_item["verification_status"] = "passed"
                finish_agent_attempt(
                    run_id,
                    current_attempt_id,
                    "succeeded",
                    outcome_type="passed",
                    verification_status="passed",
                    job_id=execution_item.get("execution_job_id") or execution.get("job_id"),
                    test_run_id=execution_item.get("execution_run_id") or execution.get("run_id"),
                    result_id=execution.get("result_id"),
                    asset_id=execution_asset.get("asset_id"),
                    revision_id=execution_asset.get("current_revision_id"),
                    output_summary=execution_item,
                    artifact_refs=agent_retry_execution_artifact_refs(execution_item),
                )
                current_attempt_id = ""
                flow_result["execution"] = execution_item
                flow_result["final_script"] = execution_item
                flow_row = begin_agent_item_retry_finalization(
                    run_id,
                    retry_flow_id,
                    current_phase="completed",
                    progress_message="验证已通过，正在保存各阶段结果。",
                    result=flow_result,
                    error="",
                )
                generation_final = {
                    **generated_script,
                    "verification_status": "passed",
                    "final_asset": execution_asset or generated_asset,
                }
                merge_agent_retry_step_result(
                    run_id,
                    "generate_scripts",
                    flow_row,
                    "succeeded",
                    script_item=generation_final,
                )
                merge_agent_retry_step_result(
                    run_id,
                    "execute_scripts",
                    flow_row,
                    "succeeded",
                    script_item=execution_item,
                )
                # A newly generated script that passes needs no repair. Remove any
                # old current repair result/failure for the same item without
                # manufacturing a repair artifact.
                merge_agent_retry_step_result(
                    run_id,
                    "repair_scripts",
                    flow_row,
                    "succeeded",
                    remove_matching_script=True,
                )
                supersede_agent_failed_script_review(run_id, flow_row, execution_item)
                mark_agent_suite_stale_after_item_retry(run_id, flow_row, execution_item)
                flow_row = complete_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    "succeeded",
                    current_phase="completed",
                    progress_message="脚本已重新生成并验证通过。",
                    result=flow_result,
                    error="",
                    event_message="脚本已重新生成并验证通过。",
                    flow=flow_row,
                )
                return

            execution_error_type = execution_failure_context.get("error_type") or classify_agent_retry_execution_error(
                execution_error,
                execution,
            )
            execution_context = {
                "job_id": execution_failure_context.get("job_id")
                or execution_item.get("execution_job_id")
                or execution.get("job_id")
                or "",
                "test_run_id": execution_failure_context.get("test_run_id")
                or execution_item.get("execution_run_id")
                or execution.get("run_id")
                or "",
                "result_id": execution_failure_context.get("result_id") or execution.get("result_id"),
                "asset_id": execution_failure_context.get("asset_id") or execution_asset.get("asset_id"),
                "error_type": execution_error_type,
                "partial_artifacts": list(execution_failure_context.get("partial_artifacts") or []),
            }
            execution_failure = build_agent_retry_failure_item(
                flow_row,
                current_attempt_id,
                execution_item,
                execution_error,
                execution_context,
                execution=execution,
            )
            finish_agent_attempt(
                run_id,
                current_attempt_id,
                "failed",
                verification_status="failed",
                job_id=execution_context["job_id"],
                test_run_id=execution_context["test_run_id"],
                result_id=execution_context["result_id"],
                asset_id=execution_asset.get("asset_id"),
                revision_id=execution_asset.get("current_revision_id"),
                error_type=execution_error_type,
                error_message=execution_error,
                error_stack=execution_error_stack,
                output_summary=execution_failure,
                artifact_refs=agent_retry_execution_artifact_refs(execution_item)
                + [{"source": "partial", "path": path} for path in execution_context["partial_artifacts"]],
            )
            execution_attempt_id = current_attempt_id
            current_attempt_id = ""
            flow_result["execution"] = execution_failure
            merge_agent_retry_step_result(
                run_id,
                "execute_scripts",
                flow_row,
                "failed",
                failure_item=execution_failure,
            )

            if not flow_row.get("auto_repair") or execution_error_type != "execution":
                terminal_status = "failed" if execution_error_type == "execution" else "blocked"
                progress_message = (
                    "脚本执行失败，自动修复已关闭。"
                    if execution_error_type == "execution"
                    else "脚本执行受环境或基础设施问题阻断，未修改脚本。"
                )
                flow_row = begin_agent_item_retry_finalization(
                    run_id,
                    retry_flow_id,
                    current_phase="executing",
                    progress_message="正在保存脚本执行失败结果。",
                    result=flow_result,
                    error=execution_error,
                )
                merge_agent_retry_step_result(
                    run_id,
                    "execute_scripts",
                    flow_row,
                    terminal_status,
                    failure_item=execution_failure,
                )
                clear_agent_retry_step_markers(run_id, flow_row, ["generate_scripts", "repair_scripts"])
                flow_row = complete_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    terminal_status,
                    current_phase="executing",
                    progress_message=progress_message,
                    result=flow_result,
                    error=execution_error,
                    event_message=progress_message,
                    event_type="error",
                    flow=flow_row,
                )
                return

            current_phase = "repairing"
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                current_phase=current_phase,
                progress_message="执行失败，正在自动修复脚本。",
                result=flow_result,
            )
            merge_agent_retry_step_result(run_id, "generate_scripts", flow_row, "retrying")
            merge_agent_retry_step_result(run_id, "execute_scripts", flow_row, "retrying")
            merge_agent_retry_step_result(run_id, "repair_scripts", flow_row, "retrying")
            touched_steps.add("repair_scripts")
            append_agent_item_retry_event(run_id, flow_row, "脚本执行失败，开始自动修复。")
            agent_raise_if_cancelled(run_id)

            repair_attempt = start_agent_attempt(
                run_id,
                "repair_scripts",
                "script_repair",
                flow_row.get("item_key"),
                module_name=generated_script.get("module_name"),
                plan_filename=generated_script.get("plan_filename"),
                filename=generated_script.get("filename"),
                input_snapshot=execution_failure,
                retry_flow_id=retry_flow_id,
                parent_attempt_id=execution_attempt_id,
            )
            current_attempt_id = repair_attempt["attempt_id"]
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                repair_attempt_id=current_attempt_id,
            )
            try:
                repaired_script = agent_repair_script(run_id, "repair_scripts", execution_failure)
            except Exception as exc:
                context = agent_attempt_failure_context(exc)
                if context["error_type"] == "cancelled":
                    raise OpencodeTaskCancelled(str(exc)) from exc
                repair_failure = build_agent_retry_failure_item(flow_row, current_attempt_id, execution_failure, exc, context)
                source_asset = execution_failure.get("asset") if isinstance(execution_failure.get("asset"), dict) else {}
                finish_agent_attempt(
                    run_id,
                    current_attempt_id,
                    "failed",
                    verification_status="failed",
                    job_id=context["job_id"],
                    test_run_id=context["test_run_id"],
                    result_id=context["result_id"],
                    asset_id=context["asset_id"] or source_asset.get("asset_id"),
                    revision_id=source_asset.get("current_revision_id"),
                    source_asset_id=source_asset.get("from_plan_asset_id"),
                    error_type=context["error_type"],
                    error_message=str(exc),
                    error_stack=traceback.format_exc(),
                    output_summary=repair_failure,
                    artifact_refs=[{"source": "partial", "path": path} for path in context["partial_artifacts"]],
                )
                current_attempt_id = ""
                flow_result["repair"] = repair_failure
                flow_row = begin_agent_item_retry_finalization(
                    run_id,
                    retry_flow_id,
                    current_phase="repairing",
                    progress_message="正在保存自动修复失败结果。",
                    result=flow_result,
                    error=str(exc),
                )
                merge_agent_retry_step_result(
                    run_id,
                    "repair_scripts",
                    flow_row,
                    "failed",
                    failure_item=repair_failure,
                )
                clear_agent_retry_step_markers(run_id, flow_row, ["generate_scripts", "execute_scripts"])
                flow_row = complete_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    "failed",
                    current_phase="repairing",
                    progress_message="自动修复脚本失败。",
                    result=flow_result,
                    error=str(exc),
                    event_message=f"自动修复脚本失败：{exc}",
                    event_type="error",
                    flow=flow_row,
                )
                return

            repaired_script = {
                **repaired_script,
                "attempt_id": current_attempt_id,
                "retry_flow_id": retry_flow_id,
                "verification_status": "pending",
            }
            repaired_asset = repaired_script.get("asset") if isinstance(repaired_script.get("asset"), dict) else {}
            finish_agent_attempt(
                run_id,
                current_attempt_id,
                "succeeded",
                outcome_type="repaired",
                verification_status="not_run",
                job_id=repaired_script.get("repair_job_id"),
                test_run_id=repaired_script.get("repair_test_run_id"),
                result_id=repaired_script.get("repair_result_id"),
                asset_id=repaired_asset.get("asset_id"),
                revision_id=repaired_asset.get("current_revision_id"),
                source_asset_id=repaired_asset.get("from_plan_asset_id"),
                output_summary=repaired_script,
                artifact_refs=(
                    [
                        {
                            "source": "test_assets",
                            "artifact_type": "script",
                            "asset_id": repaired_asset.get("asset_id"),
                            "revision_id": repaired_asset.get("current_revision_id"),
                        }
                    ]
                    if repaired_asset.get("asset_id")
                    else []
                ),
            )
            repair_attempt_id = current_attempt_id
            current_attempt_id = ""
            flow_result["repair"] = repaired_script

            current_phase = "verifying"
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                current_phase=current_phase,
                progress_message="脚本修复完成，正在复验。",
                result=flow_result,
            )
            merge_agent_retry_step_result(run_id, "generate_scripts", flow_row, "retrying")
            merge_agent_retry_step_result(run_id, "execute_scripts", flow_row, "retrying")
            merge_agent_retry_step_result(
                run_id,
                "repair_scripts",
                flow_row,
                "retrying",
            )
            append_agent_item_retry_event(run_id, flow_row, "脚本修复完成，开始复验。")
            agent_raise_if_cancelled(run_id)

            verification_attempt = start_agent_attempt(
                run_id,
                "execute_scripts",
                "script_verification",
                flow_row.get("item_key"),
                module_name=repaired_script.get("module_name"),
                plan_filename=repaired_script.get("plan_filename"),
                filename=repaired_script.get("filename"),
                input_snapshot=repaired_script,
                retry_flow_id=retry_flow_id,
                parent_attempt_id=repair_attempt_id,
            )
            current_attempt_id = verification_attempt["attempt_id"]
            flow_row = update_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                verification_attempt_id=current_attempt_id,
            )
            verification_item = execute_agent_retry_script(run_id, "execute_scripts", repaired_script)
            verification_item = {
                **verification_item,
                "attempt_id": current_attempt_id,
                "retry_flow_id": retry_flow_id,
                "verification_attempt": True,
            }
            verification_failure_context = verification_item.pop("_failure_context", {})
            verification_error_stack = verification_item.pop("_error_stack", "")
            verification = (
                verification_item.get("execution") if isinstance(verification_item.get("execution"), dict) else {}
            )
            verification_error = str(verification_item.get("error") or "")
            if verification_error:
                verification_asset = (
                    verification_item.get("asset") if isinstance(verification_item.get("asset"), dict) else repaired_asset
                )
                verification_error_type = verification_failure_context.get(
                    "error_type"
                ) or classify_agent_retry_execution_error(verification_error, verification)
                verification_context = {
                    "job_id": verification_failure_context.get("job_id")
                    or verification_item.get("execution_job_id")
                    or verification.get("job_id")
                    or "",
                    "test_run_id": verification_failure_context.get("test_run_id")
                    or verification_item.get("execution_run_id")
                    or verification.get("run_id")
                    or "",
                    "result_id": verification_failure_context.get("result_id") or verification.get("result_id"),
                    "asset_id": verification_failure_context.get("asset_id") or verification_asset.get("asset_id"),
                    "error_type": verification_error_type,
                    "partial_artifacts": list(verification_failure_context.get("partial_artifacts") or []),
                }
                verification_failure = build_agent_retry_failure_item(
                    flow_row,
                    current_attempt_id,
                    verification_item,
                    verification_error,
                    verification_context,
                    execution=verification,
                    verification_attempt=True,
                )
                finish_agent_attempt(
                    run_id,
                    current_attempt_id,
                    "failed",
                    verification_status="failed",
                    job_id=verification_context["job_id"],
                    test_run_id=verification_context["test_run_id"],
                    result_id=verification_context["result_id"],
                    asset_id=verification_asset.get("asset_id"),
                    revision_id=verification_asset.get("current_revision_id"),
                    error_type=verification_error_type,
                    error_message=verification_error,
                    error_stack=verification_error_stack,
                    output_summary=verification_failure,
                    artifact_refs=agent_retry_execution_artifact_refs(verification_item)
                    + [{"source": "partial", "path": path} for path in verification_context["partial_artifacts"]],
                )
                finish_agent_attempt(
                    run_id,
                    repair_attempt_id,
                    "succeeded",
                    verification_status="failed",
                    output_summary={**repaired_script, "verification": verification_failure},
                )
                current_attempt_id = ""
                repaired_script["verification_status"] = "failed"
                repaired_script["verification"] = verification_failure
                flow_result["verification"] = verification_failure
                flow_result["final_script"] = repaired_script
                flow_row = begin_agent_item_retry_finalization(
                    run_id,
                    retry_flow_id,
                    current_phase="verifying",
                    progress_message="正在保存复验失败结果。",
                    result=flow_result,
                    error=verification_error,
                )
                merge_agent_retry_step_result(
                    run_id,
                    "execute_scripts",
                    flow_row,
                    "failed",
                    failure_item=verification_failure,
                )
                merge_agent_retry_step_result(
                    run_id,
                    "repair_scripts",
                    flow_row,
                    "failed",
                    failure_item=verification_failure,
                    remove_matching_script=True,
                )
                clear_agent_retry_step_markers(run_id, flow_row, ["generate_scripts"])
                flow_row = complete_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    "failed",
                    current_phase="verifying",
                    progress_message="脚本修复后复验仍然失败。",
                    result=flow_result,
                    error=verification_error,
                    event_message="脚本修复后复验仍然失败。",
                    event_type="error",
                    flow=flow_row,
                )
                return

            verification_item["verification_status"] = "passed"
            finish_agent_attempt(
                run_id,
                current_attempt_id,
                "succeeded",
                outcome_type="passed",
                verification_status="passed",
                job_id=verification_item.get("execution_job_id") or verification.get("job_id"),
                test_run_id=verification_item.get("execution_run_id") or verification.get("run_id"),
                result_id=verification.get("result_id"),
                asset_id=repaired_asset.get("asset_id"),
                revision_id=repaired_asset.get("current_revision_id"),
                output_summary=verification_item,
                artifact_refs=agent_retry_execution_artifact_refs(verification_item),
            )
            finish_agent_attempt(
                run_id,
                repair_attempt_id,
                "succeeded",
                verification_status="passed",
                output_summary={**repaired_script, "verification": verification_item},
            )
            current_attempt_id = ""
            repaired_script["verification_status"] = "passed"
            repaired_script["verification"] = verification_item
            flow_result["verification"] = verification_item
            flow_result["final_script"] = repaired_script
            flow_row = begin_agent_item_retry_finalization(
                run_id,
                retry_flow_id,
                current_phase="completed",
                progress_message="复验已通过，正在保存各阶段结果。",
                result=flow_result,
                error="",
            )
            generation_final = {
                **generated_script,
                "verification_status": "passed",
                "final_asset": repaired_asset,
                "repaired": True,
            }
            merge_agent_retry_step_result(
                run_id,
                "generate_scripts",
                flow_row,
                "succeeded",
                script_item=generation_final,
            )
            merge_agent_retry_step_result(
                run_id,
                "execute_scripts",
                flow_row,
                "succeeded",
                script_item=verification_item,
            )
            merge_agent_retry_step_result(
                run_id,
                "repair_scripts",
                flow_row,
                "succeeded",
                script_item=repaired_script,
            )
            supersede_agent_failed_script_review(run_id, flow_row, repaired_script)
            mark_agent_suite_stale_after_item_retry(run_id, flow_row, repaired_script)
            flow_row = complete_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                "succeeded",
                current_phase="completed",
                progress_message="脚本已修复并复验通过。",
                result=flow_result,
                error="",
                event_message="脚本已修复并复验通过。",
                flow=flow_row,
            )
        except OpencodeTaskCancelled as exc:
            if current_attempt_id:
                attempt = get_agent_attempt(run_id, current_attempt_id) or {}
                if attempt.get("status") == "running":
                    finish_agent_attempt(
                        run_id,
                        current_attempt_id,
                        "cancelled",
                        error_type="cancelled",
                        error_message=str(exc),
                        error_stack=traceback.format_exc(),
                    )
            flow_result["cancelled_at_phase"] = current_phase
            flow_row = get_agent_item_retry_flow(run_id, retry_flow_id) or {}
            clear_agent_retry_step_markers(run_id, flow_row, touched_steps)
            flow_row = terminalize_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                "cancelled",
                expected_statuses=AGENT_ITEM_RETRY_ACTIVE_STATUSES,
                current_phase=current_phase if current_phase in AGENT_ITEM_RETRY_PHASES else "queued",
                progress_message="本次重试已取消。",
                result=flow_result,
                error=str(exc),
                cancel_requested=True,
                event_message="本次重试已取消。",
                flow=flow_row,
            )
        except Exception as exc:
            if current_attempt_id:
                attempt = get_agent_attempt(run_id, current_attempt_id) or {}
                if attempt.get("status") == "running":
                    finish_agent_attempt(
                        run_id,
                        current_attempt_id,
                        "failed",
                        error_type=classify_agent_attempt_error(exc),
                        error_message=str(exc),
                        error_stack=traceback.format_exc(),
                    )
            flow_result["unexpected_error"] = str(exc)
            flow_row = get_agent_item_retry_flow(run_id, retry_flow_id) or {}
            clear_agent_retry_step_markers(run_id, flow_row, touched_steps)
            if flow_row.get("cancel_requested") or flow_row.get("status") == "cancelling":
                flow_row = terminalize_agent_item_retry_flow(
                    run_id,
                    retry_flow_id,
                    "cancelled",
                    expected_statuses=AGENT_ITEM_RETRY_ACTIVE_STATUSES,
                    current_phase=current_phase if current_phase in AGENT_ITEM_RETRY_PHASES else "queued",
                    progress_message="本次重试已取消。",
                    result=flow_result,
                    error=str(exc),
                    cancel_requested=True,
                    event_message="本次重试已取消。",
                    flow=flow_row,
                )
                return
            flow_row = terminalize_agent_item_retry_flow(
                run_id,
                retry_flow_id,
                "failed",
                expected_statuses={"queued", "running", "finalizing"},
                current_phase=current_phase if current_phase in AGENT_ITEM_RETRY_PHASES else "queued",
                progress_message="单项重试发生异常。",
                result=flow_result,
                error=str(exc),
                event_message=f"单项重试发生异常：{exc}",
                event_type="error",
                flow=flow_row,
            )
        finally:
            agent_set_current_job(run_id, "")
            cleanup_agent_item_retry_task(retry_flow_id)


def start_agent_item_retry_thread(run_id, retry_flow_id, project, author):
    thread = threading.Thread(
        target=run_agent_item_retry_workflow,
        args=(run_id, retry_flow_id, project, author),
        daemon=True,
    )
    thread.start()
    return thread


def _page_inventory_model_dependencies():
    return page_inventory_model.PageInventoryModelDependencies(
        load_json_column=lambda value, fallback: load_json_column(
            value,
            fallback,
        ),
        normalize_confidence=lambda value: normalize_confidence(value),
        normalize_string_list=lambda value: normalize_string_list(value),
        normalize_json_object_or_array=lambda value, fallback: (
            normalize_json_object_or_array(value, fallback)
        ),
        allowed_sources=frozenset(PAGE_INVENTORY_SOURCES),
    )


def _page_inventory_repository_dependencies():
    return page_inventory_repository.PageInventoryRepositoryDependencies(
        require_platform_database=lambda: require_platform_database(),
        get_page_inventory_table=lambda config: get_page_inventory_table(config),
        get_current_project_id=lambda: get_current_project_id(),
        platform_mysql_connection=lambda config: platform_mysql_connection(
            config
        ),
        validate_uid=lambda value, field_name: validate_uid(
            value,
            field_name,
        ),
        compact_json_dumps=lambda value: compact_json_dumps(value),
        current_time_ms=lambda: current_time_ms(),
        new_inventory_uid=lambda: uuid.uuid4().hex,
        get_page_inventory_by_uid=lambda inventory_uid: (
            get_page_inventory_by_uid(inventory_uid)
        ),
    )


def _page_inventory_repository():
    return page_inventory_repository.PageInventoryRepository(
        _page_inventory_repository_dependencies()
    )


def _page_inventory_service_dependencies():
    return page_inventory_service.PageInventoryServiceDependencies(
        list_rows=lambda limit=None: _page_inventory_repository().list_rows(
            limit=limit
        ),
        get_by_uid=lambda inventory_uid: (
            _page_inventory_repository().get_by_uid(inventory_uid)
        ),
        upsert_normalized=lambda item, inventory_uid=None: (
            _page_inventory_repository().upsert(
                item,
                inventory_uid=inventory_uid,
            )
        ),
        delete_by_uid=lambda inventory_uid: (
            _page_inventory_repository().delete(inventory_uid)
        ),
        serialize_page_inventory=lambda row: serialize_page_inventory(row),
        normalize_page_inventory_payload=lambda payload: (
            normalize_page_inventory_payload(payload)
        ),
        parse_page_inventory_from_markdown=lambda text: (
            parse_page_inventory_from_markdown(text)
        ),
        app_dir=APP_DIR,
    )


def _page_inventory_service():
    return page_inventory_service.PageInventoryService(
        _page_inventory_service_dependencies()
    )


def _page_inventory_web_services():
    return PageInventoryWebServices(
        list_rows=lambda: list_page_inventory_rows(),
        serialize_page_inventory=lambda row: serialize_page_inventory(row),
        upsert_page_inventory=lambda payload, inventory_uid=None: (
            upsert_page_inventory(
                payload,
                inventory_uid=inventory_uid,
            )
        ),
        get_page_inventory_by_uid=lambda inventory_uid: (
            get_page_inventory_by_uid(inventory_uid)
        ),
        delete_page_inventory=lambda inventory_uid: (
            delete_page_inventory(inventory_uid)
        ),
        import_page_inventory_from_doc=lambda payload: (
            import_page_inventory_from_doc(payload)
        ),
    )


def _plan_workbook_service():
    return plan_workbook.PlanWorkbookService(
        plan_workbook.PlanWorkbookDependencies(
            get_project_key=lambda: get_current_project()["project_key"],
            get_plan_file=lambda module_name, filename: get_plan_file(module_name, filename),
            validate_module_name=lambda value: validate_module_name(value),
            validate_plan_filename=lambda value: validate_plan_filename(value),
            sync_plan_asset=lambda *args, **kwargs: sync_plan_asset(*args, **kwargs),
            find_plan_asset=lambda path: get_test_asset_by_path("plan", path),
            mark_plan_asset_deleted=lambda asset: mark_test_asset_deleted(asset),
            commit_removed_plan=lambda path, message: ensure_git_commit_for_removed_path(path, message),
            current_timestamp=lambda: time.strftime("%Y%m%d-%H%M%S"),
        )
    )


def _plan_workbook_web_services():
    return PlanWorkbookWebServices(
        export_plans=lambda selections: _plan_workbook_service().export(selections),
        import_plans=lambda data, policy: _plan_workbook_service().import_bytes(data, policy),
        upload_max_bytes=plan_workbook.PLAN_WORKBOOK_MAX_UPLOAD_BYTES,
    )


def serialize_page_inventory(row):
    return page_inventory_model.serialize_page_inventory(
        row,
        _page_inventory_model_dependencies(),
    )


def normalize_accounts(value):
    return page_inventory_model.normalize_accounts(
        value,
        _page_inventory_model_dependencies(),
    )


def normalize_page_inventory_payload(payload):
    return page_inventory_model.normalize_page_inventory_payload(
        payload,
        _page_inventory_model_dependencies(),
    )


def list_page_inventory_rows(limit=None):
    return _page_inventory_service().list_rows(limit=limit)


def get_page_inventory_by_uid(inventory_uid):
    return _page_inventory_service().get_by_uid(inventory_uid)


def upsert_page_inventory(payload, inventory_uid=None):
    return _page_inventory_service().upsert(
        payload,
        inventory_uid=inventory_uid,
    )


def delete_page_inventory(inventory_uid):
    return _page_inventory_service().delete(inventory_uid)


split_markdown_table_row = page_inventory_model.split_markdown_table_row


def parse_page_inventory_from_markdown(markdown_text):
    return page_inventory_model.parse_page_inventory_from_markdown(
        markdown_text,
        _page_inventory_model_dependencies(),
    )


def import_page_inventory_from_doc(payload):
    return _page_inventory_service().import_from_doc(payload)
def resolve_optional_path(value, base_dir=None):
    if not value:
        return None

    path = Path(value).expanduser()
    if path.is_absolute() or base_dir is None:
        return path
    return (base_dir / path).resolve(strict=False)


def resolve_database_baseline_marker_path(config):
    project_root = get_project_root()
    marker_path = resolve_optional_path(config.get("marker_path"), project_root)
    if marker_path:
        return marker_path
    return project_root / ".database-baseline" / "baseline.marker.json"


def resolve_database_baseline_lock_path(config):
    project_root = get_project_root()
    lock_path = resolve_optional_path(config.get("lock_path"), project_root)
    if lock_path:
        return lock_path

    mode = config.get("mode")
    if mode == "command":
        return resolve_database_baseline_marker_path(config).parent / DATABASE_BASELINE_LOCK_DIR_NAME

    baseline_path = resolve_optional_path(config.get("baseline_path"))
    database_path = resolve_optional_path(config.get("database_path"))
    anchor_path = baseline_path or database_path
    if anchor_path:
        return anchor_path.parent / DATABASE_BASELINE_LOCK_DIR_NAME

    return project_root / ".database-baseline" / DATABASE_BASELINE_LOCK_DIR_NAME


@contextmanager
def database_baseline_lock(config):
    lock_path = resolve_database_baseline_lock_path(config)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    timeout_seconds = config.get("timeout_seconds") or DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS
    stale_seconds = max(timeout_seconds * 2, 60)
    deadline = time.monotonic() + timeout_seconds
    acquired = False

    while True:
        try:
            lock_path.mkdir()
            acquired = True
            owner_path = lock_path / "owner.json"
            owner_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            break
        except FileExistsError:
            try:
                age_seconds = time.time() - lock_path.stat().st_mtime
                if age_seconds > stale_seconds:
                    shutil.rmtree(lock_path, ignore_errors=True)
                    continue
            except FileNotFoundError:
                continue

            if time.monotonic() >= deadline:
                raise RuntimeError(f"等待数据库基线锁超时：{lock_path}")
            time.sleep(1)

    try:
        yield
    finally:
        if acquired:
            shutil.rmtree(lock_path, ignore_errors=True)


def write_text_if_changed(path, content):
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except UnicodeDecodeError:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(content, encoding="utf-8", newline="")
    temp_path.replace(path)
    return True


def validate_generated_script_content(content, target_filename):
    errors = []
    if not content.strip():
        errors.append("候选脚本为空。")

    if "from '@playwright/test'" not in content and 'from "@playwright/test"' not in content:
        errors.append("候选脚本未导入 @playwright/test。")

    test_count = len(re.findall(r"(?<![.\w$])test\s*\(", content))
    if test_count != 1:
        errors.append(f"候选脚本必须且只能包含一个 test(...)，当前检测到 {test_count} 个。")

    forbidden_patterns = {
        "page.waitForTimeout": "禁止使用 page.waitForTimeout()。",
        "page.waitForNavigation": "禁止使用 page.waitForNavigation()。",
        "page.waitForLoadState": "禁止使用 page.waitForLoadState()。",
        "page.evaluate": "禁止使用 page.evaluate()。",
    }
    for pattern, message in forbidden_patterns.items():
        if pattern in content:
            errors.append(message)

    if "expect(" not in content:
        errors.append("候选脚本缺少 expect(...) 断言。")

    if errors:
        raise RuntimeError(f"候选脚本校验失败（{target_filename}）：\n" + "\n".join(f"- {item}" for item in errors))


def write_file_atomically(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_bytes(content)
    temp_path.replace(path)


def save_asset_content_with_rollback(path, content, save_asset, rollback_asset, rollback_message):
    original_content = read_file_bytes(path)
    if original_content is None:
        raise FileNotFoundError(f"Asset file not found: {path}")

    write_file_atomically(path, content.encode("utf-8"))
    try:
        return save_asset()
    except Exception as sync_error:
        rollback_errors = []
        try:
            write_file_atomically(path, original_content)
        except Exception as rollback_error:
            rollback_errors.append(f"文件回滚失败：{rollback_error}")

        if not rollback_errors:
            try:
                project_root = get_project_root()
                if (project_root / ".git").exists():
                    ensure_git_commit_for_path(path, rollback_message)
            except Exception as git_rollback_error:
                rollback_errors.append(f"Git 回滚失败：{git_rollback_error}")

            try:
                rollback_asset()
            except Exception as asset_rollback_error:
                rollback_errors.append(f"版本状态回滚失败：{asset_rollback_error}")

        if rollback_errors:
            raise RuntimeError(f"{sync_error}（{'；'.join(rollback_errors)}）") from sync_error
        raise


def backup_script_file(module_name, target_file, original_content):
    if original_content is None:
        return None

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup_dir = get_script_generation_backup_dir(module_name)
    backup_dir.mkdir(parents=True, exist_ok=True)
    base_name = target_file.name[: -len(".spec.ts")] if target_file.name.endswith(".spec.ts") else target_file.stem
    backup_file = backup_dir / f"{base_name}.{timestamp}.{uuid.uuid4().hex[:8]}.spec.ts.bak"
    backup_file.write_bytes(original_content)
    return backup_file


def restore_snapshot_files(snapshot):
    for info in snapshot.values():
        path = info["path"]
        if info["exists"]:
            write_file_atomically(path, info["content"])
        elif path.exists():
            path.unlink()


def changed_snapshot_paths(snapshot):
    changed = []
    for key, info in snapshot.items():
        path = info["path"]
        current_content = read_file_bytes(path)
        current_exists = current_content is not None
        current_hash = sha256_bytes(current_content) if current_exists else ""
        if current_exists != info["exists"] or current_hash != info["hash"]:
            changed.append(key)
    return changed


def new_managed_file_paths(snapshot):
    known_paths = set(snapshot)
    new_paths = {}
    for path in iter_generation_managed_files():
        key = str(path.resolve(strict=False))
        if key not in known_paths:
            new_paths[key] = path
    return new_paths


def cleanup_new_managed_files(snapshot):
    for path in new_managed_file_paths(snapshot).values():
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def get_new_generated_script_files(script_dir, existing_script_names):
    if not script_dir.exists():
        return []
    return sorted(
        [item for item in script_dir.glob("*.spec.ts") if item.is_file() and item.name not in existing_script_names],
        key=lambda item: item.name.lower(),
    )


def cleanup_new_generated_script_files(script_dir, existing_script_names):
    for item in get_new_generated_script_files(script_dir, existing_script_names):
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def choose_generated_script_source(candidate_file, target_file, script_dir, existing_script_names, original_target_hash):
    if candidate_file.exists() and candidate_file.is_file():
        return candidate_file, "candidate"

    current_target_hash = file_hash(target_file)
    if target_file.exists() and current_target_hash and current_target_hash != original_target_hash:
        return target_file, "target"

    new_script_files = get_new_generated_script_files(script_dir, existing_script_names)
    if len(new_script_files) == 1:
        return new_script_files[0], "new-script"
    if len(new_script_files) > 1:
        names = ", ".join(item.name for item in new_script_files)
        raise RuntimeError(f"生成期间出现多个新脚本，无法判断候选文件：{names}")

    raise RuntimeError(f"OpenCode 已结束，但未生成候选脚本：{candidate_file}")


def finalize_script_generation(module_name, plan_filename, plan_file, target_file, candidate_file, snapshot, existing_script_names):
    script_dir = get_script_module_dir(module_name)
    validate_chinese_script_filename(target_file.name)
    target_key = str(target_file.resolve(strict=False))
    original_target = snapshot.get(target_key, {})
    original_target_content = original_target.get("content")
    original_target_hash = original_target.get("hash", "")
    source_file = None
    source_kind = ""

    try:
        source_file, source_kind = choose_generated_script_source(
            candidate_file,
            target_file,
            script_dir,
            existing_script_names,
            original_target_hash,
        )
        source_content = source_file.read_text(encoding="utf-8")
        validate_generated_script_content(source_content, target_file.name)
        source_bytes = source_content.encode("utf-8")

        allowed_changed = {target_key}
        source_key = str(source_file.resolve(strict=False))
        if source_kind in {"new-script", "target"}:
            allowed_changed.add(source_key)

        unexpected_changes = [
            key
            for key in changed_snapshot_paths(snapshot)
            if key not in allowed_changed
        ]
        unexpected_new_files = {
            key: path
            for key, path in new_managed_file_paths(snapshot).items()
            if key not in allowed_changed
        }
        if unexpected_changes or unexpected_new_files:
            changed_names = [str(snapshot[key]["path"]) for key in unexpected_changes]
            changed_names.extend(str(path) for path in unexpected_new_files.values())
            raise RuntimeError(f"生成期间修改了非目标文件：{', '.join(changed_names)}")

        backup_file = None
        changed = source_bytes != original_target_content
        if changed:
            backup_file = backup_script_file(module_name, target_file, original_target_content)
            write_file_atomically(target_file, source_bytes)

        if source_kind == "new-script" and source_file.resolve(strict=False) != target_file.resolve(strict=False):
            source_file.unlink(missing_ok=True)

        return {
            "plan_filename": plan_filename,
            "plan_name": Path(plan_filename).stem,
            "script_filename": target_file.name,
            "target_path": str(target_file),
            "candidate_path": str(candidate_file),
            "source_kind": source_kind,
            "backup_path": str(backup_file) if backup_file else "",
            "changed": changed,
        }
    except Exception:
        restore_snapshot_files(snapshot)
        cleanup_new_generated_script_files(script_dir, existing_script_names)
        cleanup_new_managed_files(snapshot)
        raise


def get_import_path_between(source_file, importing_dir):
    import_path = Path(os.path.relpath(source_file, importing_dir)).as_posix()
    if not import_path.startswith("."):
        import_path = f"./{import_path}"

    for suffix in (".ts", ".mts", ".cts", ".js", ".mjs", ".cjs"):
        if import_path.endswith(suffix):
            return import_path[: -len(suffix)]
    return import_path


def build_database_baseline_runtime_config(config):
    project_root = get_project_root()
    working_directory = resolve_optional_path(config.get("working_directory"))
    database_path = resolve_optional_path(config.get("database_path"))
    baseline_path = resolve_optional_path(config.get("baseline_path"))
    marker_path = resolve_database_baseline_marker_path(config)
    lock_path = resolve_database_baseline_lock_path(config)

    return {
        "enabled": bool(config.get("enabled")),
        "mode": config.get("mode"),
        "projectRoot": str(project_root),
        "databasePath": str(database_path) if database_path else "",
        "baselinePath": str(baseline_path) if baseline_path else "",
        "markerPath": str(marker_path) if marker_path else "",
        "lockPath": str(lock_path),
        "workingDirectory": str(working_directory) if working_directory else "",
        "backupCommand": config.get("backup_command", ""),
        "restoreCommand": config.get("restore_command", ""),
        "timeoutSeconds": config.get("timeout_seconds") or DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS,
    }


def database_baseline_global_setup_source():
    return r"""const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");

const configPath = path.join(__dirname, "database-baseline.config.json");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pathExists(targetPath) {
  try {
    await fsp.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function acquireLock(config) {
  const lockPath = config.lockPath || path.join(__dirname, "baseline.restore.lock");
  const timeoutMs = Math.max(Number(config.timeoutSeconds || 1800), 1) * 1000;
  const staleMs = Math.max(timeoutMs * 2, 60000);
  const deadline = Date.now() + timeoutMs;

  while (true) {
    try {
      await fsp.mkdir(path.dirname(lockPath), { recursive: true });
      await fsp.mkdir(lockPath);
      await fsp.writeFile(
        path.join(lockPath, "owner.json"),
        JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }, null, 2),
        "utf8",
      );
      return async () => {
        await fsp.rm(lockPath, { recursive: true, force: true });
      };
    } catch (error) {
      if (!error || error.code !== "EEXIST") {
        throw error;
      }

      try {
        const stat = await fsp.stat(lockPath);
        if (Date.now() - stat.mtimeMs > staleMs) {
          await fsp.rm(lockPath, { recursive: true, force: true });
          continue;
        }
      } catch (statError) {
        if (statError && statError.code === "ENOENT") {
          continue;
        }
        throw statError;
      }

      if (Date.now() >= deadline) {
        throw new Error(`Timed out waiting for database baseline lock: ${lockPath}`);
      }
      await sleep(1000);
    }
  }
}

async function runCommand(command, workingDirectory, timeoutSeconds, label) {
  if (!command || (Array.isArray(command) && command.length === 0)) {
    throw new Error(`Database baseline ${label} command is not configured.`);
  }

  if (workingDirectory && !(await pathExists(workingDirectory))) {
    throw new Error(`Database baseline working directory does not exist: ${workingDirectory}`);
  }

  const timeoutMs = Math.max(Number(timeoutSeconds || 1800), 1) * 1000;

  const child = Array.isArray(command)
    ? spawn(command[0], command.slice(1), { cwd: workingDirectory || undefined, windowsHide: true })
    : spawn(command, [], { cwd: workingDirectory || undefined, shell: true, windowsHide: true });

  const timer = setTimeout(() => {
    child.kill("SIGTERM");
  }, timeoutMs);

  child.stdout?.resume();
  child.stderr?.resume();

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("close", resolve);
  }).finally(() => clearTimeout(timer));

  if (exitCode !== 0) {
    throw new Error(`Database baseline ${label} failed with exit code ${exitCode}.`);
  }

  console.log(`Database baseline ${label} completed.`);
}

async function prepareFileBaseline(config) {
  if (!config.databasePath) {
    throw new Error("databasePath is not configured.");
  }
  if (!config.baselinePath) {
    throw new Error("baselinePath is not configured.");
  }
  if (!(await pathExists(config.databasePath))) {
    throw new Error(`Runtime database file does not exist: ${config.databasePath}`);
  }

  await fsp.mkdir(path.dirname(config.baselinePath), { recursive: true });
  if (await pathExists(config.baselinePath)) {
    await fsp.copyFile(config.baselinePath, config.databasePath);
    console.log("Database baseline restore completed.");
    return;
  }

  await fsp.copyFile(config.databasePath, config.baselinePath);
  console.log("Database baseline created.");
}

async function prepareCommandBaseline(config) {
  if (!config.markerPath) {
    throw new Error("markerPath is not configured.");
  }

  await fsp.mkdir(path.dirname(config.markerPath), { recursive: true });
  if (await pathExists(config.markerPath)) {
    await runCommand(config.restoreCommand, config.workingDirectory, config.timeoutSeconds, "restore");
    return;
  }

  await runCommand(config.backupCommand, config.workingDirectory, config.timeoutSeconds, "backup");
  await fsp.writeFile(
    config.markerPath,
    JSON.stringify({ createdAt: new Date().toISOString(), mode: "command" }, null, 2),
    "utf8",
  );
  console.log("Database baseline marker created.");
}

async function main() {
  if (!fs.existsSync(configPath)) {
    return;
  }

  const config = JSON.parse(await fsp.readFile(configPath, "utf8"));
  if (!config.enabled) {
    return;
  }

  const releaseLock = await acquireLock(config);
  try {
    if (config.mode === "file") {
      await prepareFileBaseline(config);
      return;
    }
    if (config.mode === "command") {
      await prepareCommandBaseline(config);
      return;
    }
    throw new Error(`Unsupported database baseline mode: ${config.mode}`);
  } finally {
    await releaseLock();
  }
}

module.exports = async function globalSetup() {
  await main();
};
"""


def database_baseline_playwright_config_source(project_root, helper_dir):
    base_config = find_playwright_config(project_root)
    project_root_json = json.dumps(str(project_root), ensure_ascii=False)
    global_setup_json = json.dumps(
        str(helper_dir / DATABASE_BASELINE_GLOBAL_SETUP_FILENAME),
        ensure_ascii=False,
    )

    if base_config:
        import_path = get_import_path_between(base_config, helper_dir)
        return f"""import path from 'node:path';
import {{ defineConfig }} from '@playwright/test';
import baseConfigModule from {json.dumps(import_path)};

const baseConfig = ((baseConfigModule as any).default ?? baseConfigModule) as any;
const projectRoot = {project_root_json};
const resolveProjectPath = (value: any, fallback: string) => {{
  if (!value) return fallback;
  const text = String(value);
  return path.isAbsolute(text) ? text : path.join(projectRoot, text);
}};

export default defineConfig({{
  ...baseConfig,
  testDir: resolveProjectPath(baseConfig.testDir, path.join(projectRoot, 'tests')),
  outputDir: resolveProjectPath(baseConfig.outputDir, path.join(projectRoot, 'test-results')),
  fullyParallel: false,
  workers: 1,
  globalSetup: {global_setup_json},
}});
"""

    return f"""import path from 'node:path';
import {{ defineConfig }} from '@playwright/test';

const projectRoot = {project_root_json};

export default defineConfig({{
  testDir: path.join(projectRoot, 'tests'),
  outputDir: path.join(projectRoot, 'test-results'),
  fullyParallel: false,
  workers: 1,
  globalSetup: {global_setup_json},
}});
"""


def ensure_database_baseline_playwright_files(config):
    if not config.get("enabled"):
        return []

    project_root = get_project_root()
    helper_dir = project_root / DATABASE_BASELINE_HELPER_DIR_NAME
    helper_dir.mkdir(parents=True, exist_ok=True)

    runtime_config_path = helper_dir / DATABASE_BASELINE_RUNTIME_CONFIG_FILENAME
    global_setup_path = helper_dir / DATABASE_BASELINE_GLOBAL_SETUP_FILENAME
    playwright_config_path = helper_dir / DATABASE_BASELINE_PLAYWRIGHT_CONFIG_FILENAME

    changed = []
    runtime_config = json.dumps(build_database_baseline_runtime_config(config), ensure_ascii=False, indent=2)
    if write_text_if_changed(runtime_config_path, f"{runtime_config}\n"):
        changed.append(str(runtime_config_path))
    if write_text_if_changed(global_setup_path, database_baseline_global_setup_source()):
        changed.append(str(global_setup_path))
    if write_text_if_changed(playwright_config_path, database_baseline_playwright_config_source(project_root, helper_dir)):
        changed.append(str(playwright_config_path))

    if not changed:
        return []
    return [f"已同步 Playwright 数据库基线文件：{', '.join(changed)}"]


def run_database_baseline_command(command, working_directory, timeout_seconds, action_label):
    if not command:
        raise RuntimeError(f"已启用数据库基线，但未配置 {action_label} 命令。")

    cwd = resolve_optional_path(working_directory) if working_directory else None
    if cwd and not cwd.exists():
        raise RuntimeError(f"数据库基线工作目录不存在：{cwd}")

    shell = isinstance(command, str)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        shell=shell,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"数据库基线{action_label}失败，退出码：{completed.returncode}")

    return f"数据库基线{action_label}完成。"


def prepare_file_database_baseline(config):
    database_path = resolve_optional_path(config.get("database_path"))
    baseline_path = resolve_optional_path(config.get("baseline_path"))

    if not database_path:
        raise RuntimeError("已启用文件数据库基线，但未配置 database_path。")
    if not baseline_path:
        raise RuntimeError("已启用文件数据库基线，但未配置 baseline_path。")
    if not database_path.is_file():
        raise RuntimeError(f"运行数据库文件不存在：{database_path}")

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if baseline_path.exists():
        shutil.copy2(baseline_path, database_path)
        return ["数据库基线恢复完成。"]

    shutil.copy2(database_path, baseline_path)
    return ["数据库基线创建完成。"]


def prepare_command_database_baseline(config):
    marker_path = resolve_database_baseline_marker_path(config)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = config.get("timeout_seconds") or DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS
    working_directory = config.get("working_directory")

    if marker_path.exists():
        message = run_database_baseline_command(
            config.get("restore_command"),
            working_directory,
            timeout_seconds,
            "恢复",
        )
        return [message]

    message = run_database_baseline_command(
        config.get("backup_command"),
        working_directory,
        timeout_seconds,
        "备份",
    )
    marker_path.write_text(
        json.dumps(
            {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "mode": "command",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return [message]


def prepare_database_baseline_for_test():
    config = get_database_baseline_config()
    if not config.get("enabled"):
        return []

    messages = ensure_database_baseline_playwright_files(config)
    with database_baseline_lock(config):
        mode = config.get("mode")
        if mode == "file":
            return [*messages, *prepare_file_database_baseline(config)]
        if mode == "command":
            return [*messages, *prepare_command_database_baseline(config)]

    raise RuntimeError(f"不支持的数据库基线模式：{mode}")


def get_npx_executable():
    return shutil.which("npx") or "npx"


def strip_ansi(value):
    return ANSI_ESCAPE_PATTERN.sub("", value)


def _execution_playwright_dependencies():
    return execution_playwright.PlaywrightDependencies(
        path_is_file=lambda path: path.is_file(),
        get_npx_executable=lambda: get_npx_executable(),
    )


def find_playwright_config(project_root):
    return execution_playwright.find_playwright_config(
        project_root,
        _execution_playwright_dependencies(),
    )


def get_config_import_path(config_file, base_dir):
    return execution_playwright.get_config_import_path(
        config_file,
        base_dir,
    )


def make_execution_run_id():
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def normalize_execution_mode(value):
    return execution_results.normalize_execution_mode(value)


def get_execution_mode_label(execution_mode):
    return execution_results.get_execution_mode_label(execution_mode)


def create_video_override_config(project_root, reporter_mode="html", include_database_global_setup=False):
    if reporter_mode not in {"html", "blob"}:
        raise ValueError(f"Unsupported Playwright reporter mode: {reporter_mode}")

    base_config = find_playwright_config(project_root)
    baseline_global_setup = None
    if include_database_global_setup:
        baseline_config = get_database_baseline_config()
        if baseline_config.get("enabled"):
            ensure_database_baseline_playwright_files(baseline_config)
            baseline_global_setup = (
                project_root / DATABASE_BASELINE_HELPER_DIR_NAME / DATABASE_BASELINE_GLOBAL_SETUP_FILENAME
            )

    run_id = make_execution_run_id()
    results_dir = project_root / "test-results" / RUN_ARTIFACTS_DIR_NAME / run_id
    report_dir = project_root / PLAYWRIGHT_REPORT_DIR_NAME / RUN_ARTIFACTS_DIR_NAME / run_id
    # Playwright's HTML reporter clears its output folder before writing the
    # merged report. Keep blob inputs outside report_dir so trace attachments
    # remain readable while the reporter copies them into report_dir/data.
    blob_report_dir = results_dir / "blob-reports"
    json_report_file = report_dir / "report.json"
    result_output_dir = results_dir.relative_to(project_root).as_posix()
    report_output_folder = report_dir.relative_to(project_root).as_posix()
    json_report_output_file = json_report_file.relative_to(project_root).as_posix()
    override_config = project_root / f".test-plan-viewer-video-{uuid.uuid4().hex}.config.ts"
    execution_override_entries = ""
    if baseline_global_setup:
        execution_override_entries = (
            f"  globalSetup: {json.dumps(str(baseline_global_setup), ensure_ascii=False)},\n"
            "  fullyParallel: false,\n"
            "  workers: 1,\n"
        )
    elif not include_database_global_setup:
        execution_override_entries = "  fullyParallel: false,\n  workers: 1,\n"

    if base_config:
        import_path = get_config_import_path(base_config, project_root)
        config_source = f"""import {{ defineConfig }} from '@playwright/test';
import baseConfigModule from {json.dumps(import_path)};

const baseConfig = ((baseConfigModule as any).default ?? baseConfigModule) as any;
const reporterMode = {json.dumps(reporter_mode)};
const defaultResultOutputDir = {json.dumps(result_output_dir)};
const resultOutputDir = process.env.TEST_PLAN_VIEWER_OUTPUT_DIR || defaultResultOutputDir;
const blobOutputFile = process.env.TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE;
const reportOutputFolder = {json.dumps(report_output_folder)};
const jsonReportFile = {json.dumps(json_report_output_file)};
const normalizeProjectName = (name: any, index: number) => {{
  const normalized = String(name || `project-${{index}}`)
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return normalized || `project-${{index}}`;
}};
const projects = Array.isArray(baseConfig.projects)
  ? baseConfig.projects.map((project: any, index: number) => ({{
      ...project,
      outputDir: `${{resultOutputDir}}/${{normalizeProjectName(project.name, index)}}`,
      use: {{ ...(project.use || {{}}), ignoreHTTPSErrors: true, video: 'on' }},
    }}))
  : undefined;
const normalizeReporterEntry = (reporter: any) => {{
  if (typeof reporter === 'string') return [reporter];
  if (Array.isArray(reporter) && typeof reporter[0] === 'string') return reporter;
  return null;
}};
const isReporterTuple = (reporter: any) =>
  Array.isArray(reporter) &&
  typeof reporter[0] === 'string' &&
  (reporter.length <= 1 || typeof reporter[1] !== 'string');
const normalizeReporter = (reporter: any) => {{
  if (!reporter) return [];
  if (typeof reporter === 'string') return [[reporter]];
  if (!Array.isArray(reporter)) return [];
  if (isReporterTuple(reporter)) return [reporter];
  return reporter.map(normalizeReporterEntry).filter(Boolean);
}};
const forceHtmlReporterClosed = (reporter: any) => {{
  const name = reporter[0];
  if (name !== 'html') return reporter;
  const options =
    reporter[1] && typeof reporter[1] === 'object'
      ? reporter[1]
      : {{}};
  return ['html', {{ ...options, open: 'never', outputFolder: reportOutputFolder }}];
}};
const reporters = normalizeReporter(baseConfig.reporter);
const hasHtmlReporter = reporters.some((reporter: any) => {{
  return reporter[0] === 'html';
}});
const passthroughReporters = reporters.filter((reporter: any) => {{
  return !['html', 'json', 'blob'].includes(reporter[0]);
}});
const htmlReporter = hasHtmlReporter
  ? reporters.map(forceHtmlReporterClosed)
  : [...reporters, ['html', {{ open: 'never', outputFolder: reportOutputFolder }}]];
const blobReporter = blobOutputFile ? ['blob', {{ outputFile: blobOutputFile }}] : ['blob'];
const reporter =
  reporterMode === 'blob'
    ? [...passthroughReporters, blobReporter, ['json', {{ outputFile: jsonReportFile }}]]
    : [...htmlReporter, ['json', {{ outputFile: jsonReportFile }}]];

export default defineConfig({{
  ...baseConfig,
  reporter,
  outputDir: resultOutputDir,
  use: {{ ...(baseConfig.use || {{}}), ignoreHTTPSErrors: true, video: 'on' }},
  ...(projects ? {{ projects }} : {{}}),
{execution_override_entries.rstrip()}
}});
"""
    else:
        config_source = f"""import {{ defineConfig }} from '@playwright/test';

const reporterMode = {json.dumps(reporter_mode)};
const defaultResultOutputDir = {json.dumps(result_output_dir)};
const resultOutputDir = process.env.TEST_PLAN_VIEWER_OUTPUT_DIR || defaultResultOutputDir;
const blobOutputFile = process.env.TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE;
const blobReporter = blobOutputFile ? ['blob', {{ outputFile: blobOutputFile }}] : ['blob'];
const reporter =
  reporterMode === 'blob'
    ? [blobReporter, ['json', {{ outputFile: {json.dumps(json_report_output_file)} }}]]
    : [
        ['html', {{ open: 'never', outputFolder: {json.dumps(report_output_folder)} }}],
        ['json', {{ outputFile: {json.dumps(json_report_output_file)} }}],
      ];

export default defineConfig({{
  reporter,
  outputDir: resultOutputDir,
  use: {{ ignoreHTTPSErrors: true, video: 'on' }},
{execution_override_entries.rstrip()}
}});
"""

    override_config.write_text(config_source, encoding="utf-8")
    return {
        "config_file": override_config,
        "results_dir": results_dir,
        "report_dir": report_dir,
        "blob_report_dir": blob_report_dir,
        "json_report_file": json_report_file,
        "run_id": run_id,
    }


def create_playwright_merge_report_config(project_root, report_dir, json_report_file):
    report_output_folder = report_dir.relative_to(project_root).as_posix()
    json_report_output_file = json_report_file.relative_to(project_root).as_posix()
    merge_config = project_root / f".test-plan-viewer-merge-{uuid.uuid4().hex}.config.ts"
    config_source = f"""import {{ defineConfig }} from '@playwright/test';

export default defineConfig({{
  testDir: {json.dumps(str(project_root / "tests"), ensure_ascii=False)},
  reporter: [
    ['html', {{ open: 'never', outputFolder: {json.dumps(report_output_folder)} }}],
    ['json', {{ outputFile: {json.dumps(json_report_output_file)} }}],
  ],
}});
"""
    merge_config.write_text(config_source, encoding="utf-8")
    return merge_config


def build_playwright_test_command(config_file, relative_script_paths):
    return execution_playwright.build_playwright_test_command(
        config_file,
        relative_script_paths,
        _execution_playwright_dependencies(),
    )


def build_playwright_merge_reports_command(config_file, blob_report_dir):
    return execution_playwright.build_playwright_merge_reports_command(
        config_file,
        blob_report_dir,
        _execution_playwright_dependencies(),
    )


def quote_command_argument(argument):
    return execution_playwright.quote_command_argument(argument)


def build_script_execution_context(module_name, filename, include_database_global_setup=False):
    script_file = get_script_file(module_name, filename)
    project_root = get_project_root()
    relative_script_path = get_script_test_relative_path(module_name, filename)

    if not script_file.exists():
        raise FileNotFoundError(f"Script file not found: {script_file}")

    artifacts = create_video_override_config(project_root, include_database_global_setup=include_database_global_setup)
    video_config = artifacts["config_file"]
    command, command_text = build_playwright_test_command(video_config, [relative_script_path])

    return {
        "script_file": script_file,
        "project_root": project_root,
        "relative_script_path": relative_script_path,
        "video_config": video_config,
        "results_dir": artifacts["results_dir"],
        "report_dir": artifacts["report_dir"],
        "run_id": artifacts["run_id"],
        "command": command,
        "command_text": command_text,
    }


def build_seed_execution_context():
    script_file = get_seed_script_file()
    project_root = get_project_root()
    relative_script_path = get_seed_script_relative_path()

    if not script_file.exists():
        raise FileNotFoundError(f"Seed script file not found: {script_file}")

    artifacts = create_video_override_config(project_root, include_database_global_setup=False)
    video_config = artifacts["config_file"]
    command, command_text = build_playwright_test_command(video_config, [relative_script_path])

    return {
        "script_file": script_file,
        "project_root": project_root,
        "relative_script_path": relative_script_path,
        "video_config": video_config,
        "results_dir": artifacts["results_dir"],
        "report_dir": artifacts["report_dir"],
        "run_id": artifacts["run_id"],
        "command": command,
        "command_text": command_text,
    }


def normalize_report_file_path(value, project_root):
    return execution_results.normalize_report_file_path(
        value,
        project_root,
        _execution_result_dependencies(),
    )


def update_script_result_status(script_results, relative_path, status):
    return execution_results.update_script_result_status(
        script_results,
        relative_path,
        status,
    )


def is_playwright_test_failed(test):
    return execution_results.is_playwright_test_failed(test)


def parse_playwright_json_script_results(
    json_report_file,
    module_name,
    filenames,
    fallback_status,
):
    return execution_results.parse_playwright_json_script_results(
        json_report_file,
        module_name,
        filenames,
        fallback_status,
        _execution_result_dependencies(),
    )


def parse_playwright_json_relative_script_results(
    json_report_file,
    relative_path_keys,
    fallback_status,
):
    return execution_results.parse_playwright_json_relative_script_results(
        json_report_file,
        relative_path_keys,
        fallback_status,
        _execution_result_dependencies(),
    )


def format_script_result_summary(script_results):
    return execution_results.format_script_result_summary(
        script_results
    )


def build_module_script_execution_context(
    module_name, filenames, execution_mode=EXECUTION_MODE_BATCH, include_database_global_setup=False
):
    module_name = validate_module_name(module_name)
    execution_mode = normalize_execution_mode(execution_mode)
    if not isinstance(filenames, list) or not filenames:
        raise ValueError("filenames must be a non-empty list.")

    unique_filenames = []
    seen = set()
    for raw_filename in filenames:
        filename = validate_script_filename(str(raw_filename or "").strip())
        if filename in seen:
            continue
        seen.add(filename)
        unique_filenames.append(filename)

    if not unique_filenames:
        raise ValueError("filenames must be a non-empty list.")

    script_files = []
    relative_script_paths = []
    for filename in unique_filenames:
        script_file = get_script_file(module_name, filename)
        if not script_file.exists():
            raise FileNotFoundError(f"Script file not found: {script_file}")
        script_files.append(script_file)
        relative_script_paths.append(get_script_test_relative_path(module_name, filename))

    project_root = get_project_root()
    artifacts = create_video_override_config(
        project_root,
        reporter_mode="blob" if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE else "html",
        include_database_global_setup=include_database_global_setup and execution_mode != EXECUTION_MODE_SERIAL_PER_FILE,
    )
    video_config = artifacts["config_file"]
    command, command_text = build_playwright_test_command(video_config, relative_script_paths)
    merge_config = None
    merge_command = []
    merge_command_text = ""
    if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE:
        merge_config = create_playwright_merge_report_config(
            project_root,
            artifacts["report_dir"],
            artifacts["json_report_file"],
        )
        merge_command, merge_command_text = build_playwright_merge_reports_command(
            merge_config,
            artifacts["blob_report_dir"],
        )

    return {
        "module_name": module_name,
        "execution_mode": execution_mode,
        "filenames": unique_filenames,
        "script_files": script_files,
        "project_root": project_root,
        "relative_script_paths": relative_script_paths,
        "video_config": video_config,
        "merge_config": merge_config,
        "blob_report_dir": artifacts["blob_report_dir"],
        "results_dir": artifacts["results_dir"],
        "report_dir": artifacts["report_dir"],
        "json_report_file": artifacts["json_report_file"],
        "run_id": artifacts["run_id"],
        "command": command,
        "command_text": command_text,
        "merge_command": merge_command,
        "merge_command_text": merge_command_text,
    }


def build_test_suite_execution_context(items, execution_mode=EXECUTION_MODE_BATCH, include_database_global_setup=False):
    execution_mode = normalize_execution_mode(execution_mode)
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list.")

    unique_items = []
    seen = set()
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        module_name = validate_module_name(str(raw_item.get("module_name", "")).strip())
        filename = validate_script_filename(str(raw_item.get("filename", "")).strip())
        key = f"{module_name}/{filename}"
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(
            {
                "module_name": module_name,
                "filename": filename,
                "key": key,
                "relative_path": get_script_test_relative_path(module_name, filename),
            }
        )

    if not unique_items:
        raise ValueError("items must be a non-empty list.")

    script_files = []
    relative_script_paths = []
    relative_path_keys = {}
    for item in unique_items:
        script_file = get_script_file(item["module_name"], item["filename"])
        if not script_file.exists():
            raise FileNotFoundError(f"Script file not found: {script_file}")
        script_files.append(script_file)
        relative_script_paths.append(item["relative_path"])
        relative_path_keys[item["relative_path"].replace("\\", "/")] = item["key"]

    project_root = get_project_root()
    artifacts = create_video_override_config(
        project_root,
        reporter_mode="blob" if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE else "html",
        include_database_global_setup=include_database_global_setup and execution_mode != EXECUTION_MODE_SERIAL_PER_FILE,
    )
    video_config = artifacts["config_file"]
    command, command_text = build_playwright_test_command(video_config, relative_script_paths)
    merge_config = None
    merge_command = []
    merge_command_text = ""
    if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE:
        merge_config = create_playwright_merge_report_config(
            project_root,
            artifacts["report_dir"],
            artifacts["json_report_file"],
        )
        merge_command, merge_command_text = build_playwright_merge_reports_command(
            merge_config,
            artifacts["blob_report_dir"],
        )

    return {
        "execution_mode": execution_mode,
        "items": unique_items,
        "script_files": script_files,
        "project_root": project_root,
        "relative_script_paths": relative_script_paths,
        "relative_path_keys": relative_path_keys,
        "video_config": video_config,
        "merge_config": merge_config,
        "blob_report_dir": artifacts["blob_report_dir"],
        "results_dir": artifacts["results_dir"],
        "report_dir": artifacts["report_dir"],
        "json_report_file": artifacts["json_report_file"],
        "run_id": artifacts["run_id"],
        "command": command,
        "command_text": command_text,
        "merge_command": merge_command,
        "merge_command_text": merge_command_text,
    }


def build_script_recording_context(module_name, filename):
    script_file = get_script_file(module_name, filename)
    project_root = get_project_root()
    relative_script_path = get_script_test_relative_path(module_name, filename)

    if not script_file.exists():
        raise FileNotFoundError(f"Script file not found: {script_file}")

    command_display = [
        "npx",
        "playwright",
        "codegen",
        "--target=playwright-test",
        "--output",
        relative_script_path,
    ]
    command = [get_npx_executable(), *command_display[1:]]

    return {
        "script_file": script_file,
        "project_root": project_root,
        "relative_script_path": relative_script_path,
        "command": command,
        "command_text": " ".join(quote_command_argument(item) for item in command_display),
    }


def make_job_snapshot(job):
    return {
        "id": job["id"],
        "status": job["status"],
        "module_name": job["module_name"],
        "plan_filename": job.get("plan_filename") or get_default_plan_filename(job["module_name"]),
        "target_path": job["target_path"],
        "logs": list(job["logs"]),
        "error": job.get("error"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def update_generation_job(job_id, **updates):
    with PLAN_GENERATION_LOCK:
        job = PLAN_GENERATION_JOBS[job_id]
        job.update(updates)
        job["updated_at"] = time.time()
        snapshot = make_job_snapshot(job)

    try:
        save_platform_job_to_mysql(snapshot)
    except Exception:
        pass
    return snapshot


def append_generation_log(job_id, message):
    with PLAN_GENERATION_LOCK:
        job = PLAN_GENERATION_JOBS[job_id]
        job["logs"].append(message)
        job["updated_at"] = time.time()
        snapshot = make_job_snapshot(job)

    try:
        save_platform_job_to_mysql(snapshot)
    except Exception:
        pass
    return snapshot


def opencode_url(path, query=None):
    config = get_opencode_config()
    base_url = config["opencode_server_url"].rstrip("/")
    url = f"{base_url}{path}"

    if query:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlparse.urlencode(query)}"

    return url


def opencode_headers(accept="application/json", content_type=None):
    config = get_opencode_config()

    headers = {"Accept": accept}
    if content_type:
        headers["Content-Type"] = content_type

    password = config.get("opencode_password", "")
    if password:
        username = config.get("opencode_username", "opencode") or "opencode"
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    return headers


def opencode_project_query():
    return {"directory": str(get_project_root())}


def opencode_request(path, payload=None, timeout=None, method=None, query=None, accept="application/json"):
    if timeout is None:
        timeout = get_opencode_task_timeout_seconds()

    data = None
    headers = opencode_headers(accept=accept)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request_method = method or ("POST" if payload is not None else "GET")
    request_obj = urlrequest.Request(
        opencode_url(path, query=query),
        data=data,
        headers=headers,
        method=request_method,
    )

    try:
        with urlrequest.urlopen(request_obj, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenCode HTTP {exc.code}: {body or exc.reason}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"无法连接 OpenCode Server: {exc.reason}") from exc

    if not body:
        return {}

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def abort_opencode_session(session_id):
    if not session_id:
        return False

    opencode_request(
        f"/session/{session_id}/abort",
        method="POST",
        timeout=10,
        query=opencode_project_query(),
    )
    return True


OPENCODE_TASK_REGISTRY = generation_cancellation.OpenCodeTaskRegistry(
    tasks=OPENCODE_TASKS,
    lock=OPENCODE_TASK_LOCK,
    get_job=lambda job_id: get_test_job(job_id),
    update_job=lambda job_id, **updates: update_test_job(job_id, **updates),
    database_enabled=lambda: is_platform_database_enabled(),
    abort_session=lambda session_id: abort_opencode_session(session_id),
)
register_opencode_task = OPENCODE_TASK_REGISTRY.register
set_opencode_task_session = OPENCODE_TASK_REGISTRY.set_session
is_opencode_task_cancelled = OPENCODE_TASK_REGISTRY.is_cancelled
cleanup_opencode_task = OPENCODE_TASK_REGISTRY.cleanup
cancel_opencode_task = OPENCODE_TASK_REGISTRY.cancel


def opencode_event_stream(timeout=None):
    if timeout is None:
        timeout = get_opencode_task_timeout_seconds()

    request_obj = urlrequest.Request(
        opencode_url("/event", query=opencode_project_query()),
        headers=opencode_headers(accept="text/event-stream"),
        method="GET",
    )

    try:
        return urlrequest.urlopen(request_obj, timeout=timeout)
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenCode Event Stream HTTP {exc.code}: {body or exc.reason}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"无法连接 OpenCode Event Stream: {exc.reason}") from exc


def format_opencode_execution_error(message):
    return generation_opencode.format_opencode_execution_error(
        message, OPENCODE_TOOL_STATUS_ERROR_PATTERN
    )


def send_opencode_prompt(prompt, default_agent=None):
    ensure_project_opencode_prompt_files()
    session = opencode_request(
        "/session",
        build_opencode_session_payload("生成测试计划", prompt, default_agent=default_agent),
        timeout=30,
        query=opencode_project_query(),
    )
    session_id = session.get("id")
    if not session_id:
        raise RuntimeError(f"OpenCode 未返回 session id: {session}")

    return send_opencode_prompt_to_session(session_id, prompt, default_agent=default_agent)


def send_opencode_prompt_to_session(session_id, prompt, default_agent=None):
    ensure_project_opencode_prompt_files()
    return opencode_request(
        f"/session/{session_id}/message",
        build_opencode_prompt_payload(prompt, default_agent=default_agent),
        query=opencode_project_query(),
    )


def send_opencode_prompt_async(session_id, prompt, default_agent=None):
    ensure_project_opencode_prompt_files()
    return opencode_request(
        f"/session/{session_id}/prompt_async",
        build_opencode_prompt_payload(prompt, default_agent=default_agent),
        timeout=30,
        query=opencode_project_query(),
    )


def send_opencode_prompt_cancellable(prompt, job_id, *, default_agent=None, session_title=None):
    title = session_title or agent_message("generate_plan_title", module="")
    return generation_cancellation.send_cancellable_prompt(
        prompt, job_id, default_agent=default_agent, session_title=title,
        ensure_prompt_files=ensure_project_opencode_prompt_files, register_task=register_opencode_task,
        is_cancelled=is_opencode_task_cancelled,
        create_session=lambda payload: opencode_request("/session", payload, timeout=30, query=opencode_project_query()),
        build_session_payload=build_opencode_session_payload, set_session=set_opencode_task_session,
        send_prompt=send_opencode_prompt_to_session, abort_session=abort_opencode_session,
        task_timeout=get_opencode_task_timeout_seconds,
        timeout_error=lambda seconds: agent_message("opencode_wait_timeout", duration=format_timeout_seconds(seconds)),
        cancelled_error=lambda: OpencodeTaskCancelled(agent_message("task_cancelled_generic")),
        cleanup_task=cleanup_opencode_task,
    )


def summarize_opencode_response(response):
    if not response:
        return ""

    parts = response.get("parts")
    if isinstance(parts, list):
        texts = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        if texts:
            return "\n".join(texts)[-2000:]

    raw = response.get("raw")
    if isinstance(raw, str):
        return raw[-2000:]

    return json.dumps(response, ensure_ascii=False)[-2000:]


def summarize_opencode_messages(messages):
    if not isinstance(messages, list):
        return summarize_opencode_response(messages if isinstance(messages, dict) else {"raw": str(messages)})

    texts = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if part.get("type") in {"text", "reasoning"} and isinstance(text, str) and text.strip():
                texts.append(text.strip())

    return "\n".join(texts)[-2000:]


def get_opencode_event_session_id(event):
    properties = event.get("properties") or {}
    if isinstance(properties.get("sessionID"), str):
        return properties["sessionID"]

    info = properties.get("info")
    if isinstance(info, dict) and isinstance(info.get("id"), str):
        return info["id"]

    part = properties.get("part")
    if isinstance(part, dict) and isinstance(part.get("sessionID"), str):
        return part["sessionID"]

    return None


def stream_plan_generation(
    module_name,
    full_prompt,
    target_file,
    *,
    setup_targets,
    completion_check=None,
    completion_required=True,
    target_label=None,
    session_title=None,
    success_message=None,
    success_payload_factory=None,
    default_agent=None,
    setup_parent_run_id=None,
    cancel_job_id=None,
    job_id=None,
    agent_stream=False,
    finish_job_on_success=True,
    validate_plan_completion=False,
    agent_cancel_check=None,
    cancel_cleanup=None,
):
    session_id = None
    prompt_error = []
    seen_tool_states = set()
    seen_tool_inputs = set()
    seen_tool_outputs = set()
    seen_tool_metadata = set()
    seen_diff_files = set()
    part_texts = {}
    next_tool_input_buffers = {}
    streamed_any_text = False
    terminalized = False
    cancel_cleanup_completed = False
    output_batcher = AgentOutputBatcher()
    log_redaction_config = [None]
    job_log_writer = (
        BufferedJobLogWriter(get_job_log_path(job_id), tail_bytes=JOB_LOG_TAIL_LIMIT)
        if job_id
        else None
    )
    plan_completion_probe = (
        PlanCompletionProbe(
            target_file,
            language=agent_project_language(),
        )
        if completion_check is None
        and completion_required
        and validate_plan_completion
        else None
    )
    completion_enabled = completion_check is not None or plan_completion_probe is not None
    target_label = target_label or str(target_file)
    session_title = session_title or agent_message("generate_plan_title", module=module_name)
    success_message = success_message or agent_message("task_success_file", target=target_label)
    register_opencode_task(cancel_job_id, target_label)
    if job_id:
        update_test_job(job_id, fetch=False, status="running", started_at=current_time_ms())

    def is_complete():
        if completion_check is not None:
            return completion_check()
        if plan_completion_probe is not None:
            return plan_completion_probe.check()
        return target_file.exists()

    def emit_log(message, *, persist=True):
        checkpoint = None
        if log_redaction_config[0] is None:
            context_project = current_context_project()
            if context_project:
                log_redaction_config[0] = parse_target_system_config(
                    context_project.get("target_system")
                )
            elif has_request_context():
                log_redaction_config[0] = get_current_target_system_config()
            else:
                log_redaction_config[0] = {}
        safe_message = redact_sensitive_text(message, log_redaction_config[0])
        payload = {"message": safe_message, "job_id": job_id}
        if job_log_writer is not None and safe_message and persist:
            snapshot = job_log_writer.append(f"{safe_message}\n")
            if is_platform_database_enabled() and job_log_writer.snapshot_due():
                if agent_stream:
                    checkpoint = snapshot
                    payload["_job_log_snapshot"] = snapshot.as_updates()
                else:
                    persist_test_job_log_snapshot(job_id, snapshot)
                    job_log_writer.mark_snapshot_persisted(snapshot)
        yield sse_payload("log", payload)
        if checkpoint is not None:
            # Resumption acknowledges the Agent consumer committed the log
            # event and its checkpoint in one transaction.
            job_log_writer.mark_snapshot_persisted(checkpoint)

    def build_success_payload():
        if not success_payload_factory:
            return {}

        payload = success_payload_factory()
        if not isinstance(payload, dict):
            return {}

        return payload

    def get_success_target_asset_id(success_payload):
        asset = success_payload.get("asset") if isinstance(success_payload, dict) else None
        return asset.get("asset_id") if isinstance(asset, dict) else None

    def safe_serialized_job():
        if not job_id:
            return None
        try:
            return serialize_job(get_test_job(job_id))
        except Exception:
            return None

    def emit_success_result():
        nonlocal terminalized
        success_payload = build_success_payload()
        success_logs = []
        if "video" in success_payload:
            video_info = success_payload.get("video")
            if video_info:
                success_logs.append(agent_message("video_found", path=video_info.get("path", "")))
            else:
                success_logs.append(success_payload.get("video_error") or agent_message("video_missing"))
        success_logs.append(success_message)
        if job_id:
            for message in success_logs:
                append_test_job_log(
                    job_id,
                    f"{message}\n",
                    writer=job_log_writer,
                    persist_snapshot=False,
                )
        if job_id:
            if finish_job_on_success:
                finish_test_job(
                    job_id,
                    "succeeded",
                    target_asset_id=get_success_target_asset_id(success_payload),
                    log_writer=job_log_writer,
                )
                terminalized = True
            elif job_log_writer is not None:
                snapshot = job_log_writer.snapshot()
                persist_test_job_log_snapshot(job_id, snapshot)
                job_log_writer.mark_snapshot_persisted(snapshot)
        if not finish_job_on_success:
            ready_payload = {
                **success_payload,
                "source_ready": True,
                "plan_phase": "splitting",
            }
            yield emit_status("running", extra=ready_payload)
            for message in success_logs:
                yield from emit_log(message, persist=False)
            return
        yield emit_status("succeeded", extra=success_payload)
        for message in success_logs:
            yield from emit_log(message, persist=False)
        done_payload = {"ok": True}
        done_payload.update(success_payload)
        if job_id:
            done_payload["job_id"] = job_id
            done_payload["job"] = safe_serialized_job()
        yield sse_payload("done", done_payload)

    def emit_status(status, error=None, extra=None):
        payload = {
            "status": status,
            "module_name": module_name,
            "target_path": target_label,
            "error": error,
        }
        if job_id:
            payload["job_id"] = job_id
            payload["job"] = safe_serialized_job()
        if extra:
            payload.update(extra)
        return sse_payload("status", payload)

    def prompt_worker():
        try:
            raise_if_cancelled()
            send_opencode_prompt_async(session_id, full_prompt, default_agent=default_agent)
        except Exception as exc:
            prompt_error.append(exc)

    def build_delta_event(batch):
        payload = {
            "text": batch.text,
            "job_id": job_id,
            "module_name": module_name,
            "stream_kind": "model-output",
            **batch.metadata(),
        }
        checkpoint = None
        if job_log_writer is not None:
            snapshot = job_log_writer.append(batch.text)
            if job_log_writer.snapshot_due():
                if agent_stream:
                    checkpoint = snapshot
                    payload["_job_log_snapshot"] = snapshot.as_updates()
                else:
                    persist_test_job_log_snapshot(job_id, snapshot)
                    job_log_writer.mark_snapshot_persisted(snapshot)
        return sse_payload("delta", payload), checkpoint

    def yield_batch(batch):
        if batch is None:
            return
        event, checkpoint = build_delta_event(batch)
        yield event
        if checkpoint is not None and job_log_writer is not None:
            # The generator resumes only after the Agent consumer has
            # committed the yielded batch. A failed consumer leaves the
            # checkpoint due so a later terminal flush can persist it.
            job_log_writer.mark_snapshot_persisted(checkpoint)

    def emit_delta(text):
        nonlocal streamed_any_text
        if not text:
            return
        streamed_any_text = True
        for batch in output_batcher.add(text):
            yield from yield_batch(batch)

    def flush_delta(reason="structured"):
        batch = output_batcher.flush(reason=reason)
        if batch is not None:
            yield from yield_batch(batch)

    def stage_terminal_result(status, error):
        """Write the final file tail and job state before terminal SSE yields."""

        nonlocal terminalized

        batch = output_batcher.finish(reason=status)
        terminal_delta = None
        if batch is not None:
            if job_log_writer is not None:
                job_log_writer.append(batch.text)
            terminal_delta = sse_payload(
                "delta",
                {
                    "text": batch.text,
                    "job_id": job_id,
                    "module_name": module_name,
                    "stream_kind": "model-output",
                    **batch.metadata(),
                },
            )
        if job_id:
            terminal_message = str(error) if status == "cancelled" else agent_message("task_failed", error=error)
            append_test_job_log(
                job_id,
                f"{terminal_message}\n",
                writer=job_log_writer,
                persist_snapshot=False,
            )
            finish_test_job(
                job_id,
                status,
                error=str(error),
                log_writer=job_log_writer,
            )
            terminalized = True
        return terminal_delta

    def wait_for_stable_completion(timeout_seconds=2.0):
        if not completion_required:
            return True
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            raise_if_cancelled()
            if is_complete():
                return True
            time.sleep(0.1)
        return False

    active_sse_reader = [None]

    def iter_bounded_opencode_events(response):
        reader = BoundedSseReader(
            response,
            max_queue_size=256,
            join_timeout=1.0,
            event_iterator=iter_sse_events,
        ).start()
        active_sse_reader[0] = reader
        try:
            while True:
                item = reader.poll(timeout=0.1)
                if item is None:
                    yield None, None
                    continue
                if item.kind == "error":
                    raise item.error or RuntimeError(agent_message("event_stream_read_failed"))
                if item.kind == "eof":
                    return
                yield item.event, item.data
        finally:
            reader.close()
            if active_sse_reader[0] is reader:
                active_sse_reader[0] = None

    def wait_for_idle(deadline, timeout_seconds):
        last_notice = 0
        while time.monotonic() < deadline:
            raise_if_cancelled()
            if is_complete():
                return True
            try:
                statuses = opencode_request(
                    "/session/status",
                    method="GET",
                    timeout=15,
                    query=opencode_project_query(),
                )
            except Exception:
                statuses = {}

            status = statuses.get(session_id) if isinstance(statuses, dict) else None
            if isinstance(status, dict) and status.get("type") == "idle":
                return True

            now = time.monotonic()
            if now - last_notice >= 10:
                last_notice = now
                yield from emit_log(agent_message("opencode_waiting"))
            time.sleep(0.5)

        raise RuntimeError(agent_message("opencode_wait_timeout", duration=format_timeout_seconds(timeout_seconds)))

    def abort_active_session():
        if session_id:
            try:
                abort_opencode_session(session_id)
            except Exception:
                pass

    def run_cancel_cleanup():
        nonlocal cancel_cleanup_completed
        if cancel_cleanup_completed or cancel_cleanup is None:
            return ""
        cancel_cleanup_completed = True
        try:
            cancel_cleanup()
            return ""
        except Exception as cleanup_error:
            return str(cleanup_error)

    def raise_if_cancelled():
        if agent_cancel_check is not None:
            try:
                agent_cancel_check()
            except OpencodeTaskCancelled:
                abort_active_session()
                raise
        if not is_opencode_task_cancelled(cancel_job_id):
            return

        abort_active_session()
        raise OpencodeTaskCancelled(agent_message("task_cancelled_generic"))

    def send_fallback_prompt_with_cancellation(timeout_seconds):
        result = []
        completed = threading.Event()

        def fallback_worker():
            try:
                response = send_opencode_prompt_to_session(
                    session_id,
                    full_prompt,
                    default_agent=default_agent,
                )
                result.append(("response", response))
            except BaseException as exc:
                result.append(("error", exc))
            finally:
                completed.set()

        threading.Thread(target=fallback_worker, daemon=True).start()
        deadline = time.monotonic() + timeout_seconds
        while not completed.is_set():
            raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                abort_active_session()
                raise RuntimeError(agent_message("opencode_fallback_timeout", duration=format_timeout_seconds(timeout_seconds)))
            completed.wait(min(0.1, remaining))

        raise_if_cancelled()
        kind, value = result[0]
        if kind == "error":
            raise value
        return value

    def trim_log_value(value, limit=30000):
        if value is None:
            return ""
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except TypeError:
                value = str(value)
        value = strip_ansi(value)
        if len(value) <= limit:
            return value
        omitted = len(value) - limit
        return f"{value[:limit]}\n{agent_message('truncated', count=omitted)}"

    def compact_json(value, limit=6000):
        if value in (None, ""):
            return ""
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            text = str(value)
        return trim_log_value(text, limit)

    def fingerprint(value):
        text = trim_log_value(value, 30000)
        return (len(text), text[:160], text[-160:])

    def emit_once(bucket, key, message):
        if key in bucket or not message:
            return ""
        bucket.add(key)
        yield from emit_log(message)

    def tool_identity(part=None, properties=None):
        part = part or {}
        properties = properties or {}
        return (
            part.get("id")
            or part.get("callID")
            or properties.get("callID")
            or properties.get("partID")
            or properties.get("tool")
            or "tool"
        )

    def tool_input_message(title, value):
        text = compact_json(value)
        if not text:
            return ""
        return f"{agent_message('tool_input', title=title)}\n{text}"

    def tool_metadata_message(title, value):
        text = compact_json(value)
        if not text or text == "{}":
            return ""
        return f"{agent_message('tool_metadata', title=title)}\n{text}"

    def tool_attachments_message(title, attachments):
        if not attachments:
            return ""
        lines = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            label = item.get("filename") or item.get("name") or item.get("mime") or "attachment"
            detail = item.get("url") or item.get("uri") or ""
            mime = item.get("mime") or ""
            lines.append(" ".join(part for part in [str(label), f"({mime})" if mime else "", str(detail)] if part))
        if not lines:
            return ""
        return f"{agent_message('tool_attachments', title=title)}\n" + "\n".join(lines)

    def tool_content_text(content):
        if not isinstance(content, list):
            return ""
        lines = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text" and isinstance(item.get("text"), str):
                lines.append(item["text"])
            elif item_type == "file":
                label = item.get("name") or item.get("mime") or "file"
                uri = item.get("uri") or item.get("url") or ""
                mime = item.get("mime") or ""
                lines.append(" ".join(part for part in [agent_message("file_tag", label=label), f"({mime})" if mime else "", uri] if part))
        return trim_log_value("\n".join(lines))

    def emit_tool_part_logs(part):
        state = part.get("state") or {}
        state_status = state.get("status", "pending")
        tool_name = part.get("tool") or "tool"
        title = state.get("title") or tool_name
        identity = tool_identity(part=part)

        status_key = (identity, "status", state_status, title)
        if state_status == "completed":
            yield from emit_once(seen_tool_states, status_key, agent_message("tool_completed", title=title))
        elif state_status == "error":
            detail = project_copy(f": {state.get('error', '')}", f"，{state.get('error', '')}") if state.get("error") else ""
            yield from emit_once(
                seen_tool_states,
                status_key,
                agent_message("tool_failed", title=title, detail=detail),
            )
        elif state_status == "running":
            yield from emit_once(seen_tool_states, status_key, agent_message("tool_running", title=title))
        elif state_status == "pending":
            yield from emit_once(seen_tool_states, status_key, agent_message("tool_pending", title=title))

        input_message = tool_input_message(title, state.get("input"))
        if input_message:
            yield from emit_once(seen_tool_inputs, (identity, "input", fingerprint(input_message)), input_message)

        metadata_message = tool_metadata_message(title, state.get("metadata"))
        if metadata_message:
            yield from emit_once(seen_tool_metadata, (identity, "metadata", fingerprint(metadata_message)), metadata_message)

        if state_status == "completed":
            output = state.get("output")
            output_text = trim_log_value(output)
            if output_text:
                yield from emit_once(
                    seen_tool_outputs,
                    (identity, "output", fingerprint(output_text)),
                    f"{agent_message('tool_output', title=title)}\n{output_text}",
                )
            attachments_message = tool_attachments_message(title, state.get("attachments"))
            if attachments_message:
                yield from emit_once(
                    seen_tool_outputs,
                    (identity, "attachments", fingerprint(attachments_message)),
                    attachments_message,
                )
        elif state_status == "error" and state.get("error"):
            error_text = trim_log_value(state.get("error"))
            yield from emit_once(
                seen_tool_outputs,
                (identity, "error", fingerprint(error_text)),
                f"{agent_message('tool_error', title=title)}\n{error_text}",
            )

    def emit_next_tool_logs(event_type, properties):
        call_id = properties.get("callID") or properties.get("tool") or "tool"
        title = properties.get("tool") or properties.get("name") or call_id

        if event_type == "session.next.tool.input.started":
            yield from emit_once(seen_tool_states, (call_id, event_type, title), agent_message("tool_input_started", title=title))
            return

        if event_type == "session.next.tool.input.delta":
            delta = properties.get("delta")
            if isinstance(delta, str) and delta:
                next_tool_input_buffers[call_id] = f"{next_tool_input_buffers.get(call_id, '')}{delta}"
                yield from emit_once(
                    seen_tool_inputs,
                    (call_id, event_type, len(next_tool_input_buffers[call_id]), fingerprint(delta)),
                    f"{agent_message('tool_input_delta', title=title)}\n{trim_log_value(delta, 4000)}",
                )
            return

        if event_type == "session.next.tool.input.ended":
            text = properties.get("text")
            if not isinstance(text, str):
                text = next_tool_input_buffers.get(call_id, "")
            yield from emit_once(
                seen_tool_inputs,
                (call_id, event_type, fingerprint(text)),
                f"{agent_message('tool_input_completed', title=title)}\n{trim_log_value(text, 6000)}"
                if text else agent_message("tool_input_completed", title=title),
            )
            return

        if event_type == "session.next.tool.called":
            yield from emit_once(seen_tool_states, (call_id, event_type, title), agent_message("tool_running", title=title))
            input_message = tool_input_message(title, properties.get("input"))
            if input_message:
                yield from emit_once(seen_tool_inputs, (call_id, "called-input", fingerprint(input_message)), input_message)
            return

        if event_type == "session.next.tool.progress":
            content_text = tool_content_text(properties.get("content"))
            structured_text = compact_json(properties.get("structured"))
            if content_text:
                yield from emit_once(
                    seen_tool_outputs,
                    (call_id, event_type, "content", fingerprint(content_text)),
                    f"{agent_message('tool_progress', title=title)}\n{content_text}",
                )
            if structured_text and structured_text != "{}":
                yield from emit_once(
                    seen_tool_metadata,
                    (call_id, event_type, "structured", fingerprint(structured_text)),
                    f"{agent_message('tool_progress_data', title=title)}\n{structured_text}",
                )
            return

        if event_type == "session.next.tool.success":
            yield from emit_once(seen_tool_states, (call_id, event_type, title), agent_message("tool_completed", title=title))
            content_text = tool_content_text(properties.get("content"))
            if content_text:
                yield from emit_once(
                    seen_tool_outputs,
                    (call_id, "success-content", fingerprint(content_text)),
                    f"{agent_message('tool_output', title=title)}\n{content_text}",
                )
            structured_text = compact_json(properties.get("structured"))
            if structured_text and structured_text != "{}":
                yield from emit_once(
                    seen_tool_metadata,
                    (call_id, "success-structured", fingerprint(structured_text)),
                    f"{agent_message('tool_structured', title=title)}\n{structured_text}",
                )
            result_text = compact_json(properties.get("result"))
            if result_text and result_text != "{}":
                yield from emit_once(
                    seen_tool_outputs,
                    (call_id, "success-result", fingerprint(result_text)),
                    f"{agent_message('tool_raw_result', title=title)}\n{result_text}",
                )
            return

        if event_type == "session.next.tool.failed":
            error = properties.get("error") or {}
            if isinstance(error, dict):
                message = error.get("message") or compact_json(error)
            else:
                message = str(error)
            detail = project_copy(f": {message}", f"，{message}") if message else ""
            yield from emit_once(
                seen_tool_states,
                (call_id, event_type, title, fingerprint(message)),
                agent_message("tool_failed", title=title, detail=detail),
            )
            result_text = compact_json(properties.get("result"))
            if result_text and result_text != "{}":
                yield from emit_once(
                    seen_tool_outputs,
                    (call_id, "failed-result", fingerprint(result_text)),
                    f"{agent_message('tool_failed_result', title=title)}\n{result_text}",
                )

    try:
        yield emit_status("running")
        raise_if_cancelled()
        setup_logs = []
        try:
            prepare_bound_setup(
                setup_parent_run_id or job_id,
                setup_targets,
                emit_log=setup_logs.append,
            )
        except Exception:
            for message in setup_logs:
                yield from emit_log(message)
            raise
        for message in setup_logs:
            raise_if_cancelled()
            yield from emit_log(message)
        raise_if_cancelled()
        yield from emit_log(agent_message("task_created_target", target=target_label))
        session = opencode_request(
            "/session",
            build_opencode_session_payload(session_title, full_prompt, default_agent=default_agent),
            timeout=30,
            query=opencode_project_query(),
        )
        session_id = session.get("id")
        if not session_id:
            raise RuntimeError(agent_message("session_missing", session=session))

        if set_opencode_task_session(cancel_job_id, session_id):
            raise_if_cancelled()
        yield from emit_log(agent_message("session_created", session_id=session_id))
        opencode_timeout = get_opencode_task_timeout_seconds()

        try:
            raise_if_cancelled()
            event_response = opencode_event_stream(timeout=opencode_timeout)
        except Exception as exc:
            raise_if_cancelled()
            yield from emit_log(agent_message("event_stream_fallback", error=exc))
            response = send_fallback_prompt_with_cancellation(opencode_timeout)
            raise_if_cancelled()
            summary = summarize_opencode_response(response)
            if summary:
                yield from emit_delta(summary)
            yield from flush_delta("fallback")
            if completion_required and not wait_for_stable_completion():
                raise RuntimeError(agent_message("target_missing_after_return", target=target_label))
            yield from emit_success_result()
            return

        with event_response:
            worker = threading.Thread(target=prompt_worker, daemon=True)
            worker.start()
            yield from emit_log(agent_message("submitted"))

            deadline = time.monotonic() + opencode_timeout
            is_idle = False
            for _, data in iter_bounded_opencode_events(event_response):
                raise_if_cancelled()
                if prompt_error:
                    raise prompt_error[0]

                now = time.monotonic()
                if now > deadline:
                    raise RuntimeError(agent_message("realtime_timeout", duration=format_timeout_seconds(opencode_timeout)))

                due_batch = output_batcher.flush_due()
                if due_batch is not None:
                    yield from yield_batch(due_batch)

                if completion_enabled and is_complete():
                    yield from flush_delta("completion")
                    if plan_completion_probe is not None:
                        yield emit_status(
                            "running",
                            extra={
                                "source_plan_ready": True,
                                "plan_phase": "splitting",
                                "case_count": len(plan_completion_probe.cases),
                            },
                        )
                        yield from emit_log(agent_message("source_ready"))
                    else:
                        yield from emit_log(agent_message("target_detected"))
                    if session_id:
                        try:
                            abort_opencode_session(session_id)
                        except Exception:
                            pass
                    if active_sse_reader[0] is not None:
                        active_sse_reader[0].close()
                    worker.join(timeout=1)
                    yield from emit_success_result()
                    return

                if data is None:
                    if agent_stream:
                        yield ": agent-stream-tick\n\n"
                    continue

                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                if event_type == "server.connected":
                    continue

                if get_opencode_event_session_id(event) != session_id:
                    continue

                properties = event.get("properties") or {}
                part = properties.get("part") or {}
                is_model_text_event = event_type == "message.part.delta" or (
                    event_type == "message.part.updated"
                    and part.get("type") in {"text", "reasoning"}
                )
                if not is_model_text_event:
                    yield from flush_delta("structured")

                if event_type in {
                    "session.next.tool.input.started",
                    "session.next.tool.input.delta",
                    "session.next.tool.input.ended",
                    "session.next.tool.called",
                    "session.next.tool.progress",
                    "session.next.tool.success",
                    "session.next.tool.failed",
                }:
                    yield from emit_next_tool_logs(event_type, properties)
                    continue

                if event_type == "message.part.delta":
                    field = properties.get("field")
                    delta = properties.get("delta")
                    if field in {"text", "reasoning"} and isinstance(delta, str):
                        yield from emit_delta(delta)
                    continue

                if event_type == "message.part.updated":
                    part_id = part.get("id")
                    part_type = part.get("type")

                    if part_type in {"text", "reasoning"}:
                        delta = properties.get("delta")
                        text = part.get("text")
                        if isinstance(delta, str):
                            yield from emit_delta(delta)
                            if isinstance(text, str) and part_id:
                                part_texts[part_id] = text
                        elif isinstance(text, str) and part_id:
                            previous = part_texts.get(part_id, "")
                            if text.startswith(previous):
                                yield from emit_delta(text[len(previous) :])
                            elif text != previous:
                                yield from emit_delta(text)
                            part_texts[part_id] = text
                        continue

                    if part_type == "tool":
                        yield from emit_tool_part_logs(part)
                        continue

                    if part_type == "patch":
                        files = part.get("files") or []
                        if files:
                            yield from emit_log(agent_message("patch_generated", files=", ".join(files)))
                        continue

                if event_type == "file.edited":
                    file_path = properties.get("file")
                    if file_path:
                        yield from emit_log(agent_message("file_edited", path=file_path))
                    continue

                if event_type == "session.diff":
                    for item in properties.get("diff") or []:
                        file_path = item.get("file") if isinstance(item, dict) else None
                        if file_path and file_path not in seen_diff_files:
                            seen_diff_files.add(file_path)
                            yield from emit_log(agent_message("file_changed", path=file_path))
                    continue

                if event_type == "session.status":
                    status = properties.get("status") or {}
                    if status.get("type") == "retry":
                        detail = project_copy(f": {status.get('message')}", f"：{status.get('message')}") if status.get("message") else ""
                        yield from emit_log(agent_message("opencode_retrying", detail=detail))
                    continue

                if event_type == "session.error":
                    error_info = properties.get("error") or {}
                    message = error_info.get("data", {}).get("message") or error_info.get("name") or error_info
                    raise RuntimeError(agent_message("opencode_execution_failed", error=format_opencode_execution_error(message)))

                if event_type == "session.idle":
                    is_idle = True
                    break

            yield from flush_delta("reader-eof")
            worker.join(timeout=1)
            if prompt_error:
                raise prompt_error[0]

            if not is_idle:
                yield from emit_log(agent_message("event_stream_ended"))
                yield from wait_for_idle(deadline, opencode_timeout)

        messages = opencode_request(
            f"/session/{session_id}/message",
            method="GET",
            timeout=30,
            query=opencode_project_query(),
        )
        summary = summarize_opencode_messages(messages)
        if summary and not streamed_any_text:
            yield from emit_delta(summary)

        yield from flush_delta("stream-finished")

        if completion_required and not wait_for_stable_completion():
            raise RuntimeError(agent_message("target_missing_after_end", target=target_label))

        yield from emit_success_result()
    except OpencodeTaskCancelled as exc:
        cleanup_error = run_cancel_cleanup()
        terminal_delta = stage_terminal_result("cancelled", exc)
        if terminal_delta:
            yield terminal_delta
        yield emit_status("cancelled", str(exc))
        yield from emit_log(str(exc), persist=False)
        if cleanup_error:
            yield from emit_log(f"取消后的文件回滚失败：{cleanup_error}", persist=False)
        done_payload = {"ok": False, "status": "cancelled", "error": str(exc)}
        if job_id:
            done_payload["job_id"] = job_id
            done_payload["job"] = safe_serialized_job()
        yield sse_payload("done", done_payload)
    except GeneratorExit:
        run_cancel_cleanup()
        pending_batch = output_batcher.finish(reason="generator-exit")
        if pending_batch is not None and job_log_writer is not None:
            try:
                job_log_writer.append(pending_batch.text)
            except Exception:
                pass
        if job_id and job_log_writer is not None and not terminalized:
            disconnect_error = agent_message("stream_closed")
            try:
                job_log_writer.append(f"{disconnect_error}\n")
                finish_test_job(
                    job_id,
                    "cancelled",
                    error=disconnect_error,
                    log_writer=job_log_writer,
                )
            except Exception:
                pass
        if cancel_job_id and session_id:
            try:
                abort_opencode_session(session_id)
            except Exception:
                pass
        raise
    except Exception as exc:
        terminal_delta = stage_terminal_result("failed", exc)
        if terminal_delta:
            yield terminal_delta
        yield emit_status("failed", str(exc))
        yield from emit_log(agent_message("task_failed", error=exc), persist=False)
        done_payload = {"ok": False, "error": str(exc), "status": "failed"}
        if job_id:
            done_payload["job_id"] = job_id
            done_payload["job"] = safe_serialized_job()
        yield sse_payload("done", done_payload)
    finally:
        if active_sse_reader[0] is not None:
            active_sse_reader[0].close()
        if job_log_writer is not None:
            job_log_writer.close()
        cleanup_opencode_task(cancel_job_id)


def run_plan_generation_job(job_id, full_prompt, target_file, default_agent=None):
    try:
        update_generation_job(job_id, status="running")
        prepare_bound_setup(
            job_id,
            build_setup_targets(),
            emit_log=lambda message: append_generation_log(
                job_id,
                message,
            ),
        )
        append_generation_log(job_id, "已提交到 OpenCode，正在生成测试计划，通常耗时几分钟。")
        response = send_opencode_prompt(full_prompt, default_agent=default_agent)
        summary = summarize_opencode_response(response)
        if summary:
            append_generation_log(job_id, summary)

        if not target_file.exists():
            raise RuntimeError(f"OpenCode 已返回，但未生成目标文件：{target_file}")

        update_generation_job(job_id, status="succeeded")
        append_generation_log(job_id, f"任务成功，文件已生成：{target_file}")
    except Exception as exc:
        update_generation_job(job_id, status="failed", error=str(exc))
        append_generation_log(job_id, f"任务失败：{exc}")


def get_script_file(module_name, filename):
    module_name = validate_module_name(module_name)
    filename = validate_script_filename(filename)
    return artifact_paths.build_script_file(
        get_tests_dir(),
        module_name,
        filename,
    )


def _auth_repository_dependencies():
    return auth_repository.AuthRepositoryDependencies(
        get_auth_config=lambda: get_auth_config(),
        get_platform_database_config=lambda: (
            get_platform_database_config()
        ),
        ensure_platform_database_schema=lambda config: (
            ensure_platform_database_schema(config)
        ),
        platform_table_sql=lambda config, table_name: (
            platform_table_sql(config, table_name)
        ),
        platform_mysql_connection=lambda config: (
            platform_mysql_connection(config)
        ),
        current_time_ms=lambda: current_time_ms(),
    )


def _auth_repository():
    return auth_repository.AuthRepository(
        _auth_repository_dependencies()
    )


def _auth_service_dependencies():
    return auth_service.AuthServiceDependencies(
        repository=_auth_repository(),
        get_auth_config=lambda: get_auth_config(),
        check_password_hash=lambda password_hash, password: (
            check_password_hash(password_hash, password)
        ),
        generate_password_hash=lambda password: (
            generate_password_hash(password)
        ),
    )


def _auth_service():
    return auth_service.AuthService(
        _auth_service_dependencies()
    )


def _auth_web_services():
    return AuthWebServices(
        get_auth_config=lambda: get_auth_config(),
        load_current_user=lambda _session_user_id: (
            load_current_user_from_session()
        ),
        load_user_permission_codes=lambda user_id: (
            load_user_permission_codes(user_id)
        ),
        build_auth_payload=lambda user, permission_codes=None: (
            build_auth_payload(user, permission_codes)
        ),
        authenticate=lambda username, password: (
            _auth_service().authenticate(username, password)
        ),
        list_roles=lambda: _auth_service().list_roles(),
        create_role=lambda payload: (
            _auth_service().create_role(payload)
        ),
        update_role=lambda role_id, payload: (
            _auth_service().update_role(role_id, payload)
        ),
        list_users=lambda: _auth_service().list_users(),
        create_user=lambda payload: (
            _auth_service().create_user(payload)
        ),
        update_user=lambda user_id, payload, current_user_id=None: (
            _auth_service().update_user(
                user_id,
                payload,
                current_user_id=current_user_id,
            )
        ),
        reset_user_password=lambda user_id, payload: (
            _auth_service().reset_user_password(user_id, payload)
        ),
        menu_permissions=AUTH_MENU_PERMISSIONS,
    )


def get_auth_database_config():
    return _auth_repository().get_database_config()


def normalize_auth_status(value):
    return auth_model.normalize_auth_status(value)


def validate_username(value):
    return auth_model.validate_username(value)


def validate_role_code(value):
    return auth_model.validate_role_code(value)


def normalize_display_name(value, fallback):
    return auth_model.normalize_display_name(value, fallback)


def normalize_role_name(value):
    return auth_model.normalize_role_name(value)


def normalize_description(value):
    return auth_model.normalize_description(value)


def normalize_password(value, required=True):
    return auth_model.normalize_password(
        value,
        required=required,
    )


def normalize_id_list(value):
    return auth_model.normalize_id_list(value)


def normalize_permission_codes(value):
    return auth_model.normalize_permission_codes(value)


def get_user_row_by_id(cursor, config, user_id):
    return _auth_repository().get_user_row_by_id(
        cursor,
        config,
        user_id,
    )


def get_user_row_by_username(cursor, config, username):
    return _auth_repository().get_user_row_by_username(
        cursor,
        config,
        username,
    )


def load_roles_by_user_ids(cursor, config, user_ids):
    return _auth_repository().load_roles_by_user_ids(
        cursor,
        config,
        user_ids,
    )


def load_permission_codes_by_role_ids(cursor, config, role_ids):
    return _auth_repository().load_permission_codes_by_role_ids(
        cursor,
        config,
        role_ids,
    )


def load_user_permission_codes(user_id):
    return _auth_service().load_user_permission_codes(user_id)


def current_user_is_admin():
    user = getattr(g, "current_user", None)
    return bool(user and _auth_service().is_admin(user["id"]))


def serialize_user(row, roles=None):
    return auth_model.serialize_user(row, roles)


def serialize_role(row, permission_codes=None):
    return auth_model.serialize_role(row, permission_codes)


def build_auth_payload(user, permission_codes=None):
    return _auth_service().build_auth_payload(
        user,
        permission_codes,
    )


def load_current_user_from_session():
    user_id = session.get("user_id")
    if not user_id:
        return None

    user = _auth_service().load_current_user(user_id)
    if not user:
        session.clear()
        return None
    return user


def has_any_permission(permission_codes):
    return auth_model.has_any_permission(
        getattr(g, "current_permissions", set()),
        permission_codes,
    )


def required_permissions_for_endpoint(endpoint, method):
    return auth_model.required_permissions_for_endpoint(
        endpoint,
        method,
    )


def is_auth_public_endpoint(endpoint, method):
    return auth_model.is_auth_public_endpoint(endpoint, method)


def validate_existing_role_ids(cursor, config, role_ids):
    return _auth_repository().validate_existing_role_ids(
        cursor,
        config,
        role_ids,
    )


def replace_user_roles(cursor, config, user_id, role_ids):
    return _auth_repository().replace_user_roles(
        cursor,
        config,
        user_id,
        role_ids,
    )


def _test_suite_item_dependencies():
    return test_suite_service.TestSuiteItemDependencies(
        validate_module_name=lambda value: validate_module_name(value),
        validate_script_filename=(
            lambda value: validate_script_filename(value)
        ),
        get_script_file=(
            lambda module_name, filename: get_script_file(
                module_name,
                filename,
            )
        ),
        strip_spec_suffix=lambda filename: strip_spec_suffix(filename),
    )


def _test_suite_repository_dependencies():
    return test_suite_repository.TestSuiteRepositoryDependencies(
        get_platform_database_config=(
            lambda: get_platform_database_config()
        ),
        ensure_platform_database_schema=(
            lambda config: ensure_platform_database_schema(config)
        ),
        get_test_suites_table=(
            lambda config: get_test_suites_table(config)
        ),
        get_test_suite_items_table=(
            lambda config: get_test_suite_items_table(config)
        ),
        get_test_suite_tables=lambda: get_test_suite_tables(),
        get_current_project_id=lambda: get_current_project_id(),
        platform_mysql_connection=(
            lambda config: platform_mysql_connection(config)
        ),
        validate_suite_name=lambda value: validate_suite_name(value),
        validate_suite_description=(
            lambda value: validate_suite_description(value)
        ),
        serialize_test_suite_item=(
            lambda row: serialize_test_suite_item(row)
        ),
        serialize_test_suite=(
            lambda row, items=None: serialize_test_suite(row, items)
        ),
        list_test_suite_items_by_suite_ids=(
            lambda cursor, suite_items_table, project_id, suite_ids: (
                list_test_suite_items_by_suite_ids(
                    cursor,
                    suite_items_table,
                    project_id,
                    suite_ids,
                )
            )
        ),
        get_test_suite_row_by_uid=(
            lambda cursor, suites_table, project_id, suite_uid: (
                get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
            )
        ),
        ensure_test_suite_name_available=(
            lambda cursor, suites_table, project_id, name,
            excluding_suite_id=None: (
                ensure_test_suite_name_available(
                    cursor,
                    suites_table,
                    project_id,
                    name,
                    excluding_suite_id,
                )
            )
        ),
        get_test_suite_payload=(
            lambda suite_uid: get_test_suite_payload(suite_uid)
        ),
        sanitize_suite_uid=lambda: sanitize_suite_uid(),
        current_time_ms=lambda: current_time_ms(),
        current_platform_author=lambda: current_platform_author(),
        normalize_suite_item_input=(
            lambda raw_item: normalize_suite_item_input(raw_item)
        ),
        sync_script_asset=(
            lambda module_name, script_file, **kwargs: sync_script_asset(
                module_name,
                script_file,
                **kwargs,
            )
        ),
    )


def _test_suite_repository():
    return test_suite_repository.TestSuiteRepository(
        _test_suite_repository_dependencies()
    )


def _test_suite_web_services():
    return TestSuiteWebServices(
        list_test_suites=lambda: list_test_suites_from_mysql(),
        create_test_suite=(
            lambda name, description="": create_test_suite_in_mysql(
                name,
                description,
            )
        ),
        get_test_suite=lambda suite_uid: get_test_suite_payload(
            suite_uid
        ),
        update_test_suite=(
            lambda suite_uid, **changes: update_test_suite_in_mysql(
                suite_uid,
                **changes,
            )
        ),
        delete_test_suite=(
            lambda suite_uid: delete_test_suite_in_mysql(suite_uid)
        ),
        add_test_suite_items=(
            lambda suite_uid, items: add_test_suite_items_in_mysql(
                suite_uid,
                items,
            )
        ),
        delete_test_suite_item=(
            lambda suite_uid, item_id: (
                delete_test_suite_item_in_mysql(
                    suite_uid,
                    item_id,
                )
            )
        ),
        reorder_test_suite_items=(
            lambda suite_uid, item_ids: (
                reorder_test_suite_items_in_mysql(
                    suite_uid,
                    item_ids,
                )
            )
        ),
    )


def validate_suite_name(value):
    return test_suite_model.validate_suite_name(value)


def validate_suite_description(value):
    return test_suite_model.validate_suite_description(value)


def get_test_suite_tables():
    return _test_suite_repository().get_tables()


def serialize_test_suite_item(row):
    return test_suite_model.serialize_test_suite_item(
        row,
        strip_spec_suffix=lambda filename: strip_spec_suffix(filename),
    )


def serialize_test_suite(row, items=None):
    return test_suite_model.serialize_test_suite(row, items)


def list_test_suite_items_by_suite_ids(
    cursor,
    suite_items_table,
    project_id,
    suite_ids,
):
    return _test_suite_repository().list_items_by_suite_ids(
        cursor,
        suite_items_table,
        project_id,
        suite_ids,
    )


def list_test_suites_from_mysql():
    return _test_suite_repository().list()


def get_test_suite_row_by_uid(
    cursor,
    suites_table,
    project_id,
    suite_uid,
):
    return test_suite_repository.TestSuiteRepository.get_row_by_uid(
        cursor,
        suites_table,
        project_id,
        suite_uid,
    )


def get_test_suite_payload(suite_uid):
    return _test_suite_repository().get(suite_uid)


def ensure_test_suite_name_available(
    cursor,
    suites_table,
    project_id,
    name,
    excluding_suite_id=None,
):
    return (
        test_suite_repository.TestSuiteRepository.ensure_name_available(
            cursor,
            suites_table,
            project_id,
            name,
            excluding_suite_id,
        )
    )


def create_test_suite_in_mysql(name, description=""):
    return _test_suite_repository().create(name, description)


def update_test_suite_in_mysql(
    suite_uid,
    name=None,
    description=None,
):
    return _test_suite_repository().update(
        suite_uid,
        name=name,
        description=description,
    )


def delete_test_suite_in_mysql(suite_uid):
    return _test_suite_repository().delete(suite_uid)


def normalize_suite_item_input(raw_item):
    return test_suite_service.normalize_suite_item_input(
        raw_item,
        _test_suite_item_dependencies(),
    )


def add_test_suite_items_in_mysql(suite_uid, raw_items):
    return _test_suite_repository().add_items(suite_uid, raw_items)


def delete_test_suite_item_in_mysql(suite_uid, item_id):
    return _test_suite_repository().delete_item(suite_uid, item_id)


def reorder_test_suite_items_in_mysql(suite_uid, item_ids):
    return _test_suite_repository().reorder_items(
        suite_uid,
        item_ids,
    )


def list_test_suites():
    return list_test_suites_response(_test_suite_web_services())


def create_test_suite():
    return create_test_suite_response(_test_suite_web_services())


def get_test_suite(suite_uid):
    return get_test_suite_response(
        _test_suite_web_services(),
        suite_uid,
    )


def update_test_suite(suite_uid):
    return update_test_suite_response(
        _test_suite_web_services(),
        suite_uid,
    )


def delete_test_suite(suite_uid):
    return delete_test_suite_response(
        _test_suite_web_services(),
        suite_uid,
    )


def add_test_suite_items(suite_uid):
    return add_test_suite_items_response(
        _test_suite_web_services(),
        suite_uid,
    )


def delete_test_suite_item(suite_uid, item_id):
    return delete_test_suite_item_response(
        _test_suite_web_services(),
        suite_uid,
        item_id,
    )


def reorder_test_suite_items(suite_uid):
    return reorder_test_suite_items_response(
        _test_suite_web_services(),
        suite_uid,
    )


def _project_archive_service_dependencies():
    return project_archive_service.ProjectArchiveServiceDependencies(
        validation_dependencies=_project_archive_validation_dependencies(),
        get_current_project=lambda: get_current_project(),
        get_current_project_id=lambda: get_current_project_id(),
        get_project_root=lambda: get_project_root(),
        get_specs_dir=lambda: get_specs_dir(),
        get_tests_dir=lambda: get_tests_dir(),
        get_plan_file=lambda *args, **kwargs: get_plan_file(*args, **kwargs),
        get_script_file=lambda *args, **kwargs: get_script_file(*args, **kwargs),
        get_project_relative_path=lambda value: get_project_relative_path(value),
        project_relative_path=lambda value: project_relative_path(value),
        get_platform_database_config=lambda: get_platform_database_config(),
        ensure_platform_database_schema=lambda config: ensure_platform_database_schema(config),
        get_test_assets_table=lambda config: get_test_assets_table(config),
        get_test_asset_revisions_table=lambda config: get_test_asset_revisions_table(config),
        get_platform_projects_table=lambda config: get_platform_projects_table(config),
        platform_table_sql=lambda *args, **kwargs: platform_table_sql(*args, **kwargs),
        platform_mysql_connection=lambda config: platform_mysql_connection(config),
        list_test_suites=lambda: list_test_suites_from_mysql(),
        strip_spec_suffix=lambda value: strip_spec_suffix(value),
        current_time_ms=lambda: current_time_ms(),
        current_platform_author=lambda: current_platform_author(),
        get_test_suite_tables=lambda: get_test_suite_tables(),
        ensure_playwright_asset_git_repo=lambda: ensure_playwright_asset_git_repo(),
        run_git_command=lambda *args, **kwargs: run_git_command(*args, **kwargs),
        sync_plan_asset=lambda *args, **kwargs: sync_plan_asset(*args, **kwargs),
        sync_script_asset=lambda *args, **kwargs: sync_script_asset(*args, **kwargs),
        create_project=lambda payload: create_project_in_mysql(payload),
        use_project_context=lambda project: use_project_context(project),
        remove_tree=lambda *args, **kwargs: shutil.rmtree(*args, **kwargs),
    )


def _project_archive_service():
    return project_archive_service.ProjectArchiveService(
        _project_archive_service_dependencies()
    )


def _project_archive_web_services():
    return ProjectArchiveWebServices(
        build_project_export_zip=lambda: build_project_export_zip(),
        import_project_archive=lambda archive_bytes, overrides: import_project_archive(
            archive_bytes,
            overrides,
        ),
        current_export_timestamp=lambda: time.strftime("%Y%m%d-%H%M%S"),
        import_max_bytes=PROJECT_IMPORT_MAX_BYTES,
    )


def collect_project_export_files(base_dir, suffix, zip_root):
    return _project_archive_service().collect_project_export_files(
        base_dir,
        suffix,
        zip_root,
    )


def list_project_export_asset_rows():
    return _project_archive_service().list_project_export_asset_rows()


def list_project_export_suites():
    return _project_archive_service().list_project_export_suites()


def build_project_export_payload():
    return _project_archive_service().build_project_export_payload()


def build_project_export_zip():
    return _project_archive_service().build_project_export_zip()


def _project_archive_validation_dependencies():
    return project_archive.ArchiveValidationDependencies(
        validate_module_name=lambda value: validate_module_name(value),
        validate_plan_filename=lambda value: validate_plan_filename(value),
        validate_script_filename=lambda value: validate_script_filename(value),
        parse_project_key=lambda value, field_name: parse_project_key(
            value,
            field_name,
        ),
        parse_project_path_segment=lambda value, fallback, field_name: (
            parse_project_path_segment(value, fallback, field_name)
        ),
        validate_suite_name=lambda value: validate_suite_name(value),
        validate_suite_description=lambda value: (
            validate_suite_description(value)
        ),
        strip_spec_suffix=lambda value: strip_spec_suffix(value),
    )


def normalize_project_import_member_name(raw_name, is_dir=False):
    return project_archive.normalize_project_import_member_name(
        raw_name,
        is_dir=is_dir,
    )


def validate_project_import_member_name(raw_name, is_dir=False):
    return project_archive.validate_project_import_member_name(
        raw_name,
        _project_archive_validation_dependencies(),
        is_dir=is_dir,
    )


def parse_project_import_asset_path(path, asset_type):
    return project_archive.parse_project_import_asset_path(
        path,
        asset_type,
        _project_archive_validation_dependencies(),
    )


def validate_project_import_project(raw_project):
    return project_archive.validate_project_import_project(
        raw_project,
        _project_archive_validation_dependencies(),
    )


def validate_project_import_manifest(raw_manifest, file_names):
    return project_archive.validate_project_import_manifest(
        raw_manifest,
        file_names,
        _project_archive_validation_dependencies(),
    )


def parse_project_import_archive(archive_bytes):
    return project_archive.parse_project_import_archive(
        archive_bytes,
        _project_archive_validation_dependencies(),
    )


def clear_project_import_asset_directory(directory):
    return _project_archive_service().clear_project_import_asset_directory(
        directory
    )


def write_project_import_files(manifest, archive_bytes):
    return _project_archive_service().write_project_import_files(
        manifest,
        archive_bytes,
    )


def commit_project_import_file_tree():
    return _project_archive_service().commit_project_import_file_tree()


def sync_project_import_assets(manifest):
    return _project_archive_service().sync_project_import_assets(manifest)


def import_project_test_suites(manifest, script_assets):
    return _project_archive_service().import_project_test_suites(
        manifest,
        script_assets,
    )


def cleanup_imported_project(project):
    return _project_archive_service().cleanup_imported_project(project)


def import_project_archive(archive_bytes, overrides=None):
    return _project_archive_service().import_project_archive(
        archive_bytes,
        overrides,
    )
def render_markdown(content):
    return markdown_security.render_markdown(content)


def agent_run_response(run_id, include_events=False):
    run = get_agent_run_row(run_id)
    if not run:
        return None
    retry_flow_rows = list_agent_item_retry_flows(run_id=run_id)
    retry_flows = [serialize_agent_item_retry_flow(row) for row in retry_flow_rows]
    steps = [serialize_agent_step(row) for row in list_agent_steps(run_id)]
    payload = {
        "run": serialize_agent_run(run),
        "steps": steps,
        "retry_flows": retry_flows,
        "active_retry_flows": [
            flow for flow in retry_flows if flow.get("status") in AGENT_ITEM_RETRY_ACTIVE_STATUSES
        ],
    }
    if include_events:
        payload["events"] = [serialize_agent_event(row) for row in list_agent_events(run_id, 0, 200, tail=True)]
    return payload


@app.get("/api/agent/runs")
def list_agent_runs():
    try:
        runs = [serialize_agent_run(row) for row in list_agent_run_rows()]
        active_by_run = {}
        for row in list_agent_item_retry_flows(active_only=True, limit=1000):
            flow = serialize_agent_item_retry_flow(row)
            active_by_run.setdefault(flow.get("run_id"), []).append(flow)
        for run in runs:
            active_retry_flows = active_by_run.get(run.get("run_id"), [])
            run["active_retry_flows"] = active_retry_flows
            run["active_retry_flow_count"] = len(active_retry_flows)
        return jsonify({"runs": runs, "error": None})
    except Exception as exc:
        return jsonify({"runs": [], "error": f"读取 Agent 任务失败：{exc}"}), 500


@app.post("/api/agent/runs")
def create_agent_run_api():
    try:
        active = get_active_agent_run_row()
        if active:
            return jsonify({"error": "当前项目已有 Agent 任务正在运行。", "run": serialize_agent_run(active)}), 409
        active_retry_flows = list_agent_item_retry_flows(active_only=True, limit=1)
        if active_retry_flows:
            return jsonify(
                {
                    "error": "当前项目有脚本正在重试并验证，请等待完成或先取消。",
                    "retry_flow": serialize_agent_item_retry_flow(active_retry_flows[0]),
                }
            ), 409

        requirement = None
        request_values = request.form if request.files.get("file") else (request.get_json(silent=True) or request.form or {})
        plan_generation = normalize_plan_generation_request(request_values)
        if request.files.get("file"):
            requirement = create_requirement_from_upload(request.files.get("file"), title=request.form.get("title"))
        else:
            payload = request_values
            requirement_uid = str(payload.get("requirement_uid") or "").strip()
            if not requirement_uid:
                return jsonify({"error": "请上传需求 Markdown，或提供 requirement_uid。"}), 400
            requirement = get_requirement_by_uid(requirement_uid)
            if not requirement:
                return jsonify({"error": "需求不存在。"}), 404

        author = current_platform_author()
        run = create_agent_run(requirement, author, plan_generation=plan_generation)
        project = get_current_project()
        start_agent_thread(run["run_id"], project, author)
        return jsonify({**agent_run_response(run["run_id"], include_events=True), "error": None}), 202
    except AgentItemRetryConflict as exc:
        return jsonify({"error": str(exc), "retry_flow": serialize_agent_item_retry_flow(exc.flow)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"创建 Agent 任务失败：{exc}"}), 500


@app.get("/api/agent/runs/<run_id>")
def get_agent_run_api(run_id):
    try:
        payload = agent_run_response(run_id, include_events=False)
        if not payload:
            return jsonify({"error": "Agent 任务不存在。"}), 404
        return jsonify({**payload, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"读取 Agent 任务失败：{exc}"}), 500


app.register_blueprint(
    create_agent_script_preparation_blueprint(
        AgentScriptPreparationWebServices(
            get_script_preparation_snapshot=(
                agent_script_preparation.get_script_preparation_snapshot
            ),
            get_script_preparation_item=(
                get_agent_script_preparation_item_for_web
            ),
            apply_script_preparation_action=(
                agent_script_preparation.apply_script_preparation_action
            ),
            apply_script_preparation_batch_action=(
                agent_script_preparation.apply_script_preparation_batch_action
            ),
            start_script_preparation_continue=lambda run_id: (
                start_agent_script_preparation_continue_thread(
                    run_id,
                    get_current_project(),
                    current_platform_author(),
                )
            ),
            claim_script_preparation_continue=(
                claim_agent_script_preparation_continue
            ),
            conflict_type=agent_script_preparation.ScriptPreparationConflict,
        )
    )
)


@app.get("/api/agent/runs/<run_id>/attempts/<attempt_id>/diagnostic-bundle")
def download_agent_attempt_diagnostic_bundle(run_id, attempt_id):
    try:
        buffer, filename = build_agent_attempt_diagnostic_bundle(run_id, attempt_id)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"生成诊断包失败：{exc}"}), 500
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@app.post("/api/agent/runs/<run_id>/legacy-diagnostic-bundle")
def download_legacy_agent_failure_diagnostic_bundle(run_id):
    try:
        selector = request.get_json(silent=True) or {}
        attempt_id = create_legacy_agent_failure_attempt(run_id, selector)
        buffer, filename = build_agent_attempt_diagnostic_bundle(run_id, attempt_id)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"生成历史失败诊断包失败：{exc}"}), 500
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@app.get("/api/agent/runs/<run_id>/attempts")
def get_agent_run_attempts_api(run_id):
    try:
        if not get_agent_run_row(run_id):
            return jsonify({"error": "Agent 任务不存在。"}), 404
        step_key = str(request.args.get("step_key") or "").strip() or None
        attempts = [serialize_agent_attempt(row) for row in list_agent_attempts(run_id, step_key=step_key)]
        return jsonify({"attempts": attempts, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"读取 Agent 项目记录失败：{exc}"}), 500


@app.post("/api/agent/runs/<run_id>/legacy-failure-attempt")
def create_legacy_agent_failure_attempt_api(run_id):
    try:
        if not get_agent_run_row(run_id):
            return jsonify({"error": "Agent 任务不存在。"}), 404
        selector = request.get_json(silent=True) or {}
        attempt_id = create_legacy_agent_failure_attempt(run_id, selector)
        attempt = get_agent_attempt(run_id, attempt_id)
        return jsonify({"attempt": serialize_agent_attempt(attempt), "error": None})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"创建历史 Agent 失败记录失败：{exc}"}), 500


@app.post("/api/agent/runs/<run_id>/attempts/<attempt_id>/retry")
def retry_agent_generation_attempt_api(run_id, attempt_id):
    payload = request.get_json(silent=True) or {}
    try:
        run = get_agent_run_row(run_id)
        if not run:
            return jsonify({"error": "Agent 任务不存在。"}), 404
        if run.get("status") in AGENT_ACTIVE_STATUSES:
            return jsonify({"error": "Agent 主任务正在运行，不能同时执行单项重试。", "run": serialize_agent_run(run)}), 409
        active_run = get_active_agent_run_row()
        if active_run:
            return jsonify(
                {
                    "error": "当前项目有 Agent 主任务正在运行，不能同时执行单项重试。",
                    "run": serialize_agent_run(active_run),
                }
            ), 409

        attempt = get_agent_attempt(run_id, attempt_id)
        if not attempt:
            return jsonify({"error": "Agent 失败记录不存在。"}), 404
        if attempt.get("step_key") != "generate_scripts" or attempt.get("status") != "failed":
            return jsonify({"error": "只有脚本生成阶段的失败记录可以重试并验证。"}), 400
        if not is_current_agent_generation_failure(run_id, attempt):
            return jsonify({"error": "该失败记录已被后续结果替代，请刷新页面后选择当前失败项。"}), 409

        auto_repair_value = payload.get("auto_repair", True)
        if isinstance(auto_repair_value, str):
            auto_repair = auto_repair_value.strip().lower() not in {"0", "false", "no", "off"}
        else:
            auto_repair = bool(auto_repair_value)
        flow_row, created = create_agent_item_retry_flow(
            run_id,
            attempt,
            auto_repair=auto_repair,
            created_by=current_platform_author(),
        )
        if created:
            try:
                merge_agent_retry_step_result(run_id, "generate_scripts", flow_row, "retrying")
                append_agent_item_retry_event(run_id, flow_row, "单项重试已进入队列。")
                start_agent_item_retry_thread(
                    run_id,
                    flow_row["retry_flow_id"],
                    get_current_project(),
                    current_platform_author(),
                )
            except Exception as exc:
                clear_agent_retry_step_markers(run_id, flow_row, ["generate_scripts"])
                flow_row = terminalize_agent_item_retry_flow(
                    run_id,
                    flow_row["retry_flow_id"],
                    "failed",
                    expected_statuses={"queued"},
                    current_phase="queued",
                    progress_message="单项重试启动失败。",
                    result={"root_attempt_id": attempt_id, "startup_error": str(exc)},
                    error=str(exc),
                    event_message=f"单项重试启动失败：{exc}",
                    event_type="error",
                    flow=flow_row,
                )
                raise
        response = {
            "retry_flow": serialize_agent_item_retry_flow(flow_row),
            "idempotent": not created,
            "error": None,
        }
        return jsonify(response), 202 if created else 200
    except AgentItemRetryConflict as exc:
        return jsonify({"error": str(exc), "retry_flow": serialize_agent_item_retry_flow(exc.flow)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"启动单项重试失败：{exc}"}), 500


@app.get("/api/agent/runs/<run_id>/retry-flows")
def get_agent_item_retry_flows_api(run_id):
    try:
        if not get_agent_run_row(run_id):
            return jsonify({"error": "Agent 任务不存在。"}), 404
        retry_flows = [serialize_agent_item_retry_flow(row) for row in list_agent_item_retry_flows(run_id=run_id)]
        return jsonify(
            {
                "retry_flows": retry_flows,
                "active_retry_flows": [
                    flow for flow in retry_flows if flow.get("status") in AGENT_ITEM_RETRY_ACTIVE_STATUSES
                ],
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"读取单项重试记录失败：{exc}"}), 500


@app.get("/api/agent/retry-flows")
def get_project_agent_item_retry_flows_api():
    try:
        active_only = str(request.args.get("active") or "").strip().lower() in {"1", "true", "yes"}
        retry_flows = [
            serialize_agent_item_retry_flow(row)
            for row in list_agent_item_retry_flows(active_only=active_only)
        ]
        return jsonify({"retry_flows": retry_flows, "error": None})
    except Exception as exc:
        return jsonify({"error": f"读取项目单项重试记录失败：{exc}"}), 500


@app.post("/api/agent/runs/<run_id>/retry-flows/<retry_flow_id>/cancel")
def cancel_agent_item_retry_flow_api(run_id, retry_flow_id):
    try:
        flow = get_agent_item_retry_flow(run_id, retry_flow_id)
        if not flow:
            return jsonify({"error": "单项重试记录不存在。"}), 404
        if flow.get("status") not in AGENT_ITEM_RETRY_ACTIVE_STATUSES:
            return jsonify(
                {
                    "retry_flow": serialize_agent_item_retry_flow(flow),
                    "cancelled": False,
                    "error": None,
                }
            )
        flow, result = request_agent_item_retry_cancel(run_id, retry_flow_id)
        accepted = bool(result.get("cancel_requested"))
        if accepted:
            append_agent_item_retry_event(run_id, flow, "用户请求取消单项重试。")
        return jsonify(
            {
                "retry_flow": serialize_agent_item_retry_flow(flow),
                "cancelled": accepted,
                **result,
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"取消单项重试失败：{exc}"}), 500


@app.post("/api/agent/runs/<run_id>/retry-flows/<retry_flow_id>/acknowledge")
def acknowledge_agent_item_retry_flow_api(run_id, retry_flow_id):
    try:
        flow = get_agent_item_retry_flow(run_id, retry_flow_id)
        if not flow:
            return jsonify({"error": "单项重试记录不存在。"}), 404
        flow = update_agent_item_retry_flow(run_id, retry_flow_id, acknowledged_at=current_time_ms())
        return jsonify({"retry_flow": serialize_agent_item_retry_flow(flow), "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"确认单项重试结果失败：{exc}"}), 500


@app.get("/api/agent/runs/<run_id>/events")
def get_agent_run_events_api(run_id):
    try:
        after_id = int(request.args.get("after_id") or 0)
        limit = min(max(int(request.args.get("limit") or 500), 1), 1000)
        tail = str(request.args.get("tail") or "").strip().lower() in {"1", "true", "yes"}
        if not get_agent_run_row(run_id):
            return jsonify({"error": "Agent 任务不存在。"}), 404
        events = [serialize_agent_event(row) for row in list_agent_events(run_id, after_id, limit, tail=tail)]
        return jsonify({"events": events, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"读取 Agent 事件失败：{exc}"}), 500


@app.get("/api/agent/runs/<run_id>/events-stream")
def stream_agent_run_events_api(run_id):
    try:
        if not get_agent_run_row(run_id):
            return jsonify({"error": "Agent 任务不存在。"}), 404
        after_id = int(request.args.get("after_id") or 0)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"读取 Agent 任务失败：{exc}"}), 500

    def generate():
        last_id = after_id
        page_size = 200
        terminal_observation = None
        while True:
            rows, run, retry_flow_rows = read_agent_event_stream_page(
                run_id,
                last_id,
                page_size,
            )
            for row in rows:
                event = serialize_agent_event(row)
                last_id = max(last_id, int(event["event_id"] or 0))
                yield sse_payload("agent-event", event)
            if rows:
                terminal_observation = None

            # A full page means there may already be more persisted events.
            # Drain the backlog before polling run state or sleeping; otherwise
            # a terminal run can emit ``done`` after only its first 200 rows.
            if len(rows) == page_size:
                continue

            status = run.get("status") if run else "failed"
            active_retry_flows = [
                serialize_agent_item_retry_flow(row)
                for row in (retry_flow_rows or [])
            ]
            heartbeat = {
                "run_id": run_id,
                "status": status,
                "last_event_id": last_id,
                "active_retry_count": len(active_retry_flows),
                "active_retry_flows": active_retry_flows,
            }
            yield sse_payload("heartbeat", heartbeat)
            terminal_event = None
            if not active_retry_flows:
                if status in AGENT_PAUSED_STATUSES:
                    terminal_event = "paused"
                elif status in AGENT_TERMINAL_STATUSES:
                    terminal_event = "done"
            if terminal_event:
                observation = (terminal_event, status, last_id)
                if terminal_observation != observation:
                    terminal_observation = observation
                    # Confirm a short quiet window so a terminal run update and
                    # its final event cannot be observed in opposite transactions.
                    time.sleep(0.05)
                    continue
                yield sse_payload(terminal_event, heartbeat)
                break
            terminal_observation = None
            time.sleep(1)

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/agent/runs/<run_id>/cancel")
def cancel_agent_run_api(run_id):
    try:
        run = get_agent_run_row(run_id)
        if not run:
            return jsonify({"error": "Agent 任务不存在。"}), 404
        if run.get("status") in AGENT_TERMINAL_STATUSES:
            return jsonify({"run": serialize_agent_run(run), "cancelled": False, "error": None})
        if run.get("status") == "awaiting_script_action":
            update_agent_step(
                run_id,
                "prepare_scripts",
                status="cancelled",
                error="用户请求取消。",
                finished=True,
            )
            append_agent_event(
                run_id,
                "prepare_scripts",
                "status",
                "用户已取消脚本准备。",
                {"cancelled": True},
            )
            update_agent_run(run_id, status="cancelled", error="用户请求取消。", finished=True)
            return jsonify(
                {
                    "run": serialize_agent_run(get_agent_run_row(run_id)),
                    "cancelled": True,
                    "error": None,
                }
            )
        update_agent_run(run_id, status="cancelling", error="用户请求取消。")
        result = agent_request_cancel(run_id)
        append_agent_event(run_id, run.get("current_step") or "", "status", "用户请求取消 Agent 任务。", result)
        return jsonify({"run": serialize_agent_run(get_agent_run_row(run_id)), "cancelled": True, **result, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"取消 Agent 任务失败：{exc}"}), 500


@app.post("/api/agent/runs/<run_id>/resume")
def resume_agent_run_api(run_id):
    payload = request.get_json(silent=True) or {}
    try:
        run = get_agent_run_row(run_id)
        if not run:
            return jsonify({"error": "Agent 任务不存在。"}), 404
        if run.get("status") in AGENT_ACTIVE_STATUSES:
            return jsonify({"error": "该 Agent 任务正在运行，不能重复恢复。", "run": serialize_agent_run(run)}), 409
        if run.get("status") not in {"failed", "cancelled"}:
            return jsonify({"error": "只有失败或已取消的 Agent 任务可以恢复。", "run": serialize_agent_run(run)}), 400
        active_retry_flows = list_agent_item_retry_flows(active_only=True, limit=1)
        if active_retry_flows:
            return jsonify(
                {
                    "error": "当前项目有脚本正在重试并验证，请等待完成或先取消。",
                    "retry_flow": serialize_agent_item_retry_flow(active_retry_flows[0]),
                }
            ), 409

        active = get_active_agent_run_row()
        if active and active.get("run_id") != run.get("run_id"):
            return jsonify({"error": "当前项目已有 Agent 任务正在运行。", "run": serialize_agent_run(active)}), 409

        requested_step = str(payload.get("from_step") or "").strip()
        if not requested_step:
            requested_step = run.get("current_step") or ""
        if not requested_step:
            failed_step = next((serialize_agent_step(row) for row in list_agent_steps(run_id) if row.get("status") == "failed"), None)
            requested_step = (failed_step or {}).get("step_key") or "upload_requirement"
        requested_step = validate_agent_step_key(requested_step)
        from_step = resolve_agent_resume_step(run_id, requested_step)
        plan_resume_output = get_agent_plan_resume_output(run_id) if from_step == "generate_plans" else None
        resume_context = {"generate_plans": plan_resume_output} if plan_resume_output else {}

        reset_agent_run_for_resume(run_id, from_step)
        project = get_current_project()
        author = current_platform_author()
        start_agent_resume_thread(run_id, project, author, from_step, resume_context=resume_context)
        return jsonify(
            {
                **agent_run_response(run_id, include_events=True),
                "resumed": True,
                "requested_from_step": requested_step,
                "from_step": from_step,
                "error": None,
            }
        ), 202
    except AgentItemRetryConflict as exc:
        return jsonify({"error": str(exc), "retry_flow": serialize_agent_item_retry_flow(exc.flow)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"恢复 Agent 任务失败：{exc}"}), 500


def list_projects():
    return list_projects_response(_project_web_services())


def export_project():
    return export_project_response(_project_archive_web_services())


def create_project():
    return create_project_response(_project_web_services())


def import_project():
    return import_project_response(_project_archive_web_services())


def get_project_settings():
    return get_project_settings_response(_project_web_services())


def save_project_settings():
    return save_project_settings_response(_project_web_services())


@app.post("/api/project-settings/seed/generate")
def generate_project_seed():
    try:
        target_system = get_current_target_system_config()
        target_file = get_seed_script_file()
        original_hash = file_hash(target_file)
        original_mtime = target_file.stat().st_mtime if target_file.exists() else 0
        full_prompt = build_seed_generation_prompt(target_system, target_file)
        job_id = f"generator-{uuid.uuid4().hex}"
        try:
            create_test_job(
                "generator",
                job_id=job_id,
                status="queued",
                prompt=full_prompt,
            )
        except Exception as exc:
            return jsonify({"error": f"创建 Seed 生成任务失败：{exc}"}), 500

        def has_seed_output():
            if not target_file.exists() or not target_file.is_file() or target_file.stat().st_size <= 0:
                return False
            if target_file.stat().st_mtime > original_mtime + 0.001:
                return True
            current_hash = file_hash(target_file)
            return bool(current_hash and current_hash != original_hash)

        def finalize_seed_payload():
            content = target_file.read_text(encoding="utf-8")
            validate_generated_script_content(content, target_file.name)
            script_asset = sync_script_asset(
                SEED_MODULE_NAME,
                target_file,
                change_source="generator",
                source_job_id=job_id,
                from_plan_asset_id=None,
                message=f"generator: {SEED_MODULE_NAME}/{target_file.name}",
            )
            return {
                "module_name": SEED_MODULE_NAME,
                "filename": SEED_SCRIPT_FILENAME,
                "target_path": str(target_file),
                "seed_script_path": get_seed_script_relative_path(),
                "asset": serialize_asset(script_asset),
                "revisions": (
                    [serialize_revision(item) for item in list_asset_revisions(script_asset["asset_id"], 10)]
                    if script_asset
                    else []
                ),
            }

        response = Response(
            stream_with_context(
                stream_plan_generation(
                    SEED_MODULE_NAME,
                    full_prompt,
                    target_file,
                    completion_check=has_seed_output,
                    target_label=str(target_file),
                    session_title=agent_message("seed_generation_title"),
                    success_message=agent_message("seed_generation_success", target=target_file),
                    default_agent="playwright-test-generator",
                    setup_targets=build_setup_targets(
                        module_name=SEED_MODULE_NAME,
                        filename=SEED_SCRIPT_FILENAME,
                    ),
                    success_payload_factory=finalize_seed_payload,
                    job_id=job_id,
                )
            ),
            mimetype="text/event-stream",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"生成 Seed 脚本失败：{exc}"}), 500


@app.post("/api/project-settings/seed/test")
def test_project_seed():
    try:
        setup_targets = build_setup_targets(module_name=SEED_MODULE_NAME, filename=SEED_SCRIPT_FILENAME)
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_seed_execution_context()
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright Seed 测试配置失败：{exc}"}), 500

    started_at = time.time()
    result = {
        "status": "running",
        "seed_script_path": get_seed_script_relative_path(),
        "target_path": str(context["script_file"]),
        "command": context["command_text"],
        "returncode": None,
        "output": "",
        "error": None,
        "setup": None,
    }

    setup_logs = []

    def redact_seed_output(stdout, stderr):
        output = redact_sensitive_text(
            summarize_process_output(stdout, stderr, limit=12000),
            get_current_target_system_config(),
            limit=8000,
        )
        if context.get("setup_resolution"):
            output = redact_setup_text(output, context["setup_resolution"].get("script"), limit=8000)
        return output

    try:
        try:
            if context.get("setup_resolution"):
                result["setup"] = execute_setup_profile(
                    context["setup_resolution"],
                    parent_run_id=context.get("run_id"),
                    emit_log=setup_logs.append,
                )
            completed = subprocess.run(
                context["command"],
                cwd=context["project_root"],
                env=get_playwright_execution_env(),
                capture_output=True,
                timeout=get_script_execution_timeout_seconds(),
            )
            output = redact_seed_output(completed.stdout, completed.stderr)
            if setup_logs:
                output = "\n".join([*setup_logs, output]).strip()
            result.update(
                {
                    "status": "succeeded" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "output": output,
                }
            )
            if completed.returncode != 0:
                result["error"] = f"Seed 测试失败，退出码：{completed.returncode}"
        except FileNotFoundError:
            result.update({"status": "failed", "error": "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。"})
        except subprocess.TimeoutExpired as exc:
            result.update(
                {
                    "status": "failed",
                    "output": redact_seed_output(exc.stdout, exc.stderr),
                    "error": "Seed 测试超时，已停止等待结果。",
                }
            )
        except OSError as exc:
            result.update({"status": "failed", "error": f"Seed 测试失败：{exc}"})
        except SetupPreparationError as exc:
            result.update(
                {
                    "status": "failed",
                    "output": "\n".join(setup_logs),
                    "error": str(exc),
                    "setup": exc.summary,
                }
            )
        except Exception as exc:
            result.update(
                {
                    "status": "failed",
                    "output": "\n".join(setup_logs),
                    "error": f"Seed 测试前准备脚本执行失败：{exc}",
                }
            )
    finally:
        try:
            context["video_config"].unlink(missing_ok=True)
        except OSError:
            pass

    result.update(build_run_video_result(started_at, context["results_dir"]))
    result.update(build_playwright_report_result(started_at, context["report_dir"]))
    return jsonify(result)


@app.post("/api/project-settings/database/test-connection")
def test_project_database_connection():
    try:
        config = get_database_baseline_config()
        messages = test_database_baseline_connection(config)
        return jsonify(
            {
                "ok": True,
                "messages": redact_database_messages(messages, config),
                "error": None,
            }
        )
    except Exception as exc:
        try:
            config = get_database_baseline_config()
        except Exception:
            config = {}
        return jsonify({"ok": False, "messages": [], "error": redact_sensitive_text(str(exc), config)}), 400


@app.post("/api/project-settings/database/test-restore")
def test_project_database_restore():
    try:
        config = get_database_baseline_config()
        messages = prepare_database_baseline_for_test()
        return jsonify(
            {
                "ok": True,
                "messages": redact_database_messages(messages, config),
                "error": None,
            }
        )
    except Exception as exc:
        try:
            config = get_database_baseline_config()
        except Exception:
            config = {}
        return jsonify({"ok": False, "messages": [], "error": redact_sensitive_text(str(exc), config)}), 400


@app.post("/api/requirements/<requirement_uid>/analysis-stream")
def analyze_requirement_stream(requirement_uid):
    payload = request.get_json(silent=True) or {}
    try:
        requirement = get_requirement_by_uid(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        job_id = sanitize_job_id(
            str(payload.get("job_id") or f"requirement-analysis-{uuid.uuid4().hex}").strip()
        )
        response = Response(
            stream_with_context(stream_requirement_analysis(requirement, job_id=job_id)),
            mimetype="text/event-stream",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"启动需求解析失败：{exc}"}), 500


@app.post("/api/requirements/<requirement_uid>/modules/<module_uid>/generate-plan-stream")
def generate_requirement_module_plan_stream(requirement_uid, module_uid):
    payload = request.get_json(silent=True) or {}
    try:
        requirement = get_requirement_by_uid(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        module_row = get_requirement_module(requirement["id"], module_uid)
        if not module_row:
            return jsonify({"error": "候选模块不存在。"}), 404
        module_payload = serialize_requirement_module(module_row)
        module_name = str(payload.get("module_name") or module_payload["module_name"]).strip()
        plan_name = str(payload.get("plan_name") or module_payload["plan_name"] or module_name).strip()
        prompt = str(payload.get("prompt") or module_payload["planner_prompt"]).strip()
        plan_generation = normalize_plan_generation_request(payload)
        generation_mode = str(payload.get("generation_mode") or "single").strip()
        if generation_mode not in {"single", "multiple"}:
            return jsonify({"error": "generation_mode must be 'single' or 'multiple'."}), 400
        if not prompt:
            return jsonify({"error": "候选模块缺少 planner prompt。"}), 400
        validate_module_name(module_name)
        plan_filename = get_plan_filename_from_name(plan_name, module_name) if get_current_project_language() == "en" else get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=module_name)
        target_file = get_plan_target_path(module_name, plan_filename)
        if target_file.exists():
            return jsonify({"error": f"测试计划已存在：{target_file}"}), 409

        full_prompt = (
            build_multiple_plan_generation_prompt(prompt, module_name, target_file)
            if generation_mode == "multiple"
            else build_generation_prompt(prompt, target_file)
        )
        job_id = sanitize_job_id(str(payload.get("job_id") or f"planner-{uuid.uuid4().hex}").strip())
        create_test_job(
            "planner",
            job_id=job_id,
            status="queued",
            prompt=full_prompt,
            coverage_profile=plan_generation["coverage_profile"],
            prompt_customized=plan_generation["prompt_customized"],
            prompt_context=build_plan_prompt_context(
                module_payload.get("planner_prompt"),
                plan_generation["coverage_prompt"],
                prompt,
                full_prompt,
                plan_generation["coverage_profile"],
                plan_generation["prompt_customized"],
            ),
        )

        def finalize_requirement_plan_payload():
            if generation_mode == "multiple":
                return finalize_multiple_plan_files(
                    module_name,
                    target_file,
                    job_id,
                    source_message=f"planner(requirement): {module_name}/{plan_filename}",
                    split_message_prefix="planner split(requirement)",
                    requirement=requirement,
                    requirement_module_uid=module_uid,
                    coverage_profile=plan_generation["coverage_profile"],
                    prompt_customized=plan_generation["prompt_customized"],
                )

            asset = sync_plan_asset(
                module_name,
                target_file,
                change_source="planner",
                source_job_id=job_id,
                message=f"planner(requirement): {module_name}/{plan_filename}",
            )
            updated_module = link_requirement_module_plan(
                requirement["id"],
                module_uid,
                asset.get("asset_id") if asset else None,
                job_id,
                coverage_profile=plan_generation["coverage_profile"],
                prompt_customized=plan_generation["prompt_customized"],
            )
            return {
                "plan_filename": plan_filename,
                "plan_name": Path(plan_filename).stem,
                "generation_mode": generation_mode,
                "coverage_profile": plan_generation["coverage_profile"],
                "prompt_customized": plan_generation["prompt_customized"],
                "asset": serialize_asset(asset),
                "requirement_module": serialize_requirement_module(updated_module),
                "revisions": [serialize_revision(item) for item in list_asset_revisions(asset["asset_id"], 10)] if asset else [],
            }

        response = Response(
            stream_with_context(
                stream_plan_generation(
                    module_name,
                    full_prompt,
                    target_file,
                    default_agent="playwright-test-planner",
                    setup_targets=build_setup_targets(),
                    success_payload_factory=finalize_requirement_plan_payload,
                    session_title=agent_message("requirement_plan_title", module=module_name),
                    success_message=agent_message("requirement_plan_success", target=target_file),
                    cancel_job_id=job_id,
                    job_id=job_id,
                    validate_plan_completion=generation_mode == PLAN_GENERATION_MODE_MULTIPLE,
                    cancel_cleanup=lambda: target_file.unlink(missing_ok=True),
                )
            ),
            mimetype="text/event-stream",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"生成测试计划失败：{exc}"}), 500


@app.get("/api/test-suites/<suite_uid>/execution-records")
def list_test_suite_execution_records(suite_uid):
    try:
        records = list_test_suite_execution_records_from_mysql(suite_uid, request.args.get("limit", 20))
        if records is None:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"records": records, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"records": [], "error": f"读取测试集执行记录失败：{exc}"}), 500


@app.get("/api/modules")
def list_modules():
    try:
        specs_dir = get_specs_dir()
    except RuntimeError as exc:
        return jsonify({"modules": [], "error": str(exc)}), 500

    if not specs_dir.exists():
        return jsonify(
            {
                "modules": [],
                "error": f"Specs directory not found: {specs_dir}",
            }
        ), 404

    if not specs_dir.is_dir():
        return jsonify(
            {
                "modules": [],
                "error": f"Specs path is not a directory: {specs_dir}",
            }
        ), 400

    modules = []
    for child in sorted(specs_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue

        module_name = child.name
        plans = [
            plan_payload(plan_file, module_name)
            for plan_file in sorted(
                child.glob("*.md"),
                key=lambda item: (item.name != get_default_plan_filename(module_name), item.name.lower()),
            )
            if plan_file.is_file()
        ]
        if plans:
            modules.append(
                {
                    "name": module_name,
                    "path": str(child),
                    "plans": plans,
                }
            )

    return jsonify({"modules": modules, "error": None})


@app.get("/api/modules/<path:module_name>")
def get_module(module_name):
    try:
        module_file = get_module_file(module_name)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not module_file.exists():
        return jsonify({"error": f"Markdown file not found: {module_file}"}), 404

    try:
        content = module_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": f"File is not valid UTF-8: {module_file}"}), 422
    except OSError as exc:
        return jsonify({"error": f"Failed to read file: {exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "plan_filename": module_file.name,
            "path": str(module_file),
            "markdown": content,
            "html": render_markdown(content),
            "error": None,
        }
    )


@app.put("/api/modules/<path:module_name>")
def save_module(module_name):
    payload = request.get_json(silent=True) or {}
    if "markdown" not in payload or not isinstance(payload["markdown"], str):
        return jsonify({"error": "Request body must include markdown as a string."}), 400

    try:
        module_file = get_module_file(module_name)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not module_file.exists():
        return jsonify({"error": f"Markdown file not found: {module_file}"}), 404

    try:
        def save_module_asset():
            saved_asset = sync_plan_asset(
                module_name,
                module_file,
                change_source="manual",
                message=f"manual: {module_name}/{module_file.name}",
            )
            saved_revisions = list_asset_revisions(saved_asset["asset_id"], 20) if saved_asset else []
            return saved_asset, saved_revisions

        asset, revisions = save_asset_content_with_rollback(
            module_file,
            payload["markdown"],
            save_module_asset,
            lambda: sync_plan_asset(
                module_name,
                module_file,
                change_source="manual",
                message=f"rollback: {module_name}/{module_file.name}",
            ),
            rollback_message=f"rollback failed save: {module_name}/{module_file.name}",
        )
    except OSError as exc:
        return jsonify({"error": f"Failed to save file: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"保存测试计划版本失败：{exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "plan_filename": module_file.name,
            "path": str(module_file),
            "markdown": payload["markdown"],
            "html": render_markdown(payload["markdown"]),
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "error": None,
        }
    )


@app.get("/api/plans/<module_name>/<plan_filename>")
def get_plan(module_name, plan_filename):
    try:
        plan_file = get_plan_file(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not plan_file.exists():
        return jsonify({"error": f"Markdown file not found: {plan_file}"}), 404

    try:
        content = plan_file.read_text(encoding="utf-8")
        asset = sync_plan_asset(module_name, plan_file, change_source="manual", message=f"sync plan: {module_name}/{plan_filename}")
        revisions = list_asset_revisions(asset["asset_id"], 20) if asset else []
        related_scripts = list_related_scripts_for_plan(asset["asset_id"]) if asset else []
    except UnicodeDecodeError:
        return jsonify({"error": f"File is not valid UTF-8: {plan_file}"}), 422
    except OSError as exc:
        return jsonify({"error": f"Failed to read file: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"读取测试计划版本失败：{exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "plan_filename": plan_filename,
            "path": str(plan_file),
            "markdown": content,
            "html": render_markdown(content),
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "related_scripts": [serialize_related_script(item) for item in related_scripts],
            "error": None,
        }
    )


@app.put("/api/plans/<module_name>/<plan_filename>")
def save_plan(module_name, plan_filename):
    payload = request.get_json(silent=True) or {}
    if "markdown" not in payload or not isinstance(payload["markdown"], str):
        return jsonify({"error": "Request body must include markdown as a string."}), 400

    try:
        plan_file = get_plan_file(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not plan_file.exists():
        return jsonify({"error": f"Markdown file not found: {plan_file}"}), 404

    try:
        def save_plan_asset():
            saved_asset = sync_plan_asset(
                module_name,
                plan_file,
                change_source="manual",
                message=f"manual: {module_name}/{plan_filename}",
            )
            saved_revisions = list_asset_revisions(saved_asset["asset_id"], 20) if saved_asset else []
            saved_related_scripts = list_related_scripts_for_plan(saved_asset["asset_id"]) if saved_asset else []
            return saved_asset, saved_revisions, saved_related_scripts

        asset, revisions, related_scripts = save_asset_content_with_rollback(
            plan_file,
            payload["markdown"],
            save_plan_asset,
            lambda: sync_plan_asset(
                module_name,
                plan_file,
                change_source="manual",
                message=f"rollback: {module_name}/{plan_filename}",
            ),
            rollback_message=f"rollback failed save: {module_name}/{plan_filename}",
        )
    except OSError as exc:
        return jsonify({"error": f"Failed to save file: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"保存测试计划版本失败：{exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "plan_filename": plan_filename,
            "path": str(plan_file),
            "markdown": payload["markdown"],
            "html": render_markdown(payload["markdown"]),
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "related_scripts": [serialize_related_script(item) for item in related_scripts],
            "error": None,
        }
    )


@app.delete("/api/plans/<module_name>/<plan_filename>")
def delete_plan(module_name, plan_filename):
    try:
        result = delete_plan_asset(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except OSError as exc:
        return jsonify({"error": f"删除测试计划失败：{exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"删除测试计划失败：{exc}"}), 500

    deleted_asset = result.pop("asset", None)
    return jsonify(
        {
            **result,
            "asset": serialize_asset(deleted_asset),
        }
    )


@app.post("/api/plans/<module_name>/<plan_filename>/split-cases")
def split_plan_cases(module_name, plan_filename):
    payload = request.get_json(silent=True) or {}
    overwrite = bool(payload.get("overwrite"))

    try:
        plan_file = get_plan_file(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not plan_file.exists():
        return jsonify({"error": f"测试计划不存在：{plan_file}"}), 404

    try:
        result = split_case_index_plan(module_name, plan_file, overwrite=overwrite)
        for created_plan in result.get("created") or []:
            created_file = get_plan_file(module_name, created_plan["filename"])
            sync_plan_asset(
                module_name,
                created_file,
                change_source="planner",
                message=f"split plan: {module_name}/{created_plan['filename']}",
            )
    except UnicodeDecodeError:
        return jsonify({"error": f"File is not valid UTF-8: {plan_file}"}), 422
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"拆分测试计划失败：{exc}"}), 500

    return jsonify({"ok": True, "module": module_name, "error": None, **result})


@app.get("/api/plan-generation-defaults")
def get_plan_generation_defaults():
    try:
        target_path_template = str(get_plan_target_path("<模块名>", "<测试计划名>.md"))
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if get_current_project_language() == "en":
        target_path_template = target_path_template.replace("<模块名>", "<module>").replace("<测试计划名>", "<test-plan-name>")

    return jsonify(
        {
            "prompt_template": build_default_plan_prompt_template(),
            "target_path_template": target_path_template,
            "default_coverage_profile": get_plan_generation_config().get("default_coverage_profile"),
            "coverage_profiles": serialize_coverage_profiles(),
            "error": None,
        }
    )


@app.post("/api/plan-generation-stream")
def create_plan_generation_stream():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    plan_name = str(payload.get("plan_name", "")).strip()
    plan_filename = str(payload.get("plan_filename", "")).strip()
    generation_mode = str(payload.get("generation_mode", "single")).strip() or "single"
    prompt = str(payload.get("prompt", "")).strip()
    requirement_uid = str(payload.get("requirement_uid", "")).strip()
    requirement_module_uid = str(payload.get("requirement_module_uid", "")).strip()
    try:
        plan_generation = normalize_plan_generation_request(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400
    if generation_mode not in {"single", "multiple"}:
        return jsonify({"error": "generation_mode must be 'single' or 'multiple'."}), 400
    requirement = None
    if requirement_uid or requirement_module_uid:
        if not requirement_uid or not requirement_module_uid:
            return jsonify({"error": "requirement_uid and requirement_module_uid must be provided together."}), 400
        requirement = get_requirement_by_uid(requirement_uid)
        if not requirement:
            return jsonify({"error": "需求不存在。"}), 404
        if not get_requirement_module(requirement["id"], requirement_module_uid):
            return jsonify({"error": "候选模块不存在。"}), 404

    try:
        validate_module_name(module_name)
        if plan_filename:
            plan_filename = validate_plan_filename(plan_filename) if get_current_project_language() == "en" else validate_chinese_plan_filename(plan_filename)
        else:
            fallback_stem = f"{module_name}-{'case-index' if get_current_project_language() == 'en' else '用例索引'}" if generation_mode == "multiple" else module_name
            plan_filename = get_plan_filename_from_name(plan_name or fallback_stem, module_name) if get_current_project_language() == "en" else get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=fallback_stem)
        target_file = get_plan_target_path(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if target_file.exists():
        return jsonify({"error": f"测试计划已存在：{target_file}"}), 409

    full_prompt = (
        build_multiple_plan_generation_prompt(prompt, module_name, target_file)
        if generation_mode == "multiple"
        else build_generation_prompt(prompt, target_file)
    )
    job_id = sanitize_job_id(str(payload.get("job_id") or f"planner-{uuid.uuid4().hex}").strip())
    try:
        create_test_job(
            "planner",
            job_id=job_id,
            status="queued",
            prompt=full_prompt,
            coverage_profile=plan_generation["coverage_profile"],
            prompt_customized=plan_generation["prompt_customized"],
            prompt_context=build_plan_prompt_context(
                payload.get("base_prompt") or prompt,
                plan_generation["coverage_prompt"],
                prompt,
                full_prompt,
                plan_generation["coverage_profile"],
                plan_generation["prompt_customized"],
            ),
        )
    except Exception as exc:
        return jsonify({"error": f"创建测试计划生成任务失败：{exc}"}), 500

    def finalize_plan_generation_payload():
        if generation_mode == "multiple":
            return finalize_multiple_plan_files(
                module_name,
                target_file,
                job_id,
                source_message=f"planner: {module_name}/{plan_filename}",
                split_message_prefix="planner split",
                requirement=requirement,
                requirement_module_uid=requirement_module_uid if requirement else None,
                coverage_profile=plan_generation["coverage_profile"],
                prompt_customized=plan_generation["prompt_customized"],
            )

        asset = sync_plan_asset(
            module_name,
            target_file,
            change_source="planner",
            source_job_id=job_id,
            message=f"planner: {module_name}/{plan_filename}",
        )
        payload = {
            "plan_filename": plan_filename,
            "plan_name": Path(plan_filename).stem,
            "generation_mode": generation_mode,
            "coverage_profile": plan_generation["coverage_profile"],
            "prompt_customized": plan_generation["prompt_customized"],
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in list_asset_revisions(asset["asset_id"], 10)] if asset else [],
        }
        if requirement:
            updated_module = link_requirement_module_plan(
                requirement["id"],
                requirement_module_uid,
                asset.get("asset_id") if asset else None,
                job_id,
                coverage_profile=plan_generation["coverage_profile"],
                prompt_customized=plan_generation["prompt_customized"],
            )
            payload["requirement_module"] = serialize_requirement_module(updated_module)
        return payload

    response = Response(
        stream_with_context(
            stream_plan_generation(
                module_name,
                full_prompt,
                target_file,
                default_agent="playwright-test-planner",
                setup_targets=build_setup_targets(),
                success_payload_factory=finalize_plan_generation_payload,
                cancel_job_id=job_id,
                job_id=job_id,
                validate_plan_completion=generation_mode == PLAN_GENERATION_MODE_MULTIPLE,
                cancel_cleanup=lambda: target_file.unlink(missing_ok=True),
            )
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/script-generation-stream")
def create_script_generation_stream():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    plan_filename = str(payload.get("plan_filename", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    try:
        validate_module_name(module_name)
        plan_filename = validate_plan_filename(plan_filename) if plan_filename else get_default_plan_filename(module_name)
        plan_file = get_plan_target_path(module_name, plan_filename)
        script_dir = get_script_module_dir(module_name)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not plan_file.exists():
        return jsonify({"error": f"测试计划不存在：{plan_file}"}), 404

    existing_script_names = set()
    if script_dir.exists():
        existing_script_names = {item.name for item in script_dir.glob("*.spec.ts") if item.is_file()}

    script_filename = get_generated_script_filename_from_plan_filename(plan_filename)
    target_file = get_script_file(module_name, script_filename)
    plan_asset = sync_plan_asset(module_name, plan_file, change_source="manual", message=f"sync plan: {module_name}/{plan_filename}")
    job_id = sanitize_job_id(str(payload.get("job_id") or f"generator-{uuid.uuid4().hex}").strip())
    try:
        create_test_job(
            "generator",
            job_id=job_id,
            status="queued",
            source_asset_id=plan_asset.get("asset_id") if plan_asset else None,
            prompt=prompt,
        )
    except Exception as exc:
        return jsonify({"error": f"创建测试脚本生成任务失败：{exc}"}), 500
    candidate_file = get_script_generation_candidate_file(module_name, plan_filename, job_id)
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot = managed_file_snapshot(collect_generation_managed_files(module_name, plan_file, target_file))
    target_snapshot = snapshot.get(str(target_file.resolve(strict=False)), {})
    original_target_hash = target_snapshot.get("hash", "")

    def has_generated_script_output():
        if candidate_file.exists() and candidate_file.is_file() and candidate_file.stat().st_size > 0:
            return True
        if target_file.exists() and file_hash(target_file) != original_target_hash:
            return True
        return bool(get_new_generated_script_files(script_dir, existing_script_names))

    def finalize_generation_payload():
        payload = finalize_script_generation(
            module_name,
            plan_filename,
            plan_file,
            target_file,
            candidate_file,
            snapshot,
            existing_script_names,
        )
        script_asset = sync_script_asset(
            module_name,
            target_file,
            change_source="generator",
            source_job_id=job_id,
            from_plan_asset_id=plan_asset.get("asset_id") if plan_asset else None,
            message=f"generator: {module_name}/{target_file.name}",
        )
        payload["asset"] = serialize_asset(script_asset)
        payload["source_plan_asset"] = serialize_asset(plan_asset)
        payload["revisions"] = (
            [serialize_revision(item) for item in list_asset_revisions(script_asset["asset_id"], 10)]
            if script_asset
            else []
        )
        return payload

    def cleanup_cancelled_generation():
        restore_snapshot_files(snapshot)
        cleanup_new_generated_script_files(script_dir, existing_script_names)
        cleanup_new_managed_files(snapshot)
        candidate_file.unlink(missing_ok=True)

    full_prompt = build_script_generation_prompt(prompt, module_name, plan_file, script_dir, target_file, candidate_file)
    response = Response(
        stream_with_context(
            stream_plan_generation(
                module_name,
                full_prompt,
                target_file,
                completion_check=has_generated_script_output,
                target_label=str(target_file),
                session_title=agent_message("manual_script_generation_title", target=f"{module_name}/{Path(plan_filename).stem}"),
                success_message=agent_message("manual_script_generation_success", target=target_file),
                default_agent="playwright-test-generator",
                setup_targets=build_setup_targets(
                    module_name=module_name,
                    filename=script_filename,
                ),
                success_payload_factory=finalize_generation_payload,
                cancel_job_id=job_id,
                job_id=job_id,
                cancel_cleanup=cleanup_cancelled_generation,
            )
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/script-run-stream")
def create_script_run_stream():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    filename = str(payload.get("filename", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()
    job_id = str(payload.get("job_id", "")).strip()

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    try:
        script_file = get_script_file(module_name, filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not script_file.exists():
        return jsonify({"error": f"Script file not found: {script_file}"}), 404

    script_asset = sync_script_asset(module_name, script_file, change_source="manual", message=f"sync script: {module_name}/{filename}")
    job_id = sanitize_job_id(job_id or f"healer-{uuid.uuid4().hex}")
    try:
        create_test_job(
            "healer",
            job_id=job_id,
            status="queued",
            target_asset_id=script_asset.get("asset_id") if script_asset else None,
            prompt=prompt,
        )
    except Exception as exc:
        return jsonify({"error": f"创建脚本修复任务失败：{exc}"}), 500

    started_at = time.time()
    repair_snapshot = managed_file_snapshot([script_file])

    def finalize_healer_payload():
        result = build_run_video_result(started_at)
        updated_asset = sync_script_asset(
            module_name,
            script_file,
            change_source="healer",
            source_job_id=job_id,
            message=f"healer: {module_name}/{filename}",
        )
        result["asset"] = serialize_asset(updated_asset)
        result["revisions"] = (
            [serialize_revision(item) for item in list_asset_revisions(updated_asset["asset_id"], 10)]
            if updated_asset
            else []
        )
        return result

    full_prompt = build_script_run_prompt(prompt, module_name, filename, script_file)
    response = Response(
        stream_with_context(
            stream_plan_generation(
                module_name,
                full_prompt,
                script_file,
                completion_check=lambda: False,
                completion_required=False,
                target_label=str(script_file),
                session_title=agent_message("manual_script_repair_title", target=filename),
                success_message=agent_message("manual_script_repair_success", target=script_file),
                success_payload_factory=finalize_healer_payload,
                default_agent="playwright-test-healer",
                setup_targets=build_setup_targets(
                    module_name=module_name,
                    filename=filename,
                ),
                cancel_job_id=job_id,
                job_id=job_id,
                cancel_cleanup=lambda: restore_snapshot_files(repair_snapshot),
            )
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def cancel_test_job_response(job_id):
    try:
        raw_job_id = str(job_id or "").strip()
        if not raw_job_id:
            raise ValueError("job_id cannot be empty.")
        job_id = sanitize_job_id(raw_job_id)
        if is_platform_database_enabled() and not get_test_job(job_id):
            return jsonify({"error": "任务不存在。"}), 404
        return jsonify({**cancel_opencode_task(job_id), "job_id": job_id})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"取消 OpenCode 任务失败：{exc}"}), 500


@app.post("/api/jobs/<job_id>/cancel")
def cancel_test_job(job_id):
    return cancel_test_job_response(job_id)


@app.post("/api/script-run-cancel")
def cancel_script_run_stream():
    payload = request.get_json(silent=True) or {}
    return cancel_test_job_response(payload.get("job_id"))


@app.post("/api/script-executions")
def execute_test_script():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    filename = str(payload.get("filename", "")).strip()

    try:
        setup_targets = build_setup_targets(module_name=module_name, filename=filename)
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_script_execution_context(module_name, filename, include_database_global_setup=False)
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright 视频配置失败：{exc}"}), 500

    started_at = time.time()

    result = {
        "status": "running",
        "module_name": module_name,
        "filename": filename,
        "target_path": str(context["script_file"]),
        "command": context["command_text"],
        "returncode": None,
        "output": "",
        "error": None,
    }

    try:
        try:
            setup_logs = []
            setup_summary = None
            if context.get("setup_resolution"):
                setup_summary = execute_setup_profile(
                    context["setup_resolution"],
                    parent_run_id=context.get("run_id"),
                    emit_log=setup_logs.append,
                )
                database_logs = setup_logs
            else:
                database_logs = []
            completed = subprocess.run(
                context["command"],
                cwd=context["project_root"],
                env=get_playwright_execution_env(),
                capture_output=True,
                timeout=get_script_execution_timeout_seconds(),
            )
            output = summarize_process_output(
                completed.stdout,
                completed.stderr,
            )
            if database_logs:
                output = "\n".join([*database_logs, output]).strip()
            result.update(
                {
                    "status": "succeeded" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "output": output,
                    "setup": setup_summary,
                }
            )
            if completed.returncode != 0:
                result["error"] = f"脚本执行失败，退出码：{completed.returncode}"
        except FileNotFoundError:
            result.update(
                {
                    "status": "failed",
                    "error": "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。",
                }
            )
        except subprocess.TimeoutExpired as exc:
            result.update(
                {
                    "status": "failed",
                    "output": summarize_process_output(
                        exc.stdout,
                        exc.stderr,
                    ),
                    "error": "脚本执行超时，已停止等待结果。",
                }
            )
        except OSError as exc:
            result.update(
                {
                    "status": "failed",
                    "error": f"脚本执行失败：{exc}",
                }
            )
        except SetupPreparationError as exc:
            result.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "setup": exc.summary,
                }
            )
        except Exception as exc:
            result.update(
                {
                    "status": "failed",
                    "error": f"测试前准备脚本执行失败：{exc}",
                }
            )
    finally:
        try:
            context["video_config"].unlink(missing_ok=True)
        except OSError:
            pass

    result.update(build_run_video_result(started_at, context["results_dir"]))
    result.update(build_playwright_report_result(started_at, context["report_dir"]))
    return jsonify(result)


def _BufferedExecutionOutput(job_id, *, agent_stream=False, project_root=None):
    return execution_streaming.BufferedExecutionOutput(
        sys.modules[__name__],
        job_id,
        agent_stream=agent_stream,
        project_root=project_root,
    )

def stream_script_execution(module_name, filename, context, *, agent_stream=False):
    yield from execution_streaming.stream_script_execution(
        sys.modules[__name__], module_name, filename, context, agent_stream=agent_stream
    )


@app.post("/api/script-execution-stream")
def execute_test_script_stream():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    filename = str(payload.get("filename", "")).strip()

    try:
        setup_targets = build_setup_targets(module_name=module_name, filename=filename)
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_script_execution_context(module_name, filename, include_database_global_setup=False)
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright 视频配置失败：{exc}"}), 500

    response = Response(
        stream_with_context(stream_script_execution(module_name, filename, context)),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def stream_module_script_execution(module_name, filenames, context, *, agent_stream=False):
    yield from execution_streaming.stream_module_script_execution(
        sys.modules[__name__], module_name, filenames, context, agent_stream=agent_stream
    )


@app.post("/api/module-script-execution-stream")
def execute_module_test_scripts_stream():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    filenames = payload.get("filenames") or []

    try:
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        setup_targets = (
            build_setup_targets(module_name=module_name, filenames=filenames)
            if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE
            else build_setup_targets()
        )
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_module_script_execution_context(
            module_name, filenames, execution_mode, include_database_global_setup=False
        )
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright 批量执行配置失败：{exc}"}), 500

    response = Response(
        stream_with_context(stream_module_script_execution(module_name, context["filenames"], context)),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def stream_test_suite_execution(suite_id, suite_name, items, context, *, agent_stream=False):
    yield from execution_streaming.stream_test_suite_execution(
        sys.modules[__name__], suite_id, suite_name, items, context, agent_stream=agent_stream
    )


@app.post("/api/test-suite-execution-stream")
def execute_test_suite_stream():
    payload = request.get_json(silent=True) or {}
    suite_id = str(payload.get("suite_id", "")).strip()
    suite_name = str(payload.get("suite_name", "")).strip()
    items = payload.get("items") or []

    if not suite_id:
        return jsonify({"error": "suite_id is required."}), 400
    if not suite_name:
        return jsonify({"error": "suite_name is required."}), 400

    try:
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        setup_targets = build_setup_targets(
            suite_uid=suite_id,
            items=items if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE else None,
        )
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_test_suite_execution_context(items, execution_mode, include_database_global_setup=False)
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright 测试集执行配置失败：{exc}"}), 500

    response = Response(
        stream_with_context(stream_test_suite_execution(suite_id, suite_name, context["items"], context)),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/test-suites/<suite_uid>/execution-stream")
def execute_persisted_test_suite_stream(suite_uid):
    payload = request.get_json(silent=True) or {}

    try:
        suite = get_test_suite_payload(suite_uid)
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        items = suite.get("items") or []
        if not items:
            return jsonify({"error": "测试集没有可执行脚本。"}), 400
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        setup_targets = build_setup_targets(
            suite_uid=suite_uid,
            items=items if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE else None,
        )
        setup_resolution = resolve_setup_profile(setup_targets)
        context = build_test_suite_execution_context(items, execution_mode, include_database_global_setup=False)
        context["setup_targets"] = setup_targets
        context["setup_resolution"] = setup_resolution
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"创建 Playwright 测试集执行配置失败：{exc}"}), 500

    response = Response(
        stream_with_context(stream_test_suite_execution(suite["suite_uid"], suite["name"], context["items"], context)),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/script-recordings")
def record_test_script():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    filename = str(payload.get("filename", "")).strip()

    try:
        context = build_script_recording_context(module_name, filename)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    result = {
        "status": "running",
        "module_name": module_name,
        "filename": filename,
        "path": str(context["script_file"]),
        "command": context["command_text"],
        "returncode": None,
        "output": "",
        "error": None,
        "content": None,
    }

    try:
        completed = subprocess.run(
            context["command"],
            cwd=context["project_root"],
            capture_output=True,
            timeout=get_script_execution_timeout_seconds(),
        )
        output = summarize_process_output(
            completed.stdout,
            completed.stderr,
        )
        result.update(
            {
                "status": "succeeded" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "output": output,
            }
        )
        if completed.returncode != 0:
            result["error"] = f"脚本录制失败，退出码：{completed.returncode}"
    except FileNotFoundError:
        result.update(
            {
                "status": "failed",
                "error": "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。",
            }
        )
    except subprocess.TimeoutExpired as exc:
        result.update(
            {
                "status": "failed",
                "output": summarize_process_output(
                    exc.stdout,
                    exc.stderr,
                ),
                "error": "脚本录制超时，已停止等待结果。",
            }
        )
    except OSError as exc:
        result.update(
            {
                "status": "failed",
                "error": f"脚本录制失败：{exc}",
            }
        )

    try:
        result["content"] = context["script_file"].read_text(encoding="utf-8")
        if result["status"] == "succeeded":
            asset = sync_script_asset(
                module_name,
                context["script_file"],
                change_source="codegen",
                message=f"codegen: {module_name}/{filename}",
            )
            result["asset"] = serialize_asset(asset)
            result["revisions"] = (
                [serialize_revision(item) for item in list_asset_revisions(asset["asset_id"], 20)]
                if asset
                else []
            )
    except UnicodeDecodeError:
        result["content"] = ""
        result["error"] = result["error"] or f"File is not valid UTF-8: {context['script_file']}"
        result["status"] = "failed"
    except OSError as exc:
        result["content"] = ""
        result["error"] = result["error"] or f"Failed to read file: {exc}"
        result["status"] = "failed"
    except Exception as exc:
        result["error"] = result["error"] or f"保存录制脚本版本失败：{exc}"
        result["status"] = "failed"

    return jsonify(result)


@app.post("/api/plan-generation-jobs")
def create_plan_generation_job():
    payload = request.get_json(silent=True) or {}
    module_name = str(payload.get("module_name", "")).strip()
    plan_name = str(payload.get("plan_name", "")).strip()
    plan_filename = str(payload.get("plan_filename", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    try:
        validate_module_name(module_name)
        if plan_filename:
            plan_filename = validate_plan_filename(plan_filename) if get_current_project_language() == "en" else validate_chinese_plan_filename(plan_filename)
        else:
            plan_filename = get_plan_filename_from_name(plan_name or module_name, module_name) if get_current_project_language() == "en" else get_chinese_plan_filename_from_name(plan_name, module_name, fallback_stem=module_name)
        target_file = get_plan_target_path(module_name, plan_filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if target_file.exists():
        return jsonify({"error": f"测试计划已存在：{target_file}"}), 409

    job_id = uuid.uuid4().hex
    full_prompt = build_generation_prompt(prompt, target_file)
    now = time.time()
    job = {
        "id": job_id,
        "status": "pending",
        "module_name": module_name,
        "plan_filename": plan_filename,
        "target_path": str(target_file),
        "logs": [f"任务已创建，目标文件：{target_file}"],
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    with PLAN_GENERATION_LOCK:
        PLAN_GENERATION_JOBS[job_id] = job

    try:
        save_platform_job_to_mysql(make_job_snapshot(job))
    except Exception as exc:
        with PLAN_GENERATION_LOCK:
            PLAN_GENERATION_JOBS.pop(job_id, None)
        return jsonify({"error": f"保存任务到 MySQL 失败：{exc}"}), 500

    project = get_current_project()

    def run_job_in_project():
        with use_project_context(project):
            run_plan_generation_job(job_id, full_prompt, target_file, "playwright-test-planner")

    thread = threading.Thread(target=run_job_in_project, daemon=True)
    thread.start()

    with PLAN_GENERATION_LOCK:
        return jsonify(make_job_snapshot(PLAN_GENERATION_JOBS[job_id])), 202


@app.get("/api/plan-generation-jobs/<job_id>")
def get_plan_generation_job(job_id):
    with PLAN_GENERATION_LOCK:
        job = PLAN_GENERATION_JOBS.get(job_id)
        if job:
            return jsonify(make_job_snapshot(job))

    try:
        job = load_platform_job_from_mysql(job_id)
    except Exception as exc:
        return jsonify({"error": f"读取 MySQL 任务失败：{exc}"}), 500
    if not job:
        return jsonify({"error": "Generation job not found."}), 404

    return jsonify(make_job_snapshot(job))


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    try:
        job = get_test_job(job_id)
    except Exception as exc:
        return jsonify({"error": f"读取任务失败：{exc}"}), 500
    if not job:
        return jsonify({"error": "Job not found."}), 404
    serialized = serialize_job(job)
    log_path = Path(job.get("log_path")) if job.get("log_path") else get_job_log_path(job_id)
    if log_path.exists() and log_path.is_file():
        log_tail, log_size = read_file_tail(log_path)
        serialized.update(
            {
                "log_path": str(log_path),
                "log_tail": log_tail,
                "log_size": log_size,
            }
        )
    return jsonify({"job": serialized, "error": None})


@app.get("/api/jobs/<job_id>/log")
def get_job_log(job_id):
    try:
        tail = int(request.args.get("tail") or JOB_LOG_TAIL_LIMIT)
    except (TypeError, ValueError):
        tail = JOB_LOG_TAIL_LIMIT
    tail = min(max(tail, 1), 1000000)

    try:
        job = get_test_job(job_id)
        log_path = Path(job.get("log_path")) if job and job.get("log_path") else get_job_log_path(job_id)
        log_tail, size = read_file_tail(log_path, tail)
    except Exception as exc:
        return jsonify({"error": f"读取任务日志失败：{exc}"}), 500

    return jsonify({"job_id": job_id, "tail": log_tail, "size": size, "path": str(log_path), "error": None})


@app.get("/api/jobs/<job_id>/log/download")
def download_job_log(job_id):
    try:
        job = get_test_job(job_id)
        log_path = Path(job.get("log_path")) if job and job.get("log_path") else get_job_log_path(job_id)
    except Exception as exc:
        return jsonify({"error": f"读取任务日志失败：{exc}"}), 500

    if not log_path.exists() or not log_path.is_file():
        return jsonify({"error": f"Log file not found: {log_path}"}), 404

    return send_file(log_path, as_attachment=True, download_name=log_path.name, conditional=True)


@app.get("/api/assets/<int:asset_id>/revisions")
def get_asset_revisions(asset_id):
    try:
        asset = get_test_asset_by_id(asset_id)
        if not asset:
            return jsonify({"error": "Asset not found."}), 404
        revisions = list_asset_revisions(asset_id, 100)
    except Exception as exc:
        return jsonify({"error": f"读取版本历史失败：{exc}"}), 500

    return jsonify(
        {
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "error": None,
        }
    )


@app.get("/api/assets/<int:asset_id>/revisions/<int:revision_id>/content")
def get_asset_revision_content(asset_id, revision_id):
    try:
        asset = get_test_asset_by_id(asset_id)
        revision = get_asset_revision(asset_id, revision_id)
        if not asset or not revision:
            return jsonify({"error": "Revision not found."}), 404
        content = git_show_file(revision["git_commit_sha"], revision["file_path"])
    except Exception as exc:
        return jsonify({"error": f"读取版本内容失败：{exc}"}), 500

    return jsonify(
        {
            "asset": serialize_asset(asset),
            "revision": serialize_revision(revision),
            "content": content,
            "error": None,
        }
    )


@app.get("/api/assets/<int:asset_id>/revisions/<int:revision_id>/diff-current")
def get_asset_revision_diff(asset_id, revision_id):
    try:
        asset = get_test_asset_by_id(asset_id)
        revision = get_asset_revision(asset_id, revision_id)
        if not asset or not revision:
            return jsonify({"error": "Revision not found."}), 404
        diff = git_diff_file(revision["git_commit_sha"], asset["current_path"])
    except Exception as exc:
        return jsonify({"error": f"读取版本差异失败：{exc}"}), 500

    return jsonify(
        {
            "asset": serialize_asset(asset),
            "revision": serialize_revision(revision),
            "diff": diff,
            "error": None,
        }
    )


@app.post("/api/assets/<int:asset_id>/revisions/<int:revision_id>/restore")
def restore_asset_revision(asset_id, revision_id):
    try:
        asset = get_test_asset_by_id(asset_id)
        revision = get_asset_revision(asset_id, revision_id)
        if not asset or not revision:
            return jsonify({"error": "Revision not found."}), 404

        content = git_show_file(revision["git_commit_sha"], revision["file_path"])
        target_file = Path(asset["current_path"])
        target_file.write_text(content, encoding="utf-8", newline="")
        if asset["asset_type"] == "plan":
            updated_asset = sync_plan_asset(
                asset["module_name"],
                target_file,
                change_source="manual",
                message=f"restore: {asset['module_name']}/{target_file.name} to v{revision['version_no']}",
            )
        else:
            updated_asset = sync_script_asset(
                asset["module_name"],
                target_file,
                change_source="manual",
                from_plan_asset_id=asset.get("from_plan_asset_id"),
                message=f"restore: {asset['module_name']}/{target_file.name} to v{revision['version_no']}",
            )
        revisions = list_asset_revisions(updated_asset["asset_id"], 20) if updated_asset else []
    except Exception as exc:
        return jsonify({"error": f"恢复版本失败：{exc}"}), 500

    return jsonify(
        {
            "ok": True,
            "asset": serialize_asset(updated_asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "error": None,
        }
    )


@app.get("/api/test-scripts")
def list_test_scripts():
    try:
        tests_dir = get_tests_dir()
    except RuntimeError as exc:
        return jsonify({"modules": [], "error": str(exc)}), 500

    if not tests_dir.exists():
        return jsonify(
            {
                "modules": [],
                "error": f"Tests directory not found: {tests_dir}",
            }
        ), 404

    if not tests_dir.is_dir():
        return jsonify(
            {
                "modules": [],
                "error": f"Tests path is not a directory: {tests_dir}",
            }
        ), 400

    modules = []
    for child in sorted(tests_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue

        scripts = []
        for script_file in sorted(child.glob("*.spec.ts"), key=lambda item: item.name.lower()):
            if not script_file.is_file():
                continue

            scripts.append(
                {
                    "name": script_file.name,
                    "display_name": script_file.name[: -len(".spec.ts")],
                    "path": str(script_file),
                }
            )

        if scripts:
            modules.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "scripts": scripts,
                }
            )

    return jsonify({"modules": modules, "error": None})


@app.get("/api/test-scripts/<path:module_name>/<path:filename>")
def get_test_script(module_name, filename):
    try:
        script_file = get_script_file(module_name, filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not script_file.exists():
        return jsonify({"error": f"Script file not found: {script_file}"}), 404

    try:
        content = script_file.read_text(encoding="utf-8")
        asset = sync_script_asset(module_name, script_file, change_source="manual", message=f"sync script: {module_name}/{filename}")
        revisions = list_asset_revisions(asset["asset_id"], 20) if asset else []
        source_plan = get_plan_asset_for_script_asset(asset)
        recent_results = list_recent_script_results(asset["asset_id"], 20) if asset else []
    except UnicodeDecodeError:
        return jsonify({"error": f"File is not valid UTF-8: {script_file}"}), 422
    except OSError as exc:
        return jsonify({"error": f"Failed to read file: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"读取测试脚本版本失败：{exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "filename": filename,
            "path": str(script_file),
            "content": content,
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "source_plan": serialize_asset(source_plan),
            "recent_results": [serialize_run_result(item) for item in recent_results],
            "error": None,
        }
    )


@app.delete("/api/test-scripts/<path:module_name>/<path:filename>")
def delete_test_script(module_name, filename):
    try:
        result = delete_script_asset(module_name, filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except OSError as exc:
        return jsonify({"error": f"删除测试脚本失败：{exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"删除测试脚本失败：{exc}"}), 500

    deleted_asset = result.pop("asset", None)
    return jsonify(
        {
            **result,
            "asset": serialize_asset(deleted_asset),
        }
    )


@app.get("/api/run-videos/<path:relative_path>")
def get_run_video(relative_path):
    try:
        video_file = get_run_video_file(relative_path)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not video_file.exists() or not video_file.is_file():
        return jsonify({"error": f"Video file not found: {video_file}"}), 404

    mimetype = "video/webm" if video_file.suffix.lower() == ".webm" else "video/mp4"
    return send_file(video_file, mimetype=mimetype, conditional=True)


@app.get("/api/playwright-reports/<path:relative_path>")
def get_playwright_report(relative_path):
    try:
        report_file = get_playwright_report_file(relative_path)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not report_file.exists() or not report_file.is_file():
        return jsonify({"error": f"Report file not found: {report_file}"}), 404

    return send_file(report_file, conditional=True)


@app.put("/api/test-scripts/<path:module_name>/<path:filename>")
def save_test_script(module_name, filename):
    payload = request.get_json(silent=True) or {}
    if "content" not in payload or not isinstance(payload["content"], str):
        return jsonify({"error": "Request body must include content as a string."}), 400

    try:
        script_file = get_script_file(module_name, filename)
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    if not script_file.exists():
        return jsonify({"error": f"Script file not found: {script_file}"}), 404

    try:
        def save_script_asset():
            saved_asset = sync_script_asset(
                module_name,
                script_file,
                change_source="manual",
                message=f"manual: {module_name}/{filename}",
            )
            saved_revisions = list_asset_revisions(saved_asset["asset_id"], 20) if saved_asset else []
            saved_source_plan = get_plan_asset_for_script_asset(saved_asset)
            saved_recent_results = list_recent_script_results(saved_asset["asset_id"], 20) if saved_asset else []
            return saved_asset, saved_revisions, saved_source_plan, saved_recent_results

        asset, revisions, source_plan, recent_results = save_asset_content_with_rollback(
            script_file,
            payload["content"],
            save_script_asset,
            lambda: sync_script_asset(
                module_name,
                script_file,
                change_source="manual",
                message=f"rollback: {module_name}/{filename}",
            ),
            rollback_message=f"rollback failed save: {module_name}/{filename}",
        )
    except OSError as exc:
        return jsonify({"error": f"Failed to save file: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"保存测试脚本版本失败：{exc}"}), 500

    return jsonify(
        {
            "module": module_name,
            "filename": filename,
            "path": str(script_file),
            "content": payload["content"],
            "asset": serialize_asset(asset),
            "revisions": [serialize_revision(item) for item in revisions],
            "source_plan": serialize_asset(source_plan),
            "recent_results": [serialize_run_result(item) for item in recent_results],
            "error": None,
        }
    )


app.register_blueprint(
    create_auth_blueprint(_auth_web_services())
)
app.register_blueprint(
    create_platform_records_blueprint(
        PlatformRecordServices(
            get_database_config=lambda: get_platform_database_config(),
            load_records=lambda: load_platform_records_from_mysql(),
            save_record=lambda bucket, record_key, record: save_platform_record_to_mysql(
                bucket,
                record_key,
                record,
            ),
        )
    )
)
app.register_blueprint(
    create_projects_blueprint(_project_web_services())
)
app.register_blueprint(
    create_project_archive_blueprint(_project_archive_web_services())
)
app.register_blueprint(
    create_setup_blueprint(_setup_web_services())
)
app.register_blueprint(
    create_requirements_blueprint(
        _requirement_web_services()
    )
)
app.register_blueprint(
    create_page_inventory_blueprint(
        _page_inventory_web_services()
    )
)
app.register_blueprint(
    create_plan_workbook_blueprint(_plan_workbook_web_services())
)
app.register_blueprint(
    create_test_suites_blueprint(_test_suite_web_services())
)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
