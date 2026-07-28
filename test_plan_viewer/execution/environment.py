"""Least-privilege environments and output redaction for test processes."""

import os
import re


EXECUTION_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "PATH",
        "PATHEXT",
        "PLAYWRIGHT_BROWSERS_PATH",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    }
)
EXECUTION_EXTRA_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE",
        "TEST_PLAN_VIEWER_OUTPUT_DIR",
    }
)
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|private[_-]?key|"
    r"authorization|cookie|credential|password|passwd|pwd|"
    r"secret|session|token|username)"
)


def require_test_execution_enabled(source=None):
    """Require an explicit opt-in before running repository code."""

    source = os.environ if source is None else source
    enabled = str(
        source.get("PLATFORM_ALLOW_TEST_EXECUTION") or ""
    ).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "Test execution is disabled by default. Set "
            "PLATFORM_ALLOW_TEST_EXECUTION=true only for trusted "
            "single-tenant projects."
        )


def validate_target_credential_environment_name(name):
    """Keep target credentials in a dedicated environment namespace."""

    normalized = str(name or "").strip()
    if not normalized:
        return ""
    if not normalized.startswith("TARGET_"):
        raise ValueError(
            "target credential environment variables must use the "
            "dedicated TARGET_ prefix."
        )
    return normalized


def build_playwright_environment(
    source,
    target_system,
    *,
    extra=None,
):
    """Build the minimum environment required by a Playwright child."""

    source = os.environ if source is None else source
    target_system = target_system or {}
    environment = build_isolated_tool_environment(source)

    base_url = str(target_system.get("base_url") or "").strip()
    if base_url:
        environment["PLAYWRIGHT_BASE_URL"] = base_url

    for field_name in ("username_env", "password_env"):
        name = validate_target_credential_environment_name(
            target_system.get(field_name)
        )
        if name and name in source:
            environment[name] = str(source[name])

    for key, value in (extra or {}).items():
        if key not in EXECUTION_EXTRA_ENVIRONMENT_ALLOWLIST:
            raise ValueError(
                f"unsupported Playwright environment override: {key}"
            )
        if value is not None:
            environment[key] = str(value)
    return environment


def build_isolated_tool_environment(source=None):
    """Build a base environment for trusted platform tooling."""

    source = os.environ if source is None else source
    return {
        str(key): str(value)
        for key, value in source.items()
        if key in EXECUTION_ENVIRONMENT_ALLOWLIST
        or key.startswith("LC_")
    }


def _collect_config_secrets(value, environment, secrets, parent_key=""):
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower().endswith("_env"):
                referenced_name = str(item or "").strip()
                referenced_value = environment.get(referenced_name)
                if referenced_value:
                    secrets.add(str(referenced_value))
            elif SENSITIVE_KEY_PATTERN.search(key_text):
                if isinstance(item, (str, int, float)) and item:
                    secrets.add(str(item))
            _collect_config_secrets(
                item,
                environment,
                secrets,
                key_text,
            )
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_config_secrets(
                item,
                environment,
                secrets,
                parent_key,
            )


def collect_concrete_secret_values(configs=(), environment=None):
    """Collect concrete credential values without returning their names."""

    environment = os.environ if environment is None else environment
    secrets = {
        str(value)
        for key, value in environment.items()
        if value
        and (
            str(key).startswith("TARGET_")
            or SENSITIVE_KEY_PATTERN.search(str(key))
        )
    }
    for config in configs:
        _collect_config_secrets(
            config,
            environment,
            secrets,
        )
    return {
        secret
        for secret in secrets
        if len(secret) >= 2
    }


def redact_concrete_secrets(value, configs=(), environment=None):
    """Replace every known concrete credential value in text."""

    text = str(value or "")
    secrets = collect_concrete_secret_values(
        configs,
        environment,
    )
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "******")
    return text
