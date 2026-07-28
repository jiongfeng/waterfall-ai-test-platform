import json
import re

from test_plan_viewer.setup.validation import (
    DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS,
    SETUP_ENVIRONMENT_NAME_PATTERN,
    validate_setup_environment_reference,
)


SETUP_BINDING_PRECEDENCE = {
    "project": 1,
    "test_suite": 2,
    "script": 3,
}
SETUP_ENVIRONMENT_ENVELOPE_FIELDS = frozenset(
    {
        "version",
        "environment_refs",
        "credentials_migration_required",
        "legacy_environment_keys",
    }
)


class SetupPreparationError(RuntimeError):
    def __init__(self, message, summary=None):
        super().__init__(message)
        self.summary = summary or {}


def legacy_setup_environment_keys(value):
    if isinstance(value, dict):
        candidates = value.keys()
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = ()
    return sorted(
        {
            str(key).strip()
            for key in candidates
            if str(key).strip()
        }
    )


def _safe_setup_environment_keys(value):
    return sorted(
        {
            str(key).strip()
            for key in legacy_setup_environment_keys(value)
            if SETUP_ENVIRONMENT_NAME_PATTERN.fullmatch(
                str(key).strip()
            )
        }
    )


def _normalize_v2_setup_environment_envelope(value):
    if (
        not isinstance(value, dict)
        or value.get("version") != 2
    ):
        return None, False
    version_is_canonical = (
        type(value.get("version")) is int
    )

    references_value = value.get("environment_refs")
    references = {}
    invalid_reference_keys = []
    references_are_canonical = isinstance(
        references_value,
        dict,
    )
    if isinstance(references_value, dict):
        for raw_child_name, raw_platform_name in (
            references_value.items()
        ):
            child_name = str(raw_child_name).strip()
            platform_name = str(raw_platform_name).strip()
            if (
                not isinstance(raw_child_name, str)
                or raw_child_name != child_name
                or not isinstance(raw_platform_name, str)
                or raw_platform_name != platform_name
            ):
                references_are_canonical = False
            try:
                validate_setup_environment_reference(
                    child_name,
                    platform_name,
                )
            except ValueError:
                references_are_canonical = False
                if SETUP_ENVIRONMENT_NAME_PATTERN.fullmatch(
                    child_name
                ):
                    invalid_reference_keys.append(child_name)
                continue
            references[child_name] = platform_name

    keys = set(value)
    base_keys = {"version", "environment_refs"}
    migration_keys = {
        *base_keys,
        "credentials_migration_required",
        "legacy_environment_keys",
    }
    metadata_keys = _safe_setup_environment_keys(
        value.get("legacy_environment_keys")
    )
    raw_metadata_keys = value.get("legacy_environment_keys")
    metadata_is_canonical = (
        isinstance(raw_metadata_keys, list)
        and raw_metadata_keys == metadata_keys
    )
    canonical = bool(
        version_is_canonical
        and references_are_canonical
        and (
            keys == base_keys
            or (
                keys == migration_keys
                and value.get(
                    "credentials_migration_required"
                )
                is True
                and metadata_is_canonical
            )
        )
    )
    if canonical:
        return value, True

    legacy_keys = set(metadata_keys)
    legacy_keys.update(invalid_reference_keys)
    for raw_key, item in value.items():
        key = str(raw_key).strip()
        if key in SETUP_ENVIRONMENT_ENVELOPE_FIELDS:
            continue
        if key == "environment_overrides":
            legacy_keys.update(
                _safe_setup_environment_keys(item)
            )
        elif SETUP_ENVIRONMENT_NAME_PATTERN.fullmatch(key):
            legacy_keys.add(key)
    return {
        "version": 2,
        "environment_refs": references,
        "credentials_migration_required": True,
        "legacy_environment_keys": sorted(legacy_keys),
    }, False


def build_setup_environment_scrub_envelope(value):
    if value in (None, ""):
        return None
    parse_failed = False
    try:
        parsed = (
            json.loads(value)
            if isinstance(value, str)
            else value
        )
    except (TypeError, json.JSONDecodeError):
        parsed = None
        parse_failed = True
    safe_v2_envelope, v2_is_canonical = (
        _normalize_v2_setup_environment_envelope(parsed)
    )
    if safe_v2_envelope is not None:
        return None if v2_is_canonical else safe_v2_envelope
    legacy_keys = _safe_setup_environment_keys(parsed)
    migration_required = bool(
        legacy_keys
        or parse_failed
        or parsed not in (None, {})
    )
    envelope = {
        "version": 2,
        "environment_refs": {},
    }
    if migration_required:
        envelope.update(
            {
                "credentials_migration_required": True,
                "legacy_environment_keys": legacy_keys,
            }
        )
    return envelope


def deserialize_setup_environment(value):
    safe_v2_envelope, _v2_is_canonical = (
        _normalize_v2_setup_environment_envelope(value)
    )
    if safe_v2_envelope is not None:
        return {
            "environment_refs": dict(
                safe_v2_envelope["environment_refs"]
            ),
            "credentials_migration_required": bool(
                safe_v2_envelope.get(
                    "credentials_migration_required"
                )
            ),
            "legacy_environment_keys": list(
                safe_v2_envelope.get(
                    "legacy_environment_keys"
                )
                or []
            ),
        }

    legacy_keys = legacy_setup_environment_keys(value)
    return {
        "environment_refs": {},
        "credentials_migration_required": bool(legacy_keys),
        "legacy_environment_keys": legacy_keys,
    }


def sanitize_setup_snapshot(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "environment_overrides":
                legacy_keys = legacy_setup_environment_keys(item)
                result["environment_refs"] = {}
                result["credentials_migration_required"] = bool(
                    legacy_keys
                )
                result["legacy_environment_keys"] = legacy_keys
                continue
            if key == "_resolved_environment_values":
                continue
            result[key] = sanitize_setup_snapshot(item)
        return result
    if isinstance(value, list):
        return [sanitize_setup_snapshot(item) for item in value]
    return value


def serialize_setup_script(row, load_json_column):
    if not row:
        return None
    if (
        row.get("environment_json") is None
        and "environment_refs" in row
    ):
        environment_value = {
            "version": 2,
            "environment_refs": row.get("environment_refs"),
        }
    else:
        environment_value = load_json_column(
            row.get("environment_json"),
            row.get("environment_overrides") or {},
        )
    environment = deserialize_setup_environment(environment_value)
    return {
        "uid": row.get("script_uid") or row.get("uid"),
        "name": row.get("script_name") or row.get("name") or "",
        "description": row.get("description") or "",
        "script_content": row.get("script_content") or "",
        "working_directory": row.get("working_directory") or "",
        **environment,
        "timeout_seconds": int(
            row.get("timeout_seconds")
            or DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS
        ),
        "concurrency_key": row.get("concurrency_key") or "",
        "enabled": bool(
            row.get("script_enabled", row.get("enabled", True))
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get(
            "script_updated_at",
            row.get("updated_at"),
        ),
    }


def serialize_setup_binding(row):
    return {
        "binding_id": row.get("binding_id"),
        "uid": row.get("binding_uid") or row.get("uid"),
        "scope_type": row.get("scope_type") or row.get("target_type"),
        "scope_key": row.get("scope_key") or row.get("target_key") or "",
        "scope_label": row.get("scope_label") or "",
        "script_uid": row.get("script_uid") or "",
        "script_name": row.get("script_name") or "",
        "priority": int(row.get("priority") or 0),
        "enabled": bool(row.get("enabled", True)),
        "updated_at": row.get("updated_at"),
    }


def serialize_setup_run(row, load_json_column):
    started_at = row.get("started_at")
    finished_at = row.get("finished_at")
    return {
        "uid": row.get("run_uid") or row.get("uid"),
        "parent_run_id": row.get("parent_run_id") or "",
        "target_type": row.get("target_type") or "",
        "target_key": row.get("target_key") or "",
        "script_uid": row.get("script_uid") or "",
        "script_name": row.get("script_name") or "",
        "status": row.get("status") or "",
        "exit_code": row.get("exit_code"),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": (
            row.get("duration_ms")
            if row.get("duration_ms") is not None
            else (
                (finished_at - started_at)
                if started_at and finished_at
                else None
            )
        ),
        "output_summary": row.get("output_summary") or "",
        "error": row.get("error") or "",
        "script_snapshot": sanitize_setup_snapshot(
            load_json_column(
                row.get("script_snapshot_json"),
                {},
            )
        ),
    }


def select_setup_binding(
    bindings,
    targets,
    *,
    precedence=SETUP_BINDING_PRECEDENCE,
):
    target_positions = {
        (item["scope_type"], item["scope_key"]): index
        for index, item in enumerate(targets)
    }
    candidates = []
    for binding in bindings or []:
        key = (binding.get("scope_type"), binding.get("scope_key"))
        if not binding.get("enabled", True) or key not in target_positions:
            continue
        candidates.append(binding)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            precedence.get(item.get("scope_type"), 0),
            int(item.get("priority") or 0),
            -target_positions[
                (item.get("scope_type"), item.get("scope_key"))
            ],
            int(item.get("binding_id") or 0),
            str(item.get("uid") or ""),
        ),
    )


def build_setup_targets(
    project,
    module_name=None,
    filename=None,
    suite_uid=None,
    filenames=None,
    items=None,
):
    targets = []
    if module_name and filename:
        targets.append(
            {
                "scope_type": "script",
                "scope_key": f"{module_name}/{filename}",
            }
        )
    elif module_name and isinstance(filenames, list):
        targets.extend(
            {
                "scope_type": "script",
                "scope_key": f"{module_name}/{item}",
            }
            for item in filenames
        )
    if isinstance(items, list):
        targets.extend(
            {
                "scope_type": "script",
                "scope_key": (
                    f"{item.get('module_name')}/{item.get('filename')}"
                ),
            }
            for item in items
            if (
                isinstance(item, dict)
                and item.get("module_name")
                and item.get("filename")
            )
        )
    if suite_uid:
        targets.append(
            {
                "scope_type": "test_suite",
                "scope_key": str(suite_uid),
            }
        )
    targets.append(
        {
            "scope_type": "project",
            "scope_key": project.get("project_key") or "default",
        }
    )
    return targets


def setup_secret_values(script):
    values = set()
    for value in script.get("_resolved_environment_values") or ():
        text = "" if value is None else str(value)
        if text:
            values.add(text)
    return values


def redact_setup_text(
    value,
    script=None,
    limit=4000,
    *,
    redact_sensitive_text,
    secret_values=setup_secret_values,
):
    text = redact_sensitive_text(value, limit=None)
    for secret in sorted(
        secret_values(script or {}),
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, "******")
    text = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
        r"\1******",
        text,
    )
    text = re.sub(
        (
            r"(?i)(token|secret|api[-_]?key|authorization|cookie)"
            r"(\s*[=:]\s*)([^\s&;,]+)"
        ),
        r"\1\2******",
        text,
    )
    if limit and len(text) > limit:
        text = f"{text[-limit:]}\n...[仅保留末尾 {limit} 个字符]"
    return text


def redact_setup_snapshot(value, parent_key="", *, redact_text):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "environment_overrides":
                legacy_keys = legacy_setup_environment_keys(item)
                result["environment_refs"] = {}
                result["credentials_migration_required"] = bool(
                    legacy_keys
                )
                result["legacy_environment_keys"] = legacy_keys
                continue
            if key == "_resolved_environment_values":
                continue
            if re.search(
                (
                    r"(?i)(password|passwd|pwd|secret|token|authorization|"
                    r"api[-_]?key|cookie)"
                ),
                str(key),
            ):
                result[key] = (
                    "******" if item not in (None, "") else item
                )
            else:
                result[key] = redact_setup_snapshot(
                    item,
                    str(key),
                    redact_text=redact_text,
                )
        return result
    if isinstance(value, list):
        return [
            redact_setup_snapshot(
                item,
                parent_key,
                redact_text=redact_text,
            )
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(value, limit=None)
    return value
