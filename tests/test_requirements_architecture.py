import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "test_plan_viewer" / "requirements"
WEB_PATH = ROOT / "test_plan_viewer" / "web" / "requirements.py"

NON_STREAM_ROUTES = {
    ("GET", "/api/requirements"),
    ("POST", "/api/requirements/upload"),
    ("GET", "/api/requirements/<requirement_uid>"),
    (
        "GET",
        "/api/requirements/<requirement_uid>/download",
    ),
    ("DELETE", "/api/requirements/<requirement_uid>"),
    (
        "GET",
        "/api/requirements/<requirement_uid>/modules",
    ),
    (
        "PUT",
        (
            "/api/requirements/<requirement_uid>/modules/"
            "<module_uid>"
        ),
    ),
    (
        "DELETE",
        (
            "/api/requirements/<requirement_uid>/modules/"
            "<module_uid>"
        ),
    ),
}


class RequirementsArchitectureTests(unittest.TestCase):
    def test_required_domain_files_and_explicit_dependencies_exist(self):
        for filename in (
            "__init__.py",
            "model.py",
            "storage.py",
            "repository.py",
            "service.py",
        ):
            with self.subTest(filename=filename):
                self.assertTrue((PACKAGE / filename).is_file())

        expected_definitions = {
            "storage.py": (
                "class RequirementStorageDependencies",
                "class RequirementStorage",
            ),
            "repository.py": (
                "class RequirementRepositoryDependencies",
                "class RequirementRepository",
            ),
            "service.py": (
                "class RequirementServiceDependencies",
                "class RequirementService",
            ),
            "model.py": (
                "class RequirementSerializationDependencies",
                "class RequirementModuleModelDependencies",
            ),
        }
        for filename, definitions in expected_definitions.items():
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

    def test_blueprint_owns_only_non_streaming_routes(self):
        source = WEB_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "class RequirementWebServices",
            source,
        )
        self.assertIn(
            "def create_requirements_blueprint(",
            source,
        )
        for method, path in NON_STREAM_ROUTES:
            with self.subTest(method=method, path=path):
                for part in path.split("<module_uid>"):
                    self.assertIn(part, source)
                self.assertIn(f'"{method}"', source)
        self.assertNotIn("analysis-stream", source)
        self.assertNotIn("generate-plan-stream", source)


if __name__ == "__main__":
    unittest.main()
