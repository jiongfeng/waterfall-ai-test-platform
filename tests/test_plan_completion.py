import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_plan_viewer.generation.completion import PlanCompletionProbe


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def plan_markdown(cases):
    return f"```json\n{json.dumps({'cases': cases}, ensure_ascii=False)}\n```\n"


def valid_case(title="登录成功", filename="登录成功.md"):
    return {"title": title, "filename": filename, "steps": ["提交有效账号"]}


class PlanCompletionProbeTests(unittest.TestCase):
    def test_baseline_captures_existing_content_and_is_never_accepted_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "用例索引.md"
            content = plan_markdown([valid_case()])
            target.write_text(content, encoding="utf-8")
            clock = FakeClock()
            probe = PlanCompletionProbe(target, clock=clock)

            self.assertTrue(probe.baseline.exists)
            self.assertEqual(probe.baseline.size, len(content.encode("utf-8")))
            self.assertGreater(probe.baseline.mtime_ns, 0)
            self.assertEqual(
                probe.baseline.sha256,
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
            self.assertFalse(probe.check())
            clock.advance(5)
            self.assertFalse(probe.check())
            self.assertIn("not changed", probe.last_error)

    def test_new_plan_requires_two_stable_observations_at_least_half_a_second_apart(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "用例索引.md"
            clock = FakeClock()
            probe = PlanCompletionProbe(target, clock=clock)
            target.write_text(plan_markdown([valid_case()]), encoding="utf-8")

            self.assertFalse(probe.check())
            clock.advance(0.49)
            self.assertFalse(probe.check())
            clock.advance(0.01)
            self.assertTrue(probe.check())
            self.assertEqual(probe.cases[0]["filename"], "登录成功.md")
            self.assertEqual(probe.last_error, "")

    def test_english_plan_completes_with_english_case_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case-index.md"
            clock = FakeClock()
            probe = PlanCompletionProbe(
                target,
                clock=clock,
                language="en",
            )
            target.write_text(
                plan_markdown(
                    [
                        valid_case(
                            "Successful Login",
                            "successful-login.md",
                        ),
                        valid_case(
                            "Locked Account",
                            "locked-account.md",
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertFalse(probe.check())
            clock.advance(0.5)
            self.assertTrue(probe.check())
            self.assertEqual(probe.language, "en")
            self.assertEqual(
                [case["filename"] for case in probe.cases],
                ["successful-login.md", "locked-account.md"],
            )

    def test_english_normalization_rejects_titles_that_fall_back_to_same_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case-index.md"
            clock = FakeClock()
            probe = PlanCompletionProbe(
                target,
                clock=clock,
                language="en",
            )
            target.write_text(
                plan_markdown(
                    [
                        valid_case("Same Title", "中文一.md"),
                        valid_case("Same Title", "中文二.md"),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertFalse(probe.check())
            clock.advance(0.5)
            self.assertFalse(probe.check())
            self.assertIn("unsafe or duplicated", probe.last_error)

    def test_filesystem_equivalent_filenames_never_complete(self):
        scenarios = (
            ("en", "case-index.md", "Login.md", "login.md"),
            ("zh-CN", "用例索引.md", "ガ登录.md", "カ\u3099登录.md"),
        )
        for language, target_name, first, second in scenarios:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / target_name
                clock = FakeClock()
                probe = PlanCompletionProbe(
                    target,
                    clock=clock,
                    language=language,
                )
                target.write_text(
                    plan_markdown(
                        [
                            valid_case("First", first),
                            valid_case("Second", second),
                        ]
                    ),
                    encoding="utf-8",
                )

                self.assertFalse(probe.check())
                clock.advance(0.5)
                self.assertFalse(probe.check())
                self.assertIn("unsafe or duplicated", probe.last_error)

    def test_candidate_change_restarts_the_stability_window(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "用例索引.md"
            clock = FakeClock()
            probe = PlanCompletionProbe(target, clock=clock)
            target.write_text(plan_markdown([valid_case()]), encoding="utf-8")

            self.assertFalse(probe.check())
            clock.advance(0.4)
            target.write_text(
                plan_markdown([valid_case("登录失败", "登录失败.md")]),
                encoding="utf-8",
            )
            self.assertFalse(probe.check())
            clock.advance(0.49)
            self.assertFalse(probe.check())
            clock.advance(0.01)
            self.assertTrue(probe.check())
            self.assertEqual(probe.cases[0]["filename"], "登录失败.md")

    def test_existing_file_must_change_content_not_only_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "用例索引.md"
            content = plan_markdown([valid_case()])
            target.write_text(content, encoding="utf-8")
            clock = FakeClock()
            probe = PlanCompletionProbe(target, clock=clock)
            target.touch()

            self.assertFalse(probe.check())
            clock.advance(1)
            self.assertFalse(probe.check())

            target.write_text(
                plan_markdown([valid_case("登录失败", "登录失败.md")]),
                encoding="utf-8",
            )
            self.assertFalse(probe.check())
            clock.advance(0.5)
            self.assertTrue(probe.check())

    def test_empty_malformed_and_oversized_plans_never_complete(self):
        invalid_contents = {
            "empty": "",
            "malformed": "```json\n{not-json}\n```",
            "oversized": plan_markdown(
                [valid_case(f"用例{index}", f"用例{index}.md") for index in range(26)]
            ),
        }

        for label, content in invalid_contents.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "用例索引.md"
                clock = FakeClock()
                probe = PlanCompletionProbe(target, clock=clock)
                target.write_text(content, encoding="utf-8")

                self.assertFalse(probe.check())
                clock.advance(0.5)
                self.assertFalse(probe.check())
                self.assertTrue(probe.last_error)

    def test_english_probe_accepts_a_raw_path_that_split_normalizes_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case-index.md"
            clock = FakeClock()
            probe = PlanCompletionProbe(
                target,
                clock=clock,
                language="en",
            )
            target.write_text(
                plan_markdown(
                    [
                        valid_case(
                            "Checkout Flow",
                            "../checkout-flow.md",
                        )
                    ]
                ),
                encoding="utf-8",
            )

            self.assertFalse(probe.check())
            clock.advance(0.5)
            self.assertTrue(probe.check())
            self.assertEqual(probe.last_error, "")

    def test_mixed_or_source_only_case_entries_are_rejected(self):
        invalid_cases = [
            [valid_case(), "not-an-object"],
            [valid_case("索引", "用例索引.md")],
        ]
        for cases in invalid_cases:
            with self.subTest(cases=cases), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "用例索引.md"
                clock = FakeClock()
                probe = PlanCompletionProbe(target, clock=clock)
                target.write_text(plan_markdown(cases), encoding="utf-8")
                self.assertFalse(probe.check())
                clock.advance(0.5)
                self.assertFalse(probe.check())

    def test_duplicate_source_and_internal_filenames_reject_the_entire_plan(self):
        invalid_case_sets = {
            "duplicate": [
                valid_case(),
                valid_case("重复登录", "登录成功.md"),
            ],
            "source": [
                valid_case(),
                valid_case("索引", "用例索引.md"),
            ],
            "internal": [
                valid_case(),
                valid_case("内部计划", "_内部计划.md"),
            ],
        }

        for label, invalid_cases in invalid_case_sets.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "用例索引.md"
                clock = FakeClock()
                probe = PlanCompletionProbe(target, clock=clock)
                target.write_text(plan_markdown(invalid_cases), encoding="utf-8")

                self.assertFalse(probe.check())
                clock.advance(0.5)
                self.assertFalse(probe.check())
                self.assertIn("unsafe or duplicated", probe.last_error)


if __name__ == "__main__":
    unittest.main()
