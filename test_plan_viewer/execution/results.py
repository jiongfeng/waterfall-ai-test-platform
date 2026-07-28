"""Execution modes, statuses, summaries, and Playwright JSON parsing."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable


EXECUTION_MODE_BATCH = "batch"
EXECUTION_MODE_BATCH_ONCE = "batch_once"
EXECUTION_MODE_SERIAL_PER_FILE = "serial_per_file"
VALID_EXECUTION_MODES = {
    EXECUTION_MODE_BATCH,
    EXECUTION_MODE_SERIAL_PER_FILE,
}

DATABASE_RESET_ONCE_PER_RUN = "once_per_run"
DATABASE_RESET_BEFORE_EACH_FILE = "before_each_file"

PLAYWRIGHT_AGGREGATE_TEST_STATUSES = {
    "expected",
    "unexpected",
    "flaky",
    "skipped",
}
PLAYWRIGHT_RESULT_STATUSES = {
    "passed",
    "failed",
    "timedOut",
    "skipped",
    "interrupted",
}
PLAYWRIGHT_FAILURE_RESULT_STATUSES = {
    "failed",
    "timedOut",
    "interrupted",
}


@dataclass(frozen=True)
class ResultDependencies:
    """Project path and report-file capabilities used by JSON parsing."""

    get_project_root: Callable[[], Path]
    get_script_test_relative_path: Callable[[str, str], str]
    resolve_path: Callable[[Path], Path]
    read_text: Callable[[Path], str]


def db_execution_mode(value):
    if value == EXECUTION_MODE_SERIAL_PER_FILE:
        return EXECUTION_MODE_SERIAL_PER_FILE
    return EXECUTION_MODE_BATCH_ONCE


def execution_database_reset_mode(value):
    if value == EXECUTION_MODE_SERIAL_PER_FILE:
        return DATABASE_RESET_BEFORE_EACH_FILE
    return DATABASE_RESET_ONCE_PER_RUN


def db_run_status(status):
    if status == "succeeded":
        return "passed"
    if status == "failed":
        return "failed"
    if status in {
        "cancelled",
        "timed_out",
        "running",
        "queued",
    }:
        return status
    return "failed"


def db_result_status(status):
    if status in {"succeeded", "passed"}:
        return "passed"
    if status == "failed":
        return "failed"
    if status in {
        "skipped",
        "timed_out",
        "interrupted",
        "unknown",
    }:
        return status
    return "unknown"


def is_completed_script_result_status(status):
    return db_result_status(status) != "unknown"


def finalize_script_results_after_error(
    keys,
    script_results,
    unresolved_status="failed",
):
    current_results = (
        script_results
        if isinstance(script_results, dict)
        else {}
    )
    finalized_results = {}
    for key in keys:
        current_status = current_results.get(key)
        finalized_results[key] = (
            current_status
            if is_completed_script_result_status(current_status)
            else unresolved_status
        )
    return finalized_results


def build_execution_summary(script_results, returncode=None):
    counts = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "unknown": 0,
    }
    for status in (script_results or {}).values():
        normalized = db_result_status(status)
        counts["total"] += 1
        if normalized == "passed":
            counts["passed"] += 1
        elif normalized == "failed":
            counts["failed"] += 1
        elif normalized == "skipped":
            counts["skipped"] += 1
        else:
            counts["unknown"] += 1
    counts["returncode"] = returncode
    return counts


def normalize_execution_mode(value):
    mode = str(value or EXECUTION_MODE_BATCH).strip()
    if not mode:
        return EXECUTION_MODE_BATCH
    if mode not in VALID_EXECUTION_MODES:
        raise ValueError(
            "execution_mode must be 'batch' or 'serial_per_file'."
        )
    return mode


def get_execution_mode_label(execution_mode):
    if execution_mode == EXECUTION_MODE_SERIAL_PER_FILE:
        return "按文件串行执行"
    return "当前批量执行"


def normalize_report_file_path(value, project_root, dependencies):
    if not isinstance(value, str) or not value:
        return ""

    normalized = value.replace("\\", "/")
    try:
        path = Path(value)
        if path.is_absolute():
            normalized = dependencies.resolve_path(path).relative_to(
                project_root
            ).as_posix()
    except (OSError, ValueError):
        pass

    return normalized.lstrip("./")


def update_script_result_status(
    script_results,
    relative_path,
    status,
):
    if not relative_path or relative_path not in script_results:
        return

    if status == "failed":
        script_results[relative_path] = "failed"
        return

    if script_results[relative_path] == "unknown":
        script_results[relative_path] = "succeeded"


def is_playwright_test_failed(test):
    if not isinstance(test, dict):
        return False

    status = test.get("status")
    if status in PLAYWRIGHT_AGGREGATE_TEST_STATUSES:
        return status == "unexpected"

    expected_status = test.get("expectedStatus")
    if status in PLAYWRIGHT_RESULT_STATUSES:
        if expected_status in PLAYWRIGHT_RESULT_STATUSES:
            return status != expected_status
        return status in PLAYWRIGHT_FAILURE_RESULT_STATUSES

    for result in test.get("results") or []:
        if not isinstance(result, dict):
            continue
        result_status = result.get("status")
        if result_status in PLAYWRIGHT_FAILURE_RESULT_STATUSES:
            return True

    return False


def _parse_report_statuses(
    report,
    normalized_paths,
    project_root,
    dependencies,
):
    filename_to_relative_path = {
        Path(relative_path).name: relative_path
        for relative_path in normalized_paths
    }
    normalized_statuses = {
        relative_path: "unknown"
        for relative_path in normalized_paths
    }

    def match_report_file(raw_file, current_file=""):
        node_file = normalize_report_file_path(
            raw_file,
            project_root,
            dependencies,
        )
        if not node_file:
            return current_file

        for relative_path in normalized_paths:
            if (
                node_file == relative_path
                or node_file.endswith(f"/{relative_path}")
            ):
                return relative_path

        return filename_to_relative_path.get(
            Path(node_file).name,
            current_file,
        )

    def normalize_script_results(node, current_file=""):
        if not isinstance(node, dict):
            return

        effective_file = match_report_file(
            node.get("file"),
            current_file,
        )

        specs = node.get("specs")
        if isinstance(specs, list):
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                spec_failed = spec.get("ok") is False
                for test in spec.get("tests") or []:
                    if is_playwright_test_failed(test):
                        spec_failed = True
                update_script_result_status(
                    normalized_statuses,
                    effective_file,
                    "failed" if spec_failed else "succeeded",
                )

        suites = node.get("suites")
        if isinstance(suites, list):
            for suite in suites:
                normalize_script_results(suite, effective_file)

    for suite in report.get("suites") or []:
        normalize_script_results(suite)

    return normalized_statuses


def parse_playwright_json_script_results(
    json_report_file,
    module_name,
    filenames,
    fallback_status,
    dependencies,
):
    project_root = dependencies.resolve_path(
        dependencies.get_project_root()
    )
    relative_paths = {
        dependencies.get_script_test_relative_path(
            module_name,
            filename,
        ).replace("\\", "/"): filename
        for filename in filenames
    }

    try:
        report = json.loads(
            dependencies.read_text(Path(json_report_file))
        )
    except (OSError, json.JSONDecodeError):
        return {
            filename: fallback_status
            for filename in filenames
        }

    normalized_statuses = _parse_report_statuses(
        report,
        relative_paths,
        project_root,
        dependencies,
    )
    resolved_count = sum(
        1
        for status in normalized_statuses.values()
        if status != "unknown"
    )
    unknown_status = (
        fallback_status
        if fallback_status == "succeeded" or resolved_count == 0
        else "unknown"
    )

    return {
        filename: (
            normalized_statuses.get(relative_path)
            if normalized_statuses.get(relative_path) != "unknown"
            else unknown_status
        )
        for relative_path, filename in relative_paths.items()
    }


def parse_playwright_json_relative_script_results(
    json_report_file,
    relative_path_keys,
    fallback_status,
    dependencies,
):
    project_root = dependencies.resolve_path(
        dependencies.get_project_root()
    )
    normalized_keys = {
        str(relative_path).replace("\\", "/").lstrip("./"): key
        for relative_path, key in relative_path_keys.items()
        if relative_path and key
    }

    try:
        report = json.loads(
            dependencies.read_text(Path(json_report_file))
        )
    except (OSError, json.JSONDecodeError):
        return {
            key: fallback_status
            for key in normalized_keys.values()
        }

    normalized_statuses = _parse_report_statuses(
        report,
        normalized_keys,
        project_root,
        dependencies,
    )
    resolved_count = sum(
        1
        for status in normalized_statuses.values()
        if status != "unknown"
    )
    unknown_status = (
        fallback_status
        if fallback_status == "succeeded" or resolved_count == 0
        else "unknown"
    )

    return {
        key: status if status != "unknown" else unknown_status
        for relative_path, key in normalized_keys.items()
        for status in [normalized_statuses.get(relative_path)]
    }


def format_script_result_summary(script_results):
    counts = {
        "succeeded": 0,
        "failed": 0,
        "unknown": 0,
    }
    for status in script_results.values():
        if status == "succeeded":
            counts["succeeded"] += 1
        elif status == "failed":
            counts["failed"] += 1
        else:
            counts["unknown"] += 1

    parts = []
    if counts["succeeded"]:
        parts.append(f"成功 {counts['succeeded']} 个")
    if counts["failed"]:
        parts.append(f"失败 {counts['failed']} 个")
    if counts["unknown"]:
        parts.append(f"未解析 {counts['unknown']} 个")
    return "，".join(parts)
