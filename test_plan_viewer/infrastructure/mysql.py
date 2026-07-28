"""Low-level MySQL connection, naming, and schema helpers."""

from contextlib import contextmanager

from test_plan_viewer.configuration import MYSQL_IDENTIFIER_PATTERN

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    DictCursor = None


def quote_mysql_identifier(identifier):
    if not identifier or not MYSQL_IDENTIFIER_PATTERN.match(identifier):
        raise RuntimeError(f"Invalid MySQL identifier: {identifier}")
    return f"`{identifier}`"


def platform_table_name(config, name):
    return f"{config.get('table_prefix', '')}{name}"


def platform_table_sql(config, name):
    return quote_mysql_identifier(platform_table_name(config, name))


@contextmanager
def platform_mysql_connection(config, use_database=True):
    if not config.get("enabled"):
        raise RuntimeError("未启用平台 MySQL 持久化，请在 config.json 配置 platform_database。")
    if pymysql is None:
        raise RuntimeError("缺少 PyMySQL 依赖，请先执行 python -m pip install -r requirements.txt。")

    kwargs = {
        "host": config["host"],
        "port": config["port"],
        "user": config["user"],
        "password": config["password"],
        "charset": config["charset"],
        "autocommit": False,
        "connect_timeout": config["connect_timeout"],
        "cursorclass": DictCursor,
    }
    if use_database:
        kwargs["database"] = config["database"]

    connection = pymysql.connect(**kwargs)
    try:
        yield connection
    finally:
        connection.close()


def mysql_column_exists(cursor, config, table_name, column_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (config["database"], platform_table_name(config, table_name), column_name),
    )
    return int((cursor.fetchone() or {}).get("total") or 0) > 0


def mysql_table_exists(cursor, config, table_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (config["database"], platform_table_name(config, table_name)),
    )
    return int((cursor.fetchone() or {}).get("total") or 0) > 0


def mysql_table_has_columns(cursor, config, table_name, required_columns):
    if not mysql_table_exists(cursor, config, table_name):
        return False
    return all(mysql_column_exists(cursor, config, table_name, column_name) for column_name in required_columns)


def mysql_index_exists(cursor, config, table_name, index_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s
        """,
        (config["database"], platform_table_name(config, table_name), index_name),
    )
    return int((cursor.fetchone() or {}).get("total") or 0) > 0


def mysql_primary_key_columns(cursor, config, table_name):
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = 'PRIMARY'
        ORDER BY SEQ_IN_INDEX ASC
        """,
        (config["database"], platform_table_name(config, table_name)),
    )
    return [row["COLUMN_NAME"] for row in cursor.fetchall()]


def mysql_column_type(cursor, config, table_name, column_name):
    cursor.execute(
        """
        SELECT COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (config["database"], platform_table_name(config, table_name), column_name),
    )
    row = cursor.fetchone() or {}
    return str(row.get("COLUMN_TYPE") or "").lower()


def ensure_mysql_column(cursor, config, table_name, column_name, column_sql):
    if mysql_column_exists(cursor, config, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE {platform_table_sql(config, table_name)} ADD COLUMN {column_sql}")


def ensure_mysql_column_type(cursor, config, table_name, column_name, expected_type, column_sql):
    current_type = mysql_column_type(cursor, config, table_name, column_name)
    if not current_type:
        cursor.execute(f"ALTER TABLE {platform_table_sql(config, table_name)} ADD COLUMN {column_sql}")
        return
    if current_type == expected_type.lower():
        return
    cursor.execute(f"ALTER TABLE {platform_table_sql(config, table_name)} MODIFY COLUMN {column_sql}")


def ensure_mysql_index(cursor, config, table_name, index_name, index_sql):
    if mysql_index_exists(cursor, config, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE {platform_table_sql(config, table_name)} ADD {index_sql}")


__all__ = [
    "ensure_mysql_column",
    "ensure_mysql_column_type",
    "ensure_mysql_index",
    "mysql_column_exists",
    "mysql_column_type",
    "mysql_index_exists",
    "mysql_primary_key_columns",
    "mysql_table_exists",
    "mysql_table_has_columns",
    "platform_mysql_connection",
    "platform_table_name",
    "platform_table_sql",
    "quote_mysql_identifier",
]
