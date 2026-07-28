"""Authentication use cases independent from Flask and session storage."""

from dataclasses import dataclass
from typing import Callable

from test_plan_viewer.auth import model


class AuthNotFoundError(Exception):
    """Raised when an addressed user or role no longer exists."""


@dataclass(frozen=True)
class AuthServiceDependencies:
    """Repository and password operations required by auth use cases."""

    repository: object
    get_auth_config: Callable
    check_password_hash: Callable
    generate_password_hash: Callable


class AuthService:
    """Validate auth requests and coordinate persistence."""

    def __init__(self, dependencies):
        if not isinstance(dependencies, AuthServiceDependencies):
            raise TypeError(
                "dependencies must be AuthServiceDependencies"
            )
        self.dependencies = dependencies

    @property
    def repository(self):
        return self.dependencies.repository

    @property
    def menu_permissions(self):
        return model.AUTH_MENU_PERMISSIONS

    def get_auth_config(self):
        return self.dependencies.get_auth_config()

    def load_current_user(self, session_user_id):
        if not session_user_id:
            return None
        try:
            user_id = int(session_user_id)
        except (TypeError, ValueError):
            return None

        user = self.repository.get_user_by_id(user_id)
        if (
            not user
            or user.get("status")
            != model.AUTH_USER_STATUS_ACTIVE
        ):
            return None
        return user

    def load_user_permission_codes(self, user_id):
        return self.repository.load_user_permission_codes(user_id)

    def build_auth_payload(self, user, permission_codes=None):
        if permission_codes is None:
            permission_codes = self.load_user_permission_codes(
                user["id"]
            )
        return model.build_auth_payload(
            user,
            permission_codes,
        )

    def authenticate(self, username, password):
        username = model.validate_username(username)
        password = str(password or "")
        return self.repository.authenticate_user(
            username,
            password,
            self.dependencies.check_password_hash,
        )

    def list_roles(self):
        rows, permissions = self.repository.list_roles()
        return [
            model.serialize_role(
                row,
                permissions.get(int(row["id"]), []),
            )
            for row in rows
        ]

    def create_role(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        return self.repository.create_role(
            model.validate_role_code(payload.get("code")),
            model.normalize_role_name(payload.get("name")),
            model.normalize_description(
                payload.get("description")
            ),
            model.normalize_auth_status(payload.get("status")),
            model.normalize_permission_codes(
                payload.get("permissions")
            ),
        )

    def update_role(self, role_id, payload):
        payload = payload if isinstance(payload, dict) else {}
        updated_role_id = self.repository.update_role(
            role_id,
            model.normalize_role_name(payload.get("name")),
            model.normalize_description(
                payload.get("description")
            ),
            model.normalize_auth_status(payload.get("status")),
            model.normalize_permission_codes(
                payload.get("permissions")
            ),
        )
        if updated_role_id is None:
            raise AuthNotFoundError("角色不存在。")
        return updated_role_id

    def list_users(self):
        rows, roles = self.repository.list_users()
        return [
            model.serialize_user(
                row,
                roles.get(int(row["id"]), []),
            )
            for row in rows
        ]

    def create_user(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        username = model.validate_username(
            payload.get("username")
        )
        password = model.normalize_password(
            payload.get("password"),
            required=True,
        )
        return self.repository.create_user(
            username,
            self.dependencies.generate_password_hash(password),
            model.normalize_display_name(
                payload.get("display_name"),
                username,
            ),
            model.normalize_auth_status(payload.get("status")),
            model.normalize_id_list(payload.get("role_ids")),
        )

    def update_user(
        self,
        user_id,
        payload,
        current_user_id=None,
    ):
        payload = payload if isinstance(payload, dict) else {}
        display_name = model.normalize_display_name(
            payload.get("display_name"),
            "",
        )
        status = model.normalize_auth_status(
            payload.get("status")
        )
        role_ids = model.normalize_id_list(
            payload.get("role_ids")
        )
        if (
            int(current_user_id or 0) == user_id
            and status != model.AUTH_USER_STATUS_ACTIVE
        ):
            raise ValueError("不能禁用当前登录账号。")

        updated_user_id = self.repository.update_user(
            user_id,
            display_name,
            status,
            role_ids,
        )
        if updated_user_id is None:
            raise AuthNotFoundError("用户不存在。")
        return updated_user_id

    def reset_user_password(self, user_id, payload):
        payload = payload if isinstance(payload, dict) else {}
        password = model.normalize_password(
            payload.get("password"),
            required=True,
        )
        updated_user_id = self.repository.reset_user_password(
            user_id,
            self.dependencies.generate_password_hash(password),
        )
        if updated_user_id is None:
            raise AuthNotFoundError("用户不存在。")
        return updated_user_id
