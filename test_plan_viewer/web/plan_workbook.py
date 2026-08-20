"""HTTP delivery for test-plan XLSX import and export."""

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify, request, send_file

from test_plan_viewer.plans.workbook import PlanWorkbookConflict


PLAN_XLSX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@dataclass(frozen=True)
class PlanWorkbookWebServices:
    export_plans: Callable[[list], tuple]
    import_plans: Callable[[bytes, str], dict]
    upload_max_bytes: int


def export_plans_xlsx_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        buffer, filename = services.export_plans(payload.get("plans"))
        return send_file(
            buffer,
            mimetype=PLAN_XLSX_MIMETYPE,
            as_attachment=True,
            download_name=filename,
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (UnicodeDecodeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"导出测试计划失败：{exc}"}), 500


def import_plans_xlsx_response(services):
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "请选择测试计划 Excel 文件。"}), 400
    if not upload.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "只支持 .xlsx 文件。"}), 400
    try:
        data = upload.read(services.upload_max_bytes + 1)
        if len(data) > services.upload_max_bytes:
            return jsonify({"error": "导入 Excel 不能超过 20MB。"}), 413
        result = services.import_plans(
            data,
            str(request.form.get("conflict_policy") or "reject"),
        )
        return jsonify(result)
    except PlanWorkbookConflict as exc:
        return jsonify({"error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"导入测试计划失败：{exc}"}), 500


def create_plan_workbook_blueprint(services):
    if not isinstance(services, PlanWorkbookWebServices):
        raise TypeError("services must be PlanWorkbookWebServices")
    blueprint = Blueprint("plan_workbook", __name__)
    blueprint.add_url_rule(
        "/api/plans/export-xlsx",
        view_func=lambda: export_plans_xlsx_response(services),
        methods=["POST"],
        endpoint="export_plans_xlsx",
    )
    blueprint.add_url_rule(
        "/api/plans/import-xlsx",
        view_func=lambda: import_plans_xlsx_response(services),
        methods=["POST"],
        endpoint="import_plans_xlsx",
    )
    return blueprint


__all__ = ["PlanWorkbookWebServices", "create_plan_workbook_blueprint"]
