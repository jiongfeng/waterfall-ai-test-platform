"""Context-neutral generation, execution, repair and analysis atoms."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
import threading
import time
import uuid

from test_plan_viewer.agent import localization as agent_localization
from test_plan_viewer.agent import failure_handling as agent_failure_handling
from test_plan_viewer.agent.script_preparation import ScriptPreparationConflict
from test_plan_viewer.script_preparation.manager import (
    ModuleScriptPreparationCancelled,
)


class ModulePreparationOperationFailed(RuntimeError):
    def __init__(self, message, *, rollback_script=None):
        super().__init__(message)
        self.rollback_script = rollback_script


class ModulePreparationTaskRegistry:
    """Persist the current external job so cancellation is module-owned."""

    def __init__(
        self, repository, cancel_external_job, target_lease=None,
        heartbeat_interval=5.0, heartbeat_context_factory=nullcontext,
    ):
        self.repository = repository
        self.cancel_external_job = cancel_external_job
        self.target_lease_factory = target_lease
        self._heartbeat_lock = threading.Lock()
        self._heartbeat_at = {}
        self._heartbeat_stops = {}
        self._heartbeat_interval = float(heartbeat_interval)
        self._heartbeat_context_factory = heartbeat_context_factory
        self._registered = threading.local()

    def register(self, run_id):
        self.repository.set_current_job(run_id, "")
        token = self.repository.current_worker_token()
        key = (run_id, token)
        tokens = getattr(self._registered, "tokens", None)
        if tokens is None:
            tokens = {}
            self._registered.tokens = tokens
        tokens[run_id] = token
        with self._heartbeat_lock:
            self._heartbeat_at[key] = 0.0
        if not isinstance(token, str) or not token:
            return
        stop = threading.Event()
        context = self._heartbeat_context_factory()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(run_id, token, stop, context),
            daemon=True,
            name=f"script-preparation-heartbeat-{run_id}",
        )
        with self._heartbeat_lock:
            self._heartbeat_stops[key] = stop
        thread.start()

    def _heartbeat_loop(self, run_id, token, stop, context):
        with context:
            while not stop.wait(self._heartbeat_interval):
                try:
                    self.repository.heartbeat_worker(run_id, worker_token=token)
                except Exception:
                    return

    def cleanup(self, run_id):
        current_token = self.repository.current_worker_token()
        tokens = getattr(self._registered, "tokens", {})
        registered_token = tokens.pop(run_id, "")
        token = current_token or registered_token
        key = (run_id, token)
        with self._heartbeat_lock:
            stop = self._heartbeat_stops.pop(key, None)
            self._heartbeat_at.pop(key, None)
        if stop:
            stop.set()
        if current_token:
            self.repository.set_current_job(run_id, "")

    def set_current_job(self, run_id, job_id):
        self.repository.set_current_job(run_id, job_id)
        self.heartbeat(run_id, force=True)

    def heartbeat(self, run_id, *, force=False):
        now = time.monotonic()
        key = (run_id, self.repository.current_worker_token())
        with self._heartbeat_lock:
            previous = float(self._heartbeat_at.get(key) or 0.0)
            if not force and now - previous < 5.0:
                return False
            self._heartbeat_at[key] = now
        return self.repository.heartbeat_worker(run_id)

    def target_lease(self, module_name, filename):
        if self.target_lease_factory is None:
            return nullcontext()
        return self.target_lease_factory(module_name, filename)

    def request_cancel(self, run_id):
        run = self.repository.get(run_id) or {}
        job_id = str(run.get("current_job_id") or "")
        if job_id:
            try:
                self.cancel_external_job(job_id)
            except Exception:
                pass
        return {"cancel_requested": True, "current_job_id": job_id}

    def raise_if_cancelled(self, run_id):
        if self.repository.is_cancel_requested(run_id):
            raise ModuleScriptPreparationCancelled("脚本准备任务已取消。")


def consume_sse(runtime, registry, run_id, generator):
    """Consume an internal SSE generator without writing Agent events."""

    result = {"status": "running", "logs": ""}
    terminal_seen = False
    try:
        for block in generator:
            registry.heartbeat(run_id)
            registry.raise_if_cancelled(run_id)
            for event_name, payload in runtime.parse_sse_text_blocks(block):
                payload = payload if isinstance(payload, dict) else {}
                if event_name in {"status", "done"}:
                    result.update(
                        {
                            key: value
                            for key, value in payload.items()
                            if value is not None
                        }
                    )
                    terminal_seen = terminal_seen or event_name == "done" or str(
                        payload.get("status") or ""
                    ) in {
                        "succeeded",
                        "completed",
                        "failed",
                        "cancelled",
                        "blocked",
                    }
                elif event_name == "error":
                    result.update(payload)
                    result["status"] = "failed"
                    result["ok"] = False
                    terminal_seen = True
                elif event_name == "delta":
                    result["logs"] = (
                        f"{result.get('logs', '')}{payload.get('text') or ''}"
                    )[-runtime.JOB_LOG_TAIL_LIMIT :]
                elif event_name == "log":
                    message = str(payload.get("message") or "")
                    result["logs"] = (
                        f"{result.get('logs', '')}{message}\n"
                    )[-runtime.JOB_LOG_TAIL_LIMIT :]
    finally:
        close = getattr(generator, "close", None)
        if callable(close):
            close()
    if not terminal_seen:
        result.update(
            {
                "status": "failed",
                "ok": False,
                "error": "后台事件流提前结束，未收到终态结果。",
            }
        )
    return result


def _terminal_succeeded(result):
    return (
        isinstance(result, dict)
        and result.get("ok") is not False
        and result.get("status") in {"succeeded", "completed"}
    )


def _assert_current_revision(runtime, module_name, filename, script):
    asset = runtime.get_test_asset_by_path(
        "script", runtime.get_script_file(module_name, filename)
    )
    actual = asset.get("current_revision_id") if isinstance(asset, dict) else None
    script_asset = script.get("asset") if isinstance(script.get("asset"), dict) else {}
    expected = (
        script.get("current_revision_id")
        or script.get("revision_id")
        or script_asset.get("current_revision_id")
    )
    if str(expected) != str(actual):
        raise ScriptPreparationConflict("脚本版本已变化，请刷新后重试。")


def generate_script(
    runtime,
    registry,
    run_id,
    step_key,
    plan,
    **kwargs,
):
    module_name = runtime.validate_module_name(plan["module_name"])
    plan_filename = runtime.validate_plan_filename(plan["plan_filename"])
    filename = runtime.get_generated_script_filename_from_plan_filename(
        plan_filename, language=runtime.agent_project_language()
    )
    with registry.target_lease(module_name, filename):
        return _generate_script_locked(
            runtime, registry, run_id, step_key, plan, **kwargs
        )


def _generate_script_locked(
    runtime,
    registry,
    run_id,
    _step_key,
    plan,
    *,
    original_prompt=None,
    supplemental_prompt="",
):
    module_name = runtime.validate_module_name(plan["module_name"])
    plan_filename = runtime.validate_plan_filename(plan["plan_filename"])
    language = runtime.agent_project_language()
    plan_file = runtime.get_plan_target_path(module_name, plan_filename)
    if not plan_file.exists():
        raise FileNotFoundError(f"测试计划不存在：{plan_file}")
    script_dir = runtime.get_script_module_dir(module_name)
    existing_names = (
        {item.name for item in script_dir.glob("*.spec.ts") if item.is_file()}
        if script_dir.exists()
        else set()
    )
    filename = runtime.get_generated_script_filename_from_plan_filename(
        plan_filename, language=language
    )
    target_file = runtime.get_script_file(module_name, filename)
    current_asset = runtime.get_test_asset_by_path("script", target_file)
    current_revision = (
        current_asset.get("current_revision_id")
        if isinstance(current_asset, dict)
        else None
    )
    if "_expected_script_revision_id" in plan and str(
        plan.get("_expected_script_revision_id")
    ) != str(current_revision):
        raise ScriptPreparationConflict("脚本版本已变化，请刷新后重试。")
    plan_asset = runtime.sync_plan_asset(
        module_name,
        plan_file,
        change_source="manual",
        message=f"script preparation sync plan: {module_name}/{plan_filename}",
    )
    job_id = f"generator-{uuid.uuid4().hex}"
    prompt = str(
        original_prompt or runtime.build_agent_script_generation_prompt(plan)
    ).strip()
    prompt = agent_localization.append_supplemental_prompt(
        language, prompt, supplemental_prompt, "generation"
    )
    runtime.create_test_job(
        "generator",
        job_id=job_id,
        status="queued",
        source_asset_id=plan_asset.get("asset_id") if plan_asset else None,
        prompt=prompt,
    )
    candidate_file = runtime.get_script_generation_candidate_file(
        module_name, plan_filename, job_id, language=language
    )
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot = runtime.managed_file_snapshot(
        runtime.collect_generation_managed_files(
            module_name, plan_file, target_file
        )
    )
    target_snapshot = snapshot.get(str(target_file.resolve(strict=False)), {})
    original_target_hash = target_snapshot.get("hash", "")

    def has_output():
        return (
            candidate_file.is_file() and candidate_file.stat().st_size > 0
        ) or (
            target_file.exists()
            and runtime.file_hash(target_file) != original_target_hash
        ) or bool(runtime.get_new_generated_script_files(script_dir, existing_names))

    def finalize():
        payload = runtime.finalize_script_generation(
            module_name,
            plan_filename,
            plan_file,
            target_file,
            candidate_file,
            snapshot,
            existing_names,
            language=language,
        )
        script_asset = runtime.sync_script_asset(
            module_name,
            target_file,
            change_source="generator",
            source_job_id=job_id,
            from_plan_asset_id=plan_asset.get("asset_id") if plan_asset else None,
            message=f"script preparation generator: {module_name}/{target_file.name}",
        )
        payload["asset"] = runtime.serialize_asset(script_asset)
        payload["source_plan_asset"] = runtime.serialize_asset(plan_asset)
        return payload

    def rollback_generation():
        runtime.restore_snapshot_files(snapshot)
        runtime.cleanup_new_generated_script_files(script_dir, existing_names)
        runtime.cleanup_new_managed_files(snapshot)
        candidate_file.unlink(missing_ok=True)
        if target_file.is_file():
            asset = runtime.sync_script_asset(
                module_name,
                target_file,
                change_source="rollback",
                message=f"script preparation rollback: {module_name}/{filename}",
            )
            return {
                "module_name": module_name,
                "plan_filename": plan_filename,
                "filename": filename,
                "path": str(target_file),
                "asset": runtime.serialize_asset(asset),
            }
        asset = runtime.get_test_asset_by_path("script", target_file)
        if asset:
            runtime.mark_test_asset_deleted(asset)
        return None

    full_prompt = runtime.build_script_generation_prompt(
        prompt, module_name, plan_file, script_dir, target_file, candidate_file
    )
    registry.set_current_job(run_id, job_id)
    try:
        result = consume_sse(
            runtime,
            registry,
            run_id,
            runtime.stream_plan_generation(
                module_name,
                full_prompt,
                target_file,
                completion_check=has_output,
                target_label=str(target_file),
                session_title=f"脚本生成：{module_name}/{Path(plan_filename).stem}",
                success_message=f"脚本生成完成：{target_file}",
                default_agent="playwright-test-generator",
                setup_targets=runtime.build_setup_targets(
                    module_name=module_name, filename=filename
                ),
                setup_parent_run_id=run_id,
                success_payload_factory=finalize,
                cancel_job_id=job_id,
                job_id=job_id,
                agent_stream=False,
                agent_cancel_check=lambda: registry.raise_if_cancelled(run_id),
                cancel_cleanup=rollback_generation,
            ),
        )
    finally:
        registry.set_current_job(run_id, "")
    if not _terminal_succeeded(result):
        rollback_script = rollback_generation()
        raise ModulePreparationOperationFailed(
            result.get("error") or f"生成脚本失败：{module_name}/{plan_filename}",
            rollback_script=rollback_script,
        )
    return {
        "module_name": module_name,
        "plan_filename": plan_filename,
        "filename": result.get("script_filename") or target_file.name,
        "path": result.get("target_path") or str(target_file),
        "asset": result.get("asset"),
        "job_id": job_id,
    }


def execute_script(runtime, registry, run_id, step_key, script):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    with registry.target_lease(module_name, filename):
        return _execute_script_locked(
            runtime, registry, run_id, step_key, script
        )


def _execute_script_locked(runtime, registry, run_id, _step_key, script):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    _assert_current_revision(runtime, module_name, filename, script)
    setup_targets = runtime.build_setup_targets(
        module_name=module_name, filename=filename
    )
    setup_resolution = runtime.resolve_setup_profile(setup_targets)
    context = runtime.build_script_execution_context(
        module_name, filename, include_database_global_setup=False
    )
    context["setup_targets"] = setup_targets
    context["setup_resolution"] = setup_resolution
    job_id = f"execution-{context['run_id']}"
    registry.set_current_job(run_id, job_id)
    try:
        result = consume_sse(
            runtime,
            registry,
            run_id,
            runtime.stream_script_execution(
                module_name, filename, context, agent_stream=True
            ),
        )
        registry.raise_if_cancelled(run_id)
    finally:
        registry.set_current_job(run_id, "")
    execution = runtime.summarize_agent_execution_result(result)
    item = {
        **script,
        "execution": execution,
        "execution_run_id": result.get("run_id"),
        "execution_job_id": result.get("job_id"),
    }
    if not _terminal_succeeded(result):
        item["error"] = result.get("error") or "脚本执行失败。"
    return item


def repair_script(
    runtime,
    registry,
    run_id,
    step_key,
    script,
    **kwargs,
):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    with registry.target_lease(module_name, filename):
        return _repair_script_locked(
            runtime, registry, run_id, step_key, script, **kwargs
        )


def _repair_script_locked(
    runtime,
    registry,
    run_id,
    _step_key,
    script,
    *,
    failure=None,
    original_prompt=None,
    supplemental_prompt="",
):
    module_name = runtime.validate_module_name(script["module_name"])
    filename = runtime.validate_script_filename(script["filename"])
    script_file = runtime.get_script_file(module_name, filename)
    if not script_file.exists():
        raise FileNotFoundError(f"测试脚本不存在：{script_file}")
    _assert_current_revision(runtime, module_name, filename, script)
    prompt = str(
        original_prompt
        or runtime.build_agent_script_repair_prompt(script, failure)
    ).strip()
    prompt = agent_localization.append_supplemental_prompt(
        runtime.agent_project_language(), prompt, supplemental_prompt, "repair"
    )
    script_asset = runtime.sync_script_asset(
        module_name,
        script_file,
        change_source="manual",
        message=f"script preparation sync: {module_name}/{filename}",
    )
    job_id = f"healer-{uuid.uuid4().hex}"
    runtime.create_test_job(
        "healer",
        job_id=job_id,
        status="queued",
        target_asset_id=script_asset.get("asset_id") if script_asset else None,
        prompt=prompt,
    )
    started_at = time.time()
    repair_snapshot = runtime.managed_file_snapshot([script_file])

    def rollback_repair():
        runtime.restore_snapshot_files(repair_snapshot)
        asset = runtime.sync_script_asset(
            module_name,
            script_file,
            change_source="rollback",
            message=f"script preparation rollback: {module_name}/{filename}",
        )
        return {
            **script,
            "asset": runtime.serialize_asset(asset),
        }

    def finalize():
        result = runtime.build_run_video_result(started_at)
        updated_asset = runtime.sync_script_asset(
            module_name,
            script_file,
            change_source="healer",
            source_job_id=job_id,
            message=f"script preparation healer: {module_name}/{filename}",
        )
        result["asset"] = runtime.serialize_asset(updated_asset)
        return result

    registry.set_current_job(run_id, job_id)
    try:
        result = consume_sse(
            runtime,
            registry,
            run_id,
            runtime.stream_plan_generation(
                module_name,
                runtime.build_script_run_prompt(
                    prompt, module_name, filename, script_file
                ),
                script_file,
                completion_check=lambda: False,
                completion_required=False,
                target_label=str(script_file),
                session_title=f"脚本修复：{filename}",
                success_message=f"脚本修复完成：{script_file}",
                success_payload_factory=finalize,
                default_agent="playwright-test-healer",
                setup_targets=runtime.build_setup_targets(
                    module_name=module_name, filename=filename
                ),
                setup_parent_run_id=run_id,
                cancel_job_id=job_id,
                job_id=job_id,
                agent_stream=False,
                agent_cancel_check=lambda: registry.raise_if_cancelled(run_id),
                cancel_cleanup=rollback_repair,
            ),
        )
    finally:
        registry.set_current_job(run_id, "")
    if not _terminal_succeeded(result):
        rollback_script = rollback_repair()
        raise ModulePreparationOperationFailed(
            result.get("error") or f"修复脚本失败：{module_name}/{filename}",
            rollback_script=rollback_script,
        )
    return {
        **script,
        "asset": result.get("asset") or script.get("asset"),
        "repair_job_id": job_id,
        "repair_test_run_id": result.get("run_id") or "",
        "repair_result_id": result.get("result_id"),
    }


def analyze_failure(runtime, registry, run_id, _step_key, payload):
    runtime.ensure_test_platform_failure_analyst_agent()
    job_id = f"failure-analysis-{uuid.uuid4().hex}"
    title = runtime.agent_message("failure_analysis_instruction")
    safe_payload = agent_failure_handling.redact_agent_failure_value(payload)
    prompt = (
        "@test-platform-failure-analyst\n"
        f"{title}\n\n请只输出 JSON。输入如下：\n"
        f"{json.dumps(safe_payload, ensure_ascii=False, indent=2)}\n"
    )
    runtime.create_test_job(
        "agent_review", job_id=job_id, status="running", prompt=prompt
    )
    registry.set_current_job(run_id, job_id)
    try:
        response = runtime.send_opencode_prompt_cancellable(
            prompt,
            job_id,
            default_agent="test-platform-failure-analyst",
            session_title="脚本失败分析",
        )
        registry.raise_if_cancelled(run_id)
        text = runtime.collect_opencode_response_text(response)
        safe_text = agent_failure_handling.redact_agent_failure_value(text)
        runtime.append_test_job_log(
            job_id, str(safe_text)[-runtime.JOB_LOG_TAIL_LIMIT :]
        )
        parsed = runtime.extract_json_object_from_text(text)
        parsed = agent_failure_handling.redact_agent_failure_value(parsed)
        runtime.finish_test_job(job_id, "succeeded")
        return parsed
    except Exception as exc:
        safe_error = agent_failure_handling.redact_agent_failure_value(str(exc))
        runtime.append_test_job_log(job_id, f"{safe_error}\n")
        runtime.finish_test_job(job_id, "failed", error=str(safe_error))
        raise
    finally:
        registry.set_current_job(run_id, "")


__all__ = [
    "ModulePreparationTaskRegistry",
    "ModulePreparationOperationFailed",
    "analyze_failure",
    "consume_sse",
    "execute_script",
    "generate_script",
    "repair_script",
]
