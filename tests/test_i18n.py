import ast
import hashlib
import unittest
from pathlib import Path

from test_plan_viewer.configuration import coverage_profiles_for_language
from test_plan_viewer.i18n import (
    _EXACT_ERRORS,
    _PREFIX_ERRORS,
    localize_platform_error,
)


APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_SCAN_EXCLUSIONS = {
    Path("test_plan_viewer/i18n.py"): "the platform translation catalog",
    Path("test_plan_viewer/agent/localization.py"): "the Agent bilingual catalog",
    Path("test_plan_viewer/generation/prompts.py"): "model instructions, not platform UI copy",
}
# Existing domain-layer exception debt is frozen by content rather than hidden
# behind a directory wildcard. New or changed Chinese exceptions alter this
# baseline and must either be registered above or reviewed deliberately.
LEGACY_DOMAIN_ERROR_BASELINE = (
    331,
    "f0ee439a4ffed7a147eade5312bfe402647fbdd4c45b02b5997e0ec1f1961b2f",
)


def contains_han(value):
    return isinstance(value, str) and any(
        "\u3400" <= character <= "\u9fff"
        for character in value
    )


def error_expression_patterns(node):
    if isinstance(node, ast.Constant) and contains_han(node.value):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            "".join(
                part.value
                if isinstance(part, ast.Constant)
                else "{dynamic}"
                for part in node.values
            )
        ]
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and contains_han(child.value)
    ]


def platform_error_is_registered(pattern):
    text = pattern.strip()
    leading = text.split("{dynamic}", 1)[0]
    return text in _EXACT_ERRORS or any(
        text.startswith(source) or leading == source
        for source, _target in _PREFIX_ERRORS
    )


def raised_error_patterns(tree):
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.Raise) or statement.exc is None:
            continue
        target = statement.exc
        if isinstance(target, ast.Call) and target.args:
            target = target.args[0]
        for pattern in error_expression_patterns(target):
            if contains_han(pattern):
                yield pattern


class PlatformErrorLocalizationTests(unittest.TestCase):
    def test_english_translates_known_platform_errors(self):
        self.assertEqual(
            localize_platform_error("需求不存在。", "en"),
            "Requirement not found.",
        )
        self.assertEqual(
            localize_platform_error("创建 Agent 任务失败：需求不存在。", "en"),
            "Could not create Agent task: Requirement not found.",
        )
        self.assertEqual(
            localize_platform_error("脚本准备任务已取消。", "en"),
            "The script-preparation task was cancelled.",
        )
        self.assertEqual(
            localize_platform_error("重置密码失败：需求不存在。", "en"),
            "Could not reset password: Requirement not found.",
        )

    def test_chinese_and_unknown_errors_are_preserved(self):
        self.assertEqual(localize_platform_error("需求不存在。", "zh-CN"), "需求不存在。")
        third_party = "浏览器连接被目标系统拒绝"
        self.assertEqual(localize_platform_error(third_party, "en"), third_party)

    def test_direct_api_error_literals_are_registered(self):
        """Prevent new Chinese Flask error payloads from bypassing i18n."""

        paths = [APP_DIR / "app.py"]
        paths.extend(
            sorted((APP_DIR / "test_plan_viewer" / "web").rglob("*.py"))
        )
        missing = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for mapping in (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Dict)
            ):
                for key, value in zip(mapping.keys, mapping.values):
                    if not (
                        isinstance(key, ast.Constant)
                        and key.value == "error"
                    ):
                        continue
                    for pattern in error_expression_patterns(value):
                        if not contains_han(pattern):
                            continue
                        if not platform_error_is_registered(pattern):
                            missing.append(
                                f"{path.relative_to(APP_DIR)}:"
                                f"{value.lineno}: {pattern}"
                            )
        self.assertEqual(
            missing,
            [],
            "Direct API error literals must be registered in "
            "test_plan_viewer.i18n before they can be returned.",
        )

    def test_script_preparation_stage_errors_are_localized(self):
        self.assertEqual(
            localize_platform_error("脚本准备阶段不存在。", "en"),
            "Script-preparation stage not found.",
        )
        self.assertEqual(
            localize_platform_error("角色不存在。", "en"),
            "Role not found.",
        )

    def test_backend_chinese_exception_baseline_does_not_grow(self):
        paths = [APP_DIR / "app.py"]
        paths.extend(sorted((APP_DIR / "test_plan_viewer").rglob("*.py")))
        unregistered = []
        for path in paths:
            relative_path = path.relative_to(APP_DIR)
            if relative_path in BACKEND_SCAN_EXCLUSIONS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for pattern in raised_error_patterns(tree):
                if not platform_error_is_registered(pattern):
                    unregistered.append(
                        f"{relative_path.as_posix()}\t{pattern.strip()}"
                    )
        unregistered = sorted(set(unregistered))
        digest = hashlib.sha256(
            "\n".join(unregistered).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            (len(unregistered), digest),
            LEGACY_DOMAIN_ERROR_BASELINE,
            "The backend Chinese-exception baseline changed. Register new "
            "platform-owned messages in test_plan_viewer.i18n. If a changed "
            "message is intentionally preserved diagnostic content, review "
            "the entries and update the explicit baseline.\n"
            + "\n".join(unregistered[:20]),
        )


class CoverageProfileLocalizationTests(unittest.TestCase):
    def test_english_profiles_are_entirely_english_platform_copy(self):
        profiles = coverage_profiles_for_language("en")
        self.assertEqual(profiles["core"]["label"], "Core regression")
        for profile in profiles.values():
            self.assertNotRegex(profile["label"], r"[\u3400-\u9fff]")
            self.assertNotRegex(profile["description"], r"[\u3400-\u9fff]")
            self.assertNotRegex(profile["template_prompt"], r"[\u3400-\u9fff]")

    def test_chinese_profiles_remain_the_existing_copy(self):
        self.assertEqual(coverage_profiles_for_language("zh-CN")["core"]["label"], "核心回归")
