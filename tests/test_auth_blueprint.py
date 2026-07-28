import re
import unittest
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.auth import model
from test_plan_viewer.auth.service import AuthNotFoundError
from test_plan_viewer.configuration import APP_DIR
from test_plan_viewer.web.auth import (
    AuthWebServices,
    create_auth_blueprint,
)
from test_plan_viewer.web.security import CSRF_HEADER_NAME

SAME_ORIGIN_HEADERS = {"Origin": "http://localhost"}


ACTIVE_USER = {
    "id": 7,
    "username": "admin",
    "display_name": "管理员",
    "status": "active",
}


def build_payload(user, permission_codes=None):
    return model.build_auth_payload(
        user,
        (
            permission_codes
            if permission_codes is not None
            else ["menu.users", "menu.roles"]
        ),
    )


def make_services(**overrides):
    values = {
        "get_auth_config": Mock(return_value={"enabled": False}),
        "load_current_user": Mock(return_value=ACTIVE_USER),
        "load_user_permission_codes": Mock(
            return_value=["menu.users", "menu.roles"]
        ),
        "build_auth_payload": Mock(side_effect=build_payload),
        "authenticate": Mock(return_value=ACTIVE_USER),
        "list_roles": Mock(return_value=[]),
        "create_role": Mock(return_value=11),
        "update_role": Mock(return_value=11),
        "list_users": Mock(return_value=[]),
        "create_user": Mock(return_value=21),
        "update_user": Mock(return_value=21),
        "reset_user_password": Mock(return_value=21),
        "menu_permissions": model.AUTH_MENU_PERMISSIONS,
    }
    values.update(overrides)
    return AuthWebServices(**values)


def create_isolated_app(services):
    application = Flask(
        __name__,
        template_folder=str(APP_DIR / "templates"),
        static_folder=str(APP_DIR / "static"),
    )
    application.secret_key = "isolated-auth-test"
    application.register_blueprint(
        create_auth_blueprint(services)
    )

    @application.get("/")
    def home():
        return "home"

    @application.get("/plain")
    def plain():
        return "plain"

    return application


def set_session_user(client, user_id=7, username="admin"):
    with client.session_transaction() as current_session:
        current_session["user_id"] = user_id
        current_session["username"] = username


def add_project_creation_route(application, view_func=None):
    application.add_url_rule(
        "/api/projects",
        endpoint="projects.create_project",
        methods=["POST"],
        view_func=view_func or (lambda: {"ok": True}),
    )


class AuthBlueprintContractTests(unittest.TestCase):
    def test_blueprint_registers_all_stable_routes_and_guard(self):
        services = make_services()
        application = create_isolated_app(services)
        contracts = {
            (method, rule.rule)
            for rule in application.url_map.iter_rules()
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }

        self.assertTrue(
            {
                ("GET", "/login"),
                ("POST", "/api/auth/login"),
                ("POST", "/api/auth/logout"),
                ("GET", "/api/auth/me"),
                ("GET", "/api/admin/permissions"),
                ("GET", "/api/admin/roles"),
                ("POST", "/api/admin/roles"),
                ("PUT", "/api/admin/roles/<int:role_id>"),
                ("GET", "/api/admin/users"),
                ("POST", "/api/admin/users"),
                ("PUT", "/api/admin/users/<int:user_id>"),
                (
                    "POST",
                    (
                        "/api/admin/users/<int:user_id>/"
                        "reset-password"
                    ),
                ),
            }.issubset(contracts)
        )

    def test_disabled_auth_exposes_anonymous_full_menu_context(self):
        services = make_services()
        client = create_isolated_app(services).test_client()

        response = client.get("/api/auth/me")
        permissions = client.get("/api/admin/permissions")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["user"])
        self.assertEqual(
            response.json["permissions"],
            [
                permission["code"]
                for permission in model.AUTH_MENU_PERMISSIONS
            ],
        )
        self.assertEqual(
            response.json["menus"],
            [
                permission["section"]
                for permission in model.AUTH_MENU_PERMISSIONS
            ],
        )
        self.assertIsNone(response.json["error"])
        self.assertEqual(permissions.status_code, 200)
        self.assertEqual(
            permissions.json["permissions"],
            model.AUTH_MENU_PERMISSIONS,
        )

    def test_enabled_auth_redirects_pages_and_returns_api_login_hint(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
        )
        client = create_isolated_app(services).test_client()

        page_response = client.get("/")
        api_response = client.get("/api/auth/me")

        self.assertEqual(page_response.status_code, 302)
        self.assertEqual(
            page_response.headers["Location"],
            "/login",
        )
        self.assertEqual(api_response.status_code, 401)
        self.assertEqual(
            api_response.json,
            {"error": "请先登录。", "redirect": "/login"},
        )

    def test_invalid_session_is_cleared_before_unauthorized_response(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_current_user=Mock(return_value=None),
        )
        client = create_isolated_app(services).test_client()
        set_session_user(client, user_id="invalid")

        response = client.get("/api/auth/me")

        self.assertEqual(response.status_code, 401)
        with client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)
            self.assertNotIn("username", current_session)

    def test_permission_guard_uses_any_matching_admin_permission(self):
        allowed_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.users"]
            ),
        )
        allowed_client = create_isolated_app(
            allowed_services
        ).test_client()
        set_session_user(allowed_client)

        allowed = allowed_client.get("/api/admin/roles")

        self.assertEqual(allowed.status_code, 200)

        denied_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.scripts"]
            ),
        )
        denied_client = create_isolated_app(
            denied_services
        ).test_client()
        set_session_user(denied_client)

        denied = denied_client.get("/api/admin/roles")
        unrestricted = denied_client.get("/plain")

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json,
            {"error": "当前账号没有访问该功能的权限。"},
        )
        self.assertEqual(unrestricted.status_code, 200)

    def test_project_creation_policy_returns_401_403_and_success(self):
        anonymous_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.projectSettings"]
            ),
        )
        anonymous_app = create_isolated_app(anonymous_services)
        add_project_creation_route(anonymous_app)
        anonymous = anonymous_app.test_client().post(
            "/api/projects"
        )

        denied_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.plans"]
            ),
        )
        denied_app = create_isolated_app(denied_services)
        add_project_creation_route(denied_app)
        denied_client = denied_app.test_client()
        set_session_user(denied_client)
        denied = denied_client.post("/api/projects")

        allowed_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.projectSettings"]
            ),
        )
        allowed_app = create_isolated_app(allowed_services)
        add_project_creation_route(allowed_app)
        allowed_client = allowed_app.test_client()
        set_session_user(allowed_client)
        allowed = allowed_client.post(
            "/api/projects",
            headers=SAME_ORIGIN_HEADERS,
        )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(
            anonymous.json,
            {"error": "请先登录。", "redirect": "/login"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json,
            {"error": "当前账号没有访问该功能的权限。"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json, {"ok": True})

    def test_authenticated_unregistered_api_is_denied_by_default(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=list(model.AUTH_MENU_PERMISSION_CODES)
            ),
        )
        application = create_isolated_app(services)
        unregistered_view = Mock(return_value={"ok": True})

        def call_unregistered_view():
            return unregistered_view()

        application.add_url_rule(
            "/api/unregistered",
            endpoint="extension.unregistered_api",
            methods=["GET"],
            view_func=call_unregistered_view,
        )
        client = application.test_client()
        set_session_user(client)

        response = client.get("/api/unregistered")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json,
            {"error": "当前账号没有访问该功能的权限。"},
        )
        unregistered_view.assert_not_called()

    def test_guard_wraps_session_and_permission_loading_failures(self):
        session_error_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_current_user=Mock(
                side_effect=RuntimeError("user db down")
            ),
        )
        session_error_client = create_isolated_app(
            session_error_services
        ).test_client()
        set_session_user(session_error_client)

        session_error = session_error_client.get("/api/auth/me")

        self.assertEqual(session_error.status_code, 500)
        self.assertEqual(
            session_error.json,
            {"error": "读取登录状态失败：user db down"},
        )

        permission_error_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                side_effect=RuntimeError("permission db down")
            ),
        )
        permission_error_client = create_isolated_app(
            permission_error_services
        ).test_client()
        set_session_user(permission_error_client)

        permission_error = permission_error_client.get(
            "/api/auth/me"
        )

        self.assertEqual(permission_error.status_code, 500)
        self.assertEqual(
            permission_error.json,
            {"error": "读取用户权限失败：permission db down"},
        )


class AuthBlueprintSessionTests(unittest.TestCase):
    def test_login_page_keeps_disabled_and_authenticated_redirects(self):
        disabled_client = create_isolated_app(
            make_services()
        ).test_client()
        self.assertEqual(
            disabled_client.get("/login").headers["Location"],
            "/",
        )

        enabled_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
        )
        enabled_client = create_isolated_app(
            enabled_services
        ).test_client()
        anonymous = enabled_client.get("/login")
        self.assertEqual(anonymous.status_code, 200)
        self.assertIn("账号登录", anonymous.get_data(as_text=True))

        set_session_user(enabled_client)
        authenticated = enabled_client.get("/login")
        self.assertEqual(authenticated.status_code, 302)
        self.assertEqual(authenticated.headers["Location"], "/")

    def test_successful_login_sets_session_and_returns_ordered_payload(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
        )
        client = create_isolated_app(services).test_client()

        response = client.post(
            "/api/auth/login",
            headers=SAME_ORIGIN_HEADERS,
            json={
                "username": "admin",
                "password": "secret-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user"]["id"], 7)
        self.assertEqual(
            response.json["permissions"],
            ["menu.users", "menu.roles"],
        )
        self.assertIsNone(response.json["error"])
        services.authenticate.assert_called_once_with(
            "admin",
            "secret-password",
        )
        with client.session_transaction() as current_session:
            self.assertEqual(current_session["user_id"], 7)
            self.assertEqual(
                current_session["username"],
                "admin",
            )

    def test_login_error_statuses_and_logout_session_behavior(self):
        disabled_services = make_services()
        disabled_client = create_isolated_app(
            disabled_services
        ).test_client()
        disabled = disabled_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
        )
        self.assertEqual(disabled.status_code, 400)
        self.assertEqual(
            disabled.json["error"],
            "平台登录鉴权未启用。",
        )

        invalid_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            authenticate=Mock(return_value=None),
        )
        invalid_client = create_isolated_app(
            invalid_services
        ).test_client()
        invalid = invalid_client.post(
            "/api/auth/login",
            headers=SAME_ORIGIN_HEADERS,
            json={"username": "admin", "password": "bad"},
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(
            invalid.json,
            {"error": "用户名或密码错误。"},
        )

        validation_services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            authenticate=Mock(
                side_effect=ValueError("用户名格式错误。")
            ),
        )
        validation_client = create_isolated_app(
            validation_services
        ).test_client()
        validation = validation_client.post(
            "/api/auth/login",
            headers=SAME_ORIGIN_HEADERS,
            json={},
        )
        self.assertEqual(validation.status_code, 400)
        self.assertEqual(
            validation.json,
            {"error": "用户名格式错误。"},
        )

        set_session_user(invalid_client)
        logout = invalid_client.post(
            "/api/auth/logout",
            headers=SAME_ORIGIN_HEADERS,
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json, {"ok": True, "error": None})
        with invalid_client.session_transaction() as current_session:
            self.assertEqual(dict(current_session), {})

    def test_auth_me_uses_guard_user_and_permissions(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=[
                    "menu.requirements",
                    "menu.scripts",
                ]
            ),
        )
        client = create_isolated_app(services).test_client()
        set_session_user(client)

        response = client.get("/api/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["permissions"],
            ["menu.requirements", "menu.scripts"],
        )
        self.assertEqual(
            response.json["menus"],
            ["requirements", "scripts"],
        )


class AuthBlueprintCsrfTests(unittest.TestCase):
    def test_login_rejects_missing_and_cross_origin_requests(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
        )
        client = create_isolated_app(services).test_client()

        for headers in (
            {},
            {"Origin": "https://attacker.example"},
            {"Origin": "null"},
        ):
            with self.subTest(headers=headers):
                response = client.post(
                    "/api/auth/login",
                    headers=headers,
                    json={
                        "username": "admin",
                        "password": "secret-password",
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json,
                    {
                        "error": (
                            "请求来源校验失败，请刷新页面后重试。"
                        )
                    },
                )

        services.authenticate.assert_not_called()

    def test_login_accepts_token_issued_by_login_page(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
        )
        client = create_isolated_app(services).test_client()
        page = client.get("/login")
        token_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        )

        self.assertIsNotNone(token_match)
        response = client.post(
            "/api/auth/login",
            headers={
                CSRF_HEADER_NAME: token_match.group(1),
            },
            json={
                "username": "admin",
                "password": "secret-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        services.authenticate.assert_called_once()

    def test_authenticated_write_without_proof_is_rejected_before_view(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.users"]
            ),
        )
        client = create_isolated_app(services).test_client()
        set_session_user(client)

        response = client.put(
            "/api/admin/users/21",
            json={"display_name": "测试员"},
        )

        self.assertEqual(response.status_code, 403)
        services.update_user.assert_not_called()


class AuthBlueprintAdminCrudTests(unittest.TestCase):
    def test_success_payloads_keep_ids_and_current_user_context(self):
        services = make_services()
        client = create_isolated_app(services).test_client()

        create_role = client.post(
            "/api/admin/roles",
            json={"code": "qa"},
        )
        update_role = client.put(
            "/api/admin/roles/11",
            json={"name": "测试"},
        )
        create_user = client.post(
            "/api/admin/users",
            json={"username": "tester"},
        )
        update_user = client.put(
            "/api/admin/users/21",
            json={"display_name": "测试员"},
        )
        reset_password = client.post(
            "/api/admin/users/21/reset-password",
            json={"password": "new-password"},
        )

        self.assertEqual(
            create_role.json,
            {"ok": True, "role_id": 11, "error": None},
        )
        self.assertEqual(
            update_role.json,
            {"ok": True, "role_id": 11, "error": None},
        )
        self.assertEqual(
            create_user.json,
            {"ok": True, "user_id": 21, "error": None},
        )
        self.assertEqual(
            update_user.json,
            {"ok": True, "user_id": 21, "error": None},
        )
        self.assertEqual(
            reset_password.json,
            {"ok": True, "user_id": 21, "error": None},
        )
        services.update_user.assert_called_once_with(
            21,
            {"display_name": "测试员"},
            current_user_id=None,
        )

    def test_enabled_update_passes_the_authenticated_user_id(self):
        services = make_services(
            get_auth_config=Mock(return_value={"enabled": True}),
            load_user_permission_codes=Mock(
                return_value=["menu.users"]
            ),
        )
        client = create_isolated_app(services).test_client()
        set_session_user(client)

        response = client.put(
            "/api/admin/users/21",
            headers=SAME_ORIGIN_HEADERS,
            json={"display_name": "测试员"},
        )

        self.assertEqual(response.status_code, 200)
        services.update_user.assert_called_once_with(
            21,
            {"display_name": "测试员"},
            current_user_id=7,
        )

    def test_value_not_found_and_unexpected_errors_keep_status_contract(self):
        validation_services = make_services(
            create_role=Mock(
                side_effect=ValueError("角色名称不能为空。")
            ),
        )
        validation = create_isolated_app(
            validation_services
        ).test_client().post(
            "/api/admin/roles",
            json={},
        )
        self.assertEqual(validation.status_code, 400)
        self.assertEqual(
            validation.json,
            {"error": "角色名称不能为空。"},
        )

        missing_services = make_services(
            update_role=Mock(
                side_effect=AuthNotFoundError("角色不存在。")
            ),
            reset_user_password=Mock(
                side_effect=AuthNotFoundError("用户不存在。")
            ),
        )
        missing_client = create_isolated_app(
            missing_services
        ).test_client()
        missing_role = missing_client.put(
            "/api/admin/roles/99",
            json={"name": "missing"},
        )
        missing_user = missing_client.post(
            "/api/admin/users/99/reset-password",
            json={"password": "password"},
        )
        self.assertEqual(missing_role.status_code, 404)
        self.assertEqual(
            missing_role.json,
            {"error": "角色不存在。"},
        )
        self.assertEqual(missing_user.status_code, 404)
        self.assertEqual(
            missing_user.json,
            {"error": "用户不存在。"},
        )

        failed_services = make_services(
            list_users=Mock(side_effect=RuntimeError("db down")),
        )
        failed = create_isolated_app(
            failed_services
        ).test_client().get("/api/admin/users")
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(
            failed.json,
            {"error": "读取用户失败：db down"},
        )


if __name__ == "__main__":
    unittest.main()
