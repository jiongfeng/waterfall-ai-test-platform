"""Application adapter helpers shared by Agent and module preparation."""

from contextlib import ExitStack, contextmanager

from test_plan_viewer.agent import failure_handling
from test_plan_viewer.agent import script_preparation as state_machine
from test_plan_viewer.script_preparation.target_lease import (
    ScriptTargetBusy,
    acquire_script_target_lease,
)


def load_step_output(runtime, run_id, step_key):
    row = runtime.get_agent_step_row(run_id, step_key)
    if not row:
        return None
    output = runtime.load_json_column(row.get("output_json"), None)
    return output if isinstance(output, dict) else None


def get_item_for_web(runtime, run_id, item_id):
    reconcile_items_for_web(runtime, run_id, [item_id])
    item = state_machine.get_script_preparation_item(run_id, item_id)
    script = item.get("current_script")
    if isinstance(script, dict):
        script_file = runtime.get_script_file(item["module_name"], item["filename"])
        if script_file.is_file():
            item["current_script"] = {
                **script,
                "content": script_file.read_text(encoding="utf-8"),
            }
    return item


def get_snapshot_for_web(runtime, run_id):
    reconcile_items_for_web(runtime, run_id, [])
    return state_machine.get_script_preparation_snapshot(run_id)


def _can_reconcile(run, allow_claimed):
    if not isinstance(run, dict):
        return False
    status = run.get("status")
    step = run.get("current_step")
    return (status == "awaiting_script_action" and step == state_machine.SCRIPT_PREPARATION_STEP_KEY) or (
        allow_claimed
        and status == "running"
        and step in {
            state_machine.SCRIPT_PREPARATION_STEP_KEY,
            "create_suite",
            "run_suite",
        }
    )


@contextmanager
def script_preparation_barrier(runtime, run_id, *, timeout_seconds=0):
    """Hold every affected module lease in stable order for a final decision."""

    snapshot = state_machine.get_script_preparation_snapshot(run_id)
    targets = {}
    for item in snapshot.get("items") or []:
        module_name = runtime.validate_module_name(item["module_name"])
        filename = runtime.validate_script_filename(item["filename"])
        targets.setdefault(module_name.casefold(), (module_name, filename))
    with ExitStack() as stack:
        for module_name, filename in (targets[key] for key in sorted(targets)):
            stack.enter_context(
                acquire_script_target_lease(
                    runtime,
                    module_name,
                    filename,
                    timeout_seconds=timeout_seconds,
                )
            )
        yield


@contextmanager
def script_preparation_plan_barrier(runtime, plans):
    targets = {}
    for plan in plans or []:
        module_name = runtime.validate_module_name(plan["module_name"])
        filename = runtime.get_generated_script_filename_from_plan_filename(
            plan["plan_filename"], language=runtime.agent_project_language()
        )
        targets.setdefault(module_name.casefold(), (module_name, filename))
    with ExitStack() as stack:
        for module_name, filename in (targets[key] for key in sorted(targets)):
            stack.enter_context(
                acquire_script_target_lease(runtime, module_name, filename)
            )
        yield


def run_agent_script_preparation_with_barrier(runtime, run_id, plans):
    with script_preparation_plan_barrier(runtime, plans):
        try:
            return state_machine.run_agent_script_preparation(run_id, plans)
        except runtime.OpencodeTaskCancelled as exc:
            finalize_agent_cancellation(runtime, run_id, str(exc))
            raise


def finalize_agent_cancellation(runtime, run_id, message):
    run = runtime.get_agent_run_row(run_id) or {}
    if run.get("status") == "cancelled":
        return True
    if run.get("status") != "cancelling":
        return False
    try:
        state_machine.cancel_script_preparation_interrupted(run_id, message)
    except Exception:
        pass
    return bool(runtime.mark_agent_workflow_cancelled(run_id, message))


def publish_agent_terminal(
    runtime,
    run_id,
    *,
    expected_status,
    terminal_status,
    error,
    fallback_step="",
    current_step=None,
    summary=None,
):
    """Atomically close child records before exposing a recoverable terminal run."""

    config = runtime.require_platform_database()
    runs_table = runtime.get_agent_runs_table(config)
    steps_table = runtime.get_agent_run_steps_table(config)
    events_table = runtime.get_agent_run_events_table(config)
    project_id = runtime.get_current_project_id()
    run_id = runtime.validate_uid(run_id, "run_id")
    now_ms = runtime.current_time_ms()
    message = str(error)
    applied = False
    with runtime.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status, current_step FROM {runs_table} "
                "WHERE project_id = %s AND run_id = %s FOR UPDATE",
                (project_id, run_id),
            )
            row = cursor.fetchone() or {}
            if row.get("status") == expected_status:
                step_key = current_step or row.get("current_step") or fallback_step
                if terminal_status == "cancelled":
                    cursor.execute(
                        f"UPDATE {steps_table} SET status = 'cancelled', error = %s, "
                        "finished_at = %s, updated_at = %s "
                        "WHERE project_id = %s AND run_id = %s AND status = 'running'",
                        (message, now_ms, now_ms, project_id, run_id),
                    )
                    if step_key == state_machine.SCRIPT_PREPARATION_STEP_KEY:
                        cursor.execute(
                            f"UPDATE {steps_table} SET status = 'cancelled', error = %s, "
                            "finished_at = %s, updated_at = %s "
                            "WHERE project_id = %s AND run_id = %s AND step_key = %s",
                            (message, now_ms, now_ms, project_id, run_id, step_key),
                        )
                    event_message = "Agent 任务已取消。"
                elif terminal_status == "failed":
                    if step_key:
                        cursor.execute(
                            f"UPDATE {steps_table} SET status = 'failed', error = %s, "
                            "finished_at = %s, updated_at = %s "
                            "WHERE project_id = %s AND run_id = %s AND step_key = %s",
                            (message, now_ms, now_ms, project_id, run_id, step_key),
                        )
                        runtime.insert_agent_event_row(
                            cursor,
                            events_table,
                            project_id,
                            run_id,
                            step_key,
                            "error",
                            runtime.agent_message(
                                "step_failed",
                                step=runtime.agent_step_name(step_key),
                                error=error,
                            ),
                            {"error": message},
                            created_at=now_ms,
                        )
                    event_message = f"Agent 任务失败：{message}"
                else:
                    event_message = "Agent 全流程执行完成。"
                runtime.insert_agent_event_row(
                    cursor,
                    events_table,
                    project_id,
                    run_id,
                    step_key,
                    "error" if terminal_status == "failed" else "status",
                    event_message,
                    summary if isinstance(summary, dict) else {"error": message},
                    created_at=now_ms,
                )
                extra_fields = []
                extra_values = []
                if current_step is not None:
                    extra_fields.append("current_step = %s")
                    extra_values.append(current_step)
                if summary is not None:
                    extra_fields.append("summary_json = %s")
                    extra_values.append(runtime.compact_json_dumps(summary))
                extra_sql = f", {', '.join(extra_fields)}" if extra_fields else ""
                cursor.execute(
                    f"UPDATE {runs_table} SET status = %s, error = %s, "
                    "finished_at = %s, updated_at = GREATEST(updated_at + 1, %s)"
                    f"{extra_sql} "
                    "WHERE project_id = %s AND run_id = %s AND status = %s",
                    (
                        terminal_status,
                        message,
                        now_ms,
                        now_ms,
                        *extra_values,
                        project_id,
                        run_id,
                        expected_status,
                    ),
                )
                applied = cursor.rowcount == 1
        connection.commit()
    return applied, runtime.get_agent_run_row(run_id)


def apply_action_for_web(runtime, run_id, item_id, **parameters):
    try:
        return state_machine.apply_script_preparation_action(
            run_id, item_id, **parameters
        )
    except runtime.OpencodeTaskCancelled as exc:
        finalize_agent_cancellation(runtime, run_id, str(exc))
        raise state_machine.ScriptPreparationConflict("Agent 任务已取消。") from exc


def apply_batch_action_for_web(runtime, run_id, items, **parameters):
    try:
        return state_machine.apply_script_preparation_batch_action(
            run_id, items, **parameters
        )
    except runtime.OpencodeTaskCancelled as exc:
        finalize_agent_cancellation(runtime, run_id, str(exc))
        raise state_machine.ScriptPreparationConflict("Agent 任务已取消。") from exc


def recover_interrupted_for_web(runtime, run_id):
    with script_preparation_barrier(runtime, run_id):
        run = runtime.get_agent_run_row(run_id) or {}
        if run.get("status") == "cancelling":
            message = "Agent 任务已取消。"
            finalize_agent_cancellation(runtime, run_id, message)
            return True
        if run.get("current_step") != state_machine.SCRIPT_PREPARATION_STEP_KEY:
            return False
        step = runtime.get_agent_step_row(
            run_id, state_machine.SCRIPT_PREPARATION_STEP_KEY
        ) or {}
        if run.get("status") == "running" and step.get("status") == "running":
            state_machine.recover_script_preparation_interrupted(run_id)
            return True
        return False


def find_agent_suite(runtime, run_id):
    description = f"Agent run {run_id} 自动创建。"
    return next(
        (
            value
            for value in runtime.list_test_suites_from_mysql()
            if value.get("description") == description
        ),
        None,
    )


def clear_agent_suite(runtime, run_id):
    suite = find_agent_suite(runtime, run_id)
    if suite:
        runtime.delete_test_suite_in_mysql(suite["id"])
    runtime.update_agent_run(run_id, suite_uid="")
    return suite


def find_or_create_agent_suite(runtime, run_id, suite_name, items):
    """Recover the Agent-owned suite and converge it to the desired scripts."""

    description = f"Agent run {run_id} 自动创建。"
    suite = find_agent_suite(runtime, run_id)
    if suite is None:
        suite = runtime.create_test_suite_in_mysql(suite_name, description)
    desired = {
        (value.get("module_name"), value.get("filename")): value
        for value in items
    }
    for value in list(suite.get("items") or []):
        key = (value.get("module_name"), value.get("filename"))
        if key in desired:
            continue
        if value.get("item_id") is None:
            raise RuntimeError("自动测试集存在无法移除的未知脚本项。")
        suite = runtime.delete_test_suite_item_in_mysql(
            suite["id"], value["item_id"]
        )
        if not suite:
            raise RuntimeError("自动测试集脚本项移除失败。")
    present = {
        (value.get("module_name"), value.get("filename"))
        for value in suite.get("items") or []
    }
    missing = [
        value
        for value in items
        if (value.get("module_name"), value.get("filename")) not in present
    ]
    if missing:
        suite = runtime.add_test_suite_items_in_mysql(suite["id"], missing)
    actual = {
        (value.get("module_name"), value.get("filename")): value
        for value in (suite or {}).get("items") or []
    }
    if set(actual) != set(desired):
        raise RuntimeError("自动测试集脚本成员收敛失败。")
    ordered_ids = [actual[key].get("item_id") for key in desired]
    if any(value is None for value in ordered_ids):
        raise RuntimeError("自动测试集脚本项缺少持久化 ID。")
    current_keys = [
        (value.get("module_name"), value.get("filename"))
        for value in suite.get("items") or []
    ]
    if current_keys != list(desired):
        suite = runtime.reorder_test_suite_items_in_mysql(suite["id"], ordered_ids)
    final_keys = [
        (value.get("module_name"), value.get("filename"))
        for value in (suite or {}).get("items") or []
    ]
    if final_keys != list(desired):
        raise RuntimeError("自动测试集脚本成员或顺序在提交时发生变化。")
    return suite


def fail_agent_suite_execution(runtime, run_id, output, counts, message):
    error = RuntimeError(message or "测试集执行失败。")
    runtime.update_agent_step(
        run_id, "run_suite", output_data=output, counts=counts
    )
    runtime.agent_fail_step(run_id, "run_suite", error)
    return error


def terminal_operation_succeeded(result):
    return (
        isinstance(result, dict)
        and result.get("ok") is not False
        and result.get("status") in {"succeeded", "completed"}
    )


def plan_source_ready(result):
    """Accept the planner's intentional non-terminal handoff to the split phase."""

    return (
        isinstance(result, dict)
        and result.get("status") == "running"
        and result.get("source_ready") is True
        and result.get("plan_phase") == "splitting"
        and bool(str(result.get("plan_filename") or "").strip())
    )


def suite_execution_succeeded(output):
    return isinstance(output, dict) and terminal_operation_succeeded(
        output.get("result")
    )


def complete_agent_workflow(runtime, run_id, final_status, final_summary):
    applied, run = publish_agent_terminal(
        runtime,
        run_id,
        expected_status="running",
        terminal_status=final_status,
        error="",
        current_step="run_suite",
        summary=final_summary,
    )
    if not applied:
        if (run or {}).get("status") == "cancelling":
            message = "Agent 任务已取消。"
            finalize_agent_cancellation(runtime, run_id, message)
        return False
    return True


def request_agent_workflow_cancel(runtime, run_id, observed_status):
    applied, run = runtime.update_agent_run(
        run_id,
        status="cancelling",
        error="用户请求取消。",
        expected_status=observed_status,
        report_applied=True,
    )
    return applied and (run or {}).get("status") == "cancelling", run


def claim_agent_resume(runtime, run_id, from_step):
    applied, run = runtime.update_agent_run(
        run_id,
        status="running",
        current_step=from_step,
        error="",
        reopened=True,
        expected_status="queued",
        report_applied=True,
    )
    if applied:
        return True
    if (run or {}).get("status") == "cancelling":
        finalize_agent_cancellation(runtime, run_id, "用户请求取消。")
    return False


def claim_agent_workflow_start(runtime, run_id, step_key):
    applied, run = runtime.update_agent_run(
        run_id,
        status="running",
        current_step=step_key,
        error="",
        reopened=True,
        expected_status="queued",
        report_applied=True,
    )
    if applied:
        return True
    if (run or {}).get("status") == "cancelling":
        runtime.mark_agent_workflow_cancelled(run_id, "用户请求取消。")
    return False


def claim_agent_step_start(runtime, run_id, step_key):
    run = runtime.get_agent_run_row(run_id) or {}
    observed = run.get("status")
    if observed in {"cancelling", "cancelled"}:
        raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
    if observed != "running":
        raise RuntimeError("Agent 任务状态已变化，不能启动新步骤。")
    applied, current = runtime.update_agent_run(
        run_id,
        status="running",
        current_step=step_key,
        expected_status=observed,
        report_applied=True,
    )
    if applied:
        return True
    if (current or {}).get("status") in {"cancelling", "cancelled"}:
        raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
    raise RuntimeError("Agent 步骤启动 CAS 失败。")


def start_agent_step_atomic(runtime, run_id, step_key, input_data=None):
    """Claim a running Agent step and publish its child state in one commit."""

    config = runtime.require_platform_database()
    runs_table = runtime.get_agent_runs_table(config)
    steps_table = runtime.get_agent_run_steps_table(config)
    events_table = runtime.get_agent_run_events_table(config)
    project_id = runtime.get_current_project_id()
    run_id = runtime.validate_uid(run_id, "run_id")
    now_ms = runtime.current_time_ms()
    with runtime.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status FROM {runs_table} WHERE project_id = %s "
                "AND run_id = %s FOR UPDATE",
                (project_id, run_id),
            )
            status = (cursor.fetchone() or {}).get("status")
            if status in {"cancelling", "cancelled"}:
                raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
            if status != "running":
                raise RuntimeError("Agent 任务状态已变化，不能启动新步骤。")
            cursor.execute(
                f"UPDATE {runs_table} SET current_step = %s, "
                "updated_at = GREATEST(updated_at + 1, %s) "
                "WHERE project_id = %s AND run_id = %s AND status = 'running'",
                (step_key, now_ms, project_id, run_id),
            )
            fields = [
                "status = 'running'",
                "error = ''",
                "started_at = COALESCE(started_at, %s)",
                "updated_at = %s",
            ]
            values = [now_ms, now_ms]
            if input_data is not None:
                fields.append("input_json = %s")
                values.append(runtime.compact_json_dumps(input_data))
            cursor.execute(
                f"UPDATE {steps_table} SET {', '.join(fields)} "
                "WHERE project_id = %s AND run_id = %s AND step_key = %s",
                (*values, project_id, run_id, step_key),
            )
            runtime.insert_agent_event_row(
                cursor,
                events_table,
                project_id,
                run_id,
                step_key,
                "status",
                runtime.agent_message(
                    "step_started", step=runtime.agent_step_name(step_key)
                ),
                {"status": "running"},
                created_at=now_ms,
            )
        connection.commit()
    return True


def update_preparation_agent_run(runtime, run_id, **values):
    target_status = values.get("status")
    if target_status not in {"running", "awaiting_script_action"}:
        return runtime.update_agent_run(run_id, **values)
    run = runtime.get_agent_run_row(run_id) or {}
    observed = run.get("status")
    if observed in {"cancelling", "cancelled"}:
        raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
    if observed not in {"running", "awaiting_script_action"}:
        raise state_machine.ScriptPreparationConflict(
            "Agent 任务状态已变化，脚本准备结果未覆盖。"
        )
    applied, current = runtime.update_agent_run(
        run_id,
        **values,
        expected_status=observed,
        report_applied=True,
    )
    if applied:
        return current
    if (current or {}).get("status") in {"cancelling", "cancelled"}:
        raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
    raise state_machine.ScriptPreparationConflict(
        "Agent 任务状态已变化，脚本准备结果未覆盖。"
    )


def persist_preparation_state_atomic(
    runtime, run_id, *, step_values, event_values, run_values
):
    """Fence preparation state, event, and run projection in one transaction."""

    config = runtime.require_platform_database()
    runs_table = runtime.get_agent_runs_table(config)
    steps_table = runtime.get_agent_run_steps_table(config)
    events_table = runtime.get_agent_run_events_table(config)
    project_id = runtime.get_current_project_id()
    run_id = runtime.validate_uid(run_id, "run_id")
    now_ms = runtime.current_time_ms()
    with runtime.platform_mysql_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status FROM {runs_table} WHERE project_id = %s "
                "AND run_id = %s FOR UPDATE",
                (project_id, run_id),
            )
            observed = (cursor.fetchone() or {}).get("status")
            if observed in {"cancelled", "cancelling"} and run_values:
                raise runtime.OpencodeTaskCancelled("Agent 任务已取消。")
            if observed not in {"running", "awaiting_script_action", "cancelling"}:
                raise state_machine.ScriptPreparationConflict(
                    "Agent 任务状态已变化，脚本准备结果未覆盖。"
                )
            fields = ["status = %s", "error = %s", "updated_at = %s"]
            values = [step_values["status"], step_values.get("error") or "", now_ms]
            for key, column in (
                ("input_data", "input_json"),
                ("output_data", "output_json"),
                ("counts", "counts_json"),
            ):
                if step_values.get(key) is not None:
                    fields.append(f"{column} = %s")
                    values.append(runtime.compact_json_dumps(step_values[key]))
            if step_values.get("started"):
                fields.append("started_at = COALESCE(started_at, %s)")
                values.append(now_ms)
            if step_values.get("finished"):
                fields.append("finished_at = %s")
                values.append(now_ms)
            cursor.execute(
                f"UPDATE {steps_table} SET {', '.join(fields)} "
                "WHERE project_id = %s AND run_id = %s AND step_key = %s",
                (*values, project_id, run_id, state_machine.SCRIPT_PREPARATION_STEP_KEY),
            )
            runtime.insert_agent_event_row(
                cursor,
                events_table,
                project_id,
                run_id,
                state_machine.SCRIPT_PREPARATION_STEP_KEY,
                event_values["event_type"],
                event_values["message"],
                event_values["payload"],
                created_at=now_ms,
            )
            if run_values:
                cursor.execute(
                    f"UPDATE {runs_table} SET status = %s, current_step = %s, "
                    "error = %s, updated_at = GREATEST(updated_at + 1, %s) "
                    "WHERE project_id = %s AND run_id = %s AND status = %s",
                    (
                        run_values["status"],
                        state_machine.SCRIPT_PREPARATION_STEP_KEY,
                        run_values.get("error") or "",
                        now_ms,
                        project_id,
                        run_id,
                        observed,
                    ),
                )
        connection.commit()
    return True


def cancel_awaiting_agent_workflow(runtime, run_id, observed_status):
    try:
        with script_preparation_barrier(runtime, run_id, timeout_seconds=5):
            accepted, run = request_agent_workflow_cancel(
                runtime, run_id, observed_status
            )
            if not accepted:
                return False, run, {}
            result = runtime.agent_request_cancel(run_id)
            finalize_agent_cancellation(runtime, run_id, "用户请求取消。")
            return True, runtime.get_agent_run_row(run_id), result
    except ScriptTargetBusy:
        return False, runtime.get_agent_run_row(run_id), {}


def reconcile_items_for_web(runtime, run_id, item_ids, *, allow_claimed=False):
    run = runtime.get_agent_run_row(run_id)
    if not _can_reconcile(run, allow_claimed):
        return False
    del item_ids
    snapshot = state_machine.get_script_preparation_snapshot(run_id)
    changed = False
    for snapshot_item in snapshot.get("items") or []:
        item_id = str(snapshot_item.get("item_id") or "").strip()
        if not item_id:
            continue
        item = state_machine.get_script_preparation_item(run_id, item_id)
        module_name = runtime.validate_module_name(item["module_name"])
        filename = runtime.validate_script_filename(item["filename"])
        with acquire_script_target_lease(runtime, module_name, filename):
            current_run = runtime.get_agent_run_row(run_id)
            if not _can_reconcile(current_run, allow_claimed):
                continue
            item = state_machine.get_script_preparation_item(run_id, item_id)
            script_file = runtime.get_script_file(module_name, filename)
            if script_file.is_file():
                asset = runtime.sync_script_asset(
                    module_name,
                    script_file,
                    change_source="manual",
                    message=f"adopt external script: {module_name}/{filename}",
                )
                actual_revision = (
                    asset.get("current_revision_id")
                    if isinstance(asset, dict)
                    else None
                )
            else:
                actual_revision = None
            if str(actual_revision) == str(item.get("current_revision_id")):
                continue
            state_machine.adopt_script_preparation_external_versions(
                run_id,
                [{"item_id": item_id, "revision_id": actual_revision}],
            )
            changed = True
    return changed


def finish_with_script_preparation_barrier(runtime, run_id, callback):
    """Recheck all revisions and keep module leases through suite execution."""

    with script_preparation_barrier(runtime, run_id):
        reconcile_items_for_web(runtime, run_id, [], allow_claimed=True)
        run = runtime.get_agent_run_row(run_id) or {}
        step = runtime.get_agent_step_row(
            run_id, state_machine.SCRIPT_PREPARATION_STEP_KEY
        ) or {}
        if not (
            run.get("status") == "running"
            and run.get("current_step")
            in {state_machine.SCRIPT_PREPARATION_STEP_KEY, "create_suite", "run_suite"}
            and step.get("status") == "succeeded"
        ):
            return None
        if run.get("current_step") == "create_suite":
            create_step = runtime.get_agent_step_row(run_id, "create_suite") or {}
            create_output = runtime.get_agent_step_output(run_id, "create_suite") or {}
            has_suite = bool(run.get("suite_uid")) or isinstance(
                create_output.get("suite"), dict
            ) or bool(find_agent_suite(runtime, run_id))
            if create_step.get("status") not in {"queued", "succeeded"} and not has_suite:
                runtime.mark_agent_workflow_failed(
                    run_id,
                    RuntimeError("测试集创建 worker 中断，请从测试集阶段恢复。"),
                    "create_suite",
                )
                return None
        elif run.get("current_step") == "run_suite":
            suite_step = runtime.get_agent_step_row(run_id, "run_suite") or {}
            if suite_step.get("status") not in {"queued", "succeeded"}:
                runtime.mark_agent_workflow_failed(
                    run_id,
                    RuntimeError("测试集执行 worker 中断，请从执行阶段恢复。"),
                    "run_suite",
                )
                return None
            if suite_step.get("status") == "succeeded" and not suite_execution_succeeded(
                runtime.get_agent_step_output(run_id, "run_suite") or {}
            ):
                runtime.mark_agent_workflow_failed(
                    run_id,
                    RuntimeError("测试集执行结果失败，不能完成 Agent 任务。"),
                    "run_suite",
                )
                return None
        snapshot = state_machine.get_script_preparation_snapshot(run_id)
        counts = snapshot.get("counts") or {}
        if not counts.get("total") or counts.get("terminal") != counts.get("total"):
            return None
        return callback(snapshot)


def save_script(runtime, run_id, item, content, expected_revision_id=None):
    del run_id
    module_name = runtime.validate_module_name(item["module_name"])
    filename = runtime.validate_script_filename(item["filename"])
    with acquire_script_target_lease(runtime, module_name, filename):
        script_file = runtime.get_script_file(module_name, filename)
        current_asset = runtime.get_test_asset_by_path("script", script_file)
        actual_revision_id = (
            current_asset.get("current_revision_id")
            if isinstance(current_asset, dict)
            else None
        )
        if str(expected_revision_id) != str(actual_revision_id):
            raise state_machine.ScriptPreparationConflict(
                "脚本版本已变化，请刷新后重新编辑。"
            )
        rollback = {}

        def rollback_asset():
            rollback["asset"] = runtime.sync_script_asset(
                module_name,
                script_file,
                change_source="manual",
                message=f"rollback agent edit: {module_name}/{filename}",
            )
            return rollback["asset"]

        try:
            asset = runtime.save_asset_content_with_rollback(
                script_file,
                str(content),
                lambda: runtime.sync_script_asset(
                    module_name,
                    script_file,
                    change_source="manual",
                    message=f"agent manual edit: {module_name}/{filename}",
                ),
                rollback_asset,
                rollback_message=f"rollback agent edit: {module_name}/{filename}",
            )
        except Exception as exc:
            if rollback.get("asset"):
                exc.rollback_script = {
                    **dict(item.get("current_script") or {}),
                    "module_name": module_name,
                    "plan_filename": item.get("plan_filename") or "",
                    "filename": filename,
                    "path": str(script_file),
                    "asset": runtime.serialize_asset(rollback["asset"]),
                }
            raise
    return {
        "module_name": module_name,
        "plan_filename": item.get("plan_filename") or "",
        "filename": filename,
        "path": str(script_file),
        "asset": runtime.serialize_asset(asset),
    }


def analyze_failure(runtime, run_id, step_key, payload):
    return runtime.call_agent_failure_analyst(
        run_id,
        step_key,
        runtime.agent_message("failure_analysis_instruction"),
        failure_handling.redact_agent_failure_value(payload),
    )


def _assert_current_revision(runtime, module_name, filename, script):
    script_file = runtime.get_script_file(module_name, filename)
    current_asset = runtime.get_test_asset_by_path("script", script_file)
    actual = (
        current_asset.get("current_revision_id")
        if isinstance(current_asset, dict)
        else None
    )
    asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
    if "current_revision_id" in script:
        expected, present = script.get("current_revision_id"), True
    elif "revision_id" in script:
        expected, present = script.get("revision_id"), True
    else:
        expected, present = asset.get("current_revision_id"), "current_revision_id" in asset
    if present and str(expected) != str(actual):
        raise state_machine.ScriptPreparationConflict(
            "脚本版本已变化，请刷新后重试。"
        )


def generate_script(runtime, run_id, step_key, plan, **kwargs):
    module_name = runtime.validate_module_name(plan["module_name"])
    filename = runtime.get_generated_script_filename_from_plan_filename(
        plan["plan_filename"], language=runtime.agent_project_language()
    )
    with acquire_script_target_lease(runtime, module_name, filename):
        if "_expected_script_revision_id" in plan:
            _assert_current_revision(
                runtime,
                module_name,
                filename,
                {"current_revision_id": plan.get("_expected_script_revision_id")},
            )
        return runtime.agent_generate_script_for_plan(
            run_id, step_key, plan, **kwargs
        )


def execute_script(runtime, run_id, step_key, script, **kwargs):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    with acquire_script_target_lease(runtime, module_name, filename):
        _assert_current_revision(runtime, module_name, filename, script)
        return runtime.agent_execute_generated_script(
            run_id, step_key, script, **kwargs
        )


def repair_script(runtime, run_id, step_key, script, **kwargs):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    with acquire_script_target_lease(runtime, module_name, filename):
        _assert_current_revision(runtime, module_name, filename, script)
        return runtime.agent_repair_script(run_id, step_key, script, **kwargs)


def resolve_dependency(runtime, name):
    dependencies = {
        "load_step_output": runtime.get_agent_script_preparation_output,
        "get_agent_run": runtime.get_agent_run_row,
        "update_agent_step": runtime.update_agent_step,
        "update_agent_run": lambda run_id, **values: update_preparation_agent_run(
            runtime, run_id, **values
        ),
        "append_agent_event": runtime.append_agent_event,
        "generate_script": lambda *args, **kwargs: generate_script(
            runtime, *args, **kwargs
        ),
        "execute_script": lambda *args, **kwargs: execute_script(
            runtime, *args, **kwargs
        ),
        "repair_script": lambda *args, **kwargs: repair_script(
            runtime, *args, **kwargs
        ),
        "analyze_failure": runtime.analyze_agent_script_preparation_failure,
        "save_script": runtime.save_agent_prepared_script,
        "build_generation_prompt": runtime.build_agent_script_generation_prompt,
        "build_repair_prompt": runtime.build_agent_script_repair_prompt,
        "resolve_script_filename": (
            lambda plan: runtime.get_generated_script_filename_from_plan_filename(
                plan["plan_filename"]
            )
        ),
        "current_time_ms": runtime.current_time_ms,
        "redact_value": lambda value: value,
        "is_cancelled_error": lambda error: isinstance(
            error, runtime.OpencodeTaskCancelled
        ),
        "make_id": lambda prefix: f"{prefix}-{runtime.uuid.uuid4().hex}",
        "waiting_run_status": "awaiting_script_action",
        "get_project_language": runtime.agent_project_language,
        "persist_state": lambda run_id, **values: persist_preparation_state_atomic(
            runtime, run_id, **values
        ),
    }
    return dependencies[name]


__all__ = [
    "analyze_failure",
    "apply_action_for_web",
    "apply_batch_action_for_web",
    "get_item_for_web",
    "get_snapshot_for_web",
    "generate_script",
    "load_step_output",
    "execute_script",
    "cancel_awaiting_agent_workflow",
    "claim_agent_resume",
    "claim_agent_step_start",
    "claim_agent_workflow_start",
    "clear_agent_suite",
    "complete_agent_workflow",
    "fail_agent_suite_execution",
    "find_agent_suite",
    "find_or_create_agent_suite",
    "finalize_agent_cancellation",
    "finish_with_script_preparation_barrier",
    "repair_script",
    "publish_agent_terminal",
    "persist_preparation_state_atomic",
    "plan_source_ready",
    "request_agent_workflow_cancel",
    "recover_interrupted_for_web",
    "reconcile_items_for_web",
    "resolve_dependency",
    "save_script",
    "script_preparation_barrier",
    "script_preparation_plan_barrier",
    "start_agent_step_atomic",
    "suite_execution_succeeded",
    "terminal_operation_succeeded",
    "update_preparation_agent_run",
    "run_agent_script_preparation_with_barrier",
]
