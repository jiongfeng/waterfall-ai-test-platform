"""HTTP routes for lightweight platform records."""

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify, request


EMPTY_RECORD_BUCKETS = {
    "view_state": {},
    "script_run_records": {},
    "script_repair_records": {},
    "module_execution_records": {},
    "module_repair_batches": {},
    "plan_generation_records": {},
    "requirement_plan_generation_batches": {},
    "script_generation_records": {},
    "test_suites": {},
    "test_suite_execution_records": {},
}


@dataclass(frozen=True)
class PlatformRecordServices:
    """Explicit application services consumed by this delivery module."""

    get_database_config: Callable[[], dict]
    load_records: Callable[[], dict]
    save_record: Callable[[str, str, dict], None]


def serialize_platform_record_buckets(buckets):
    """Convert repository buckets into the stable browser payload."""

    return {
        "view_state": buckets["view_state"].get("default") or {},
        "script_run_records": buckets["script_run_records"],
        "script_repair_records": buckets["script_repair_records"],
        "module_execution_records": buckets["module_execution_records"],
        "module_repair_batches": buckets["module_repair_batches"],
        "plan_generation_records": buckets["plan_generation_records"],
        "requirement_plan_generation_batches": buckets["requirement_plan_generation_batches"],
        "script_generation_records": buckets["script_generation_records"],
        "test_suites": buckets["test_suites"].get("default") or {},
        "test_suite_execution_records": buckets["test_suite_execution_records"],
    }


def create_platform_records_blueprint(services):
    """Create platform-record routes with their dependencies supplied by the app."""

    if not isinstance(services, PlatformRecordServices):
        raise TypeError("services must be a PlatformRecordServices instance")

    blueprint = Blueprint("platform_records", __name__)

    @blueprint.get("/api/platform-records")
    def get_platform_records():
        try:
            config = services.get_database_config()
            if not config.get("enabled"):
                return jsonify(
                    {
                        "enabled": False,
                        "records": dict(EMPTY_RECORD_BUCKETS),
                        "error": "未启用平台 MySQL 持久化，请在 config.json 配置 platform_database。",
                    }
                )

            buckets = services.load_records()
            return jsonify(
                {
                    "enabled": True,
                    "records": serialize_platform_record_buckets(buckets),
                    "error": None,
                }
            )
        except Exception as exc:
            return jsonify(
                {
                    "enabled": True,
                    "records": {},
                    "error": f"读取平台持久化记录失败：{exc}",
                }
            ), 500

    @blueprint.put("/api/platform-records/<bucket>/<path:record_key>")
    def save_platform_record(bucket, record_key):
        payload = request.get_json(silent=True) or {}
        record = payload.get("record")
        if not isinstance(record, dict):
            return jsonify({"error": "Request body must include record as an object."}), 400

        try:
            services.save_record(bucket, record_key, record)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"保存平台持久化记录失败：{exc}"}), 500

        return jsonify(
            {
                "ok": True,
                "bucket": bucket,
                "record_key": record_key,
                "error": None,
            }
        )

    return blueprint
