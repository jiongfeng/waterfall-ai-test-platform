"""HTTP delivery for persisted test-suite CRUD operations."""

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify, request


@dataclass(frozen=True)
class TestSuiteWebServices:
    """Application services consumed by test-suite HTTP handlers."""

    list_test_suites: Callable[[], list]
    create_test_suite: Callable[[object, object], dict]
    get_test_suite: Callable[[str], dict]
    update_test_suite: Callable[..., dict]
    delete_test_suite: Callable[[str], bool]
    add_test_suite_items: Callable[[str, object], dict]
    delete_test_suite_item: Callable[[str, int], dict]
    reorder_test_suite_items: Callable[[str, object], dict]


def list_test_suites_response(services):
    try:
        return jsonify(
            {
                "suites": services.list_test_suites(),
                "error": None,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "suites": [],
                "error": f"读取测试集失败：{exc}",
            }
        ), 500


def create_test_suite_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        suite = services.create_test_suite(
            payload.get("name"),
            payload.get("description", ""),
        )
        return jsonify({"suite": suite, "error": None}), 201
    except ValueError as exc:
        status = 409 if "重复" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except Exception as exc:
        return jsonify(
            {"error": f"创建测试集失败：{exc}"}
        ), 500


def get_test_suite_response(services, suite_uid):
    try:
        suite = services.get_test_suite(suite_uid)
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"suite": suite, "error": None})
    except Exception as exc:
        return jsonify(
            {"error": f"读取测试集失败：{exc}"}
        ), 500


def update_test_suite_response(services, suite_uid):
    payload = request.get_json(silent=True) or {}
    try:
        suite = services.update_test_suite(
            suite_uid,
            name=(
                payload.get("name")
                if "name" in payload
                else None
            ),
            description=(
                payload.get("description")
                if "description" in payload
                else None
            ),
        )
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"suite": suite, "error": None})
    except ValueError as exc:
        status = 409 if "重复" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except Exception as exc:
        return jsonify(
            {"error": f"更新测试集失败：{exc}"}
        ), 500


def delete_test_suite_response(services, suite_uid):
    try:
        deleted = services.delete_test_suite(suite_uid)
        if not deleted:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"ok": True, "error": None})
    except Exception as exc:
        return jsonify(
            {"error": f"删除测试集失败：{exc}"}
        ), 500


def add_test_suite_items_response(services, suite_uid):
    payload = request.get_json(silent=True) or {}
    try:
        suite = services.add_test_suite_items(
            suite_uid,
            payload.get("items"),
        )
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"suite": suite, "error": None})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        status = 409 if "已包含" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except Exception as exc:
        return jsonify(
            {"error": f"添加测试集脚本失败：{exc}"}
        ), 500


def delete_test_suite_item_response(
    services,
    suite_uid,
    item_id,
):
    try:
        suite = services.delete_test_suite_item(
            suite_uid,
            item_id,
        )
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"suite": suite, "error": None})
    except Exception as exc:
        return jsonify(
            {"error": f"移除测试集脚本失败：{exc}"}
        ), 500


def reorder_test_suite_items_response(services, suite_uid):
    payload = request.get_json(silent=True) or {}
    try:
        suite = services.reorder_test_suite_items(
            suite_uid,
            payload.get("item_ids"),
        )
        if not suite:
            return jsonify({"error": "测试集不存在。"}), 404
        return jsonify({"suite": suite, "error": None})
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"调整测试集脚本顺序失败：{exc}"}
        ), 500


def create_test_suites_blueprint(services):
    """Create non-execution suite routes from explicit services."""

    if not isinstance(services, TestSuiteWebServices):
        raise TypeError(
            "services must be a TestSuiteWebServices instance"
        )

    blueprint = Blueprint("test_suites", __name__)
    blueprint.add_url_rule(
        "/api/test-suites",
        view_func=lambda: list_test_suites_response(services),
        methods=["GET"],
        endpoint="list_test_suites",
    )
    blueprint.add_url_rule(
        "/api/test-suites",
        view_func=lambda: create_test_suite_response(services),
        methods=["POST"],
        endpoint="create_test_suite",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>",
        view_func=lambda suite_uid: get_test_suite_response(
            services,
            suite_uid,
        ),
        methods=["GET"],
        endpoint="get_test_suite",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>",
        view_func=lambda suite_uid: update_test_suite_response(
            services,
            suite_uid,
        ),
        methods=["PUT"],
        endpoint="update_test_suite",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>",
        view_func=lambda suite_uid: delete_test_suite_response(
            services,
            suite_uid,
        ),
        methods=["DELETE"],
        endpoint="delete_test_suite",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>/items",
        view_func=lambda suite_uid: add_test_suite_items_response(
            services,
            suite_uid,
        ),
        methods=["POST"],
        endpoint="add_test_suite_items",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>/items/<int:item_id>",
        view_func=(
            lambda suite_uid, item_id: (
                delete_test_suite_item_response(
                    services,
                    suite_uid,
                    item_id,
                )
            )
        ),
        methods=["DELETE"],
        endpoint="delete_test_suite_item",
    )
    blueprint.add_url_rule(
        "/api/test-suites/<suite_uid>/items/reorder",
        view_func=lambda suite_uid: reorder_test_suite_items_response(
            services,
            suite_uid,
        ),
        methods=["PUT"],
        endpoint="reorder_test_suite_items",
    )
    return blueprint
