"""Application orchestration for page inventory."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable


DEFAULT_PAGE_INVENTORY_DOCUMENT = (
    "被测系统与测试平台使用说明.md"
)


@dataclass(frozen=True)
class PageInventoryServiceDependencies:
    """Repository and model capabilities used by the service."""

    list_rows: Callable[..., list]
    get_by_uid: Callable[[str], dict]
    upsert_normalized: Callable[..., dict]
    delete_by_uid: Callable[[str], bool]
    serialize_page_inventory: Callable[[dict], dict]
    normalize_page_inventory_payload: Callable[[dict], dict]
    parse_page_inventory_from_markdown: Callable[[str], list]
    app_dir: Path


class PageInventoryService:
    """Coordinate normalized writes and documentation imports."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            PageInventoryServiceDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "PageInventoryServiceDependencies instance"
            )
        self.dependencies = dependencies

    def list_rows(self, limit=None):
        return self.dependencies.list_rows(limit=limit)

    def get_by_uid(self, inventory_uid):
        return self.dependencies.get_by_uid(inventory_uid)

    def upsert(self, payload, inventory_uid=None):
        normalized = (
            self.dependencies.normalize_page_inventory_payload(
                payload
            )
        )
        return self.dependencies.upsert_normalized(
            normalized,
            inventory_uid=inventory_uid,
        )

    def delete(self, inventory_uid):
        return self.dependencies.delete_by_uid(inventory_uid)

    def import_from_doc(self, payload):
        payload = payload or {}
        content = payload.get("content")
        if not isinstance(content, str):
            doc_path = payload.get("path") or str(
                Path(self.dependencies.app_dir)
                / DEFAULT_PAGE_INVENTORY_DOCUMENT
            )
            path = Path(doc_path).expanduser()
            if not path.is_absolute():
                path = (
                    Path(self.dependencies.app_dir) / path
                )
            content = path.read_text(encoding="utf-8")
        rows = (
            self.dependencies.parse_page_inventory_from_markdown(
                content
            )
        )
        imported = []
        for row in rows:
            inventory_uid = hashlib.sha256(
                (
                    f"{row.get('page_name')}|"
                    f"{row.get('url')}"
                ).encode("utf-8")
            ).hexdigest()[:32]
            imported.append(
                self.dependencies.serialize_page_inventory(
                    self.upsert(
                        row,
                        inventory_uid=inventory_uid,
                    )
                )
            )
        return imported


def import_page_inventory_from_doc(payload, dependencies):
    """Compatibility-friendly direct entry point for imports."""

    return PageInventoryService(
        dependencies
    ).import_from_doc(payload)


__all__ = [
    "DEFAULT_PAGE_INVENTORY_DOCUMENT",
    "PageInventoryService",
    "PageInventoryServiceDependencies",
    "import_page_inventory_from_doc",
]
