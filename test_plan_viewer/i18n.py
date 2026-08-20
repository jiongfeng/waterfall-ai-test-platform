"""Project-language helpers for first-party API messages.

This deliberately translates only messages emitted by Waterfall itself.  Error
text received from a browser, Playwright, OpenCode, a target application, or a
user-provided document must remain verbatim so it can be diagnosed safely.
"""

from __future__ import annotations


_EXACT_ERRORS = {
    "请先登录。": "Please sign in first.",
    "请求来源校验失败，请刷新页面后重试。": "Request-origin verification failed. Refresh the page and try again.",
    "需求不存在。": "Requirement not found.",
    "候选模块不存在。": "Candidate module not found.",
    "测试计划不存在。": "Test plan not found.",
    "测试集不存在。": "Test suite not found.",
    "测试集没有可执行脚本。": "The test suite has no executable scripts.",
    "Agent 任务不存在。": "Agent task not found.",
    "Agent 失败记录不存在。": "Agent failure record not found.",
    "脚本项不存在。": "Script item not found.",
    "脚本准备任务已取消。": "The script-preparation task was cancelled.",
    "单项重试记录不存在。": "Item retry record not found.",
    "当前项目已有 Agent 任务正在运行。": "An Agent task is already running for this project.",
    "当前项目有 Agent 主任务正在运行。": "An Agent primary task is running for this project.",
    "当前项目有脚本正在重试并验证。": "Scripts are currently being retried and validated for this project.",
    "Agent 主任务正在运行，不能同时执行单项重试。": "The Agent primary task is running, so an item retry cannot start.",
    "该 Agent 任务正在运行，不能重复恢复。": "This Agent task is running and cannot be resumed again.",
    "只有失败或已取消的 Agent 任务可以恢复。": "Only failed or cancelled Agent tasks can be resumed.",
    "只有脚本生成阶段的失败记录可以重试并验证。": "Only failure records from script generation can be retried and validated.",
    "该失败记录已被后续结果替代，请刷新页面后选择当前失败项。": "This failure record was superseded by a later result. Refresh and select the current item.",
    "请上传需求 Markdown，或提供 requirement_uid。": "Upload a requirement Markdown file or provide requirement_uid.",
    "候选模块缺少 planner prompt。": "The candidate module has no planner prompt.",
    "请求体必须是 JSON 对象。": "The request body must be a JSON object.",
    "items 必须是非空列表。": "items must be a non-empty list.",
    "测试集名字不能为空。": "A test-suite name is required.",
    "测试集名字不能重复。": "A test-suite name must be unique.",
    "准备脚本解析结果无效。": "The setup-script parsing result is invalid.",
    "准备脚本执行目标无效。": "The setup-script execution target is invalid.",
    "准备脚本执行目标过长。": "The setup-script execution target is too long.",
    "项目名称不能为空。": "A project name is required.",
    "未找到默认项目。": "Default project not found.",
    "测试资产路径必须位于 Playwright 项目目录内。": "The test-asset path must be inside the Playwright project directory.",
    "不支持的 Agent 步骤。": "Unsupported Agent step.",
    "不支持的 Agent 项目状态。": "Unsupported Agent item status.",
    "不支持的单项重试状态。": "Unsupported item-retry status.",
    "不支持的单项重试阶段。": "Unsupported item-retry stage.",
    "不支持的单项重试合并状态。": "Unsupported item-retry merge status.",
    "该阶段不支持单项重试结果合并。": "This stage does not support item-retry result merging.",
    "该 Agent 阶段不支持历史失败诊断包。": "This Agent stage does not support historical failure diagnostic bundles.",
    "Agent 阶段记录不存在。": "Agent stage record not found.",
    "该阶段没有可诊断的历史失败记录。": "This stage has no diagnosable historical failure records.",
    "未找到对应的历史失败记录。": "Corresponding historical failure record not found.",
    "历史失败记录标识不唯一，无法安全生成诊断包。": "The historical failure record identifier is not unique, so a diagnostic bundle cannot be generated safely.",
    "用户请求取消。": "Cancelled by user request.",
}

_PREFIX_ERRORS = (
    ("重置密码失败：", "Could not reset password: "),
    ("创建项目失败：", "Could not create project: "),
    ("创建 Agent 任务失败：", "Could not create Agent task: "),
    ("读取 Agent 任务失败：", "Could not load Agent task: "),
    ("生成诊断包失败：", "Could not create diagnostic bundle: "),
    ("生成历史失败诊断包失败：", "Could not create historical failure diagnostic bundle: "),
    ("读取 Agent 项目记录失败：", "Could not load Agent item record: "),
    ("创建历史 Agent 失败记录失败：", "Could not create historical Agent failure record: "),
    ("启动单项重试失败：", "Could not start item retry: "),
    ("读取单项重试记录失败：", "Could not load item retry record: "),
    ("读取项目单项重试记录失败：", "Could not load project item retries: "),
    ("取消单项重试失败：", "Could not cancel item retry: "),
    ("确认单项重试结果失败：", "Could not confirm item retry result: "),
    ("读取 Agent 事件失败：", "Could not load Agent events: "),
    ("取消 Agent 任务失败：", "Could not cancel Agent task: "),
    ("恢复 Agent 任务失败：", "Could not resume Agent task: "),
    ("创建 Seed 生成任务失败：", "Could not create Seed-generation task: "),
    ("生成 Seed 脚本失败：", "Could not generate Seed script: "),
    ("创建 Playwright Seed 测试配置失败：", "Could not create Playwright Seed test configuration: "),
    ("启动需求解析失败：", "Could not start requirement analysis: "),
    ("生成测试计划失败：", "Could not generate test plan: "),
    ("读取测试集执行记录失败：", "Could not load test-suite execution history: "),
    ("保存测试计划版本失败：", "Could not save test-plan revision: "),
    ("读取测试计划版本失败：", "Could not load test-plan revision: "),
    ("删除测试计划失败：", "Could not delete test plan: "),
    ("拆分测试计划失败：", "Could not split test plan: "),
    ("创建测试计划生成任务失败：", "Could not create test-plan generation task: "),
    ("创建测试脚本生成任务失败：", "Could not create test-script generation task: "),
    ("创建脚本修复任务失败：", "Could not create script-repair task: "),
    ("取消 OpenCode 任务失败：", "Could not cancel OpenCode task: "),
    ("创建 Playwright 视频配置失败：", "Could not create Playwright video configuration: "),
    ("创建 Playwright 批量执行配置失败：", "Could not create Playwright bulk-execution configuration: "),
    ("创建 Playwright 测试集执行配置失败：", "Could not create Playwright test-suite execution configuration: "),
    ("保存任务到 MySQL 失败：", "Could not save task to MySQL: "),
    ("读取 MySQL 任务失败：", "Could not load MySQL task: "),
    ("读取任务失败：", "Could not load task: "),
    ("读取任务日志失败：", "Could not load task log: "),
    ("读取版本历史失败：", "Could not load revision history: "),
    ("读取版本内容失败：", "Could not load revision content: "),
    ("读取版本差异失败：", "Could not load revision diff: "),
    ("恢复版本失败：", "Could not restore revision: "),
    ("读取测试脚本版本失败：", "Could not load test-script revision: "),
    ("删除测试脚本失败：", "Could not delete test script: "),
    ("保存测试脚本版本失败：", "Could not save test-script revision: "),
    ("项目标识已存在：", "Project key already exists: "),
    ("项目不存在或已禁用：", "Project not found or disabled: "),
    ("测试计划已存在：", "Test plan already exists: "),
    ("测试计划不存在：", "Test plan not found: "),
    ("测试脚本不存在：", "Test script not found: "),
)


def localize_platform_error(message: object, language: str) -> object:
    """Return English only for recognized platform-owned errors.

    The caller may pass non-string JSON values; those are intentionally left
    untouched.  Prefix matching recursively localizes a nested platform error
    while leaving unknown, third-party diagnostic detail unchanged.
    """

    if language != "en" or not isinstance(message, str):
        return message
    text = message.strip()
    if text in _EXACT_ERRORS:
        return _EXACT_ERRORS[text]
    for source, target in _PREFIX_ERRORS:
        if text.startswith(source):
            detail = text[len(source) :]
            localized_detail = localize_platform_error(detail, language)
            return f"{target}{localized_detail}"
    return message
