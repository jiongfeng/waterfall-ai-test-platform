"""Page-inventory domain package."""

from .model import (
    PAGE_INVENTORY_SOURCES,
    PageInventoryModelDependencies,
    normalize_accounts,
    normalize_page_inventory_payload,
    parse_page_inventory_from_markdown,
    serialize_page_inventory,
    split_markdown_table_row,
)
from .repository import (
    PageInventoryRepository,
    PageInventoryRepositoryDependencies,
)
from .service import (
    DEFAULT_PAGE_INVENTORY_DOCUMENT,
    PageInventoryService,
    PageInventoryServiceDependencies,
    import_page_inventory_from_doc,
)


__all__ = [
    "DEFAULT_PAGE_INVENTORY_DOCUMENT",
    "PAGE_INVENTORY_SOURCES",
    "PageInventoryModelDependencies",
    "PageInventoryRepository",
    "PageInventoryRepositoryDependencies",
    "PageInventoryService",
    "PageInventoryServiceDependencies",
    "import_page_inventory_from_doc",
    "normalize_accounts",
    "normalize_page_inventory_payload",
    "parse_page_inventory_from_markdown",
    "serialize_page_inventory",
    "split_markdown_table_row",
]
