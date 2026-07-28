import json
import os
import random
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

from test_plan_viewer.execution import evidence, playwright, results


# These references preserve the small status-mapping rules from app.py at the
# extraction boundary. Randomized parity therefore remains independent of git
# and of future compatibility wrappers in app.py.
def reference_db_result_status(status):
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


def reference_db_run_status(status):
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


def reference_build_execution_summary(script_results, returncode=None):
    counts = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "unknown": 0,
    }
    for status in (script_results or {}).values():
        normalized = reference_db_result_status(status)
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


def make_evidence_dependencies(project_root):
    resolved_root = project_root.resolve(strict=False)
    return evidence.EvidenceDependencies(
        get_project_root=lambda: project_root,
        get_project_relative_path=(
            lambda path: path.resolve(strict=False).relative_to(
                resolved_root
            )
        ),
        resolve_path=lambda path: Path(path).resolve(strict=False),
        path_exists=lambda path: path.exists(),
        path_is_file=lambda path: path.is_file(),
        path_is_dir=lambda path: path.is_dir(),
        stat_path=lambda path: path.stat(),
        rglob=lambda path, pattern: path.rglob(pattern),
    )


def make_playwright_dependencies(npx="npx"):
    return playwright.PlaywrightDependencies(
        path_is_file=lambda path: path.is_file(),
        get_npx_executable=lambda: npx,
    )


def make_result_dependencies(project_root):
    return results.ResultDependencies(
        get_project_root=lambda: project_root,
        get_script_test_relative_path=(
            lambda module_name, filename: (
                f"tests/{module_name}/{filename}"
            )
        ),
        resolve_path=lambda path: Path(path).resolve(strict=False),
        read_text=lambda path: path.read_text(encoding="utf-8"),
    )


def set_modified_time(path, modified_at):
    os.utime(path, (modified_at, modified_at))


class EvidencePathTests(unittest.TestCase):
    def test_video_and_report_paths_are_confined_to_their_roots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            project_root = workspace / "project"
            project_root.mkdir()
            dependencies = make_evidence_dependencies(project_root)

            valid_video = evidence.get_run_video_file(
                "test-results/运行/video.WEBM",
                dependencies,
            )
            valid_report = evidence.get_playwright_report_file(
                "playwright-report/index.html",
                dependencies,
            )

            self.assertEqual(
                valid_video,
                (
                    project_root
                    / "test-results"
                    / "运行"
                    / "video.WEBM"
                ).resolve(strict=False),
            )
            self.assertEqual(
                valid_report,
                (
                    project_root
                    / "playwright-report"
                    / "index.html"
                ).resolve(strict=False),
            )

            with self.assertRaisesRegex(ValueError, "outside project root"):
                evidence.get_run_video_file(
                    "../outside.mp4",
                    dependencies,
                )
            with self.assertRaisesRegex(
                ValueError,
                "outside playwright-report",
            ):
                evidence.get_playwright_report_file(
                    "test-results/report.html",
                    dependencies,
                )
            with self.assertRaisesRegex(
                ValueError,
                "Unsupported video",
            ):
                evidence.get_run_video_file(
                    "test-results/output.zip",
                    dependencies,
                )
            with self.assertRaisesRegex(ValueError, "Invalid"):
                evidence.get_run_video_file(
                    "bad\x00video.webm",
                    dependencies,
                )

    def test_symlink_escape_is_rejected_after_resolution(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            project_root = workspace / "project"
            outside = workspace / "outside"
            project_root.mkdir()
            outside.mkdir()
            (project_root / "linked").symlink_to(
                outside,
                target_is_directory=True,
            )
            (project_root / "playwright-report").mkdir()
            (
                project_root
                / "playwright-report"
                / "linked"
            ).symlink_to(
                outside,
                target_is_directory=True,
            )
            dependencies = make_evidence_dependencies(project_root)

            with self.assertRaisesRegex(ValueError, "outside project root"):
                evidence.get_run_video_file(
                    "linked/video.mp4",
                    dependencies,
                )
            with self.assertRaisesRegex(
                ValueError,
                "outside playwright-report",
            ):
                evidence.get_playwright_report_file(
                    "playwright-report/linked/index.html",
                    dependencies,
                )

    def test_serializers_keep_encoded_urls_and_file_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            video = (
                project_root
                / "test-results"
                / "登录 流程"
                / "video.webm"
            )
            report = project_root / "playwright-report" / "index.html"
            video.parent.mkdir(parents=True)
            report.parent.mkdir(parents=True)
            video.write_bytes(b"video")
            report.write_bytes(b"report")
            set_modified_time(video, 101.5)
            set_modified_time(report, 102.5)
            dependencies = make_evidence_dependencies(project_root)

            serialized_video = evidence.serialize_run_video(
                video,
                dependencies,
            )
            serialized_report = evidence.serialize_playwright_report(
                report,
                dependencies,
            )

            self.assertEqual(serialized_video["size"], 5)
            self.assertEqual(serialized_video["mtime"], 101.5)
            self.assertEqual(
                serialized_video["relative_path"],
                "test-results/登录 流程/video.webm",
            )
            self.assertEqual(
                serialized_video["url"],
                (
                    "/api/run-videos/test-results/"
                    "%E7%99%BB%E5%BD%95%20%E6%B5%81%E7%A8%8B/video.webm"
                ),
            )
            self.assertEqual(serialized_report["size"], 6)
            self.assertEqual(
                serialized_report["url"],
                "/api/playwright-reports/playwright-report/index.html",
            )


class EvidenceSelectionTests(unittest.TestCase):
    def test_latest_current_video_and_report_are_selected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            results_dir = project_root / "custom-results"
            report_dir = project_root / "custom-report"
            results_dir.mkdir()
            report_dir.mkdir()
            old_video = results_dir / "old.webm"
            current_video = results_dir / "nested" / "current.mp4"
            newest_video = results_dir / "nested" / "newest.webm"
            current_video.parent.mkdir()
            for path in (old_video, current_video, newest_video):
                path.write_bytes(path.name.encode("utf-8"))
            report_file = report_dir / "index.html"
            report_file.write_text("report", encoding="utf-8")
            set_modified_time(old_video, 97)
            set_modified_time(current_video, 100)
            set_modified_time(newest_video, 103)
            set_modified_time(report_file, 101)
            dependencies = make_evidence_dependencies(project_root)

            selected_video = evidence.find_latest_run_video(
                100,
                dependencies,
                results_dir,
            )
            selected_report = evidence.find_latest_playwright_report(
                100,
                dependencies,
                report_dir,
            )

            self.assertEqual(selected_video, newest_video)
            self.assertEqual(selected_report, report_file)
            video_result = evidence.build_run_video_result(
                100,
                dependencies,
                results_dir,
            )
            report_result = evidence.build_playwright_report_result(
                100,
                dependencies,
                report_dir,
            )
            self.assertEqual(
                video_result["video"]["relative_path"],
                "custom-results/nested/newest.webm",
            )
            self.assertIsNone(video_result["video_error"])
            self.assertEqual(
                report_result["report"]["relative_path"],
                "custom-report/index.html",
            )
            self.assertIsNone(report_result["report_error"])

    def test_stale_or_missing_artifacts_return_the_legacy_messages(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            results_dir = project_root / "test-results"
            report_dir = project_root / "playwright-report"
            results_dir.mkdir()
            report_dir.mkdir()
            stale_video = results_dir / "video.webm"
            stale_report = report_dir / "index.html"
            stale_video.write_bytes(b"video")
            stale_report.write_text("report", encoding="utf-8")
            set_modified_time(stale_video, 10)
            set_modified_time(stale_report, 10)
            dependencies = make_evidence_dependencies(project_root)

            video_result = evidence.build_run_video_result(
                20,
                dependencies,
            )
            report_result = evidence.build_playwright_report_result(
                20,
                dependencies,
            )

            self.assertIsNone(video_result["video"])
            self.assertIn("未找到本次执行视频", video_result["video_error"])
            self.assertIsNone(report_result["report"])
            self.assertIn(
                "未找到本次 Playwright HTML report",
                report_result["report_error"],
            )

    def test_evidence_builder_converts_filesystem_errors_to_payload_errors(self):
        project_root = Path("/virtual/project")
        dependencies = evidence.EvidenceDependencies(
            get_project_root=lambda: project_root,
            get_project_relative_path=lambda path: path,
            resolve_path=lambda path: Path(path),
            path_exists=lambda _path: True,
            path_is_file=lambda _path: True,
            path_is_dir=lambda _path: True,
            stat_path=lambda _path: (_ for _ in ()).throw(
                OSError("stat failed")
            ),
            rglob=lambda _path, _pattern: (_ for _ in ()).throw(
                OSError("scan failed")
            ),
        )

        video_result = evidence.build_run_video_result(
            1,
            dependencies,
        )
        report_result = evidence.build_playwright_report_result(
            1,
            dependencies,
        )

        self.assertIn("读取本次执行视频失败", video_result["video_error"])
        self.assertIsNone(report_result["report"])
        self.assertIn(
            "未找到本次 Playwright HTML report",
            report_result["report_error"],
        )


class PlaywrightCommandTests(unittest.TestCase):
    def test_config_discovery_uses_the_legacy_precedence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            (project_root / "playwright.config.js").write_text(
                "module.exports = {}",
                encoding="utf-8",
            )
            (project_root / "playwright.config.mts").write_text(
                "export default {}",
                encoding="utf-8",
            )
            dependencies = make_playwright_dependencies()

            self.assertEqual(
                playwright.find_playwright_config(
                    project_root,
                    dependencies,
                ),
                project_root / "playwright.config.mts",
            )

    def test_config_import_path_strips_typescript_suffix_only(self):
        root = Path("/workspace/project")
        self.assertEqual(
            playwright.get_config_import_path(
                root / "config" / "base.config.mts",
                root,
            ),
            "./config/base.config",
        )
        self.assertEqual(
            playwright.get_config_import_path(
                root / "playwright.config.js",
                root,
            ),
            "./playwright.config.js",
        )
        with self.assertRaises(ValueError):
            playwright.get_config_import_path(
                Path("/outside/playwright.config.ts"),
                root,
            )

    def test_windows_paths_are_preserved_in_command_and_quoted_for_display(self):
        npx = r"C:\Program Files\nodejs\npx.cmd"
        config = PureWindowsPath(
            r"C:\Work Space\项目\playwright.config.ts"
        )
        script = r"tests\登录 模块\登录.spec.ts"
        blob_dir = PureWindowsPath(
            r"C:\Work Space\项目\blob reports"
        )
        dependencies = make_playwright_dependencies(npx)

        command, display = playwright.build_playwright_test_command(
            config,
            [script],
            dependencies,
        )
        merge_command, merge_display = (
            playwright.build_playwright_merge_reports_command(
                config,
                blob_dir,
                dependencies,
            )
        )

        self.assertEqual(
            command,
            [
                npx,
                "playwright",
                "test",
                "--config",
                str(config),
                "--trace=on",
                script,
            ],
        )
        self.assertEqual(
            display,
            (
                'npx playwright test --config '
                '"C:\\\\Work Space\\\\项目\\\\playwright.config.ts" '
                '--trace=on "tests\\\\登录 模块\\\\登录.spec.ts"'
            ),
        )
        self.assertEqual(merge_command[0], npx)
        self.assertEqual(
            merge_display,
            (
                'npx playwright merge-reports --config '
                '"C:\\\\Work Space\\\\项目\\\\playwright.config.ts" '
                '"C:\\\\Work Space\\\\项目\\\\blob reports"'
            ),
        )

    def test_command_argument_quoting_matches_the_original_edge_cases(self):
        values = {
            "": '""',
            "plain": "plain",
            "中文路径": "中文路径",
            "two words": '"two words"',
            "tab\tvalue": '"tab\\tvalue"',
        }
        for value, expected in values.items():
            with self.subTest(value=value):
                self.assertEqual(
                    playwright.quote_command_argument(value),
                    expected,
                )


class ExecutionStatusParityTests(unittest.TestCase):
    def test_status_and_summary_helpers_have_seeded_random_parity(self):
        generator = random.Random(20260723)
        status_values = (
            None,
            "",
            "succeeded",
            "passed",
            "failed",
            "skipped",
            "timed_out",
            "interrupted",
            "unknown",
            "cancelled",
            "running",
            "queued",
            "unexpected-value",
            0,
            1,
        )

        for sample_index in range(400):
            status = generator.choice(status_values)
            with self.subTest(
                sample_index=sample_index,
                status=status,
            ):
                self.assertEqual(
                    results.db_result_status(status),
                    reference_db_result_status(status),
                )
                self.assertEqual(
                    results.db_run_status(status),
                    reference_db_run_status(status),
                )

            script_results = {
                f"脚本{index}.spec.ts": generator.choice(status_values)
                for index in range(generator.randint(0, 15))
            }
            returncode = generator.choice([None, 0, 1, 137])
            self.assertEqual(
                results.build_execution_summary(
                    script_results,
                    returncode,
                ),
                reference_build_execution_summary(
                    script_results,
                    returncode,
                ),
            )

    def test_execution_modes_and_database_reset_modes_are_stable(self):
        self.assertEqual(
            results.normalize_execution_mode(None),
            results.EXECUTION_MODE_BATCH,
        )
        self.assertEqual(
            results.normalize_execution_mode("  "),
            results.EXECUTION_MODE_BATCH,
        )
        self.assertEqual(
            results.normalize_execution_mode("serial_per_file"),
            results.EXECUTION_MODE_SERIAL_PER_FILE,
        )
        with self.assertRaisesRegex(ValueError, "execution_mode"):
            results.normalize_execution_mode("parallel")

        self.assertEqual(
            results.db_execution_mode("serial_per_file"),
            "serial_per_file",
        )
        self.assertEqual(
            results.db_execution_mode("batch"),
            "batch_once",
        )
        self.assertEqual(
            results.execution_database_reset_mode("serial_per_file"),
            "before_each_file",
        )
        self.assertEqual(
            results.execution_database_reset_mode("batch"),
            "once_per_run",
        )
        self.assertEqual(
            results.get_execution_mode_label("serial_per_file"),
            "按文件串行执行",
        )
        self.assertEqual(
            results.get_execution_mode_label("batch"),
            "当前批量执行",
        )

    def test_error_finalization_preserves_only_completed_statuses(self):
        finalized = results.finalize_script_results_after_error(
            ["成功", "失败", "跳过", "运行中", "缺失"],
            {
                "成功": "succeeded",
                "失败": "failed",
                "跳过": "skipped",
                "运行中": "running",
            },
            unresolved_status="interrupted",
        )

        self.assertEqual(
            finalized,
            {
                "成功": "succeeded",
                "失败": "failed",
                "跳过": "skipped",
                "运行中": "interrupted",
                "缺失": "interrupted",
            },
        )
        self.assertEqual(
            results.format_script_result_summary(
                {
                    "a": "succeeded",
                    "b": "failed",
                    "c": "unknown",
                    "d": "interrupted",
                }
            ),
            "成功 1 个，失败 1 个，未解析 2 个",
        )

    def test_playwright_failure_status_rules_are_preserved(self):
        cases = [
            ({"status": "expected"}, False),
            ({"status": "unexpected"}, True),
            ({"status": "flaky"}, False),
            ({"status": "failed", "expectedStatus": "failed"}, False),
            ({"status": "passed", "expectedStatus": "failed"}, True),
            ({"status": "timedOut"}, True),
            (
                {
                    "status": "custom",
                    "results": [{"status": "interrupted"}],
                },
                True,
            ),
            ({"status": "custom", "results": [None]}, False),
            (None, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    results.is_playwright_test_failed(value),
                    expected,
                )


class PlaywrightJsonResultTests(unittest.TestCase):
    def test_missing_and_damaged_json_use_the_fallback_status(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            dependencies = make_result_dependencies(project_root)
            missing = project_root / "missing.json"
            damaged = project_root / "damaged.json"
            damaged.write_text("{broken", encoding="utf-8")

            for report_file in (missing, damaged):
                with self.subTest(report_file=report_file):
                    self.assertEqual(
                        results.parse_playwright_json_script_results(
                            report_file,
                            "登录",
                            ["成功.spec.ts", "失败.spec.ts"],
                            "failed",
                            dependencies,
                        ),
                        {
                            "成功.spec.ts": "failed",
                            "失败.spec.ts": "failed",
                        },
                    )
                    self.assertEqual(
                        (
                            results
                            .parse_playwright_json_relative_script_results(
                                report_file,
                                {
                                    "tests/登录/成功.spec.ts": "成功",
                                    "tests/登录/失败.spec.ts": "失败",
                                },
                                "interrupted",
                                dependencies,
                            )
                        ),
                        {
                            "成功": "interrupted",
                            "失败": "interrupted",
                        },
                    )

    def test_duplicate_report_entries_keep_failure_sticky(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            report_file = project_root / "report.json"
            report_file.write_text(
                json.dumps(
                    {
                        "suites": [
                            {
                                "file": "tests/登录/重复.spec.ts",
                                "specs": [
                                    {
                                        "ok": True,
                                        "tests": [
                                            {"status": "expected"}
                                        ],
                                    }
                                ],
                            },
                            {
                                "file": "tests/登录/重复.spec.ts",
                                "specs": [
                                    {
                                        "ok": False,
                                        "tests": [
                                            {"status": "unexpected"}
                                        ],
                                    }
                                ],
                            },
                            {
                                "file": "tests/登录/重复.spec.ts",
                                "specs": [
                                    {
                                        "ok": True,
                                        "tests": [
                                            {"status": "expected"}
                                        ],
                                    }
                                ],
                            },
                            {
                                "file": "tests/登录/通过.spec.ts",
                                "specs": [
                                    {
                                        "ok": True,
                                        "tests": [
                                            {"status": "expected"}
                                        ],
                                    }
                                ],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dependencies = make_result_dependencies(project_root)

            parsed = results.parse_playwright_json_script_results(
                report_file,
                "登录",
                [
                    "重复.spec.ts",
                    "通过.spec.ts",
                    "未出现.spec.ts",
                    "重复.spec.ts",
                ],
                "failed",
                dependencies,
            )

            self.assertEqual(
                parsed,
                {
                    "重复.spec.ts": "failed",
                    "通过.spec.ts": "succeeded",
                    "未出现.spec.ts": "unknown",
                },
            )

    def test_relative_results_normalize_backslashes_and_nested_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            report_file = project_root / "report.json"
            absolute_script = (
                project_root
                / "tests"
                / "模块A"
                / "通过.spec.ts"
            )
            report_file.write_text(
                json.dumps(
                    {
                        "suites": [
                            {
                                "file": str(absolute_script),
                                "suites": [
                                    {
                                        "specs": [
                                            {
                                                "ok": True,
                                                "tests": [],
                                            }
                                        ]
                                    }
                                ],
                            },
                            {
                                "file": (
                                    "tests\\模块B\\失败.spec.ts"
                                ),
                                "specs": [
                                    {
                                        "ok": False,
                                        "tests": [],
                                    }
                                ],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dependencies = make_result_dependencies(project_root)

            parsed = (
                results.parse_playwright_json_relative_script_results(
                    report_file,
                    {
                        "./tests/模块A/通过.spec.ts": "模块A/通过",
                        "tests\\模块B\\失败.spec.ts": "模块B/失败",
                    },
                    "failed",
                    dependencies,
                )
            )

            self.assertEqual(
                parsed,
                {
                    "模块A/通过": "succeeded",
                    "模块B/失败": "failed",
                },
            )

    def test_random_duplicate_specs_match_the_expected_aggregation(self):
        generator = random.Random(11768)
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            report_file = project_root / "report.json"
            dependencies = make_result_dependencies(project_root)
            filenames = [
                f"随机脚本{index}.spec.ts"
                for index in range(8)
            ]

            for sample_index in range(150):
                suites = []
                outcomes = {filename: [] for filename in filenames}
                for filename in filenames:
                    for _ in range(generator.randint(0, 3)):
                        succeeded = generator.choice([True, False])
                        outcomes[filename].append(succeeded)
                        raw_file = (
                            f"tests/随机模块/{filename}"
                            if generator.choice([True, False])
                            else (
                                f"prefix/tests/随机模块/{filename}"
                            )
                        )
                        suites.append(
                            {
                                "file": raw_file,
                                "specs": [
                                    {
                                        "ok": succeeded,
                                        "tests": [],
                                    }
                                ],
                            }
                        )
                generator.shuffle(suites)
                report_file.write_text(
                    json.dumps(
                        {"suites": suites},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                fallback = generator.choice(
                    ["failed", "succeeded"]
                )
                resolved_count = sum(
                    bool(items)
                    for items in outcomes.values()
                )
                expected = {}
                for filename, items in outcomes.items():
                    if any(item is False for item in items):
                        expected[filename] = "failed"
                    elif items:
                        expected[filename] = "succeeded"
                    elif fallback == "succeeded" or resolved_count == 0:
                        expected[filename] = fallback
                    else:
                        expected[filename] = "unknown"

                with self.subTest(sample_index=sample_index):
                    self.assertEqual(
                        results.parse_playwright_json_script_results(
                            report_file,
                            "随机模块",
                            filenames,
                            fallback,
                            dependencies,
                        ),
                        expected,
                    )


class ExecutionBoundaryTests(unittest.TestCase):
    def test_execution_package_has_no_app_or_flask_imports(self):
        package_dir = (
            Path(__file__).resolve().parents[1]
            / "test_plan_viewer"
            / "execution"
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
