"""Pure validation and serialization for persisted test suites."""

from typing import Callable


def validate_suite_name(value):
    """Normalize a required test-suite name."""

    name = str(value or "").strip()
    if not name:
        raise ValueError("测试集名字不能为空。")
    if len(name) > 255:
        raise ValueError("测试集名字不能超过 255 个字符。")
    return name


def validate_suite_description(value):
    """Normalize an optional test-suite description."""

    description = str(value or "").strip()
    if len(description) > 1024:
        raise ValueError("测试集说明不能超过 1024 个字符。")
    return description


def serialize_test_suite_item(
    row,
    *,
    strip_spec_suffix: Callable[[str], str],
):
    """Serialize one suite item without framework or database state."""

    if not row:
        return None
    filename = row.get("filename") or ""
    return {
        "item_id": row.get("item_id"),
        "id": row.get("item_id"),
        "script_asset_id": row.get("script_asset_id"),
        "module_name": row.get("module_name") or "",
        "filename": filename,
        "display_name": (
            row.get("display_name") or strip_spec_suffix(filename)
        ),
        "path": row.get("script_path") or "",
        "script_path": row.get("script_path") or "",
        "sort_order": int(row.get("sort_order") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def serialize_test_suite(row, items=None):
    """Serialize one suite and its already-serialized items."""

    if not row:
        return None
    return {
        "suite_id": row.get("suite_id"),
        "suite_uid": row.get("suite_uid"),
        "id": row.get("suite_uid"),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or "active",
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "items": items or [],
    }
