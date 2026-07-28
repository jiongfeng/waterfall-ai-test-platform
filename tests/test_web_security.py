import re
import unittest

from flask import Flask, session

from test_plan_viewer.web import create_application
from test_plan_viewer.web.security import (
    CONTENT_SECURITY_POLICY,
    CSRF_HEADER_NAME,
    CSRF_SESSION_KEY,
    PLAYWRIGHT_REPORT_CONTENT_SECURITY_POLICY,
    issue_csrf_token,
    validate_csrf_request,
)


class SecurityResponseHeaderTests(unittest.TestCase):
    def test_application_sets_security_headers_on_page_and_error_responses(self):
        application = create_application("security-headers")

        @application.get("/forced-error")
        def forced_error():
            return {"error": "forced"}, 503

        with application.test_client() as client:
            page = client.get("/")
            error = client.get("/forced-error")

        for response in (page, error):
            self.assertEqual(
                response.headers["Content-Security-Policy"],
                CONTENT_SECURITY_POLICY,
            )
            self.assertEqual(
                response.headers["X-Content-Type-Options"],
                "nosniff",
            )
            self.assertEqual(
                response.headers["Referrer-Policy"],
                "no-referrer",
            )
            self.assertEqual(
                response.headers["X-Frame-Options"],
                "DENY",
            )
            self.assertIn(
                "frame-ancestors 'none'",
                response.headers["Content-Security-Policy"],
            )

    def test_json_responses_are_redacted_without_hiding_platform_username(self):
        application = create_application(
            "security-json-redaction"
        )

        @application.get("/sensitive-error")
        def sensitive_error():
            return {
                "username": "platform-admin",
                "error": (
                    "Authorization: Bearer unknown-bearer"
                ),
                "details": {
                    "password": "unknown-password",
                    "access_token": "unknown-token",
                    "private_key": "unknown-private-key",
                    "access_key": "unknown-access-key",
                    "dbPassword": "unknown-db-password",
                    "authToken": "unknown-auth-token",
                    "secretKey": "unknown-secret-key",
                    "passphrase": "unknown-passphrase",
                    "password_env": "TARGET_DEMO_PASSWORD",
                    "credentials_migration_required": True,
                    "legacy_secret_fields": ["password"],
                },
                "script": {
                    "content": (
                        "const password = "
                        'process.env.TARGET_SYSTEM_PASSWORD ?? "";'
                    ),
                },
            }

        with application.test_client() as client:
            response = client.get("/sensitive-error")

        payload = response.get_json()
        self.assertEqual(
            payload["username"],
            "platform-admin",
        )
        self.assertNotIn(
            "unknown-bearer",
            response.get_data(as_text=True),
        )
        self.assertEqual(
            payload["details"]["password"],
            "******",
        )
        self.assertEqual(
            payload["details"]["access_token"],
            "******",
        )
        self.assertEqual(
            payload["details"]["private_key"],
            "******",
        )
        self.assertEqual(
            payload["details"]["access_key"],
            "******",
        )
        for key in (
            "dbPassword",
            "authToken",
            "secretKey",
            "passphrase",
        ):
            self.assertEqual(
                payload["details"][key],
                "******",
            )
        self.assertEqual(
            payload["details"]["password_env"],
            "TARGET_DEMO_PASSWORD",
        )
        self.assertTrue(
            payload["details"]["credentials_migration_required"]
        )
        self.assertEqual(
            payload["details"]["legacy_secret_fields"],
            ["password"],
        )
        self.assertEqual(
            payload["script"]["content"],
            (
                "const password = "
                'process.env.TARGET_SYSTEM_PASSWORD ?? "";'
            ),
        )

    def test_csp_keeps_scripts_same_origin_and_blocks_active_embeds(self):
        directives = {item.strip() for item in CONTENT_SECURITY_POLICY.split(";")}

        self.assertIn("default-src 'self'", directives)
        self.assertIn("script-src 'self'", directives)
        self.assertIn("object-src 'none'", directives)
        self.assertIn("base-uri 'none'", directives)
        self.assertIn("frame-ancestors 'none'", directives)
        script_directive = next(item for item in directives if item.startswith("script-src "))
        self.assertNotIn("'unsafe-inline'", script_directive)

    def test_playwright_report_uses_a_sandbox_compatible_networkless_policy(self):
        application = create_application("report-security-headers")
        application.add_url_rule(
            "/report",
            endpoint="get_playwright_report",
            view_func=lambda: "<script>renderReport()</script>",
        )

        with application.test_client() as client:
            response = client.get("/report")

        self.assertEqual(
            response.headers["Content-Security-Policy"],
            PLAYWRIGHT_REPORT_CONTENT_SECURITY_POLICY,
        )
        self.assertEqual(
            response.headers["X-Frame-Options"],
            "SAMEORIGIN",
        )
        self.assertIn(
            "connect-src 'none'",
            response.headers["Content-Security-Policy"],
        )
        self.assertIn(
            "frame-ancestors 'self'",
            response.headers["Content-Security-Policy"],
        )

    def test_page_exposes_session_token_without_inline_login_script(self):
        application = create_application("security-template")

        with application.test_client() as client:
            page = client.get("/")

        html = page.get_data(as_text=True)
        token_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            html,
        )
        self.assertIsNotNone(token_match)
        self.assertGreaterEqual(len(token_match.group(1)), 32)
        self.assertEqual(html.count('sandbox="allow-scripts allow-downloads"'), 2)


class CsrfValidationTests(unittest.TestCase):
    def setUp(self):
        self.application = Flask(__name__)
        self.application.secret_key = "csrf-validation-test"

    def test_session_token_is_stable_and_explicit_header_is_accepted(self):
        with self.application.test_request_context(
            "/api/example",
            method="POST",
        ):
            token = issue_csrf_token()
            self.assertEqual(issue_csrf_token(), token)

        with self.application.test_request_context(
            "/api/example",
            method="POST",
            headers={CSRF_HEADER_NAME: token},
        ):
            session[CSRF_SESSION_KEY] = token
            self.assertTrue(validate_csrf_request())

    def test_same_origin_origin_or_referer_is_accepted(self):
        for headers in (
            {"Origin": "http://localhost"},
            {"Referer": "http://localhost/application/page"},
        ):
            with self.subTest(headers=headers):
                with self.application.test_request_context(
                    "/api/example",
                    method="DELETE",
                    base_url="http://localhost/",
                    headers=headers,
                ):
                    self.assertTrue(validate_csrf_request())

    def test_missing_cross_origin_and_null_origins_fail_closed(self):
        for headers in (
            {},
            {"Origin": "https://attacker.example"},
            {"Origin": "null"},
            {
                "Origin": "https://attacker.example",
                "Referer": "http://localhost/",
            },
            {"Referer": "https://attacker.example/path"},
        ):
            with self.subTest(headers=headers):
                with self.application.test_request_context(
                    "/api/example",
                    method="PATCH",
                    base_url="http://localhost/",
                    headers=headers,
                ):
                    self.assertFalse(validate_csrf_request())

    def test_safe_methods_do_not_require_origin_or_token(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                with self.application.test_request_context(
                    "/api/example",
                    method=method,
                ):
                    self.assertTrue(validate_csrf_request())


if __name__ == "__main__":
    unittest.main()
