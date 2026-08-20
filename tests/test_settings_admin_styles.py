import unittest
from pathlib import Path
from unittest.mock import patch

import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
SHARED_STYLESHEET = STATIC_DIR / "styles.css"
ADMIN_STYLESHEET = STATIC_DIR / "css" / "features" / "admin.css"
PROJECT_SETTINGS_STYLESHEET = (
    STATIC_DIR / "css" / "features" / "project-settings.css"
)
EXPECTED_IMPORT_PREFIX = [
    '@import url("./css/features/setup-preparation.css");',
    '@import url("./css/features/requirements.css");',
    '@import url("./css/features/test-suites.css");',
    '@import url("./css/features/admin.css");',
    '@import url("./css/features/project-settings.css");',
]

EXPECTED_ADMIN_SELECTORS = {
    ".admin-checkbox",
    ".admin-checkbox input",
    ".admin-checkbox-grid",
    ".admin-form",
    ".admin-form-actions",
    ".admin-form-header",
    ".admin-form-header h3",
    ".admin-form-header p",
    ".admin-panel",
    ".admin-table",
    ".admin-toolbar",
    ".admin-toolbar h3",
    ".admin-toolbar p",
    ".system-tag",
}
ADMIN_CASCADE_GUARDS = {
    ".module-script-table.admin-table",
}
SHARED_ADMIN_SELECTORS = {
    ".admin-form-grid",
    ".app-shell.admin-mode .sidebar",
    ".app-shell.admin-mode .workspace-shell",
}

EXPECTED_PROJECT_SETTINGS_SELECTORS = {
    ".project-seed-current",
    ".project-seed-current > span",
    ".project-seed-current > strong",
    ".project-seed-generate-caret",
    ".project-seed-generate-menu",
    ".project-seed-generate-menu > button",
    ".project-seed-generate-menu > button strong",
    ".project-seed-generate-menu > button span",
    ".project-seed-generate-menu > button:focus-visible",
    ".project-seed-generate-menu > button:hover:not(:disabled)",
    ".project-seed-generate-menu > button:last-child",
    ".project-seed-generate-menu-wrap",
    ".project-seed-generate-menu[hidden]",
    ".project-seed-generate-toggle",
    ".project-seed-guidance",
    ".project-seed-guidance p",
    ".project-seed-guidance p + p",
    ".project-seed-overwrite-hint",
    ".project-settings-actions",
    ".project-settings-basic-panel",
    ".project-settings-command-grid",
    ".project-settings-command-grid textarea",
    ".project-settings-empty",
    ".project-settings-form",
    ".project-settings-grid",
    ".project-settings-header",
    ".project-settings-header h3",
    ".project-settings-header p",
    ".project-settings-output-wrap",
    ".project-settings-panel",
    ".project-settings-section",
    ".project-settings-toggle",
    ".project-settings-top-tabs",
}

SHARED_SELECTORS_THAT_STAY = {
    ".admin-form-grid",
    ".form-field",
    ".modal",
    ".module-script-table",
    ".primary-button",
    ".secondary-button",
}


def css_preludes(source):
    preludes = []
    buffer = []
    quote = None
    in_comment = False
    index = 0

    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if in_comment:
            if character == "*" and following == "/":
                in_comment = False
                index += 2
                continue
        elif quote:
            buffer.append(character)
            if character == "\\":
                if following:
                    buffer.append(following)
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
            buffer.append(character)
        elif character == "{":
            prelude = "".join(buffer).strip()
            if prelude:
                preludes.append(prelude)
            buffer = []
        elif character == "}":
            buffer = []
        else:
            buffer.append(character)

        index += 1

    return preludes


def css_selectors(source):
    selectors = set()
    for prelude in css_preludes(source):
        if prelude.startswith("@"):
            continue
        selectors.update(
            selector.strip()
            for selector in prelude.split(",")
            if selector.strip()
        )
    return selectors


def assert_balanced_css_braces(test_case, source, source_name):
    depth = 0
    quote = None
    in_comment = False
    index = 0

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


class SettingsAndAdminStylesTests(unittest.TestCase):
    def test_feature_imports_are_ordered_before_shared_rules(self):
        shared_lines = SHARED_STYLESHEET.read_text(encoding="utf-8").splitlines()
        import_lines = [
            line
            for line in shared_lines
            if line.startswith("@import ")
        ]
        first_rule_index = next(
            index
            for index, line in enumerate(shared_lines)
            if line and not line.startswith("@import ")
        )

        self.assertTrue(ADMIN_STYLESHEET.is_file())
        self.assertTrue(PROJECT_SETTINGS_STYLESHEET.is_file())
        self.assertEqual(import_lines[:5], EXPECTED_IMPORT_PREFIX)
        self.assertEqual(shared_lines[:first_rule_index], [*import_lines, ""])
        self.assertTrue(shared_lines[first_rule_index].startswith(":root"))

    def test_admin_selectors_are_scoped_without_moving_shared_forms(self):
        admin_selectors = css_selectors(
            ADMIN_STYLESHEET.read_text(encoding="utf-8")
        )
        shared_selectors = css_selectors(
            SHARED_STYLESHEET.read_text(encoding="utf-8")
        )

        self.assertEqual(
            admin_selectors,
            EXPECTED_ADMIN_SELECTORS | ADMIN_CASCADE_GUARDS,
        )
        leaked_admin_selectors = {
            selector
            for selector in shared_selectors
            if "admin" in selector
        }
        self.assertEqual(leaked_admin_selectors, SHARED_ADMIN_SELECTORS)
        self.assertTrue(SHARED_SELECTORS_THAT_STAY <= shared_selectors)

    def test_project_settings_selectors_have_no_shared_file_leakage(self):
        project_settings_selectors = css_selectors(
            PROJECT_SETTINGS_STYLESHEET.read_text(encoding="utf-8")
        )
        shared_selectors = css_selectors(
            SHARED_STYLESHEET.read_text(encoding="utf-8")
        )

        self.assertEqual(
            project_settings_selectors,
            EXPECTED_PROJECT_SETTINGS_SELECTORS,
        )
        self.assertEqual(
            {
                selector
                for selector in shared_selectors
                if "project-settings" in selector
            },
            set(),
        )

    def test_stylesheets_have_balanced_css_blocks(self):
        for stylesheet in (
            SHARED_STYLESHEET,
            ADMIN_STYLESHEET,
            PROJECT_SETTINGS_STYLESHEET,
        ):
            with self.subTest(stylesheet=stylesheet.name):
                assert_balanced_css_braces(
                    self,
                    stylesheet.read_text(encoding="utf-8"),
                    stylesheet.name,
                )

    def test_flask_serves_admin_and_project_settings_stylesheets(self):
        client = app.app.test_client()

        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            for static_url in (
                "/static/css/features/admin.css",
                "/static/css/features/project-settings.css",
            ):
                with self.subTest(static_url=static_url):
                    response = client.get(static_url)
                    try:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.mimetype, "text/css")
                        self.assertTrue(response.data)
                    finally:
                        response.close()


if __name__ == "__main__":
    unittest.main()
