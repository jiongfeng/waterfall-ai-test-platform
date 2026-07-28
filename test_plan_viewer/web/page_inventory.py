"""HTTP delivery for page-inventory operations."""

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify, request


@dataclass(frozen=True)
class PageInventoryWebServices:
    """Application services consumed by page-inventory routes."""

    list_rows: Callable[..., list]
    serialize_page_inventory: Callable[[dict], dict]
    upsert_page_inventory: Callable[..., dict]
    get_page_inventory_by_uid: Callable[[str], dict]
    delete_page_inventory: Callable[[str], bool]
    import_page_inventory_from_doc: Callable[[dict], list]


def list_page_inventory_response(services):
    try:
        items = [
            services.serialize_page_inventory(row)
            for row in services.list_rows()
        ]
        return jsonify({"items": items, "error": None})
    except Exception as exc:
        return jsonify(
            {
                "items": [],
                "error": (
                    f"读取页面 inventory 失败：{exc}"
                ),
            }
        ), 500


def create_page_inventory_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        item = services.upsert_page_inventory(payload)
        return jsonify(
            {
                "item": (
                    services.serialize_page_inventory(item)
                ),
                "error": None,
            }
        ), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {
                "error": (
                    f"保存页面 inventory 失败：{exc}"
                )
            }
        ), 500


def update_page_inventory_response(
    services,
    inventory_uid,
):
    payload = request.get_json(silent=True) or {}
    try:
        if not services.get_page_inventory_by_uid(
            inventory_uid
        ):
            return jsonify(
                {"error": "页面 inventory 不存在。"}
            ), 404
        item = services.upsert_page_inventory(
            payload,
            inventory_uid=inventory_uid,
        )
        return jsonify(
            {
                "item": (
                    services.serialize_page_inventory(item)
                ),
                "error": None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {
                "error": (
                    f"保存页面 inventory 失败：{exc}"
                )
            }
        ), 500


def delete_page_inventory_response(
    services,
    inventory_uid,
):
    try:
        deleted = services.delete_page_inventory(
            inventory_uid
        )
        if not deleted:
            return jsonify(
                {"error": "页面 inventory 不存在。"}
            ), 404
        return jsonify({"ok": True, "error": None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {
                "error": (
                    f"删除页面 inventory 失败：{exc}"
                )
            }
        ), 500


def import_page_inventory_response(services):
    payload = request.get_json(silent=True) or {}
    try:
        items = services.import_page_inventory_from_doc(
            payload
        )
        return jsonify(
            {
                "items": items,
                "count": len(items),
                "error": None,
            }
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify(
            {
                "error": (
                    f"导入页面 inventory 失败：{exc}"
                )
            }
        ), 500


def create_page_inventory_blueprint(services):
    """Create the five page-inventory routes."""

    if not isinstance(services, PageInventoryWebServices):
        raise TypeError(
            "services must be a "
            "PageInventoryWebServices instance"
        )

    blueprint = Blueprint("page_inventory", __name__)
    blueprint.add_url_rule(
        "/api/page-inventory",
        view_func=lambda: list_page_inventory_response(
            services
        ),
        methods=["GET"],
        endpoint="list_page_inventory",
    )
    blueprint.add_url_rule(
        "/api/page-inventory",
        view_func=lambda: create_page_inventory_response(
            services
        ),
        methods=["POST"],
        endpoint="create_page_inventory",
    )
    blueprint.add_url_rule(
        "/api/page-inventory/<inventory_uid>",
        view_func=lambda inventory_uid: (
            update_page_inventory_response(
                services,
                inventory_uid,
            )
        ),
        methods=["PUT"],
        endpoint="update_page_inventory",
    )
    blueprint.add_url_rule(
        "/api/page-inventory/<inventory_uid>",
        view_func=lambda inventory_uid: (
            delete_page_inventory_response(
                services,
                inventory_uid,
            )
        ),
        methods=["DELETE"],
        endpoint="delete_page_inventory",
    )
    blueprint.add_url_rule(
        "/api/page-inventory/import-from-doc",
        view_func=lambda: import_page_inventory_response(
            services
        ),
        methods=["POST"],
        endpoint="import_page_inventory",
    )
    return blueprint


__all__ = [
    "PageInventoryWebServices",
    "create_page_inventory_blueprint",
]
