"""Configuration parsing and default application path resolution."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
CONFIG_PATH = DEFAULT_CONFIG_PATH

DISABLED_DATABASE_BASELINE_CONFIG = {"enabled": False}
DEFAULT_TARGET_SYSTEM_CONFIG = {
    "base_url": "",
    "login_url": "/login",
    "username": "",
    "password": "",
}
DEFAULT_COVERAGE_PROFILE = "core"
DEFAULT_PROJECT_LANGUAGE = "en"
SUPPORTED_PROJECT_LANGUAGES = frozenset({"zh-CN", "en"})
COVERAGE_PROFILES = {
    "core": {
        "key": "core",
        "label": "核心回归",
        "description": "优先覆盖用户最常用、业务价值最高的正向主要流程。",
        "suggested_max_cases": 10,
        "template_prompt": (
            "优先覆盖用户最常用、业务价值最高的正向主要流程，建议生成 3-5 条、最多 10 条测试用例。"
            "需求明确规定的关键异常或边界规则不得遗漏；不要主动扩展需求未提及的异常、边界、兼容性、"
            "安全或低频分支，也不要为了凑数生成重复用例。"
        ),
    },
    "standard": {
        "key": "standard",
        "label": "标准功能",
        "description": "覆盖需求明确的正向、异常、边界、角色与权限规则。",
        "suggested_max_cases": 15,
        "template_prompt": (
            "覆盖需求明确描述的正向流程、异常处理、边界条件、角色差异和权限规则，建议最多生成 15 条测试用例。"
            "不要主动扩展需求之外的兼容性、安全攻击或低频业务路径，也不要为了凑数生成重复用例。"
        ),
    },
    "comprehensive": {
        "key": "comprehensive",
        "label": "全面回归",
        "description": "主动探索功能、边界、错误、权限、兼容性、安全与低频场景。",
        "suggested_max_cases": 25,
        "template_prompt": (
            "在完整覆盖主要业务流程和需求明确规则的基础上，主动探索错误处理、边界条件、状态组合、角色差异、"
            "权限绕过、兼容性、安全和低频分支场景，建议最多生成 25 条测试用例。保持场景独立并避免重复。"
        ),
    },
}

DEFAULT_OPENCODE_TASK_TIMEOUT_SECONDS = 7200
DEFAULT_SCRIPT_EXECUTION_TIMEOUT_SECONDS = 7200
DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS = 1800

MYSQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$]+$")
AUTH_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_DISABLED = "disabled"


def normalize_project_language(value, default=DEFAULT_PROJECT_LANGUAGE):
    """Return one supported project UI/prompt language."""

    normalized = str(value or "").strip()
    if not normalized:
        return default
    if normalized not in SUPPORTED_PROJECT_LANGUAGES:
        raise ValueError("Unsupported project language.")
    return normalized


def parse_boolean(value, default=False):
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value must be true or false.")


def resolve_config_path(config_path=None):
    if config_path is not None:
        return Path(config_path).expanduser()
    configured_path = str(
        os.environ.get("PLATFORM_CONFIG_PATH") or ""
    ).strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_CONFIG_PATH


def validate_http_url(
    value,
    field_name,
    *,
    allow_empty=False,
    allow_relative=False,
):
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme:
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
        ):
            raise ValueError(
                f"{field_name} must use an absolute HTTP(S) URL."
            )
        return text.rstrip("/")
    if allow_relative and text.startswith("/"):
        return text
    raise ValueError(
        f"{field_name} must use an absolute HTTP(S) URL"
        + (" or a root-relative path." if allow_relative else ".")
    )


def parse_timeout_seconds(value, default, field_name):
    if value in (None, ""):
        return default

    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"config.json {field_name} must be a positive integer.") from exc

    if timeout <= 0:
        raise ValueError(f"config.json {field_name} must be a positive integer.")

    return timeout


def format_timeout_seconds(seconds):
    seconds = int(seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def parse_project_key(value, field_name="project_key"):
    project_key = str(value or "").strip()
    if not project_key or not PROJECT_KEY_PATTERN.match(project_key):
        raise ValueError(f"config.json {field_name} must be 1-64 letters, numbers, '.', '_' or '-'.")
    return project_key


def parse_project_path_segment(value, default, field_name):
    segment = str(value or default or "").strip() or default
    if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment or "\x00" in segment:
        raise ValueError(f"config.json {field_name} must be a simple directory name.")
    return segment


def parse_project_opencode_config(value):
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("config.json projects[].opencode_config must be an object.")

    normalized = {}
    if "opencode_server_url" in value or "server_url" in value:
        server_url = validate_http_url(
            value.get("opencode_server_url")
            or value.get("server_url"),
            "projects[].opencode_config.opencode_server_url",
            allow_empty=True,
        )
        if server_url:
            normalized["opencode_server_url"] = server_url
    if "opencode_username" in value or "username" in value:
        username = str(value.get("opencode_username") or value.get("username") or "").strip()
        if username:
            normalized["opencode_username"] = username
    if "opencode_password" in value or "password" in value:
        normalized["opencode_password"] = str(
            value.get("opencode_password", value.get("password", ""))
        )

    return normalized or None


def validate_coverage_profile(value, fallback=DEFAULT_COVERAGE_PROFILE):
    profile = str(value or fallback or DEFAULT_COVERAGE_PROFILE).strip().lower()
    if profile not in COVERAGE_PROFILES:
        raise ValueError("coverage_profile must be 'core', 'standard' or 'comprehensive'.")
    return profile


def parse_plan_generation_config(value):
    if value in (None, ""):
        return {"default_coverage_profile": DEFAULT_COVERAGE_PROFILE}
    if not isinstance(value, dict):
        raise ValueError("plan_generation must be an object.")
    return {
        "default_coverage_profile": validate_coverage_profile(
            value.get("default_coverage_profile"), DEFAULT_COVERAGE_PROFILE
        )
    }


def parse_target_system_config(value):
    if value in (None, ""):
        return dict(DEFAULT_TARGET_SYSTEM_CONFIG)
    if not isinstance(value, dict):
        raise ValueError("target_system must be an object.")

    base_url = validate_http_url(
        value.get("base_url"),
        "target_system.base_url",
        allow_empty=True,
    )
    login_url = validate_http_url(
        value.get("login_url", value.get("login_path", "/login"))
        or "/login",
        "target_system.login_url",
        allow_relative=True,
    )
    username = str(value.get("username", "")).strip()
    password = str(value.get("password", ""))
    return {
        "base_url": base_url,
        "login_url": login_url,
        "username": username,
        "password": password,
    }


def parse_project_entry(value, fallback_key=None, fallback_name=None):
    if not isinstance(value, dict):
        raise ValueError("config.json projects items must be objects.")

    project_key = parse_project_key(value.get("key") or value.get("project_key") or fallback_key or "default")
    name = str(value.get("name") or fallback_name or project_key).strip()
    if not name:
        raise ValueError("config.json projects[].name cannot be empty.")
    if len(name) > 128:
        raise ValueError("config.json projects[].name is too long.")

    project_root = str(value.get("playwright_project_root") or value.get("root") or "").strip()
    if not project_root:
        raise ValueError("config.json projects[].playwright_project_root is required.")

    return {
        "project_key": project_key,
        "key": project_key,
        "name": name,
        "description": str(value.get("description", "")).strip()[:512],
        "playwright_project_root": project_root,
        "specs_dir": parse_project_path_segment(value.get("specs_dir"), "specs", "projects[].specs_dir"),
        "tests_dir": parse_project_path_segment(value.get("tests_dir"), "tests", "projects[].tests_dir"),
        "opencode_config": parse_project_opencode_config(value.get("opencode_config")),
        "target_system": parse_target_system_config(value.get("target_system"))
        if value.get("target_system") not in (None, "")
        else None,
        "database_baseline": parse_database_baseline_config(value.get("database_baseline"))
        if value.get("database_baseline") not in (None, "")
        else None,
        "plan_generation": parse_plan_generation_config(value.get("plan_generation")),
        "status": PROJECT_STATUS_DISABLED if value.get("status") == PROJECT_STATUS_DISABLED else PROJECT_STATUS_ACTIVE,
        "is_default": bool(value.get("is_default", False)),
    }


def parse_projects_config(config):
    raw_projects = config.get("projects")
    projects = []
    seen = set()

    if isinstance(raw_projects, list):
        for raw_project in raw_projects:
            project = parse_project_entry(raw_project)
            if project["project_key"] in seen:
                raise ValueError(f"config.json projects has duplicate key: {project['project_key']}")
            seen.add(project["project_key"])
            projects.append(project)
    elif raw_projects not in (None, ""):
        raise ValueError("config.json projects must be an array.")

    legacy_root = str(config.get("playwright_project_root", "")).strip()
    if legacy_root and "default" not in seen:
        projects.insert(
            0,
            parse_project_entry(
                {
                    "key": "default",
                    "name": "默认项目",
                    "playwright_project_root": legacy_root,
                    "is_default": True,
                }
            ),
        )

    if not projects:
        raise ValueError("config.json must include playwright_project_root or projects[].playwright_project_root.")

    default_project_key = str(config.get("default_project_key") or "").strip()
    if default_project_key:
        default_project_key = parse_project_key(default_project_key, "default_project_key")
    if not default_project_key:
        default_project_key = next((project["project_key"] for project in projects if project.get("is_default")), "")
    if not default_project_key or default_project_key not in {project["project_key"] for project in projects}:
        default_project_key = projects[0]["project_key"]

    for project in projects:
        project["is_default"] = project["project_key"] == default_project_key

    return projects, default_project_key


def parse_platform_database_config(value):
    if value in (None, ""):
        return {"enabled": False, "type": "mysql"}

    if not isinstance(value, dict):
        raise ValueError("config.json platform_database must be an object.")

    enabled = bool(value.get("enabled", False))
    database_type = str(value.get("type", "mysql")).strip().lower() or "mysql"
    if database_type != "mysql":
        raise ValueError("config.json platform_database.type must be 'mysql'.")

    try:
        port = int(value.get("port", 3306))
    except (TypeError, ValueError) as exc:
        raise ValueError("config.json platform_database.port must be a positive integer.") from exc
    if port <= 0:
        raise ValueError("config.json platform_database.port must be a positive integer.")

    connect_timeout = parse_timeout_seconds(
        value.get("connect_timeout"),
        5,
        "platform_database.connect_timeout",
    )
    database = str(value.get("database", "")).strip()
    if enabled and not database:
        raise ValueError("config.json platform_database.database is required when platform_database is enabled.")
    if database and not MYSQL_IDENTIFIER_PATTERN.match(database):
        raise ValueError("config.json platform_database.database may only contain letters, numbers, '_' and '$'.")

    table_prefix = str(value.get("table_prefix", "")).strip()
    if table_prefix and not MYSQL_IDENTIFIER_PATTERN.match(table_prefix):
        raise ValueError("config.json platform_database.table_prefix may only contain letters, numbers, '_' and '$'.")

    user = str(value.get("user", "")).strip()
    if enabled and not user:
        raise ValueError(
            "config.json platform_database.user is required when platform_database is enabled."
        )

    return {
        "enabled": enabled,
        "type": database_type,
        "host": str(value.get("host", "127.0.0.1")).strip() or "127.0.0.1",
        "port": port,
        "user": user,
        "password": str(value.get("password", "")),
        "database": database,
        "charset": str(value.get("charset", "utf8mb4")).strip() or "utf8mb4",
        "table_prefix": table_prefix,
        "create_database": bool(value.get("create_database", True)),
        "connect_timeout": connect_timeout,
    }


def parse_database_baseline_config(value):
    if value in (None, ""):
        return {"enabled": False}

    if not isinstance(value, dict):
        raise ValueError("config.json database_baseline must be an object.")

    enabled = bool(value.get("enabled", False))
    mode = str(value.get("mode", "")).strip().lower()
    if not mode:
        mode = "file" if value.get("database_path") else "command"

    if mode not in {"file", "command"}:
        raise ValueError("config.json database_baseline.mode must be 'file' or 'command'.")

    timeout_seconds = parse_timeout_seconds(
        value.get("timeout_seconds"),
        DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS,
        "database_baseline.timeout_seconds",
    )

    return {
        "enabled": enabled,
        "mode": mode,
        "database_path": str(value.get("database_path", "")).strip(),
        "baseline_path": str(value.get("baseline_path", "")).strip(),
        "marker_path": str(value.get("marker_path", "")).strip(),
        "lock_path": str(value.get("lock_path", "")).strip(),
        "working_directory": str(value.get("working_directory", "")).strip(),
        "backup_command": value.get("backup_command", ""),
        "restore_command": value.get("restore_command", ""),
        "test_command": value.get("test_command", ""),
        "timeout_seconds": timeout_seconds,
    }


def parse_auth_config(value):
    if value in (None, ""):
        value = {}

    if not isinstance(value, dict):
        raise ValueError("config.json auth must be an object.")

    enabled = parse_boolean(
        os.environ.get("PLATFORM_AUTH_ENABLED"),
        value.get("enabled", True),
    )
    session_secret = (
        os.environ.get("PLATFORM_SESSION_SECRET")
        or str(value.get("session_secret", "")).strip()
        or "test-plan-viewer-change-me"
    )
    initial_admin_password = (
        os.environ.get("PLATFORM_ADMIN_PASSWORD")
        or str(value.get("initial_admin_password", "")).strip()
        or "Admin@123456"
    )
    initial_admin_username = str(value.get("initial_admin_username", "admin")).strip() or "admin"
    if not AUTH_USERNAME_PATTERN.match(initial_admin_username):
        raise ValueError("config.json auth.initial_admin_username must be 3-64 letters, numbers, '.', '_' or '-'.")
    return {
        "enabled": enabled,
        "session_secret": session_secret,
        "initial_admin_username": initial_admin_username,
        "initial_admin_password": initial_admin_password,
    }


def load_config(config_path=None):
    path = resolve_config_path(config_path)
    if not path.exists():
        return {
            "playwright_project_root": "",
            "projects": [],
            "default_project_key": "",
            "project_workspace_root": "",
            "project_template_dependency_source_root": "",
            "error": f"Config file not found: {path}",
        }

    try:
        with path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as exc:
        return {
            "playwright_project_root": "",
            "projects": [],
            "default_project_key": "",
            "project_workspace_root": "",
            "project_template_dependency_source_root": "",
            "error": f"Invalid JSON in config.json: {exc}",
        }

    project_root = str(config.get("playwright_project_root", "")).strip()
    try:
        projects, default_project_key = parse_projects_config(config)
        default_project = next(project for project in projects if project["project_key"] == default_project_key)
        project_root = project_root or default_project["playwright_project_root"]
        opencode_task_timeout_seconds = parse_timeout_seconds(
            config.get("opencode_task_timeout_seconds"),
            DEFAULT_OPENCODE_TASK_TIMEOUT_SECONDS,
            "opencode_task_timeout_seconds",
        )
        script_execution_timeout_seconds = parse_timeout_seconds(
            config.get("script_execution_timeout_seconds"),
            DEFAULT_SCRIPT_EXECUTION_TIMEOUT_SECONDS,
            "script_execution_timeout_seconds",
        )
        platform_database = parse_platform_database_config(config.get("platform_database"))
        database_baseline = parse_database_baseline_config(config.get("database_baseline"))
        auth = parse_auth_config(config.get("auth"))
    except ValueError as exc:
        return {
            "playwright_project_root": project_root,
            "projects": [],
            "default_project_key": "",
            "project_workspace_root": str(config.get("project_workspace_root", "")).strip(),
            "project_template_dependency_source_root": str(
                config.get("project_template_dependency_source_root", "")
            ).strip(),
            "error": str(exc),
        }

    return {
        "playwright_project_root": project_root,
        "opencode_server_url": validate_http_url(
            config.get(
                "opencode_server_url",
                "http://127.0.0.1:4096",
            )
            or "http://127.0.0.1:4096",
            "opencode_server_url",
        ),
        "opencode_username": str(config.get("opencode_username", "opencode")).strip() or "opencode",
        "opencode_password": str(config.get("opencode_password", "")),
        "opencode_task_timeout_seconds": opencode_task_timeout_seconds,
        "script_execution_timeout_seconds": script_execution_timeout_seconds,
        "project_workspace_root": str(config.get("project_workspace_root", "")).strip(),
        "project_template_dependency_source_root": str(
            config.get("project_template_dependency_source_root", "")
        ).strip(),
        "projects": projects,
        "default_project_key": default_project_key,
        "platform_database": platform_database,
        "database_baseline": database_baseline,
        "auth": auth,
        "error": None,
    }


__all__ = [
    "APP_DIR",
    "AUTH_USERNAME_PATTERN",
    "CONFIG_PATH",
    "COVERAGE_PROFILES",
    "DEFAULT_COVERAGE_PROFILE",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DATABASE_BASELINE_TIMEOUT_SECONDS",
    "DEFAULT_OPENCODE_TASK_TIMEOUT_SECONDS",
    "DEFAULT_SCRIPT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_TARGET_SYSTEM_CONFIG",
    "DISABLED_DATABASE_BASELINE_CONFIG",
    "MYSQL_IDENTIFIER_PATTERN",
    "PACKAGE_DIR",
    "PROJECT_KEY_PATTERN",
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_DISABLED",
    "format_timeout_seconds",
    "load_config",
    "parse_boolean",
    "parse_auth_config",
    "parse_database_baseline_config",
    "parse_plan_generation_config",
    "parse_platform_database_config",
    "parse_project_entry",
    "parse_project_key",
    "parse_project_opencode_config",
    "parse_project_path_segment",
    "parse_projects_config",
    "parse_target_system_config",
    "parse_timeout_seconds",
    "resolve_config_path",
    "validate_coverage_profile",
]
