"""Framework-independent Agent diagnostic bundle construction.

The module owns redaction, ZIP inventory/manifest construction, source
snapshots, and artifact selection. Request state, persistence, project paths,
serializers, runtime inspection, and clocks are supplied explicitly through
dependency records.
"""

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Callable
import zipfile

from test_plan_viewer.artifacts.naming import (
    ARTIFACT_FILENAME_UNSAFE_PATTERN,
)


DIAGNOSTIC_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|session|private[_-]?key|access[_-]?key|username)"
)


@dataclass(frozen=True)
class DiagnosticBuilderDependencies:
    """Capabilities and limits used by redaction and ZIP assembly."""

    get_current_project: Callable
    get_platform_database_config: Callable
    redact_sensitive_text: Callable
    get_project_root: Callable
    get_home_path: Callable
    text_file_max_bytes: int
    bundle_max_bytes: int


@dataclass(frozen=True)
class AgentDiagnosticDependencies:
    """All runtime collaborators needed to build an attempt bundle."""

    builder: DiagnosticBuilderDependencies
    load_json_column: Callable
    get_requirement_by_uid: Callable
    read_requirement_markdown: Callable
    get_plan_target_path: Callable
    get_script_file: Callable
    get_asset_revision: Callable
    git_show_file: Callable
    git_diff_file: Callable
    list_job_artifacts: Callable
    list_run_artifacts: Callable
    serialize_run_artifact_payload: Callable
    get_agent_run_row: Callable
    get_agent_attempt: Callable
    serialize_agent_run: Callable
    serialize_agent_attempt: Callable
    get_agent_step_row: Callable
    serialize_agent_step: Callable
    get_test_job: Callable
    serialize_job: Callable
    agent_step_name: Callable
    list_agent_events: Callable
    serialize_agent_event: Callable
    get_job_log_path: Callable
    get_test_run: Callable
    serialize_test_suite_execution_run: Callable
    get_run_result: Callable
    serialize_run_result: Callable
    get_git_head_sha: Callable
    current_time_ms: Callable
    platform_version: Callable
    python_version: str
    run_process: Callable
    format_timestamp: Callable
    bundle_format_version: int
    playwright_config_filenames: tuple


def collect_diagnostic_secret_values(value, parent_key=""):
    """Collect scalar values stored below sensitive configuration keys."""

    secrets = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "")
            if (
                DIAGNOSTIC_SENSITIVE_KEY_PATTERN.search(key_text)
                and item not in (None, "")
                and isinstance(item, (str, int, float))
            ):
                secrets.add(str(item))
            secrets.update(
                collect_diagnostic_secret_values(item, key_text)
            )
    elif isinstance(value, list):
        for item in value:
            secrets.update(
                collect_diagnostic_secret_values(item, parent_key)
            )
    return secrets


def diagnostic_redaction_context(dependencies):
    """Return source configs and concrete secret values for redaction."""

    project = dependencies.get_current_project()
    configs = [
        project.get("target_system") or {},
        project.get("database_baseline") or {},
        project.get("opencode_config") or {},
        dependencies.get_platform_database_config() or {},
    ]
    secrets = set()
    for config in configs:
        secrets.update(collect_diagnostic_secret_values(config))
    return configs, {secret for secret in secrets if len(secret) >= 2}


def redact_diagnostic_text(
    value,
    *,
    dependencies,
    limit=None,
    context=None,
):
    """Redact credentials and anonymize project/home paths in text."""

    configs, secrets = context or diagnostic_redaction_context(dependencies)
    text = dependencies.redact_sensitive_text(value, *configs)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "******")
    text = re.sub(
        r"((?:账号|用户名|密码)\s*[：:=]?\s*)([^，,。；;\s]+)",
        r"\1******",
        text,
    )
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1******",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|cookie|set-cookie)\s*[:=]\s*)[^\s,;]+",
        r"\1******",
        text,
    )
    project_root_path = dependencies.get_project_root().expanduser()
    project_roots = {
        str(project_root_path),
        str(project_root_path.resolve(strict=False)),
    }
    home_path = Path(dependencies.get_home_path()).expanduser()
    homes = {
        str(home_path),
        str(home_path.resolve(strict=False)),
    }
    for project_root in sorted(
        (item for item in project_roots if item),
        key=len,
        reverse=True,
    ):
        text = text.replace(project_root, "${PROJECT_ROOT}")
    for home in sorted(
        (item for item in homes if item),
        key=len,
        reverse=True,
    ):
        text = text.replace(home, "${HOME}")
    if limit and len(text.encode("utf-8")) > limit:
        encoded = text.encode("utf-8")[:limit]
        text = (
            encoded.decode("utf-8", errors="ignore")
            + "\n...[诊断包已截断]"
        )
    return text


def redact_diagnostic_value(
    value,
    *,
    dependencies,
    context=None,
    key="",
):
    """Recursively redact sensitive fields and strings in JSON-like data."""

    context = context or diagnostic_redaction_context(dependencies)
    if (
        DIAGNOSTIC_SENSITIVE_KEY_PATTERN.search(str(key or ""))
        and value not in (None, "")
    ):
        return "******"
    if isinstance(value, dict):
        return {
            str(item_key): redact_diagnostic_value(
                item,
                dependencies=dependencies,
                context=context,
                key=item_key,
            )
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [
            redact_diagnostic_value(
                item,
                dependencies=dependencies,
                context=context,
                key=key,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            redact_diagnostic_value(
                item,
                dependencies=dependencies,
                context=context,
                key=key,
            )
            for item in value
        ]
    if isinstance(value, str):
        return redact_diagnostic_text(
            value,
            dependencies=dependencies,
            context=context,
        )
    return value


def normalize_diagnostic_member_name(name):
    """Validate and normalize one portable ZIP member path."""

    name = str(name or "").replace("\\", "/").strip("/")
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("诊断包文件路径非法。")
    return path.as_posix()


class DiagnosticBundleBuilder:
    """Accumulate a redacted, size-bounded diagnostic ZIP."""

    def __init__(self, dependencies, *, redaction_context=None):
        self.dependencies = dependencies
        self.files = {}
        self.inventory = []
        self.omitted = []
        self.total_bytes = 0
        self.redaction_context = (
            redaction_context
            or diagnostic_redaction_context(dependencies)
        )

    def omit(self, name, reason, source=""):
        self.omitted.append(
            {
                "path": str(name or ""),
                "reason": str(reason or ""),
                "source": str(source or ""),
            }
        )

    def add_bytes(self, name, data, *, source="generated"):
        name = normalize_diagnostic_member_name(name)
        data = bytes(data or b"")
        if len(data) > self.dependencies.text_file_max_bytes:
            self.omit(name, "文件超过单文件大小限制", source)
            return False
        if (
            self.total_bytes + len(data)
            > self.dependencies.bundle_max_bytes
        ):
            self.omit(name, "诊断包超过总大小限制", source)
            return False
        if name in self.files:
            self.omit(name, "压缩包内路径重复", source)
            return False
        self.files[name] = data
        self.total_bytes += len(data)
        self.inventory.append(
            {
                "path": name,
                "source": source,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        return True

    def add_text(
        self,
        name,
        text,
        *,
        source="generated",
        redact=True,
    ):
        if redact:
            text = redact_diagnostic_text(
                text,
                limit=self.dependencies.text_file_max_bytes,
                context=self.redaction_context,
                dependencies=self.dependencies,
            )
        return self.add_bytes(
            name,
            str(text or "").encode("utf-8"),
            source=source,
        )

    def add_json(self, name, value, *, source="database"):
        redacted = redact_diagnostic_value(
            value,
            context=self.redaction_context,
            dependencies=self.dependencies,
        )
        return self.add_text(
            name,
            json.dumps(
                redacted,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            source=source,
            redact=False,
        )

    def add_project_text_file(
        self,
        name,
        path,
        *,
        source="filesystem",
    ):
        path = Path(path or "").expanduser()
        if path.is_symlink():
            self.omit(name, "不打包符号链接", str(path))
            return False
        try:
            resolved = path.resolve(strict=True)
            root = (
                self.dependencies.get_project_root()
                .expanduser()
                .resolve(strict=True)
            )
            resolved.relative_to(root)
        except (OSError, ValueError):
            self.omit(
                name,
                "文件不存在或不在项目目录中",
                str(path),
            )
            return False
        if not resolved.is_file():
            self.omit(name, "不打包目录", str(path))
            return False
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            self.omit(name, f"读取失败：{exc}", str(path))
            return False
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.omit(
                name,
                "默认诊断包不包含无法脱敏的二进制文件",
                str(path),
            )
            return False
        return self.add_text(name, text, source=str(path))

    def build(self, manifest):
        self.add_json(
            "inventory.json",
            {
                "included": list(self.inventory),
                "omitted": list(self.omitted),
            },
            source="generated",
        )
        manifest = {
            **manifest,
            "included_files": list(self.inventory),
            "omitted_files": list(self.omitted),
        }
        self.add_json("manifest.json", manifest, source="generated")
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        buffer.seek(0)
        return buffer


def diagnostic_safe_filename(value, fallback):
    """Return the legacy bounded, portable diagnostic filename."""

    value = ARTIFACT_FILENAME_UNSAFE_PATTERN.sub(
        "-",
        str(value or ""),
    ).strip(" .-")
    value = re.sub(r"\s+", "-", value)
    return value[:80] or fallback


def diagnostic_event_matches_attempt(event, attempt, dependencies):
    """Whether a persisted Agent event belongs to one attempt."""

    payload = dependencies.load_json_column(
        event.get("payload_json"),
        {},
    )
    if payload.get("attempt_id") == attempt.get("attempt_id"):
        return True
    if (
        attempt.get("job_id")
        and event.get("job_id") == attempt.get("job_id")
    ):
        return True
    if event.get("step_key") != attempt.get("step_key"):
        return False
    module_name = attempt.get("module_name") or ""
    filename = (
        attempt.get("filename")
        or attempt.get("plan_filename")
        or ""
    )
    return bool(
        (
            module_name
            and (
                payload.get("module_name") == module_name
                or module_name in str(event.get("message") or "")
            )
        )
        or (
            filename
            and (
                payload.get("filename") == filename
                or payload.get("plan_filename") == filename
                or filename in str(event.get("message") or "")
            )
        )
    )


def diagnostic_source_snapshot(
    builder,
    attempt,
    run,
    step,
    dependencies,
):
    """Collect relevant requirement, module, plan, script, and revision text."""

    requirement_uid = run.get("requirement_uid") or ""
    if requirement_uid:
        try:
            requirement = dependencies.get_requirement_by_uid(
                requirement_uid
            )
            if requirement:
                builder.add_text(
                    "sources/requirement.md",
                    dependencies.read_requirement_markdown(requirement),
                    source=(
                        requirement.get("file_path")
                        or "requirements"
                    ),
                )
        except Exception as exc:
            builder.omit(
                "sources/requirement.md",
                f"读取需求失败：{exc}",
                requirement_uid,
            )

    input_snapshot = attempt.get("input_snapshot") or {}
    module_name = (
        attempt.get("module_name")
        or input_snapshot.get("module_name")
        or ""
    )
    module_uid = (
        attempt.get("module_uid")
        or input_snapshot.get("module_uid")
        or ""
    )
    module_item = (
        input_snapshot if isinstance(input_snapshot, dict) else {}
    )
    if not module_item.get("business_goal"):
        step_input = step.get("input") or {}
        modules = (
            step_input.get("modules")
            if isinstance(step_input, dict)
            else []
        )
        module_item = next(
            (
                item
                for item in modules or []
                if isinstance(item, dict)
                and (
                    (
                        module_uid
                        and item.get("module_uid") == module_uid
                    )
                    or (
                        module_name
                        and item.get("module_name") == module_name
                    )
                )
            ),
            module_item,
        )
    if module_item:
        builder.add_json(
            "sources/module.json",
            module_item,
            source="agent_run_attempts.input_snapshot_json",
        )

    plan_filename = (
        attempt.get("plan_filename")
        or input_snapshot.get("plan_filename")
        or ""
    )
    if module_name and plan_filename:
        try:
            builder.add_project_text_file(
                "sources/"
                + diagnostic_safe_filename(
                    plan_filename,
                    "source-plan.md",
                ),
                dependencies.get_plan_target_path(
                    module_name,
                    plan_filename,
                ),
            )
        except Exception as exc:
            builder.omit(
                "sources/source-plan.md",
                f"定位测试计划失败：{exc}",
                f"{module_name}/{plan_filename}",
            )

    filename = (
        attempt.get("filename")
        or input_snapshot.get("filename")
        or ""
    )
    if module_name and filename:
        try:
            builder.add_project_text_file(
                "sources/"
                + diagnostic_safe_filename(
                    filename,
                    "script-current.spec.ts",
                ),
                dependencies.get_script_file(
                    module_name,
                    filename,
                ),
            )
        except Exception as exc:
            builder.omit(
                "sources/script-current.spec.ts",
                f"定位测试脚本失败：{exc}",
                f"{module_name}/{filename}",
            )

    asset = (
        input_snapshot.get("asset")
        if isinstance(input_snapshot, dict)
        and isinstance(input_snapshot.get("asset"), dict)
        else {}
    )
    asset_id = asset.get("asset_id")
    revision_id = asset.get("current_revision_id")
    if asset_id and revision_id:
        try:
            revision = dependencies.get_asset_revision(
                asset_id,
                revision_id,
            )
            if revision:
                before_content = dependencies.git_show_file(
                    revision.get("git_commit_sha"),
                    revision.get("file_path"),
                )
                suffix = (
                    ".spec.ts"
                    if attempt.get("item_type")
                    in {"script", "script_execution", "script_repair"}
                    else ".md"
                )
                builder.add_text(
                    f"sources/before-attempt{suffix}",
                    before_content,
                    source=f"asset-revision:{revision_id}",
                )
                diff = dependencies.git_diff_file(
                    revision.get("git_commit_sha"),
                    revision.get("file_path"),
                )
                if diff:
                    builder.add_text(
                        "sources/changes.patch",
                        diff,
                        source=f"asset-revision:{revision_id}",
                    )
        except Exception as exc:
            builder.omit(
                "sources/before-attempt",
                f"读取历史版本失败：{exc}",
                f"asset:{asset_id}/revision:{revision_id}",
            )


def collect_diagnostic_artifacts(builder, attempt, dependencies):
    """Collect textual outputs and retain metadata for excluded binaries."""

    for index, ref in enumerate(
        attempt.get("artifact_refs") or [],
        start=1,
    ):
        if not isinstance(ref, dict) or not ref.get("path"):
            continue
        path = Path(ref.get("path"))
        safe_name = diagnostic_safe_filename(
            path.name,
            f"partial-{index}.txt",
        )
        builder.add_project_text_file(
            f"artifacts/partial-output/{index:02d}-{safe_name}",
            path,
        )

    job_id = attempt.get("job_id") or ""
    if job_id:
        job_artifacts = dependencies.list_job_artifacts(job_id)
        builder.add_json(
            "artifacts/job-artifacts.json",
            [
                {
                    "artifact_id": item.get("artifact_id"),
                    "artifact_type": item.get("artifact_type"),
                    "relative_path": item.get("relative_path") or "",
                    "size": item.get("size"),
                    "sha256": item.get("sha256") or "",
                    "created_at": item.get("created_at"),
                }
                for item in job_artifacts
            ],
            source="job_artifacts",
        )

    test_run_id = attempt.get("test_run_id") or ""
    if not test_run_id:
        return
    run_artifacts = dependencies.list_run_artifacts(
        test_run_id,
        attempt.get("result_id"),
    )
    builder.add_json(
        "artifacts/run-artifacts.json",
        [
            dependencies.serialize_run_artifact_payload(item)
            for item in run_artifacts
        ],
        source="test_run_artifacts",
    )
    used_names = set()
    for item in run_artifacts:
        artifact_type = item.get("artifact_type") or "artifact"
        path = Path(item.get("path") or "")
        if artifact_type in {"video", "trace", "screenshot"}:
            builder.omit(
                f"artifacts/{artifact_type}/{path.name}",
                "默认脱敏诊断包不包含视频、trace 或截图；元数据已保留",
                str(path),
            )
            continue
        if artifact_type not in {
            "json_report",
            "html_report",
            "log",
        }:
            builder.omit(
                f"artifacts/{path.name}",
                "未识别的产物类型",
                str(path),
            )
            continue
        safe_name = diagnostic_safe_filename(
            path.name,
            f"{artifact_type}.txt",
        )
        member_name = (
            f"artifacts/{artifact_type}/{safe_name}"
        )
        if member_name in used_names:
            member_name = (
                f"artifacts/{artifact_type}/"
                f"{item.get('artifact_id')}-{safe_name}"
            )
        used_names.add(member_name)
        builder.add_project_text_file(member_name, path)


def build_agent_attempt_diagnostic_bundle(
    run_id,
    attempt_id,
    dependencies,
):
    """Build the redacted diagnostic ZIP and stable download filename."""

    run_row = dependencies.get_agent_run_row(run_id)
    attempt_row = dependencies.get_agent_attempt(run_id, attempt_id)
    if not run_row or not attempt_row:
        raise FileNotFoundError("Agent 项目尝试记录不存在。")
    run = dependencies.serialize_agent_run(run_row)
    attempt = dependencies.serialize_agent_attempt(attempt_row)
    step_row = dependencies.get_agent_step_row(
        run_id,
        attempt.get("step_key"),
    )
    step = (
        dependencies.serialize_agent_step(step_row)
        if step_row
        else {}
    )
    job_row = (
        dependencies.get_test_job(attempt.get("job_id"))
        if attempt.get("job_id")
        else None
    )
    job = dependencies.serialize_job(job_row) if job_row else None
    builder = DiagnosticBundleBuilder(dependencies.builder)

    readme = f"""# Agent 自动测试诊断包

- Agent Run：{run_id}
- Attempt：{attempt_id}
- 阶段：{attempt.get('step_key')} / {dependencies.agent_step_name(attempt.get('step_key'))}
- 项目：{attempt.get('item_key')}
- 状态：{attempt.get('status')}
- 结果：{attempt.get('outcome_type') or '-'}
- 错误类型：{attempt.get('error_type') or '-'}
- 错误：{attempt.get('error') or '-'}

请结合 manifest.json、attempt/attempt.json、日志、Prompt、相关源码和执行产物分析失败根因，区分环境、Prompt、Agent、工具、产物和 Playwright 执行问题，并给出可直接实施的修改方案。

本压缩包已对平台配置、Prompt、日志和文本产物进行自动脱敏。视频、trace、截图等难以可靠脱敏的二进制产物默认不包含，详见 inventory.json。
"""
    builder.add_text(
        "README_FOR_CODEX.md",
        readme,
        source="generated",
    )
    builder.add_json(
        "attempt/attempt.json",
        attempt,
        source="agent_run_attempts",
    )
    if attempt.get("status") == "failed":
        builder.add_json(
            "failure/failure.json",
            attempt,
            source="agent_run_attempts",
        )
    builder.add_json(
        "attempt/stage-input.json",
        step.get("input") or {},
        source="agent_run_steps.input_json",
    )
    builder.add_json(
        "attempt/stage-output.json",
        step.get("output") or {},
        source="agent_run_steps.output_json",
    )
    builder.add_json(
        "attempt/stage-counts.json",
        step.get("counts") or {},
        source="agent_run_steps.counts_json",
    )

    events = [
        dependencies.serialize_agent_event(event)
        for event in dependencies.list_agent_events(
            run_id,
            0,
            1000,
        )
        if diagnostic_event_matches_attempt(
            event,
            attempt_row,
            dependencies,
        )
    ]
    event_lines = "\n".join(
        json.dumps(
            redact_diagnostic_value(
                event,
                context=builder.redaction_context,
                dependencies=dependencies.builder,
            ),
            ensure_ascii=False,
            default=str,
        )
        for event in events
    )
    builder.add_text(
        "logs/agent-events.jsonl",
        event_lines,
        source="agent_run_events",
        redact=False,
    )

    if job:
        builder.add_text(
            "prompts/effective-prompt.txt",
            job.get("prompt") or "",
            source="test_jobs.prompt",
        )
        builder.add_json(
            "prompts/prompt-context.json",
            job.get("prompt_context") or {},
            source="test_jobs.prompt_context_json",
        )
        log_path = Path(
            job.get("log_path")
            or dependencies.get_job_log_path(job.get("job_id"))
        )
        builder.add_project_text_file(
            "logs/job.log",
            log_path,
            source="test_jobs.log_path",
        )
        builder.add_json(
            "attempt/job.json",
            {
                key: value
                for key, value in job.items()
                if key not in {"prompt", "log_tail"}
            },
            source="test_jobs",
        )
    else:
        builder.omit(
            "logs/job.log",
            "该 attempt 没有关联 job_id 或任务记录不存在",
        )

    diagnostic_source_snapshot(
        builder,
        attempt,
        run,
        step,
        dependencies,
    )
    collect_diagnostic_artifacts(builder, attempt, dependencies)

    if attempt.get("test_run_id"):
        test_run = dependencies.get_test_run(
            attempt.get("test_run_id")
        )
        if test_run:
            builder.add_json(
                "execution/run.json",
                dependencies.serialize_test_suite_execution_run(
                    test_run
                ),
                source="test_runs",
            )
    if attempt.get("result_id"):
        result = dependencies.get_run_result(
            attempt.get("result_id")
        )
        if result:
            builder.add_json(
                "execution/result.json",
                dependencies.serialize_run_result(result),
                source="test_run_results",
            )

    project = dependencies.builder.get_current_project()
    builder.add_json(
        "environment/project-config.redacted.json",
        {
            "project_key": project.get("project_key"),
            "name": project.get("name"),
            "specs_dir": project.get("specs_dir"),
            "tests_dir": project.get("tests_dir"),
            "target_system": project.get("target_system"),
            "database_baseline": project.get(
                "database_baseline"
            ),
            "plan_generation": project.get("plan_generation"),
            "opencode_config": project.get("opencode_config"),
        },
        source="platform_projects",
    )
    versions = {
        "platform": dependencies.platform_version(),
        "python": dependencies.python_version,
        "git_commit_sha": dependencies.get_git_head_sha(),
    }
    for command, key in (
        (["node", "--version"], "node"),
        (["npm", "--version"], "npm"),
    ):
        try:
            completed = dependencies.run_process(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            versions[key] = (
                completed.stdout or completed.stderr or ""
            ).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            versions[key] = f"unavailable: {exc}"
    builder.add_json(
        "environment/versions.json",
        versions,
        source="runtime",
    )
    for filename in (
        "package.json",
        "package-lock.json",
        *dependencies.playwright_config_filenames,
    ):
        path = dependencies.builder.get_project_root() / filename
        if path.exists():
            builder.add_project_text_file(
                f"environment/{filename}",
                path,
            )

    manifest = {
        "bundle_schema_version": dependencies.bundle_format_version,
        "generated_at": dependencies.current_time_ms(),
        "project_key": project.get("project_key") or "",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "step_key": attempt.get("step_key") or "",
        "item_key": attempt.get("item_key") or "",
        "status": attempt.get("status") or "",
        "job_id": attempt.get("job_id") or "",
        "test_run_id": attempt.get("test_run_id") or "",
        "result_id": attempt.get("result_id"),
        "redaction": {
            "enabled": True,
            "binary_sensitive_artifacts_included": False,
            "project_paths_replaced": True,
        },
    }
    buffer = builder.build(manifest)
    timestamp = dependencies.format_timestamp("%Y%m%d-%H%M%S")
    filename = (
        "agent-diagnostic-{}-{}-{}-{}.zip".format(
            diagnostic_safe_filename(
                project.get("project_key"),
                "project",
            ),
            diagnostic_safe_filename(
                attempt.get("step_key"),
                "step",
            ),
            diagnostic_safe_filename(
                attempt.get("item_key"),
                "item",
            ),
            timestamp,
        )
    )
    return buffer, filename
