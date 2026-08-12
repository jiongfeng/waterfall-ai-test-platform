import unittest
from pathlib import Path
from unittest.mock import patch

import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
SHARED_STYLESHEET = STATIC_DIR / "styles.css"
REQUIREMENTS_STYLESHEET = STATIC_DIR / "css" / "features" / "requirements.css"
SETUP_IMPORT = '@import url("./css/features/setup-preparation.css");'
REQUIREMENTS_IMPORT = '@import url("./css/features/requirements.css");'

EXPECTED_REQUIREMENTS_SELECTORS = {
    "#requirementFileInput.hidden",
    "#requirementModulesTab",
    "#requirementModulesTab span",
    ".module-name-button",
    ".module-name-button:hover",
    ".requirement-download-link",
    ".requirement-delete-warning",
    ".requirement-header-actions",
    ".requirement-list-meta",
    ".requirement-list-title",
    ".requirement-module-card",
    ".requirement-module-card .form-field",
    ".requirement-module-card .form-field input",
    ".requirement-module-card .form-field span",
    ".requirement-module-card .form-field textarea",
    ".requirement-module-card .requirement-prompt-editor",
    ".requirement-module-card-header",
    ".requirement-module-card-header p",
    ".requirement-module-detail-actions",
    ".requirement-module-detail-body",
    ".requirement-module-detail-editor",
    ".requirement-module-detail-editor .form-field",
    ".requirement-module-detail-editor .form-field span",
    ".requirement-module-detail-editor .form-field textarea",
    ".requirement-module-detail-editor .requirement-prompt-editor",
    ".requirement-module-detail-modal",
    ".requirement-module-detail-status",
    ".requirement-module-grid",
    ".requirement-module-job-output pre",
    ".requirement-module-meta",
    ".requirement-module-meta div",
    ".requirement-module-meta span",
    ".requirement-module-meta strong",
    ".requirement-module-plan-path",
    ".requirement-module-row-log",
    ".requirement-module-row-log.error",
    ".requirement-module-row-meta",
    ".requirement-module-table",
    ".requirement-module-table td",
    ".requirement-module-table td p",
    ".requirement-module-table td:last-child",
    ".requirement-module-table th",
    ".requirement-module-table th:last-child",
    ".requirement-module-table tr:last-child td",
    ".requirement-module-table-wrap",
    ".requirement-module-title-row",
    ".requirement-module-title-row h4",
    ".requirement-module-toolbar",
    ".requirement-modules-empty",
    ".requirement-modules-empty h3",
    ".requirement-modules-empty p",
    ".requirement-modules-list",
    ".requirement-pane-actions",
    ".requirement-pane-header",
    ".requirement-pane-header h3",
    ".requirement-pane-header p",
    ".requirement-plan-batch-label",
    ".requirement-plan-batch-label:first-child",
    ".requirement-plan-batch-log",
    ".requirement-plan-batch-output",
    ".requirement-plan-batch-prompt",
    ".requirement-plan-batch-record",
    ".requirement-preview",
    ".requirement-tab-panel",
    ".requirement-tab-panel > .job-output",
    ".requirement-tab-panel > .job-output pre",
    ".requirements-layout",
    ".requirements-panel",
    ".requirements-tab-panels",
    ".requirements-tabs",
}

# The feature import precedes shared rules, so composed variants need the
# component class in their selector to retain their former override priority.
CASCADE_GUARD_SELECTORS = {
    ".job-output.requirement-module-job-output pre",
    ".markdown-preview.requirement-preview",
    ".modal-body.requirement-module-detail-body",
    ".modal.requirement-module-detail-modal",
    ".secondary-button.requirement-download-link",
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
    test_case.assertEqual(
        depth,
        0,
        f"{source_name} has unbalanced CSS braces",
    )


class RequirementsStylesTests(unittest.TestCase):
    def test_requirements_import_follows_setup_import_before_rules(self):
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

        self.assertTrue(REQUIREMENTS_STYLESHEET.is_file())
        self.assertEqual(import_lines[:2], [SETUP_IMPORT, REQUIREMENTS_IMPORT])
        self.assertEqual(
            shared_lines[:first_rule_index],
            [*import_lines, ""],
        )
        self.assertTrue(shared_lines[first_rule_index].startswith(":root"))

    def test_all_requirements_selectors_live_in_the_feature_stylesheet(self):
        requirements_source = REQUIREMENTS_STYLESHEET.read_text(encoding="utf-8")
        shared_source = SHARED_STYLESHEET.read_text(encoding="utf-8")

        self.assertEqual(
            css_selectors(requirements_source),
            EXPECTED_REQUIREMENTS_SELECTORS | CASCADE_GUARD_SELECTORS,
        )
        leaked_selectors = {
            selector
            for selector in css_selectors(shared_source)
            if "requirement" in selector.lower()
            or selector in {".module-name-button", ".module-name-button:hover"}
        }
        self.assertEqual(leaked_selectors, set())

    def test_stylesheets_have_balanced_css_blocks(self):
        for stylesheet in (SHARED_STYLESHEET, REQUIREMENTS_STYLESHEET):
            with self.subTest(stylesheet=stylesheet.name):
                assert_balanced_css_braces(
                    self,
                    stylesheet.read_text(encoding="utf-8"),
                    stylesheet.name,
                )

    def test_flask_serves_requirements_stylesheet(self):
        client = app.app.test_client()

        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            response = client.get("/static/css/features/requirements.css")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/css")
            self.assertIn(b".requirements-panel", response.data)
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
