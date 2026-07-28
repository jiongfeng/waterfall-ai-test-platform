import io
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import app
from test_plan_viewer.web.project_archive import (
    ProjectArchiveWebServices,
    create_project_archive_blueprint,
)


def make_services(**overrides):
    result = {
        "project": {
            "project_key": "restored",
            "name": "恢复项目",
        },
        "counts": {
            "modules": 1,
            "plans": 1,
            "scripts": 1,
            "test_suites": 1,
            "suite_items": 1,
        },
        "warnings": [],
        "error": None,
    }
    values = {
        "build_project_export_zip": Mock(
            return_value=(
                io.BytesIO(b"zip-content"),
                {
                    "project": {
                        "project_key": "demo"
                    }
                },
            )
        ),
        "import_project_archive": Mock(
            return_value=result
        ),
        "current_export_timestamp": Mock(
            return_value="20260723-101112"
        ),
        "import_max_bytes": 200 * 1024 * 1024,
    }
    values.update(overrides)
    return ProjectArchiveWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_project_archive_blueprint(services)
    )
    return application


class ProjectArchiveBlueprintTests(unittest.TestCase):
    def test_routes_are_registered_with_stable_methods(self):
        application = make_app(make_services())
        contracts = {
            (method, rule.rule): rule.endpoint
            for rule in application.url_map.iter_rules()
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }

        self.assertEqual(
            contracts[("GET", "/api/projects/export")],
            "project_archive.export_project",
        )
        self.assertEqual(
            contracts[("POST", "/api/projects/import")],
            "project_archive.import_project",
        )

    def test_export_preserves_download_contract(self):
        services = make_services(
            build_project_export_zip=Mock(
                return_value=(
                    io.BytesIO(b"zip-content"),
                    {
                        "project": {
                            "project_key": (
                                "..demo key.."
                            )
                        }
                    },
                )
            )
        )

        response = make_app(services).test_client().get(
            "/api/projects/export"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"zip-content")
        self.assertEqual(
            response.mimetype,
            "application/zip",
        )
        self.assertIn(
            (
                "attachment; filename="
                "playwright-project-demo-key-"
                "20260723-101112.zip"
            ),
            response.headers["Content-Disposition"],
        )
        services.build_project_export_zip.assert_called_once_with()
        (
            services.current_export_timestamp
            .assert_called_once_with()
        )

    def test_import_preserves_overrides_read_limit_and_result(self):
        import_archive = Mock(
            return_value={
                "project": {"project_key": "override"},
                "counts": {},
                "warnings": [],
                "error": None,
            }
        )
        services = make_services(
            import_project_archive=import_archive,
            import_max_bytes=3,
        )

        response = make_app(services).test_client().post(
            "/api/projects/import",
            data={
                "file": (
                    io.BytesIO(b"12345"),
                    "project.zip",
                ),
                "project_key": " override ",
                "name": " 恢复项目 ",
                "description": " ",
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.get_json()["project"]["project_key"],
            "override",
        )
        import_archive.assert_called_once_with(
            b"1234",
            {
                "project_key": "override",
                "name": "恢复项目",
            },
        )

    def test_import_error_statuses_and_messages_are_preserved(self):
        missing = make_app(
            make_services()
        ).test_client().post(
            "/api/projects/import",
            data={},
        )
        duplicate = make_app(
            make_services(
                import_project_archive=Mock(
                    side_effect=ValueError(
                        "项目标识已存在：demo"
                    )
                )
            )
        ).test_client().post(
            "/api/projects/import",
            data={
                "file": (
                    io.BytesIO(b"zip"),
                    "project.zip",
                )
            },
            content_type="multipart/form-data",
        )
        invalid = make_app(
            make_services(
                import_project_archive=Mock(
                    side_effect=ValueError("导入文件不是合法 zip。")
                )
            )
        ).test_client().post(
            "/api/projects/import",
            data={
                "file": (
                    io.BytesIO(b"zip"),
                    "project.zip",
                )
            },
            content_type="multipart/form-data",
        )
        failed = make_app(
            make_services(
                import_project_archive=Mock(
                    side_effect=RuntimeError("database offline")
                )
            )
        ).test_client().post(
            "/api/projects/import",
            data={
                "file": (
                    io.BytesIO(b"zip"),
                    "project.zip",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(
            missing.get_json()["error"],
            "请选择项目导入 zip 文件。",
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(
            failed.get_json()["error"],
            "导入项目失败：database offline",
        )

    def test_export_error_statuses_and_messages_are_preserved(self):
        invalid = make_app(
            make_services(
                build_project_export_zip=Mock(
                    side_effect=ValueError("项目目录非法")
                )
            )
        ).test_client().get("/api/projects/export")
        failed = make_app(
            make_services(
                build_project_export_zip=Mock(
                    side_effect=RuntimeError(
                        "database offline"
                    )
                )
            )
        ).test_client().get("/api/projects/export")

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.get_json()["error"],
            "项目目录非法",
        )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(
            failed.get_json()["error"],
            "导出项目失败：database offline",
        )


class ProjectArchiveLegacyParityTests(unittest.TestCase):
    def test_isolated_export_matches_current_app_route_contract(self):
        manifest = {
            "project": {"project_key": "demo"}
        }
        isolated = make_app(
            make_services(
                build_project_export_zip=Mock(
                    return_value=(
                        io.BytesIO(b"same-zip"),
                        manifest,
                    )
                )
            )
        ).test_client().get("/api/projects/export")

        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "build_project_export_zip",
                return_value=(
                    io.BytesIO(b"same-zip"),
                    manifest,
                ),
            ),
            patch.object(
                app.time,
                "strftime",
                return_value="20260723-101112",
            ),
        ):
            legacy = app.app.test_client().get(
                "/api/projects/export"
            )

        self.assertEqual(
            isolated.status_code,
            legacy.status_code,
        )
        self.assertEqual(isolated.data, legacy.data)
        self.assertEqual(
            isolated.mimetype,
            legacy.mimetype,
        )
        self.assertEqual(
            isolated.headers["Content-Disposition"],
            legacy.headers["Content-Disposition"],
        )

    def test_isolated_import_matches_current_app_patch_contract(self):
        result = {
            "project": {"project_key": "override"},
            "counts": {
                "modules": 1,
                "plans": 1,
                "scripts": 1,
                "test_suites": 0,
                "suite_items": 0,
            },
            "warnings": [],
            "error": None,
        }
        isolated_import = Mock(return_value=result)
        isolated = make_app(
            make_services(
                import_project_archive=isolated_import
            )
        ).test_client().post(
            "/api/projects/import",
            data={
                "file": (
                    io.BytesIO(b"archive"),
                    "project.zip",
                ),
                "project_key": " override ",
            },
            content_type="multipart/form-data",
        )

        with (
            patch.object(
                app,
                "get_auth_config",
                return_value={"enabled": False},
            ),
            patch.object(
                app,
                "import_project_archive",
                return_value=result,
            ) as legacy_import,
        ):
            legacy = app.app.test_client().post(
                "/api/projects/import",
                data={
                    "file": (
                        io.BytesIO(b"archive"),
                        "project.zip",
                    ),
                    "project_key": " override ",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(
            isolated.status_code,
            legacy.status_code,
        )
        self.assertEqual(
            isolated.get_json(),
            legacy.get_json(),
        )
        isolated_import.assert_called_once_with(
            b"archive",
            {"project_key": "override"},
        )
        legacy_import.assert_called_once_with(
            b"archive",
            {"project_key": "override"},
        )


if __name__ == "__main__":
    unittest.main()
