#!/usr/bin/env python3
"""Manage the isolated native OpenCode service used by local macOS deployments."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


LABEL = "com.waterfall-ai.native-opencode"
PORT = 4096
HOST = "127.0.0.1"
VOLUME_DESTINATIONS = {
    "platform_projects": Path("data/playwright-projects"),
    "platform_workspaces": Path("data/playwright-workspaces"),
    "opencode_config": Path("native-opencode/config/opencode"),
    "opencode_data": Path("native-opencode/data/opencode"),
    "opencode_cache": Path("native-opencode/cache/opencode"),
    "opencode_state": Path("native-opencode/state"),
}
PATH_COLUMNS = (
    ("platform_projects", "playwright_project_root"),
    ("job_artifacts", "path"),
    ("requirements", "file_path"),
    ("test_asset_revisions", "file_path"),
    ("test_assets", "current_path"),
    ("test_jobs", "log_path"),
    ("test_run_artifacts", "path"),
    ("test_run_results", "script_path"),
)


class NativeOpenCodeError(RuntimeError):
    """A safe-to-display native runtime error."""


def run(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise NativeOpenCodeError(
            f"command failed ({command[0]}): {detail or f'exit {result.returncode}'}"
        )
    return result


def ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise NativeOpenCodeError(f"runtime path is not a directory: {path}")
    if metadata.st_uid != os.geteuid():
        raise NativeOpenCodeError(f"runtime path is not owned by this user: {path}")
    path.chmod(0o700)


def read_env(path: Path) -> dict[str, str]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise NativeOpenCodeError(f"environment file must be a regular 0600 file: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            raise NativeOpenCodeError(f"environment line {line_number} has no '='")
        key = key.strip()
        if key in values:
            raise NativeOpenCodeError(f"environment contains duplicate key: {key}")
        values[key] = value.strip()
    return values


def resolve_binary(explicit: str | None) -> Path:
    candidate = explicit or shutil.which("opencode")
    if not candidate:
        raise NativeOpenCodeError("native opencode CLI is not installed")
    path = Path(candidate).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise NativeOpenCodeError(f"native opencode CLI is not executable: {path}")
    if sys.platform == "darwin":
        details = run(["file", str(path)], capture=True).stdout.lower()
        machine = os.uname().machine.lower()
        if machine == "arm64" and "arm64" not in details:
            raise NativeOpenCodeError(
                f"OpenCode must be a native ARM64 executable on this Mac: {path}"
            )
    return path


def runtime_paths(runtime_root: Path) -> dict[str, Path]:
    native_root = runtime_root / "native-opencode"
    return {
        "root": native_root,
        "config": native_root / "config",
        "data": native_root / "data",
        "cache": native_root / "cache",
        "state": native_root / "state",
        "logs": native_root / "logs",
        "plist": native_root / f"{LABEL}.plist",
    }


def prepare(runtime_root: Path) -> dict[str, Path]:
    ensure_private_directory(runtime_root)
    paths = runtime_paths(runtime_root)
    for key in ("root", "config", "data", "cache", "state", "logs"):
        ensure_private_directory(paths[key])
    for directory in (
        paths["config"] / "opencode",
        paths["data"] / "opencode",
        paths["data"] / "opencode/log",
        paths["data"] / "opencode/repos",
        paths["cache"] / "opencode",
        paths["state"] / "opencode",
        runtime_root / "data/playwright-projects",
        runtime_root / "data/playwright-workspaces",
    ):
        ensure_private_directory(directory)
    return paths


def health(env_file: Path, *, timeout: float = 2.0) -> dict[str, object]:
    values = read_env(env_file)
    username = values.get("OPENCODE_SERVER_USERNAME", "opencode")
    password = values.get("OPENCODE_SERVER_PASSWORD", "")
    if not password:
        raise NativeOpenCodeError("OPENCODE_SERVER_PASSWORD is missing")
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    request = urllib.request.Request(
        f"http://{HOST}:{PORT}/global/health",
        headers={"Authorization": f"Basic {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise NativeOpenCodeError("native OpenCode health endpoint is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("healthy") is not True:
        raise NativeOpenCodeError("native OpenCode did not report healthy=true")
    return payload


def build_environment(runtime_root: Path, env_file: Path) -> dict[str, str]:
    paths = prepare(runtime_root)
    configured = read_env(env_file)
    password = configured.get("OPENCODE_SERVER_PASSWORD", "")
    if not password:
        raise NativeOpenCodeError("OPENCODE_SERVER_PASSWORD is missing")
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(Path.home()),
            "XDG_CONFIG_HOME": str(paths["config"]),
            "XDG_DATA_HOME": str(paths["data"]),
            "XDG_CACHE_HOME": str(paths["cache"]),
            "XDG_STATE_HOME": str(paths["state"]),
            "OPENCODE_HOSTNAME": HOST,
            "OPENCODE_PORT": str(PORT),
            "OPENCODE_SERVER_USERNAME": configured.get(
                "OPENCODE_SERVER_USERNAME", "opencode"
            ),
            "OPENCODE_SERVER_PASSWORD": password,
        }
    )
    return environment


def run_server(runtime_root: Path, env_file: Path, binary: Path) -> None:
    environment = build_environment(runtime_root, env_file)
    os.chdir(runtime_root / "data/playwright-workspaces")
    os.execve(
        str(binary),
        [str(binary), "serve", "--hostname", HOST, "--port", str(PORT)],
        environment,
    )


def plist_payload(
    helper: Path,
    runtime_root: Path,
    env_file: Path,
    binary: Path,
) -> dict[str, object]:
    paths = runtime_paths(runtime_root)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(helper),
            "run",
            "--runtime-root",
            str(runtime_root),
            "--env-file",
            str(env_file),
            "--opencode-binary",
            str(binary),
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "WorkingDirectory": str(runtime_root / "data/playwright-workspaces"),
        "StandardOutPath": str(paths["logs"] / "stdout.log"),
        "StandardErrorPath": str(paths["logs"] / "stderr.log"),
        "Umask": 0o077,
    }


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launch_loaded() -> bool:
    result = run(
        ["launchctl", "print", f"{launch_domain()}/{LABEL}"],
        capture=True,
        check=False,
    )
    return result.returncode == 0


def launch_pid() -> int | None:
    result = run(
        ["launchctl", "print", f"{launch_domain()}/{LABEL}"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    match = re.search(r"(?m)^\s*pid = ([0-9]+)\s*$", result.stdout)
    return int(match.group(1)) if match else None


def start(
    runtime_root: Path,
    env_file: Path,
    binary: Path,
    *,
    wait_seconds: int,
) -> None:
    paths = prepare(runtime_root)
    helper = Path(__file__).resolve()
    payload = plist_payload(helper, runtime_root, env_file.resolve(), binary)
    encoded = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
    current = paths["plist"].read_bytes() if paths["plist"].exists() else b""
    reload_required = current != encoded
    if reload_required:
        temporary = paths["plist"].with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.chmod(0o600)
        os.replace(temporary, paths["plist"])

    if launch_loaded() and reload_required:
        run(["launchctl", "bootout", launch_domain(), str(paths["plist"])], check=False)
    if not launch_loaded():
        run(["launchctl", "bootstrap", launch_domain(), str(paths["plist"])])
    else:
        if launch_pid() is not None:
            try:
                health(env_file)
                return
            except NativeOpenCodeError:
                pass
        else:
            try:
                health(env_file)
            except NativeOpenCodeError:
                pass
            else:
                raise NativeOpenCodeError(
                    f"port {PORT} is occupied by an unmanaged OpenCode process"
                )
        if launch_loaded():
            run(["launchctl", "kickstart", "-k", f"{launch_domain()}/{LABEL}"])

    deadline = time.monotonic() + wait_seconds
    last_error = "not ready"
    while time.monotonic() < deadline:
        try:
            payload = health(env_file)
            if launch_pid() is None:
                raise NativeOpenCodeError(
                    "health endpoint is not owned by the native OpenCode LaunchAgent"
                )
            print(f"native OpenCode ready: version={payload.get('version', 'unknown')}")
            return
        except NativeOpenCodeError as exc:
            last_error = str(exc)
            time.sleep(1)
    raise NativeOpenCodeError(
        f"native OpenCode did not become healthy within {wait_seconds}s: {last_error}"
    )


def stop(runtime_root: Path) -> None:
    paths = runtime_paths(runtime_root)
    if launch_loaded():
        run(
            ["launchctl", "bootout", launch_domain(), str(paths["plist"])],
            check=False,
        )


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(runtime_root: Path, binary: Path) -> dict[str, object]:
    paths = runtime_paths(runtime_root)
    configs = []
    for name in ("opencode.json", "opencode.jsonc"):
        candidate = paths["config"] / "opencode" / name
        if candidate.is_file() and not candidate.is_symlink():
            configs.append({"name": name, "sha256": file_hash(candidate)})
    auth_path = paths["data"] / "opencode/auth.json"
    provider_shape: dict[str, object] = {}
    if auth_path.is_file() and not auth_path.is_symlink():
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        if isinstance(auth, dict):
            for provider, value in sorted(auth.items()):
                provider_shape[provider] = (
                    sorted(value) if isinstance(value, dict) else type(value).__name__
                )
    version = run([str(binary), "--version"], capture=True).stdout.strip()
    return {
        "binary": str(binary),
        "version": version,
        "machine": os.uname().machine,
        "config": configs,
        "provider_shape": provider_shape,
        "projects_root": str(runtime_root / "data/playwright-projects"),
        "workspaces_root": str(runtime_root / "data/playwright-workspaces"),
    }


def resolve_volume(compose_project: str, key: str) -> str:
    result = run(
        [
            "docker",
            "volume",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
            "--filter",
            f"label=com.docker.compose.volume={key}",
        ],
        capture=True,
    )
    matches = [line for line in result.stdout.splitlines() if line]
    if len(matches) != 1:
        raise NativeOpenCodeError(
            f"expected one {key} volume for {compose_project}, found {len(matches)}"
        )
    return matches[0]


def copy_volume(volume: str, destination: Path, image: str) -> None:
    marker = destination / ".waterfall-volume-source"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == volume:
        return
    existing_files = [
        path
        for path in destination.rglob("*")
        if path.is_file() or path.is_symlink()
    ]
    if existing_files:
        raise NativeOpenCodeError(
            f"migration destination is not empty and has no matching marker: {destination}"
        )
    for child in sorted(destination.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    producer = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--volume",
            f"{volume}:/source:ro",
            "--entrypoint",
            "/bin/tar",
            image,
            "-C",
            "/source",
            "-cf",
            "-",
            ".",
        ],
        stdout=subprocess.PIPE,
    )
    assert producer.stdout is not None
    consumer = subprocess.run(
        ["/usr/bin/tar", "-C", str(destination), "-xf", "-"],
        stdin=producer.stdout,
        check=False,
    )
    producer.stdout.close()
    producer_status = producer.wait()
    if producer_status or consumer.returncode:
        raise NativeOpenCodeError(f"failed to copy Docker volume {volume}")
    marker.write_text(volume + "\n", encoding="utf-8")
    marker.chmod(0o600)


def migrate_volumes(runtime_root: Path, compose_project: str, image: str) -> None:
    prepare(runtime_root)
    for key, relative_destination in VOLUME_DESTINATIONS.items():
        volume = resolve_volume(compose_project, key)
        destination = runtime_root / relative_destination
        ensure_private_directory(destination)
        copy_volume(volume, destination, image)


def mysql_hex(value: str) -> str:
    return "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)"


def mysql_container(compose_project: str) -> str:
    result = run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
            "--filter",
            "label=com.docker.compose.service=mysql",
            "--format",
            "{{.ID}}",
        ],
        capture=True,
    )
    matches = [line for line in result.stdout.splitlines() if line]
    if len(matches) != 1:
        raise NativeOpenCodeError(
            f"expected one running mysql container, found {len(matches)}"
        )
    return matches[0]


def migrate_database_paths(
    compose_project: str,
    projects_root: Path,
    workspaces_root: Path,
    *,
    reverse: bool,
) -> None:
    container = mysql_container(compose_project)
    schema_query = """
SELECT TABLE_NAME, COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND (TABLE_NAME, COLUMN_NAME) IN (
    ('platform_projects','playwright_project_root'),
    ('job_artifacts','path'),
    ('requirements','file_path'),
    ('test_asset_revisions','file_path'),
    ('test_assets','current_path'),
    ('test_jobs','log_path'),
    ('test_run_artifacts','path'),
    ('test_run_results','script_path')
  );
""".strip()
    query_command = [
        "docker",
        "exec",
        "-i",
        container,
        "/bin/sh",
        "-ceu",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --batch --skip-column-names "$MYSQL_DATABASE"',
    ]
    available_result = run(
        query_command,
        input_text=schema_query,
        capture=True,
    )
    available = {
        tuple(line.split("\t", 1))
        for line in available_result.stdout.splitlines()
        if "\t" in line
    }
    mappings = [
        ("/data/playwright-projects", str(projects_root)),
        ("/data/playwright-workspaces", str(workspaces_root)),
    ]
    if reverse:
        mappings = [(new, old) for old, new in mappings]
    statements = ["START TRANSACTION;"]
    for table, column in PATH_COLUMNS:
        if (table, column) not in available:
            continue
        for old_root, new_root in mappings:
            old_sql = mysql_hex(old_root)
            new_sql = mysql_hex(new_root)
            statements.append(
                f"UPDATE `{table}` SET `{column}` = CONCAT({new_sql}, "
                f"SUBSTRING(`{column}`, CHAR_LENGTH({old_sql}) + 1)) "
                f"WHERE BINARY LEFT(`{column}`, CHAR_LENGTH({old_sql})) = "
                f"BINARY {old_sql} AND (CHAR_LENGTH(`{column}`) = "
                f"CHAR_LENGTH({old_sql}) OR SUBSTRING(`{column}`, "
                f"CHAR_LENGTH({old_sql}) + 1, 1) = '/');"
            )
    statements.append("COMMIT;")
    run(query_command, input_text="\n".join(statements))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "run",
            "start",
            "stop",
            "status",
            "verify",
            "manifest",
            "migrate-volumes",
            "migrate-database",
        ),
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--opencode-binary")
    parser.add_argument("--wait-seconds", type=int, default=60)
    parser.add_argument("--compose-project")
    parser.add_argument("--platform-image")
    parser.add_argument("--projects-root", type=Path)
    parser.add_argument("--workspaces-root", type=Path)
    parser.add_argument("--reverse", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    runtime_root = args.runtime_root.expanduser().resolve()
    try:
        if args.command == "prepare":
            prepare(runtime_root)
            return 0
        if args.command == "stop":
            stop(runtime_root)
            return 0
        if args.command == "migrate-volumes":
            if not args.compose_project or not args.platform_image:
                raise NativeOpenCodeError(
                    "migrate-volumes requires --compose-project and --platform-image"
                )
            migrate_volumes(runtime_root, args.compose_project, args.platform_image)
            return 0
        if args.command == "migrate-database":
            if not args.compose_project or not args.projects_root or not args.workspaces_root:
                raise NativeOpenCodeError(
                    "migrate-database requires project, projects-root and workspaces-root"
                )
            migrate_database_paths(
                args.compose_project,
                args.projects_root.resolve(),
                args.workspaces_root.resolve(),
                reverse=args.reverse,
            )
            return 0
        if args.env_file is None:
            raise NativeOpenCodeError(f"{args.command} requires --env-file")
        env_file = args.env_file.expanduser().resolve()
        binary = resolve_binary(args.opencode_binary)
        if args.command == "run":
            run_server(runtime_root, env_file, binary)
        elif args.command == "start":
            start(
                runtime_root,
                env_file,
                binary,
                wait_seconds=args.wait_seconds,
            )
        elif args.command == "status":
            payload = health(env_file)
            print(json.dumps(payload, sort_keys=True))
        elif args.command == "verify":
            if not launch_loaded():
                raise NativeOpenCodeError("native OpenCode LaunchAgent is not loaded")
            if launch_pid() is None:
                raise NativeOpenCodeError("native OpenCode LaunchAgent has no live process")
            payload = health(env_file)
            details = manifest(runtime_root, binary)
            if not details["config"] or not details["provider_shape"]:
                raise NativeOpenCodeError(
                    "native OpenCode provider config/auth manifest is incomplete"
                )
            print(
                "native OpenCode verified: "
                f"version={payload.get('version', details['version'])} "
                f"machine={details['machine']}"
            )
        elif args.command == "manifest":
            print(json.dumps(manifest(runtime_root, binary), sort_keys=True))
        return 0
    except (NativeOpenCodeError, OSError, json.JSONDecodeError) as exc:
        print(f"native-opencode: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
