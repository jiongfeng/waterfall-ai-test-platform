import unittest
from unittest.mock import patch

import app
from test_plan_viewer.auth import build_disabled_auth_payload
from test_plan_viewer.auth import model


class AuthPolicyTests(unittest.TestCase):
    def test_every_registered_api_method_has_exactly_one_policy(self):
        registered_routes = {
            (rule.endpoint, method)
            for rule in app.app.url_map.iter_rules()
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }
        registered_api_routes = {
            (rule.endpoint, method)
            for rule in app.app.url_map.iter_rules()
            if rule.rule.startswith("/api/")
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }
        declared_api_routes = (
            set(model.AUTH_API_PERMISSION_POLICY)
            | (
                set(model.AUTH_PUBLIC_ENDPOINT_METHODS)
                & registered_api_routes
            )
        )

        self.assertTrue(
            set(model.AUTH_API_PERMISSION_POLICY).isdisjoint(
                model.AUTH_PUBLIC_ENDPOINT_METHODS
            ),
            "同一 endpoint + method 不能同时声明为公开和受保护",
        )
        self.assertEqual(
            registered_api_routes - declared_api_routes,
            set(),
            "存在未登记权限策略的 API 路由",
        )
        self.assertEqual(
            set(model.AUTH_API_PERMISSION_POLICY)
            - registered_api_routes,
            set(),
            "权限策略引用了不存在的 API 路由",
        )
        self.assertEqual(
            set(model.AUTH_PUBLIC_ENDPOINT_METHODS)
            - registered_routes,
            set(),
            "公开访问策略引用了不存在的路由",
        )
        for required_permissions in (
            model.AUTH_API_PERMISSION_POLICY.values()
        ):
            self.assertTrue(
                required_permissions.issubset(
                    model.AUTH_MENU_PERMISSION_CODES
                ),
                "API 权限策略引用了不存在的权限码",
            )

    def test_disabled_auth_payload_exposes_all_configured_menus_without_a_user(self):
        payload = build_disabled_auth_payload(app.AUTH_MENU_PERMISSIONS)

        self.assertIsNone(payload["user"])
        self.assertEqual(
            payload["permissions"],
            [permission["code"] for permission in app.AUTH_MENU_PERMISSIONS],
        )
        self.assertEqual(
            payload["menus"],
            [permission["section"] for permission in app.AUTH_MENU_PERMISSIONS],
        )

    def test_auth_me_returns_anonymous_context_when_login_is_disabled(self):
        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            response = app.app.test_client().get("/api/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["user"])
        self.assertEqual(
            response.json["menus"],
            [permission["section"] for permission in app.AUTH_MENU_PERMISSIONS],
        )
        self.assertIsNone(response.json["error"])

    def test_auth_me_still_requires_a_user_when_login_is_enabled(self):
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": True}),
            patch.object(app, "load_current_user_from_session", return_value=None),
        ):
            response = app.app.test_client().get("/api/auth/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["redirect"], "/login")


if __name__ == "__main__":
    unittest.main()
