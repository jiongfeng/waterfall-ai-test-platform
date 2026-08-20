"""Cross-process serialization for operations that touch one script target."""

from __future__ import annotations

import hashlib
import threading
from contextlib import ExitStack


class ScriptTargetBusy(RuntimeError):
    """Raised when another Agent or module worker owns the same script target."""


_LOCAL_GUARD = threading.Lock()
_LOCAL_LOCKS = {}
_HELD_CONTEXT = threading.local()


def _lock_name(project_id, module_name, filename):
    del filename
    identity = f"{project_id}:{module_name}".casefold().encode("utf-8")
    return f"script-module:{hashlib.sha256(identity).hexdigest()[:48]}"


class ScriptTargetLease:
    """Idempotently acquired/released MySQL advisory target lease.

    ``GET_LOCK`` is connection-owned, so a crashed worker cannot strand the
    target.  The same helper is used by Agent and ordinary module preparation.
    A process-local fallback keeps non-MySQL development mode deterministic.
    """

    def __init__(self, runtime, module_name, filename, *, timeout_seconds=0):
        self.runtime = runtime
        self.module_name = runtime.validate_module_name(module_name)
        self.filename = runtime.validate_script_filename(filename)
        self.config = runtime.get_platform_database_config()
        # Database + module is stable across processes without resolving the
        # current project (which itself may require another DB connection).
        # It is intentionally conservative and may serialize same-named
        # modules in different projects on one database.
        database_scope = self.config.get("database") if self.config.get("enabled") else 0
        self.name = _lock_name(database_scope, self.module_name, self.filename)
        self.timeout_seconds = max(0, float(timeout_seconds or 0))
        self._local_lock = None
        self._connection_context = None
        self._connection = None
        self._acquired = False
        self._nested = False

    def acquire(self):
        if self._acquired:
            return self
        held = getattr(_HELD_CONTEXT, "names", None)
        if held is None:
            held = {}
            _HELD_CONTEXT.names = held
        if int(held.get(self.name) or 0) > 0:
            held[self.name] += 1
            self._nested = True
            self._acquired = True
            return self
        if not self.config.get("enabled"):
            return self._acquire_local(held)

        self._connection_context = self.runtime.platform_mysql_connection(
            self.config
        )
        try:
            self._connection = self._connection_context.__enter__()
        except Exception:
            # The mutation itself will still surface a configured DB outage;
            # local serialization keeps isolated/unit runtimes usable.
            self._connection_context = None
            return self._acquire_local(held)
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT GET_LOCK(%s, %s) AS acquired",
                    (self.name, int(self.timeout_seconds)),
                )
                self._acquired = (
                    int((cursor.fetchone() or {}).get("acquired") or 0) == 1
                )
            if not self._acquired:
                self._busy()
            held[self.name] = 1
            return self
        except Exception:
            self.release()
            raise

    def _acquire_local(self, held):
        with _LOCAL_GUARD:
            self._local_lock = _LOCAL_LOCKS.setdefault(
                self.name, threading.Lock()
            )
        if not self._local_lock.acquire(timeout=self.timeout_seconds):
            self._busy()
        self._acquired = True
        held[self.name] = 1
        return self

    def release(self):
        held = getattr(_HELD_CONTEXT, "names", {})
        if self._nested:
            held[self.name] = max(0, int(held.get(self.name) or 1) - 1)
            if not held[self.name]:
                held.pop(self.name, None)
            self._nested = False
            self._acquired = False
            return
        if self._local_lock is not None:
            if self._acquired:
                self._local_lock.release()
            self._local_lock = None
            self._acquired = False
            held.pop(self.name, None)
            return
        if self._connection is not None:
            if self._acquired:
                try:
                    with self._connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (self.name,))
                except Exception:
                    pass
            context = self._connection_context
            self._connection = None
            self._connection_context = None
            self._acquired = False
            held.pop(self.name, None)
            context.__exit__(None, None, None)

    def _busy(self):
        raise ScriptTargetBusy(
            "脚本目标正在被其他准备任务处理："
            f"{self.module_name}/{self.filename}"
        )

    def __enter__(self):
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()


def acquire_script_target_lease(runtime, module_name, filename, *, timeout_seconds=0):
    return ScriptTargetLease(
        runtime,
        module_name,
        filename,
        timeout_seconds=timeout_seconds,
    )


def hold_script_target_lease(runtime, module_name, filename, iterable):
    """Keep a target lease until a streaming response iterator terminates."""

    with acquire_script_target_lease(runtime, module_name, filename):
        yield from iterable


def hold_script_module_leases(runtime, targets, iterable):
    """Acquire multiple module leases in stable order for suite execution."""

    unique = {}
    for module_name, filename in targets:
        unique.setdefault(str(module_name).casefold(), (module_name, filename))
    with ExitStack() as stack:
        for module_name, filename in (
            unique[key] for key in sorted(unique)
        ):
            stack.enter_context(
                acquire_script_target_lease(runtime, module_name, filename)
            )
        yield from iterable


def release_script_target_lease_after(lease, iterable):
    """Release an eagerly acquired lease when its response stream closes."""

    try:
        yield from iterable
    finally:
        lease.release()


def call_with_script_target_lease(runtime, module_name, filename, callback):
    with acquire_script_target_lease(runtime, module_name, filename):
        return callback()


__all__ = [
    "ScriptTargetBusy",
    "ScriptTargetLease",
    "acquire_script_target_lease",
    "call_with_script_target_lease",
    "hold_script_target_lease",
    "hold_script_module_leases",
    "release_script_target_lease_after",
]
