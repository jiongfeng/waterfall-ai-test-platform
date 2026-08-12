import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.web.requirements import (
    RequirementWebServices,
    create_requirements_blueprint,
)


def make_services(**overrides):
    requirement = {
        "id": 3,
        "requirement_uid": "requirement-3",
        "title": "需求",
        "filename": "需求.md",
        "file_path": "",
    }
    module = {
        "id": 5,
        "module_uid": "module-5",
        "module_name": "登录",
    }
    values = {
        "list_requirements": Mock(return_value=[requirement]),
        "serialize_requirement": Mock(
            side_effect=lambda row, **kwargs: {
                **row,
                **(
                    {"markdown": "# 需求", "html": "<h1>需求</h1>"}
                    if kwargs.get("include_content")
                    else {}
                ),
            }
        ),
        "create_requirement": Mock(return_value=requirement),
        "get_requirement": Mock(return_value=requirement),
        "delete_requirement": Mock(return_value=True),
        "list_modules": Mock(return_value=[module]),
        "get_module": Mock(return_value=module),
        "serialize_module": Mock(
            side_effect=lambda row: dict(row)
        ),
        "build_planner_prompt": Mock(
            return_value="reset prompt"
        ),
        "update_module": Mock(return_value=module),
        "delete_module": Mock(return_value=True),
    }
    values.update(overrides)
    return RequirementWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_requirements_blueprint(services)
    )
    return application


class RequirementBlueprintTests(unittest.TestCase):
    def test_list_upload_detail_and_delete_contracts(self):
        services = make_services()
        with make_app(services).test_client() as client:
            listed = client.get("/api/requirements")
            uploaded = client.post(
                "/api/requirements/upload",
                data={
                    "title": "上传标题",
                    "file": (
                        io.BytesIO(b"# Requirement"),
                        "requirement.md",
                    ),
                },
                content_type="multipart/form-data",
            )
            detail = client.get(
                "/api/requirements/requirement-3"
            )
            deleted = client.delete(
                "/api/requirements/requirement-3",
                json={"confirmation_name": "需求"},
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        uploaded_file = (
            services.create_requirement.call_args.args[0]
        )
        self.assertEqual(uploaded_file.filename, "requirement.md")
        self.assertEqual(
            services.create_requirement.call_args.kwargs,
            {"title": "上传标题"},
        )
        self.assertEqual(
            detail.json["modules"][0]["module_uid"],
            "module-5",
        )
        services.delete_requirement.assert_called_once_with(
            "requirement-3"
        )

    def test_delete_requires_exact_requirement_name(self):
        services = make_services()
        with make_app(services).test_client() as client:
            response = client.delete(
                "/api/requirements/requirement-3",
                json={"confirmation_name": "错误名称"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json["error"],
            "输入的需求名称不匹配。",
        )
        services.delete_requirement.assert_not_called()

    def test_module_routes_preserve_reset_and_not_found_behavior(self):
        services = make_services()
        with make_app(services).test_client() as client:
            listed = client.get(
                "/api/requirements/requirement-3/modules"
            )
            updated = client.put(
                (
                    "/api/requirements/requirement-3/"
                    "modules/module-5"
                ),
                json={
                    "module_name": "登录",
                    "reset_planner_prompt": True,
                },
            )
            deleted = client.delete(
                (
                    "/api/requirements/requirement-3/"
                    "modules/module-5"
                )
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        services.build_planner_prompt.assert_called_once()
        update_payload = services.update_module.call_args.args[2]
        self.assertEqual(
            update_payload["planner_prompt"],
            "reset prompt",
        )

        missing_services = make_services(
            get_requirement=Mock(return_value=None)
        )
        with make_app(missing_services).test_client() as client:
            missing = client.get(
                "/api/requirements/missing/modules"
            )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json["error"], "需求不存在。")

    def test_validation_and_operation_errors_keep_status_contracts(self):
        services = make_services(
            create_requirement=Mock(
                side_effect=ValueError("需求文件不能为空。")
            ),
            update_module=Mock(
                side_effect=ValueError("不支持的候选模块状态。")
            ),
            delete_module=Mock(
                side_effect=RuntimeError("database offline")
            ),
        )
        with make_app(services).test_client() as client:
            invalid_upload = client.post(
                "/api/requirements/upload"
            )
            invalid_module = client.put(
                (
                    "/api/requirements/requirement-3/"
                    "modules/module-5"
                ),
                json={"status": "invalid"},
            )
            failed_delete = client.delete(
                (
                    "/api/requirements/requirement-3/"
                    "modules/module-5"
                )
            )

        self.assertEqual(invalid_upload.status_code, 400)
        self.assertEqual(invalid_module.status_code, 400)
        self.assertEqual(failed_delete.status_code, 500)
        self.assertIn(
            "删除候选模块失败",
            failed_delete.json["error"],
        )

    def test_download_uses_attachment_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            requirement_file = Path(directory) / "需求.md"
            requirement_file.write_text(
                "# 需求",
                encoding="utf-8",
            )
            services = make_services(
                get_requirement=Mock(
                    return_value={
                        "id": 3,
                        "requirement_uid": "requirement-3",
                        "filename": "下载需求.md",
                        "file_path": str(requirement_file),
                    }
                )
            )
            with make_app(services).test_client() as client:
                response = client.get(
                    (
                        "/api/requirements/requirement-3/"
                        "download"
                    )
                )
                status_code = response.status_code
                response_text = response.data.decode("utf-8")
                disposition = response.headers[
                    "Content-Disposition"
                ]
                response.close()

        self.assertEqual(status_code, 200)
        self.assertEqual(response_text, "# 需求")
        self.assertIn(
            "attachment",
            disposition,
        )

    def test_stream_routes_are_not_registered_by_this_blueprint(self):
        application = make_app(make_services())
        paths = {rule.rule for rule in application.url_map.iter_rules()}

        self.assertNotIn(
            "/api/requirements/<requirement_uid>/analysis-stream",
            paths,
        )
        self.assertNotIn(
            (
                "/api/requirements/<requirement_uid>/modules/"
                "<module_uid>/generate-plan-stream"
            ),
            paths,
        )


if __name__ == "__main__":
    unittest.main()
