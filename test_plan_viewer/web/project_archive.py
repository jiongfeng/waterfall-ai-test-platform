"""HTTP delivery for project ZIP export and import."""

from dataclasses import dataclass
import re
from typing import Callable

from flask import Blueprint, jsonify, request, send_file


@dataclass(frozen=True)
class ProjectArchiveWebServices:
    """Project archive operations consumed by HTTP handlers."""

    build_project_export_zip: Callable
    import_project_archive: Callable
    current_export_timestamp: Callable[[], str]
    import_max_bytes: int


def export_project_response(services):
    """Return the current project as a downloadable ZIP."""

    try:
        buffer, manifest = (
            services.build_project_export_zip()
        )
        project_key = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "-",
            manifest["project"]["project_key"],
        ).strip(".-") or "project"
        timestamp = services.current_export_timestamp()
        return send_file(
            buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=(
                f"playwright-project-{project_key}-"
                f"{timestamp}.zip"
            ),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"导出项目失败：{exc}"}
        ), 500


def import_project_response(services):
    """Restore a project from one multipart ZIP upload."""

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(
            {"error": "请选择项目导入 zip 文件。"}
        ), 400

    try:
        archive_bytes = upload.read(
            services.import_max_bytes + 1
        )
        overrides = {
            key: str(request.form.get(key) or "").strip()
            for key in (
                "project_key",
                "name",
                "description",
                "specs_dir",
                "tests_dir",
            )
            if str(request.form.get(key) or "").strip()
        }
        result = services.import_project_archive(
            archive_bytes,
            overrides,
        )
        return jsonify(result), 201
    except ValueError as exc:
        status = 409 if "已存在" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except Exception as exc:
        return jsonify(
            {"error": f"导入项目失败：{exc}"}
        ), 500


def create_project_archive_blueprint(services):
    """Create project archive routes with injected services."""

    if not isinstance(
        services,
        ProjectArchiveWebServices,
    ):
        raise TypeError(
            "services must be a "
            "ProjectArchiveWebServices instance"
        )

    blueprint = Blueprint("project_archive", __name__)
    blueprint.add_url_rule(
        "/api/projects/export",
        view_func=lambda: export_project_response(services),
        methods=["GET"],
        endpoint="export_project",
    )
    blueprint.add_url_rule(
        "/api/projects/import",
        view_func=lambda: import_project_response(services),
        methods=["POST"],
        endpoint="import_project",
    )
    return blueprint
