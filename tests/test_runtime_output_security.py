import io
import json
import os
import unittest
from urllib import error as urlerror
from unittest.mock import patch

import app


class RuntimeOutputSecurityTests(unittest.TestCase):
    def setUp(self):
        self.secret = "runtime-secret-value"
        self.environment = patch.dict(
            os.environ,
            {"TARGET_RUNTIME_PASSWORD": self.secret},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    @staticmethod
    def _http_error(body):
        return urlerror.HTTPError(
            "http://opencode.invalid",
            500,
            "Server Error",
            {},
            io.BytesIO(body.encode("utf-8")),
        )

    def test_opencode_http_error_body_is_redacted(self):
        unknown_secret = "not-present-in-the-environment"
        with (
            patch.object(
                app,
                "opencode_url",
                return_value="http://opencode.invalid/session",
            ),
            patch.object(app, "opencode_headers", return_value={}),
            patch.object(
                app,
                "opencode_project_query",
                return_value={},
            ),
            patch.object(
                app,
                "get_opencode_task_timeout_seconds",
                return_value=30,
            ),
            patch.object(
                app.urlrequest,
                "urlopen",
                side_effect=self._http_error(
                    (
                        '{"password":"'
                        f'{unknown_secret}'
                        '","access_token":"another-unknown"}'
                    )
                ),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.opencode_request("/session")

        self.assertNotIn(
            unknown_secret,
            str(raised.exception),
        )
        self.assertNotIn(
            "another-unknown",
            str(raised.exception),
        )
        self.assertIn("******", str(raised.exception))

    def test_opencode_event_stream_error_body_is_redacted(self):
        with (
            patch.object(
                app,
                "opencode_url",
                return_value="http://opencode.invalid/event",
            ),
            patch.object(app, "opencode_headers", return_value={}),
            patch.object(
                app,
                "opencode_project_query",
                return_value={},
            ),
            patch.object(
                app,
                "get_opencode_task_timeout_seconds",
                return_value=30,
            ),
            patch.object(
                app.urlrequest,
                "urlopen",
                side_effect=self._http_error(
                    f"token={self.secret}"
                ),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.opencode_event_stream()

        self.assertNotIn(self.secret, str(raised.exception))
        self.assertIn("******", str(raised.exception))

    def test_opencode_request_redacts_outbound_payload(self):
        captured = {}

        class SuccessfulResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b"{}"

        def capture_request(request_obj, timeout):
            captured["data"] = request_obj.data
            captured["timeout"] = timeout
            return SuccessfulResponse()

        with (
            patch.object(
                app,
                "opencode_url",
                return_value="http://opencode.invalid/session",
            ),
            patch.object(app, "opencode_headers", return_value={}),
            patch.object(
                app.urlrequest,
                "urlopen",
                side_effect=capture_request,
            ),
        ):
            app.opencode_request(
                "/session",
                payload={
                    "prompt": (
                        "password=unknown-outbound-secret "
                        f"environment-value={self.secret}"
                    ),
                    "metadata": {
                        "dbPassword": (
                            "unknown-structured-secret"
                        )
                    },
                },
                timeout=30,
            )

        payload_text = captured["data"].decode("utf-8")
        self.assertNotIn(
            "unknown-outbound-secret",
            payload_text,
        )
        self.assertNotIn(
            "unknown-structured-secret",
            payload_text,
        )
        self.assertNotIn(self.secret, payload_text)
        self.assertEqual(captured["timeout"], 30)

    def test_every_sse_payload_is_structurally_redacted(self):
        event_text = app.sse_payload(
            "done",
            {
                "ok": False,
                "error": f"password={self.secret}",
                "nested": {
                    "access_token": self.secret,
                    "message": f"failed with {self.secret}",
                },
                "text": "private_key=unknown-private-key",
            },
        )
        data_line = next(
            line
            for line in event_text.splitlines()
            if line.startswith("data: ")
        )
        payload = json.loads(data_line.removeprefix("data: "))

        self.assertNotIn(self.secret, event_text)
        self.assertEqual(
            payload["nested"]["access_token"],
            "******",
        )
        self.assertIn("******", payload["error"])
        self.assertNotIn(
            "unknown-private-key",
            event_text,
        )

    def test_structured_redaction_preserves_environment_reference_source(self):
        source = (
            "const password = "
            'process.env.TARGET_SYSTEM_PASSWORD ?? "";'
        )

        safe = app.redact_runtime_value(
            {
                "content": source,
                "error": "password=unknown-runtime-value",
            }
        )

        self.assertEqual(safe["content"], source)
        self.assertNotIn(
            "unknown-runtime-value",
            safe["error"],
        )

    def test_private_and_access_keys_are_redacted_in_structured_and_text_values(self):
        private_key = (
            "-----BEGIN "
            + "PRIVATE KEY-----\n"
            + "unknown-private-material\n"
            + "-----END "
            + "PRIVATE KEY-----"
        )
        private_key_name = "private_" + "key"
        access_key_name = "access_" + "key"
        safe = app.redact_runtime_value(
            {
                private_key_name: private_key,
                access_key_name: "AKIA" + "UNKNOWNACCESSKEY",
            }
        )

        self.assertEqual(safe[private_key_name], "******")
        self.assertEqual(safe[access_key_name], "******")
        self.assertNotIn(
            "unknown-private-material",
            app.redact_sensitive_text(private_key),
        )

        camel_case = {
            "dbPassword": "unknown-db-password",
            "authToken": "unknown-auth-token",
            "secretKey": "unknown-secret-key",
            "passphrase": "unknown-passphrase",
        }
        redacted_json = app.redact_sensitive_text(
            json.dumps(camel_case)
        )
        for secret in camel_case.values():
            self.assertNotIn(secret, redacted_json)

    def test_unknown_labeled_and_bearer_values_are_redacted(self):
        examples = (
            (
                "Authorization: Bearer unknown-bearer",
                "unknown-bearer",
            ),
            (
                '{"password":"unknown-password"}',
                "unknown-password",
            ),
            (
                '{"access_token":"unknown-token"}',
                "unknown-token",
            ),
            (
                'client_secret="unknown secret value"',
                "unknown secret value",
            ),
            (
                "Basic dXNlcjpwYXNz",
                "dXNlcjpwYXNz",
            ),
            (
                "authorization=Basic dXNlcjpwYXNz",
                "dXNlcjpwYXNz",
            ),
            (
                "password=correct horse battery staple",
                "correct horse battery staple",
            ),
            (
                "client_secret=alpha beta;",
                "alpha beta",
            ),
            (
                "password=process.env.PASSWORD||hardcoded",
                "hardcoded",
            ),
            (
                "token=${TOKEN}hardcoded",
                "hardcoded",
            ),
            (
                "secret=env://vault/ref?token=hardcoded",
                "hardcoded",
            ),
        )
        for source, secret in examples:
            with self.subTest(source=source):
                redacted = app.redact_sensitive_text(source)
                self.assertNotIn(secret, redacted)
                self.assertIn("******", redacted)

    def test_pure_environment_references_are_not_redacted(self):
        references = (
            "password=process.env.TARGET_PASSWORD",
            'password=process.env.TARGET_PASSWORD ?? ""',
            "token=${TARGET_TOKEN}",
            "secret=env://TARGET_SECRET",
        )

        for reference in references:
            with self.subTest(reference=reference):
                self.assertEqual(
                    app.redact_sensitive_text(reference),
                    reference,
                )


if __name__ == "__main__":
    unittest.main()
