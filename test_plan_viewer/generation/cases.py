"""Case-index parsing, normalization, serialization, and splitting rules."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Callable

from test_plan_viewer.artifacts.naming import (
    get_case_plan_filename_from_title,
)

from .prompts import ABSOLUTE_PLAN_MAX_CASES


JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


@dataclass(frozen=True)
class CaseDependencies:
    """Filesystem and path capabilities supplied by the composition root."""

    get_specs_dir: Callable[[], Path]
    validate_module_name: Callable[[str], str]
    get_plan_file: Callable[[str, str], Path]
    plan_payload: Callable[[Path, str], dict]
    ensure_directory: Callable[[Path], None]
    file_exists: Callable[[Path], bool]
    read_text: Callable[[Path], str]
    write_text: Callable[[Path, str], None]


def normalize_case_filename(value, title, index=None):
    filename = str(value or "").strip()
    title = str(title or "").strip()
    if not filename and not title:
        raise ValueError("用例缺少 title 或 filename。")
    return get_case_plan_filename_from_title(
        filename,
        title,
        index=index,
    )


def extract_case_index(markdown_text):
    candidates = JSON_FENCE_PATTERN.findall(markdown_text or "")
    stripped = (markdown_text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            cases = data.get("cases")
            if isinstance(cases, list):
                return data

    raise ValueError("未找到包含 cases 数组的 JSON 代码块。")


def normalize_case_steps(value):
    if not isinstance(value, list):
        return []
    steps = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                steps.append({"text": text, "expect": []})
            continue
        if not isinstance(item, dict):
            continue
        text = str(
            item.get("text")
            or item.get("step")
            or item.get("action")
            or ""
        ).strip()
        if not text:
            continue
        expect = item.get("expect")
        if expect is None:
            expect = (
                item.get("expected")
                or item.get("expects")
                or item.get("expectations")
            )
        if isinstance(expect, str):
            expect_items = [expect.strip()] if expect.strip() else []
        elif isinstance(expect, list):
            expect_items = [
                str(expect_item).strip()
                for expect_item in expect
                if str(expect_item).strip()
            ]
        else:
            expect_items = []
        steps.append({"text": text, "expect": expect_items})
    return steps


def list_text_items(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    return []


def case_to_markdown(module_name, source_filename, case):
    title = str(case.get("title") or case.get("name") or "").strip()
    if not title:
        title = Path(
            normalize_case_filename(case.get("filename"), "")
        ).stem
    suite = str(case.get("suite") or module_name).strip()
    description = str(case.get("description") or "").strip()
    preconditions = list_text_items(case.get("preconditions"))
    steps = normalize_case_steps(case.get("steps"))

    lines = [
        f"# {title}",
        "",
        f"模块：{module_name}",
        f"来源：{source_filename}",
    ]
    if suite:
        lines.append(f"套件：{suite}")
    if description:
        lines.extend(["", "## 说明", "", description])
    if preconditions:
        lines.extend(["", "## 前置条件", ""])
        lines.extend(f"- {item}" for item in preconditions)
    lines.extend(["", "## Steps", ""])
    if steps:
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step['text']}")
            for expect in step["expect"]:
                lines.append(f"   - Expect: {expect}")
            lines.append("")
    else:
        lines.append("1. 待补充步骤")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def normalize_case_index_cases(data):
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict):
        cases = data.get("cases")
        if not isinstance(cases, list):
            cases = data.get("plans")
        if not isinstance(cases, list):
            cases = data.get("tests")
    else:
        cases = None
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 数组为空。")
    return cases


def split_case_index_cases(
    module_name,
    source_filename,
    cases,
    dependencies,
    overwrite=False,
    source_plan_file=None,
):
    if len(cases) > ABSOLUTE_PLAN_MAX_CASES:
        raise ValueError(
            f"测试计划包含 {len(cases)} 个用例，"
            f"超过平台绝对上限 {ABSOLUTE_PLAN_MAX_CASES} 个。"
        )
    created = []
    reused = []
    skipped = []
    conflicts = []
    module_dir = (
        dependencies.get_specs_dir()
        / dependencies.validate_module_name(module_name)
    )
    seen = set()
    planned_writes = []
    source_name = (
        source_plan_file.name
        if source_plan_file
        else source_filename
    )

    # Build and validate the complete write plan before touching any target.
    # A malformed later case must not leave earlier case files behind.
    source_payload = (
        dependencies.plan_payload(source_plan_file, module_name)
        if source_plan_file
        else {"filename": source_filename}
    )

    for case_index, raw_case in enumerate(cases, start=1):
        if not isinstance(raw_case, dict):
            continue
        title = str(
            raw_case.get("title")
            or raw_case.get("name")
            or ""
        ).strip()
        raw_filename = str(raw_case.get("filename") or "").strip()
        filename = normalize_case_filename(
            raw_filename,
            title,
            index=case_index,
        )
        if (
            raw_filename == source_name
            or raw_filename.startswith("_")
            or filename == source_name
            or filename.startswith("_")
            or filename in seen
        ):
            skipped.append(
                {
                    "filename": filename,
                    "reason": "索引文件、内部文件或重复文件名不会拆分。",
                }
            )
            continue
        seen.add(filename)
        target_file = dependencies.get_plan_file(
            module_name,
            filename,
        )
        markdown = case_to_markdown(
            module_name,
            source_filename,
            {**raw_case, "filename": filename},
        )
        payload = dependencies.plan_payload(target_file, module_name)
        if dependencies.file_exists(target_file) and not overwrite:
            if dependencies.read_text(target_file) == markdown:
                reused.append(payload)
            else:
                conflict = {
                    "filename": filename,
                    "reason": "文件已存在。",
                    "reason_code": "content_conflict",
                }
                conflicts.append(conflict)
                # Keep the legacy skipped collection populated for callers
                # that have not adopted the explicit conflicts field yet.
                skipped.append(conflict)
            continue
        planned_writes.append((target_file, markdown, payload))

    if not planned_writes and not reused and not skipped:
        raise ValueError("cases 数组中没有可拆分的有效用例。")

    # A content conflict makes the entire split unsafe. Returning the complete
    # plan lets the composition root report a precise failure while ensuring
    # that no non-conflicting case is written as a partial result.
    if conflicts:
        return {
            "created": [],
            "reused": reused,
            "skipped": skipped,
            "conflicts": conflicts,
            "reason_code": "case_content_conflict",
            "source": source_payload,
        }

    dependencies.ensure_directory(module_dir)
    for target_file, markdown, payload in planned_writes:
        dependencies.write_text(target_file, markdown)
        created.append(payload)

    return {
        "created": created,
        "reused": reused,
        "skipped": skipped,
        "conflicts": [],
        "source": source_payload,
    }


def split_case_index_plan(
    module_name,
    source_plan_file,
    dependencies,
    overwrite=False,
):
    markdown_text = dependencies.read_text(source_plan_file)
    data = extract_case_index(markdown_text)
    cases = normalize_case_index_cases(data)
    return split_case_index_cases(
        module_name,
        source_plan_file.name,
        cases,
        dependencies,
        overwrite=overwrite,
        source_plan_file=source_plan_file,
    )
