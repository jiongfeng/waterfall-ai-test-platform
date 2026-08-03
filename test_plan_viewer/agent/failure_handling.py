"""Persistent, human-in-the-loop handling for Agent script failures.

This domain module intentionally has no Flask or application-module dependency.
The composition root supplies infrastructure and workflow callbacks explicitly.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import traceback
from typing import Any, Callable

from test_plan_viewer.core.validation import normalize_string_list, validate_uid


CURRENT_AGENT_PIPELINE_VERSION = 2
AGENT_FAILURE_BUSY_STATUSES = frozenset({"analyzing", "retrying", "executing", "editing", "deleting"})


class AgentFailureCheckpointConflict(RuntimeError):
    """Raised when an item or checkpoint changed while an action was requested."""


@dataclass(frozen=True)
class FailureHandlingDependencies:
    project_operation_lock: Any
    opencode_cancelled_type: Any
    current_time_ms: Callable[..., Any]
    sha256_bytes: Callable[..., Any]
    redact_value: Callable[..., Any]
    get_agent_attempt: Callable[..., Any]
    serialize_agent_attempt: Callable[..., Any]
    get_test_job: Callable[..., Any]
    serialize_job: Callable[..., Any]
    get_generated_script_filename_from_plan_filename: Callable[..., Any]
    get_script_file: Callable[..., Any]
    get_project_root: Callable[..., Any]
    require_platform_database: Callable[..., Any]
    get_agent_run_steps_table: Callable[..., Any]
    get_current_project_id: Callable[..., Any]
    platform_mysql_connection: Callable[..., Any]
    compact_json_dumps: Callable[..., Any]
    load_json_column: Callable[..., Any]
    get_agent_step_row: Callable[..., Any]
    agent_start_step: Callable[..., Any]
    agent_finish_step: Callable[..., Any]
    update_agent_step: Callable[..., Any]
    get_agent_run_row: Callable[..., Any]
    update_agent_run: Callable[..., Any]
    append_agent_event: Callable[..., Any]
    call_agent_failure_analyst: Callable[..., Any]
    start_agent_attempt: Callable[..., Any]
    agent_generate_script_for_plan: Callable[..., Any]
    agent_repair_script: Callable[..., Any]
    finish_agent_attempt: Callable[..., Any]
    agent_attempt_failure_context: Callable[..., Any]
    agent_execute_single_script_for_review: Callable[..., Any]
    summarize_agent_execution_result: Callable[..., Any]
    sync_script_asset: Callable[..., Any]
    serialize_asset: Callable[..., Any]
    sha256_file: Callable[..., Any]
    delete_script_asset: Callable[..., Any]
    get_optional_agent_step_output: Callable[..., Any]
    dedupe_agent_scripts: Callable[..., Any]
    list_agent_item_retry_flows: Callable[..., Any]
    agent_register_task: Callable[..., Any]
    use_project_context: Callable[..., Any]
    use_author_context: Callable[..., Any]
    get_requirement_by_uid: Callable[..., Any]
    serialize_requirement: Callable[..., Any]
    agent_create_suite: Callable[..., Any]
    agent_run_suite: Callable[..., Any]
    agent_set_current_job: Callable[..., Any]
    agent_cleanup_task: Callable[..., Any]
    agent_fail_step: Callable[..., Any]


def failure_handling_dependencies_from_resolver(project_operation_lock, resolver):
    """Build lazy callbacks so composition-root monkey patches remain observable."""

    def lazy(name):
        return lambda *args, **kwargs: resolver(name)(*args, **kwargs)

    values = {
        field_name: lazy(field_name)
        for field_name in FailureHandlingDependencies.__dataclass_fields__
        if field_name not in {"project_operation_lock", "opencode_cancelled_type"}
    }
    return FailureHandlingDependencies(
        project_operation_lock=project_operation_lock,
        opencode_cancelled_type=resolver("opencode_cancelled_type"),
        **values,
    )


_DEPENDENCIES: FailureHandlingDependencies | None = None
AGENT_PROJECT_OPERATION_LOCK = threading.RLock()
OpencodeTaskCancelled = RuntimeError


def configure_failure_handling(dependencies: FailureHandlingDependencies):
    global _DEPENDENCIES, AGENT_PROJECT_OPERATION_LOCK, OpencodeTaskCancelled
    _DEPENDENCIES = dependencies
    AGENT_PROJECT_OPERATION_LOCK = dependencies.project_operation_lock
    OpencodeTaskCancelled = dependencies.opencode_cancelled_type


def _deps() -> FailureHandlingDependencies:
    if _DEPENDENCIES is None:
        raise RuntimeError("Agent failure handling dependencies are not configured.")
    return _DEPENDENCIES


def current_time_ms(*args, **kwargs):
    return _deps().current_time_ms(*args, **kwargs)


def sha256_bytes(*args, **kwargs):
    return _deps().sha256_bytes(*args, **kwargs)


def get_agent_attempt(*args, **kwargs):
    return _deps().get_agent_attempt(*args, **kwargs)


def serialize_agent_attempt(*args, **kwargs):
    return _deps().serialize_agent_attempt(*args, **kwargs)


def get_test_job(*args, **kwargs):
    return _deps().get_test_job(*args, **kwargs)


def serialize_job(*args, **kwargs):
    return _deps().serialize_job(*args, **kwargs)


def get_generated_script_filename_from_plan_filename(*args, **kwargs):
    return _deps().get_generated_script_filename_from_plan_filename(*args, **kwargs)


def get_script_file(*args, **kwargs):
    return _deps().get_script_file(*args, **kwargs)


def get_project_root(*args, **kwargs):
    return _deps().get_project_root(*args, **kwargs)


def require_platform_database(*args, **kwargs):
    return _deps().require_platform_database(*args, **kwargs)


def get_agent_run_steps_table(*args, **kwargs):
    return _deps().get_agent_run_steps_table(*args, **kwargs)


def get_current_project_id(*args, **kwargs):
    return _deps().get_current_project_id(*args, **kwargs)


def platform_mysql_connection(*args, **kwargs):
    return _deps().platform_mysql_connection(*args, **kwargs)


def compact_json_dumps(*args, **kwargs):
    return _deps().compact_json_dumps(*args, **kwargs)


def load_json_column(*args, **kwargs):
    return _deps().load_json_column(*args, **kwargs)


def get_agent_step_row(*args, **kwargs):
    return _deps().get_agent_step_row(*args, **kwargs)


def agent_start_step(*args, **kwargs):
    return _deps().agent_start_step(*args, **kwargs)


def agent_finish_step(*args, **kwargs):
    return _deps().agent_finish_step(*args, **kwargs)


def update_agent_step(*args, **kwargs):
    return _deps().update_agent_step(*args, **kwargs)


def get_agent_run_row(*args, **kwargs):
    return _deps().get_agent_run_row(*args, **kwargs)


def update_agent_run(*args, **kwargs):
    return _deps().update_agent_run(*args, **kwargs)


def append_agent_event(*args, **kwargs):
    return _deps().append_agent_event(*args, **kwargs)


def call_agent_failure_analyst(*args, **kwargs):
    return _deps().call_agent_failure_analyst(*args, **kwargs)


def start_agent_attempt(*args, **kwargs):
    return _deps().start_agent_attempt(*args, **kwargs)


def agent_generate_script_for_plan(*args, **kwargs):
    return _deps().agent_generate_script_for_plan(*args, **kwargs)


def agent_repair_script(*args, **kwargs):
    return _deps().agent_repair_script(*args, **kwargs)


def finish_agent_attempt(*args, **kwargs):
    return _deps().finish_agent_attempt(*args, **kwargs)


def agent_attempt_failure_context(*args, **kwargs):
    return _deps().agent_attempt_failure_context(*args, **kwargs)


def agent_execute_single_script_for_review(*args, **kwargs):
    return _deps().agent_execute_single_script_for_review(*args, **kwargs)


def summarize_agent_execution_result(*args, **kwargs):
    return _deps().summarize_agent_execution_result(*args, **kwargs)


def sync_script_asset(*args, **kwargs):
    return _deps().sync_script_asset(*args, **kwargs)


def serialize_asset(*args, **kwargs):
    return _deps().serialize_asset(*args, **kwargs)


def sha256_file(*args, **kwargs):
    return _deps().sha256_file(*args, **kwargs)


def delete_script_asset(*args, **kwargs):
    return _deps().delete_script_asset(*args, **kwargs)


def get_optional_agent_step_output(*args, **kwargs):
    return _deps().get_optional_agent_step_output(*args, **kwargs)


def dedupe_agent_scripts(*args, **kwargs):
    return _deps().dedupe_agent_scripts(*args, **kwargs)


def list_agent_item_retry_flows(*args, **kwargs):
    return _deps().list_agent_item_retry_flows(*args, **kwargs)


def agent_register_task(*args, **kwargs):
    return _deps().agent_register_task(*args, **kwargs)


def use_project_context(*args, **kwargs):
    return _deps().use_project_context(*args, **kwargs)


def use_author_context(*args, **kwargs):
    return _deps().use_author_context(*args, **kwargs)


def get_requirement_by_uid(*args, **kwargs):
    return _deps().get_requirement_by_uid(*args, **kwargs)


def serialize_requirement(*args, **kwargs):
    return _deps().serialize_requirement(*args, **kwargs)


def agent_create_suite(*args, **kwargs):
    return _deps().agent_create_suite(*args, **kwargs)


def agent_run_suite(*args, **kwargs):
    return _deps().agent_run_suite(*args, **kwargs)


def agent_set_current_job(*args, **kwargs):
    return _deps().agent_set_current_job(*args, **kwargs)


def agent_cleanup_task(*args, **kwargs):
    return _deps().agent_cleanup_task(*args, **kwargs)


def agent_fail_step(*args, **kwargs):
    return _deps().agent_fail_step(*args, **kwargs)


class _AgentDiagnosticsProxy:
    @staticmethod
    def redact_diagnostic_value(value, **_kwargs):
        return _deps().redact_value(value)


agent_diagnostics = _AgentDiagnosticsProxy()


def _agent_diagnostic_dependencies():
    return None


# DOMAIN_IMPLEMENTATION_START
def skip_agent_plan_review(run_id, plans):
    """Keep the legacy row readable while omitting review from pipeline v2."""

    update_agent_step(
        run_id,
        "review_plans",
        status="skipped",
        input_data={"plan_count": len(plans)},
        output_data={
            "reason": "removed_in_failure_checkpoint_v2",
            "plans": plans,
            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
        },
        counts={"plans": len(plans), "skipped": len(plans)},
        error="",
        started=True,
        finished=True,
    )
    append_agent_event(
        run_id,
        "review_plans",
        "status",
        "计划审查已在新版流程中移除。",
        {"status": "skipped", "reason": "removed_in_failure_checkpoint_v2"},
    )
    return plans


def agent_failure_item_id(run_id, source_step, failure):
    stable_source = "|".join(
        [
            str(run_id or ""),
            str(source_step or ""),
            str(failure.get("attempt_id") or failure.get("failure_id") or ""),
            str(failure.get("module_name") or ""),
            str(failure.get("plan_filename") or ""),
            str(failure.get("filename") or ""),
        ]
    )
    return f"failure-{sha256_bytes(stable_source.encode('utf-8'))[:24]}"


def redact_agent_failure_value(value):
    try:
        return agent_diagnostics.redact_diagnostic_value(
            value,
            dependencies=_agent_diagnostic_dependencies(),
        )
    except Exception:
        if isinstance(value, dict):
            return {"redaction_failed": True}
        if isinstance(value, list):
            return []
        return "[已隐藏]"


def redact_agent_failure_text(value):
    redacted = redact_agent_failure_value(str(value or ""))
    return redacted if isinstance(redacted, str) else "[已隐藏]"


def resolve_agent_failure_artifact_path(value):
    text = str(value or "").strip()
    if not text:
        return None
    marker = "${PROJECT_ROOT}"
    if text == marker or text.startswith(f"{marker}/"):
        root = Path(get_project_root()).resolve(strict=False)
        candidate = (root / text[len(marker):].lstrip("/")).resolve(strict=False)
    elif "${" in text:
        return None
    else:
        root = Path(get_project_root()).resolve(strict=False)
        candidate = Path(text).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def agent_failure_script_descriptor(failure):
    module_name = str(failure.get("module_name") or "").strip()
    filename = str(failure.get("filename") or "").strip()
    if not filename and failure.get("plan_filename"):
        try:
            filename = get_generated_script_filename_from_plan_filename(failure.get("plan_filename"))
        except Exception:
            filename = ""
    script_file = None
    if module_name and filename:
        try:
            script_file = get_script_file(module_name, filename)
        except Exception:
            script_file = None
    partial_paths = [
        path
        for value in (failure.get("partial_artifacts") or [])
        if isinstance(value, str) and value.strip()
        for path in [resolve_agent_failure_artifact_path(value)]
        if path is not None
    ]
    candidate_file = next(
        (path for path in partial_paths if path.suffix == ".ts" and path.exists() and path.is_file()),
        None,
    )
    script_exists = bool(script_file and script_file.exists() and script_file.is_file())
    candidate_exists = bool(candidate_file and candidate_file.exists() and candidate_file.is_file())
    return {
        "module_name": module_name,
        "filename": filename,
        "script_path": redact_agent_failure_text(script_file or ""),
        "candidate_path": redact_agent_failure_text(candidate_file or ""),
        "script_exists": script_exists,
        "candidate_exists": candidate_exists,
        "editable_artifact_kind": "formal_script" if script_exists else ("candidate" if candidate_exists else "none"),
        "can_edit": script_exists or candidate_exists,
        "can_execute": script_exists,
        "can_delete": script_exists,
    }


def build_agent_failure_evidence(run_id, source_step, failure):
    attempt_id = str(failure.get("attempt_id") or failure.get("failure_id") or "")
    attempt = None
    if attempt_id:
        try:
            attempt = serialize_agent_attempt(get_agent_attempt(run_id, attempt_id))
        except Exception:
            attempt = None
    job_id = str(failure.get("job_id") or (attempt or {}).get("job_id") or "")
    job = None
    if job_id:
        try:
            job = serialize_job(get_test_job(job_id))
        except Exception:
            job = None

    evidence = [
        {
            "evidence_id": "failure",
            "kind": "failure",
            "title": "失败信息",
            "data": {
                "source_step": source_step,
                "error_type": failure.get("error_type") or "unknown",
                "error": failure.get("error") or "",
                "failed_at": failure.get("failed_at"),
                "execution": failure.get("execution") or {},
            },
        }
    ]
    if attempt:
        evidence.append(
            {
                "evidence_id": f"attempt:{attempt_id}",
                "kind": "attempt",
                "title": "执行尝试",
                "data": attempt,
            }
        )
    if job:
        evidence.append(
            {
                "evidence_id": f"job:{job_id}",
                "kind": "job",
                "title": "模型任务与日志",
                "data": {
                    "job_id": job.get("job_id"),
                    "job_type": job.get("job_type"),
                    "status": job.get("status"),
                    "prompt": job.get("prompt") or "",
                    "log_tail": job.get("log_tail") or "",
                    "error": job.get("error") or "",
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                },
            }
        )
    partial_artifacts = failure.get("partial_artifacts") or []
    if partial_artifacts:
        evidence.append(
            {
                "evidence_id": "partial-artifacts",
                "kind": "artifacts",
                "title": "部分产物",
                "data": {"paths": partial_artifacts},
            }
        )
    return redact_agent_failure_value(evidence)


def compute_agent_failure_evidence_hash(evidence):
    normalized = json.dumps(evidence or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(normalized.encode("utf-8"))


def build_agent_failure_item(run_id, source_step, failure):
    descriptor = agent_failure_script_descriptor(failure)
    evidence = build_agent_failure_evidence(run_id, source_step, failure)
    safe_failure = redact_agent_failure_value(failure)
    safe_failure = safe_failure if isinstance(safe_failure, dict) else {}
    now_ms = current_time_ms()
    item = {
        "item_id": agent_failure_item_id(run_id, source_step, failure),
        "source_step": source_step,
        "source_type": "generation" if source_step == "generate_scripts" else "repair",
        "root_attempt_id": failure.get("attempt_id") or failure.get("failure_id") or "",
        "module_uid": failure.get("module_uid") or "",
        "module_name": descriptor["module_name"],
        "plan_filename": failure.get("plan_filename") or "",
        "filename": descriptor["filename"],
        "status": "unresolved",
        "resolution": "",
        "included_in_suite": False,
        "error_type": safe_failure.get("error_type") or "unknown",
        "error": safe_failure.get("error") or "失败信息已隐藏。",
        "failed_at": safe_failure.get("failed_at"),
        "source_failure": safe_failure,
        "evidence_snapshot": evidence,
        "evidence_version": 1,
        "evidence_hash": compute_agent_failure_evidence_hash(evidence),
        "analysis": None,
        "analysis_version": 0,
        "analysis_evidence_hash": "",
        "analysis_stale": False,
        "latest_attempt": None,
        "latest_action": None,
        "current_script": None,
        "created_at": now_ms,
        "updated_at": now_ms,
        **descriptor,
    }
    return item


def refresh_agent_failure_item_capabilities(item):
    item = dict(item or {})
    descriptor = agent_failure_script_descriptor(
        {
            **(item.get("source_failure") if isinstance(item.get("source_failure"), dict) else {}),
            "module_name": item.get("module_name"),
            "plan_filename": item.get("plan_filename"),
            "filename": item.get("filename"),
            "partial_artifacts": (
                (item.get("source_failure") or {}).get("partial_artifacts")
                if isinstance(item.get("source_failure"), dict)
                else []
            ),
        }
    )
    item.update(descriptor)
    if item.get("status") in {"deleted", "ignored", "kept_unresolved"}:
        item.update({"can_edit": False, "can_execute": False, "can_delete": False})
    return item


def normalize_agent_failure_checkpoint_output(output):
    output = dict(output or {})
    items = []
    for item in output.get("failure_items") or []:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        if not item.get("item_id") and item.get("failure_item_id"):
            item["item_id"] = item.get("failure_item_id")
        if not item.get("source_step") and item.get("source_stage"):
            item["source_step"] = item.get("source_stage")
        if not item.get("source_type"):
            item["source_type"] = (
                "generation"
                if item.get("source_step") == "generate_scripts"
                else "repair"
            )
        item = refresh_agent_failure_item_capabilities(item)
        item["error"] = redact_agent_failure_text(item.get("error"))
        for key in (
            "source_failure", "evidence_snapshot", "analysis",
            "latest_attempt", "latest_action", "current_script",
        ):
            if item.get(key) is not None:
                item[key] = redact_agent_failure_value(item[key])
        items.append(item)
    output["failure_items"] = items
    output["generation_failures"] = [item for item in items if item.get("source_step") == "generate_scripts"]
    output["repair_failures"] = [item for item in items if item.get("source_step") == "repair_scripts"]
    output["scripts"] = [
        item.get("current_script")
        for item in items
        if item.get("status") == "resolved"
        and item.get("included_in_suite")
        and isinstance(item.get("current_script"), dict)
    ]
    output["unresolved_count"] = sum(
        1
        for item in items
        if item.get("status") not in {"resolved", "deleted", "ignored", "kept_unresolved"}
    )
    output["resolved_count"] = sum(1 for item in items if item.get("status") == "resolved")
    output["handled_count"] = sum(
        1 for item in items if item.get("status") in {"resolved", "deleted", "ignored", "kept_unresolved"}
    )
    output["version"] = max(1, int(output.get("version") or 1))
    output["pipeline_version"] = CURRENT_AGENT_PIPELINE_VERSION
    return output


def mutate_agent_failure_checkpoint(run_id, mutator, *, allow_continuing=False):
    config = require_platform_database()
    table = get_agent_run_steps_table(config)
    project_id = get_current_project_id()
    run_id = validate_uid(run_id, "run_id")
    with AGENT_PROJECT_OPERATION_LOCK:
        with platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT output_json
                    FROM {table}
                    WHERE project_id = %s AND run_id = %s AND step_key = 'review_failed_scripts'
                    FOR UPDATE
                    """,
                    (project_id, run_id),
                )
                row = cursor.fetchone()
                if not row:
                    raise FileNotFoundError("失败分析与处置步骤不存在。")
                output = load_json_column(row.get("output_json"), {})
                output = output if isinstance(output, dict) else {}
                if output.get("continuing") and not allow_continuing:
                    raise AgentFailureCheckpointConflict("失败处置正在进入下一阶段，请勿重复操作。")
                if output.get("cancelling") and not allow_continuing:
                    raise AgentFailureCheckpointConflict("失败处置正在取消任务，请勿重复操作。")
                result = mutator(output)
                output["version"] = int(output.get("version") or 0) + 1
                output = normalize_agent_failure_checkpoint_output(output)
                counts = {
                    "failed": len(output["failure_items"]),
                    "generation_failed": len(output["generation_failures"]),
                    "repair_failed": len(output["repair_failures"]),
                    "resolved": output["resolved_count"],
                    "unresolved": output["unresolved_count"],
                    "handled": output["handled_count"],
                }
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET output_json = %s, counts_json = %s, updated_at = %s
                    WHERE project_id = %s AND run_id = %s AND step_key = 'review_failed_scripts'
                    """,
                    (
                        compact_json_dumps(output),
                        compact_json_dumps(counts),
                        current_time_ms(),
                        project_id,
                        run_id,
                    ),
                )
            connection.commit()
    return result, output


def get_agent_failure_checkpoint_output(run_id):
    row = get_agent_step_row(run_id, "review_failed_scripts")
    if not row:
        return None
    output = load_json_column(row.get("output_json"), {})
    return normalize_agent_failure_checkpoint_output(output if isinstance(output, dict) else {})


def get_agent_failure_item(run_id, item_id):
    output = get_agent_failure_checkpoint_output(run_id)
    if not output:
        return None
    return next((item for item in output["failure_items"] if item.get("item_id") == item_id), None)


def replace_agent_failure_item(output, replacement, expected_statuses=None):
    item_id = replacement.get("item_id")
    items = list(output.get("failure_items") or [])
    for index, current in enumerate(items):
        if current.get("item_id") != item_id:
            continue
        if expected_statuses is not None and current.get("status") not in set(expected_statuses):
            raise AgentFailureCheckpointConflict("该失败项状态已变化，请刷新后重试。")
        items[index] = {**current, **replacement, "updated_at": current_time_ms()}
        output["failure_items"] = items
        return items[index]
    raise FileNotFoundError("失败项不存在。")


def replace_agent_failure_item_when_idle(output, replacement, expected_statuses=None):
    item_id = replacement.get("item_id")
    active = next(
        (
            item
            for item in output.get("failure_items") or []
            if item.get("status") in AGENT_FAILURE_BUSY_STATUSES
            and item.get("item_id") != item_id
        ),
        None,
    )
    if active:
        raise AgentFailureCheckpointConflict("另一个失败项正在处理中，请等待完成后重试。")
    return replace_agent_failure_item(output, replacement, expected_statuses)


def prepare_agent_failure_checkpoint(run_id, generated_failures, repair_failures):
    step_key = "review_failed_scripts"
    generated_failures = list(generated_failures or [])
    repair_failures = list(repair_failures or [])
    failures = [
        *(("generate_scripts", item) for item in generated_failures),
        *(("repair_scripts", item) for item in repair_failures),
    ]
    agent_start_step(
        run_id,
        step_key,
        {
            "failure_count": len(failures),
            "generation_failure_count": len(generated_failures),
            "repair_failure_count": len(repair_failures),
            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
        },
    )
    if not failures:
        output = normalize_agent_failure_checkpoint_output(
            {
                "failure_items": [],
                "version": 1,
                "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
            }
        )
        agent_finish_step(
            run_id,
            step_key,
            output,
            {"failed": 0, "generation_failed": 0, "repair_failed": 0, "resolved": 0, "unresolved": 0},
        )
        return []

    items = [build_agent_failure_item(run_id, source_step, failure) for source_step, failure in failures]
    output = normalize_agent_failure_checkpoint_output(
        {
            "failure_items": items,
            "version": 1,
            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
            "awaiting_since": current_time_ms(),
        }
    )
    counts = {
        "failed": len(items),
        "generation_failed": len(output["generation_failures"]),
        "repair_failed": len(output["repair_failures"]),
        "resolved": 0,
        "unresolved": len(items),
    }
    update_agent_step(
        run_id,
        step_key,
        status="awaiting_action",
        output_data=output,
        counts=counts,
        error="",
    )
    run_row = get_agent_run_row(run_id) or {}
    summary = load_json_column(run_row.get("summary_json"), {})
    summary = summary if isinstance(summary, dict) else {}
    summary.update(
        {
            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
            "failure_checkpoint": {
                "total": len(items),
                "generation_failed": len(output["generation_failures"]),
                "repair_failed": len(output["repair_failures"]),
                "unresolved": len(items),
            },
        }
    )
    update_agent_run(
        run_id,
        status="awaiting_failure_action",
        current_step=step_key,
        summary=summary,
        error="",
    )
    append_agent_event(
        run_id,
        step_key,
        "status",
        f"已收集 {len(items)} 个失败项，等待分析和处置。",
        {"status": "awaiting_failure_action", "counts": counts},
    )
    return items


def normalize_agent_failure_analysis(parsed, item):
    parsed = parsed if isinstance(parsed, dict) else {}
    recommendation = parsed.get("recommendation")
    if not isinstance(recommendation, dict):
        recommendation = {}
    category = str(parsed.get("root_cause_category") or "unknown").strip().lower()
    allowed_categories = {
        "infrastructure",
        "model",
        "output_validation",
        "test_code",
        "product_defect",
        "data",
        "setup",
        "flaky",
        "unknown",
    }
    if category not in allowed_categories:
        category = "unknown"
    confidence = parsed.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "summary": str(parsed.get("summary") or item.get("error") or "暂时无法确定失败原因。").strip(),
        "root_cause_category": category,
        "confidence": confidence,
        "facts": normalize_string_list(parsed.get("facts")),
        "hypotheses": normalize_string_list(parsed.get("hypotheses")),
        "evidence_refs": normalize_string_list(parsed.get("evidence_refs")),
        "missing_evidence": normalize_string_list(parsed.get("missing_evidence")),
        "recommended_action": str(
            parsed.get("recommended_action")
            or recommendation.get("action")
            or ("regenerate" if item.get("source_type") == "generation" else "repair")
        ).strip(),
        "suggestion": str(
            parsed.get("suggestion")
            or recommendation.get("suggestion")
            or parsed.get("prompt_patch")
            or ""
        ).strip(),
        "prompt_patch": str(parsed.get("prompt_patch") or recommendation.get("prompt_patch") or "").strip(),
        "risks": normalize_string_list(parsed.get("risks")),
        "generated_at": current_time_ms(),
    }


def analyze_agent_failure_item(run_id, item_id, force=False):
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    if item.get("analysis") and not item.get("analysis_stale") and not force:
        return item
    if item.get("status") in AGENT_FAILURE_BUSY_STATUSES:
        raise AgentFailureCheckpointConflict("该失败项正在处理中。")
    previous_status = item.get("status") or "unresolved"

    claimed = {
        **item,
        "status": "analyzing",
        "latest_action": {"type": "analyze", "status": "running", "started_at": current_time_ms()},
    }

    def claim_analysis(output):
        active = next(
            (
                other
                for other in output.get("failure_items") or []
                if other.get("status") in AGENT_FAILURE_BUSY_STATUSES
                and other.get("item_id") != item_id
            ),
            None,
        )
        if active:
            raise AgentFailureCheckpointConflict("另一个失败项正在处理中，请等待完成后重试。")
        return replace_agent_failure_item(output, claimed, {previous_status})

    mutate_agent_failure_checkpoint(run_id, claim_analysis)
    try:
        parsed = call_agent_failure_analyst(
            run_id,
            "review_failed_scripts",
            "分析 Playwright 脚本失败原因并给出重新生成或重新修复建议。不要修改、删除或裁决文件。",
            {
                "kind": "failure_analysis",
                "failure_item": {
                    key: claimed.get(key)
                    for key in (
                        "item_id",
                        "source_type",
                        "module_name",
                        "plan_filename",
                        "filename",
                        "error_type",
                        "error",
                        "evidence_snapshot",
                    )
                },
                "response_schema": {
                    "summary": "string",
                    "root_cause_category": "infrastructure|model|output_validation|test_code|product_defect|data|setup|flaky|unknown",
                    "confidence": "0..1",
                    "facts": ["string"],
                    "hypotheses": ["string"],
                    "evidence_refs": ["evidence_id"],
                    "missing_evidence": ["string"],
                    "recommended_action": "regenerate|repair|edit|reexecute|retry_infrastructure|ignore",
                    "suggestion": "string",
                    "prompt_patch": "string",
                    "risks": ["string"],
                },
            },
        )
        analysis = normalize_agent_failure_analysis(parsed, claimed)
        updated = {
            **claimed,
            "status": previous_status,
            "analysis": analysis,
            "analysis_version": int(claimed.get("analysis_version") or 0) + 1,
            "analysis_evidence_hash": claimed.get("evidence_hash") or "",
            "analysis_stale": False,
            "latest_action": {
                "type": "analyze",
                "status": "succeeded",
                "finished_at": current_time_ms(),
            },
        }
        result, _ = mutate_agent_failure_checkpoint(
            run_id,
            lambda output: replace_agent_failure_item(output, updated, {"analyzing"}),
        )
        return refresh_agent_failure_item_capabilities(result)
    except Exception as exc:
        failed = {
            **claimed,
            "status": previous_status,
            "latest_action": {
                "type": "analyze",
                "status": "failed",
                "error": redact_agent_failure_text(exc),
                "finished_at": current_time_ms(),
            },
        }
        try:
            mutate_agent_failure_checkpoint(
                run_id,
                lambda output: replace_agent_failure_item(output, failed, {"analyzing"}),
            )
        except Exception:
            pass
        raise
    finally:
        agent_set_current_job(run_id, "")
        agent_cleanup_task(run_id)


def agent_failure_item_with_evidence(item, action_type, status, data):
    evidence = list(item.get("evidence_snapshot") or [])
    now_ms = current_time_ms()
    evidence.append(
        redact_agent_failure_value(
            {
                "evidence_id": f"{action_type}:{now_ms}",
                "kind": action_type,
                "title": {
                    "retry": "重试结果",
                    "execute": "执行验证结果",
                    "edit": "人工编辑",
                    "delete": "归档删除",
                    "ignore": "保留未解决项",
                }.get(action_type, "处置记录"),
                "data": {"status": status, **(data if isinstance(data, dict) else {"value": data})},
            }
        )
    )
    return {
        **item,
        "evidence_snapshot": evidence,
        "evidence_version": int(item.get("evidence_version") or 0) + 1,
        "evidence_hash": compute_agent_failure_evidence_hash(evidence),
        "analysis_stale": bool(item.get("analysis")),
        "updated_at": now_ms,
    }


def claim_agent_failure_item(run_id, item_id, action_type, allowed_statuses):
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    if item.get("status") not in set(allowed_statuses):
        raise AgentFailureCheckpointConflict("该失败项当前不能执行此操作，请刷新后重试。")
    claimed = {
        **item,
        "status": {"execute": "executing", "delete": "deleting"}.get(action_type, f"{action_type}ing"),
        "latest_action": {
            "type": action_type,
            "status": "running",
            "started_at": current_time_ms(),
        },
    }
    def claim(output):
        active = next(
            (
                other
                for other in output.get("failure_items") or []
            if other.get("status") in AGENT_FAILURE_BUSY_STATUSES
                and other.get("item_id") != item_id
            ),
            None,
        )
        if active:
            raise AgentFailureCheckpointConflict("另一个失败项正在处理中，请等待完成后重试。")
        return replace_agent_failure_item(output, claimed, allowed_statuses)

    result, _ = mutate_agent_failure_checkpoint(run_id, claim)
    return result, item.get("status")


def finish_agent_failure_item_action(run_id, item, expected_status, final_status):
    updated = {
        **item,
        "status": final_status,
        "latest_action": {
            **(item.get("latest_action") or {}),
            "status": (item.get("latest_action") or {}).get("status") or "succeeded",
            "finished_at": current_time_ms(),
        },
    }
    result, _ = mutate_agent_failure_checkpoint(
        run_id,
        lambda output: replace_agent_failure_item(output, updated, {expected_status}),
    )
    return refresh_agent_failure_item_capabilities(result)


def rollback_agent_failure_item_action(run_id, item, expected_status, previous_status, action_type, error):
    rollback = {
        **item,
        "status": previous_status,
        "latest_action": {
            "type": action_type,
            "status": "failed",
            "error": redact_agent_failure_text(error),
            "finished_at": current_time_ms(),
        },
    }
    try:
        mutate_agent_failure_checkpoint(
            run_id,
            lambda output: replace_agent_failure_item(output, rollback, {expected_status}),
        )
    except Exception:
        pass


def retry_agent_failure_item(run_id, item_id, instructions=""):
    claimed, previous_status = claim_agent_failure_item(
        run_id,
        item_id,
        "retry",
        {"unresolved", "pending_verification"},
    )
    try:
        source_step = claimed.get("source_step")
        failure = claimed.get("source_failure") if isinstance(claimed.get("source_failure"), dict) else {}
        module_name = claimed.get("module_name") or failure.get("module_name") or ""
        plan_filename = claimed.get("plan_filename") or failure.get("plan_filename") or ""
        filename = claimed.get("filename") or failure.get("filename") or ""
        item_key = f"{module_name}/{plan_filename or filename}"
        attempt = start_agent_attempt(
            run_id,
            source_step,
            "script_regeneration" if source_step == "generate_scripts" else "script_repair",
            item_key,
            module_uid=claimed.get("module_uid"),
            module_name=module_name,
            plan_filename=plan_filename,
            filename=filename,
            input_snapshot={
                "failure_item_id": item_id,
                "source_failure": failure,
                "instructions": str(instructions or ""),
                "analysis": claimed.get("analysis"),
            },
            parent_attempt_id=claimed.get("root_attempt_id") or None,
        )
        attempt_id = attempt["attempt_id"]
    except Exception as exc:
        rollback_agent_failure_item_action(
            run_id,
            claimed,
            "retrying",
            previous_status,
            "retry",
            exc,
        )
        raise
    try:
        if source_step == "generate_scripts":
            source = {
                **failure,
                "module_name": module_name,
                "plan_filename": plan_filename,
            }
            script = agent_generate_script_for_plan(
                run_id,
                "review_failed_scripts",
                source,
                instructions=instructions,
            )
            outcome_type = "regenerated"
        elif source_step == "repair_scripts":
            source = {
                **failure,
                **(claimed.get("current_script") if isinstance(claimed.get("current_script"), dict) else {}),
                "module_name": module_name,
                "plan_filename": plan_filename,
                "filename": filename,
            }
            script = agent_repair_script(
                run_id,
                "review_failed_scripts",
                source,
                instructions=instructions,
            )
            outcome_type = "repaired"
        else:
            raise ValueError("不支持的失败来源。")
        script = {**script, "attempt_id": attempt_id}
        asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
        finish_agent_attempt(
            run_id,
            attempt_id,
            "succeeded",
            outcome_type=outcome_type,
            verification_status="not_run",
            job_id=script.get("job_id") or script.get("repair_job_id"),
            test_run_id=script.get("repair_test_run_id"),
            result_id=script.get("repair_result_id"),
            asset_id=asset.get("asset_id"),
            revision_id=asset.get("current_revision_id"),
            source_asset_id=asset.get("from_plan_asset_id"),
            output_summary=script,
            artifact_refs=[
                {
                    "source": "test_assets",
                    "artifact_type": "script",
                    "asset_id": asset.get("asset_id"),
                    "revision_id": asset.get("current_revision_id"),
                }
            ]
            if asset.get("asset_id")
            else [],
        )
        updated = agent_failure_item_with_evidence(
            claimed,
            "retry",
            "succeeded",
            {"attempt_id": attempt_id, "script": script},
        )
        updated.update(
            {
                "current_script": script,
                "filename": script.get("filename") or filename,
                "latest_attempt": {
                    "attempt_id": attempt_id,
                    "source_step": source_step,
                    "status": "succeeded",
                    "outcome_type": outcome_type,
                },
                "latest_action": {
                    "type": "retry",
                    "status": "succeeded",
                    "attempt_id": attempt_id,
                },
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "retrying", "pending_verification")
    except Exception as exc:
        failure_context = agent_attempt_failure_context(exc)
        safe_error = redact_agent_failure_text(exc)
        safe_failure_context = redact_agent_failure_value(failure_context)
        safe_failure_context = safe_failure_context if isinstance(safe_failure_context, dict) else {}
        finish_agent_attempt(
            run_id,
            attempt_id,
            "failed",
            verification_status="failed",
            job_id=failure_context["job_id"],
            test_run_id=failure_context["test_run_id"],
            result_id=failure_context["result_id"],
            asset_id=failure_context["asset_id"],
            error_type=failure_context["error_type"],
            error_message=str(exc),
            error_stack=traceback.format_exc(),
            output_summary={"failure_item_id": item_id, "error": str(exc)},
            artifact_refs=[
                {"source": "partial", "path": path}
                for path in failure_context["partial_artifacts"]
            ],
        )
        updated = agent_failure_item_with_evidence(
            claimed,
            "retry",
            "failed",
            {
                "attempt_id": attempt_id,
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
                "partial_artifacts": failure_context["partial_artifacts"],
            },
        )
        updated.update(
            {
                "error_type": safe_failure_context.get("error_type") or failure_context["error_type"],
                "error": safe_error,
                "source_failure": {
                    **failure,
                    "partial_artifacts": safe_failure_context.get("partial_artifacts") or [],
                },
                "latest_attempt": {
                    "attempt_id": attempt_id,
                    "source_step": source_step,
                    "status": "failed",
                    "error": safe_error,
                },
                "latest_action": {
                    "type": "retry",
                    "status": "failed",
                    "attempt_id": attempt_id,
                    "error": safe_error,
                },
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "retrying", "unresolved")
    finally:
        agent_set_current_job(run_id, "")
        agent_cleanup_task(run_id)


def execute_agent_failure_item(run_id, item_id):
    claimed, previous_status = claim_agent_failure_item(
        run_id,
        item_id,
        "execute",
        {"unresolved", "pending_verification"},
    )
    try:
        if not claimed.get("can_execute"):
            raise AgentFailureCheckpointConflict("没有可执行的正式脚本。")
        script = {
            **(
                claimed.get("current_script")
                if isinstance(claimed.get("current_script"), dict)
                else {}
            ),
            "module_name": claimed.get("module_name"),
            "plan_filename": claimed.get("plan_filename"),
            "filename": claimed.get("filename"),
        }
        attempt = start_agent_attempt(
            run_id,
            "execute_scripts",
            "script_verification",
            f"{script['module_name']}/{script['filename']}",
            module_name=script["module_name"],
            plan_filename=script.get("plan_filename"),
            filename=script["filename"],
            input_snapshot={"failure_item_id": item_id, "script": script},
            parent_attempt_id=claimed.get("root_attempt_id") or None,
        )
        attempt_id = attempt["attempt_id"]
    except Exception as exc:
        rollback_agent_failure_item_action(
            run_id,
            claimed,
            "executing",
            previous_status,
            "execute",
            exc,
        )
        raise
    try:
        result = agent_execute_single_script_for_review(
            run_id,
            "review_failed_scripts",
            script,
        )
        finish_agent_attempt(
            run_id,
            attempt_id,
            "succeeded",
            outcome_type="passed",
            verification_status="passed",
            job_id=result.get("job_id"),
            test_run_id=result.get("run_id"),
            result_id=result.get("result_id"),
            output_summary=result,
        )
        updated = agent_failure_item_with_evidence(
            claimed,
            "execute",
            "succeeded",
            {"attempt_id": attempt_id, "result": summarize_agent_execution_result(result)},
        )
        updated.update(
            {
                "current_script": script,
                "resolution": "verified",
                "included_in_suite": True,
                "latest_attempt": {
                    "attempt_id": attempt_id,
                    "source_step": "execute_scripts",
                    "status": "succeeded",
                    "verification_status": "passed",
                },
                "latest_action": {
                    "type": "execute",
                    "status": "succeeded",
                    "attempt_id": attempt_id,
                },
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "executing", "resolved")
    except Exception as exc:
        failure_context = agent_attempt_failure_context(exc)
        safe_error = redact_agent_failure_text(exc)
        safe_failure_context = redact_agent_failure_value(failure_context)
        safe_failure_context = safe_failure_context if isinstance(safe_failure_context, dict) else {}
        finish_agent_attempt(
            run_id,
            attempt_id,
            "failed",
            verification_status="failed",
            job_id=failure_context["job_id"],
            test_run_id=failure_context["test_run_id"],
            result_id=failure_context["result_id"],
            error_type=failure_context["error_type"],
            error_message=str(exc),
            error_stack=traceback.format_exc(),
            output_summary={"failure_item_id": item_id, "error": str(exc)},
        )
        updated = agent_failure_item_with_evidence(
            claimed,
            "execute",
            "failed",
            {
                "attempt_id": attempt_id,
                "error_type": failure_context["error_type"],
                "error": str(exc),
                "job_id": failure_context["job_id"],
                "test_run_id": failure_context["test_run_id"],
                "result_id": failure_context["result_id"],
            },
        )
        updated.update(
            {
                "error_type": safe_failure_context.get("error_type") or failure_context["error_type"],
                "error": safe_error,
                "resolution": "",
                "included_in_suite": False,
                "latest_attempt": {
                    "attempt_id": attempt_id,
                    "source_step": "execute_scripts",
                    "status": "failed",
                    "verification_status": "failed",
                    "error": safe_error,
                },
                "latest_action": {
                    "type": "execute",
                    "status": "failed",
                    "attempt_id": attempt_id,
                    "error": safe_error,
                },
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "executing", "unresolved")
    finally:
        agent_set_current_job(run_id, "")
        agent_cleanup_task(run_id)


def read_agent_failure_item_script(run_id, item_id):
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    formal = get_script_file(item.get("module_name"), item.get("filename"))
    candidate = resolve_agent_failure_artifact_path(item.get("candidate_path"))
    if item.get("script_exists") and formal.is_file():
        path = formal
        artifact_kind = "formal_script"
    elif item.get("candidate_exists") and candidate is not None and candidate.is_file():
        path = candidate
        artifact_kind = "candidate"
    else:
        raise FileNotFoundError("该失败项没有可编辑脚本或候选稿。")
    return {
        "item_id": item_id,
        "artifact_kind": artifact_kind,
        "path": redact_agent_failure_text(path),
        "content": path.read_text(encoding="utf-8"),
        "content_sha256": sha256_file(path),
    }


def save_agent_failure_item_script(run_id, item_id, content, expected_content_sha256=""):
    content = str(content or "")
    if not content.strip():
        raise ValueError("脚本内容不能为空。")
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    if not item.get("module_name") or not item.get("filename"):
        raise ValueError("失败项缺少脚本模块或文件名。")
    if not (item.get("script_exists") or item.get("candidate_exists")):
        raise AgentFailureCheckpointConflict("该失败项没有可编辑脚本或候选稿。")
    claimed, previous_status = claim_agent_failure_item(
        run_id,
        item_id,
        "edit",
        {"unresolved", "pending_verification"},
    )
    script_file = None
    target_existed = False
    previous_bytes = b""
    target_snapshot_ready = False
    target_write_started = False
    try:
        script_file = get_script_file(claimed["module_name"], claimed["filename"])
        source_file = (
            get_script_file(claimed["module_name"], claimed["filename"])
            if claimed.get("script_exists")
            else resolve_agent_failure_artifact_path(claimed.get("candidate_path"))
        )
        target_existed = script_file.is_file()
        previous_bytes = script_file.read_bytes() if target_existed else b""
        target_snapshot_ready = True
        expected_hash = str(expected_content_sha256 or "").strip()
        if expected_hash and (
            source_file is None
            or not source_file.is_file()
            or sha256_file(source_file) != expected_hash
        ):
            raise AgentFailureCheckpointConflict("脚本内容已被其他操作修改，请刷新后重新编辑。")
        script_file.parent.mkdir(parents=True, exist_ok=True)
        target_write_started = True
        script_file.write_text(content, encoding="utf-8", newline="")
        asset = sync_script_asset(
            claimed["module_name"],
            script_file,
            change_source="manual",
            message=f"agent failure item edit: {claimed['module_name']}/{claimed['filename']}",
        )
        current_script = {
            "module_name": claimed["module_name"],
            "plan_filename": claimed.get("plan_filename") or "",
            "filename": claimed["filename"],
            "path": str(script_file),
            "asset": serialize_asset(asset),
        }
        updated = agent_failure_item_with_evidence(
            claimed,
            "edit",
            "succeeded",
            {
                "path": str(script_file),
                "content_sha256": sha256_bytes(content.encode("utf-8")),
                "asset": serialize_asset(asset),
            },
        )
        updated.update(
            {
                "current_script": current_script,
                "resolution": "",
                "included_in_suite": False,
                "latest_action": {"type": "edit", "status": "succeeded"},
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "editing", "pending_verification")
    except Exception as exc:
        try:
            if target_write_started and target_snapshot_ready and script_file is not None:
                if target_existed:
                    script_file.write_bytes(previous_bytes)
                elif script_file.exists():
                    script_file.unlink()
        except OSError:
            pass
        rollback_agent_failure_item_action(run_id, claimed, "editing", previous_status, "edit", exc)
        raise


def delete_agent_failure_item(run_id, item_id):
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    if not item.get("can_delete"):
        raise AgentFailureCheckpointConflict("该失败项没有可归档删除的正式脚本，请使用保留未解决项。")
    claimed, previous_status = claim_agent_failure_item(
        run_id,
        item_id,
        "delete",
        {"unresolved", "pending_verification"},
    )
    try:
        delete_result = delete_script_asset(
            claimed["module_name"],
            claimed["filename"],
            f"agent failure item delete: {claimed['module_name']}/{claimed['filename']}",
        )
        updated = agent_failure_item_with_evidence(
            claimed,
            "delete",
            "succeeded",
            {
                "archive": delete_result.get("archive"),
                "asset": serialize_asset(delete_result.get("asset")),
            },
        )
        updated.update(
            {
                "resolution": "archived",
                "included_in_suite": False,
                "current_script": None,
                "latest_action": {"type": "delete", "status": "succeeded"},
            }
        )
        return finish_agent_failure_item_action(run_id, updated, "deleting", "deleted")
    except Exception as exc:
        rollback_agent_failure_item_action(run_id, claimed, "deleting", previous_status, "delete", exc)
        raise


def ignore_agent_failure_item(run_id, item_id):
    item = get_agent_failure_item(run_id, item_id)
    if not item:
        raise FileNotFoundError("失败项不存在。")
    if item.get("status") in AGENT_FAILURE_BUSY_STATUSES:
        raise AgentFailureCheckpointConflict("该失败项正在处理中。")
    updated = agent_failure_item_with_evidence(
        item,
        "ignore",
        "succeeded",
        {"reason": "用户选择保留失败记录并从本次测试集排除。"},
    )
    updated.update(
        {
            "status": "ignored",
            "resolution": "ignored",
            "included_in_suite": False,
            "latest_action": {"type": "ignore", "status": "succeeded"},
        }
    )
    result, _ = mutate_agent_failure_checkpoint(
        run_id,
        lambda output: replace_agent_failure_item_when_idle(output, updated, {item.get("status")}),
    )
    return refresh_agent_failure_item_capabilities(result)


def collect_agent_checkpoint_final_scripts(run_id):
    execute_output = get_optional_agent_step_output(run_id, "execute_scripts") or {}
    repair_output = get_optional_agent_step_output(run_id, "repair_scripts") or {}
    checkpoint_output = get_agent_failure_checkpoint_output(run_id) or {}
    return dedupe_agent_scripts(
        [
            *(execute_output.get("scripts") or []),
            *(repair_output.get("scripts") or []),
            *(checkpoint_output.get("scripts") or []),
        ]
    )


def get_agent_failure_coverage_gap(run_id):
    output = get_agent_failure_checkpoint_output(run_id) or {}
    items = [
        item
        for item in output.get("failure_items") or []
        if item.get("status") == "kept_unresolved"
    ]
    return {
        "count": len(items),
        "items": [
            {
                "item_id": item.get("item_id"),
                "source_type": item.get("source_type"),
                "module_name": item.get("module_name"),
                "plan_filename": item.get("plan_filename"),
                "filename": item.get("filename"),
                "error": item.get("error"),
            }
            for item in items
        ],
    }


def continue_agent_failure_checkpoint(run_id):
    run = get_agent_run_row(run_id)
    if not run:
        raise FileNotFoundError("Agent 任务不存在。")
    if run.get("status") != "awaiting_failure_action":
        raise AgentFailureCheckpointConflict("该 Agent 任务当前不在失败处置阶段。")
    output = get_agent_failure_checkpoint_output(run_id) or {}
    if output.get("continuing"):
        raise AgentFailureCheckpointConflict("失败处置正在进入下一阶段，请勿重复操作。")
    if any(item.get("status") in AGENT_FAILURE_BUSY_STATUSES for item in output.get("failure_items") or []):
        raise AgentFailureCheckpointConflict("仍有失败项正在处理中，请等待完成后继续。")
    active_retry_flows = list_agent_item_retry_flows(run_id=run_id, active_only=True)
    if active_retry_flows:
        raise AgentFailureCheckpointConflict("仍有脚本正在重试并验证，请等待完成或先取消。")

    final_scripts = collect_agent_checkpoint_final_scripts(run_id)
    if not final_scripts:
        raise AgentFailureCheckpointConflict("没有可执行脚本，不能创建空测试集。请先重新生成、修复或编辑至少一个脚本并执行验证。")

    def mark_for_continue(checkpoint):
        if any(
            item.get("status") in AGENT_FAILURE_BUSY_STATUSES
            for item in checkpoint.get("failure_items") or []
        ):
            raise AgentFailureCheckpointConflict("仍有失败项正在处理中，请等待完成后继续。")
        checkpoint["continuing"] = True
        checkpoint["continue_claimed_at"] = current_time_ms()
        items = []
        for item in checkpoint.get("failure_items") or []:
            if item.get("status") in {"resolved", "deleted", "ignored"}:
                items.append(item)
            else:
                items.append(
                    {
                        **item,
                        "continue_previous_state": {
                            "status": item.get("status"),
                            "resolution": item.get("resolution"),
                            "included_in_suite": item.get("included_in_suite"),
                        },
                        "status": "kept_unresolved",
                        "resolution": "kept_unresolved",
                        "included_in_suite": False,
                        "updated_at": current_time_ms(),
                    }
                )
        checkpoint["failure_items"] = items
        checkpoint["continued_at"] = current_time_ms()
        return items

    _, output = mutate_agent_failure_checkpoint(run_id, mark_for_continue)
    unresolved_items = [
        item for item in output["failure_items"] if item.get("status") == "kept_unresolved"
    ]
    summary = load_json_column(run.get("summary_json"), {})
    summary = summary if isinstance(summary, dict) else {}
    summary.update(
        {
            "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
            "partial_success": bool(unresolved_items),
            "coverage_gap": {
                "count": len(unresolved_items),
                "items": [
                    {
                        "item_id": item.get("item_id"),
                        "source_type": item.get("source_type"),
                        "module_name": item.get("module_name"),
                        "plan_filename": item.get("plan_filename"),
                        "filename": item.get("filename"),
                        "error": item.get("error"),
                    }
                    for item in unresolved_items
                ],
            },
        }
    )
    try:
        update_agent_step(
            run_id,
            "review_failed_scripts",
            status="succeeded",
            output_data=output,
            counts={
                "failed": len(output["failure_items"]),
                "generation_failed": len(output["generation_failures"]),
                "repair_failed": len(output["repair_failures"]),
                "resolved": output["resolved_count"],
                "unresolved": len(unresolved_items),
                "handled": output["handled_count"],
            },
            error="",
            finished=True,
        )
        update_agent_run(
            run_id,
            status="running",
            current_step="create_suite",
            summary=summary,
            error="",
        )
    except Exception:
        def rollback_continue(checkpoint):
            restored = []
            for item in checkpoint.get("failure_items") or []:
                previous = item.get("continue_previous_state")
                if isinstance(previous, dict):
                    item = {**item, **previous}
                    item.pop("continue_previous_state", None)
                restored.append(item)
            checkpoint["failure_items"] = restored
            checkpoint.pop("continuing", None)
            checkpoint.pop("continue_claimed_at", None)
            checkpoint.pop("continued_at", None)
            return restored

        try:
            _, restored_output = mutate_agent_failure_checkpoint(
                run_id,
                rollback_continue,
                allow_continuing=True,
            )
            update_agent_step(
                run_id,
                "review_failed_scripts",
                status="awaiting_action",
                output_data=restored_output,
                error="",
            )
            update_agent_run(
                run_id,
                status="awaiting_failure_action",
                current_step="review_failed_scripts",
                summary=load_json_column(run.get("summary_json"), {}),
                error="",
            )
        except Exception:
            pass
        raise
    append_agent_event(
        run_id,
        "review_failed_scripts",
        "status",
        f"失败处置已确认，保留 {len(unresolved_items)} 个未解决项并继续创建测试集。",
        {"unresolved_count": len(unresolved_items), "script_count": len(final_scripts)},
    )
    return {
        "final_scripts": final_scripts,
        "unresolved_items": unresolved_items,
        "partial_success": bool(unresolved_items),
        "coverage_gap": summary["coverage_gap"],
    }


def cancel_agent_failure_checkpoint(run_id):
    run = get_agent_run_row(run_id)
    if not run:
        raise FileNotFoundError("Agent 任务不存在。")
    if run.get("status") != "awaiting_failure_action":
        raise AgentFailureCheckpointConflict("该 Agent 任务当前不在失败处置阶段。")

    def claim_cancel(checkpoint):
        if any(
            item.get("status") in AGENT_FAILURE_BUSY_STATUSES
            for item in checkpoint.get("failure_items") or []
        ):
            raise AgentFailureCheckpointConflict("失败项正在处理中，请等待完成后再取消任务。")
        checkpoint["cancelling"] = True
        checkpoint["cancel_claimed_at"] = current_time_ms()
        return checkpoint

    mutate_agent_failure_checkpoint(run_id, claim_cancel)
    try:
        update_agent_step(
            run_id,
            "review_failed_scripts",
            status="cancelled",
            error="用户在失败处置阶段取消任务。",
            finished=True,
        )
        cancelled_run = update_agent_run(
            run_id,
            status="cancelled",
            error="用户在失败处置阶段取消任务。",
            finished=True,
        )
    except Exception:
        def rollback_cancel(checkpoint):
            checkpoint.pop("cancelling", None)
            checkpoint.pop("cancel_claimed_at", None)
            return checkpoint

        try:
            mutate_agent_failure_checkpoint(run_id, rollback_cancel, allow_continuing=True)
        except Exception:
            pass
        raise
    append_agent_event(
        run_id,
        "review_failed_scripts",
        "status",
        "用户在失败处置阶段取消 Agent 任务。",
        {"status": "cancelled"},
    )
    return cancelled_run


def resume_agent_failure_checkpoint(run_id):
    run = get_agent_run_row(run_id)
    if not run:
        raise FileNotFoundError("Agent 任务不存在。")
    if (
        run.get("status") != "cancelled"
        or run.get("current_step") != "review_failed_scripts"
    ):
        raise AgentFailureCheckpointConflict("该任务不是已取消的失败处置任务。")

    def reopen(checkpoint):
        if checkpoint.get("continuing"):
            raise AgentFailureCheckpointConflict("失败处置已进入下一阶段，不能恢复到处置页面。")
        checkpoint.pop("cancelling", None)
        checkpoint.pop("cancel_claimed_at", None)
        checkpoint["resumed_at"] = current_time_ms()
        return checkpoint

    _, output = mutate_agent_failure_checkpoint(
        run_id,
        reopen,
        allow_continuing=True,
    )
    update_agent_step(
        run_id,
        "review_failed_scripts",
        status="awaiting_action",
        output_data=output,
        error="",
        reopened=True,
    )
    resumed_run = update_agent_run(
        run_id,
        status="awaiting_failure_action",
        current_step="review_failed_scripts",
        error="",
        reopened=True,
    )
    append_agent_event(
        run_id,
        "review_failed_scripts",
        "status",
        "已恢复到失败分析与处置阶段，原处置记录保持不变。",
        {"status": "awaiting_failure_action"},
    )
    return resumed_run


def run_agent_failure_continue_workflow(run_id, project, author):
    agent_register_task(run_id)
    with use_project_context(project), use_author_context(f"agent:{author or 'platform'}"):
        try:
            run = get_agent_run_row(run_id)
            if not run:
                return
            requirement = get_requirement_by_uid(run.get("requirement_uid"))
            if not requirement:
                raise RuntimeError("需求不存在。")
            final_scripts = collect_agent_checkpoint_final_scripts(run_id)
            if not final_scripts:
                raise RuntimeError("没有可加入测试集的有效脚本。")
            checkpoint_output = get_agent_failure_checkpoint_output(run_id) or {}
            unresolved_items = [
                item
                for item in checkpoint_output.get("failure_items") or []
                if item.get("status") == "kept_unresolved"
            ]
            suite = agent_create_suite(run_id, requirement, final_scripts)
            execution = agent_run_suite(run_id, suite)
            previous_summary = load_json_column(run.get("summary_json"), {})
            previous_summary = previous_summary if isinstance(previous_summary, dict) else {}
            final_summary = {
                **previous_summary,
                "requirement": serialize_requirement(requirement, include_content=False),
                "script_count": len(final_scripts),
                "suite": suite,
                "execution": execution.get("summary") or {},
                "pipeline_version": CURRENT_AGENT_PIPELINE_VERSION,
                "partial_success": bool(unresolved_items),
                "coverage_gap": {
                    "count": len(unresolved_items),
                    "items": [
                        {
                            "item_id": item.get("item_id"),
                            "source_type": item.get("source_type"),
                            "module_name": item.get("module_name"),
                            "plan_filename": item.get("plan_filename"),
                            "filename": item.get("filename"),
                            "error": item.get("error"),
                        }
                        for item in unresolved_items
                    ],
                },
            }
            final_status = "succeeded_with_unresolved" if unresolved_items else "succeeded"
            update_agent_run(
                run_id,
                status=final_status,
                current_step="run_suite",
                summary=final_summary,
                error="",
                finished=True,
            )
            append_agent_event(
                run_id,
                "run_suite",
                "status",
                "Agent 流程执行完成，存在未解决覆盖缺口。"
                if unresolved_items
                else "Agent 流程执行完成。",
                final_summary,
            )
        except OpencodeTaskCancelled as exc:
            update_agent_run(run_id, status="cancelled", error=str(exc), finished=True)
            append_agent_event(run_id, "", "status", "Agent 任务已取消。", {"error": str(exc)})
        except Exception as exc:
            current_run = get_agent_run_row(run_id) or {}
            current_step = current_run.get("current_step") or "create_suite"
            if current_step:
                agent_fail_step(run_id, current_step, exc)
            update_agent_run(run_id, status="failed", error=str(exc), finished=True)
            append_agent_event(run_id, current_step, "error", f"Agent 继续任务失败：{exc}", {"error": str(exc)})
        finally:
            agent_set_current_job(run_id, "")
            agent_cleanup_task(run_id)
