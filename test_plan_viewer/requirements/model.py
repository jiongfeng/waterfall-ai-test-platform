"""Pure requirement and candidate-module models."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REQUIREMENT_MODULE_STATUSES = frozenset(
    {
        "candidate",
        "confirmed",
        "generated",
        "deleted",
        "superseded",
    }
)


@dataclass(frozen=True)
class RequirementSerializationDependencies:
    """Content readers and renderers used by requirement serialization."""

    read_requirement_markdown: Callable[[dict], str]
    render_markdown: Callable[[str], str]


@dataclass(frozen=True)
class RequirementModuleModelDependencies:
    """Application policies used by candidate-module models."""

    validate_module_name: Callable[[str], str]
    get_chinese_plan_filename_from_name: Callable[..., str]
    normalize_confidence: Callable[[object], float]
    normalize_string_list: Callable[[object], list]
    normalize_json_object_or_array: Callable[[object, object], object]
    get_seed_script_relative_path: Callable[[], str]
    strip_legacy_coverage_notices: Callable[[str], str]
    append_database_baseline_write_operation_notice: Callable[
        [str],
        str,
    ]
    load_json_column: Callable[[object, object], object]
    list_requirement_module_plans: Callable[[int], list]
    get_test_asset_by_id: Callable[[int], dict]
    serialize_asset: Callable[[dict], dict]
    dedupe_chinese_artifact_naming_notice: Callable[[str], str]
    redact_value: Callable[[object], object] = lambda value: value


def extract_requirement_title(markdown_text, filename):
    """Use the first H1, then fall back to the filename stem."""

    import re

    for line in (markdown_text or "").splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()[:255]
    return Path(filename).stem[:255] or "未命名需求"


def serialize_requirement(
    row,
    include_content=False,
    *,
    dependencies=None,
):
    """Serialize a requirement row for the stable browser contract."""

    if not row:
        return None
    payload = {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "requirement_uid": row.get("requirement_uid"),
        "title": row.get("title") or "",
        "filename": row.get("filename") or "",
        "file_path": row.get("file_path") or "",
        "content_sha256": row.get("content_sha256") or "",
        "status": row.get("status") or "",
        "source_type": row.get("source_type") or "",
        "created_by": row.get("created_by") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "module_count": int(row.get("module_count") or 0),
    }
    if include_content:
        if not isinstance(
            dependencies,
            RequirementSerializationDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "RequirementSerializationDependencies instance "
                "when include_content is true"
            )
        markdown_text = dependencies.read_requirement_markdown(
            row
        )
        payload.update(
            {
                "markdown": markdown_text,
                "html": dependencies.render_markdown(
                    markdown_text
                ),
            }
        )
    return payload


def build_planner_prompt_from_requirement_module(
    module_data,
    requirement=None,
    *,
    dependencies,
):
    """Build the editable planner prompt for one candidate module."""

    module_name = module_data.get("module_name") or "<模块名>"
    test_points = dependencies.normalize_string_list(
        module_data.get("test_points")
    )
    inventory = dependencies.normalize_json_object_or_array(
        module_data.get("matched_inventory"),
        {},
    )
    inventory_lines = []
    if isinstance(inventory, dict):
        for label, key in (
            ("页面", "page_name"),
            ("路径", "url"),
            ("菜单入口", "menu_path"),
            ("推荐角色", "roles"),
            ("关键控件", "stable_selectors"),
        ):
            value = inventory.get(key)
            if isinstance(value, list):
                value = " / ".join(
                    str(item) for item in value
                )
            if value:
                inventory_lines.append(f"- {label}：{value}")
    if not inventory_lines:
        inventory_lines.append(
            "- 未匹配到确定页面，进入页面前需要先复核导航和权限。"
        )

    test_point_lines = [
        f"- {item}" for item in test_points
    ] or ["- 根据需求文档识别明确描述的可测试行为。"]
    baseline_note = (
        "是"
        if (
            module_data.get("baseline_required")
            or module_data.get("write_risk")
        )
        else "否"
    )
    requirement_title = (
        requirement.get("title")
        if requirement
        else "需求文档"
    )
    return (
        "@playwright-test-planner\n"
        f"请根据需求文档《{requirement_title}》和页面 inventory，"
        f"生成“{module_name}”模块测试计划。\n\n"
        "需求要点：\n"
        + "\n".join(test_point_lines)
        + "\n\n已知页面 inventory：\n"
        + "\n".join(inventory_lines)
        + "\n\n要求：\n"
        f"1. 使用 {dependencies.get_seed_script_relative_path()} "
        "作为入口。\n"
        "2. 实际登录系统并复核页面。\n"
        "3. 记录进入该界面的导航路径。\n"
        "4. 优先使用稳定定位器。\n"
        "5. 写库操作必须标记需要数据库基线；"
        f"当前候选基线要求：{baseline_note}。\n"
        "6. 需要执行写库场景时，说明基线恢复或测试数据准备。\n"
        "7. 测试覆盖范围和用例数量由生成计划时用户最终确认的"
        "语句决定。"
    )


def normalize_requirement_module_candidate(
    raw,
    requirement=None,
    *,
    dependencies,
):
    """Normalize an analyzed or user-edited candidate module."""

    if not isinstance(
        dependencies,
        RequirementModuleModelDependencies,
    ):
        raise TypeError(
            "dependencies must be a "
            "RequirementModuleModelDependencies instance"
        )
    if not isinstance(raw, dict):
        raise ValueError("模块候选必须是对象。")
    module_name = str(
        raw.get("module_name") or raw.get("name") or ""
    ).strip()
    if not module_name:
        raise ValueError("模块候选缺少 module_name。")
    dependencies.validate_module_name(module_name)
    plan_name = (
        str(raw.get("plan_name") or module_name).strip()
        or module_name
    )
    plan_name = Path(
        dependencies.get_chinese_plan_filename_from_name(
            plan_name,
            module_name,
            fallback_stem=module_name,
        )
    ).stem
    confidence = dependencies.normalize_confidence(
        raw.get("confidence")
    )
    write_risk = bool(raw.get("write_risk"))
    baseline_required = (
        bool(raw.get("baseline_required")) or write_risk
    )
    module_data = {
        "module_name": module_name,
        "plan_name": plan_name,
        "business_goal": str(
            raw.get("business_goal") or ""
        ).strip(),
        "requirement_refs": (
            dependencies.normalize_string_list(
                raw.get("requirement_refs")
            )
        ),
        "test_points": dependencies.normalize_string_list(
            raw.get("test_points")
        ),
        "matched_inventory": (
            dependencies.normalize_json_object_or_array(
                raw.get("matched_inventory"),
                {},
            )
        ),
        "open_questions": (
            dependencies.normalize_string_list(
                raw.get("open_questions")
            )
        ),
        "write_risk": write_risk,
        "baseline_required": baseline_required,
        "confidence": confidence,
    }
    planner_prompt = str(
        raw.get("planner_prompt") or ""
    ).strip()
    if not planner_prompt:
        planner_prompt = (
            build_planner_prompt_from_requirement_module(
                module_data,
                requirement=requirement,
                dependencies=dependencies,
            )
        )
    elif "@playwright-test-planner" not in planner_prompt:
        planner_prompt = (
            f"@playwright-test-planner\n{planner_prompt}"
        )
    planner_prompt = (
        dependencies.strip_legacy_coverage_notices(
            planner_prompt
        )
    )
    planner_prompt = (
        dependencies
        .append_database_baseline_write_operation_notice(
            planner_prompt
        )
    )
    module_data["planner_prompt"] = planner_prompt
    return dependencies.redact_value(module_data)


def serialize_requirement_module(row, *, dependencies):
    """Serialize a candidate module and its generated-plan links."""

    if not isinstance(
        dependencies,
        RequirementModuleModelDependencies,
    ):
        raise TypeError(
            "dependencies must be a "
            "RequirementModuleModelDependencies instance"
        )
    if not row:
        return None
    confidence = dependencies.normalize_confidence(
        row.get("confidence")
    )
    generated_plans = (
        dependencies.list_requirement_module_plans(
            row.get("id")
        )
        if row.get("id")
        else []
    )
    generated_plan = (
        generated_plans[0] if generated_plans else None
    )
    if row.get("generated_plan_asset_id"):
        try:
            asset = dependencies.get_test_asset_by_id(
                row.get("generated_plan_asset_id")
            )
        except Exception:
            asset = None
        if asset:
            legacy_generated_plan = {
                "asset": dependencies.serialize_asset(asset),
                "module_name": asset.get("module_name"),
                "plan_filename": Path(
                    asset.get("current_path") or ""
                ).name,
                "path": asset.get("current_path") or "",
            }
            if not generated_plan:
                generated_plan = legacy_generated_plan
                generated_plans = [legacy_generated_plan]
    return dependencies.redact_value({
        "id": row.get("id"),
        "requirement_id": row.get("requirement_id"),
        "module_uid": row.get("module_uid"),
        "module_name": row.get("module_name") or "",
        "plan_name": row.get("plan_name") or "",
        "status": row.get("status") or "",
        "confidence": confidence,
        "business_goal": row.get("business_goal") or "",
        "requirement_refs": dependencies.load_json_column(
            row.get("requirement_refs_json"),
            [],
        ),
        "test_points": dependencies.load_json_column(
            row.get("test_points_json"),
            [],
        ),
        "matched_inventory": dependencies.load_json_column(
            row.get("matched_inventory_json"),
            {},
        ),
        "open_questions": dependencies.load_json_column(
            row.get("open_questions_json"),
            [],
        ),
        "baseline_required": bool(
            row.get("baseline_required")
        ),
        "write_risk": bool(row.get("write_risk")),
        "planner_prompt": (
            dependencies.strip_legacy_coverage_notices(
                dependencies
                .dedupe_chinese_artifact_naming_notice(
                    row.get("planner_prompt") or ""
                )
            )
        ),
        "source_job_id": row.get("source_job_id") or "",
        "generated_plan_asset_id": row.get(
            "generated_plan_asset_id"
        ),
        "generated_plan": generated_plan,
        "generated_plans": generated_plans,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    })
