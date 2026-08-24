#!/usr/bin/env python3
"""Validate and stage the Docker deployment configuration without exposing it."""

from __future__ import annotations

import argparse
import json
import os
import re
from secrets import token_urlsafe
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


MAX_CONFIG_BYTES = 64 * 1024
PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
COMPOSE_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "password-here",
    "password_here",
    "replace-me",
    "replace_me",
    "your-password",
    "your_password",
}
QUICKSTART_SECRET_NAMES = (
    "PLATFORM_SESSION_SECRET",
    "PLATFORM_ADMIN_PASSWORD",
    "PLATFORM_DB_PASSWORD",
    "OPENCODE_SERVER_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
)
QUICKSTART_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")


class ConfigError(ValueError):
    """A safe-to-display configuration error."""


def _require_nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _require_secret(value: object, field: str) -> None:
    secret = _require_nonempty_text(value, field)
    if secret.strip().lower() in PLACEHOLDER_VALUES:
        raise ConfigError(f"{field} still contains a placeholder value")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ConfigError(f"non-standard JSON number is not allowed: {value}")


def parse_and_validate(raw: bytes) -> dict[str, object]:
    config = parse_json_object(raw)

    _require_nonempty_text(
        config.get("project_workspace_root"),
        "project_workspace_root",
    )
    _require_nonempty_text(
        config.get("project_template_dependency_source_root"),
        "project_template_dependency_source_root",
    )

    opencode_url = _require_nonempty_text(
        config.get("opencode_server_url"),
        "opencode_server_url",
    )
    parsed_url = urlsplit(opencode_url)
    if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.hostname:
        raise ConfigError("opencode_server_url must be an absolute HTTP(S) URL")
    _require_nonempty_text(config.get("opencode_username"), "opencode_username")
    _require_secret(config.get("opencode_password"), "opencode_password")

    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ConfigError("projects must be a non-empty array")

    project_keys: set[str] = set()
    for index, project in enumerate(projects):
        field = f"projects[{index}]"
        if not isinstance(project, dict):
            raise ConfigError(f"{field} must be an object")
        key = _require_nonempty_text(project.get("key"), f"{field}.key")
        if not PROJECT_KEY_PATTERN.fullmatch(key):
            raise ConfigError(
                f"{field}.key must contain only letters, numbers, '.', '_' or '-'"
            )
        if key in project_keys:
            raise ConfigError(f"projects contains duplicate key: {key}")
        project_keys.add(key)
        _require_nonempty_text(project.get("name"), f"{field}.name")
        _require_nonempty_text(
            project.get("playwright_project_root"),
            f"{field}.playwright_project_root",
        )

    default_project_key = _require_nonempty_text(
        config.get("default_project_key"),
        "default_project_key",
    )
    if default_project_key not in project_keys:
        raise ConfigError("default_project_key must reference an entry in projects")

    configured_language = config.get("default_project_language")
    if configured_language is not None:
        default_project_language = _require_nonempty_text(
            configured_language,
            "default_project_language",
        )
        if default_project_language.lower() not in {"zh-cn", "en"}:
            raise ConfigError(
                "default_project_language must be 'zh-CN' or 'en'"
            )

    database = config.get("platform_database")
    if not isinstance(database, dict):
        raise ConfigError("platform_database must be an object")
    if database.get("enabled") is True:
        if str(database.get("type", "mysql")).lower() != "mysql":
            raise ConfigError("platform_database.type must be 'mysql'")
        for field in ("host", "user", "database"):
            _require_nonempty_text(
                database.get(field),
                f"platform_database.{field}",
            )
        _require_secret(
            database.get("password"),
            "platform_database.password",
        )

    return config


def parse_json_object(raw: bytes) -> dict[str, object]:
    if not raw:
        raise ConfigError("configuration file is empty")
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError(
            f"configuration exceeds the {MAX_CONFIG_BYTES}-byte deployment limit"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("configuration must be valid UTF-8") from exc
    try:
        config = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_number,
        )
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(config, dict):
        raise ConfigError("configuration root must be a JSON object")
    return config


def read_secure_source(path: Path) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError(f"cannot securely open configuration file: {path}") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError(f"configuration is not a regular file: {path}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o600:
            raise ConfigError(
                f"configuration mode must be 0600, found {mode:04o}: {path}"
            )
        if metadata.st_size <= 0:
            raise ConfigError(f"configuration file is empty: {path}")
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(
                f"configuration exceeds the {MAX_CONFIG_BYTES}-byte deployment limit: {path}"
            )

        chunks: list[bytes] = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError(
                f"configuration exceeds the {MAX_CONFIG_BYTES}-byte deployment limit: {path}"
            )
        return raw
    finally:
        os.close(descriptor)


def read_secure_env_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError(f"cannot securely open environment file: {path}") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError(f"environment is not a regular file: {path}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o600:
            raise ConfigError(
                f"environment mode must be 0600, found {mode:04o}: {path}"
            )
        if metadata.st_size <= 0:
            raise ConfigError(f"environment file is empty: {path}")
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(
                f"environment exceeds the {MAX_CONFIG_BYTES}-byte deployment limit: {path}"
            )
        chunks: list[bytes] = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError(
                f"environment exceeds the {MAX_CONFIG_BYTES}-byte deployment limit: {path}"
            )
        if b"\x00" in raw:
            raise ConfigError("environment file must not contain NUL bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError("environment file must be valid UTF-8") from exc
        return raw
    finally:
        os.close(descriptor)


def validate_env_file(path: Path) -> None:
    read_secure_env_file(path)


def _atomic_write_private_file(path: Path, payload: bytes) -> None:
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise ConfigError(f"cannot securely update private file: {path}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def initialize_quickstart_config(config_path: Path, environment_path: Path) -> None:
    environment_text = read_secure_env_file(environment_path).decode("utf-8")
    secret_values: dict[str, str] = {}
    updated_lines: list[str] = []

    for line_number, raw_line in enumerate(environment_text.splitlines(), start=1):
        key, separator, value = raw_line.partition("=")
        if key not in QUICKSTART_SECRET_NAMES:
            updated_lines.append(raw_line)
            continue
        if not separator:
            raise ConfigError(
                f"{key} on environment line {line_number} has no '='"
            )
        if key in secret_values:
            raise ConfigError(f"environment contains duplicate {key} entries")
        value = value.strip()
        if value and not QUICKSTART_SECRET_PATTERN.fullmatch(value):
            raise ConfigError(
                f"{key} must be unquoted URL-safe text before initialization"
            )
        if not value:
            value = token_urlsafe(36)
        secret_values[key] = value
        updated_lines.append(f"{key}={value}")

    missing = [
        name for name in QUICKSTART_SECRET_NAMES if name not in secret_values
    ]
    if missing:
        raise ConfigError(
            "environment is missing required quickstart secret fields: "
            + ", ".join(missing)
        )

    config = parse_json_object(read_secure_source(config_path))
    database = config.get("platform_database")
    if not isinstance(database, dict):
        raise ConfigError("platform_database must be an object")
    config["opencode_password"] = secret_values["OPENCODE_SERVER_PASSWORD"]
    database["password"] = secret_values["PLATFORM_DB_PASSWORD"]

    config_payload = (
        json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    parse_and_validate(config_payload)
    environment_payload = (
        "\n".join(updated_lines) + "\n"
    ).encode("utf-8")

    # Update the environment first. If the process is interrupted before the
    # JSON replacement, rerunning this command preserves the generated values
    # and completes the synchronization without rotating any secret.
    _atomic_write_private_file(environment_path, environment_payload)
    _atomic_write_private_file(config_path, config_payload)


def resolve_compose_project(path: Path, default: str) -> str:
    if not COMPOSE_PROJECT_PATTERN.fullmatch(default):
        raise ConfigError("default Compose project name is invalid")

    text = read_secure_env_file(path).decode("utf-8")
    project: str | None = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if key.strip() != "COMPOSE_PROJECT_NAME":
            continue
        if not separator:
            raise ConfigError(
                f"COMPOSE_PROJECT_NAME on environment line {line_number} has no '='"
            )
        if project is not None:
            raise ConfigError("environment contains duplicate COMPOSE_PROJECT_NAME entries")
        project = value.strip()
        if not COMPOSE_PROJECT_PATTERN.fullmatch(project):
            raise ConfigError(
                "COMPOSE_PROJECT_NAME must match "
                "[a-z0-9][a-z0-9_.-]{0,62} without quotes or interpolation"
            )

    return default if project is None else project


def canonical_bytes(config: dict[str, object]) -> bytes:
    try:
        text = json.dumps(
            config,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("configuration cannot be encoded as canonical JSON") from exc
    return text.encode("utf-8")


def _validate_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError(f"cannot inspect private runtime directory: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise ConfigError(f"runtime path is not a private directory: {path}")
    if metadata.st_uid != os.geteuid():
        raise ConfigError(f"runtime directory is not owned by the current user: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode != 0o700:
        raise ConfigError(
            f"runtime directory mode must be 0700, found {mode:04o}: {path}"
        )


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ConfigError(f"cannot create private runtime directory: {path}") from exc
    _validate_private_directory(path)


def _replace_path_prefix(value: object, old_root: str, new_root: str) -> object:
    if not isinstance(value, str):
        return value
    if value == old_root:
        return new_root
    prefix = old_root.rstrip("/") + "/"
    if value.startswith(prefix):
        return new_root.rstrip("/") + "/" + value[len(prefix) :]
    return value


def apply_runtime_overrides(
    config: dict[str, object],
    *,
    projects_root: Path | None = None,
    workspaces_root: Path | None = None,
    opencode_url: str | None = None,
) -> dict[str, object]:
    """Return a staged-only config adapted to the local runtime topology."""
    if projects_root is None and workspaces_root is None and opencode_url is None:
        return config

    overridden = json.loads(json.dumps(config))
    replacements: list[tuple[str, str]] = []
    if projects_root is not None:
        if not projects_root.is_absolute():
            raise ConfigError("projects runtime root must be an absolute path")
        replacements.append(("/data/playwright-projects", str(projects_root)))
    if workspaces_root is not None:
        if not workspaces_root.is_absolute():
            raise ConfigError("workspaces runtime root must be an absolute path")
        replacements.append(("/data/playwright-workspaces", str(workspaces_root)))

    for field in (
        "project_workspace_root",
        "project_template_dependency_source_root",
    ):
        value = overridden.get(field)
        for old_root, new_root in replacements:
            value = _replace_path_prefix(value, old_root, new_root)
        overridden[field] = value

    projects = overridden.get("projects")
    if isinstance(projects, list):
        for project in projects:
            if not isinstance(project, dict):
                continue
            value = project.get("playwright_project_root")
            for old_root, new_root in replacements:
                value = _replace_path_prefix(value, old_root, new_root)
            project["playwright_project_root"] = value

    if opencode_url is not None:
        overridden["opencode_server_url"] = opencode_url
    return parse_and_validate(canonical_bytes(overridden))


def stage_config(
    source: Path,
    destination: Path,
    *,
    projects_root: Path | None = None,
    workspaces_root: Path | None = None,
    opencode_url: str | None = None,
) -> None:
    config = parse_and_validate(read_secure_source(source))
    config = apply_runtime_overrides(
        config,
        projects_root=projects_root,
        workspaces_root=workspaces_root,
        opencode_url=opencode_url,
    )
    canonical = canonical_bytes(config)

    runtime_directory = destination.parent.parent
    secret_directory = destination.parent
    _ensure_private_directory(runtime_directory)
    _ensure_private_directory(secret_directory)

    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".platform-config.",
            dir=secret_directory,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(canonical)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o444)
        os.replace(temporary_path, destination)
        temporary_path = None
        directory_descriptor = os.open(
            secret_directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise ConfigError(f"cannot stage runtime configuration: {destination}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def validate_config(source: Path) -> None:
    canonical_bytes(parse_and_validate(read_secure_source(source)))


def validate_runtime_config(source: Path) -> None:
    _validate_private_directory(source.parent.parent)
    _validate_private_directory(source.parent)

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ConfigError(f"cannot securely open staged configuration: {source}") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError(f"staged configuration is not a regular file: {source}")
        if metadata.st_uid != os.geteuid():
            raise ConfigError(
                f"staged configuration is not owned by the current user: {source}"
            )
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o444:
            raise ConfigError(
                f"staged configuration mode must be 0444, found {mode:04o}: {source}"
            )
        if metadata.st_size <= 0 or metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(f"staged configuration has an invalid size: {source}")
        raw = b""
        while len(raw) <= MAX_CONFIG_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_CONFIG_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError(f"staged configuration has an invalid size: {source}")
    finally:
        os.close(descriptor)

    expected = canonical_bytes(parse_and_validate(raw))
    if raw != expected:
        raise ConfigError("staged configuration is not canonical JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize, validate, and securely stage Docker configuration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("--config", type=Path, required=True)
    initialize_parser.add_argument("--environment", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--source", type=Path, required=True)

    runtime_parser = subparsers.add_parser("validate-runtime")
    runtime_parser.add_argument("--source", type=Path, required=True)

    environment_parser = subparsers.add_parser("validate-env")
    environment_parser.add_argument("--source", type=Path, required=True)

    project_parser = subparsers.add_parser("compose-project")
    project_parser.add_argument("--source", type=Path, required=True)
    project_parser.add_argument("--default", required=True)

    stage_parser = subparsers.add_parser("stage")
    stage_parser.add_argument("--source", type=Path, required=True)
    stage_parser.add_argument("--destination", type=Path, required=True)
    stage_parser.add_argument("--projects-root", type=Path)
    stage_parser.add_argument("--workspaces-root", type=Path)
    stage_parser.add_argument("--opencode-url")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "initialize":
            initialize_quickstart_config(args.config, args.environment)
            result = "initialized"
        elif args.command == "validate":
            validate_config(args.source)
            result = "validated"
        elif args.command == "validate-runtime":
            validate_runtime_config(args.source)
            result = "validated"
        elif args.command == "validate-env":
            validate_env_file(args.source)
            result = "validated"
        elif args.command == "compose-project":
            result = resolve_compose_project(args.source, args.default)
        elif args.command == "stage":
            stage_config(
                args.source,
                args.destination,
                projects_root=args.projects_root,
                workspaces_root=args.workspaces_root,
                opencode_url=args.opencode_url,
            )
            result = "staged"
        else:  # pragma: no cover - argparse restricts the command choices.
            raise ConfigError(f"unsupported command: {args.command}")
    except ConfigError as exc:
        print(f"configctl: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
