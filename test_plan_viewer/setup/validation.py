import uuid
from dataclasses import dataclass
from pathlib import Path

from test_plan_viewer.configuration import PROJECT_KEY_PATTERN


DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS = 300
MAX_SETUP_SCRIPT_TIMEOUT_SECONDS = 7200
SETUP_BINDING_TARGET_TYPES = {"project", "test_suite", "script"}


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
        "environment_overrides": dependencies.normalize_string_map(
            source.get("environment_overrides"),
            "environment_overrides",
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
