import unittest
from io import BytesIO
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.plans.workbook import PlanWorkbookConflict
from test_plan_viewer.web.plan_workbook import (
    PLAN_XLSX_MIMETYPE,
    PlanWorkbookWebServices,
    create_plan_workbook_blueprint,
)


def make_services(**overrides):
    values = {
        "export_plans": Mock(
            return_value=(BytesIO(b"xlsx-data"), "测试计划-alpha-20260806.xlsx")
        ),
        "import_plans": Mock(
            return_value={
                "created": 1,
                "overwritten": 0,
                "skipped": 0,
                "total": 1,
                "items": [],
                "error": None,
            }
        ),
        "upload_max_bytes": 20 * 1024 * 1024,
    }
    values.update(overrides)
    return PlanWorkbookWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(create_plan_workbook_blueprint(services))
    return application


class PlanWorkbookBlueprintTests(unittest.TestCase):
    def test_export_returns_xlsx_download_and_passes_selected_plans(self):
        services = make_services()
        selection = [{"module_name": "模块", "plan_filename": "计划.md"}]

        with make_app(services).test_client() as client:
            response = client.post("/api/plans/export-xlsx", json={"plans": selection})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, PLAN_XLSX_MIMETYPE)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(".xlsx", response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"xlsx-data")
        services.export_plans.assert_called_once_with(selection)

    def test_export_maps_missing_invalid_and_unexpected_failures(self):
        cases = (
            (FileNotFoundError("missing"), 404, "missing"),
            (ValueError("invalid"), 400, "invalid"),
            (RuntimeError("broken"), 500, "导出测试计划失败"),
        )
        for failure, status, message in cases:
            with self.subTest(status=status):
                services = make_services(export_plans=Mock(side_effect=failure))
                with make_app(services).test_client() as client:
                    response = client.post("/api/plans/export-xlsx", json={"plans": []})
                self.assertEqual(response.status_code, status)
                self.assertIn(message, response.json["error"])

    def test_import_reads_multipart_file_and_conflict_policy(self):
        services = make_services()
        with make_app(services).test_client() as client:
            response = client.post(
                "/api/plans/import-xlsx",
                data={
                    "file": (BytesIO(b"workbook"), "plans.XLSX"),
                    "conflict_policy": "overwrite",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["created"], 1)
        services.import_plans.assert_called_once_with(b"workbook", "overwrite")

    def test_import_validates_file_extension_and_streamed_upload_limit(self):
        services = make_services(upload_max_bytes=4)
        with make_app(services).test_client() as client:
            missing = client.post("/api/plans/import-xlsx")
            wrong_type = client.post(
                "/api/plans/import-xlsx",
                data={"file": (BytesIO(b"data"), "plans.xls")},
                content_type="multipart/form-data",
            )
            too_large = client.post(
                "/api/plans/import-xlsx",
                data={"file": (BytesIO(b"12345"), "plans.xlsx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(wrong_type.status_code, 400)
        self.assertEqual(too_large.status_code, 413)
        services.import_plans.assert_not_called()

    def test_import_maps_conflict_validation_and_unexpected_failures(self):
        cases = (
            (PlanWorkbookConflict("exists"), 409, "exists"),
            (ValueError("invalid"), 400, "invalid"),
            (RuntimeError("broken"), 500, "导入测试计划失败"),
        )
        for failure, status, message in cases:
            with self.subTest(status=status):
                services = make_services(import_plans=Mock(side_effect=failure))
                with make_app(services).test_client() as client:
                    response = client.post(
                        "/api/plans/import-xlsx",
                        data={"file": (BytesIO(b"data"), "plans.xlsx")},
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, status)
                self.assertIn(message, response.json["error"])

    def test_blueprint_rejects_invalid_service_container(self):
        with self.assertRaises(TypeError):
            create_plan_workbook_blueprint(object())


if __name__ == "__main__":
    unittest.main()
