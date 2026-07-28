import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from test_plan_viewer.configuration import PROJECT_KEY_PATTERN
from test_plan_viewer.security.source import (
    assert_no_embedded_secrets,
)


DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS = 300
MAX_SETUP_SCRIPT_TIMEOUT_SECONDS = 7200
SETUP_BINDING_TARGET_TYPES = {"project", "test_suite", "script"}
SETUP_ENVIRONMENT_NAME_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)
SETUP_ENVIRONMENT_REFERENCE_PREFIX = "TARGET_"
SETUP_BASE_ENVIRONMENT_ALLOWLIST = frozenset({
    "COMSPEC",
    "HOME",
    "LANG",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
})


@dataclass(frozen=True)
class SetupValidationDependencies:
    resolve_working_directory: callable
    validate_uid: callable
    normalize_name: callable
    normalize_string_map: callable
    normalize_timeout: callable


def validate_setup_uid(
    value,
    field_name="uid",
    generate=False,
    *,
    pattern=PROJECT_KEY_PATTERN,
    uid_factory=None,
):
    text = str(value or "").strip()
    if not text and generate:
        return (uid_factory or (lambda: uuid.uuid4().hex))()
    if not text or not pattern.match(text):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, '.', '_' or '-'."
        )
    return text


def normalize_setup_name(value, field_name="name"):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    if len(text) > 255:
        raise ValueError(f"{field_name} is too long.")
    return text


def normalize_setup_string_map(value, field_name):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    result = {}
    for key, item in value.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError(f"{field_name} contains an empty key.")
        if "\x00" in normalized_key or "=" in normalized_key:
            raise ValueError(
                f"{field_name} contains an invalid environment key."
            )
        normalized_value = str(item if item is not None else "")
        if "\x00" in normalized_value:
            raise ValueError(
                f"{field_name} contains an invalid null character."
            )
        result[normalized_key] = normalized_value
    return result


def validate_setup_environment_reference(
    child_name,
    platform_name,
):
    if not SETUP_ENVIRONMENT_NAME_PATTERN.fullmatch(child_name):
        raise ValueError(
            "environment_refs contains an invalid subprocess "
            "environment variable name."
        )
    if (
        child_name.upper() in SETUP_BASE_ENVIRONMENT_ALLOWLIST
        or child_name.upper().startswith("LC_")
    ):
        raise ValueError(
            "environment_refs cannot override a protected subprocess "
            "environment variable."
        )
    if not SETUP_ENVIRONMENT_NAME_PATTERN.fullmatch(platform_name):
        raise ValueError(
            "environment_refs contains an invalid platform "
            "environment variable name."
        )
    if not platform_name.startswith(
        SETUP_ENVIRONMENT_REFERENCE_PREFIX
    ):
        raise ValueError(
            "environment_refs platform variable names must start "
            f"with {SETUP_ENVIRONMENT_REFERENCE_PREFIX}."
        )


def normalize_setup_environment_refs(value, normalize_string_map):
    references = normalize_string_map(value, "environment_refs")
    result = {}
    for child_name, platform_name in references.items():
        child_name = str(child_name).strip()
        platform_name = str(platform_name).strip()
        validate_setup_environment_reference(
            child_name,
            platform_name,
        )
        result[child_name] = platform_name
    return result


def normalize_setup_timeout(
    value,
    fallback=DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS,
    *,
    maximum=MAX_SETUP_SCRIPT_TIMEOUT_SECONDS,
):
    try:
        timeout = int(value if value not in (None, "") else fallback)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a positive integer.") from exc
    if timeout <= 0 or timeout > maximum:
        raise ValueError(
            f"timeout_seconds must be between 1 and {maximum}."
        )
    return timeout


def normalize_setup_script_payload(payload, existing, dependencies):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be an object.")
    if "environment_overrides" in payload:
        raise ValueError(
            "environment_overrides is no longer accepted; "
            "use environment_refs."
        )
    if (
        existing
        and existing.get("credentials_migration_required")
        and "environment_refs" not in payload
    ):
        raise ValueError(
            "旧版明文环境配置需要重新绑定 environment_refs 后才能保存。"
        )
    source = {**(existing or {}), **payload}
    script_content = source.get("script_content")
    if script_content is None:
        script_content = source.get("content")
    if not isinstance(script_content, str) or not script_content.strip():
        raise ValueError("script_content is required.")
    if "\x00" in script_content:
        raise ValueError(
            "script_content contains an invalid null character."
        )
    assert_no_embedded_secrets(
        script_content,
        source_label="Setup script",
    )
    working_directory = str(source.get("working_directory") or "").strip()
    dependencies.resolve_working_directory(working_directory)
    return {
        "uid": dependencies.validate_uid(
            source.get("uid") or source.get("script_uid"),
            "uid",
            generate=True,
        ),
        "name": dependencies.normalize_name(source.get("name")),
        "description": str(source.get("description") or "").strip()[:1024],
        "script_content": script_content,
        "working_directory": working_directory,
        "environment_refs": normalize_setup_environment_refs(
            source.get("environment_refs"),
            dependencies.normalize_string_map,
        ),
        "timeout_seconds": dependencies.normalize_timeout(
            source.get("timeout_seconds")
        ),
        "concurrency_key": str(
            source.get("concurrency_key") or ""
        ).strip()[:255],
        "enabled": bool(source.get("enabled", True)),
    }


def normalize_setup_binding_payload(
    payload,
    existing,
    dependencies,
    *,
    target_types=SETUP_BINDING_TARGET_TYPES,
):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be an object.")
    source = {**(existing or {}), **payload}
    scope_type = str(
        source.get("scope_type") or source.get("target_type") or ""
    ).strip()
    if scope_type not in target_types:
        raise ValueError(
            "scope_type must be 'project', 'test_suite' or 'script'."
        )
    scope_key = str(
        source.get("scope_key") or source.get("target_key") or ""
    ).strip()
    if not scope_key:
        raise ValueError("scope_key is required.")
    if len(scope_key) > 512:
        raise ValueError("scope_key is too long.")
    try:
        priority = int(source.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("priority must be an integer.") from exc
    return {
        "uid": dependencies.validate_uid(
            source.get("uid") or source.get("binding_uid"),
            "uid",
            generate=True,
        ),
        "scope_type": scope_type,
        "scope_key": scope_key,
        "scope_label": str(
            source.get("scope_label") or ""
        ).strip()[:255],
        "script_uid": dependencies.validate_uid(
            source.get("script_uid"),
            "script_uid",
        ),
        "priority": priority,
        "enabled": bool(source.get("enabled", True)),
    }


def resolve_setup_working_directory(value, project_root):
    project_root = Path(project_root).resolve(strict=False)
    text = str(value or "").strip()
    candidate = project_root if not text else Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(
            "working_directory 必须位于当前项目目录内。"
        ) from exc
    if not resolved.is_dir():
        raise ValueError(f"working_directory 不存在或不是目录：{resolved}")
    return resolved
