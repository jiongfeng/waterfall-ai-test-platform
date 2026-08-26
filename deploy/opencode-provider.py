#!/usr/bin/env python3
"""Configure and verify the OpenCode provider used by the platform."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


MAX_CONFIG_BYTES = 64 * 1024
MAX_RESOLVED_CONFIG_BYTES = 1024 * 1024
CONFIG_SCHEMA = "https://opencode.ai/config.json"
JSON_CONFIG_NAME = "opencode.json"
JSONC_CONFIG_NAME = "opencode.jsonc"
SMOKE_MARKER = "WATERFALL_AI_PROVIDER_OK"
SMOKE_PROMPT = (
    f"Reply with exactly {SMOKE_MARKER} and nothing else. Do not use tools or inspect files."
)


class ProviderError(RuntimeError):
    """A provider setup error that is safe to display."""


class JsoncSyntaxError(ProviderError):
    """A JSONC syntax error that show-model may resolve through OpenCode."""


def validate_model_id(value: str) -> str:
    if not value or len(value) > 256:
        raise ProviderError("model ID must contain 1 to 256 characters")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ProviderError("model ID must not contain whitespace or control characters")
    provider, separator, model = value.partition("/")
    if not separator or not provider or not model:
        raise ProviderError("model ID must use the provider/model format")
    return value


def config_directory() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    root = Path(configured) if configured else Path.home() / ".config"
    directory = root.expanduser() / "opencode"
    if directory.exists() or directory.is_symlink():
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or directory.is_symlink():
            raise ProviderError(f"OpenCode config path is not a directory: {directory}")
        if metadata.st_uid != os.geteuid():
            raise ProviderError(f"OpenCode config path is not owned by this user: {directory}")
    else:
        directory.mkdir(mode=0o700, parents=True)
    directory.chmod(0o700)
    return directory


def config_path() -> Path:
    directory = config_directory()
    json_path = directory / JSON_CONFIG_NAME
    jsonc_path = directory / JSONC_CONFIG_NAME
    json_present = json_path.exists() or json_path.is_symlink()
    jsonc_present = jsonc_path.exists() or jsonc_path.is_symlink()
    if json_present and jsonc_present:
        raise ProviderError(
            "both opencode.json and opencode.jsonc are present; keep exactly one "
            "OpenCode config file"
        )
    if jsonc_present:
        return jsonc_path
    return json_path


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ProviderError(f"OpenCode config contains a duplicate key: {key}")
        payload[key] = value
    return payload


def reject_nonfinite_number(value: str) -> None:
    raise ProviderError(f"OpenCode config contains a non-finite number: {value}")


def parse_json_object(text: str, *, invalid_message: str) -> dict[str, object]:
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite_number,
        )
    except json.JSONDecodeError as exc:
        raise ProviderError(invalid_message) from exc
    if not isinstance(payload, dict):
        raise ProviderError("OpenCode config root must be a JSON object")
    return payload


def load_config(path: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        return {"$schema": CONFIG_SCHEMA}
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ProviderError(f"OpenCode config is not a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise ProviderError(f"OpenCode config is not owned by this user: {path}")
    if metadata.st_size > MAX_CONFIG_BYTES:
        raise ProviderError(f"OpenCode config exceeds the {MAX_CONFIG_BYTES}-byte safety limit")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        if path.name == JSONC_CONFIG_NAME:
            raise JsoncSyntaxError(
                "opencode.jsonc is not strict UTF-8 JSON; update its model field manually"
            ) from exc
        raise ProviderError("OpenCode config is not valid UTF-8 JSON") from exc
    try:
        return parse_json_object(text, invalid_message="OpenCode config is not valid JSON")
    except ProviderError as exc:
        if path.name == JSONC_CONFIG_NAME and isinstance(exc.__cause__, json.JSONDecodeError):
            raise JsoncSyntaxError(
                "opencode.jsonc uses JSONC syntax; update its model field manually"
            ) from exc
        raise


def fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_config(path: Path, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ProviderError(
            f"updated OpenCode config exceeds the {MAX_CONFIG_BYTES}-byte safety limit"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".opencode.json.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def migrate_jsonc_to_json(path: Path) -> Path:
    target = path.with_name(JSON_CONFIG_NAME)
    if target.exists() or target.is_symlink():
        raise ProviderError(
            "both opencode.json and opencode.jsonc are present; keep exactly one "
            "OpenCode config file"
        )
    path.chmod(0o600)
    os.replace(path, target)
    fsync_directory(path.parent)
    return target


def set_model(model: str) -> None:
    selected = validate_model_id(model)
    path = config_path()
    payload = load_config(path)
    if path.name == JSONC_CONFIG_NAME:
        path = migrate_jsonc_to_json(path)
    payload["model"] = selected
    save_config(path, payload)
    print(f"OpenCode default model configured: {selected}")


def resolved_config() -> dict[str, object]:
    result = run_opencode(("debug", "config"), capture=True)
    if result.returncode != 0:
        raise ProviderError("OpenCode could not resolve the active JSONC configuration")
    encoded = (result.stdout or "").encode("utf-8")
    if len(encoded) > MAX_RESOLVED_CONFIG_BYTES:
        raise ProviderError("OpenCode resolved configuration exceeds the safety limit")
    return parse_json_object(
        result.stdout or "",
        invalid_message="OpenCode returned an invalid resolved configuration",
    )


def show_model() -> None:
    path = config_path()
    try:
        payload = load_config(path)
    except JsoncSyntaxError:
        payload = resolved_config()
    model = payload.get("model")
    if model is None:
        print("OpenCode default model: not configured")
        return
    if not isinstance(model, str):
        raise ProviderError("OpenCode config model field must be a string")
    print(f"OpenCode default model: {validate_model_id(model)}")


def run_opencode(
    arguments: Sequence[str],
    *,
    capture: bool = False,
    timeout: int | None = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        with tempfile.TemporaryDirectory(prefix="waterfall-opencode-provider-") as workdir:
            binary = os.environ.get("WATERFALL_OPENCODE_BINARY", "opencode")
            return subprocess.run(
                [binary, *arguments],
                cwd=workdir,
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                check=False,
                timeout=timeout,
            )
    except FileNotFoundError as exc:
        raise ProviderError("opencode CLI is not installed in this runtime") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderError("OpenCode provider command timed out after 180 seconds") from exc


def auth_login() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ProviderError("provider login requires an interactive terminal")
    result = run_opencode(("auth", "login"), timeout=None)
    if result.returncode != 0:
        raise ProviderError("OpenCode provider login did not complete successfully")


def auth_list() -> None:
    result = run_opencode(("auth", "list"))
    if result.returncode != 0:
        raise ProviderError("OpenCode could not list configured provider credentials")


def models(provider: str | None) -> None:
    if provider is not None:
        if (
            not provider
            or provider.startswith("-")
            or any(character.isspace() for character in provider)
        ):
            raise ProviderError("provider ID must be one non-option token")
        arguments = ("models", provider)
    else:
        arguments = ("models",)
    result = run_opencode(arguments)
    if result.returncode != 0:
        raise ProviderError("OpenCode could not list available models")


def smoke(model: str) -> None:
    selected = validate_model_id(model)
    result = run_opencode(
        (
            "run",
            "--model",
            selected,
            "--format",
            "json",
            SMOKE_PROMPT,
        ),
        capture=True,
    )
    if result.returncode != 0:
        raise ProviderError(
            "OpenCode inference failed; review provider authentication and the model ID"
        )
    if SMOKE_MARKER not in (result.stdout or ""):
        raise ProviderError("OpenCode returned successfully but the inference marker was missing")
    print(f"OpenCode provider inference verified: {selected}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth-login")
    subparsers.add_parser("auth-list")
    models_parser = subparsers.add_parser("models")
    models_parser.add_argument("provider", nargs="?")
    set_model_parser = subparsers.add_parser("set-model")
    set_model_parser.add_argument("model")
    subparsers.add_parser("show-model")
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("model")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "auth-login":
            auth_login()
        elif args.command == "auth-list":
            auth_list()
        elif args.command == "models":
            models(args.provider)
        elif args.command == "set-model":
            set_model(args.model)
        elif args.command == "show-model":
            show_model()
        elif args.command == "smoke":
            smoke(args.model)
        return 0
    except (ProviderError, OSError) as exc:
        print(f"opencode-provider: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
