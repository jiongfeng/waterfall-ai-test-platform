import io
import json
import unittest
import zipfile

from test_plan_viewer.configuration import (
    parse_project_key,
    parse_project_path_segment,
)
from test_plan_viewer.projects import archive


def validate_module_name(value):
    value = str(value or "").strip()
    if not value or "/" in value or "\\" in value:
        raise ValueError("invalid module")
    return value


def validate_plan_filename(value):
    value = str(value or "").strip()
    if not value.endswith(".md") or "/" in value or "\\" in value:
        raise ValueError("invalid plan")
    return value


def validate_script_filename(value):
    value = str(value or "").strip()
    if (
        not value.endswith(".spec.ts")
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("invalid script")
    return value


def validate_suite_name(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("invalid suite")
    return value


def validate_suite_description(value):
    return str(value or "").strip()


DEPENDENCIES = archive.ArchiveValidationDependencies(
    validate_module_name=validate_module_name,
    validate_plan_filename=validate_plan_filename,
    validate_script_filename=validate_script_filename,
    parse_project_key=parse_project_key,
    parse_project_path_segment=parse_project_path_segment,
    validate_suite_name=validate_suite_name,
    validate_suite_description=validate_suite_description,
    strip_spec_suffix=lambda value: value.removesuffix(".spec.ts"),
)


def make_manifest():
    return {
        "format_version": 1,
        "project": {
            "project_key": "demo",
            "name": "演示项目",
            "specs_dir": "specs",
            "tests_dir": "tests",
        },
        "plans": [
            {
                "module_name": "登录",
                "filename": "登录.md",
                "path": "specs/登录/登录.md",
            }
        ],
        "scripts": [
            {
                "module_name": "登录",
                "filename": "登录.spec.ts",
                "path": "tests/登录/登录.spec.ts",
                "from_plan": {
                    "module_name": "登录",
                    "filename": "登录.md",
                },
            }
        ],
        "test_suites": [
            {
                "suite_uid": "suite-1",
                "name": "冒烟",
                "items": [
                    {
                        "module_name": "登录",
                        "filename": "登录.spec.ts",
                    }
                ],
            }
        ],
    }


def make_archive(manifest, extra_files=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                manifest,
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        bundle.writestr("specs/登录/登录.md", "# 登录")
        bundle.writestr(
            "tests/登录/登录.spec.ts",
            "test('登录', async () => {});",
        )
        for name, content in (extra_files or {}).items():
            bundle.writestr(name, content)
    return buffer.getvalue()


class ProjectArchiveTests(unittest.TestCase):
    def test_member_validation_rejects_traversal_and_foreign_roots(self):
        for name in (
            "../manifest.json",
            "specs/../secret.md",
            "/manifest.json",
            "other/file.md",
            "specs\\登录\\登录.md",
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    archive.validate_project_import_member_name(
                        name,
                        DEPENDENCIES,
                    )

    def test_valid_archive_preserves_plan_script_and_suite_relations(self):
        parsed = archive.parse_project_import_archive(
            make_archive(make_manifest()),
            DEPENDENCIES,
        )

        self.assertEqual(parsed["project"]["project_key"], "demo")
        self.assertEqual(parsed["modules"], [{"name": "登录"}])
        self.assertEqual(
            parsed["scripts"][0]["from_plan"],
            {
                "module_name": "登录",
                "filename": "登录.md",
            },
        )
        self.assertEqual(
            parsed["test_suites"][0]["items"][0],
            {
                "module_name": "登录",
                "filename": "登录.spec.ts",
                "display_name": "登录",
                "sort_order": 1,
            },
        )

    def test_archive_rejects_undeclared_asset_files(self):
        with self.assertRaisesRegex(
            ValueError,
            "未在 manifest 声明",
        ):
            archive.parse_project_import_archive(
                make_archive(
                    make_manifest(),
                    {"specs/登录/额外.md": "# undeclared"},
                ),
                DEPENDENCIES,
            )

    def test_archive_rejects_missing_manifest_and_invalid_zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("specs/登录/登录.md", "# 登录")

        with self.assertRaisesRegex(ValueError, "缺少 manifest"):
            archive.parse_project_import_archive(
                buffer.getvalue(),
                DEPENDENCIES,
            )
        with self.assertRaisesRegex(ValueError, "不是合法 zip"):
            archive.parse_project_import_archive(
                b"not a zip",
                DEPENDENCIES,
            )


if __name__ == "__main__":
    unittest.main()
