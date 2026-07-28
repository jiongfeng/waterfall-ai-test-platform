import unittest
from pathlib import Path
from unittest.mock import patch

import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
SHARED_STYLESHEET = STATIC_DIR / "styles.css"
SETUP_STYLESHEET = STATIC_DIR / "css" / "features" / "setup-preparation.css"
SETUP_IMPORT = '@import url("./css/features/setup-preparation.css");'


def assert_balanced_css_braces(test_case, source, source_name):
    depth = 0
    index = 0
    quote = None
    in_comment = False

    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if in_comment:
            if character == "*" and following == "/":
                in_comment = False
                index += 2
                continue
        elif quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character == "/" and following == "*":
            in_comment = True
            index += 2
            continue
        elif character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            test_case.assertGreaterEqual(
                depth,
                0,
                f"{source_name} closes a CSS block before one is opened",
            )

        index += 1

    test_case.assertFalse(in_comment, f"{source_name} has an unterminated comment")
    test_case.assertIsNone(quote, f"{source_name} has an unterminated string")
    test_case.assertEqual(depth, 0, f"{source_name} has unbalanced CSS braces")


class SetupPreparationStylesTests(unittest.TestCase):
    def test_setup_stylesheet_is_imported_before_shared_rules(self):
        shared_source = SHARED_STYLESHEET.read_text(encoding="utf-8")

        self.assertTrue(SETUP_STYLESHEET.is_file())
        self.assertEqual(shared_source.splitlines()[0], SETUP_IMPORT)
        self.assertNotIn(
            "/* Single-layer setup script management */",
            shared_source,
        )

    def test_stylesheets_have_balanced_css_blocks(self):
        for stylesheet in (SHARED_STYLESHEET, SETUP_STYLESHEET):
            with self.subTest(stylesheet=stylesheet.name):
                assert_balanced_css_braces(
                    self,
                    stylesheet.read_text(encoding="utf-8"),
                    stylesheet.name,
                )

    def test_flask_serves_setup_stylesheet(self):
        client = app.app.test_client()

        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            response = client.get("/static/css/features/setup-preparation.css")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/css")
            self.assertIn(
                b"Single-layer setup script management",
                response.data,
            )
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
