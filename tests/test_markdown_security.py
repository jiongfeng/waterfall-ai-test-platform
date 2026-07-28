import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from test_plan_viewer.security import markdown as markdown_security

ATTACK_MARKDOWN = """
# 安全标题

<script>script-payload</script>
<style>style-payload</style>
<iframe srcdoc="<script>alert(1)</script>">iframe-payload</iframe>
<svg><a href="javascript:alert(1)" onload="alert(1)">svg-payload</a></svg>
<math><mtext><img src="x" onerror="alert(1)"></mtext></math>
<img src="https://example.test/image.png" onerror="alert(1)" style="position:fixed">
<a href="javascript:alert(1)" onclick="alert(1)">dangerous-link</a>
<a href="jav&#x61;script:alert(1)">encoded-dangerous-link</a>
<a href="data:text/html,<script>alert(1)</script>">data-link</a>
"""


class MarkdownSanitizerTests(unittest.TestCase):
    def test_safe_markdown_formatting_survives_allowlist(self):
        rendered = markdown_security.render_markdown(
            """
# 标题

**加粗**和[链接](https://example.test/docs)。

```python
print("ok")
```

| 列一 | 列二 |
| --- | --- |
| A | B |

![截图](https://example.test/screenshot.png "示例")
"""
        )

        self.assertIn("<h1>标题</h1>", rendered)
        self.assertIn("<strong>加粗</strong>", rendered)
        self.assertIn('href="https://example.test/docs"', rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)
        self.assertIn('class="language-python"', rendered)
        self.assertIn("<table>", rendered)
        self.assertIn('src="https://example.test/screenshot.png"', rendered)
        self.assertIn('loading="lazy"', rendered)
        self.assertIn('referrerpolicy="no-referrer"', rendered)

    def test_active_content_attributes_and_dangerous_urls_are_removed(self):
        rendered = markdown_security.render_markdown(ATTACK_MARKDOWN)
        lowered = rendered.lower()

        for forbidden in (
            "<script",
            "<style",
            "<iframe",
            "<svg",
            "<math",
            "script-payload",
            "style-payload",
            "iframe-payload",
            "svg-payload",
            "onerror",
            "onload",
            "onclick",
            "javascript:",
            "data:text/html",
            "position:fixed",
        ):
            self.assertNotIn(forbidden, lowered)

        self.assertIn("<h1>安全标题</h1>", rendered)
        self.assertIn('src="https://example.test/image.png"', rendered)
        self.assertIn(">dangerous-link</a>", rendered)
        self.assertNotIn('href="javascript:', lowered)

    def test_raw_attributes_relative_urls_and_unapproved_classes_are_removed(self):
        rendered = markdown_security.render_markdown(
            """
<h2 id="dom-clobber" class="unsafe" onclick="alert(1)">标题</h2>
<a href="//attacker.test/path">协议相对链接</a>
<a href="/internal/path">相对链接</a>
<code class="language-python unsafe" onmouseover="alert(1)">code</code>
<!-- hidden comment -->
"""
        )

        self.assertIn("<h2>标题</h2>", rendered)
        self.assertIn(
            '<a rel="noopener noreferrer">协议相对链接</a>',
            rendered,
        )
        self.assertIn(
            '<a rel="noopener noreferrer">相对链接</a>',
            rendered,
        )
        self.assertIn(
            '<code class="language-python">code</code>',
            rendered,
        )
        for forbidden in (
            'id="dom-clobber"',
            'class="unsafe"',
            "onclick",
            "onmouseover",
            'href="//attacker.test/path"',
            'href="/internal/path"',
            "<!--",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_missing_sanitizer_fails_closed(self):
        with (
            patch.object(markdown_security, "_nh3", None),
            patch.object(
                markdown_security,
                "_NH3_IMPORT_ERROR",
                ImportError("nh3 unavailable"),
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "Markdown preview is unavailable",
            ),
        ):
            markdown_security.render_markdown("<img onerror=alert(1)>")

    def test_non_string_content_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "must be a string"):
            markdown_security.render_markdown(None)


class MarkdownPreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.client = app.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def assert_preview_is_sanitized(self, rendered):
        lowered = rendered.lower()
        self.assertIn("<h1>安全标题</h1>", rendered)
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<svg", lowered)
        self.assertNotIn("onerror", lowered)
        self.assertNotIn("javascript:", lowered)

    def test_plan_preview_route_returns_only_sanitized_html(self):
        plan_file = self.project_root / "specs" / "登录" / "登录.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text(ATTACK_MARKDOWN, encoding="utf-8")

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(app, "get_plan_file", return_value=plan_file),
            patch.object(app, "sync_plan_asset", return_value=None),
        ):
            response = self.client.get("/api/plans/%E7%99%BB%E5%BD%95/%E7%99%BB%E5%BD%95.md")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["markdown"], ATTACK_MARKDOWN)
        self.assert_preview_is_sanitized(response.json["html"])

    def test_requirement_preview_route_returns_only_sanitized_html(self):
        requirement = {
            "id": 3,
            "requirement_uid": "requirement-3",
            "title": "安全需求",
            "filename": "安全需求.md",
            "file_path": str(self.project_root / "安全需求.md"),
        }

        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(
                app,
                "get_requirement_by_uid",
                return_value=requirement,
            ),
            patch.object(app, "list_requirement_modules", return_value=[]),
            patch.object(
                app,
                "read_requirement_markdown",
                return_value=ATTACK_MARKDOWN,
            ),
        ):
            response = self.client.get("/api/requirements/requirement-3")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(
            response.json["requirement"]["markdown"],
            ATTACK_MARKDOWN,
        )
        self.assert_preview_is_sanitized(response.json["requirement"]["html"])


if __name__ == "__main__":
    unittest.main()
