"""Normalization, serialization, and Markdown parsing for page inventory."""

from dataclasses import dataclass
import re
from typing import Callable

from test_plan_viewer.execution.environment import (
    validate_target_credential_environment_name,
)


PAGE_INVENTORY_SOURCES = frozenset(
    {"manual", "doc", "scanner", "plan", "script"}
)
PAGE_INVENTORY_LITERAL_CREDENTIAL_KEYS = frozenset(
    {
        "loginpassword",
        "loginusername",
        "passwd",
        "password",
        "pwd",
        "username",
    }
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


def sanitize_page_inventory_sample_data(value):
    if isinstance(value, dict):
        result = {}
        migration_required = False
        for key, item in value.items():
            normalized_key = re.sub(
                r"[^a-z0-9]",
                "",
                str(key).lower(),
            )
            if (
                normalized_key
                in PAGE_INVENTORY_LITERAL_CREDENTIAL_KEYS
            ):
                migration_required = (
                    migration_required
                    or item not in (None, "")
                )
                continue
            sanitized, child_migration = (
                sanitize_page_inventory_sample_data(item)
            )
            result[key] = sanitized
            migration_required = (
                migration_required or child_migration
            )
        return result, migration_required
    if isinstance(value, list):
        result = []
        migration_required = False
        for item in value:
            sanitized, child_migration = (
                sanitize_page_inventory_sample_data(item)
            )
            result.append(sanitized)
            migration_required = (
                migration_required or child_migration
            )
        return result, migration_required
    return value, False


def serialize_page_inventory(row, dependencies):
    """Serialize one database row to the browser-facing contract."""

    if not row:
        return None
    dependencies = _require_dependencies(dependencies)
    load_json_column = dependencies.load_json_column
    accounts = normalize_accounts(
        load_json_column(
            row.get("accounts_json"),
            [],
        ),
        dependencies,
    )
    sample_data, sample_migration_required = (
        sanitize_page_inventory_sample_data(
            load_json_column(
                row.get("sample_data_json"),
                [],
            )
        )
    )
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
        "accounts": accounts,
        "credentials_migration_required": bool(
            row.get("credentials_migration_required")
            or sample_migration_required
            or any(
                account.get("credentials_migration_required")
                for account in accounts
            )
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
        "sample_data": sample_data,
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


def _normalize_account_reference(
    value,
    field_name,
    *,
    strict,
):
    reference = str(value or "").strip()
    if reference.startswith("env://"):
        reference = reference[6:]
    if not reference:
        return "", False
    try:
        return (
            validate_target_credential_environment_name(
                reference
            ),
            False,
        )
    except ValueError as exc:
        if strict:
            raise ValueError(
                f"accounts[].{field_name} must reference a "
                "TARGET_ environment variable."
            ) from exc
        return "", True


def normalize_accounts(
    value,
    dependencies,
    *,
    reject_plaintext=False,
):
    """Normalize account references without exposing stored secrets."""

    dependencies = _require_dependencies(dependencies)
    if isinstance(value, list):
        accounts = []
        for item in value:
            if isinstance(item, dict):
                has_plaintext = bool(
                    item.get("username")
                    or item.get("password")
                )
                if has_plaintext and reject_plaintext:
                    raise ValueError(
                        "accounts must use username_ref/password_ref; "
                        "plaintext username/password is not accepted."
                    )
                username_ref, invalid_username_ref = (
                    _normalize_account_reference(
                        item.get("username_ref")
                        or item.get("username_env"),
                        "username_ref",
                        strict=reject_plaintext,
                    )
                )
                password_ref, invalid_password_ref = (
                    _normalize_account_reference(
                        item.get("password_ref")
                        or item.get("password_env"),
                        "password_ref",
                        strict=reject_plaintext,
                    )
                )
                migration_required = bool(
                    has_plaintext
                    or invalid_username_ref
                    or invalid_password_ref
                    or item.get(
                        "credentials_migration_required"
                    )
                )
                purpose = str(
                    item.get("purpose") or ""
                ).strip()
                if (
                    username_ref
                    or password_ref
                    or purpose
                    or migration_required
                ):
                    accounts.append(
                        {
                            "username_ref": username_ref,
                            "password_ref": password_ref,
                            "purpose": purpose,
                            "credentials_migration_required": (
                                migration_required
                            ),
                        }
                    )
            else:
                legacy_value = str(
                    item or ""
                ).strip().strip("`")
                if legacy_value and reject_plaintext:
                    raise ValueError(
                        "accounts must use username_ref/password_ref; "
                        "plaintext account labels are not accepted."
                    )
                if legacy_value:
                    accounts.append(
                        {
                            "username_ref": "",
                            "password_ref": "",
                            "purpose": "",
                            "credentials_migration_required": True,
                        }
                    )
        return accounts
    legacy_accounts = dependencies.normalize_string_list(value)
    if legacy_accounts and reject_plaintext:
        raise ValueError(
            "accounts must use username_ref/password_ref; "
            "plaintext account labels are not accepted."
        )
    return [
        {
            "username_ref": "",
            "password_ref": "",
            "purpose": "",
            "credentials_migration_required": True,
        }
        for _item in legacy_accounts
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
    accounts = normalize_accounts(
        payload.get("accounts"),
        dependencies,
        reject_plaintext=True,
    )
    sample_data = dependencies.normalize_json_object_or_array(
        payload.get("sample_data"),
        [],
    )
    sample_data, sample_migration_required = (
        sanitize_page_inventory_sample_data(sample_data)
    )
    if sample_migration_required:
        raise ValueError(
            "sample_data must not contain plaintext username/password "
            "fields; use account references."
        )
    return {
        "page_name": page_name[:255],
        "url": str(payload.get("url") or "").strip()[:512],
        "menu_path": normalize_string_list(
            payload.get("menu_path")
        ),
        "roles": normalize_string_list(payload.get("roles")),
        "accounts": accounts,
        "credentials_migration_required": bool(
            payload.get("credentials_migration_required")
            or sample_migration_required
            or any(
                account.get("credentials_migration_required")
                for account in accounts
            )
        ),
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
        "sample_data": sample_data,
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
