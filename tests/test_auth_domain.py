import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_plan_viewer.auth import model, repository, service


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_values=None,
        fetchall_values=None,
        lastrowid=41,
    ):
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_values = list(fetchall_values or [])
        self.lastrowid = lastrowid
        self.execute_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def fetchone(self):
        if not self.fetchone_values:
            return None
        return self.fetchone_values.pop(0)

    def fetchall(self):
        if not self.fetchall_values:
            return []
        return self.fetchall_values.pop(0)


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commit_count += 1


def make_repository(cursor=None, **overrides):
    cursor = cursor or FakeCursor()
    connection = FakeConnection(cursor)
    values = {
        "get_auth_config": lambda: {"enabled": True},
        "get_platform_database_config": (
            lambda: {"enabled": True, "table_prefix": ""}
        ),
        "ensure_platform_database_schema": Mock(),
        "platform_table_sql": (
            lambda _config, table_name: f"`{table_name}`"
        ),
        "platform_mysql_connection": lambda _config: connection,
        "current_time_ms": lambda: 123456,
    }
    values.update(overrides)
    dependencies = repository.AuthRepositoryDependencies(**values)
    return repository.AuthRepository(dependencies), connection


class FakeAuthRepository:
    def __init__(self):
        self.users = {}
        self.permissions = {}
        self.roles = {}
        self.role_rows = []
        self.role_permissions = {}
        self.user_rows = []
        self.user_roles = {}
        self.authenticated_user = None
        self.created_role_args = None
        self.updated_role_args = None
        self.created_user_args = None
        self.updated_user_args = None
        self.reset_password_args = None
        self.updated_role_id = 11
        self.updated_user_id = 21
        self.reset_user_id = 21

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def load_user_permission_codes(self, user_id):
        return list(self.permissions.get(user_id, []))

    def load_user_role_codes(self, user_id):
        return list(self.roles.get(user_id, []))

    def authenticate_user(
        self,
        username,
        password,
        check_password_hash,
    ):
        self.authenticate_args = (
            username,
            password,
            check_password_hash,
        )
        return self.authenticated_user

    def list_roles(self):
        return self.role_rows, self.role_permissions

    def create_role(self, *args):
        self.created_role_args = args
        return 10

    def update_role(self, *args):
        self.updated_role_args = args
        return self.updated_role_id

    def list_users(self):
        return self.user_rows, self.user_roles

    def create_user(self, *args):
        self.created_user_args = args
        return 20

    def update_user(self, *args):
        self.updated_user_args = args
        return self.updated_user_id

    def reset_user_password(self, *args):
        self.reset_password_args = args
        return self.reset_user_id


def make_service(fake_repository=None):
    fake_repository = fake_repository or FakeAuthRepository()
    dependencies = service.AuthServiceDependencies(
        repository=fake_repository,
        get_auth_config=lambda: {"enabled": True},
        check_password_hash=lambda stored, supplied: (
            stored == f"hash:{supplied}"
        ),
        generate_password_hash=lambda password: f"hash:{password}",
    )
    return service.AuthService(dependencies), fake_repository


class AuthModelTests(unittest.TestCase):
    def test_validation_and_serialization_match_the_auth_contract(self):
        self.assertEqual(model.validate_username(" admin.user "), "admin.user")
        self.assertEqual(model.validate_role_code(" qa-admin "), "qa-admin")
        self.assertEqual(
            model.normalize_id_list([1, "2", 1]),
            [1, 2],
        )
        self.assertEqual(
            model.normalize_permission_codes(
                ["menu.users", "menu.users", "menu.roles"]
            ),
            ["menu.users", "menu.roles"],
        )

        for value in ("a", "中文用户", "../admin"):
            with (
                self.subTest(username=value),
                self.assertRaises(ValueError),
            ):
                model.validate_username(value)

        payload = model.build_auth_payload(
            {
                "id": 7,
                "username": "admin",
                "display_name": "管理员",
            },
            {"menu.roles", "menu.requirements"},
        )
        self.assertEqual(
            payload,
            {
                "user": {
                    "id": 7,
                    "username": "admin",
                    "display_name": "管理员",
                },
                "permissions": [
                    "menu.requirements",
                    "menu.roles",
                ],
                "menus": ["requirements", "roles"],
                "is_admin": False,
            },
        )

    def test_permission_policy_uses_endpoint_and_http_method(self):
        self.assertTrue(model.has_any_permission([], set()))
        self.assertTrue(
            model.has_any_permission(
                {"menu.users"},
                {"menu.users", "menu.roles"},
            )
        )
        self.assertFalse(
            model.has_any_permission(
                {"menu.scripts"},
                {"menu.users", "menu.roles"},
            )
        )
        self.assertEqual(
            model.required_permissions_for_endpoint(
                "auth.update_auth_role",
                "PUT",
            ),
            frozenset({"menu.users", "menu.roles"}),
        )
        self.assertTrue(
            model.is_auth_public_endpoint(
                "auth.auth_login",
                "POST",
            )
        )
        self.assertFalse(
            model.is_auth_public_endpoint(
                "auth.auth_login",
                "GET",
            )
        )
        self.assertEqual(
            model.required_permissions_for_endpoint(
                "projects.create_project",
                "POST",
            ),
            frozenset({"menu.projectSettings"}),
        )
        self.assertEqual(
            model.required_permissions_for_endpoint(
                "projects.list_projects",
                "GET",
            ),
            frozenset(),
        )
        self.assertIsNone(
            model.required_permissions_for_endpoint(
                "unregistered.api",
                "GET",
            )
        )


class AuthRepositoryTests(unittest.TestCase):
    def test_database_configuration_requires_both_auth_and_mysql(self):
        ensure_schema = Mock()
        auth_disabled, _connection = make_repository(
            get_auth_config=lambda: {"enabled": False},
            ensure_platform_database_schema=ensure_schema,
        )
        with self.assertRaisesRegex(RuntimeError, "登录鉴权未启用"):
            auth_disabled.get_database_config()
        ensure_schema.assert_not_called()

        mysql_disabled, _connection = make_repository(
            get_platform_database_config=lambda: {"enabled": False},
            ensure_platform_database_schema=ensure_schema,
        )
        with self.assertRaisesRegex(RuntimeError, "platform_database"):
            mysql_disabled.get_database_config()
        ensure_schema.assert_not_called()

    def test_authentication_updates_login_time_only_for_active_user(self):
        user = {
            "id": 5,
            "username": "admin",
            "status": "active",
            "password_hash": "hash:secret",
        }
        cursor = FakeCursor(fetchone_values=[user])
        auth_repository, connection = make_repository(cursor)

        authenticated = auth_repository.authenticate_user(
            "admin",
            "secret",
            lambda stored, supplied: stored == f"hash:{supplied}",
        )

        self.assertIs(authenticated, user)
        self.assertEqual(authenticated["last_login_at"], 123456)
        self.assertEqual(connection.commit_count, 1)
        update_calls = [
            (sql, params)
            for sql, params in cursor.execute_calls
            if "SET last_login_at" in sql
        ]
        self.assertEqual(
            update_calls[0][1],
            (123456, 123456, 5),
        )

        disabled_cursor = FakeCursor(
            fetchone_values=[
                {
                    **user,
                    "status": "disabled",
                }
            ]
        )
        disabled_repository, disabled_connection = make_repository(
            disabled_cursor
        )
        self.assertIsNone(
            disabled_repository.authenticate_user(
                "admin",
                "secret",
                lambda _stored, _supplied: True,
            )
        )
        self.assertEqual(disabled_connection.commit_count, 0)

    def test_admin_role_cannot_be_disabled_or_lose_permissions(self):
        cursor = FakeCursor(
            fetchone_values=[
                {
                    "id": 1,
                    "code": "admin",
                    "status": "active",
                }
            ]
        )
        auth_repository, connection = make_repository(cursor)

        updated = auth_repository.update_role(
            1,
            "管理员",
            "系统角色",
            "disabled",
            [],
        )

        self.assertEqual(updated, 1)
        self.assertEqual(connection.commit_count, 1)
        role_update = next(
            (sql, params)
            for sql, params in cursor.execute_calls
            if "UPDATE `platform_roles`" in sql
        )
        self.assertEqual(role_update[1][2], "active")
        permission_inserts = [
            params
            for sql, params in cursor.execute_calls
            if "INSERT INTO `platform_role_permissions`" in sql
        ]
        self.assertEqual(
            [params[1] for params in permission_inserts],
            [
                permission["code"]
                for permission in model.AUTH_MENU_PERMISSIONS
            ],
        )

    def test_user_listing_and_role_validation_keep_shape_and_errors(self):
        cursor = FakeCursor(
            fetchall_values=[
                [
                    {
                        "id": 2,
                        "username": "tester",
                        "display_name": "测试员",
                        "status": "active",
                    }
                ],
                [
                    {
                        "user_id": 2,
                        "id": 9,
                        "code": "qa",
                        "name": "测试",
                        "status": "active",
                        "is_system": 0,
                    }
                ],
            ]
        )
        auth_repository, _connection = make_repository(cursor)

        rows, roles = auth_repository.list_users()

        self.assertEqual(rows[0]["username"], "tester")
        self.assertEqual(
            roles,
            {
                2: [
                    {
                        "id": 9,
                        "code": "qa",
                        "name": "测试",
                        "status": "active",
                        "is_system": False,
                    }
                ]
            },
        )

        validation_cursor = FakeCursor(
            fetchall_values=[[{"id": 1}]]
        )
        with self.assertRaisesRegex(
            ValueError,
            "角色不存在：2",
        ):
            auth_repository.validate_existing_role_ids(
                validation_cursor,
                {"enabled": True},
                [1, 2],
            )


class AuthServiceTests(unittest.TestCase):
    def test_session_user_resolution_and_payload_are_framework_free(self):
        auth_service, fake_repository = make_service()
        active_user = {
            "id": 7,
            "username": "admin",
            "display_name": "管理员",
            "status": "active",
        }
        fake_repository.users = {
            7: active_user,
            8: {**active_user, "id": 8, "status": "disabled"},
        }
        fake_repository.permissions = {
            7: ["menu.roles", "menu.requirements"]
        }
        fake_repository.roles = {7: ["admin"]}

        self.assertIsNone(auth_service.load_current_user("invalid"))
        self.assertIsNone(auth_service.load_current_user(8))
        self.assertIs(
            auth_service.load_current_user("7"),
            active_user,
        )
        self.assertEqual(
            auth_service.build_auth_payload(active_user),
            {
                "user": {
                    "id": 7,
                    "username": "admin",
                    "display_name": "管理员",
                },
                "permissions": [
                    "menu.requirements",
                    "menu.roles",
                ],
                "menus": ["requirements", "roles"],
                "is_admin": True,
            },
        )

    def test_role_and_user_commands_normalize_before_persistence(self):
        auth_service, fake_repository = make_service()

        role_id = auth_service.create_role(
            {
                "code": " qa ",
                "name": " 测试角色 ",
                "description": " 说明 ",
                "status": "ACTIVE",
                "permissions": [
                    "menu.scripts",
                    "menu.scripts",
                ],
            }
        )
        user_id = auth_service.create_user(
            {
                "username": " tester ",
                "password": "password-123",
                "display_name": " 测试员 ",
                "status": "active",
                "role_ids": [2, "2", 3],
            }
        )

        self.assertEqual(role_id, 10)
        self.assertEqual(
            fake_repository.created_role_args,
            (
                "qa",
                "测试角色",
                "说明",
                "active",
                ["menu.scripts"],
            ),
        )
        self.assertEqual(user_id, 20)
        self.assertEqual(
            fake_repository.created_user_args,
            (
                "tester",
                "hash:password-123",
                "测试员",
                "active",
                [2, 3],
            ),
        )

    def test_self_disable_and_missing_records_keep_distinct_errors(self):
        auth_service, fake_repository = make_service()
        with self.assertRaisesRegex(
            ValueError,
            "不能禁用当前登录账号",
        ):
            auth_service.update_user(
                7,
                {
                    "display_name": "管理员",
                    "status": "disabled",
                    "role_ids": [],
                },
                current_user_id=7,
            )
        self.assertIsNone(fake_repository.updated_user_args)

        fake_repository.updated_role_id = None
        with self.assertRaisesRegex(
            service.AuthNotFoundError,
            "角色不存在",
        ):
            auth_service.update_role(
                99,
                {
                    "name": "缺失角色",
                    "description": "",
                    "status": "active",
                    "permissions": [],
                },
            )

        fake_repository.reset_user_id = None
        with self.assertRaisesRegex(
            service.AuthNotFoundError,
            "用户不存在",
        ):
            auth_service.reset_user_password(
                99,
                {"password": "new-password"},
            )


class AuthPackageBoundaryTests(unittest.TestCase):
    def test_only_web_delivery_modules_import_flask(self):
        package_root = (
            Path(__file__).resolve().parents[1]
            / "test_plan_viewer"
        )
        violations = []
        for source_file in package_root.rglob("*.py"):
            tree = ast.parse(
                source_file.read_text(encoding="utf-8"),
                filename=str(source_file),
            )
            imports_flask = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports_flask = any(
                        alias.name == "flask"
                        or alias.name.startswith("flask.")
                        for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    imports_flask = (
                        node.module == "flask"
                        or str(node.module or "").startswith("flask.")
                    )
                if imports_flask:
                    break
            if (
                imports_flask
                and "web" not in source_file.relative_to(
                    package_root
                ).parts
            ):
                violations.append(
                    str(source_file.relative_to(package_root))
                )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
