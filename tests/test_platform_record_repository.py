from contextlib import contextmanager
import json
import unittest

from test_plan_viewer.platform_records import (
    PlatformRecordRepository,
    PlatformRecordRepositoryDependencies,
    compact_json_dumps,
    load_json_column,
    record_updated_at_ms,
    validate_platform_record_bucket,
    validate_platform_record_key,
)
from test_plan_viewer.security.runtime import redact_runtime_value


class FakeCursor:
    def __init__(self, rows=None, row=None):
        self.rows = list(rows or [])
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


class PlatformRecordRepositoryTests(unittest.TestCase):
    def make_repository(self, cursor, enabled=True):
        connection = FakeConnection(cursor)
        schema_calls = []

        @contextmanager
        def mysql_connection(_config):
            yield connection

        dependencies = PlatformRecordRepositoryDependencies(
            get_database_config=lambda: {"enabled": enabled},
            ensure_schema=lambda config: schema_calls.append(config),
            table_sql=lambda _config, table: f"`{table}`",
            get_project_id=lambda: 17,
            mysql_connection=mysql_connection,
            get_default_plan_filename=lambda module: f"{module}.md",
            now_ms=lambda: 123456,
            redact_value=redact_runtime_value,
        )
        return PlatformRecordRepository(dependencies, {"view_state", "script_run_records"}), connection, schema_calls

    def test_validation_and_json_helpers(self):
        self.assertEqual(validate_platform_record_bucket("view_state", {"view_state"}), "view_state")
        with self.assertRaises(ValueError):
            validate_platform_record_bucket("unknown", {"view_state"})
        with self.assertRaises(ValueError):
            validate_platform_record_key("../\x00")
        with self.assertRaises(ValueError):
            validate_platform_record_key("x" * 513)
        self.assertEqual(record_updated_at_ms({"updated_at": "42"}, lambda: 9), 42)
        self.assertEqual(record_updated_at_ms({"updated_at": "invalid"}, lambda: 9), 9)
        self.assertEqual(compact_json_dumps({"中文": True}), '{"中文":true}')
        self.assertEqual(load_json_column('{"ok":true}', {}), {"ok": True})
        self.assertEqual(load_json_column("{", {"fallback": True}), {"fallback": True})

    def test_load_records_filters_unknown_and_invalid_rows(self):
        cursor = FakeCursor(
            rows=[
                {"bucket": "view_state", "record_key": "default", "record_json": '{"active":"plans"}'},
                {"bucket": "unknown", "record_key": "ignored", "record_json": "{}"},
                {"bucket": "script_run_records", "record_key": "broken", "record_json": "{"},
            ]
        )
        repository, _connection, schema_calls = self.make_repository(cursor)

        records = repository.load_records()

        self.assertEqual(records["view_state"], {"default": {"active": "plans"}})
        self.assertEqual(records["script_run_records"], {})
        self.assertEqual(len(schema_calls), 1)
        self.assertEqual(cursor.calls[0][1], (17,))

    def test_save_record_uses_compact_json_timestamp_and_commit(self):
        cursor = FakeCursor()
        repository, connection, _schema_calls = self.make_repository(cursor)

        repository.save_record("view_state", "default", {"中文": True})

        self.assertEqual(connection.commits, 1)
        params = cursor.calls[0][1]
        self.assertEqual(params, (17, "view_state", "default", '{"中文":true}', 123456))

    def test_records_are_redacted_when_saved_and_loaded(self):
        cursor = FakeCursor(
            rows=[
                {
                    "bucket": "view_state",
                    "record_key": "default",
                    "record_json": (
                        '{"password":"loaded-secret",'
                        '"nested":{"authorization":"Bearer token"}}'
                    ),
                }
            ]
        )
        repository, _connection, _schema_calls = self.make_repository(
            cursor
        )

        records = repository.load_records()

        self.assertEqual(
            records["view_state"]["default"],
            {
                "password": "******",
                "nested": {"authorization": "******"},
            },
        )

        save_cursor = FakeCursor()
        repository, _connection, _schema_calls = self.make_repository(
            save_cursor
        )
        repository.save_record(
            "view_state",
            "default",
            {
                "password": "saved-secret",
                "nested": {"authorization": "Bearer token"},
                "prompt": "access_key=saved-prompt-access-key",
            },
        )
        saved_json = save_cursor.calls[0][1][3]
        self.assertNotIn("saved-secret", saved_json)
        self.assertNotIn("Bearer token", saved_json)
        self.assertNotIn("saved-prompt-access-key", saved_json)
        self.assertEqual(
            json.loads(saved_json),
            {
                "password": "******",
                "nested": {"authorization": "******"},
                "prompt": "access_key=******",
            },
        )

    def test_jobs_are_redacted_when_saved_and_loaded(self):
        save_cursor = FakeCursor()
        repository, _connection, _schema_calls = self.make_repository(
            save_cursor
        )
        repository.save_job(
            {
                "id": "job-1",
                "status": "failed",
                "module_name": "checkout",
                "target_path": "tests/checkout.spec.ts",
                "logs": ["authorization: Bearer saved-token"],
                "error": "password=saved-password",
                "prompt_context": {
                    "access_token": "saved-access-token"
                },
                "created_at": 1,
                "updated_at": 2,
            }
        )
        params = save_cursor.calls[0][1]
        serialized_params = json.dumps(
            params,
            ensure_ascii=False,
            default=str,
        )
        self.assertNotIn("saved-token", serialized_params)
        self.assertNotIn("saved-password", serialized_params)
        self.assertNotIn("saved-access-token", serialized_params)

        load_cursor = FakeCursor(
            row={
                "job_id": "job-2",
                "status": "failed",
                "module_name": "checkout",
                "plan_filename": "checkout.md",
                "target_path": "tests/checkout.spec.ts",
                "logs": '["token=loaded-token"]',
                "error": "password=loaded-password",
                "payload_json": (
                    '{"prompt_context":'
                    '{"client_secret":"loaded-client-secret"}}'
                ),
                "created_at": 1,
                "updated_at": 2,
            }
        )
        repository, _connection, _schema_calls = self.make_repository(
            load_cursor
        )
        loaded_job = repository.load_job("job-2")
        serialized_job = json.dumps(
            loaded_job,
            ensure_ascii=False,
        )
        self.assertNotIn("loaded-token", serialized_job)
        self.assertNotIn("loaded-password", serialized_job)
        self.assertNotIn("loaded-client-secret", serialized_job)

    def test_jobs_short_circuit_when_database_is_disabled(self):
        repository, connection, schema_calls = self.make_repository(FakeCursor(), enabled=False)

        self.assertIsNone(repository.save_job({"id": "unused"}))
        self.assertIsNone(repository.load_job("unused"))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(schema_calls, [])
        with self.assertRaisesRegex(RuntimeError, "需求管理"):
            repository.require_database()


if __name__ == "__main__":
    unittest.main()
