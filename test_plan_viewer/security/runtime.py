"""Redact credentials from persisted and streamed runtime values."""

import json
import os
import re

from test_plan_viewer.execution import environment as execution_environment
from test_plan_viewer.process_output import normalize_process_output


PUBLIC_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:(?:^|[_-])(?:api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization|cookie|credentials?|"
    r"password|passwd|pwd|passphrase|secret|token|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"session[_-]?secret)$|[a-z0-9]*(?:password|passwd|pwd|"
    r"passphrase|authToken|secretKey|apiKey|accessKey|"
    r"privateKey|accessToken|refreshToken|clientSecret|"
    r"sessionSecret))$"
)
RUNTIME_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:(?:^|[_-])(?:api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization|cookie|credentials?|"
    r"password|passwd|pwd|passphrase|secret|token|username|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"session[_-]?secret)$|[a-z0-9]*(?:password|passwd|pwd|"
    r"passphrase|authToken|secretKey|apiKey|accessKey|"
    r"privateKey|accessToken|refreshToken|clientSecret|"
    r"sessionSecret|username))$"
)
DIAGNOSTIC_TEXT_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[_-])(?:error|errors|message|messages|detail|"
    r"details|reason|reasons|log|logs|output|stdout|stderr|"
    r"traceback|exception|exceptions|command|prompt|text|raw|"
    r"data|delta|tail)(?:$|[_-])"
)
_SENSITIVE_KEY_FRAGMENT = (
    r"(?:api[_-]?key|access[_-]?key|private[_-]?key|"
    r"authorization|cookie|credential|password|passwd|pwd|"
    r"passphrase|secret|session|token|username)"
)
_JSON_SECRET_PATTERN = re.compile(
    (
        r"(?is)(?P<prefix>[\"'][^\"']*"
        + _SENSITIVE_KEY_FRAGMENT
        + r"[^\"']*[\"']\s*:\s*)"
        r"(?P<quote>[\"'])(?:\\.|[^\\])*?(?P=quote)"
    )
)
_HEADER_SECRET_PATTERN = re.compile(
    (
        r"(?im)(?P<prefix>\b(?:authorization|"
        r"proxy-authorization|cookie|set-cookie)\s*:\s*)"
        r"[^\r\n]+"
    )
)
_BEARER_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>\bbearer\s+)[A-Za-z0-9._~+/=-]+"
)
_BASIC_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>\bbasic\s+)[A-Za-z0-9._~+/=-]+"
)
_SECRET_LABEL_FRAGMENT = (
    r"[A-Za-z0-9_.-]*(?:api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization|cookie|credential|"
    r"password|passwd|pwd|passphrase|secret|token)"
)
_LABELED_QUOTED_SECRET_PATTERN = re.compile(
    (
        r"(?is)(?P<prefix>\b"
        + _SECRET_LABEL_FRAGMENT
        + r"\b\s*[=:]\s*)"
        r"(?P<quote>[\"'])(?:\\.|[^\\])*?(?P=quote)"
    )
)
_LABELED_SECRET_PATTERN = re.compile(
    (
        r"(?i)(?P<prefix>\b"
        + _SECRET_LABEL_FRAGMENT
        + r"\b\s*[=:]\s*)"
        r"(?P<value>\$\{[^\r\n,;\]]+|[^\r\n,;}\]]+)"
    )
)
_PEM_PRIVATE_KEY_PATTERN = re.compile(
    (
        r"(?is)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"
        r".*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----"
    )
)
_PURE_ENV_REFERENCE_PATTERN = re.compile(
    r"""(?ix)^(?:
    process\.env\.[A-Za-z_][A-Za-z0-9_]*
    (?:\s*(?:\?\?|\|\|)\s*(?:""|'')\s*)?
    |\$\{[A-Za-z_][A-Za-z0-9_]*\}
    |env://[A-Za-z_][A-Za-z0-9_]*
    )$"""
)


def _redact_json_secret(match):
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}******{quote}"


def _redact_labeled_secret(match):
    value = match.group("value")
    if _PURE_ENV_REFERENCE_PATTERN.fullmatch(value.strip()):
        return match.group(0)
    return f"{match.group('prefix')}******"


def _redact_text_patterns(text):
    text = _JSON_SECRET_PATTERN.sub(
        _redact_json_secret,
        text,
    )
    text = _HEADER_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}******",
        text,
    )
    text = _BEARER_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}******",
        text,
    )
    text = _BASIC_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}******",
        text,
    )
    text = _LABELED_QUOTED_SECRET_PATTERN.sub(
        _redact_json_secret,
        text,
    )
    text = _LABELED_SECRET_PATTERN.sub(
        _redact_labeled_secret,
        text,
    )
    text = _PEM_PRIVATE_KEY_PATTERN.sub("******", text)
    text = re.sub(
        r"(?i)(//[^:\s/@]+:)([^@\s/]+)(@)",
        r"\1******\3",
        text,
    )
    return re.sub(
        r"([A-Za-z0-9_.$-]+)/(\"[^\"]+\"|'[^']+'|[^\s@]+)@",
        r"\1/******@",
        text,
    )


def redact_sensitive_text(value, *configs, limit=None):
    text = (
        normalize_process_output(value)
        if isinstance(value, (str, bytes))
        else str(value or "")
    )
    text = execution_environment.redact_concrete_secrets(
        text,
        configs,
        os.environ,
    )
    stripped = text.strip()
    parsed_json = False
    if (
        stripped.startswith(("{", "["))
        and stripped.endswith(("}", "]"))
    ):
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            pass
        else:
            text = json.dumps(
                redact_runtime_value(parsed),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            parsed_json = True
    if not parsed_json:
        text = _redact_text_patterns(text)
    if limit and len(text) > limit:
        omitted = len(text) - limit
        return (
            f"{text[:limit]}\n"
            f"...[已截断 {omitted} 个字符]"
        )
    return text


def redact_runtime_value(value, parent_key=""):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if RUNTIME_SENSITIVE_KEY_PATTERN.search(key_text):
                result[key] = (
                    "******"
                    if item not in (None, "")
                    else item
                )
            else:
                result[key] = redact_runtime_value(
                    item,
                    key_text,
                )
        return result
    if isinstance(value, list):
        return [
            redact_runtime_value(item, parent_key)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_runtime_value(item, parent_key)
            for item in value
        )
    if isinstance(value, str):
        if (
            not parent_key
            or DIAGNOSTIC_TEXT_KEY_PATTERN.search(parent_key)
        ):
            return redact_sensitive_text(value)
        return value
    return value


def redact_natural_language_value(value):
    """Recursively redact free-form model output before persistence."""

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if RUNTIME_SENSITIVE_KEY_PATTERN.search(str(key)):
                result[key] = (
                    "******"
                    if item not in (None, "")
                    else item
                )
            else:
                result[key] = redact_natural_language_value(item)
        return result
    if isinstance(value, list):
        return [
            redact_natural_language_value(item)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_natural_language_value(item)
            for item in value
        )
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_public_response_value(value, parent_key=""):
    """Redact API output without hiding ordinary account usernames."""

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if PUBLIC_SENSITIVE_KEY_PATTERN.search(str(key)):
                result[key] = (
                    "******"
                    if item not in (None, "")
                    else item
                )
            else:
                result[key] = redact_public_response_value(
                    item,
                    str(key),
                )
        return result
    if isinstance(value, list):
        return [
            redact_public_response_value(item, parent_key)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_public_response_value(item, parent_key)
            for item in value
        )
    if isinstance(value, str):
        if DIAGNOSTIC_TEXT_KEY_PATTERN.search(parent_key):
            return redact_sensitive_text(value)
        return value
    return value


__all__ = [
    "PUBLIC_SENSITIVE_KEY_PATTERN",
    "RUNTIME_SENSITIVE_KEY_PATTERN",
    "redact_natural_language_value",
    "redact_public_response_value",
    "redact_runtime_value",
    "redact_sensitive_text",
]
