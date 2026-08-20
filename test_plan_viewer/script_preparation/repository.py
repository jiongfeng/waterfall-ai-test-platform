"""Project-scoped persistence for ordinary module script-preparation runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any, Callable


ACTIVE_RUN_STATUSES = frozenset(
    {"queued", "running", "failing", "cancelling", "awaiting_action"}
)
ACTIONABLE_RUN_STATUSES = frozenset({"awaiting_action", "completed"})


class ModuleScriptPreparationConflict(RuntimeError):
    """Raised when a module already has work or a run changed concurrently."""

    def __init__(self, message, existing_run=None):
        super().__init__(message)
        self.existing_run = existing_run


@dataclass(frozen=True)
class ModuleScriptPreparationRepositoryDependencies:
    get_platform_database_config: Callable[..., dict]
    ensure_platform_database_schema: Callable[..., Any]
    platform_mysql_connection: Callable[..., Any]
    get_script_preparation_runs_table: Callable[..., str]
    get_current_project_id: Callable[[], int]
    current_time_ms: Callable[[], int]
    validate_uid: Callable[[str, str], str]


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _retryable_create_error(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    try:
        return int(code) in {1062, 1213}
    except (TypeError, ValueError):
        return False


class ModuleScriptPreparationRepository:
    def __init__(self, dependencies):
        if not isinstance(
            dependencies, ModuleScriptPreparationRepositoryDependencies
        ):
            raise TypeError(
                "dependencies must be ModuleScriptPreparationRepositoryDependencies"
            )
        self.dependencies = dependencies
        self._worker_context = threading.local()

    def _worker_token(self):
        return str(getattr(self._worker_context, "worker_token", "") or "")

    @staticmethod
    def _raise_missing_or_lost(worker_token):
        if worker_token:
            raise ModuleScriptPreparationConflict("脚本准备 worker 租约已失效。")
        raise FileNotFoundError("脚本准备任务不存在。")

    def _context(self):
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise RuntimeError("脚本准备任务需要启用平台 MySQL 持久化。")
        self.dependencies.ensure_platform_database_schema(config)
        return (
            config,
            self.dependencies.get_script_preparation_runs_table(config),
            int(self.dependencies.get_current_project_id()),
        )

    @staticmethod
    def scope_key(module_name):
        return f"module:{str(module_name or '').strip().casefold()}"[:512]

    def serialize(self, row):
        if not row:
            return None
        value = dict(row)
        value["plan_filenames"] = _load(value.pop("plan_filenames_json", None), [])
        value["plan_snapshots"] = _load(value.pop("plan_snapshots_json", None), [])
        value["state"] = _load(value.pop("state_json", None), {})
        value["action_queue"] = _load(value.pop("action_queue_json", None), [])
        value["recent_actions"] = _load(value.pop("recent_actions_json", None), [])
        value["cancel_requested"] = bool(value.get("cancel_requested"))
        return value

    def get(self, run_id):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s",
                    (project_id, run_id),
                )
                return self.serialize(cursor.fetchone())

    def create(
        self,
        *,
        run_id,
        module_name,
        plan_filenames,
        plan_snapshots,
        client_request_id,
        created_by,
        _retried=False,
    ):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        client_request_id = str(client_request_id or "").strip()[:128] or None
        scope_key = self.scope_key(module_name)
        now = self.dependencies.current_time_ms()
        normalized_plans = sorted(
            {str(value) for value in plan_filenames},
            key=lambda value: (value.casefold(), value),
        )
        plan_snapshots = list(plan_snapshots)
        retry = False
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    if client_request_id:
                        cursor.execute(
                            f"SELECT * FROM {table} WHERE project_id = %s "
                            "AND client_request_id = %s LIMIT 1 FOR UPDATE",
                            (project_id, client_request_id),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            value = self.serialize(existing)
                            if (
                                value.get("module_name") == module_name
                                and sorted(
                                    set(value.get("plan_filenames") or []),
                                    key=lambda plan: (plan.casefold(), plan),
                                )
                                == normalized_plans
                            ):
                                connection.commit()
                                return value, False
                            raise ModuleScriptPreparationConflict(
                                "client_request_id 已用于不同的脚本准备请求。"
                            )
                    cursor.execute(
                        f"SELECT * FROM {table} WHERE project_id = %s "
                        "AND active_scope_key = %s LIMIT 1 FOR UPDATE",
                        (project_id, scope_key),
                    )
                    active = cursor.fetchone()
                    if active:
                        active_value = self.serialize(active)
                        if sorted(
                            set(active_value.get("plan_filenames") or []),
                            key=lambda value: (value.casefold(), value),
                        ) == normalized_plans:
                            connection.commit()
                            return active_value, False
                        raise ModuleScriptPreparationConflict(
                            "当前模块已有其他计划集合的脚本准备任务。",
                            existing_run=active_value,
                        )
                    cursor.execute(
                        f"""
                        INSERT INTO {table}
                          (project_id, run_id, module_name, status,
                           active_scope_key, client_request_id,
                           plan_filenames_json, plan_snapshots_json, state_json,
                           action_queue_json, recent_actions_json,
                           cancel_requested, error, created_by, started_at,
                           finished_at, created_at, updated_at)
                        VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s, NULL, '[]', '[]',
                                0, '', %s, NULL, NULL, %s, %s)
                        """,
                        (
                            project_id,
                            run_id,
                            module_name,
                            scope_key,
                            client_request_id,
                            _dump(normalized_plans),
                            _dump(plan_snapshots),
                            str(created_by or "")[:255] or None,
                            now,
                            now,
                        ),
                    )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if not _retried and _retryable_create_error(exc):
                    retry = True
                else:
                    raise
        if retry:
            return self.create(
                run_id=run_id,
                module_name=module_name,
                plan_filenames=normalized_plans,
                plan_snapshots=plan_snapshots,
                client_request_id=client_request_id,
                created_by=created_by,
                _retried=True,
            )
        return self.get(run_id), True

    def save_state(self, run_id, state, *, step_status, started=False, finished=False):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        current_run = self.get(run_id) or {}
        has_pending_actions = bool(current_run.get("action_queue"))
        initial_finished = bool(state.get("initial_run_finished"))
        worker_token = self._worker_token()
        if has_pending_actions:
            status = "running"
            active_scope = self.scope_key(current_run.get("module_name"))
        elif (
            (finished or step_status == "succeeded")
            and initial_finished
            and not worker_token
        ):
            status = "completed"
            active_scope = None
        elif step_status == "awaiting_action":
            status = "awaiting_action"
            active_scope = self.scope_key(current_run.get("module_name"))
        else:
            status = "running"
            active_scope = self.scope_key(current_run.get("module_name"))
        fields = [
            "state_json = %s",
            "status = CASE WHEN status IN ('completed', 'failed', 'cancelled', 'failing') THEN status "
            "WHEN cancel_requested = 1 THEN 'cancelling' ELSE %s END",
            "active_scope_key = CASE WHEN status IN ('completed', 'failed', 'cancelled') THEN NULL "
            "WHEN status = 'failing' OR cancel_requested = 1 THEN active_scope_key ELSE %s END",
            "error = CASE WHEN status = 'failing' THEN error ELSE %s END",
            "updated_at = %s",
        ]
        values = [_dump(state), status, active_scope, str(state.get("error") or ""), now]
        if started:
            fields.append("started_at = COALESCE(started_at, %s)")
            values.append(now)
        if status == "completed":
            fields.append(
                "finished_at = CASE WHEN status = 'failing' OR cancel_requested = 1 "
                "THEN finished_at ELSE %s END"
            )
            values.append(now)
        else:
            fields.append(
                "finished_at = CASE WHEN status IN ('completed', 'failed', 'cancelled', 'failing') "
                "OR cancel_requested = 1 THEN finished_at ELSE NULL END"
            )
        where = "project_id = %s AND run_id = %s"
        values.extend([project_id, run_id])
        if worker_token:
            where += " AND worker_token = %s"
            values.append(worker_token)
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET {', '.join(fields)} "
                    f"WHERE {where}",
                    tuple(values),
                )
                if cursor.rowcount == 0:
                    self._raise_missing_or_lost(worker_token)
            connection.commit()

    def update_status(self, run_id, status, *, error=None, finished=False):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        status = str(status or "")[:32]
        now = self.dependencies.current_time_ms()
        worker_token = self._worker_token()
        terminal = status in {"completed", "cancelled", "failed"}
        if status == "failed":
            fields = [
                "status = CASE WHEN status = 'cancelled' THEN 'cancelled' "
                "WHEN cancel_requested = 1 OR status = 'cancelling' THEN 'cancelling' "
                "WHEN status IN ('completed', 'failed') THEN status ELSE 'failed' END",
                "updated_at = %s",
            ]
            values = [now]
        elif status == "cancelled":
            fields = [
                "status = CASE WHEN status IN ('completed', 'failed', 'cancelled') "
                "THEN status ELSE 'cancelled' END",
                "updated_at = %s",
            ]
            values = [now]
        else:
            fields = [
                "status = CASE WHEN status IN ('completed', 'failed', 'cancelled') THEN status "
                "WHEN cancel_requested = 1 THEN 'cancelling' ELSE %s END",
                "updated_at = %s",
            ]
            values = [status, now]
        if error is not None:
            fields.append("error = %s")
            values.append(str(error or ""))
        if terminal:
            if status == "cancelled":
                fields.extend(["active_scope_key = NULL", "finished_at = %s"])
            elif status == "failed":
                fields.extend(
                    [
                        "active_scope_key = CASE WHEN cancel_requested = 1 OR status = 'cancelling' THEN active_scope_key ELSE NULL END",
                        "finished_at = CASE WHEN cancel_requested = 1 OR status = 'cancelling' THEN finished_at ELSE %s END",
                    ]
                )
            else:
                fields.extend(
                    [
                        "active_scope_key = CASE WHEN cancel_requested = 1 THEN active_scope_key ELSE NULL END",
                        "finished_at = CASE WHEN cancel_requested = 1 THEN finished_at ELSE %s END",
                    ]
                )
            values.append(now)
        elif finished:
            fields.append("finished_at = %s")
            values.append(now)
        where = "project_id = %s AND run_id = %s"
        values.extend([project_id, run_id])
        if worker_token:
            where += " AND worker_token = %s"
            values.append(worker_token)
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET {', '.join(fields)} "
                    f"WHERE {where}",
                    tuple(values),
                )
                if cursor.rowcount == 0:
                    self._raise_missing_or_lost(worker_token)
            connection.commit()
        return self.get(run_id)

    def assert_actionable(self, run_id, *, worker=False):
        run = self.get(run_id)
        if not run:
            raise FileNotFoundError("脚本准备任务不存在。")
        allowed = ACTIONABLE_RUN_STATUSES | ({"running"} if worker else set())
        if run.get("status") not in allowed:
            raise ModuleScriptPreparationConflict(
                "脚本准备任务当前不能执行人工操作，请刷新后重试。"
            )
        if run.get("cancel_requested"):
            raise ModuleScriptPreparationConflict("脚本准备任务正在取消。")
        if run.get("worker_token") and not worker:
            raise ModuleScriptPreparationConflict("脚本准备任务正在处理其他操作。")
        return run

    def claim_scope(self, run_id):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s "
                        "LIMIT 1 FOR UPDATE",
                        (project_id, run_id),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise FileNotFoundError("脚本准备任务不存在。")
                    if row.get("status") not in ACTIONABLE_RUN_STATUSES:
                        raise ModuleScriptPreparationConflict(
                            "脚本准备任务当前不能执行人工操作。"
                        )
                    if bool(row.get("cancel_requested")):
                        raise ModuleScriptPreparationConflict(
                            "脚本准备任务正在取消。"
                        )
                    stale_worker = bool(row.get("worker_token")) and int(
                        row.get("worker_lease_until") or 0
                    ) <= now
                    if row.get("worker_token") and not stale_worker:
                        raise ModuleScriptPreparationConflict(
                            "脚本准备任务正在处理其他操作。"
                        )
                    scope_key = self.scope_key(row.get("module_name"))
                    cursor.execute(
                        f"SELECT run_id FROM {table} WHERE project_id = %s "
                        "AND active_scope_key = %s AND run_id <> %s LIMIT 1 FOR UPDATE",
                        (project_id, scope_key, run_id),
                    )
                    if cursor.fetchone():
                        raise ModuleScriptPreparationConflict(
                            "当前模块已有其他脚本准备任务正在运行。"
                        )
                    cursor.execute(
                        f"UPDATE {table} SET status = 'running', active_scope_key = %s, "
                        "worker_token = NULL, worker_lease_until = NULL, current_job_id = NULL, "
                        "finished_at = NULL, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s",
                        (scope_key, now, project_id, run_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def enqueue_actions(self, run_id, actions):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s "
                        "LIMIT 1 FOR UPDATE",
                        (project_id, run_id),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise FileNotFoundError("脚本准备任务不存在。")
                    if row.get("status") not in ACTIONABLE_RUN_STATUSES:
                        raise ModuleScriptPreparationConflict(
                            "脚本准备任务当前不能执行人工操作。"
                        )
                    if row.get("worker_token"):
                        raise ModuleScriptPreparationConflict(
                            "脚本准备任务正在处理其他操作。"
                        )
                    queue = _load(row.get("action_queue_json"), [])
                    busy_ids = {
                        str(item.get("item_id") or "")
                        for item in queue
                        if item.get("state") in {"queued", "running"}
                    }
                    requested_ids = {str(item.get("item_id") or "") for item in actions}
                    if busy_ids & requested_ids:
                        raise ModuleScriptPreparationConflict(
                            "所选脚本已有待执行操作，请稍后重试。"
                        )
                    queue.extend(actions)
                    scope_key = self.scope_key(row.get("module_name"))
                    cursor.execute(
                        f"SELECT run_id FROM {table} WHERE project_id = %s "
                        "AND active_scope_key = %s AND run_id <> %s LIMIT 1 FOR UPDATE",
                        (project_id, scope_key, run_id),
                    )
                    if cursor.fetchone():
                        raise ModuleScriptPreparationConflict(
                            "当前模块已有其他脚本准备任务正在运行。"
                        )
                    cursor.execute(
                        f"UPDATE {table} SET action_queue_json = %s, status = 'running', "
                        "active_scope_key = %s, finished_at = NULL, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s",
                        (_dump(queue), scope_key, now, project_id, run_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(run_id)

    def claim_next_action(self, run_id):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        worker_token = self._worker_token()
        if not worker_token:
            raise ModuleScriptPreparationConflict("脚本准备 worker 尚未取得执行租约。")
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT action_queue_json FROM {table} WHERE project_id = %s "
                    "AND run_id = %s AND worker_token = %s LIMIT 1 FOR UPDATE",
                    (project_id, run_id, worker_token),
                )
                row = cursor.fetchone()
                if not row:
                    self._raise_missing_or_lost(worker_token)
                queue = _load(row.get("action_queue_json"), [])
                action = next(
                    (item for item in queue if item.get("state") == "queued"), None
                )
                if action is not None:
                    action["state"] = "running"
                    action["started_at"] = now
                    cursor.execute(
                        f"UPDATE {table} SET action_queue_json = %s, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s AND worker_token = %s",
                        (_dump(queue), now, project_id, run_id, worker_token),
                    )
                    if cursor.rowcount != 1:
                        self._raise_missing_or_lost(worker_token)
            connection.commit()
        return dict(action) if action else None

    def claim_worker(self, run_id, worker_token, *, lease_ms=30_000):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        worker_token = str(worker_token or "")[:64]
        if not worker_token:
            raise ValueError("worker_token 不能为空。")
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT status, cancel_requested, worker_token, worker_lease_until, "
                        "action_queue_json, recent_actions_json "
                        f"FROM {table} WHERE project_id = %s AND run_id = %s "
                        "LIMIT 1 FOR UPDATE",
                        (project_id, run_id),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise FileNotFoundError("脚本准备任务不存在。")
                    current_token = str(row.get("worker_token") or "")
                    lease_until = int(row.get("worker_lease_until") or 0)
                    recoverable_awaiting = (
                        row.get("status") == "awaiting_action"
                        and bool(current_token)
                        and lease_until <= now
                    )
                    if row.get("status") not in {
                        "queued",
                        "running",
                        "failing",
                        "cancelling",
                    } and not recoverable_awaiting:
                        if current_token and lease_until <= now:
                            cursor.execute(
                                f"UPDATE {table} SET worker_token = NULL, worker_lease_until = NULL, "
                                "current_job_id = NULL, updated_at = %s "
                                "WHERE project_id = %s AND run_id = %s AND worker_token = %s",
                                (now, project_id, run_id, current_token),
                            )
                        connection.commit()
                        return False
                    if current_token and current_token != worker_token and lease_until > now:
                        connection.commit()
                        return False
                    queue = _load(row.get("action_queue_json"), [])
                    took_over = bool(
                        current_token
                        and current_token != worker_token
                        and lease_until <= now
                    )
                    if took_over:
                        interrupted = [
                            action
                            for action in queue
                            if action.get("state") == "running"
                        ]
                        queue = [
                            action
                            for action in queue
                            if action.get("state") != "running"
                        ]
                        recent = _load(row.get("recent_actions_json"), [])
                        recent.extend(
                            {
                                **action,
                                "state": "failed",
                                "error": (
                                    "后台 worker 中断，操作结果未知，未自动重放。"
                                ),
                                "finished_at": now,
                            }
                            for action in interrupted
                        )
                    else:
                        recent = _load(row.get("recent_actions_json"), [])
                    cursor.execute(
                        f"UPDATE {table} SET worker_token = %s, worker_lease_until = %s, "
                        "action_queue_json = %s, recent_actions_json = %s, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s",
                        (
                            worker_token,
                            now + int(lease_ms),
                            _dump(queue),
                            _dump(recent[-50:]),
                            now,
                            project_id,
                            run_id,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._worker_context.worker_token = worker_token
        self._worker_context.took_over = took_over
        return True

    def clear_expired_worker(self, run_id, statuses):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        allowed = {str(value) for value in statuses}
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT status, worker_token, worker_lease_until FROM {table} "
                    "WHERE project_id = %s AND run_id = %s LIMIT 1 FOR UPDATE",
                    (project_id, run_id),
                )
                row = cursor.fetchone() or {}
                token = str(row.get("worker_token") or "")
                cleared = bool(
                    token
                    and row.get("status") in allowed
                    and int(row.get("worker_lease_until") or 0) <= now
                )
                if cleared:
                    cursor.execute(
                        f"UPDATE {table} SET worker_token = NULL, worker_lease_until = NULL, "
                        "current_job_id = NULL, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s AND worker_token = %s",
                        (now, project_id, run_id, token),
                    )
            connection.commit()
        return cleared

    def worker_took_over(self):
        return bool(getattr(self._worker_context, "took_over", False))

    def current_worker_token(self):
        return self._worker_token()

    def heartbeat_worker(self, run_id, *, lease_ms=30_000, worker_token=None):
        token = str(worker_token or self._worker_token() or "")
        if not token:
            return False
        config, table, project_id = self._context()
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET worker_lease_until = %s, updated_at = %s "
                    "WHERE project_id = %s AND run_id = %s AND worker_token = %s",
                    (now + int(lease_ms), now, project_id, run_id, token),
                )
                owned = cursor.rowcount == 1
            connection.commit()
        if not owned:
            raise ModuleScriptPreparationConflict("脚本准备 worker 租约已失效。")
        return True

    def release_worker(self, run_id, *, force=False):
        """Release only after atomically confirming no queued handoff exists."""

        token = self._worker_token()
        if not token:
            return True
        config, table, project_id = self._context()
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT action_queue_json, cancel_requested, status FROM {table} "
                        "WHERE project_id = %s "
                        "AND run_id = %s AND worker_token = %s LIMIT 1 FOR UPDATE",
                        (project_id, run_id, token),
                    )
                    row = cursor.fetchone()
                    if not row:
                        self._raise_missing_or_lost(token)
                    queue = _load(row.get("action_queue_json"), [])
                    if not force and any(
                        item.get("state") == "queued" for item in queue
                    ):
                        connection.commit()
                        return False
                    now = self.dependencies.current_time_ms()
                    if bool(row.get("cancel_requested")):
                        cursor.execute(
                            f"UPDATE {table} SET worker_token = NULL, "
                            "worker_lease_until = NULL, current_job_id = NULL, "
                            "finished_at = CASE WHEN status IN ('completed', 'failed', 'cancelled') "
                            "THEN finished_at ELSE NULL END, "
                            "status = CASE WHEN status IN ('completed', 'failed', 'cancelled') "
                            "THEN status ELSE 'cancelling' END, updated_at = %s "
                            "WHERE project_id = %s AND run_id = %s AND worker_token = %s",
                            (now, project_id, run_id, token),
                        )
                    else:
                        cursor.execute(
                            f"UPDATE {table} SET worker_token = NULL, worker_lease_until = NULL, current_job_id = NULL, "
                            "updated_at = %s WHERE project_id = %s AND run_id = %s "
                            "AND worker_token = %s",
                            (now, project_id, run_id, token),
                        )
                    if cursor.rowcount != 1:
                        self._raise_missing_or_lost(token)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._worker_context.worker_token = ""
        self._worker_context.took_over = False
        return True

    def finish_action(self, run_id, action_id, *, error=""):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        worker_token = self._worker_token()
        where = "project_id = %s AND run_id = %s"
        where_values = [project_id, run_id]
        if worker_token:
            where += " AND worker_token = %s"
            where_values.append(worker_token)
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT action_queue_json, recent_actions_json FROM {table} "
                    f"WHERE {where} LIMIT 1 FOR UPDATE",
                    tuple(where_values),
                )
                row = cursor.fetchone()
                if not row:
                    self._raise_missing_or_lost(worker_token)
                queue = _load(row.get("action_queue_json"), [])
                completed = next(
                    (
                        item
                        for item in queue
                        if str(item.get("action_id") or "") == str(action_id)
                    ),
                    None,
                )
                queue = [
                    item
                    for item in queue
                    if str(item.get("action_id") or "") != str(action_id)
                ]
                recent = _load(row.get("recent_actions_json"), [])
                if completed:
                    recent.append(
                        {
                            **completed,
                            "state": "failed" if error else "succeeded",
                            "error": str(error or ""),
                            "finished_at": now,
                        }
                    )
                    recent = recent[-50:]
                cursor.execute(
                    f"UPDATE {table} SET action_queue_json = %s, recent_actions_json = %s, "
                    "error = %s, updated_at = %s "
                    f"WHERE {where}",
                    (
                        _dump(queue),
                        _dump(recent),
                        str(error or ""),
                        now,
                        *where_values,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_missing_or_lost(worker_token)
            connection.commit()

    def request_cancel(self, run_id):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        now = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(config) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT * FROM {table} WHERE project_id = %s AND run_id = %s "
                        "LIMIT 1 FOR UPDATE",
                        (project_id, run_id),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise FileNotFoundError("脚本准备任务不存在。")
                    if row.get("status") in {"completed", "cancelled", "failed"}:
                        connection.commit()
                        return self.serialize(row)
                    queue = _load(row.get("action_queue_json"), [])
                    recent = _load(row.get("recent_actions_json"), [])
                    recent.extend(
                        {
                            **item,
                            "state": "cancelled",
                            "error": "任务已取消，操作未执行。",
                            "finished_at": now,
                        }
                        for item in queue
                    )
                    cursor.execute(
                        f"UPDATE {table} SET cancel_requested = 1, "
                        "status = 'cancelling', action_queue_json = '[]', "
                        "recent_actions_json = %s, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s",
                        (_dump(recent[-50:]), now, project_id, run_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(run_id)

    def is_cancel_requested(self, run_id):
        run = self.get(run_id)
        return bool(run and run.get("cancel_requested"))

    def set_current_job(self, run_id, job_id):
        config, table, project_id = self._context()
        run_id = self.dependencies.validate_uid(run_id, "run_id")
        worker_token = self._worker_token()
        where = "project_id = %s AND run_id = %s"
        values = [
            str(job_id or "")[:64] or None,
            self.dependencies.current_time_ms(),
            project_id,
            run_id,
        ]
        if worker_token:
            where += " AND worker_token = %s"
            values.append(worker_token)
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET current_job_id = %s, updated_at = %s "
                    f"WHERE {where}",
                    tuple(values),
                )
                if cursor.rowcount != 1:
                    self._raise_missing_or_lost(worker_token)
            connection.commit()


__all__ = [
    "ACTIONABLE_RUN_STATUSES",
    "ACTIVE_RUN_STATUSES",
    "ModuleScriptPreparationConflict",
    "ModuleScriptPreparationRepository",
    "ModuleScriptPreparationRepositoryDependencies",
]
