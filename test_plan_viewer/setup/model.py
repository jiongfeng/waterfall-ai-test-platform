import re

from test_plan_viewer.setup.validation import (
    DEFAULT_SETUP_SCRIPT_TIMEOUT_SECONDS,
)


SETUP_BINDING_PRECEDENCE = {
    "project": 1,
    "test_suite": 2,
    "script": 3,
}
SETUP_SCRUBBED_ENVIRONMENT_FIELDS = frozenset(
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


def load_setup_environment_overrides(row, load_json_column):
    """Load literal overrides without executing a scrubbed v2 envelope.

    Credential values removed by the v2 scrub cannot be recovered. Treat
    that legacy envelope as an empty override map so its metadata never
    becomes child-process environment variables.
    """

    value = load_json_column(
        row.get("environment_json"),
        row.get("environment_overrides") or {},
    )
    if (
        isinstance(value, dict)
        and value.get("version") == 2
        and bool(
            SETUP_SCRUBBED_ENVIRONMENT_FIELDS.intersection(value)
            - {"version"}
        )
    ):
        return {}
    return value


def serialize_setup_script(row, load_json_column):
    if not row:
        return None
    return {
        "uid": row.get("script_uid") or row.get("uid"),
        "name": row.get("script_name") or row.get("name") or "",
        "description": row.get("description") or "",
        "script_content": row.get("script_content") or "",
        "working_directory": row.get("working_directory") or "",
        "environment_overrides": load_setup_environment_overrides(
            row,
            load_json_column,
        ),
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
        "script_snapshot": load_json_column(
            row.get("script_snapshot_json"),
            {},
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
    for mapping_name in ("environment_overrides",):
        mapping = script.get(mapping_name) or {}
        if not isinstance(mapping, dict):
            continue
        for value in mapping.values():
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
        if parent_key == "environment_overrides":
            return {key: "******" for key in value}
        result = {}
        for key, item in value.items():
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
