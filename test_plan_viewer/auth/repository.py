"""MySQL persistence for platform users, roles, and permissions."""

from dataclasses import dataclass
from typing import Callable

from test_plan_viewer.auth.model import (
    AUTH_MENU_PERMISSION_CODES,
    AUTH_MENU_PERMISSIONS,
    AUTH_USER_STATUS_ACTIVE,
)


@dataclass(frozen=True)
class AuthRepositoryDependencies:
    """Infrastructure supplied by the application composition root."""

    get_auth_config: Callable
    get_platform_database_config: Callable
    ensure_platform_database_schema: Callable
    platform_table_sql: Callable
    platform_mysql_connection: Callable
    current_time_ms: Callable


class AuthRepository:
    """Persist auth records without depending on Flask or request state."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            AuthRepositoryDependencies,
        ):
            raise TypeError(
                "dependencies must be AuthRepositoryDependencies"
            )
        self.dependencies = dependencies

    def get_database_config(self):
        auth = self.dependencies.get_auth_config()
        if not auth.get("enabled"):
            raise RuntimeError("平台登录鉴权未启用。")

        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise RuntimeError(
                "用户、角色和权限需要启用 platform_database。"
            )

        self.dependencies.ensure_platform_database_schema(config)
        return config

    def _table(self, config, table_name):
        return self.dependencies.platform_table_sql(
            config,
            table_name,
        )

    def get_user_row_by_id(self, cursor, config, user_id):
        users_table = self._table(config, "platform_users")
        cursor.execute(
            f"SELECT * FROM {users_table} WHERE id = %s",
            (user_id,),
        )
        return cursor.fetchone()

    def get_user_row_by_username(
        self,
        cursor,
        config,
        username,
    ):
        users_table = self._table(config, "platform_users")
        cursor.execute(
            f"SELECT * FROM {users_table} WHERE username = %s",
            (username,),
        )
        return cursor.fetchone()

    def get_user_by_id(self, user_id):
        config = self.get_database_config()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                return self.get_user_row_by_id(
                    cursor,
                    config,
                    user_id,
                )

    def load_roles_by_user_ids(self, cursor, config, user_ids):
        result = {
            int(user_id): []
            for user_id in user_ids
        }
        if not user_ids:
            return result

        placeholders = ",".join(["%s"] * len(user_ids))
        user_roles_table = self._table(
            config,
            "platform_user_roles",
        )
        roles_table = self._table(config, "platform_roles")
        cursor.execute(
            f"""
            SELECT ur.user_id, r.id, r.code, r.name, r.status,
                   r.is_system
            FROM {user_roles_table} ur
            JOIN {roles_table} r ON r.id = ur.role_id
            WHERE ur.user_id IN ({placeholders})
            ORDER BY r.name ASC
            """,
            tuple(user_ids),
        )
        for row in cursor.fetchall():
            result.setdefault(
                int(row["user_id"]),
                [],
            ).append(
                {
                    "id": int(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "status": row["status"],
                    "is_system": bool(row.get("is_system")),
                }
            )
        return result

    def load_permission_codes_by_role_ids(
        self,
        cursor,
        config,
        role_ids,
    ):
        result = {
            int(role_id): []
            for role_id in role_ids
        }
        if not role_ids:
            return result

        placeholders = ",".join(["%s"] * len(role_ids))
        role_permissions_table = self._table(
            config,
            "platform_role_permissions",
        )
        permissions_table = self._table(
            config,
            "platform_permissions",
        )
        cursor.execute(
            f"""
            SELECT rp.role_id, p.code
            FROM {role_permissions_table} rp
            JOIN {permissions_table} p
              ON p.code = rp.permission_code
            WHERE rp.role_id IN ({placeholders})
            ORDER BY p.sort_order ASC, p.code ASC
            """,
            tuple(role_ids),
        )
        for row in cursor.fetchall():
            result.setdefault(
                int(row["role_id"]),
                [],
            ).append(row["code"])
        return result

    def load_user_permission_codes(self, user_id):
        config = self.get_database_config()
        roles_table = self._table(config, "platform_roles")
        user_roles_table = self._table(
            config,
            "platform_user_roles",
        )
        role_permissions_table = self._table(
            config,
            "platform_role_permissions",
        )
        permissions_table = self._table(
            config,
            "platform_permissions",
        )

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT DISTINCT p.code, p.sort_order
                    FROM {user_roles_table} ur
                    JOIN {roles_table} r
                      ON r.id = ur.role_id
                     AND r.status = 'active'
                    JOIN {role_permissions_table} rp
                      ON rp.role_id = r.id
                    JOIN {permissions_table} p
                      ON p.code = rp.permission_code
                    WHERE ur.user_id = %s
                    ORDER BY p.sort_order ASC, p.code ASC
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()

        return [
            row["code"]
            for row in rows
            if row.get("code") in AUTH_MENU_PERMISSION_CODES
        ]

    def load_user_role_codes(self, user_id):
        config = self.get_database_config()
        roles_table = self._table(config, "platform_roles")
        user_roles_table = self._table(config, "platform_user_roles")
        with self.dependencies.platform_mysql_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT DISTINCT r.code
                    FROM {user_roles_table} ur
                    JOIN {roles_table} r
                      ON r.id = ur.role_id
                     AND r.status = 'active'
                    WHERE ur.user_id = %s
                    ORDER BY r.code ASC
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [str(row.get("code") or "") for row in rows if row.get("code")]

    def authenticate_user(
        self,
        username,
        password,
        check_password_hash,
    ):
        config = self.get_database_config()
        users_table = self._table(config, "platform_users")
        now_ms = self.dependencies.current_time_ms()

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                user = self.get_user_row_by_username(
                    cursor,
                    config,
                    username,
                )
                if (
                    not user
                    or user.get("status") != AUTH_USER_STATUS_ACTIVE
                    or not check_password_hash(
                        user.get("password_hash") or "",
                        password,
                    )
                ):
                    return None

                cursor.execute(
                    (
                        f"UPDATE {users_table} "
                        "SET last_login_at = %s, updated_at = %s "
                        "WHERE id = %s"
                    ),
                    (now_ms, now_ms, user["id"]),
                )
            connection.commit()

        user["last_login_at"] = now_ms
        return user

    def list_roles(self):
        config = self.get_database_config()
        roles_table = self._table(config, "platform_roles")
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    (
                        f"SELECT * FROM {roles_table} "
                        "ORDER BY is_system DESC, name ASC"
                    )
                )
                rows = cursor.fetchall()
                permissions = (
                    self.load_permission_codes_by_role_ids(
                        cursor,
                        config,
                        [row["id"] for row in rows],
                    )
                )
        return rows, permissions

    def create_role(
        self,
        code,
        name,
        description,
        status,
        permission_codes,
    ):
        now_ms = self.dependencies.current_time_ms()
        config = self.get_database_config()
        roles_table = self._table(config, "platform_roles")
        role_permissions_table = self._table(
            config,
            "platform_role_permissions",
        )

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {roles_table}
                      (code, name, description, status, is_system,
                       created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 0, %s, %s)
                    """,
                    (
                        code,
                        name,
                        description,
                        status,
                        now_ms,
                        now_ms,
                    ),
                )
                role_id = cursor.lastrowid
                for permission_code in permission_codes:
                    cursor.execute(
                        f"""
                        INSERT INTO {role_permissions_table}
                          (role_id, permission_code, created_at)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            role_id,
                            permission_code,
                            now_ms,
                        ),
                    )
            connection.commit()
        return role_id

    def update_role(
        self,
        role_id,
        name,
        description,
        status,
        permission_codes,
    ):
        now_ms = self.dependencies.current_time_ms()
        config = self.get_database_config()
        roles_table = self._table(config, "platform_roles")
        role_permissions_table = self._table(
            config,
            "platform_role_permissions",
        )

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {roles_table} WHERE id = %s",
                    (role_id,),
                )
                role = cursor.fetchone()
                if not role:
                    return None

                if role.get("code") == "admin":
                    status = AUTH_USER_STATUS_ACTIVE
                    permission_codes = [
                        permission["code"]
                        for permission in AUTH_MENU_PERMISSIONS
                    ]

                cursor.execute(
                    f"""
                    UPDATE {roles_table}
                    SET name = %s, description = %s, status = %s,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        name,
                        description,
                        status,
                        now_ms,
                        role_id,
                    ),
                )
                cursor.execute(
                    (
                        f"DELETE FROM {role_permissions_table} "
                        "WHERE role_id = %s"
                    ),
                    (role_id,),
                )
                for permission_code in permission_codes:
                    cursor.execute(
                        f"""
                        INSERT INTO {role_permissions_table}
                          (role_id, permission_code, created_at)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            role_id,
                            permission_code,
                            now_ms,
                        ),
                    )
            connection.commit()
        return role_id

    def list_users(self):
        config = self.get_database_config()
        users_table = self._table(config, "platform_users")
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    (
                        f"SELECT * FROM {users_table} "
                        "ORDER BY created_at DESC, id DESC"
                    )
                )
                rows = cursor.fetchall()
                roles = self.load_roles_by_user_ids(
                    cursor,
                    config,
                    [row["id"] for row in rows],
                )
        return rows, roles

    def validate_existing_role_ids(
        self,
        cursor,
        config,
        role_ids,
    ):
        if not role_ids:
            return []

        placeholders = ",".join(["%s"] * len(role_ids))
        roles_table = self._table(config, "platform_roles")
        cursor.execute(
            (
                f"SELECT id FROM {roles_table} "
                f"WHERE id IN ({placeholders})"
            ),
            tuple(role_ids),
        )
        existing = {
            int(row["id"])
            for row in cursor.fetchall()
        }
        missing = [
            role_id
            for role_id in role_ids
            if role_id not in existing
        ]
        if missing:
            raise ValueError(
                "角色不存在："
                + ", ".join(str(item) for item in missing)
            )
        return role_ids

    def replace_user_roles(
        self,
        cursor,
        config,
        user_id,
        role_ids,
    ):
        now_ms = self.dependencies.current_time_ms()
        role_ids = self.validate_existing_role_ids(
            cursor,
            config,
            role_ids,
        )
        user_roles_table = self._table(
            config,
            "platform_user_roles",
        )
        cursor.execute(
            (
                f"DELETE FROM {user_roles_table} "
                "WHERE user_id = %s"
            ),
            (user_id,),
        )
        for role_id in role_ids:
            cursor.execute(
                f"""
                INSERT INTO {user_roles_table}
                  (user_id, role_id, created_at)
                VALUES (%s, %s, %s)
                """,
                (user_id, role_id, now_ms),
            )

    def create_user(
        self,
        username,
        password_hash,
        display_name,
        status,
        role_ids,
    ):
        now_ms = self.dependencies.current_time_ms()
        config = self.get_database_config()
        users_table = self._table(config, "platform_users")

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {users_table}
                      (username, password_hash, display_name, status,
                       last_login_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NULL, %s, %s)
                    """,
                    (
                        username,
                        password_hash,
                        display_name,
                        status,
                        now_ms,
                        now_ms,
                    ),
                )
                user_id = cursor.lastrowid
                self.replace_user_roles(
                    cursor,
                    config,
                    user_id,
                    role_ids,
                )
            connection.commit()
        return user_id

    def update_user(
        self,
        user_id,
        display_name,
        status,
        role_ids,
    ):
        now_ms = self.dependencies.current_time_ms()
        config = self.get_database_config()
        users_table = self._table(config, "platform_users")

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                user = self.get_user_row_by_id(
                    cursor,
                    config,
                    user_id,
                )
                if not user:
                    return None

                display_name = (
                    display_name
                    or user["username"]
                )
                cursor.execute(
                    f"""
                    UPDATE {users_table}
                    SET display_name = %s, status = %s,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        display_name,
                        status,
                        now_ms,
                        user_id,
                    ),
                )
                self.replace_user_roles(
                    cursor,
                    config,
                    user_id,
                    role_ids,
                )
            connection.commit()
        return user_id

    def reset_user_password(self, user_id, password_hash):
        now_ms = self.dependencies.current_time_ms()
        config = self.get_database_config()
        users_table = self._table(config, "platform_users")

        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                user = self.get_user_row_by_id(
                    cursor,
                    config,
                    user_id,
                )
                if not user:
                    return None
                cursor.execute(
                    (
                        f"UPDATE {users_table} "
                        "SET password_hash = %s, updated_at = %s "
                        "WHERE id = %s"
                    ),
                    (password_hash, now_ms, user_id),
                )
            connection.commit()
        return user_id
