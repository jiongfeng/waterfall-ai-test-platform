"""First-party Agent copy selected by the captured project language."""

from __future__ import annotations

import json
import re

from test_plan_viewer.configuration import normalize_project_language
from test_plan_viewer.i18n import localize_platform_error


STEP_NAMES = {
    "en": {
        "upload_requirement": "Requirements",
        "analyze_requirement": "Requirement analysis",
        "review_modules": "Module review",
        "generate_plans": "Plan generation",
        "prepare_scripts": "Script preparation",
        "create_suite": "Test suites",
        "run_suite": "Run",
    },
    "zh-CN": {
        "upload_requirement": "需求",
        "analyze_requirement": "需求解析",
        "review_modules": "模块审查",
        "generate_plans": "计划生成",
        "prepare_scripts": "脚本准备",
        "create_suite": "测试集",
        "run_suite": "执行",
    },
}

MESSAGES = {
    "task_created": ("Agent task created.", "Agent 任务已创建。"),
    "task_cancelled": ("Agent task cancelled.", "Agent 任务已取消。"),
    "task_resumed": ("Agent task resumed from step: {step}.", "Agent 任务已从步骤恢复：{step}。"),
    "step_started": ("{step} started.", "{step}开始。"),
    "step_completed": ("{step} completed.", "{step}完成。"),
    "step_failed": ("{step} failed: {error}", "{step}失败：{error}"),
    "analysis_created": (
        "Requirement-analysis task created; calling OpenCode.",
        "需求解析任务已创建，正在调用 OpenCode。",
    ),
    "analysis_completed": (
        "Requirement analysis completed; generated {count} candidate modules.",
        "需求解析完成，生成候选模块 {count} 个。",
    ),
    "analysis_failed": ("Requirement analysis failed: {error}", "需求解析失败：{error}"),
    "calling_agent": ("Calling {agent}.", "正在调用 {agent}。"),
    "model_structured": ("The model returned a structured result.", "模型已返回结构化结果。"),
    "model_failed": ("Model call failed: {error}", "模型调用失败：{error}"),
    "reviewer_invalid": (
        "Reviewer JSON must be one decision or contain a decisions array.",
        "reviewer JSON 必须是单条 decision 或包含 decisions 数组。",
    ),
    "review_modules": ("Review the requirement module candidates.", "审查需求候选模块。"),
    "reviewer_missing": (
        "The reviewer did not return this module; keeping it by default.",
        "reviewer 未返回该模块，默认保留。",
    ),
    "module_deleted": ("Deleted module: {module}. {reason}", "删除模块：{module}。{reason}"),
    "module_updated": ("Updated module: {module}. {reason}", "修改模块：{module}。{reason}"),
    "module_kept": ("Kept module: {module}. {reason}", "保留模块：{module}。{reason}"),
    "plan_missing": ("Plan file does not exist or is empty.", "计划文件不存在或为空。"),
    "plan_too_many": (
        "The test plan contains {count} cases, exceeding the platform limit of {limit}.",
        "测试计划包含 {count} 个用例，超过平台绝对上限 {limit} 个。",
    ),
    "case_not_object": ("Case {index} is not an object.", "第 {index} 个用例不是对象。"),
    "case_filename_invalid": (
        "Case filename is unsafe or duplicated: {filename}",
        "用例文件名不安全或重复：{filename}",
    ),
    "split_fallback": (
        "The indexed plan could not be split directly; asking the model to extract cases from Markdown: {path}",
        "索引计划无法直接拆分，正在调用模型读取普通 Markdown 并抽取 cases：{path}",
    ),
    "splitting_markdown": (
        "Splitting Markdown into single-case plans: {filename}",
        "正在将普通 Markdown 拆成单用例计划：{filename}",
    ),
    "split_json_returned": (
        "The model returned split JSON; generating single-case plans. Split job: {job_id}",
        "模型拆分 JSON 已返回，开始生成单用例计划。拆分 job：{job_id}",
    ),
    "split_completed": (
        "Markdown split completed; extracted {count} cases.",
        "普通 Markdown 拆分完成，抽取 {count} 个用例。",
    ),
    "split_failed": ("Markdown split failed: {error}", "普通 Markdown 拆分失败：{error}"),
    "split_retry": (
        "Indexed JSON split failed; trying model-assisted Markdown extraction: {error}",
        "索引 JSON 拆分失败，尝试模型拆分普通 Markdown：{error}",
    ),
    "generate_plan_title": ("Generate test plan: {module}", "生成测试计划：{module}"),
    "task_success_file": (
        "Task succeeded; file generated: {target}",
        "任务成功，文件已生成：{target}",
    ),
    "video_found": ("Execution video found: {path}", "已找到执行视频：{path}"),
    "video_missing": ("No execution video was found.", "未找到本次执行视频。"),
    "task_failed": ("Task failed: {error}", "任务失败：{error}"),
    "event_stream_read_failed": (
        "Failed to read the OpenCode event stream.",
        "OpenCode 事件流读取失败。",
    ),
    "opencode_waiting": (
        "OpenCode is still running; waiting for completion.",
        "OpenCode 仍在执行，正在等待任务完成。",
    ),
    "opencode_wait_timeout": (
        "Timed out waiting for OpenCode to finish ({duration}).",
        "等待 OpenCode 任务完成超时（已等待 {duration}）。",
    ),
    "task_cancelled_generic": ("Task cancelled.", "任务已取消。"),
    "opencode_fallback_timeout": (
        "OpenCode fallback mode timed out ({duration}).",
        "OpenCode 等待模式超时（已等待 {duration}）。",
    ),
    "truncated": ("...[truncated {count} characters]", "...[已截断 {count} 个字符]"),
    "file_tag": ("[file] {label}", "[文件] {label}"),
    "task_created_target": ("Task created; target: {target}", "任务已创建，目标位置：{target}"),
    "session_missing": (
        "OpenCode did not return a session id: {session}",
        "OpenCode 未返回 session id: {session}",
    ),
    "session_created": (
        "OpenCode session created: {session_id}",
        "OpenCode session 已创建：{session_id}",
    ),
    "event_stream_fallback": (
        "OpenCode event stream unavailable; using fallback mode: {error}",
        "OpenCode 事件流不可用，改用等待模式：{error}",
    ),
    "target_missing_after_return": (
        "OpenCode returned without generating the target content: {target}",
        "OpenCode 已返回，但未生成目标内容：{target}",
    ),
    "submitted": (
        "Submitted to OpenCode; receiving live output.",
        "已提交到 OpenCode，正在接收实时输出。",
    ),
    "realtime_timeout": (
        "OpenCode live output timed out ({duration}).",
        "OpenCode 实时输出超时（已等待 {duration}）。",
    ),
    "source_ready": (
        "Source plan generated and validated; splitting single-case plans.",
        "源计划已生成并通过校验，正在拆分单用例计划。",
    ),
    "target_detected": (
        "Target content detected; stopping the remaining OpenCode event wait.",
        "已检测到目标内容生成，停止等待 OpenCode 后续事件。",
    ),
    "patch_generated": ("Patch generated: {files}", "生成补丁：{files}"),
    "file_edited": ("File edited: {path}", "文件已编辑：{path}"),
    "file_changed": ("File change detected: {path}", "检测到文件变更：{path}"),
    "opencode_retrying": ("OpenCode is retrying{detail}", "OpenCode 正在重试{detail}"),
    "opencode_execution_failed": (
        "OpenCode execution failed: {error}",
        "OpenCode 执行失败：{error}",
    ),
    "event_stream_ended": (
        "OpenCode event stream ended; polling task status.",
        "OpenCode 事件流已结束，改用状态检查等待任务完成。",
    ),
    "target_missing_after_end": (
        "OpenCode finished without generating the target content: {target}",
        "OpenCode 已结束，但未生成目标内容：{target}",
    ),
    "stream_closed": (
        "Streaming connection closed; task cancelled.",
        "流式连接已关闭，任务已取消。",
    ),
    "agent_plan_title": ("Agent generates test plan: {module}", "Agent 生成测试计划：{module}"),
    "agent_plan_success": ("Agent generated test plan: {target}", "Agent 已生成测试计划：{target}"),
    "split_plan_failed": ("Plan split failed: {error}", "拆分计划失败：{error}"),
    "generating_plan": ("Generating plan: {module}.", "正在生成计划：{module}。"),
    "source_plan_splitting": (
        "Source plan generated; splitting: {target}.",
        "源计划已生成，正在拆分：{target}。",
    ),
    "multiple_source_deleted": (
        "Deleted intermediate multi-case Markdown: {filename}",
        "已删除多计划中间 Markdown：{filename}",
    ),
    "plan_queue": (
        "Plan-generation queue ready with {count} modules.",
        "计划生成队列已准备，共 {count} 个模块。",
    ),
    "plan_resume_queue": (
        "Incremental plan queue ready; kept {plans} plans and will retry {modules} failed modules.",
        "计划增量恢复队列已准备，保留 {plans} 个已有计划，仅重试 {modules} 个失败模块。",
    ),
    "plan_completed": (
        "Plan generation completed for {module}; generated {count} plans.",
        "计划生成完成：{module}，生成 {count} 个计划。",
    ),
    "module_plan_failed": (
        "Module plan generation failed for {module}: {error}",
        "模块计划生成失败：{module}，{error}",
    ),
    "plans_still_failed": (
        "{count} module plan generations still failed: {error}",
        "仍有 {count} 个模块计划生成失败：{error}",
    ),
    "script_generation_title": (
        "Agent generates test script: {target}",
        "Agent 生成测试脚本：{target}",
    ),
    "script_generation_success": (
        "Agent generated test script: {target}",
        "Agent 已生成测试脚本：{target}",
    ),
    "script_repair_title": ("Agent repairs test script: {target}", "Agent 修复测试脚本：{target}"),
    "script_repair_success": (
        "Agent repaired test script: {target}",
        "Agent 已完成脚本修复：{target}",
    ),
    "seed_generation_title": ("Generate Seed login script", "生成 Seed 登录脚本"),
    "seed_generation_success": ("Seed script generated: {target}", "Seed 脚本已生成：{target}"),
    "requirement_plan_title": (
        "Generate test plan from requirement: {module}",
        "从需求生成测试计划：{module}",
    ),
    "requirement_plan_success": (
        "Candidate-module test plan generated: {target}",
        "候选模块测试计划已生成：{target}",
    ),
    "manual_script_generation_title": ("Generate test script: {target}", "生成测试脚本：{target}"),
    "manual_script_generation_success": (
        "Test script generated: {target}",
        "任务成功，测试脚本已提交到：{target}",
    ),
    "manual_script_repair_title": ("Repair test script: {target}", "修复测试脚本：{target}"),
    "manual_script_repair_success": (
        "Test script repaired: {target}",
        "任务结束，测试脚本已修复：{target}",
    ),
    "script_generation_queue": (
        "Script-generation queue ready with {count} plans.",
        "脚本生成队列已准备，共 {count} 个计划。",
    ),
    "script_repair_queue": (
        "Script-repair queue ready with {count} scripts.",
        "脚本修复队列已准备，共 {count} 个脚本。",
    ),
    "script_preparation_updated": ("Script-preparation status updated.", "脚本准备状态已更新。"),
    "suite_created": (
        "Test suite created: {name}; {count} scripts.",
        "已创建测试集：{name}，脚本 {count} 条。",
    ),
    "failure_analysis_instruction": (
        "Analyze the script-preparation failure and recommend exactly one action—regenerate or repair—with a supplemental prompt.",
        "分析脚本准备失败，并在重新生成或重新修复之间给出唯一建议与补充 Prompt。",
    ),
    "regeneration_supplement": (
        "Additional requirements for this regeneration",
        "本次重新生成补充要求",
    ),
    "repair_supplement": ("Additional requirements for this repair", "本次重新修复补充要求"),
    "status": ("Status: {status}", "状态：{status}"),
    "task_status": ("Task {status}", "任务{status}"),
    "stream_persist_failed": (
        "Failed to persist the Agent output batch.",
        "Agent 输出批次持久化失败。",
    ),
    "stream_flush_failed": (
        "Agent stream terminated: {business}; failed to persist remaining output: {flush}",
        "Agent 流终止：{business}；剩余输出持久化失败：{flush}",
    ),
    "tool_input": ("Tool input: {title}", "工具输入：{title}"),
    "tool_metadata": ("Tool metadata: {title}", "工具元数据：{title}"),
    "tool_attachments": ("Tool attachments: {title}", "工具附件：{title}"),
    "tool_completed": ("Tool completed: {title}", "工具完成：{title}"),
    "tool_failed": ("Tool failed: {title}{detail}", "工具失败：{title}{detail}"),
    "tool_running": ("Running tool: {title}", "正在执行工具：{title}"),
    "tool_pending": ("Tool waiting to run: {title}", "工具等待执行：{title}"),
    "tool_output": ("Tool output: {title}", "工具输出：{title}"),
    "tool_error": ("Tool error: {title}", "工具错误：{title}"),
    "tool_input_started": ("Tool input started: {title}", "工具输入开始：{title}"),
    "tool_input_delta": ("Tool input delta: {title}", "工具输入增量：{title}"),
    "tool_input_completed": ("Tool input completed: {title}", "工具输入完成：{title}"),
    "tool_progress": ("Tool progress: {title}", "工具进度：{title}"),
    "tool_progress_data": ("Tool progress data: {title}", "工具进度数据：{title}"),
    "tool_structured": ("Structured tool result: {title}", "工具结构化结果：{title}"),
    "tool_raw_result": ("Raw tool result: {title}", "工具原始结果：{title}"),
    "tool_failed_result": ("Failed tool result: {title}", "工具失败结果：{title}"),
}

EVENT_EXACT_EN = {
    "需求已准备完成。": "Requirement prepared.",
    "Agent 全流程执行完成。": "Agent workflow completed.",
    "Agent 任务已取消。": "Agent task cancelled.",
    "用户请求取消 Agent 任务。": "The user requested cancellation of the Agent task.",
    "用户已取消脚本准备。": "The user cancelled script preparation.",
    "脚本准备状态已更新。": "Script-preparation status updated.",
}

EVENT_PREFIX_EN = (
    ("开始从步骤继续执行：", "Resuming from step: "),
    ("正在生成脚本：", "Generating script: "),
    ("脚本生成完成：", "Script generation completed: "),
    ("脚本生成失败：", "Script generation failed: "),
    ("正在执行脚本：", "Running script: "),
    ("脚本执行通过：", "Script execution passed: "),
    ("脚本执行失败，进入修复：", "Script execution failed; starting repair: "),
    ("脚本执行异常，进入修复：", "Script execution error; starting repair: "),
    ("正在修复脚本：", "Repairing script: "),
    ("脚本修复完成：", "Script repair completed: "),
    ("脚本修复失败：", "Script repair failed: "),
    ("计划已存在，跳过生成：", "Plan already exists; skipped generation: "),
    (
        "恢复任务发现不完整的源计划，已保留为诊断产物：",
        "Resume found an incomplete source plan; archived for diagnostics: ",
    ),
    ("恢复任务接管已验证的源计划：", "Resume reused the validated source plan: "),
    ("源计划已恢复，正在拆分：", "Source plan restored; splitting: "),
    ("Agent 任务失败：", "Agent task failed: "),
)


def select(language: str, english: str, chinese: str) -> str:
    return str(english if normalize_project_language(language) == "en" else chinese)


def localize_known_error(language: str, value: object) -> object:
    if normalize_project_language(language) != "en" or value is None:
        return value
    text = str(value)
    localized = localize_platform_error(text, "en")
    if localized != text:
        return localized
    for separator in ("，", ", "):
        prefix, found, detail = text.rpartition(separator)
        if not found:
            continue
        localized_detail = localize_platform_error(detail, "en")
        if localized_detail != detail:
            return f"{prefix}{found}{localized_detail}"
    return text


def message(language: str, key: str, **values: object) -> str:
    english, chinese = MESSAGES[key]
    safe_values = dict(values)
    if "error" in safe_values:
        safe_values["error"] = localize_known_error(language, safe_values["error"])
    return select(language, english, chinese).format(**safe_values)


def step_name(language: str, step_key: str) -> str:
    language = normalize_project_language(language)
    return STEP_NAMES[language].get(step_key, step_key)


def event_message(language: str, value: object) -> str:
    text = str(value or "")
    if normalize_project_language(language) != "en":
        return text
    if text in EVENT_EXACT_EN:
        return EVENT_EXACT_EN[text]
    queue_match = re.fullmatch(r"脚本生成队列已准备，共 (\d+) 个计划。", text)
    if queue_match:
        return message("en", "script_generation_queue", count=queue_match.group(1))
    repair_match = re.fullmatch(r"脚本修复队列已准备，共 (\d+) 个脚本。", text)
    if repair_match:
        return message("en", "script_repair_queue", count=repair_match.group(1))
    suite_match = re.fullmatch(r"已创建测试集：(.+)，脚本 (\d+) 条。", text)
    if suite_match:
        return message("en", "suite_created", name=suite_match.group(1), count=suite_match.group(2))
    for source, target in EVENT_PREFIX_EN:
        if text.startswith(source):
            return f"{target}{localize_known_error(language, text[len(source) :])}"
    return text


def append_supplemental_prompt(
    language: str, prompt: object, supplemental: object, kind: str
) -> str:
    text = str(prompt or "").strip()
    supplement = str(supplemental or "").strip()
    if not supplement:
        return text
    key = "regeneration_supplement" if kind == "generation" else "repair_supplement"
    return f"{text}\n\n{message(language, key)}:\n{supplement}"


def plan_conflict_error(language: str, conflicts: list[dict]) -> str:
    names = ", ".join(
        str(item.get("filename") or select(language, "unknown file", "未知文件"))
        for item in conflicts
    )
    if all(item.get("reason_code") == "content_conflict" for item in conflicts):
        return select(
            language,
            f"Plan split detected existing-file content conflicts; no plan files were written, registered, or deleted: {names}",
            f"多计划拆分检测到已有文件内容冲突；未写入、登记或删除任何计划文件：{names}",
        )
    details = "; ".join(
        f"{item.get('filename') or names}: {item.get('reason') or item.get('reason_code') or ''}"
        for item in conflicts
    )
    return select(
        language,
        f"Plan split detected conflicts; no plan files were written, registered, or deleted: {details}",
        f"多计划拆分检测到冲突；未写入、登记或删除任何计划文件：{details}",
    )


def splitter_prompt(language: str) -> str:
    example = select(language, "Example business case", "中文单用例标题")
    naming = select(
        language,
        "Every title and filename must use a clear English business name.",
        "Every title and filename must use a Chinese business name. Filename stems must contain Chinese characters and must not contain English letters.",
    )
    shape = {
        "cases": [
            {
                "title": example,
                "filename": f"{example}.md",
                "suite": "module or suite name",
                "description": "optional short description",
                "preconditions": ["optional precondition"],
                "steps": [{"text": "action", "expect": ["expected result"]}],
            }
        ]
    }
    return (
        "You are a read-only test-plan splitter for the Waterfall AI test automation platform.\n\n"
        "You convert an already generated Markdown test plan into structured JSON.\n"
        "Return only valid JSON. Do not wrap JSON in Markdown fences.\n"
        "Do not create, edit, delete, move files, run commands, or use browser tools.\n\n"
        f"The only accepted top-level shape is:\n{json.dumps(shape, ensure_ascii=False, indent=2)}\n\n"
        f"Each case must represent exactly one test case.\n{naming}\n"
    )
