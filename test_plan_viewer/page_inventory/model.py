"""Normalization, serialization, and Markdown parsing for page inventory."""

from dataclasses import dataclass
import re
from typing import Callable

PAGE_INVENTORY_SOURCES = frozenset(
    {"manual", "doc", "scanner", "plan", "script"}
)


@dataclass(frozen=True)
class PageInventoryModelDependencies:
    """Pure normalization helpers supplied by the composition root."""

    load_json_column: Callable[[object, object], object]
    normalize_confidence: Callable[[object], object]
    normalize_string_list: Callable[[object], list]
    normalize_json_object_or_array: Callable[[object, object], object]
    allowed_sources: frozenset = PAGE_INVENTORY_SOURCES


def _require_dependencies(dependencies):
    if not isinstance(
        dependencies,
        PageInventoryModelDependencies,
    ):
        raise TypeError(
            "dependencies must be a "
            "PageInventoryModelDependencies instance"
        )
    return dependencies


def serialize_page_inventory(row, dependencies):
    """Serialize one database row to the browser-facing contract."""

    if not row:
        return None
    dependencies = _require_dependencies(dependencies)
    load_json_column = dependencies.load_json_column
    return {
        "id": row.get("id"),
        "inventory_uid": row.get("inventory_uid"),
        "page_name": row.get("page_name") or "",
        "url": row.get("url") or "",
        "menu_path": load_json_column(
            row.get("menu_path_json"),
            [],
        ),
        "roles": load_json_column(row.get("roles_json"), []),
        "accounts": load_json_column(
            row.get("accounts_json"),
            [],
        ),
        "stable_selectors": load_json_column(
            row.get("stable_selectors_json"),
            [],
        ),
        "actions": load_json_column(
            row.get("actions_json"),
            [],
        ),
        "read_only_actions": load_json_column(
            row.get("read_only_actions_json"),
            [],
        ),
        "write_actions": load_json_column(
            row.get("write_actions_json"),
            [],
        ),
        "sample_data": load_json_column(
            row.get("sample_data_json"),
            [],
        ),
        "write_risk": bool(row.get("write_risk")),
        "baseline_required": bool(
            row.get("baseline_required")
        ),
        "notes": row.get("notes") or "",
        "source": row.get("source") or "",
        "confidence": dependencies.normalize_confidence(
            row.get("confidence")
        ),
        "snapshot_hash": row.get("snapshot_hash") or "",
        "last_scanned_at": row.get("last_scanned_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def normalize_accounts(value, dependencies):
    """Normalize account records used by page inventory."""

    dependencies = _require_dependencies(dependencies)
    if isinstance(value, list):
        accounts = []
        for item in value:
            if isinstance(item, dict):
                username = str(item.get("username") or "").strip()
                if username:
                    accounts.append(
                        {
                            "username": username,
                            "password_ref": str(
                                item.get("password_ref") or ""
                            ).strip(),
                            "purpose": str(
                                item.get("purpose") or ""
                            ).strip(),
                        }
                    )
            else:
                username = str(item or "").strip().strip("`")
                if username:
                    accounts.append(
                        {
                            "username": username,
                            "password_ref": "",
                            "purpose": "",
                        }
                    )
        return accounts
    return [
        {
            "username": item,
            "password_ref": "",
            "purpose": "",
        }
        for item in dependencies.normalize_string_list(value)
    ]


def normalize_page_inventory_payload(payload, dependencies):
    """Validate and normalize a page-inventory write payload."""

    dependencies = _require_dependencies(dependencies)
    if not isinstance(payload, dict):
        raise ValueError("inventory 必须是对象。")
    page_name = str(payload.get("page_name") or "").strip()
    if not page_name:
        raise ValueError("页面名称不能为空。")
    source = (
        str(payload.get("source") or "manual").strip()
        or "manual"
    )
    if source not in dependencies.allowed_sources:
        source = "manual"
    write_risk = bool(payload.get("write_risk"))
    baseline_required = (
        bool(payload.get("baseline_required")) or write_risk
    )
    normalize_string_list = dependencies.normalize_string_list
    return {
        "page_name": page_name[:255],
        "url": str(payload.get("url") or "").strip()[:512],
        "menu_path": normalize_string_list(
            payload.get("menu_path")
        ),
        "roles": normalize_string_list(payload.get("roles")),
        "accounts": normalize_accounts(payload.get("accounts"), dependencies),
        "stable_selectors": normalize_string_list(
            payload.get("stable_selectors")
        ),
        "actions": normalize_string_list(
            payload.get("actions")
        ),
        "read_only_actions": normalize_string_list(
            payload.get("read_only_actions")
        ),
        "write_actions": normalize_string_list(
            payload.get("write_actions")
        ),
        "sample_data": (
            dependencies.normalize_json_object_or_array(
                payload.get("sample_data"),
                [],
            )
        ),
        "write_risk": write_risk,
        "baseline_required": baseline_required,
        "notes": str(payload.get("notes") or "").strip(),
        "source": source,
        "confidence": dependencies.normalize_confidence(
            payload.get("confidence")
        ),
        "snapshot_hash": str(
            payload.get("snapshot_hash") or ""
        ).strip()[:64],
        "last_scanned_at": (
            payload.get("last_scanned_at")
            if isinstance(payload.get("last_scanned_at"), int)
            else None
        ),
    }


def split_markdown_table_row(line):
    """Return cells from the simple Markdown tables used by the docs."""

    text = line.strip()
    if not text.startswith("|") or not text.endswith("|"):
        return []
    return [
        cell.strip().strip("`")
        for cell in text.strip("|").split("|")
    ]


def parse_page_inventory_from_markdown(
    markdown_text,
    dependencies,
):
    """Parse the first page/path Markdown table into inventory payloads."""

    dependencies = _require_dependencies(dependencies)
    rows = []
    lines = (markdown_text or "").splitlines()
    for index, line in enumerate(lines):
        header = split_markdown_table_row(line)
        if not header or "页面" not in header or "路径" not in header:
            continue
        column_index = {
            name: column
            for column, name in enumerate(header)
        }
        for row_line in lines[index + 1 :]:
            cells = split_markdown_table_row(row_line)
            if not cells:
                break
            if all(
                re.match(r"^:?-{3,}:?$", cell)
                for cell in cells
            ):
                continue
            if len(cells) < len(header):
                continue
            page_name = cells[
                column_index.get("页面", 0)
            ].strip()
            if not page_name:
                continue
            risk_text = (
                cells[
                    column_index.get(
                        "写库风险",
                        len(cells) - 1,
                    )
                ]
                if "写库风险" in column_index
                else ""
            )
            control_text = (
                cells[
                    column_index.get(
                        "关键控件 / 操作",
                        -1,
                    )
                ]
                if "关键控件 / 操作" in column_index
                else ""
            )
            action_items = (
                dependencies.normalize_string_list(control_text)
            )
            stable_selectors = [
                item
                for item in action_items
                if item.startswith(("#", "."))
            ]
            write_risk = (
                "写库" in risk_text
                or "会写" in risk_text
                or "保存" in risk_text
            )
            rows.append(
                {
                    "page_name": page_name,
                    "url": (
                        cells[
                            column_index.get("路径", 1)
                        ].strip()
                        if "路径" in column_index
                        else ""
                    ),
                    "accounts": normalize_accounts(
                        (
                            cells[
                                column_index.get(
                                    "推荐账号",
                                    -1,
                                )
                            ]
                            if "推荐账号" in column_index
                            else ""
                        ),
                        dependencies,
                    ),
                    "stable_selectors": stable_selectors,
                    "actions": action_items,
                    "read_only_actions": [
                        item
                        for item in action_items
                        if item
                        in {
                            "查询",
                            "重置",
                            "查看",
                            "导出 Excel",
                            "导出",
                        }
                    ],
                    "write_actions": (
                        []
                        if not write_risk
                        else [
                            item
                            for item in action_items
                            if item not in stable_selectors
                        ]
                    ),
                    "write_risk": write_risk,
                    "baseline_required": write_risk,
                    "notes": risk_text,
                    "source": "doc",
                    "confidence": 0.8,
                }
            )
        if rows:
            break
    return rows


__all__ = [
    "PAGE_INVENTORY_SOURCES",
    "PageInventoryModelDependencies",
    "normalize_accounts",
    "normalize_page_inventory_payload",
    "parse_page_inventory_from_markdown",
    "serialize_page_inventory",
    "split_markdown_table_row",
]
