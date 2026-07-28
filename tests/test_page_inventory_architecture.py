import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "test_plan_viewer" / "page_inventory"
WEB_PATH = (
    ROOT
    / "test_plan_viewer"
    / "web"
    / "page_inventory.py"
)

ROUTES = {
    ("GET", "/api/page-inventory"),
    ("POST", "/api/page-inventory"),
    (
        "PUT",
        "/api/page-inventory/<inventory_uid>",
    ),
    (
        "DELETE",
        "/api/page-inventory/<inventory_uid>",
    ),
    (
        "POST",
        "/api/page-inventory/import-from-doc",
    ),
}


class PageInventoryArchitectureTests(unittest.TestCase):
    def test_domain_files_and_explicit_dependencies_exist(self):
        for filename in (
            "__init__.py",
            "model.py",
            "repository.py",
            "service.py",
        ):
            with self.subTest(filename=filename):
                self.assertTrue(
                    (PACKAGE / filename).is_file()
                )

        expected_definitions = {
            "model.py": (
                "class PageInventoryModelDependencies",
            ),
            "repository.py": (
                "class PageInventoryRepositoryDependencies",
                "class PageInventoryRepository",
            ),
            "service.py": (
                "class PageInventoryServiceDependencies",
                "class PageInventoryService",
            ),
        }
        for filename, definitions in (
            expected_definitions.items()
        ):
            source = (PACKAGE / filename).read_text(
                encoding="utf-8"
            )
            for definition in definitions:
                with self.subTest(
                    filename=filename,
                    definition=definition,
                ):
                    self.assertIn(definition, source)

    def test_domain_imports_neither_flask_nor_legacy_app(self):
        for path in sorted(PACKAGE.glob("*.py")):
            with self.subTest(path=path.name):
                tree = ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                )
                imported_roots = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported_roots.update(
                            alias.name.split(".", 1)[0]
                            for alias in node.names
                        )
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                    ):
                        imported_roots.add(
                            node.module.split(".", 1)[0]
                        )
                self.assertNotIn("app", imported_roots)
                self.assertNotIn("flask", imported_roots)

    def test_blueprint_declares_only_the_five_routes(self):
        source = WEB_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "class PageInventoryWebServices",
            source,
        )
        self.assertIn(
            "def create_page_inventory_blueprint(",
            source,
        )
        for method, route in ROUTES:
            with self.subTest(method=method, route=route):
                for part in route.split(
                    "<inventory_uid>"
                ):
                    self.assertIn(part, source)
                self.assertIn(f'"{method}"', source)


if __name__ == "__main__":
    unittest.main()
