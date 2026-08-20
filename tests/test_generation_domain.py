import json
import random
import re
import tempfile
import unittest
from pathlib import Path

from test_plan_viewer.artifacts import naming
from test_plan_viewer.generation import cases, opencode, prompts


# These small reference functions intentionally preserve the behavior that was
# in app.py at the extraction boundary. They keep the randomized parity checks
# self-contained; running this test suite never shells out to git.
def reference_dedupe_prompt_notice(prompt, notice):
    prompt_text = str(prompt or "").rstrip()
    if not notice:
        return prompt_text
    parts = prompt_text.split(notice)
    if len(parts) <= 2:
        return prompt_text
    prompt_text = f"{parts[0]}{notice}{''.join(parts[1:])}"
    return re.sub(r"\n{3,}", "\n\n", prompt_text).rstrip()


def reference_append_prompt_notice_once(prompt, notice, key):
    prompt_text = reference_dedupe_prompt_notice(prompt, notice)
    if not notice or key in prompt_text:
        return prompt_text
    if not prompt_text:
        return notice
    return f"{prompt_text}\n{notice}"


def reference_normalize_case_steps(value):
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


def reference_build_generation_prompt(
    prompt,
    target_path,
    *,
    baseline_enabled,
    relative_target_path,
):
    prompt = prompts.strip_legacy_coverage_notices(prompt)
    notice = (
        prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE
        if baseline_enabled
        else ""
    )
    prompt = reference_append_prompt_notice_once(
        prompt,
        notice,
        prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE_KEY,
    )
    prompt = reference_append_prompt_notice_once(
        prompt,
        prompts.CHINESE_ARTIFACT_NAMING_NOTICE,
        prompts.CHINESE_ARTIFACT_NAMING_NOTICE_KEY,
    )
    return (
        f"{prompt.rstrip()}\n"
        f"生成测试计划保存位置（绝对路径，供核对）：{target_path}\n"
        f"调用 planner_save_plan 时，fileName 必须使用 workspace 内相对路径：{relative_target_path}\n"
        "调用 planner_save_plan 时必须传结构化 JSON 对象：suites、tests、steps、expect 必须是数组，"
        "不要把数组或对象 JSON.stringify 成字符串。"
    )


def make_prompt_dependencies(
    *,
    baseline_enabled=False,
    workspace_relative_path=lambda path: f"relative/{Path(path).name}",
):
    return prompts.PromptDependencies(
        get_database_baseline_config=lambda: {
            "enabled": baseline_enabled,
        },
        get_workspace_relative_path=workspace_relative_path,
        parse_target_system_config=lambda value: dict(value or {}),
        build_target_login_url=lambda value: value.get("login_url") or "",
        get_seed_script_relative_path=lambda: "tests/seed/seed.spec.ts",
        get_script_test_relative_path=(
            lambda module_name, filename: f"tests/{module_name}/{filename}"
        ),
        # These parity tests verify the retained Chinese prompt behavior. The
        # application default is tested separately and is now English.
        get_project_language=lambda: "zh-CN",
    )


def make_case_dependencies(specs_dir, *, language="zh-CN"):
    return cases.CaseDependencies(
        get_specs_dir=lambda: specs_dir,
        validate_module_name=naming.validate_module_name,
        get_plan_file=(
            lambda module_name, filename: (
                specs_dir / module_name / filename
            )
        ),
        plan_payload=naming.plan_payload,
        ensure_directory=(
            lambda path: path.mkdir(parents=True, exist_ok=True)
        ),
        file_exists=lambda path: path.exists(),
        read_text=lambda path: path.read_text(encoding="utf-8"),
        write_text=lambda path, text: path.write_text(
            text,
            encoding="utf-8",
            newline="",
        ),
        get_project_language=lambda: language,
    )


class OpenCodeErrorLocalizationTests(unittest.TestCase):
    TOOL_STATUS_ERROR_PATTERN = re.compile(r"tool(?:Name)?[=: ]+([\w.-]+)")

    def test_tls_diagnostic_uses_the_requested_project_language(self):
        source = "Error: unknown certificate verification error"

        english = opencode.format_opencode_execution_error(
            source,
            self.TOOL_STATUS_ERROR_PATTERN,
            language="en",
        )
        chinese = opencode.format_opencode_execution_error(
            source,
            self.TOOL_STATUS_ERROR_PATTERN,
            language="zh-CN",
        )

        self.assertIn("TLS certificate verification failed", english)
        self.assertNotRegex(english, r"[\u3400-\u9fff]")
        self.assertIn("TLS 证书校验失败", chinese)

    def test_provider_compatibility_diagnostic_uses_english(self):
        source = (
            'Type validation failed: toolName=browser_click '
            '"choices" "error" "status"'
        )

        result = opencode.format_opencode_execution_error(
            source,
            self.TOOL_STATUS_ERROR_PATTERN,
            language="en",
        )

        self.assertIn("OpenCode provider compatibility error", result)
        self.assertIn("Triggering tool: browser_click", result)
        self.assertNotRegex(result, r"[\u3400-\u9fff]")

    def test_unknown_provider_errors_remain_verbatim(self):
        source = "Provider connection reset by peer"

        self.assertEqual(
            opencode.format_opencode_execution_error(
                source,
                self.TOOL_STATUS_ERROR_PATTERN,
                language="en",
            ),
            source,
        )


class PromptNoticeParityTests(unittest.TestCase):
    def test_notice_deduplication_matches_extracted_head_behavior(self):
        generator = random.Random(20260723)
        notice = "固定提示"
        for _ in range(250):
            fragments = [
                generator.choice(
                    ["正文", "补充", "", "\n", "\n\n", "固定提示的键"]
                )
                for _ in range(generator.randint(0, 6))
            ]
            value = notice.join(fragments)
            if generator.choice([True, False]):
                value += "\n" * generator.randint(0, 5)
            key = generator.choice(["固定", "不存在", ""])

            with self.subTest(value=value, key=key):
                self.assertEqual(
                    prompts.dedupe_prompt_notice(value, notice),
                    reference_dedupe_prompt_notice(value, notice),
                )
                self.assertEqual(
                    prompts.append_prompt_notice_once(
                        value,
                        notice,
                        key,
                    ),
                    reference_append_prompt_notice_once(
                        value,
                        notice,
                        key,
                    ),
                )

    def test_database_and_chinese_notices_are_each_present_once(self):
        dependencies = make_prompt_dependencies(
            baseline_enabled=True,
        )
        source = (
            "生成审批计划\n"
            f"{prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE}\n"
            f"{prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE}\n"
            f"{prompts.CHINESE_ARTIFACT_NAMING_NOTICE}\n"
            f"{prompts.CHINESE_ARTIFACT_NAMING_NOTICE}"
        )

        result = prompts.append_database_baseline_write_operation_notice(
            source,
            dependencies,
        )
        result = prompts.append_chinese_artifact_naming_notice(result)

        self.assertEqual(
            result.count(
                prompts.DATABASE_BASELINE_WRITE_OPERATION_NOTICE
            ),
            1,
        )
        self.assertEqual(
            result.count(prompts.CHINESE_ARTIFACT_NAMING_NOTICE),
            1,
        )


class PromptBuilderTests(unittest.TestCase):
    def test_generation_prompt_matches_the_extracted_head_text(self):
        target = Path("/workspace/specs/登录/登录流程.md")
        relative = "specs/登录/登录流程.md"
        dependencies = make_prompt_dependencies(
            baseline_enabled=True,
            workspace_relative_path=lambda _path: relative,
        )

        actual = prompts.build_generation_prompt(
            "生成登录计划",
            target,
            dependencies,
        )

        self.assertEqual(
            actual,
            reference_build_generation_prompt(
                "生成登录计划",
                target,
                baseline_enabled=True,
                relative_target_path=relative,
            ),
        )

    def test_plan_builders_keep_the_absolute_case_limit(self):
        dependencies = make_prompt_dependencies()

        multiple = prompts.build_multiple_plan_generation_prompt(
            "生成模块计划",
            "登录模块",
            "/workspace/specs/登录模块/用例索引.md",
            dependencies,
        )
        split = prompts.build_markdown_plan_split_prompt(
            "登录模块",
            Path("/workspace/specs/登录模块/原计划.md"),
            "# 原计划",
            dependencies,
        )

        self.assertEqual(prompts.ABSOLUTE_PLAN_MAX_CASES, 25)
        self.assertEqual(prompts.MODULE_PLAN_MAX_CASES, 25)
        self.assertIn("cases 数组绝对不能超过 25 个", multiple)
        self.assertIn("cases 最多 25 个", split)
        self.assertIn("relative/原计划.md", split)

    def test_workspace_path_errors_propagate_or_are_suppressed_as_before(self):
        def reject_outside_workspace(_path):
            raise ValueError("outside workspace")

        dependencies = make_prompt_dependencies(
            workspace_relative_path=reject_outside_workspace,
        )

        with self.assertRaisesRegex(ValueError, "outside workspace"):
            prompts.build_generation_prompt(
                "生成计划",
                Path("/outside/计划.md"),
                dependencies,
            )
        with self.assertRaisesRegex(ValueError, "outside workspace"):
            prompts.build_markdown_plan_split_prompt(
                "登录",
                Path("/outside/计划.md"),
                "# 内容",
                dependencies,
            )

        script_prompt = prompts.build_script_generation_prompt(
            "生成脚本",
            "登录",
            Path("/workspace/specs/登录/计划.md"),
            Path("/workspace/tests/登录"),
            dependencies,
            target_file=Path("/outside/登录.spec.ts"),
            candidate_file=Path("/tmp/登录.spec.ts"),
        )
        self.assertIn("正式测试脚本目标路径", script_prompt)
        self.assertNotIn("正式测试脚本 workspace 相对路径", script_prompt)
        self.assertIn("候选测试脚本保存路径", script_prompt)

    def test_seed_and_run_prompts_use_only_supplied_runtime_capabilities(self):
        dependencies = make_prompt_dependencies()
        target_system = {
            "base_url": "https://example.test",
            "login_url": "https://example.test/login",
            "username": "测试员",
            "password": "secret",
        }

        seed_prompt = prompts.build_seed_generation_prompt(
            target_system,
            Path("/workspace/tests/seed/seed.spec.ts"),
            dependencies,
        )
        run_prompt = prompts.build_script_run_prompt(
            "执行脚本",
            "登录",
            "登录成功.spec.ts",
            Path("/workspace/tests/登录/登录成功.spec.ts"),
            dependencies,
        )

        self.assertIn("https://example.test/login", seed_prompt)
        self.assertIn("tests/seed/seed.spec.ts", seed_prompt)
        self.assertIn(
            '{"locations": ["tests/登录/登录成功.spec.ts"]}',
            run_prompt,
        )

    def test_seed_prompt_validates_all_required_target_fields(self):
        dependencies = make_prompt_dependencies()
        complete = {
            "base_url": "https://example.test",
            "login_url": "https://example.test/login",
            "username": "user",
            "password": "password",
        }
        expectations = {
            "base_url": "被测系统地址",
            "login_url": "登录页地址",
            "username": "登录用户名",
            "password": "登录密码",
        }
        for missing_field, message in expectations.items():
            value = {**complete, missing_field: ""}
            with (
                self.subTest(missing_field=missing_field),
                self.assertRaisesRegex(ValueError, message),
            ):
                prompts.build_seed_generation_prompt(
                    value,
                    Path("/workspace/tests/seed/seed.spec.ts"),
                    dependencies,
                )


class CaseParsingTests(unittest.TestCase):
    def test_json_fences_and_raw_json_select_the_first_valid_cases_object(self):
        expected = {
            "cases": [
                {
                    "title": "登录成功",
                    "filename": "登录成功.md",
                    "steps": [],
                }
            ]
        }
        serialized = json.dumps(expected, ensure_ascii=False)
        inputs = [
            f"前言\n```json\n{serialized}\n```\n结尾",
            f"```\n{serialized}\n```",
            serialized,
            (
                "```json\nnot-json\n```\n"
                "```json\n"
                f"{serialized}\n"
                "```"
            ),
        ]
        for markdown_text in inputs:
            with self.subTest(markdown_text=markdown_text):
                self.assertEqual(
                    cases.extract_case_index(markdown_text),
                    expected,
                )

        for invalid in (
            "",
            "```json\n[]\n```",
            "```json\n{\"cases\": {}}\n```",
            "{\"plans\": []}",
        ):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ValueError, "未找到"),
            ):
                cases.extract_case_index(invalid)

    def test_json_fence_parsing_has_seeded_random_parity(self):
        generator = random.Random(8251365)
        wrappers = (
            lambda text: text,
            lambda text: f"```json\n{text}\n```",
            lambda text: f"说明\n```\n{text}\n```\n结束",
            lambda text: (
                "```json\ninvalid\n```\n"
                f"```JSON\n{text}\n```"
            ),
        )
        for index in range(100):
            expected = {
                "cases": [
                    {
                        "title": f"随机用例{index}",
                        "steps": [f"步骤{generator.randint(0, 99)}"],
                    }
                ],
                "seed": generator.randint(0, 999999),
            }
            text = json.dumps(expected, ensure_ascii=False)
            wrapped = generator.choice(wrappers)(text)
            with self.subTest(index=index):
                self.assertEqual(
                    cases.extract_case_index(wrapped),
                    expected,
                )

    def test_case_index_container_aliases_match_the_legacy_rules(self):
        values = [
            ([{"title": "一"}], [{"title": "一"}]),
            ({"cases": [{"title": "二"}]}, [{"title": "二"}]),
            ({"plans": [{"title": "三"}]}, [{"title": "三"}]),
            ({"tests": [{"title": "四"}]}, [{"title": "四"}]),
        ]
        for value, expected in values:
            with self.subTest(value=value):
                self.assertEqual(
                    cases.normalize_case_index_cases(value),
                    expected,
                )

        for value in (None, {}, [], {"cases": []}, {"cases": "invalid"}):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "cases 数组为空"),
            ):
                cases.normalize_case_index_cases(value)

    def test_english_case_index_parsing_errors_are_localized(self):
        with self.assertRaisesRegex(ValueError, "No JSON object"):
            cases.extract_case_index("not JSON", language="en")
        with self.assertRaisesRegex(ValueError, "cases array is empty"):
            cases.normalize_case_index_cases(
                {"cases": []},
                language="en",
            )


class CaseNormalizationParityTests(unittest.TestCase):
    def test_step_normalization_matches_extracted_head_on_random_inputs(self):
        generator = random.Random(1015)
        text_keys = ("text", "step", "action")
        expect_keys = ("expect", "expected", "expects", "expectations")
        scalars = (
            None,
            "",
            "  ",
            "操作",
            "  点击登录  ",
            0,
            1,
            False,
            True,
        )
        expectations = (
            None,
            "",
            "  页面可见  ",
            [],
            ["成功", "", 0, None, False],
            {"value": "ignored"},
            0,
        )

        for sample_index in range(300):
            if generator.random() < 0.15:
                value = generator.choice(scalars)
            else:
                value = []
                for _ in range(generator.randint(0, 8)):
                    item_kind = generator.choice(
                        ["string", "dict", "number", "none", "list"]
                    )
                    if item_kind == "string":
                        item = generator.choice(
                            ["", " ", "点击", "  输入账号  "]
                        )
                    elif item_kind == "dict":
                        item = {
                            generator.choice(text_keys): generator.choice(
                                scalars
                            ),
                            generator.choice(expect_keys): generator.choice(
                                expectations
                            ),
                        }
                    elif item_kind == "number":
                        item = generator.randint(-3, 3)
                    elif item_kind == "list":
                        item = ["ignored"]
                    else:
                        item = None
                    value.append(item)

            with self.subTest(sample_index=sample_index, value=value):
                self.assertEqual(
                    cases.normalize_case_steps(value),
                    reference_normalize_case_steps(value),
                )

    def test_chinese_case_filename_behavior_is_stable(self):
        self.assertEqual(
            cases.normalize_case_filename("登录成功.md", ""),
            "登录成功.md",
        )
        self.assertEqual(
            cases.normalize_case_filename("", "用户登录", index=1),
            "用户登录.md",
        )
        self.assertEqual(
            cases.normalize_case_filename(
                "login-flow.md",
                "登录流程",
                index=3,
            ),
            "登录流程-000051.md",
        )
        fallback = cases.normalize_case_filename(
            "case.md",
            "",
            index=2,
        )
        self.assertEqual(fallback, "测试用例-000050.md")
        self.assertTrue(naming.has_chinese_text(Path(fallback).stem))
        self.assertFalse(naming.has_ascii_letters(Path(fallback).stem))

        with self.assertRaisesRegex(ValueError, "缺少 title 或 filename"):
            cases.normalize_case_filename("", "")

    def test_english_case_filename_uses_safe_english_candidates_and_fallback(self):
        self.assertEqual(
            cases.normalize_case_filename(
                "successful-login.md",
                "Successful Login",
                language="en",
            ),
            "successful-login.md",
        )
        self.assertEqual(
            cases.normalize_case_filename(
                "../checkout-flow.md",
                "Checkout Flow",
                language="en",
            ),
            "checkout-flow.md",
        )
        self.assertEqual(
            cases.normalize_case_filename(
                "登录成功.md",
                "Successful Login",
                language="en",
            ),
            "Successful Login.md",
        )
        self.assertEqual(
            cases.normalize_case_filename(
                "测试用例.md",
                "",
                index=7,
                language="en",
            ),
            "test-case-007.md",
        )
        with self.assertRaisesRegex(ValueError, "missing title or filename"):
            cases.normalize_case_filename("", "", language="en")

    def test_case_markdown_keeps_headings_and_step_expectation_format(self):
        markdown = cases.case_to_markdown(
            "登录模块",
            "用例索引.md",
            {
                "title": "登录成功",
                "suite": "身份认证",
                "description": "验证有效账号。",
                "preconditions": ["账号已启用", "登录页可访问"],
                "steps": [
                    "打开登录页",
                    {
                        "action": "提交账号密码",
                        "expected": ["进入首页", "显示用户名"],
                    },
                ],
            },
        )

        self.assertEqual(
            markdown,
            (
                "# 登录成功\n"
                "\n"
                "模块：登录模块\n"
                "来源：用例索引.md\n"
                "套件：身份认证\n"
                "\n"
                "## 说明\n"
                "\n"
                "验证有效账号。\n"
                "\n"
                "## 前置条件\n"
                "\n"
                "- 账号已启用\n"
                "- 登录页可访问\n"
                "\n"
                "## Steps\n"
                "\n"
                "1. 打开登录页\n"
                "\n"
                "2. 提交账号密码\n"
                "   - Expect: 进入首页\n"
                "   - Expect: 显示用户名\n"
            ),
        )

    def test_english_case_markdown_uses_english_labels_and_empty_step_fallback(self):
        markdown = cases.case_to_markdown(
            "Login",
            "case-index.md",
            {
                "filename": "successful-login.md",
                "suite": "Authentication",
                "description": "Verify a valid account.",
                "preconditions": ["The account is active."],
                "steps": [],
            },
            language="en",
        )

        self.assertEqual(
            markdown,
            (
                "# successful-login\n"
                "\n"
                "Module: Login\n"
                "Source: case-index.md\n"
                "Suite: Authentication\n"
                "\n"
                "## Description\n"
                "\n"
                "Verify a valid account.\n"
                "\n"
                "## Preconditions\n"
                "\n"
                "- The account is active.\n"
                "\n"
                "## Steps\n"
                "\n"
                "1. Add test steps.\n"
            ),
        )


class CaseSplittingTests(unittest.TestCase):
    def test_absolute_case_limit_fails_before_any_filesystem_capability(self):
        dependencies = cases.CaseDependencies(
            get_specs_dir=lambda: (_ for _ in ()).throw(
                AssertionError("filesystem must not be consulted")
            ),
            validate_module_name=naming.validate_module_name,
            get_plan_file=lambda _module, _filename: Path("/unused"),
            plan_payload=naming.plan_payload,
            ensure_directory=lambda _path: None,
            file_exists=lambda _path: False,
            read_text=lambda _path: "",
            write_text=lambda _path, _text: None,
        )
        too_many = [
            {
                "title": f"用例{index}",
                "filename": f"用例{index}.md",
                "steps": [],
            }
            for index in range(26)
        ]

        with self.assertRaisesRegex(ValueError, "绝对上限 25"):
            cases.split_case_index_cases(
                "登录模块",
                "用例索引.md",
                too_many,
                dependencies,
            )

    def test_split_writes_cases_and_preserves_non_conflict_skip_rules(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)
            module_dir = specs_dir / "登录模块"
            module_dir.mkdir(parents=True)
            existing = module_dir / "已有流程.md"
            source = module_dir / "用例索引.md"
            source.write_text("index", encoding="utf-8")
            existing_case = {
                "title": "已有流程",
                "filename": existing.name,
                "steps": [],
            }
            existing_markdown = cases.case_to_markdown(
                "登录模块",
                source.name,
                existing_case,
            )
            existing.write_text(existing_markdown, encoding="utf-8")

            result = cases.split_case_index_cases(
                "登录模块",
                source.name,
                [
                    {
                        "title": "登录成功",
                        "filename": "登录成功.md",
                        "steps": ["提交有效账号"],
                    },
                    {
                        "title": "源文件",
                        "filename": "用例索引.md",
                        "steps": [],
                    },
                    existing_case,
                    "ignored",
                ],
                dependencies,
                source_plan_file=source,
            )

            self.assertEqual(
                [item["filename"] for item in result["created"]],
                ["登录成功.md"],
            )
            self.assertEqual(
                [item["filename"] for item in result["skipped"]],
                ["用例索引.md"],
            )
            self.assertEqual(
                [item["filename"] for item in result["reused"]],
                ["已有流程.md"],
            )
            self.assertEqual(
                result["source"]["filename"],
                "用例索引.md",
            )
            generated = module_dir / "登录成功.md"
            self.assertTrue(generated.exists())
            self.assertIn(
                "1. 提交有效账号",
                generated.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                existing.read_text(encoding="utf-8"),
                existing_markdown,
            )

    def test_english_split_writes_english_filenames_and_markdown_headings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(
                specs_dir,
                language="en",
            )

            result = cases.split_case_index_cases(
                "Login",
                "case-index.md",
                [
                    {
                        "title": "Successful Login",
                        "filename": "successful-login.md",
                        "description": "Verify a valid account.",
                        "preconditions": ["The account is active."],
                        "steps": ["Submit valid credentials."],
                    },
                    {
                        "title": "Locked Account",
                        "filename": "锁定账号.md",
                        "steps": [],
                    },
                ],
                dependencies,
            )

            self.assertEqual(
                [item["filename"] for item in result["created"]],
                ["successful-login.md", "Locked Account.md"],
            )
            for payload in result["created"]:
                markdown = Path(payload["path"]).read_text(encoding="utf-8")
                self.assertIn("Module: Login", markdown)
                self.assertIn("Source: case-index.md", markdown)
                self.assertIn("## Steps", markdown)
                self.assertNotIn("模块：", markdown)
                self.assertNotIn("来源：", markdown)
                self.assertNotIn("## 前置条件", markdown)

    def test_english_split_localizes_validation_and_conflict_reasons(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(
                specs_dir,
                language="en",
            )
            module_dir = specs_dir / "Login"
            module_dir.mkdir(parents=True)
            source = module_dir / "case-index.md"
            source.write_text("index", encoding="utf-8")
            existing = module_dir / "successful-login.md"
            existing.write_text("Maintained manually.", encoding="utf-8")
            valid_case = {
                "title": "Successful Login",
                "filename": existing.name,
                "steps": [],
            }

            result = cases.split_case_index_cases(
                "Login",
                source.name,
                [
                    valid_case,
                    {**valid_case, "filename": source.name},
                    valid_case,
                ],
                dependencies,
                source_plan_file=source,
            )

            self.assertEqual(result["reason_code"], "case_content_conflict")
            self.assertEqual(
                result["conflicts"][0]["reason"],
                "File already exists.",
            )
            self.assertIn(
                "Index and internal filenames are not split.",
                [item["reason"] for item in result["skipped"]],
            )

            with self.assertRaisesRegex(ValueError, "valid case to split"):
                cases.split_case_index_cases(
                    "Login",
                    source.name,
                    [None, "invalid"],
                    dependencies,
                )

            with self.assertRaisesRegex(ValueError, "platform limit of 25"):
                cases.split_case_index_cases(
                    "Login",
                    source.name,
                    [
                        {
                            "title": f"Case {index}",
                            "filename": f"case-{index}.md",
                            "steps": [],
                        }
                        for index in range(26)
                    ],
                    dependencies,
                )

            source.write_text("not JSON", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No JSON object"):
                cases.split_case_index_plan(
                    "Login",
                    source,
                    dependencies,
                )

    def test_late_invalid_case_is_rejected_before_any_case_file_is_written(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)

            with self.assertRaisesRegex(ValueError, "缺少 title 或 filename"):
                cases.split_case_index_cases(
                    "登录模块",
                    "用例索引.md",
                    [
                        {
                            "title": "登录成功",
                            "filename": "登录成功.md",
                            "steps": ["提交有效账号"],
                        },
                        {},
                    ],
                    dependencies,
                )

            self.assertFalse((specs_dir / "登录模块" / "登录成功.md").exists())

    def test_identical_existing_case_is_returned_as_reused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)
            module_dir = specs_dir / "登录模块"
            module_dir.mkdir(parents=True)
            raw_case = {
                "title": "登录成功",
                "filename": "登录成功.md",
                "steps": ["提交有效账号"],
            }
            expected = cases.case_to_markdown(
                "登录模块",
                "用例索引.md",
                raw_case,
            )
            target = module_dir / "登录成功.md"
            target.write_text(expected, encoding="utf-8")

            result = cases.split_case_index_cases(
                "登录模块",
                "用例索引.md",
                [raw_case],
                dependencies,
            )

            self.assertEqual(result["created"], [])
            self.assertEqual(
                [item["filename"] for item in result["reused"]],
                ["登录成功.md"],
            )
            self.assertEqual(result["skipped"], [])
            self.assertEqual(target.read_text(encoding="utf-8"), expected)

    def test_conflicting_existing_case_remains_skipped_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)
            module_dir = specs_dir / "登录模块"
            module_dir.mkdir(parents=True)
            target = module_dir / "登录成功.md"
            target.write_text("人工维护内容", encoding="utf-8")

            result = cases.split_case_index_cases(
                "登录模块",
                "用例索引.md",
                [
                    {
                        "title": "登录成功",
                        "filename": "登录成功.md",
                        "steps": ["提交有效账号"],
                    }
                ],
                dependencies,
            )

            self.assertEqual(result["created"], [])
            self.assertEqual(result["reused"], [])
            self.assertEqual(result["reason_code"], "case_content_conflict")
            self.assertEqual(
                [item["filename"] for item in result["conflicts"]],
                ["登录成功.md"],
            )
            self.assertEqual(
                [item["filename"] for item in result["skipped"]],
                ["登录成功.md"],
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "人工维护内容")

    def test_conflict_prevents_non_conflicting_case_from_being_written(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)
            module_dir = specs_dir / "登录模块"
            module_dir.mkdir(parents=True)
            conflict = module_dir / "已有登录.md"
            conflict.write_text("人工维护内容", encoding="utf-8")
            creatable = module_dir / "新登录.md"

            result = cases.split_case_index_cases(
                "登录模块",
                "用例索引.md",
                [
                    {
                        "title": "已有登录",
                        "filename": conflict.name,
                        "steps": ["提交有效账号"],
                    },
                    {
                        "title": "新登录",
                        "filename": creatable.name,
                        "steps": ["提交备用账号"],
                    },
                ],
                dependencies,
            )

            self.assertEqual(result["created"], [])
            self.assertEqual(result["reason_code"], "case_content_conflict")
            self.assertEqual(
                [item["filename"] for item in result["conflicts"]],
                [conflict.name],
            )
            self.assertEqual(conflict.read_text(encoding="utf-8"), "人工维护内容")
            self.assertFalse(creatable.exists())

    def test_unsafe_case_filename_is_normalized_before_target_planning(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)

            result = cases.split_case_index_cases(
                "登录模块",
                "用例索引.md",
                [
                    {
                        "title": "越界路径",
                        "filename": "../越界路径.md",
                        "steps": [],
                    }
                ],
                dependencies,
            )

            filename = result["created"][0]["filename"]
            self.assertNotIn("/", filename)
            self.assertNotIn("\\", filename)
            self.assertEqual(
                (specs_dir / "登录模块" / filename).parent,
                specs_dir / "登录模块",
            )
            self.assertFalse((specs_dir / "越界路径.md").exists())

    def test_normalized_duplicate_is_an_atomic_conflict_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(
                specs_dir,
                language="en",
            )
            module_dir = specs_dir / "Checkout"
            module_dir.mkdir(parents=True)
            source = module_dir / "case-index.md"
            source_content = "source remains recoverable"
            source.write_text(source_content, encoding="utf-8")

            result = cases.split_case_index_cases(
                "Checkout",
                source.name,
                [
                    {
                        "title": "Checkout through an unsafe path",
                        "filename": "../checkout-flow.md",
                        "steps": [],
                    },
                    {
                        "title": "Checkout through a safe path",
                        "filename": "checkout-flow.md",
                        "steps": [],
                    },
                    {
                        "title": "Review Inventory",
                        "filename": "review-inventory.md",
                        "steps": [],
                    },
                ],
                dependencies,
                source_plan_file=source,
            )

            self.assertEqual(result["created"], [])
            self.assertEqual(result["reason_code"], "case_content_conflict")
            self.assertEqual(
                result["conflicts"],
                [
                    {
                        "filename": "checkout-flow.md",
                        "reason": (
                            "Multiple cases resolve to the same filename."
                        ),
                        "reason_code": "duplicate_filename",
                    }
                ],
            )
            self.assertEqual(source.read_text(encoding="utf-8"), source_content)
            self.assertFalse((module_dir / "checkout-flow.md").exists())
            self.assertFalse((module_dir / "review-inventory.md").exists())

    def test_filesystem_equivalent_names_are_an_atomic_conflict(self):
        scenarios = (
            (
                "en",
                "Checkout",
                "case-index.md",
                "Login.md",
                "login.md",
                "Review Inventory.md",
            ),
            (
                "zh-CN",
                "登录模块",
                "用例索引.md",
                "ガ登录.md",
                "カ\u3099登录.md",
                "复核库存.md",
            ),
        )
        for language, module, source_name, first, second, independent in scenarios:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                specs_dir = Path(directory) / "specs"
                dependencies = make_case_dependencies(
                    specs_dir,
                    language=language,
                )
                module_dir = specs_dir / module
                module_dir.mkdir(parents=True)
                source = module_dir / source_name
                source_content = "source remains recoverable"
                source.write_text(source_content, encoding="utf-8")

                result = cases.split_case_index_cases(
                    module,
                    source.name,
                    [
                        {"title": "First", "filename": first, "steps": []},
                        {"title": "Second", "filename": second, "steps": []},
                        {
                            "title": "Independent",
                            "filename": independent,
                            "steps": [],
                        },
                    ],
                    dependencies,
                    source_plan_file=source,
                )

                self.assertEqual(result["created"], [])
                self.assertEqual(result["reason_code"], "case_content_conflict")
                self.assertEqual(len(result["conflicts"]), 1)
                self.assertEqual(
                    result["conflicts"][0]["reason_code"],
                    "duplicate_filename",
                )
                self.assertEqual(source.read_text(encoding="utf-8"), source_content)
                self.assertFalse((module_dir / independent).exists())

    def test_split_plan_reads_json_through_the_injected_dependency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "specs"
            dependencies = make_case_dependencies(specs_dir)
            source = specs_dir / "登录模块" / "用例索引.md"
            source.parent.mkdir(parents=True)
            source.write_text(
                (
                    "```json\n"
                    '{"cases":[{"title":"登录失败","steps":["提交错误密码"]}]}\n'
                    "```"
                ),
                encoding="utf-8",
            )

            result = cases.split_case_index_plan(
                "登录模块",
                source,
                dependencies,
            )

            self.assertEqual(len(result["created"]), 1)
            filename = result["created"][0]["filename"]
            self.assertEqual(filename, "登录失败.md")
            self.assertTrue((source.parent / filename).exists())

    def test_all_invalid_case_items_still_raise_the_legacy_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            dependencies = make_case_dependencies(
                Path(temporary_directory) / "specs"
            )
            with self.assertRaisesRegex(ValueError, "没有可拆分"):
                cases.split_case_index_cases(
                    "登录模块",
                    "用例索引.md",
                    [None, "invalid", []],
                    dependencies,
                )


class GenerationBoundaryTests(unittest.TestCase):
    def test_generation_domain_has_no_flask_or_app_imports(self):
        package_dir = (
            Path(__file__).resolve().parents[1]
            / "test_plan_viewer"
            / "generation"
        )
        for source_file in package_dir.glob("*.py"):
            source = source_file.read_text(encoding="utf-8")
            with self.subTest(source_file=source_file.name):
                self.assertNotRegex(
                    source,
                    r"(?m)^\s*(?:from|import)\s+(?:app|flask)\b",
                )


if __name__ == "__main__":
    unittest.main()
