import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
PACKAGE = ROOT / "test_plan_viewer" / "test_suites"
WEB_PATH = ROOT / "test_plan_viewer" / "web" / "test_suites.py"

CRUD_ROUTES = {
    ("GET", "/api/test-suites"),
    ("POST", "/api/test-suites"),
    ("GET", "/api/test-suites/<suite_uid>"),
    ("PUT", "/api/test-suites/<suite_uid>"),
    ("DELETE", "/api/test-suites/<suite_uid>"),
    ("POST", "/api/test-suites/<suite_uid>/items"),
    (
        "DELETE",
        "/api/test-suites/<suite_uid>/items/<int:item_id>",
    ),
    (
        "PUT",
        "/api/test-suites/<suite_uid>/items/reorder",
    ),
}


def decorated_routes(source):
    routes = set()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if not (
                isinstance(attribute, ast.Attribute)
                and isinstance(attribute.value, ast.Name)
            ):
                continue
            if not decorator.args:
                continue
            path = decorator.args[0]
            if not isinstance(path, ast.Constant):
                continue
            routes.add(
                (
                    attribute.attr.upper(),
                    path.value,
                )
            )
    return routes


class TestSuiteArchitectureTests(unittest.TestCase):
    def test_domain_has_model_service_and_repository_boundaries(self):
        self.assertTrue((PACKAGE / "__init__.py").is_file())
        self.assertTrue((PACKAGE / "model.py").is_file())
        self.assertTrue((PACKAGE / "service.py").is_file())
        self.assertTrue((PACKAGE / "repository.py").is_file())

        repository_source = (
            PACKAGE / "repository.py"
        ).read_text(encoding="utf-8")
        service_source = (
            PACKAGE / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "class TestSuiteRepositoryDependencies",
            repository_source,
        )
        self.assertIn(
            "class TestSuiteRepository",
            repository_source,
        )
        self.assertIn(
            "class TestSuiteItemDependencies",
            service_source,
        )

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

    def test_non_execution_routes_are_owned_by_the_blueprint(self):
        app_source = APP_PATH.read_text(encoding="utf-8")
        app_routes = decorated_routes(app_source)
        self.assertEqual(CRUD_ROUTES & app_routes, set())
        self.assertIn(
            (
                "GET",
                "/api/test-suites/<suite_uid>/execution-records",
            ),
            app_routes,
        )
        self.assertIn(
            (
                "POST",
                "/api/test-suites/<suite_uid>/execution-stream",
            ),
            app_routes,
        )

        web_source = WEB_PATH.read_text(encoding="utf-8")
        for method, path in CRUD_ROUTES:
            with self.subTest(method=method, path=path):
                self.assertIn(f'"{path}"', web_source)
                self.assertIn(f'"{method}"', web_source)
        self.assertNotIn("execution-records", web_source)
        self.assertNotIn("execution-stream", web_source)

    def test_app_keeps_compatibility_wrappers_and_composition(self):
        app_source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create_test_suites_blueprint(_test_suite_web_services())",
            app_source,
        )
        for function_name in (
            "validate_suite_name",
            "serialize_test_suite",
            "list_test_suites_from_mysql",
            "get_test_suite_payload",
            "create_test_suite_in_mysql",
            "update_test_suite_in_mysql",
            "delete_test_suite_in_mysql",
            "add_test_suite_items_in_mysql",
            "delete_test_suite_item_in_mysql",
            "reorder_test_suite_items_in_mysql",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(
                    f"def {function_name}(",
                    app_source,
                )


if __name__ == "__main__":
    unittest.main()
