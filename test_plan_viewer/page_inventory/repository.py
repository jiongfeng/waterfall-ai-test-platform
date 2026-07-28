"""Project-scoped MySQL persistence for page inventory."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PageInventoryRepositoryDependencies:
    """Application-owned database capabilities."""

    require_platform_database: Callable[[], dict]
    get_page_inventory_table: Callable[[dict], str]
    get_current_project_id: Callable[[], int]
    platform_mysql_connection: Callable[[dict], object]
    validate_uid: Callable[[object, str], str]
    compact_json_dumps: Callable[[object], str]
    current_time_ms: Callable[[], int]
    new_inventory_uid: Callable[[], str]
    get_page_inventory_by_uid: Callable[[str], dict]


class PageInventoryRepository:
    """Read and write page inventory within the active project."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            PageInventoryRepositoryDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "PageInventoryRepositoryDependencies instance"
            )
        self.dependencies = dependencies

    def list_rows(self, limit=None):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_page_inventory_table(
            config
        )
        project_id = (
            self.dependencies.get_current_project_id()
        )
        limit_sql = "LIMIT %s" if limit else ""
        params = [project_id]
        if limit:
            params.append(int(limit))
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {table}
                    WHERE project_id = %s
                    ORDER BY page_name ASC, updated_at DESC
                    {limit_sql}
                    """,
                    tuple(params),
                )
                return cursor.fetchall()

    def get_by_uid(self, inventory_uid):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_page_inventory_table(
            config
        )
        project_id = (
            self.dependencies.get_current_project_id()
        )
        inventory_uid = self.dependencies.validate_uid(
            inventory_uid,
            "inventory_uid",
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    (
                        f"SELECT * FROM {table} "
                        "WHERE project_id = %s "
                        "AND inventory_uid = %s LIMIT 1"
                    ),
                    (project_id, inventory_uid),
                )
                return cursor.fetchone()

    def upsert(self, item, inventory_uid=None):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_page_inventory_table(
            config
        )
        project_id = (
            self.dependencies.get_current_project_id()
        )
        inventory_uid = (
            self.dependencies.validate_uid(
                inventory_uid,
                "inventory_uid",
            )
            if inventory_uid
            else self.dependencies.new_inventory_uid()
        )
        now_ms = self.dependencies.current_time_ms()
        dumps = self.dependencies.compact_json_dumps
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {table}
                      (project_id, inventory_uid, page_name, url,
                       menu_path_json, roles_json, accounts_json,
                       stable_selectors_json, actions_json,
                       read_only_actions_json, write_actions_json,
                       sample_data_json, write_risk,
                       baseline_required, notes, source, confidence,
                       snapshot_hash, last_scanned_at, created_at,
                       updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      page_name = VALUES(page_name),
                      url = VALUES(url),
                      menu_path_json = VALUES(menu_path_json),
                      roles_json = VALUES(roles_json),
                      accounts_json = VALUES(accounts_json),
                      stable_selectors_json =
                        VALUES(stable_selectors_json),
                      actions_json = VALUES(actions_json),
                      read_only_actions_json =
                        VALUES(read_only_actions_json),
                      write_actions_json =
                        VALUES(write_actions_json),
                      sample_data_json = VALUES(sample_data_json),
                      write_risk = VALUES(write_risk),
                      baseline_required =
                        VALUES(baseline_required),
                      notes = VALUES(notes),
                      source = VALUES(source),
                      confidence = VALUES(confidence),
                      snapshot_hash = VALUES(snapshot_hash),
                      last_scanned_at = VALUES(last_scanned_at),
                      updated_at = VALUES(updated_at)
                    """,
                    (
                        project_id,
                        inventory_uid,
                        item["page_name"],
                        item["url"],
                        dumps(item["menu_path"]),
                        dumps(item["roles"]),
                        dumps(item["accounts"]),
                        dumps(item["stable_selectors"]),
                        dumps(item["actions"]),
                        dumps(item["read_only_actions"]),
                        dumps(item["write_actions"]),
                        dumps(item["sample_data"]),
                        int(item["write_risk"]),
                        int(item["baseline_required"]),
                        item["notes"],
                        item["source"],
                        item["confidence"],
                        item["snapshot_hash"],
                        item["last_scanned_at"],
                        now_ms,
                        now_ms,
                    ),
                )
                connection.commit()
        return self.dependencies.get_page_inventory_by_uid(
            inventory_uid
        )

    def delete(self, inventory_uid):
        config = self.dependencies.require_platform_database()
        table = self.dependencies.get_page_inventory_table(
            config
        )
        project_id = (
            self.dependencies.get_current_project_id()
        )
        inventory_uid = self.dependencies.validate_uid(
            inventory_uid,
            "inventory_uid",
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    (
                        f"DELETE FROM {table} "
                        "WHERE project_id = %s "
                        "AND inventory_uid = %s"
                    ),
                    (project_id, inventory_uid),
                )
                affected = cursor.rowcount
                connection.commit()
        return affected > 0


__all__ = [
    "PageInventoryRepository",
    "PageInventoryRepositoryDependencies",
]
