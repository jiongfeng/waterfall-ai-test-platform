"""Execution SSE generators, bound to application dependencies at call time."""

# ruff: noqa: F821 -- implementation globals are supplied by _bind_dependencies.

from __future__ import annotations

from functools import partial
from pathlib import Path
import re
from types import FunctionType

from test_plan_viewer.agent.output_buffer import AgentOutputBatcher
from test_plan_viewer.generation.event_stream import BoundedSseReader
from test_plan_viewer.infrastructure.job_logs import BufferedJobLogWriter


_AGENT_EXECUTION_LOG_PATTERNS = (
    (
        re.compile(r"^执行模式：(.+)。$"),
        lambda match: "Execution mode: {}.".format(
            {"按文件串行执行": "serial per file", "当前批量执行": "batch"}.get(
                match.group(1), match.group(1)
            )
        ),
    ),
    (
        re.compile(r"^准备执行第 (\d+)/(\d+) 个测试集脚本：(.+)$"),
        lambda match: f"Preparing test-suite script {match.group(1)}/{match.group(2)}: {match.group(3)}",
    ),
    (re.compile(r"^执行命令：(.+)$"), lambda match: f"Command: {match.group(1)}"),
    (
        re.compile(r"^开始执行准备脚本：(.+)。$"),
        lambda match: f"Running setup script: {match.group(1)}.",
    ),
    (
        re.compile(r"^准备脚本完成：(.+)。$"),
        lambda match: f"Setup script completed: {match.group(1)}.",
    ),
)


def localize_agent_execution_log(message, project_copy):
    """Localize only recognized platform log wrappers, never process output."""

    text = str(message or "")
    if text == "合并 Playwright 测试集执行报告。":
        return project_copy("Merging the Playwright test-suite report.", text)
    for pattern, formatter in _AGENT_EXECUTION_LOG_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return project_copy(formatter(match), text)
    return text


def _bind_dependencies(function, dependencies):
    """Clone a generator function with one immutable dependency namespace."""

    namespace = dict(function.__globals__)
    namespace.update(
        (name, value) for name, value in vars(dependencies).items() if not name.startswith("__")
    )
    namespace["_BufferedExecutionOutput"] = partial(
        BufferedExecutionOutput,
        dependencies,
    )
    namespace["iter_bounded_process_output"] = iter_bounded_process_output
    bound = FunctionType(
        function.__code__,
        namespace,
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def _iter_process_output_events(stream):
    for raw_line in stream:
        yield None, raw_line


def iter_bounded_process_output(process, *, poll_interval=0.1):
    """Yield process output and idle ticks without blocking stream flushes."""

    if not process.stdout:
        return
    reader = BoundedSseReader(
        process.stdout,
        max_queue_size=256,
        join_timeout=1.0,
        event_iterator=_iter_process_output_events,
        thread_name="process-output-reader",
    ).start()
    try:
        while True:
            item = reader.poll(timeout=poll_interval)
            if item is None:
                yield None
                continue
            if item.kind == "error":
                raise item.error or RuntimeError("读取进程输出失败。")
            if item.kind == "eof":
                return
            yield item.data
    finally:
        reader.close()


def terminate_process(process, *, timeout=1.0):
    """Best-effort bounded shutdown for a child process owned by a stream."""

    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except Exception:
        pass
    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=timeout)
        return
    except Exception:
        pass
    try:
        process.kill()
    except Exception:
        return
    try:
        process.wait(timeout=timeout)
    except Exception:
        pass


def best_effort(callback, *args, **kwargs):
    """Run cancellation cleanup without masking GeneratorExit."""

    try:
        return callback(*args, **kwargs)
    except Exception:
        return None


class BufferedExecutionOutput:
    """Batch process output while preserving Agent checkpoint acknowledgements."""

    def __init__(self, dependencies, job_id, *, agent_stream=False, project_root=None):
        self.dependencies = dependencies
        self.job_id = job_id
        self.agent_stream = bool(agent_stream)
        self.batcher = AgentOutputBatcher()
        log_path = (
            Path(project_root)
            / dependencies.DATABASE_BASELINE_HELPER_DIR_NAME
            / dependencies.JOB_LOG_STORAGE_DIR_NAME
            / f"{dependencies.sanitize_job_id(job_id)}.log"
            if project_root is not None
            else dependencies.get_job_log_path(job_id)
        )
        self.writer = BufferedJobLogWriter(
            log_path,
            tail_bytes=dependencies.JOB_LOG_TAIL_LIMIT,
        )
        self._output_tail = ""
        self._pending_checkpoints = []

    def _acknowledge_previous_yield(self):
        if not self._pending_checkpoints:
            return
        checkpoints = self._pending_checkpoints
        self._pending_checkpoints = []
        for checkpoint in checkpoints:
            self.writer.mark_snapshot_persisted(checkpoint)

    def _append_file(self, text):
        if not text:
            return None
        # Keep this indirection for existing callers/tests that patch the
        # compatibility helper while using one writer in production.
        self.dependencies.append_test_job_log(
            self.job_id,
            text,
            writer=self.writer,
            persist_snapshot=False,
        )
        if not self.writer.opened:
            return None
        return self.writer.snapshot()

    def _persist_direct_checkpoint_if_due(self, snapshot):
        if snapshot is None or self.agent_stream or not self.writer.snapshot_due():
            return
        self.dependencies.persist_test_job_log_snapshot(self.job_id, snapshot)
        self.writer.mark_snapshot_persisted(snapshot)

    def _batch_event(self, batch):
        snapshot = self._append_file(batch.text)
        payload = {
            "text": batch.text,
            "job_id": self.job_id,
            "stream_kind": "process-output",
            **batch.metadata(),
        }
        if snapshot is not None and self.writer.snapshot_due():
            if self.agent_stream:
                payload["_job_log_snapshot"] = snapshot.as_updates()
                self._pending_checkpoints.append(snapshot)
            else:
                self.dependencies.persist_test_job_log_snapshot(self.job_id, snapshot)
                self.writer.mark_snapshot_persisted(snapshot)
        return self.dependencies.sse_payload("delta", payload)

    def _batch_events(self, batches):
        return "".join(self._batch_event(batch) for batch in batches if batch is not None)

    def emit_delta(self, text):
        self._acknowledge_previous_yield()
        if not text:
            return ""
        self._output_tail = f"{self._output_tail}{text}"[-self.dependencies.JOB_LOG_TAIL_LIMIT :]
        return self._batch_events(self.batcher.add(text))

    def flush_due(self):
        self._acknowledge_previous_yield()
        return self._batch_events([self.batcher.flush_due()])

    def flush(self, reason="structured"):
        self._acknowledge_previous_yield()
        return self._batch_events([self.batcher.flush(reason=reason)])

    def emit_log(self, message):
        self._acknowledge_previous_yield()
        project_copy = getattr(self.dependencies, "project_copy", None)
        if self.agent_stream and callable(project_copy):
            message = localize_agent_execution_log(message, project_copy)
        prefix = self._batch_events([self.batcher.flush(reason="structured")])
        snapshot = self._append_file(f"{message}\n") if message else None
        self._persist_direct_checkpoint_if_due(snapshot)
        payload = {"message": message, "job_id": self.job_id}
        return f"{prefix}{self.dependencies.sse_payload('log', payload)}"

    def emit_event(self, event_name, payload):
        self._acknowledge_previous_yield()
        prefix = self._batch_events([self.batcher.flush(reason="structured")])
        return f"{prefix}{self.dependencies.sse_payload(event_name, payload)}"

    def finish_job(self, status, *, error=None, target_asset_id=None):
        self._acknowledge_previous_yield()
        return self.dependencies.finish_test_job(
            self.job_id,
            status,
            error=error,
            target_asset_id=target_asset_id,
            log_writer=self.writer,
        )

    def output_tail(self, limit=4000):
        return self._output_tail[-limit:]

    def abort(self):
        batch = self.batcher.finish(reason="generator-exit")
        if batch is not None:
            snapshot = self._append_file(batch.text)
            self._persist_direct_checkpoint_if_due(snapshot)

    def cancel_job(self, error, *, target_asset_id=None):
        # Do not acknowledge a suspended Agent yield here. The terminal job
        # update persists the authoritative file snapshot independently.
        self.abort()
        return self.dependencies.finish_test_job(
            self.job_id,
            "cancelled",
            error=str(error),
            target_asset_id=target_asset_id,
            log_writer=self.writer,
        )

    def close(self):
        self.writer.close()


def _stream_script_execution_impl(module_name, filename, context, *, agent_stream=False):
    started_at = time.time()
    setup_summary = None
    setup_logs = []
    current_process = None
    terminalized = False
    script_asset = sync_script_asset(
        module_name,
        context["script_file"],
        change_source="manual",
        message=f"sync script: {module_name}/{filename}",
    )
    run_id = context["run_id"]
    job_id = f"execution-{run_id}"
    create_test_run(
        run_id,
        "single_script",
        EXECUTION_MODE_BATCH,
        module_name=module_name,
        target_asset_id=script_asset.get("asset_id") if script_asset else None,
        command=context["command_text"],
        env=build_execution_env_metadata(
            {"script": get_script_test_relative_path(module_name, filename)}
        ),
        total_files=1,
    )
    create_test_job(
        "execution",
        job_id=job_id,
        status="running",
        target_asset_id=script_asset.get("asset_id") if script_asset else None,
    )
    result_row = create_run_result_for_script(
        run_id, 1, module_name, filename, command=context["command_text"], status="unknown"
    )
    result_id = result_row.get("result_id") if result_row else None
    output_stream = _BufferedExecutionOutput(
        job_id,
        agent_stream=agent_stream,
        project_root=context.get("project_root"),
    )

    def emit_log(message):
        return output_stream.emit_log(message)

    def emit_delta(text):
        return output_stream.emit_delta(text)

    def emit_status(status, error=None, extra=None):
        payload = {
            "status": status,
            "module_name": module_name,
            "filename": filename,
            "target_path": str(context["script_file"]),
            "command": context["command_text"],
            "run_id": run_id,
            "job_id": job_id,
            "result_id": result_id,
        }
        if error:
            payload["error"] = error
        if extra:
            payload.update(extra)
        return output_stream.emit_event("status", payload)

    def finish_execution_job(status, error=None):
        nonlocal terminalized
        pending = output_stream.flush("terminal")
        if pending:
            yield pending
        output_stream.finish_job(
            status,
            error=error,
            target_asset_id=script_asset.get("asset_id") if script_asset else None,
        )
        terminalized = True

    try:
        yield emit_status("running")
        if context.get("setup_resolution"):
            setup_summary = execute_setup_profile(
                context["setup_resolution"], parent_run_id=run_id, emit_log=setup_logs.append
            )
            for message in setup_logs:
                yield emit_log(message)
        process = subprocess.Popen(
            context["command"],
            cwd=context["project_root"],
            env=get_playwright_execution_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        current_process = process

        for raw_line in iter_bounded_process_output(process):
            if raw_line is None:
                pending = output_stream.flush_due()
                if pending:
                    yield pending
                elif agent_stream:
                    yield ": agent-cancel-check\n\n"
            else:
                line = normalize_process_output(raw_line)
                pending = emit_delta(strip_ansi(line))
                if pending:
                    yield pending

        returncode = process.wait()
        current_process = None
        output = output_stream.output_tail()
        run_result = {
            **build_run_video_result(started_at, context["results_dir"]),
            **build_playwright_report_result(started_at, context["report_dir"]),
        }
        status = "succeeded" if returncode == 0 else "failed"
        error = None if returncode == 0 else f"脚本执行失败，退出码：{returncode}"
        extra = {
            "returncode": returncode,
            "output": output,
            "run_id": run_id,
            "job_id": job_id,
            "result_id": result_id,
            "setup": setup_summary,
            **run_result,
        }
        update_run_result(
            result_id,
            status=status,
            stdout_tail=output,
            error_message=error,
            command=context["command_text"],
            database_reset_status="succeeded" if context.get("setup_resolution") else None,
        )
        summary = build_execution_summary({filename: status}, returncode)
        update_test_run(
            run_id, status=status, summary=summary, completed_files=1, error=error, finished=True
        )
        register_execution_artifacts(
            run_id, context, run_result, {filename: result_id} if result_id else {}
        )
        yield from finish_execution_job("succeeded" if returncode == 0 else "failed", error=error)
        yield emit_status(status, error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": returncode == 0,
                "status": status,
                "returncode": returncode,
                "output": output,
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                "result_id": result_id,
                "setup": setup_summary,
                **run_result,
            },
        )
    except SetupPreparationError as exc:
        error = str(exc)
        setup_summary = exc.summary
        for message in setup_logs:
            yield emit_log(message)
        run_result = {
            **build_run_video_result(started_at, context["results_dir"]),
            **build_playwright_report_result(started_at, context["report_dir"]),
        }
        update_run_result(
            result_id, status="failed", error_message=error, database_reset_status="failed"
        )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary({filename: "failed"}),
            completed_files=0,
            error=error,
            finished=True,
        )
        register_execution_artifacts(
            run_id, context, run_result, {filename: result_id} if result_id else {}
        )
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra={"setup": setup_summary, **run_result})
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                "result_id": result_id,
                "setup": setup_summary,
                **run_result,
            },
        )
    except FileNotFoundError:
        error = "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。"
        run_result = {
            **build_run_video_result(started_at, context["results_dir"]),
            **build_playwright_report_result(started_at, context["report_dir"]),
        }
        update_run_result(
            result_id,
            status="failed",
            error_message=error,
            database_reset_status="succeeded" if context.get("setup_resolution") else None,
        )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary({filename: "failed"}),
            completed_files=1,
            error=error,
            finished=True,
        )
        register_execution_artifacts(
            run_id, context, run_result, {filename: result_id} if result_id else {}
        )
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=run_result)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                "result_id": result_id,
                **run_result,
            },
        )
    except OSError as exc:
        error = f"脚本执行失败：{exc}"
        run_result = {
            **build_run_video_result(started_at, context["results_dir"]),
            **build_playwright_report_result(started_at, context["report_dir"]),
        }
        update_run_result(
            result_id,
            status="failed",
            error_message=error,
            database_reset_status="succeeded" if context.get("setup_resolution") else None,
        )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary({filename: "failed"}),
            completed_files=1,
            error=error,
            finished=True,
        )
        register_execution_artifacts(
            run_id, context, run_result, {filename: result_id} if result_id else {}
        )
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=run_result)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                "result_id": result_id,
                **run_result,
            },
        )
    except Exception as exc:
        error = f"测试前准备脚本执行失败：{exc}"
        run_result = {
            **build_run_video_result(started_at, context["results_dir"]),
            **build_playwright_report_result(started_at, context["report_dir"]),
        }
        update_run_result(
            result_id, status="failed", error_message=error, database_reset_status="failed"
        )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary({filename: "failed"}),
            completed_files=0,
            error=error,
            finished=True,
        )
        register_execution_artifacts(
            run_id, context, run_result, {filename: result_id} if result_id else {}
        )
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=run_result)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                "result_id": result_id,
                **run_result,
            },
        )
    except GeneratorExit:
        cancel_error = "流式连接已关闭，执行任务已取消。"
        terminate_process(current_process)
        if not terminalized:
            best_effort(
                update_run_result,
                result_id,
                status="interrupted",
                stdout_tail=output_stream.output_tail(),
                error_message=cancel_error,
                command=context["command_text"],
            )
            best_effort(
                update_test_run,
                run_id,
                status="cancelled",
                summary=build_execution_summary({filename: "interrupted"}),
                completed_files=0,
                error=cancel_error,
                finished=True,
            )
            try:
                output_stream.cancel_job(
                    cancel_error,
                    target_asset_id=(script_asset.get("asset_id") if script_asset else None),
                )
            except Exception:
                output_stream.abort()
        raise
    finally:
        output_stream.close()
        try:
            context["video_config"].unlink(missing_ok=True)
        except OSError:
            pass


def _stream_module_script_execution_impl(module_name, filenames, context, *, agent_stream=False):
    started_at = time.time()
    setup_summary = None
    setup_logs = []
    completed_playwright_files = 0
    current_process = None
    terminalized = False
    execution_mode = context.get("execution_mode", EXECUTION_MODE_BATCH)
    run_id = context["run_id"]
    job_id = f"execution-{run_id}"
    create_test_run(
        run_id,
        "module",
        execution_mode,
        module_name=module_name,
        command=context["command_text"],
        env=build_execution_env_metadata({"filenames": filenames}),
        total_files=len(filenames),
    )
    create_test_job("execution", job_id=job_id, status="running")
    result_ids = {}
    for index, item_filename in enumerate(filenames, start=1):
        row = create_run_result_for_script(
            run_id,
            index,
            module_name,
            item_filename,
            command=context["command_text"],
            status="unknown",
        )
        if row:
            result_ids[item_filename] = row["result_id"]
    script_results = {filename: "running" for filename in filenames}
    output_stream = _BufferedExecutionOutput(
        job_id,
        agent_stream=agent_stream,
        project_root=context.get("project_root"),
    )

    def emit_log(message):
        return output_stream.emit_log(message)

    def emit_delta(text):
        return output_stream.emit_delta(text)

    def emit_status(status, error=None, extra=None):
        payload = {
            "status": status,
            "module_name": module_name,
            "filenames": filenames,
            "target_path": str(get_script_module_dir(module_name)),
            "command": context["command_text"],
            "execution_mode": execution_mode,
            "run_id": run_id,
            "job_id": job_id,
        }
        if error:
            payload["error"] = error
        if extra:
            payload.update(extra)
        return output_stream.emit_event("status", payload)

    def finish_execution_job(status, error=None):
        nonlocal terminalized
        pending = output_stream.flush("terminal")
        if pending:
            yield pending
        output_stream.finish_job(status, error=error)
        terminalized = True

    try:
        yield emit_status("running")
        yield emit_log(f"执行模式：{get_execution_mode_label(execution_mode)}。")
        if context.get("setup_resolution") and execution_mode != EXECUTION_MODE_SERIAL_PER_FILE:
            setup_summary = execute_setup_profile(
                context["setup_resolution"], parent_run_id=run_id, emit_log=setup_logs.append
            )
            for message in setup_logs:
                yield emit_log(message)
        if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE:
            returncodes = []
            database_error = None
            execution_error = None
            merge_returncode = 0
            context["blob_report_dir"].mkdir(parents=True, exist_ok=True)

            def record_preparation_failure(pending_filenames, error):
                for offset, pending_filename in enumerate(pending_filenames):
                    pending_status = "failed" if offset == 0 else "interrupted"
                    script_results[pending_filename] = pending_status
                    updates = {
                        "status": pending_status,
                        "error_message": error,
                    }
                    if offset == 0:
                        updates["database_reset_status"] = "failed"
                    update_run_result(result_ids.get(pending_filename), **updates)

            for index, filename in enumerate(filenames, start=1):
                relative_script_path = get_script_test_relative_path(module_name, filename)
                command, command_text = build_playwright_test_command(
                    context["video_config"], [relative_script_path]
                )
                part_id = f"part-{index:03d}"
                blob_output_file = context["blob_report_dir"] / f"{part_id}.zip"
                part_results_dir = context["results_dir"] / part_id
                try:
                    context["json_report_file"].unlink(missing_ok=True)
                except OSError:
                    pass

                yield emit_log(f"准备执行第 {index}/{len(filenames)} 个脚本：{filename}")
                item_setup_resolution = None
                if context.get("setup_resolution"):
                    try:
                        item_setup_resolution = resolve_setup_profile(
                            build_setup_targets(module_name=module_name, filename=filename)
                        )
                    except Exception as exc:
                        database_error = f"解析测试准备脚本失败：{exc}"
                        yield emit_log(database_error)
                        record_preparation_failure(filenames[index - 1 :], database_error)
                        break
                if item_setup_resolution:
                    item_setup_logs = []
                    try:
                        setup_summary = execute_setup_profile(
                            item_setup_resolution,
                            parent_run_id=run_id,
                            emit_log=item_setup_logs.append,
                            target_override={
                                "scope_type": "script",
                                "scope_key": f"{module_name}/{filename}",
                            },
                        )
                    except SetupPreparationError as exc:
                        setup_summary = exc.summary
                        for message in item_setup_logs:
                            yield emit_log(message)
                        database_error = str(exc)
                        if not item_setup_logs or item_setup_logs[-1] != database_error:
                            yield emit_log(database_error)
                        record_preparation_failure(filenames[index - 1 :], database_error)
                        break
                    except Exception as exc:
                        for message in item_setup_logs:
                            yield emit_log(message)
                        database_error = f"测试前准备脚本执行异常：{exc}"
                        yield emit_log(database_error)
                        record_preparation_failure(filenames[index - 1 :], database_error)
                        break
                    for message in item_setup_logs:
                        yield emit_log(message)
                yield emit_log(f"执行命令：{command_text}")
                env = os.environ.copy()
                env.update(get_playwright_execution_env())
                env["TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE"] = str(blob_output_file)
                env["TEST_PLAN_VIEWER_OUTPUT_DIR"] = str(part_results_dir)
                part_started_at = time.time()
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=context["project_root"],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=0,
                    )
                    current_process = process
                except (FileNotFoundError, OSError) as exc:
                    if isinstance(exc, FileNotFoundError):
                        execution_error = "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。"
                    else:
                        execution_error = f"模块脚本执行失败：{exc}"
                    yield emit_log(execution_error)
                    for offset, pending_filename in enumerate(filenames[index - 1 :]):
                        pending_status = "failed" if offset == 0 else "interrupted"
                        script_results[pending_filename] = pending_status
                        updates = {
                            "status": pending_status,
                            "error_message": execution_error,
                        }
                        if offset == 0 and item_setup_resolution:
                            updates["database_reset_status"] = "succeeded"
                        update_run_result(result_ids.get(pending_filename), **updates)
                    break

                for raw_line in iter_bounded_process_output(process):
                    if raw_line is None:
                        pending = output_stream.flush_due()
                        if pending:
                            yield pending
                        elif agent_stream:
                            yield ": agent-cancel-check\n\n"
                    else:
                        line = normalize_process_output(raw_line)
                        pending = emit_delta(strip_ansi(line))
                        if pending:
                            yield pending

                file_returncode = process.wait()
                current_process = None
                completed_playwright_files += 1
                returncodes.append(file_returncode)
                fallback_status = "succeeded" if file_returncode == 0 else "failed"
                file_result = parse_playwright_json_script_results(
                    context["json_report_file"],
                    module_name,
                    [filename],
                    fallback_status,
                )
                script_results[filename] = file_result.get(filename, fallback_status)
                update_run_result(
                    result_ids.get(filename),
                    status=script_results[filename],
                    stdout_tail=output_stream.output_tail(),
                    error_message=None
                    if file_returncode == 0
                    else f"脚本执行失败，退出码：{file_returncode}",
                    command=command_text,
                    database_reset_status="succeeded" if item_setup_resolution else None,
                )
                register_script_video_artifact(
                    run_id, result_ids.get(filename), part_started_at, part_results_dir
                )
                update_test_run(run_id, completed_files=completed_playwright_files)

            blob_reports = sorted(context["blob_report_dir"].glob("*.zip"))
            if blob_reports:
                yield emit_log("合并 Playwright 批量执行报告。")
                yield emit_log(f"执行命令：{context['merge_command_text']}")
                merge_completed = subprocess.run(
                    context["merge_command"],
                    cwd=context["project_root"],
                    capture_output=True,
                    timeout=get_script_execution_timeout_seconds(),
                )
                merge_returncode = merge_completed.returncode
                merge_output = summarize_process_output(
                    merge_completed.stdout, merge_completed.stderr
                )
                if merge_output:
                    pending = emit_delta(strip_ansi(merge_output))
                    if pending:
                        yield pending
            elif not database_error and not execution_error:
                merge_returncode = 1
                yield emit_log("未找到 Playwright blob report，无法合并批量执行报告。")

            returncode = (
                0
                if not database_error
                and not execution_error
                and all(code == 0 for code in returncodes)
                and merge_returncode == 0
                else 1
            )
            merge_failed = merge_returncode != 0
        else:
            process = subprocess.Popen(
                context["command"],
                cwd=context["project_root"],
                env=get_playwright_execution_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            current_process = process

            for raw_line in iter_bounded_process_output(process):
                if raw_line is None:
                    pending = output_stream.flush_due()
                    if pending:
                        yield pending
                    elif agent_stream:
                        yield ": agent-cancel-check\n\n"
                else:
                    line = normalize_process_output(raw_line)
                    pending = emit_delta(strip_ansi(line))
                    if pending:
                        yield pending

            returncode = process.wait()
            current_process = None
            fallback_status = "succeeded" if returncode == 0 else "failed"
            script_results = parse_playwright_json_script_results(
                context["json_report_file"],
                module_name,
                filenames,
                fallback_status,
            )
            database_error = None
            execution_error = None
            merge_failed = False
            merge_returncode = 0

        output = output_stream.output_tail()
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        status = "succeeded" if returncode == 0 else "failed"
        result_summary = format_script_result_summary(script_results)
        error = (
            None
            if returncode == 0
            else database_error
            if database_error
            else execution_error
            if execution_error
            else f"Playwright 批量报告合并失败，退出码：{merge_returncode}"
            if merge_failed
            else f"模块脚本批量执行完成，{result_summary}，退出码：{returncode}"
            if result_summary
            else f"模块脚本批量执行失败，退出码：{returncode}"
        )
        extra = {
            "returncode": returncode,
            "output": output,
            "execution_mode": execution_mode,
            "script_results": script_results,
            "run_id": run_id,
            "job_id": job_id,
            "setup": setup_summary,
            **run_result,
        }
        if execution_mode != EXECUTION_MODE_SERIAL_PER_FILE:
            for filename, script_status in script_results.items():
                update_run_result(
                    result_ids.get(filename),
                    status=script_status,
                    stdout_tail=output,
                    error_message=None if script_status == "succeeded" else error,
                    command=context["command_text"],
                    database_reset_status="succeeded" if context.get("setup_resolution") else None,
                )
        summary = build_execution_summary(script_results, returncode)
        update_test_run(
            run_id,
            status=status,
            summary=summary,
            completed_files=(
                completed_playwright_files
                if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE
                else sum(1 for value in script_results.values() if value != "running")
            ),
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("succeeded" if returncode == 0 else "failed", error=error)
        yield emit_status(status, error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": returncode == 0,
                "status": status,
                "returncode": returncode,
                "output": output,
                "error": error,
                "execution_mode": execution_mode,
                "script_results": script_results,
                "run_id": run_id,
                "job_id": job_id,
                "setup": setup_summary,
                **run_result,
            },
        )
    except SetupPreparationError as exc:
        error = str(exc)
        setup_summary = exc.summary
        for message in setup_logs:
            yield emit_log(message)
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        script_results = {filename: "failed" for filename in filenames}
        extra = {
            "execution_mode": execution_mode,
            "script_results": script_results,
            "setup": setup_summary,
            **run_result,
        }
        for item_filename in filenames:
            update_run_result(
                result_ids.get(item_filename),
                status="failed",
                error_message=error,
                database_reset_status="failed",
            )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary(script_results),
            completed_files=0,
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except FileNotFoundError:
        error = "无法找到 npx，请确认 Node.js/npm 已加入运行环境 PATH。"
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        script_results = {filename: "failed" for filename in filenames}
        extra = {"execution_mode": execution_mode, "script_results": script_results, **run_result}
        for item_filename in filenames:
            update_run_result(
                result_ids.get(item_filename),
                status="failed",
                error_message=error,
                database_reset_status="succeeded" if context.get("setup_resolution") else None,
            )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary(script_results),
            completed_files=len(filenames),
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except OSError as exc:
        error = f"模块脚本批量执行失败：{exc}"
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        script_results = {filename: "failed" for filename in filenames}
        extra = {"execution_mode": execution_mode, "script_results": script_results, **run_result}
        for item_filename in filenames:
            update_run_result(
                result_ids.get(item_filename),
                status="failed",
                error_message=error,
                database_reset_status="succeeded" if context.get("setup_resolution") else None,
            )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary(script_results),
            completed_files=len(filenames),
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except Exception as exc:
        error = f"测试前准备脚本执行失败：{exc}"
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        script_results = {filename: "failed" for filename in filenames}
        extra = {"execution_mode": execution_mode, "script_results": script_results, **run_result}
        for item_filename in filenames:
            update_run_result(
                result_ids.get(item_filename),
                status="failed",
                error_message=error,
                database_reset_status="failed",
            )
        update_test_run(
            run_id,
            status="failed",
            summary=build_execution_summary(script_results),
            completed_files=0,
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("failed", error=error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except GeneratorExit:
        cancel_error = "流式连接已关闭，执行任务已取消。"
        terminate_process(current_process)
        if not terminalized:
            cancelled_results = {}
            for item_filename in filenames:
                current_status = script_results.get(item_filename)
                if is_completed_script_result_status(current_status):
                    cancelled_results[item_filename] = current_status
                    continue
                cancelled_results[item_filename] = "interrupted"
                best_effort(
                    update_run_result,
                    result_ids.get(item_filename),
                    status="interrupted",
                    stdout_tail=output_stream.output_tail(),
                    error_message=cancel_error,
                )
            best_effort(
                update_test_run,
                run_id,
                status="cancelled",
                summary=build_execution_summary(cancelled_results),
                completed_files=completed_playwright_files,
                error=cancel_error,
                finished=True,
            )
            try:
                output_stream.cancel_job(cancel_error)
            except Exception:
                output_stream.abort()
        raise
    finally:
        output_stream.close()
        try:
            context["video_config"].unlink(missing_ok=True)
        except OSError:
            pass
        try:
            merge_config = context.get("merge_config")
            if merge_config:
                merge_config.unlink(missing_ok=True)
        except OSError:
            pass


def _stream_test_suite_execution_impl(suite_id, suite_name, items, context, *, agent_stream=False):
    started_at = time.time()
    setup_summary = None
    setup_logs = []
    completed_playwright_files = 0
    current_process = None
    terminalized = False
    execution_mode = context.get("execution_mode", EXECUTION_MODE_BATCH)
    run_id = context["run_id"]
    job_id = f"execution-{run_id}"
    create_test_run(
        run_id,
        "test_suite",
        execution_mode,
        suite_id=suite_id,
        command=context["command_text"],
        env=build_execution_env_metadata({"suite_name": suite_name, "items": items}),
        total_files=len(items),
    )
    create_test_job("execution", job_id=job_id, status="running")
    result_ids = {}
    for index, item in enumerate(context["items"], start=1):
        row = create_run_result_for_script(
            run_id,
            index,
            item["module_name"],
            item["filename"],
            command=context["command_text"],
            status="unknown",
        )
        if row:
            result_ids[item["key"]] = row["result_id"]
    script_results = {item["key"]: "running" for item in context["items"]}
    persisted_result_keys = set()
    output_stream = _BufferedExecutionOutput(
        job_id,
        agent_stream=agent_stream,
        project_root=context.get("project_root"),
    )

    def emit_log(message):
        return output_stream.emit_log(message)

    def emit_delta(text):
        return output_stream.emit_delta(text)

    def emit_status(status, error=None, extra=None):
        payload = {
            "status": status,
            "suite_id": suite_id,
            "suite_name": suite_name,
            "items": items,
            "target_path": "tests",
            "command": context["command_text"],
            "execution_mode": execution_mode,
            "run_id": run_id,
            "job_id": job_id,
        }
        if error:
            payload["error"] = error
        if extra:
            payload.update(extra)
        return output_stream.emit_event("status", payload)

    def finish_execution_job(status, error=None):
        nonlocal terminalized
        pending = output_stream.flush("terminal")
        if pending:
            yield pending
        output_stream.finish_job(status, error=error)
        terminalized = True

    def persist_script_result(key, **updates):
        result = update_run_result(result_ids.get(key), **updates)
        persisted_result_keys.add(key)
        return result

    def finalize_failed_execution(error, preparation_failed=False):
        nonlocal script_results, terminalized
        current_results = dict(script_results)
        unresolved_keys = {
            item["key"]
            for item in context["items"]
            if not is_completed_script_result_status(current_results.get(item["key"]))
        }
        script_results = finalize_script_results_after_error(
            [item["key"] for item in context["items"]],
            current_results,
            unresolved_status="failed" if preparation_failed else "interrupted",
        )
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        extra = {
            "execution_mode": execution_mode,
            "script_results": script_results,
            "total_files": len(context["items"]),
            "completed_files": completed_playwright_files,
            **run_result,
        }
        finalization_errors = []
        for item in context["items"]:
            key = item["key"]
            if key not in unresolved_keys and key in persisted_result_keys:
                continue
            updates = {"status": script_results[key]}
            if key in unresolved_keys:
                updates["error_message"] = error
                if preparation_failed:
                    updates["database_reset_status"] = "failed"
                elif (
                    context.get("setup_resolution")
                    and execution_mode != EXECUTION_MODE_SERIAL_PER_FILE
                ):
                    updates["database_reset_status"] = "succeeded"
            try:
                persist_script_result(key, **updates)
            except Exception as exc:
                finalization_errors.append(f"保存脚本结果 {key} 失败：{exc}")
        try:
            update_test_run(
                run_id,
                status="failed",
                summary=build_execution_summary(script_results),
                completed_files=completed_playwright_files,
                error=error,
                finished=True,
            )
        except Exception as exc:
            finalization_errors.append(f"保存执行汇总失败：{exc}")
        try:
            register_execution_artifacts(run_id, context, run_result, result_ids)
        except Exception as exc:
            finalization_errors.append(f"登记执行产物失败：{exc}")
        pending = output_stream.flush("terminal")
        if pending:
            yield pending
        try:
            output_stream.finish_job("failed", error=error)
            terminalized = True
        except Exception as exc:
            finalization_errors.append(f"保存执行任务状态失败：{exc}")
        if finalization_errors:
            extra["finalization_errors"] = finalization_errors
        return extra

    try:
        yield emit_status("running")
        yield emit_log(f"执行模式：{get_execution_mode_label(execution_mode)}。")
        if context.get("setup_resolution") and execution_mode != EXECUTION_MODE_SERIAL_PER_FILE:
            setup_summary = execute_setup_profile(
                context["setup_resolution"], parent_run_id=run_id, emit_log=setup_logs.append
            )
            for message in setup_logs:
                yield emit_log(message)
        if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE:
            returncodes = []
            database_error = None
            execution_error = None
            merge_returncode = 0
            context["blob_report_dir"].mkdir(parents=True, exist_ok=True)

            def persist_preparation_failure(pending_items, error):
                for offset, pending_item in enumerate(pending_items):
                    pending_status = "failed" if offset == 0 else "interrupted"
                    script_results[pending_item["key"]] = pending_status
                    updates = {
                        "status": pending_status,
                        "error_message": error,
                    }
                    if offset == 0:
                        updates["database_reset_status"] = "failed"
                    persist_script_result(pending_item["key"], **updates)

            for index, item in enumerate(context["items"], start=1):
                relative_script_path = item["relative_path"]
                command, command_text = build_playwright_test_command(
                    context["video_config"], [relative_script_path]
                )
                part_id = f"part-{index:03d}"
                blob_output_file = context["blob_report_dir"] / f"{part_id}.zip"
                part_results_dir = context["results_dir"] / part_id
                try:
                    context["json_report_file"].unlink(missing_ok=True)
                except OSError:
                    pass

                yield emit_log(
                    f"准备执行第 {index}/{len(context['items'])} 个测试集脚本：{item['key']}"
                )
                item_setup_resolution = None
                if context.get("setup_resolution"):
                    try:
                        item_setup_resolution = resolve_setup_profile(
                            build_setup_targets(
                                module_name=item["module_name"],
                                filename=item["filename"],
                                suite_uid=suite_id,
                            )
                        )
                    except Exception as exc:
                        database_error = f"解析测试准备脚本失败：{exc}"
                        yield emit_log(database_error)
                        persist_preparation_failure(context["items"][index - 1 :], database_error)
                        break
                if item_setup_resolution:
                    item_setup_logs = []
                    try:
                        setup_summary = execute_setup_profile(
                            item_setup_resolution,
                            parent_run_id=run_id,
                            emit_log=item_setup_logs.append,
                            target_override={
                                "scope_type": "script",
                                "scope_key": item["key"],
                            },
                        )
                    except SetupPreparationError as exc:
                        setup_summary = exc.summary
                        for message in item_setup_logs:
                            yield emit_log(message)
                        database_error = str(exc)
                        if not item_setup_logs or item_setup_logs[-1] != database_error:
                            yield emit_log(database_error)
                        persist_preparation_failure(context["items"][index - 1 :], database_error)
                        break
                    except Exception as exc:
                        for message in item_setup_logs:
                            yield emit_log(message)
                        database_error = f"测试前准备脚本执行异常：{exc}"
                        yield emit_log(database_error)
                        persist_preparation_failure(context["items"][index - 1 :], database_error)
                        break
                    for message in item_setup_logs:
                        yield emit_log(message)
                yield emit_log(f"执行命令：{command_text}")
                env = os.environ.copy()
                env.update(get_playwright_execution_env())
                env["TEST_PLAN_VIEWER_BLOB_OUTPUT_FILE"] = str(blob_output_file)
                env["TEST_PLAN_VIEWER_OUTPUT_DIR"] = str(part_results_dir)
                part_started_at = time.time()
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=context["project_root"],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=0,
                    )
                    current_process = process
                except (FileNotFoundError, OSError) as exc:
                    missing_target = getattr(exc, "filename", "") or str(exc)
                    execution_error = (
                        f"测试集执行失败，未找到文件或命令：{missing_target}"
                        if isinstance(exc, FileNotFoundError)
                        else f"测试集执行失败：{exc}"
                    )
                    yield emit_log(execution_error)
                    for offset, pending_item in enumerate(context["items"][index - 1 :]):
                        pending_status = "failed" if offset == 0 else "interrupted"
                        script_results[pending_item["key"]] = pending_status
                        updates = {
                            "status": pending_status,
                            "error_message": execution_error,
                        }
                        if offset == 0 and item_setup_resolution:
                            updates["database_reset_status"] = "succeeded"
                        persist_script_result(pending_item["key"], **updates)
                    break

                for raw_line in iter_bounded_process_output(process):
                    if raw_line is None:
                        pending = output_stream.flush_due()
                        if pending:
                            yield pending
                        elif agent_stream:
                            yield ": agent-cancel-check\n\n"
                    else:
                        line = normalize_process_output(raw_line)
                        pending = emit_delta(strip_ansi(line))
                        if pending:
                            yield pending

                file_returncode = process.wait()
                current_process = None
                completed_playwright_files += 1
                returncodes.append(file_returncode)
                fallback_status = "succeeded" if file_returncode == 0 else "failed"
                file_result = parse_playwright_json_relative_script_results(
                    context["json_report_file"],
                    {relative_script_path.replace("\\", "/"): item["key"]},
                    fallback_status,
                )
                script_results[item["key"]] = file_result.get(item["key"], fallback_status)
                persist_script_result(
                    item["key"],
                    status=script_results[item["key"]],
                    stdout_tail=output_stream.output_tail(),
                    error_message=None
                    if file_returncode == 0
                    else f"脚本执行失败，退出码：{file_returncode}",
                    command=command_text,
                    database_reset_status="succeeded" if item_setup_resolution else None,
                )
                register_script_video_artifact(
                    run_id, result_ids.get(item["key"]), part_started_at, part_results_dir
                )
                update_test_run(run_id, completed_files=completed_playwright_files)
                yield emit_status(
                    "running",
                    extra={
                        "script_results": script_results,
                        "total_files": len(context["items"]),
                        "completed_files": completed_playwright_files,
                    },
                )

            blob_reports = sorted(context["blob_report_dir"].glob("*.zip"))
            if blob_reports:
                yield emit_log("合并 Playwright 测试集执行报告。")
                yield emit_log(f"执行命令：{context['merge_command_text']}")
                merge_completed = subprocess.run(
                    context["merge_command"],
                    cwd=context["project_root"],
                    capture_output=True,
                    timeout=get_script_execution_timeout_seconds(),
                )
                merge_returncode = merge_completed.returncode
                merge_output = summarize_process_output(
                    merge_completed.stdout, merge_completed.stderr
                )
                if merge_output:
                    pending = emit_delta(strip_ansi(merge_output))
                    if pending:
                        yield pending
            elif not database_error and not execution_error:
                merge_returncode = 1
                yield emit_log("未找到 Playwright blob report，无法合并测试集执行报告。")

            returncode = (
                0
                if not database_error
                and not execution_error
                and all(code == 0 for code in returncodes)
                and merge_returncode == 0
                else 1
            )
            merge_failed = merge_returncode != 0
        else:
            process = subprocess.Popen(
                context["command"],
                cwd=context["project_root"],
                env=get_playwright_execution_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            current_process = process

            for raw_line in iter_bounded_process_output(process):
                if raw_line is None:
                    pending = output_stream.flush_due()
                    if pending:
                        yield pending
                    elif agent_stream:
                        yield ": agent-cancel-check\n\n"
                else:
                    line = normalize_process_output(raw_line)
                    pending = emit_delta(strip_ansi(line))
                    if pending:
                        yield pending

            returncode = process.wait()
            current_process = None
            completed_playwright_files = len(context["items"])
            fallback_status = "succeeded" if returncode == 0 else "failed"
            script_results = parse_playwright_json_relative_script_results(
                context["json_report_file"],
                context["relative_path_keys"],
                fallback_status,
            )
            database_error = None
            execution_error = None
            merge_failed = False
            merge_returncode = 0

        output = output_stream.output_tail()
        run_result = build_playwright_report_result(started_at, context["report_dir"])
        status = "succeeded" if returncode == 0 else "failed"
        result_summary = format_script_result_summary(script_results)
        error = (
            None
            if returncode == 0
            else database_error
            if database_error
            else execution_error
            if execution_error
            else f"Playwright 测试集报告合并失败，退出码：{merge_returncode}"
            if merge_failed
            else f"测试集执行完成，{result_summary}，退出码：{returncode}"
            if result_summary
            else f"测试集执行失败，退出码：{returncode}"
        )
        extra = {
            "returncode": returncode,
            "output": output,
            "execution_mode": execution_mode,
            "script_results": script_results,
            "run_id": run_id,
            "job_id": job_id,
            "total_files": len(context["items"]),
            "completed_files": completed_playwright_files,
            "setup": setup_summary,
            **run_result,
        }
        if execution_mode != EXECUTION_MODE_SERIAL_PER_FILE:
            for key, script_status in script_results.items():
                persist_script_result(
                    key,
                    status=script_status,
                    stdout_tail=output,
                    error_message=None if script_status == "succeeded" else error,
                    command=context["command_text"],
                    database_reset_status="succeeded" if context.get("setup_resolution") else None,
                )
        summary = build_execution_summary(script_results, returncode)
        update_test_run(
            run_id,
            status=status,
            summary=summary,
            completed_files=completed_playwright_files,
            error=error,
            finished=True,
        )
        register_execution_artifacts(run_id, context, run_result, result_ids)
        yield from finish_execution_job("succeeded" if returncode == 0 else "failed", error=error)
        yield emit_status(status, error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": returncode == 0,
                "status": status,
                "returncode": returncode,
                "output": output,
                "error": error,
                "execution_mode": execution_mode,
                "script_results": script_results,
                "run_id": run_id,
                "job_id": job_id,
                "total_files": len(context["items"]),
                "completed_files": completed_playwright_files,
                "setup": setup_summary,
                **run_result,
            },
        )
    except SetupPreparationError as exc:
        error = str(exc)
        setup_summary = exc.summary
        for message in setup_logs:
            yield emit_log(message)
        extra = yield from finalize_failed_execution(error, preparation_failed=True)
        extra["setup"] = setup_summary
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except FileNotFoundError as exc:
        missing_target = getattr(exc, "filename", "") or str(exc)
        error = f"测试集执行失败，未找到文件或命令：{missing_target}"
        extra = yield from finalize_failed_execution(error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except OSError as exc:
        error = f"测试集执行失败：{exc}"
        extra = yield from finalize_failed_execution(error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except Exception as exc:
        error = f"测试集执行异常：{exc}"
        extra = yield from finalize_failed_execution(error)
        yield emit_status("failed", error=error, extra=extra)
        yield output_stream.emit_event(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": error,
                "run_id": run_id,
                "job_id": job_id,
                **extra,
            },
        )
    except GeneratorExit:
        cancel_error = "流式连接已关闭，执行任务已取消。"
        terminate_process(current_process)
        if not terminalized:
            cancelled_results = {}
            for item in context["items"]:
                key = item["key"]
                current_status = script_results.get(key)
                if is_completed_script_result_status(current_status):
                    cancelled_results[key] = current_status
                    continue
                cancelled_results[key] = "interrupted"
                best_effort(
                    persist_script_result,
                    key,
                    status="interrupted",
                    stdout_tail=output_stream.output_tail(),
                    error_message=cancel_error,
                )
            best_effort(
                update_test_run,
                run_id,
                status="cancelled",
                summary=build_execution_summary(cancelled_results),
                completed_files=completed_playwright_files,
                error=cancel_error,
                finished=True,
            )
            try:
                output_stream.cancel_job(cancel_error)
            except Exception:
                output_stream.abort()
        raise
    finally:
        output_stream.close()
        try:
            context["video_config"].unlink(missing_ok=True)
        except OSError:
            pass
        try:
            merge_config = context.get("merge_config")
            if merge_config:
                merge_config.unlink(missing_ok=True)
        except OSError:
            pass


def stream_script_execution(dependencies, module_name, filename, context, *, agent_stream=False):
    return _bind_dependencies(_stream_script_execution_impl, dependencies)(
        module_name, filename, context, agent_stream=agent_stream
    )


def stream_module_script_execution(
    dependencies, module_name, filenames, context, *, agent_stream=False
):
    return _bind_dependencies(_stream_module_script_execution_impl, dependencies)(
        module_name, filenames, context, agent_stream=agent_stream
    )


def stream_test_suite_execution(
    dependencies, suite_id, suite_name, items, context, *, agent_stream=False
):
    return _bind_dependencies(_stream_test_suite_execution_impl, dependencies)(
        suite_id, suite_name, items, context, agent_stream=agent_stream
    )
