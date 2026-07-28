import subprocess
import time
from dataclasses import dataclass

from test_plan_viewer.setup.model import SetupPreparationError


@dataclass(frozen=True)
class SetupServiceDependencies:
    get_current_project: callable
    is_platform_database_enabled: callable
    list_setup_bindings: callable
    select_setup_binding: callable
    get_setup_script: callable
    create_setup_run_record: callable
    execute_setup_script_once: callable
    finish_setup_run_record: callable
    redact_setup_text: callable
    normalize_process_output: callable
    resolve_setup_profile: callable
    execute_setup_profile: callable
    clock: callable = time.time
    timeout_expired: type = subprocess.TimeoutExpired
    preparation_error: type = SetupPreparationError


class SetupService:
    def __init__(self, dependencies):
        self.dependencies = dependencies

    def resolve_setup_profile(self, targets):
        if not self.dependencies.is_platform_database_enabled():
            return None
        bindings = self.dependencies.list_setup_bindings(
            include_disabled=False
        )
        selected = self.dependencies.select_setup_binding(
            bindings,
            targets,
        )
        if not selected:
            return None
        script = self.dependencies.get_setup_script(
            selected["script_uid"]
        )
        if not script or not script.get("enabled"):
            return None
        target = next(
            item
            for item in targets
            if (
                item["scope_type"] == selected["scope_type"]
                and item["scope_key"] == selected["scope_key"]
            )
        )
        return {
            "binding": selected,
            "script": script,
            "profile": script,
            "target": target,
        }

    def execute_setup_profile(
        self,
        resolution,
        parent_run_id=None,
        emit_log=None,
        target_override=None,
    ):
        script = resolution.get("script") or resolution.get("profile")
        if not script:
            raise ValueError("准备脚本解析结果无效。")
        setup_run = self.dependencies.create_setup_run_record(
            parent_run_id,
            resolution,
            target_override=target_override,
        )
        emit_log = emit_log or (lambda _message: None)
        emit_log(f"开始执行准备脚本：{script['name']}。")
        started = self.dependencies.clock()
        try:
            attempt = self.dependencies.execute_setup_script_once(
                script,
                script["timeout_seconds"],
            )
        except self.dependencies.timeout_expired as exc:
            attempt = {
                "ok": False,
                "exit_code": None,
                "output": self.dependencies.redact_setup_text(
                    self.dependencies.normalize_process_output(
                        exc.output
                    ).strip(),
                    script,
                    limit=4000,
                ),
                "error": (
                    f"执行超时（{script['timeout_seconds']} 秒）。"
                ),
                "duration_ms": int(
                    (self.dependencies.clock() - started) * 1000
                ),
            }
        except Exception as exc:
            attempt = {
                "ok": False,
                "exit_code": None,
                "output": "",
                "error": self.dependencies.redact_setup_text(
                    str(exc),
                    script,
                ),
                "duration_ms": int(
                    (self.dependencies.clock() - started) * 1000
                ),
            }
        status = "succeeded" if attempt["ok"] else "failed"
        terminal_error = (
            ""
            if attempt["ok"]
            else (
                f"准备失败：脚本“{script['name']}”执行失败，"
                f"{attempt['error']}"
            )
        )
        execution_result = {
            "status": status,
            "exit_code": attempt.get("exit_code"),
            "duration_ms": attempt.get("duration_ms"),
            "output_summary": attempt.get("output") or "",
            "error": terminal_error,
        }
        finished_at = self.dependencies.finish_setup_run_record(
            setup_run,
            execution_result,
        )
        result = {
            "uid": setup_run["uid"],
            "parent_run_id": parent_run_id or "",
            "target_type": setup_run["target"]["scope_type"],
            "target_key": setup_run["target"]["scope_key"],
            "script_uid": script["uid"],
            "script_name": script["name"],
            "status": status,
            "exit_code": attempt.get("exit_code"),
            "started_at": setup_run["started_at"],
            "finished_at": finished_at,
            "duration_ms": attempt.get("duration_ms"),
            "output_summary": attempt.get("output") or "",
            "error": terminal_error,
            "summary": {
                "total": 1,
                "passed": int(attempt["ok"]),
                "failed": int(not attempt["ok"]),
            },
        }
        if terminal_error:
            emit_log(terminal_error)
            raise self.dependencies.preparation_error(
                terminal_error,
                result,
            )
        emit_log(f"准备脚本完成：{script['name']}。")
        return result

    def prepare_bound_setup(
        self,
        parent_run_id,
        targets,
        emit_log=None,
    ):
        resolution = self.dependencies.resolve_setup_profile(targets)
        if resolution:
            return self.dependencies.execute_setup_profile(
                resolution,
                parent_run_id=parent_run_id,
                emit_log=emit_log,
            )
        return None
