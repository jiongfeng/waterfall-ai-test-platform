import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from test_plan_viewer.setup.validation import (
    SETUP_BASE_ENVIRONMENT_ALLOWLIST,
    validate_setup_environment_reference,
)


SETUP_SCRIPT_OUTPUT_CAPTURE_BYTES = 12000
SETUP_CONCURRENCY_LOCKS = {}
SETUP_CONCURRENCY_LOCKS_GUARD = threading.Lock()
SETUP_ENVIRONMENT_ALLOWLIST = SETUP_BASE_ENVIRONMENT_ALLOWLIST


def build_setup_environment(source=None):
    source = os.environ if source is None else source
    return {
        str(key): str(value)
        for key, value in source.items()
        if key in SETUP_ENVIRONMENT_ALLOWLIST
        or key.startswith("LC_")
    }


def resolve_setup_environment_refs(environment_refs, source=None):
    if environment_refs in (None, ""):
        return {}
    if not isinstance(environment_refs, dict):
        raise ValueError("environment_refs must be an object.")
    source = os.environ if source is None else source
    resolved = {}
    missing = []
    for child_name, platform_name in environment_refs.items():
        child_name = str(child_name).strip()
        platform_name = str(platform_name).strip()
        validate_setup_environment_reference(
            child_name,
            platform_name,
        )
        if platform_name not in source:
            missing.append(platform_name)
            continue
        resolved[child_name] = str(source[platform_name])
    if missing:
        raise ValueError(
            "准备脚本引用的运行平台环境变量缺失："
            + "、".join(sorted(set(missing)))
        )
    return resolved


@dataclass(frozen=True)
class SetupRunnerDependencies:
    resolve_working_directory: callable
    normalize_process_output: callable
    redact_setup_text: callable
    read_process_output: callable
    close_process_output: callable
    kill_process: callable
    output_buffer_factory: callable
    popen: callable
    clock: callable = time.time
    thread_factory: callable = threading.Thread
    environment_factory: callable = build_setup_environment
    os_name: str = os.name


class SetupOutputRingBuffer:
    def __init__(self, limit=SETUP_SCRIPT_OUTPUT_CAPTURE_BYTES):
        self.limit = max(1, int(limit))
        self.buffer = bytearray()
        self.lock = threading.Lock()

    def append(self, chunk):
        if not chunk:
            return
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        with self.lock:
            self.buffer.extend(chunk)
            overflow = len(self.buffer) - self.limit
            if overflow > 0:
                del self.buffer[:overflow]

    def getvalue(self):
        with self.lock:
            return bytes(self.buffer)


def read_setup_process_output(stream, output_buffer):
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            output_buffer.append(chunk)
    except (OSError, ValueError):
        return


def close_setup_process_output(process, reader):
    reader.join(timeout=2)
    if (
        reader.is_alive()
        and getattr(process, "stdout", None) is not None
    ):
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=2)


def kill_setup_process(
    process,
    *,
    os_name=os.name,
    kill_process_group=getattr(os, "killpg", None),
    sigkill=getattr(signal, "SIGKILL", signal.SIGTERM),
    timeout_expired=subprocess.TimeoutExpired,
):
    try:
        if (
            os_name == "posix"
            and getattr(process, "pid", None)
            and kill_process_group is not None
        ):
            kill_process_group(process.pid, sigkill)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
    except timeout_expired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()


def execute_setup_script_once_unlocked(
    script,
    timeout_seconds,
    dependencies,
):
    started = dependencies.clock()
    if (
        script.get("credentials_migration_required")
        or "environment_overrides" in script
    ):
        raise ValueError(
            "准备脚本包含旧版明文环境配置；请先重新绑定 "
            "environment_refs。"
        )
    resolved_environment = resolve_setup_environment_refs(
        script.get("environment_refs")
    )
    execution_script = {
        **script,
        "_resolved_environment_values": tuple(
            resolved_environment.values()
        ),
    }
    cwd = dependencies.resolve_working_directory(
        script.get("working_directory")
    )
    env = dependencies.environment_factory()
    env.update(resolved_environment)
    script_content = script.get("script_content") or ""
    if "\x00" in script_content:
        raise ValueError(
            "script_content contains an invalid null character."
        )
    shell_binary = (
        "/bin/bash" if Path("/bin/bash").is_file() else "/bin/sh"
    )
    process = dependencies.popen(
        [shell_binary, "-c", script_content],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        start_new_session=dependencies.os_name == "posix",
    )
    output_buffer = dependencies.output_buffer_factory()
    reader = dependencies.thread_factory(
        target=dependencies.read_process_output,
        args=(process.stdout, output_buffer),
        daemon=True,
    )
    reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        dependencies.kill_process(process)
        dependencies.close_process_output(process, reader)
        output = dependencies.redact_setup_text(
            dependencies.normalize_process_output(
                output_buffer.getvalue()
            ).strip(),
            execution_script,
            limit=4000,
        )
        raise subprocess.TimeoutExpired(
            getattr(process, "args", exc.cmd),
            timeout_seconds,
            output=output,
        ) from exc
    dependencies.close_process_output(process, reader)
    output = dependencies.normalize_process_output(
        output_buffer.getvalue()
    ).strip()
    success = returncode == 0
    return {
        "ok": success,
        "exit_code": returncode,
        "output": dependencies.redact_setup_text(
            output,
            execution_script,
            limit=4000,
        ),
        "error": (
            "" if success else f"Shell 退出码为 {returncode}。"
        ),
        "duration_ms": int(
            (dependencies.clock() - started) * 1000
        ),
    }


def execute_setup_script_once(
    script,
    timeout_seconds,
    *,
    execute_unlocked,
    concurrency_locks=SETUP_CONCURRENCY_LOCKS,
    concurrency_guard=SETUP_CONCURRENCY_LOCKS_GUARD,
    lock_factory=threading.Lock,
):
    concurrency_key = str(
        script.get("concurrency_key") or ""
    ).strip()
    if not concurrency_key:
        return execute_unlocked(script, timeout_seconds)
    with concurrency_guard:
        script_lock = concurrency_locks.setdefault(
            concurrency_key,
            lock_factory(),
        )
    if not script_lock.acquire(timeout=timeout_seconds):
        raise TimeoutError(f"等待并发键 {concurrency_key} 超时。")
    try:
        return execute_unlocked(script, timeout_seconds)
    finally:
        script_lock.release()
