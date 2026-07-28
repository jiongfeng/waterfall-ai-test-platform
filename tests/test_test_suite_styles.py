import unittest
from pathlib import Path
from unittest.mock import patch

import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
SHARED_STYLESHEET = STATIC_DIR / "styles.css"
TEST_SUITE_STYLESHEET = STATIC_DIR / "css" / "features" / "test-suites.css"
EXPECTED_IMPORT_PREFIX = [
    '@import url("./css/features/setup-preparation.css");',
    '@import url("./css/features/requirements.css");',
    '@import url("./css/features/test-suites.css");',
]

EXPECTED_TEST_SUITE_SELECTORS = {
    ".modal-body.suite-script-modal-body",
    ".suite-script-list",
    ".suite-script-list-header",
    ".suite-script-modal",
    ".suite-script-module-picker",
    ".suite-script-module-picker .test-suite-module-list",
    ".suite-script-option",
    ".suite-script-option input",
    ".suite-script-option-module",
    ".suite-script-option-title",
    ".suite-script-option.disabled",
    ".suite-script-option:hover",
    ".suite-script-picker",
    ".suite-script-picker-header",
    ".suite-script-picker-header span:last-child",
    ".test-suite-actions",
    ".test-suite-detail",
    ".test-suite-execution-history-button",
    ".test-suite-execution-history-button span:first-child",
    ".test-suite-execution-history-button span:nth-child(2)",
    ".test-suite-execution-history-button span:nth-child(3)",
    ".test-suite-execution-history-button.active",
    ".test-suite-execution-history-button:hover",
    ".test-suite-execution-history-empty",
    ".test-suite-execution-history-empty.error",
    ".test-suite-execution-history-list",
    ".test-suite-execution-history-panel",
    ".test-suite-execution-layout",
    ".test-suite-execution-log-panel",
    ".test-suite-execution-log-panel summary",
    ".test-suite-execution-record",
    ".test-suite-execution-result-header",
    ".test-suite-execution-result-header a.secondary-button",
    ".test-suite-execution-result-header h3",
    ".test-suite-execution-result-header p",
    ".test-suite-execution-result-panel",
    ".test-suite-execution-result-table",
    ".test-suite-execution-result-table a.secondary-button",
    ".test-suite-module-button",
    ".test-suite-module-button span:first-child",
    ".test-suite-module-button span:last-child",
    ".test-suite-module-button.active",
    ".test-suite-module-button:hover",
    ".test-suite-module-list",
    ".test-suite-module-panel",
    ".test-suite-module-title",
    ".test-suite-name-button",
    ".test-suite-name-button:hover",
    ".test-suite-panel",
    ".test-suite-progress-bar",
    ".test-suite-progress-bar span",
    ".test-suite-progress-body",
    ".test-suite-progress-log-panel",
    ".test-suite-progress-log-panel .execution-log",
    ".test-suite-progress-modal",
    ".test-suite-progress-stats",
    ".test-suite-progress-stats div",
    ".test-suite-progress-stats span",
    ".test-suite-progress-stats strong",
    ".test-suite-progress-summary",
    ".test-suite-result",
    ".test-suite-result strong",
    ".test-suite-script-panel",
    ".test-suite-script-table",
    ".test-suite-script-table-panel",
    ".test-suite-scripts-content",
    ".test-suite-scripts-layout",
    ".test-suite-status-chip",
    ".test-suite-status-chip.error",
    ".test-suite-status-chip.running",
    ".test-suite-status-chip.success",
    ".test-suite-table",
    ".test-suite-tabs",
    ".test-suite-toolbar",
    ".test-suite-toolbar h3",
    ".test-suite-toolbar p",
    ".test-suite-video-modal",
}

# These composed selectors retain the priority that feature variants had over
# earlier shared table, tab, and execution-log rules before the top-level import.
CASCADE_GUARD_SELECTORS = {
    ".execution-log-panel.test-suite-progress-log-panel",
    ".module-script-table.test-suite-execution-result-table",
    ".module-script-table.test-suite-script-table",
    ".module-script-table.test-suite-table",
    ".script-tabs.test-suite-tabs",
}

SHARED_SELECTORS_THAT_STAY = {
    ".compact-modal",
    ".execution-mode-options",
    ".execution-video",
    ".execution-video-path",
    ".execution-video-wrap",
    ".modal",
    ".modal-body",
    ".module-script-table",
    ".status-badge",
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


class TestSuiteStylesTests(unittest.TestCase):
    def test_test_suite_import_follows_existing_feature_imports(self):
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

        self.assertTrue(TEST_SUITE_STYLESHEET.is_file())
        self.assertEqual(import_lines[:3], EXPECTED_IMPORT_PREFIX)
        self.assertEqual(shared_lines[:first_rule_index], [*import_lines, ""])
        self.assertTrue(shared_lines[first_rule_index].startswith(":root"))

    def test_all_test_suite_selectors_live_in_the_feature_stylesheet(self):
        feature_selectors = css_selectors(
            TEST_SUITE_STYLESHEET.read_text(encoding="utf-8")
        )
        shared_selectors = css_selectors(
            SHARED_STYLESHEET.read_text(encoding="utf-8")
        )

        self.assertEqual(
            feature_selectors,
            EXPECTED_TEST_SUITE_SELECTORS | CASCADE_GUARD_SELECTORS,
        )
        leaked_selectors = {
            selector
            for selector in shared_selectors
            if "test-suite" in selector or "suite-script" in selector
        }
        self.assertEqual(leaked_selectors, set())
        self.assertTrue(SHARED_SELECTORS_THAT_STAY <= shared_selectors)

    def test_stylesheets_have_balanced_css_blocks(self):
        for stylesheet in (SHARED_STYLESHEET, TEST_SUITE_STYLESHEET):
            with self.subTest(stylesheet=stylesheet.name):
                assert_balanced_css_braces(
                    self,
                    stylesheet.read_text(encoding="utf-8"),
                    stylesheet.name,
                )

    def test_flask_serves_test_suite_stylesheet(self):
        client = app.app.test_client()

        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            response = client.get("/static/css/features/test-suites.css")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/css")
            self.assertIn(b".test-suite-panel", response.data)
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
