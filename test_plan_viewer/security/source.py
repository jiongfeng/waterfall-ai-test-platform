"""Reject likely plaintext credentials before executable source is stored."""

import os
import re


SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)
    (?P<label_quote>["']?)
    (?P<label>[A-Za-z_$][A-Za-z0-9_.$-]{0,127})
    (?P=label_quote)
    \s*[:=]\s*
    (?P<quote>["'`])
    (?P<value>[^"'`\r\n]*)
    (?P=quote)
    """
)
ENV_LITERAL_FALLBACK_PATTERN = re.compile(
    r"""(?ix)
    process\.env\.[A-Za-z_][A-Za-z0-9_]*
    \s*(?:\|\||\?\?)\s*
    (?P<quote>["'`])
    (?P<value>[^"'`\r\n]*)
    (?P=quote)
    """
)
PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"(?is)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"
)
PASSWORD_FILL_PATTERN = re.compile(
    r"""(?ix)
    (password|passwd|pwd|密码)
    [^\r\n]{0,160}?
    \.(fill|type)\(
    \s*(?P<quote>["'`])
    (?P<value>[^"'`\r\n]{2,})
    (?P=quote)
    """
)
PAGE_FILL_PATTERN = re.compile(
    r"""(?ix)
    \bpage\.fill\(
    \s*(?P<selector_quote>["'`])
    (?P<selector>[^"'`\r\n]*(password|passwd|pwd)[^"'`\r\n]*)
    (?P=selector_quote)
    \s*,\s*
    (?P<value_quote>["'`])
    (?P<value>[^"'`\r\n]{2,})
    (?P=value_quote)
    """
)
BEARER_LITERAL_PATTERN = re.compile(
    r"""(?ix)
    \bbearer\s+
    (?P<value>[A-Za-z0-9._~+/=-]{8,})
    """
)
SAFE_LITERAL_VALUES = frozenset(
    {
        "${password}",
        "${token}",
        "<password>",
        "<token>",
        "******",
    }
)
PURE_ENV_REFERENCE_PATTERN = re.compile(
    r"""(?ix)^(?:
    process\.env\.[A-Za-z_][A-Za-z0-9_]*
    |\$\{[A-Za-z_][A-Za-z0-9_]*\}
    |env://[A-Za-z_][A-Za-z0-9_]*
    )$"""
)
SENSITIVE_IDENTIFIER_SUFFIXES = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "token",
    "secret",
    "apikey",
    "accesskey",
    "privatekey",
    "secretkey",
    "authorization",
    "credential",
    "credentials",
    "username",
)


def _is_safe_literal(value):
    normalized = str(value or "").strip().lower()
    return (
        not normalized
        or normalized in SAFE_LITERAL_VALUES
        or PURE_ENV_REFERENCE_PATTERN.fullmatch(normalized)
    )


def _is_sensitive_identifier(value):
    normalized = re.sub(
        r"[^a-z0-9]+",
        "",
        str(value or "").lower(),
    )
    return any(
        normalized.endswith(suffix)
        for suffix in SENSITIVE_IDENTIFIER_SUFFIXES
    )


def _fallback_has_sensitive_context(content, start):
    prefix = content[max(0, start - 320):start]
    boundary = max(
        prefix.rfind(";"),
        prefix.rfind("{"),
        prefix.rfind("}"),
    )
    context = prefix[boundary + 1:]
    identifiers = re.findall(
        r"[A-Za-z_$][A-Za-z0-9_.$-]{0,127}",
        context,
    )
    return (
        any(
            _is_sensitive_identifier(identifier)
            for identifier in identifiers
        )
        or "密码" in context
    )


def find_embedded_secret_reasons(content, environment=None):
    """Return stable reason codes for likely embedded credentials."""

    if not isinstance(content, str):
        raise TypeError("Executable source must be a string.")
    environment = os.environ if environment is None else environment
    reasons = set()

    for name, value in environment.items():
        if (
            str(name).startswith("TARGET_")
            and value
            and len(str(value)) >= 2
            and str(value) in content
        ):
            reasons.add("known-target-credential")

    for match in SECRET_ASSIGNMENT_PATTERN.finditer(content):
        if (
            _is_sensitive_identifier(match.group("label"))
            and not _is_safe_literal(match.group("value"))
        ):
            reasons.add("secret-assignment")

    for match in ENV_LITERAL_FALLBACK_PATTERN.finditer(content):
        if (
            not _is_safe_literal(match.group("value"))
            and _fallback_has_sensitive_context(
                content,
                match.start(),
            )
        ):
            reasons.add("secret-fallback")

    for pattern, reason in (
        (PASSWORD_FILL_PATTERN, "password-fill"),
        (PAGE_FILL_PATTERN, "password-fill"),
        (BEARER_LITERAL_PATTERN, "bearer-token"),
    ):
        for match in pattern.finditer(content):
            if not _is_safe_literal(match.group("value")):
                reasons.add(reason)
    if PEM_PRIVATE_KEY_PATTERN.search(content):
        reasons.add("private-key")
    return sorted(reasons)


def assert_no_embedded_secrets(
    content,
    *,
    environment=None,
    source_label="Executable source",
):
    """Raise before saving source that appears to contain credentials."""

    reasons = find_embedded_secret_reasons(
        content,
        environment,
    )
    if reasons:
        raise ValueError(
            f"{source_label} appears to contain a plaintext credential "
            f"({', '.join(reasons)}). Use process.env.TARGET_* or "
            "environment_refs instead."
        )
