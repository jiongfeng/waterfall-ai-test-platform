import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from test_plan_viewer.artifacts import naming, paths, snapshots


class ArtifactNamingTests(unittest.TestCase):
    def test_module_and_filename_validation_rejects_path_traversal(self):
        for module_name in ("", " 登录", ".", "..", "../登录", "登录/查询", "登录\\查询", "登录\x00"):
            with self.subTest(module_name=module_name), self.assertRaises(ValueError):
                naming.validate_module_name(module_name)

        for filename in ("../计划.md", "子目录/计划.md", "计划\\副本.md", "计划.txt", "计划.md\x00"):
            with self.subTest(plan_filename=filename), self.assertRaises(ValueError):
                naming.validate_plan_filename(filename)

        for filename in (
            "../脚本.spec.ts",
            "子目录/脚本.spec.ts",
            "脚本\\副本.spec.ts",
            "脚本.ts",
            "脚本.spec.ts\x00",
        ):
            with self.subTest(script_filename=filename), self.assertRaises(ValueError):
                naming.validate_script_filename(filename)

    def test_chinese_artifact_generation_is_stable_and_suffix_aware(self):
        self.assertEqual(
            naming.get_generated_script_filename_from_plan_filename("登录流程.md"),
            "登录流程.spec.ts",
        )

        generated = naming.get_generated_script_filename_from_plan_filename(
            "login-flow.md",
        )
        self.assertRegex(generated, r"^测试脚本-\d{6}\.spec\.ts$")
        self.assertEqual(
            generated,
            naming.get_generated_script_filename_from_plan_filename(
                "login-flow.md",
            ),
        )

        self.assertTrue(naming.is_plan_index_filename("_内部.md"))
        self.assertTrue(naming.is_plan_index_filename("登录-用例索引.md"))
        self.assertFalse(naming.is_plan_index_filename("登录流程.md"))


class ArtifactPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        self.specs_dir = self.workspace / "specs"
        self.tests_dir = self.workspace / "tests"
        self.specs_dir.mkdir()
        self.tests_dir.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_valid_plan_and_script_paths_keep_the_configured_roots(self):
        self.assertEqual(
            paths.build_plan_file(self.specs_dir, "登录", "正向流程.md"),
            self.specs_dir / "登录" / "正向流程.md",
        )
        self.assertEqual(
            paths.build_script_file(
                self.tests_dir,
                "登录",
                "正向流程.spec.ts",
            ),
            self.tests_dir / "登录" / "正向流程.spec.ts",
        )

    def test_path_builders_reject_direct_traversal_and_symlink_escape(self):
        with self.assertRaisesRegex(ValueError, "outside specs directory"):
            paths.build_plan_file(self.specs_dir, "..", "逃逸.md")

        with self.assertRaisesRegex(ValueError, "outside tests directory"):
            paths.build_script_file(
                self.tests_dir,
                "../escape",
                "逃逸.spec.ts",
            )

        outside_dir = self.workspace / "outside"
        outside_dir.mkdir()
        (self.specs_dir / "linked").symlink_to(
            outside_dir,
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "outside specs directory"):
            paths.build_plan_file(self.specs_dir, "linked", "逃逸.md")

    def test_generation_job_id_is_sanitized_inside_candidate_root(self):
        candidate_root = self.workspace / "generation" / "candidates"
        candidate = paths.build_script_generation_candidate_file(
            candidate_root,
            "登录",
            "流程.spec.ts",
            "../../../../evil / job",
        )

        self.assertEqual(
            candidate.relative_to(candidate_root).parts,
            ("evil-job", "登录", "流程.spec.ts"),
        )

    def test_workspace_relative_path_rejects_an_outside_file(self):
        self.assertEqual(
            paths.workspace_relative_path(
                self.workspace,
                self.workspace / "tests" / "登录" / "流程.spec.ts",
            ),
            "tests/登录/流程.spec.ts",
        )
        with self.assertRaises(ValueError):
            paths.workspace_relative_path(
                self.workspace,
                self.workspace.parent / "outside.spec.ts",
            )


class ArtifactSnapshotTests(unittest.TestCase):
    def test_snapshot_distinguishes_existing_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            existing = root / "计划.md"
            missing = root / "缺失.md"
            existing.write_bytes(b"abc")

            snapshot = snapshots.managed_file_snapshot([existing, missing])

            existing_item = snapshot[str(existing.resolve())]
            self.assertTrue(existing_item["exists"])
            self.assertEqual(existing_item["content"], b"abc")
            self.assertEqual(
                existing_item["hash"],
                "ba7816bf8f01cfea414140de5dae2223"
                "b00361a396177a9cb410ff61f20015ad",
            )
            missing_item = snapshot[str(missing.resolve())]
            self.assertFalse(missing_item["exists"])
            self.assertEqual(missing_item["hash"], "")
            self.assertEqual(snapshots.file_hash(missing), "")

    def test_managed_file_discovery_only_yields_supported_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            specs_dir = root / "specs"
            tests_dir = root / "tests"
            specs_dir.mkdir()
            tests_dir.mkdir()
            (specs_dir / "计划.md").write_text("plan", encoding="utf-8")
            (specs_dir / "忽略.txt").write_text("ignore", encoding="utf-8")
            (tests_dir / "脚本.spec.ts").write_text("test", encoding="utf-8")
            (tests_dir / "忽略.ts").write_text("ignore", encoding="utf-8")

            files = set(
                snapshots.iter_generation_managed_files(
                    specs_dir,
                    tests_dir,
                )
            )

            self.assertEqual(
                files,
                {specs_dir / "计划.md", tests_dir / "脚本.spec.ts"},
            )


class AppArtifactCompatibilityTests(unittest.TestCase):
    def test_path_wrapper_uses_runtime_app_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            specs_dir = Path(temporary_directory) / "custom-specs"
            specs_dir.mkdir()
            with (
                patch.object(app, "get_specs_dir", return_value=specs_dir),
                patch.object(
                    app,
                    "validate_module_name",
                    return_value="替换模块",
                ) as validate_module,
            ):
                plan_file = app.get_plan_file("原模块", "计划.md")

            validate_module.assert_called_once_with("原模块")
            self.assertEqual(
                plan_file,
                specs_dir / "替换模块" / "计划.md",
            )

    def test_snapshot_wrapper_uses_patchable_app_read_and_hash_helpers(self):
        path = Path("/virtual/计划.md")
        with (
            patch.object(app, "read_file_bytes", return_value=b"patched"),
            patch.object(app, "sha256_bytes", return_value="patched-hash"),
        ):
            snapshot = app.managed_file_snapshot([path])

        item = next(iter(snapshot.values()))
        self.assertEqual(item["content"], b"patched")
        self.assertEqual(item["hash"], "patched-hash")


if __name__ == "__main__":
    unittest.main()
