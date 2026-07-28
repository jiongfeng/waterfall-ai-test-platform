import unittest

from test_plan_viewer.security import source


class SourceSecurityTests(unittest.TestCase):
    def test_rejects_known_target_credentials_and_password_fills(self):
        with self.assertRaisesRegex(ValueError, "plaintext credential"):
            source.assert_no_embedded_secrets(
                "await page.getByLabel('Password').fill('Real-Password-9!');",
            )

        with self.assertRaisesRegex(ValueError, "plaintext credential"):
            source.assert_no_embedded_secrets(
                "console.log('account-value');",
                environment={
                    "TARGET_ACCOUNT": "account-value",
                },
            )

    def test_accepts_environment_references(self):
        source.assert_no_embedded_secrets(
            (
                "await page.getByLabel('Password').fill("
                "process.env.TARGET_SYSTEM_PASSWORD ?? '');"
            )
        )
        source.assert_no_embedded_secrets(
            (
                "const password = "
                "process.env.TARGET_SYSTEM_PASSWORD ?? '';"
            )
        )

    def test_rejects_access_keys_and_private_key_material(self):
        access_key_name = "access_" + "key"
        access_key_value = "AKIA" + "UNKNOWNACCESSKEY"
        with self.assertRaisesRegex(ValueError, "plaintext credential"):
            source.assert_no_embedded_secrets(
                (
                    f"const {access_key_name} = "
                    f'"{access_key_value}";'
                )
            )
        private_key_text = (
            "-----BEGIN "
            + "PRIVATE KEY-----\n"
            + "unknown-private-material\n"
            + "-----END "
            + "PRIVATE KEY-----"
        )
        with self.assertRaisesRegex(ValueError, "private-key"):
            source.assert_no_embedded_secrets(private_key_text)

    def test_rejects_camel_case_secrets_and_tainted_references(self):
        examples = (
            'const clientSecret = "hardcoded-client-secret";',
            'const authToken = "hardcoded-auth-token";',
            'const dbPassword = "hardcoded-password";',
            'const accessToken = "hardcoded-access-token";',
            'const password = "hardcoded-process.env.PASSWORD";',
            'const password = "${TOKEN}hardcoded";',
            (
                "await page.getByLabel('Password')"
                '.fill("${PASSWORD}hardcoded");'
            ),
            (
                "const password = "
                'process.env.TARGET_PASSWORD || "fallback-secret";'
            ),
            (
                "accessToken = process.env.TARGET_TOKEN "
                '?? "fallback-token";'
            ),
            (
                "page.getByLabel('Password').fill("
                'process.env.TARGET_PASSWORD || "fallback");'
            ),
        )

        for example in examples:
            with self.subTest(example=example):
                with self.assertRaisesRegex(
                    ValueError,
                    "plaintext credential",
                ):
                    source.assert_no_embedded_secrets(example)

    def test_allows_empty_fallback_for_sensitive_environment_reference(self):
        source.assert_no_embedded_secrets(
            (
                "const password = "
                'process.env.TARGET_PASSWORD || "";'
            )
        )
        source.assert_no_embedded_secrets(
            (
                "const baseUrl = "
                'process.env.TARGET_BASE_URL || "https://example.test";'
            )
        )


if __name__ == "__main__":
    unittest.main()
