"""Framework-independent prompt composition for plan and script generation.

The prompt text is domain behavior. Runtime configuration and project path
resolution are supplied by the application composition root through
``PromptDependencies``.
"""

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Mapping

from test_plan_viewer.configuration import (
    DEFAULT_PROJECT_LANGUAGE,
    normalize_project_language,
)


ABSOLUTE_PLAN_MAX_CASES = 25
MODULE_PLAN_MAX_CASES = ABSOLUTE_PLAN_MAX_CASES

CHINESE_ARTIFACT_NAMING_NOTICE_KEY = "命名强约束："
CHINESE_ARTIFACT_NAMING_NOTICE = (
    "命名强约束：新生成的测试计划、单用例计划 cases[].filename、cases[].title 和测试脚本文件名必须使用中文业务名称；"
    "文件名主体必须包含中文且不能包含英文字母，不要使用英文、拼音、login、add、user、case 等技术命名。"
)
ENGLISH_ARTIFACT_NAMING_NOTICE_KEY = "Naming preference:"
ENGLISH_ARTIFACT_NAMING_NOTICE = (
    "Naming preference: generate new test-plan titles, case titles, and test-script file names "
    "with clear English business names. Keep file extensions and paths valid for the workspace."
)
DATABASE_BASELINE_WRITE_OPERATION_NOTICE = (
    "当前项目保存了旧数据库恢复配置；测试执行只会运行已绑定的准备脚本。若用例包含新增、修改、删除、审批、"
    "状态流转等写操作，请确保准备脚本能够恢复数据库基线。"
)
DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY = "当前项目保存了旧数据库恢复配置"
ENGLISH_DATABASE_BASELINE_WRITE_OPERATION_NOTICE = (
    "This project has a legacy database restore configuration. Test execution only runs bound setup scripts. "
    "For write operations such as create, update, delete, approval, or state transitions, make sure the setup script can restore the database baseline."
)
ENGLISH_DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY = "This project has a legacy database restore configuration"


@dataclass(frozen=True)
class PromptDependencies:
    """Runtime capabilities used by prompt builders.

    Keeping these callbacks explicit prevents domain code from reaching into
    Flask request state or process-wide application configuration.
    """

    get_database_baseline_config: Callable[[], Mapping[str, Any]]
    get_workspace_relative_path: Callable[[Any], str]
    parse_target_system_config: Callable[[Any], Mapping[str, Any]]
    build_target_login_url: Callable[[Mapping[str, Any]], str]
    get_seed_script_relative_path: Callable[[], str]
    get_script_test_relative_path: Callable[[str, str], str]
    get_project_language: Callable[[], str] = lambda: DEFAULT_PROJECT_LANGUAGE


def get_prompt_language(dependencies):
    return normalize_project_language(dependencies.get_project_language())


def strip_legacy_coverage_notices(prompt):
    """Remove only the two retired, exact-match coverage notices."""

    text = str(prompt or "")
    legacy_fragments = (
        "计划范围限制：只关注正向案例中的主要流程，优先覆盖用户最常用、业务价值最高的成功路径；不要主动扩展异常、"
        f"边界、兼容性、权限绕过或低频分支场景。模块计划最多包含 {10} 个测试用例，不足时不要为了凑数补充重复或次要用例。",
        "只生成正向案例中的主要流程，默认 3-5 条，最多 10 条可转脚本的测试用例。",
    )
    for fragment in legacy_fragments:
        text = text.replace(fragment, "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def dedupe_prompt_notice(prompt, notice):
    prompt_text = str(prompt or "").rstrip()
    if not notice:
        return prompt_text
    parts = prompt_text.split(notice)
    if len(parts) <= 2:
        return prompt_text
    prompt_text = f"{parts[0]}{notice}{''.join(parts[1:])}"
    return re.sub(r"\n{3,}", "\n\n", prompt_text).rstrip()


def append_prompt_notice_once(prompt, notice, key):
    prompt_text = dedupe_prompt_notice(prompt, notice)
    if not notice or key in prompt_text:
        return prompt_text
    if not prompt_text:
        return notice
    return f"{prompt_text}\n{notice}"


def get_database_baseline_write_operation_notice(dependencies):
    baseline_config = dependencies.get_database_baseline_config()
    if not baseline_config.get("enabled"):
        return ""
    if get_prompt_language(dependencies) == "en":
        return ENGLISH_DATABASE_BASELINE_WRITE_OPERATION_NOTICE
    return DATABASE_BASELINE_WRITE_OPERATION_NOTICE


def append_database_baseline_write_operation_notice(prompt, dependencies):
    notice = get_database_baseline_write_operation_notice(dependencies)
    key = (
        ENGLISH_DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY
        if get_prompt_language(dependencies) == "en"
        else DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY
    )
    return append_prompt_notice_once(
        prompt,
        notice,
        key,
    )


def append_chinese_artifact_naming_notice(prompt):
    return append_prompt_notice_once(
        prompt,
        CHINESE_ARTIFACT_NAMING_NOTICE,
        CHINESE_ARTIFACT_NAMING_NOTICE_KEY,
    )


def append_artifact_naming_notice(prompt, dependencies):
    if get_prompt_language(dependencies) == "en":
        return append_prompt_notice_once(
            prompt,
            ENGLISH_ARTIFACT_NAMING_NOTICE,
            ENGLISH_ARTIFACT_NAMING_NOTICE_KEY,
        )
    return append_chinese_artifact_naming_notice(prompt)


def dedupe_chinese_artifact_naming_notice(prompt):
    return dedupe_prompt_notice(prompt, CHINESE_ARTIFACT_NAMING_NOTICE)


def build_generation_prompt(prompt, target_path, dependencies):
    prompt = strip_legacy_coverage_notices(prompt)
    prompt = append_database_baseline_write_operation_notice(
        prompt,
        dependencies,
    )
    prompt = append_artifact_naming_notice(prompt, dependencies)
    relative_target_path = dependencies.get_workspace_relative_path(
        target_path
    )
    if get_prompt_language(dependencies) == "en":
        return (
            f"{prompt.rstrip()}\n"
            f"Test plan save location (absolute path for verification): {target_path}\n"
            f"When calling planner_save_plan, fileName must use this workspace-relative path: {relative_target_path}\n"
            "planner_save_plan must receive a structured JSON object: suites, tests, steps, and expect must be arrays, not JSON-stringified values."
        )
    return (
        f"{prompt.rstrip()}\n"
        f"生成测试计划保存位置（绝对路径，供核对）：{target_path}\n"
        f"调用 planner_save_plan 时，fileName 必须使用 workspace 内相对路径：{relative_target_path}\n"
        "调用 planner_save_plan 时必须传结构化 JSON 对象：suites、tests、steps、expect 必须是数组，"
        "不要把数组或对象 JSON.stringify 成字符串。"
    )


def build_multiple_plan_generation_prompt(
    prompt,
    module_name,
    target_path,
    dependencies,
):
    prompt = strip_legacy_coverage_notices(prompt)
    prompt = append_database_baseline_write_operation_notice(
        prompt,
        dependencies,
    )
    prompt = append_artifact_naming_notice(prompt, dependencies)
    if get_prompt_language(dependencies) == "en":
        return (
            f"{prompt.rstrip()}\n"
            "This task generates a module case index; do not create multiple test-plan files directly.\n"
            "If a page entry, menu, or API cannot be found, do not stop to ask the user. Save the index plan anyway and record gaps in evidence, open_issues, or preconditions.\n"
            f"The cases array must contain no more than {MODULE_PLAN_MAX_CASES} test cases. Save them in the index plan below.\n"
            "The index plan must contain a fenced JSON block whose top level is an object with a cases array.\n"
            "Each cases item represents one test case and must include title, filename, and steps. Use English business names for title and filename; filename must end in .md and contain no path separators. steps must be an array.\n"
            "A step can be a string or an object containing text and an expect array. suite, description, and preconditions are optional context fields.\n"
            "Do not JSON.stringify cases, steps, or expect.\n"
            f"Module name: {module_name}\n"
            f"Index plan save location: {target_path}"
        )
    return (
        f"{prompt.rstrip()}\n"
        "本次任务是模块用例索引生成，不要直接生成多个测试计划文件。\n"
        "如果实际页面入口、菜单或接口暂时找不到，不要向用户提问后停止；仍然必须保存索引计划文件，"
        "用 evidence/open_issues/preconditions 记录缺口，并根据用户最终确认的生成语句保留可验证场景。\n"
        f"cases 数组绝对不能超过 {MODULE_PLAN_MAX_CASES} 个测试用例；请把测试用例保存到下面这个索引计划文件。\n"
        "索引计划文件必须包含一个 fenced JSON 代码块，JSON 顶层必须是对象并包含 cases 数组。\n"
        "cases 数组中每个对象代表一个单独测试用例，至少包含 title、filename、steps；"
        "title 和 filename 必须使用中文业务名称，filename 必须是 .md 文件名且不能包含路径分隔符或英文字母；steps 必须是数组，"
        "每个 step 可以是字符串，也可以是包含 text 和 expect 数组的对象。\n"
        "可以包含 suite、description、preconditions 字段，用于拆分后的单用例计划补充上下文。\n"
        "不要把 cases、steps、expect JSON.stringify 成字符串。\n"
        f"模块名：{module_name}\n"
        f"索引计划保存位置：{target_path}"
    )


def build_markdown_plan_split_prompt(
    module_name,
    source_plan_file,
    markdown_text,
    dependencies,
):
    source_relative = dependencies.get_workspace_relative_path(
        source_plan_file
    )
    if get_prompt_language(dependencies) == "en":
        return (
            "@plan-markdown-splitter\n"
            "You are a test-plan splitting assistant. Convert the generated Markdown plan below into cases JSON that this platform can process.\n"
            "Output JSON only. Do not use Markdown fences or explanations.\n\n"
            "The output must be an object with a cases array. Every item must contain title, filename, suite, optional description and preconditions, and steps. "
            "Each step may be a string or an object with text and expect.\n\n"
            "Rules:\n"
            f"1. The module name is: {module_name}.\n"
            f"2. The source Markdown file is: {source_relative}.\n"
            f"3. Return at most {MODULE_PLAN_MAX_CASES} cases; each case represents exactly one test case.\n"
            "4. Prefer an existing file name or title when present. Generate English business names by default; filename must end in .md and contain no path separators.\n"
            "5. Preserve every scenario type already present; do not filter by positive, negative, boundary, or permission category.\n"
            "6. Do not merge cases or invent repeated or vague cases.\n\n"
            "Source Markdown:\n```markdown\n"
            f"{markdown_text}\n"
            "```"
        )
    return (
        "@plan-markdown-splitter\n"
        "你是测试计划拆分助手。请把下面这个已经生成的普通 Markdown 测试计划拆成平台可处理的 cases JSON。\n"
        "只输出 JSON，不要输出 Markdown fence，不要解释。\n\n"
        "输出格式必须是：\n"
        "{\n"
        "  \"cases\": [\n"
        "    {\n"
        "      \"title\": \"单个测试用例标题\",\n"
        "      \"filename\": \"单个测试用例文件名.md\",\n"
        "      \"suite\": \"模块或套件名\",\n"
        "      \"description\": \"可选说明\",\n"
        "      \"preconditions\": [\"可选前置条件\"],\n"
        "      \"steps\": [\n"
        "        {\"text\": \"操作步骤\", \"expect\": [\"预期结果\"]}\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "规则：\n"
        f"1. 模块名是：{module_name}。\n"
        f"2. 源 Markdown 文件是：{source_relative}。\n"
        f"3. cases 最多 {MODULE_PLAN_MAX_CASES} 个，每个 case 只能代表一个测试用例。\n"
        "4. 如果 Markdown 中有 **File:**、文件名或用例标题，优先用它作为 filename；filename 必须是中文业务名称 .md 文件名，不能包含路径分隔符或英文字母。\n"
        "5. 保留源计划中已经存在的各种场景类型，不要按正向、反向、边界或权限类型过滤。\n"
        "6. 不要合并多个测试用例到同一个 case；不要为了凑数生成重复或空泛用例。\n\n"
        "源 Markdown 内容：\n"
        "```markdown\n"
        f"{markdown_text}\n"
        "```"
    )


def build_script_generation_prompt(
    prompt,
    module_name,
    plan_file,
    script_dir,
    dependencies,
    target_file=None,
    candidate_file=None,
):
    extra_lines = []
    if target_file:
        extra_lines.append(
            f"正式测试脚本目标路径（不要直接写入，由平台校验后提交）：{target_file}"
        )
        try:
            extra_lines.append(
                "正式测试脚本 workspace 相对路径："
                f"{dependencies.get_workspace_relative_path(target_file)}"
            )
        except ValueError:
            pass
    if candidate_file:
        extra_lines.extend(
            [
                f"候选测试脚本保存路径（本次只允许写入这里）：{candidate_file}",
                "请生成完整 Playwright 测试脚本到候选路径；不要修改 specs 目录、测试计划文件或任何已有 tests 文件。",
                "候选路径位于 tests 目录外，请使用普通文件编辑/补丁工具写入候选文件，不要使用只支持 tests 目录的 write_test 工具。",
                "平台会读取候选脚本，静态校验通过后再备份并替换正式目标文件。",
            ]
        )
    prompt = append_database_baseline_write_operation_notice(
        prompt,
        dependencies,
    )
    prompt = append_artifact_naming_notice(prompt, dependencies)
    if get_prompt_language(dependencies) == "en":
        english_extra_lines = []
        if target_file:
            english_extra_lines.append(
                f"Final test-script target path (do not write here directly; the platform validates and commits it): {target_file}"
            )
            try:
                english_extra_lines.append(
                    "Final test-script workspace-relative path:"
                    f"{dependencies.get_workspace_relative_path(target_file)}"
                )
            except ValueError:
                pass
        if candidate_file:
            english_extra_lines.extend(
                [
                    f"Candidate test-script save path (the only path you may write in this task): {candidate_file}",
                    "Write the complete Playwright test to the candidate path. Do not modify specs, test plans, or existing tests files.",
                    "The candidate is outside tests; use an ordinary file edit or patch tool, not a write_test tool limited to tests.",
                    "The platform validates the candidate before backing up and replacing the final target.",
                ]
            )
        return (
            f"{prompt.rstrip()}\n"
            f"Test-plan absolute path: {plan_file}\n"
            f"Test-script directory absolute path: {script_dir}\n"
            + ("\n".join(english_extra_lines) + "\n" if english_extra_lines else "")
            + "Each test script must contain exactly one test(...). Do not use page.waitForTimeout, page.waitForNavigation, page.waitForLoadState, or page.evaluate."
        )
    return (
        f"{prompt.rstrip()}\n"
        f"测试计划文件绝对路径：{plan_file}\n"
        f"测试脚本保存目录绝对路径：{script_dir}\n"
        + ("\n".join(extra_lines) + "\n" if extra_lines else "")
        + "每个测试脚本只能包含一个 test(...)。不要使用 page.waitForTimeout、page.waitForNavigation、page.waitForLoadState 或 page.evaluate。"
    )


def build_seed_generation_prompt(target_system, target_file, dependencies):
    target_system = dependencies.parse_target_system_config(target_system)
    base_url = target_system.get("base_url")
    login_url = dependencies.build_target_login_url(target_system)
    username = target_system.get("username")
    password = target_system.get("password")
    if not base_url:
        raise ValueError("请先配置被测系统地址。")
    if not login_url:
        raise ValueError("请先配置登录页地址。")
    if not username:
        raise ValueError("请先配置登录用户名。")
    if not password:
        raise ValueError("请先配置登录密码。")

    relative_path = dependencies.get_seed_script_relative_path()
    if get_prompt_language(dependencies) == "en":
        return (
            "@playwright-test-generator\n"
            f"Use Playwright MCP to open the login page: {login_url}\n"
            f"Target system baseURL: {base_url}\n"
            f"Username: {username}\n"
            f"Password: {password}\n"
            "Identify the username field, password field, and login button, then complete login.\n"
            "After login, choose a stable assertion such as a URL, navigation item, title, menu, or visible main content.\n"
            f"Write the complete Playwright test to: {target_file}\n"
            f"Workspace-relative script path: {relative_path}\n"
            "The script must contain one test(...), import test and expect from @playwright/test, and avoid page.waitForTimeout, page.waitForNavigation, page.waitForLoadState, and page.evaluate."
        )
    return (
        "@playwright-test-generator\n"
        f"请使用 Playwright MCP 打开登录页：{login_url}\n"
        f"被测系统 baseURL：{base_url}\n"
        f"使用用户名：{username}\n"
        f"使用密码：{password}\n"
        "自动识别用户名输入框、密码输入框和登录按钮，完成登录。\n"
        "登录成功后选择一个稳定的页面状态作为 expect 断言，例如 URL、导航、标题、菜单或主内容可见。\n"
        f"请生成完整 Playwright 测试脚本到：{target_file}\n"
        f"脚本 workspace 相对路径：{relative_path}\n"
        "要求：脚本只能包含一个 test(...)；必须导入 @playwright/test 的 test 和 expect；"
        "不要使用 page.waitForTimeout、page.waitForNavigation、page.waitForLoadState 或 page.evaluate；"
        "现阶段允许把账号和密码直接写入 seed 脚本。"
    )


def build_script_run_prompt(
    prompt,
    module_name,
    filename,
    script_file,
    dependencies,
):
    relative_script_path = dependencies.get_script_test_relative_path(
        module_name,
        filename,
    )
    test_run_args = json.dumps(
        {"locations": [relative_script_path]},
        ensure_ascii=False,
    )
    if get_prompt_language(dependencies) == "en":
        return (
            f"{prompt.rstrip()}\n"
            f"Current test-script filename: {filename}\n"
            f"Current test-script absolute path: {script_file}\n"
            f"Current Playwright-relative path: {relative_script_path}\n"
            f"Run and verify with Playwright MCP test_run using exactly: {test_run_args}\n"
            "Do not use backslash paths, test titles, directory paths, or line numbers for test_run locations.\n"
            "Only run and repair the current test script. Do not run the full suite, and keep Playwright execution video."
        )
    return (
        f"{prompt.rstrip()}\n"
        f"当前测试脚本文件名：{filename}\n"
        f"当前测试脚本绝对路径：{script_file}\n"
        f"当前测试脚本 Playwright 相对路径：{relative_script_path}\n"
        f"运行和验证必须调用 Playwright MCP test_run，参数必须使用：{test_run_args}\n"
        "不要使用反斜杠路径、测试标题、目录路径或行号作为 test_run locations。\n"
        "只允许运行并修复当前测试脚本，不要运行全量测试，保留 Playwright 执行视频。"
    )
