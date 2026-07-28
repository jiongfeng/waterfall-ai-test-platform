"""Persistence operations for project-scoped test suites."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TestSuiteRepositoryDependencies:
    """Application-owned collaborators used by the repository."""

    get_platform_database_config: Callable[[], dict]
    ensure_platform_database_schema: Callable[[dict], None]
    get_test_suites_table: Callable[[dict], str]
    get_test_suite_items_table: Callable[[dict], str]
    get_test_suite_tables: Callable[[], tuple]
    get_current_project_id: Callable[[], int]
    platform_mysql_connection: Callable[[dict], object]
    validate_suite_name: Callable[[object], str]
    validate_suite_description: Callable[[object], str]
    serialize_test_suite_item: Callable[[dict], dict]
    serialize_test_suite: Callable[[dict, list], dict]
    list_test_suite_items_by_suite_ids: Callable[..., dict]
    get_test_suite_row_by_uid: Callable[..., dict]
    ensure_test_suite_name_available: Callable[..., None]
    get_test_suite_payload: Callable[[str], dict]
    sanitize_suite_uid: Callable[[], str]
    current_time_ms: Callable[[], int]
    current_platform_author: Callable[[], str]
    normalize_suite_item_input: Callable[[dict], dict]
    sync_script_asset: Callable[..., dict]


class TestSuiteRepository:
    """Project-scoped test-suite repository."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            TestSuiteRepositoryDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "TestSuiteRepositoryDependencies instance"
            )
        self.dependencies = dependencies

    def get_tables(self):
        config = self.dependencies.get_platform_database_config()
        if not config.get("enabled"):
            raise RuntimeError(
                "未启用平台 MySQL 持久化，请在 config.json 配置 "
                "platform_database。"
            )
        self.dependencies.ensure_platform_database_schema(config)
        return (
            config,
            self.dependencies.get_test_suites_table(config),
            self.dependencies.get_test_suite_items_table(config),
        )

    def list_items_by_suite_ids(
        self,
        cursor,
        suite_items_table,
        project_id,
        suite_ids,
    ):
        result = {int(suite_id): [] for suite_id in suite_ids}
        if not suite_ids:
            return result
        placeholders = ",".join(["%s"] * len(suite_ids))
        cursor.execute(
            f"""
            SELECT *
            FROM {suite_items_table}
            WHERE project_id = %s AND suite_id IN ({placeholders})
            ORDER BY suite_id ASC, sort_order ASC, item_id ASC
            """,
            (project_id, *suite_ids),
        )
        for row in cursor.fetchall():
            result.setdefault(int(row["suite_id"]), []).append(
                self.dependencies.serialize_test_suite_item(row)
            )
        return result

    @staticmethod
    def get_row_by_uid(
        cursor,
        suites_table,
        project_id,
        suite_uid,
    ):
        cursor.execute(
            f"""
            SELECT *
            FROM {suites_table}
            WHERE project_id = %s AND suite_uid = %s
              AND status = 'active' AND deleted_at IS NULL
            LIMIT 1
            """,
            (project_id, suite_uid),
        )
        return cursor.fetchone()

    @staticmethod
    def ensure_name_available(
        cursor,
        suites_table,
        project_id,
        name,
        excluding_suite_id=None,
    ):
        if excluding_suite_id:
            cursor.execute(
                f"""
                SELECT suite_id
                FROM {suites_table}
                WHERE project_id = %s AND name = %s
                  AND deleted_at IS NULL AND suite_id <> %s
                LIMIT 1
                """,
                (
                    project_id,
                    name,
                    excluding_suite_id,
                ),
            )
        else:
            cursor.execute(
                f"""
                SELECT suite_id
                FROM {suites_table}
                WHERE project_id = %s AND name = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (project_id, name),
            )
        if cursor.fetchone():
            raise ValueError("测试集名字不能重复。")

    def list(self):
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {suites_table}
                    WHERE project_id = %s AND status = 'active'
                      AND deleted_at IS NULL
                    ORDER BY updated_at DESC, suite_id DESC
                    """,
                    (project_id,),
                )
                rows = cursor.fetchall()
                items_by_suite_id = (
                    self.dependencies.list_test_suite_items_by_suite_ids(
                        cursor,
                        suite_items_table,
                        project_id,
                        [row["suite_id"] for row in rows],
                    )
                )
        return [
            self.dependencies.serialize_test_suite(
                row,
                items_by_suite_id.get(int(row["suite_id"]), []),
            )
            for row in rows
        ]

    def get(self, suite_uid):
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return None
                items = (
                    self.dependencies.list_test_suite_items_by_suite_ids(
                        cursor,
                        suite_items_table,
                        project_id,
                        [suite["suite_id"]],
                    ).get(int(suite["suite_id"]), [])
                )
        return self.dependencies.serialize_test_suite(suite, items)

    def create(self, name, description=""):
        name = self.dependencies.validate_suite_name(name)
        description = self.dependencies.validate_suite_description(
            description
        )
        config, suites_table, _suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        suite_uid = self.dependencies.sanitize_suite_uid()
        now_ms = self.dependencies.current_time_ms()
        author = self.dependencies.current_platform_author()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                self.dependencies.ensure_test_suite_name_available(
                    cursor,
                    suites_table,
                    project_id,
                    name,
                )
                cursor.execute(
                    f"""
                    INSERT INTO {suites_table}
                      (project_id, suite_uid, name, description, status,
                       created_by, updated_by, created_at, updated_at,
                       deleted_at)
                    VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s,
                            NULL)
                    """,
                    (
                        project_id,
                        suite_uid,
                        name,
                        description,
                        author,
                        author,
                        now_ms,
                        now_ms,
                    ),
                )
            connection.commit()
        return self.dependencies.get_test_suite_payload(suite_uid)

    def update(
        self,
        suite_uid,
        name=None,
        description=None,
    ):
        config, suites_table, _suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        updates = ["updated_by = %s", "updated_at = %s"]
        values = [
            self.dependencies.current_platform_author(),
            self.dependencies.current_time_ms(),
        ]
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return None
                if name is not None:
                    name = self.dependencies.validate_suite_name(name)
                    self.dependencies.ensure_test_suite_name_available(
                        cursor,
                        suites_table,
                        project_id,
                        name,
                        suite["suite_id"],
                    )
                    updates.append("name = %s")
                    values.append(name)
                if description is not None:
                    updates.append("description = %s")
                    values.append(
                        self.dependencies.validate_suite_description(
                            description
                        )
                    )
                values.extend([project_id, suite_uid])
                cursor.execute(
                    f"UPDATE {suites_table} "
                    f"SET {', '.join(updates)} "
                    "WHERE project_id = %s AND suite_uid = %s",
                    values,
                )
            connection.commit()
        return self.dependencies.get_test_suite_payload(suite_uid)

    def delete(self, suite_uid):
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return False
                cursor.execute(
                    f"UPDATE {suites_table} "
                    "SET status = 'deleted', deleted_at = %s, "
                    "updated_at = %s "
                    "WHERE project_id = %s AND suite_id = %s",
                    (
                        now_ms,
                        now_ms,
                        project_id,
                        suite["suite_id"],
                    ),
                )
                cursor.execute(
                    f"DELETE FROM {suite_items_table} "
                    "WHERE project_id = %s AND suite_id = %s",
                    (project_id, suite["suite_id"]),
                )
            connection.commit()
        return True

    def add_items(self, suite_uid, raw_items):
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("items must be a non-empty list.")
        items = [
            self.dependencies.normalize_suite_item_input(item)
            for item in raw_items
        ]
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return None
                cursor.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) AS max_order "
                    f"FROM {suite_items_table} "
                    "WHERE project_id = %s AND suite_id = %s",
                    (project_id, suite["suite_id"]),
                )
                sort_order = int(
                    (cursor.fetchone() or {}).get("max_order") or 0
                )
                for item in items:
                    cursor.execute(
                        f"""
                        SELECT item_id
                        FROM {suite_items_table}
                        WHERE suite_id = %s AND module_name = %s
                          AND filename = %s
                        LIMIT 1
                        """,
                        (
                            suite["suite_id"],
                            item["module_name"],
                            item["filename"],
                        ),
                    )
                    if cursor.fetchone():
                        raise ValueError(
                            "测试集已包含脚本："
                            f"{item['module_name']}/{item['filename']}"
                        )

                    script_asset = self.dependencies.sync_script_asset(
                        item["module_name"],
                        item["script_file"],
                        change_source="manual",
                        message=(
                            f"sync script: {item['module_name']}/"
                            f"{item['filename']}"
                        ),
                    )
                    sort_order += 1
                    cursor.execute(
                        f"""
                        INSERT INTO {suite_items_table}
                          (project_id, suite_id, script_asset_id,
                           module_name, filename, display_name, script_path,
                           sort_order, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            project_id,
                            suite["suite_id"],
                            (
                                script_asset.get("asset_id")
                                if script_asset
                                else None
                            ),
                            item["module_name"],
                            item["filename"],
                            item["display_name"],
                            str(item["script_file"]),
                            sort_order,
                            now_ms,
                            now_ms,
                        ),
                    )
                cursor.execute(
                    f"UPDATE {suites_table} "
                    "SET updated_by = %s, updated_at = %s "
                    "WHERE project_id = %s AND suite_id = %s",
                    (
                        self.dependencies.current_platform_author(),
                        now_ms,
                        project_id,
                        suite["suite_id"],
                    ),
                )
            connection.commit()
        return self.dependencies.get_test_suite_payload(suite_uid)

    def delete_item(self, suite_uid, item_id):
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return None
                cursor.execute(
                    f"DELETE FROM {suite_items_table} "
                    "WHERE project_id = %s AND suite_id = %s "
                    "AND item_id = %s",
                    (
                        project_id,
                        suite["suite_id"],
                        int(item_id),
                    ),
                )
                cursor.execute(
                    f"UPDATE {suites_table} "
                    "SET updated_by = %s, updated_at = %s "
                    "WHERE project_id = %s AND suite_id = %s",
                    (
                        self.dependencies.current_platform_author(),
                        now_ms,
                        project_id,
                        suite["suite_id"],
                    ),
                )
            connection.commit()
        return self.dependencies.get_test_suite_payload(suite_uid)

    def reorder_items(self, suite_uid, item_ids):
        if not isinstance(item_ids, list) or not item_ids:
            raise ValueError(
                "item_ids must be a non-empty list."
            )
        normalized_ids = []
        seen = set()
        for raw_item_id in item_ids:
            item_id = int(raw_item_id)
            if item_id in seen:
                continue
            seen.add(item_id)
            normalized_ids.append(item_id)
        config, suites_table, suite_items_table = (
            self.dependencies.get_test_suite_tables()
        )
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                suite = self.dependencies.get_test_suite_row_by_uid(
                    cursor,
                    suites_table,
                    project_id,
                    suite_uid,
                )
                if not suite:
                    return None
                placeholders = ",".join(
                    ["%s"] * len(normalized_ids)
                )
                cursor.execute(
                    f"""
                    SELECT item_id
                    FROM {suite_items_table}
                    WHERE project_id = %s AND suite_id = %s
                      AND item_id IN ({placeholders})
                    """,
                    (
                        project_id,
                        suite["suite_id"],
                        *normalized_ids,
                    ),
                )
                existing_ids = {
                    int(row["item_id"])
                    for row in cursor.fetchall()
                }
                if existing_ids != set(normalized_ids):
                    raise ValueError(
                        "item_ids contains items outside the current "
                        "suite."
                    )
                for index, item_id in enumerate(
                    normalized_ids,
                    start=1,
                ):
                    cursor.execute(
                        f"UPDATE {suite_items_table} "
                        "SET sort_order = %s, updated_at = %s "
                        "WHERE project_id = %s AND suite_id = %s "
                        "AND item_id = %s",
                        (
                            index,
                            now_ms,
                            project_id,
                            suite["suite_id"],
                            item_id,
                        ),
                    )
                cursor.execute(
                    f"UPDATE {suites_table} "
                    "SET updated_by = %s, updated_at = %s "
                    "WHERE project_id = %s AND suite_id = %s",
                    (
                        self.dependencies.current_platform_author(),
                        now_ms,
                        project_id,
                        suite["suite_id"],
                    ),
                )
            connection.commit()
        return self.dependencies.get_test_suite_payload(suite_uid)
