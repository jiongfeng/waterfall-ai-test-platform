import hashlib
import io
import json
import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

import app
from test_plan_viewer.requirements import model
from test_plan_viewer.requirements import repository
from test_plan_viewer.requirements import service
from test_plan_viewer.requirements import storage


def make_module_dependencies(**overrides):
    values = {
        "validate_module_name": app.validate_module_name,
        "get_chinese_plan_filename_from_name": (
            app.get_chinese_plan_filename_from_name
        ),
        "normalize_confidence": app.normalize_confidence,
        "normalize_string_list": app.normalize_string_list,
        "normalize_json_object_or_array": (
            app.normalize_json_object_or_array
        ),
        "get_seed_script_relative_path": (
            app.get_seed_script_relative_path
        ),
        "strip_legacy_coverage_notices": (
            app.strip_legacy_coverage_notices
        ),
        "append_database_baseline_write_operation_notice": (
            app.append_database_baseline_write_operation_notice
        ),
        "load_json_column": app.load_json_column,
        "list_requirement_module_plans": lambda _id: [],
        "get_test_asset_by_id": lambda _id: None,
        "serialize_asset": lambda asset: asset,
        "dedupe_chinese_artifact_naming_notice": (
            app.dedupe_chinese_artifact_naming_notice
        ),
    }
    values.update(overrides)
    return model.RequirementModuleModelDependencies(**values)


class FakeCursor:
    def __init__(
        self,
        *,
        fetchones=(),
        fetchalls=(),
        rowcounts=(),
    ):
        self.fetchones = deque(fetchones)
        self.fetchalls = deque(fetchalls)
        self.rowcounts = deque(rowcounts)
        self.executions = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parameters=()):
        self.executions.append((sql, parameters))
        if self.rowcounts:
            self.rowcount = self.rowcounts.popleft()

    def fetchone(self):
        return self.fetchones.popleft() if self.fetchones else None

    def fetchall(self):
        return self.fetchalls.popleft() if self.fetchalls else []


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_count += 1


def make_repository_dependencies(connection=None, **overrides):
    connection = connection or FakeConnection(FakeCursor())
    values = {
        "require_platform_database": lambda: {
            "enabled": True,
        },
        "get_requirements_table": (
            lambda _config: "`requirements`"
        ),
        "get_requirement_modules_table": (
            lambda _config: "`requirement_modules`"
        ),
        "get_current_project_id": lambda: 7,
        "platform_mysql_connection": lambda _config: connection,
        "validate_uid": app.validate_uid,
        "current_time_ms": lambda: 1234,
        "compact_json_dumps": (
            lambda value: json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        "get_requirement_by_uid": Mock(
            return_value={"requirement_uid": "requirement-fixed"}
        ),
        "get_requirement_module": Mock(
            return_value={"module_uid": "module-fixed"}
        ),
    }
    values.update(overrides)
    return repository.RequirementRepositoryDependencies(**values)


class RequirementModelParityTests(unittest.TestCase):
    def test_title_and_requirement_serialization_match_legacy_head(self):
        title_inputs = [
            ("# 账户流水\n正文", "fallback.md"),
            ("\n## 二级标题", "文件名.md"),
            ("# " + "长" * 300, "fallback.md"),
        ]
        for markdown_text, filename in title_inputs:
            with self.subTest(filename=filename):
                self.assertEqual(
                    model.extract_requirement_title(
                        markdown_text,
                        filename,
                    ),
                    app.extract_requirement_title(
                        markdown_text,
                        filename,
                    ),
                )

        row = {
            "id": 3,
            "project_id": 7,
            "requirement_uid": "requirement-3",
            "title": "账户流水",
            "filename": "账户流水.md",
            "file_path": "/requirements/账户流水.md",
            "content_sha256": "a" * 64,
            "status": "active",
            "source_type": "upload",
            "created_by": "tester",
            "created_at": 10,
            "updated_at": 20,
            "module_count": "2",
        }
        serialization_dependencies = (
            model.RequirementSerializationDependencies(
                read_requirement_markdown=lambda _row: "# 标题",
                render_markdown=lambda text: f"<h1>{text[2:]}</h1>",
            )
        )
        with (
            patch.object(
                app,
                "read_requirement_markdown",
                return_value="# 标题",
            ),
            patch.object(
                app,
                "render_markdown",
                side_effect=lambda text: f"<h1>{text[2:]}</h1>",
            ),
        ):
            legacy = app.serialize_requirement(
                row,
                include_content=True,
            )

        extracted = model.serialize_requirement(
            row,
            include_content=True,
            dependencies=serialization_dependencies,
        )
        self.assertEqual(extracted, legacy)

    def test_planner_prompt_and_normalization_match_legacy_head(self):
        dependencies = make_module_dependencies(
            get_seed_script_relative_path=lambda: "tests/seed/seed.spec.ts",
            append_database_baseline_write_operation_notice=lambda prompt: prompt,
        )
        samples = [
            (
                {
                    "module_name": "登录",
                    "test_points": ["成功登录", "错误密码"],
                    "matched_inventory": {
                        "page_name": "登录页",
                        "roles": ["管理员", "访客"],
                    },
                    "baseline_required": True,
                },
                {"title": "账号需求"},
            ),
            (
                {
                    "module_name": "账户流水",
                    "write_risk": True,
                    "planner_prompt": "自定义提示",
                    "confidence": "0.8",
                },
                None,
            ),
        ]

        for raw, requirement in samples:
            with self.subTest(module=raw["module_name"]):
                with patch.object(
                    app,
                    "get_seed_script_relative_path",
                    return_value="tests/seed/seed.spec.ts",
                ), patch.object(
                    app,
                    "get_current_project",
                    return_value={"language": "zh-CN", "tests_dir": "tests"},
                ), patch.object(
                    app,
                    "append_database_baseline_write_operation_notice",
                    side_effect=lambda prompt: prompt,
                ):
                    self.assertEqual(
                        model.build_planner_prompt_from_requirement_module(
                            raw,
                            requirement,
                            dependencies=dependencies,
                        ),
                        app.build_planner_prompt_from_requirement_module(
                            raw,
                            requirement,
                        ),
                    )
                    self.assertEqual(
                        model.normalize_requirement_module_candidate(
                            raw,
                            requirement,
                            dependencies=dependencies,
                        ),
                        app.normalize_requirement_module_candidate(
                            raw,
                            requirement,
                        ),
                    )

    def test_module_serialization_matches_legacy_plan_fallbacks(self):
        row = {
            "id": 5,
            "requirement_id": 3,
            "module_uid": "module-5",
            "module_name": "登录",
            "plan_name": "登录",
            "status": "generated",
            "confidence": "0.7",
            "requirement_refs_json": '["R1"]',
            "test_points_json": '["成功"]',
            "matched_inventory_json": '{"page_name":"登录页"}',
            "open_questions_json": "[]",
            "baseline_required": 1,
            "write_risk": 0,
            "planner_prompt": "提示",
            "generated_plan_asset_id": 8,
        }
        asset = {
            "asset_id": 8,
            "module_name": "登录",
            "current_path": "/specs/登录/登录计划.md",
        }
        dependencies = make_module_dependencies(
            get_test_asset_by_id=lambda _id: asset,
            serialize_asset=lambda value: {
                "asset_id": value["asset_id"]
            },
        )
        with (
            patch.object(
                app,
                "list_requirement_module_plans",
                return_value=[],
            ),
            patch.object(
                app,
                "get_test_asset_by_id",
                return_value=asset,
            ),
            patch.object(
                app,
                "serialize_asset",
                side_effect=lambda value: {
                    "asset_id": value["asset_id"]
                },
            ),
        ):
            legacy = app.serialize_requirement_module(row)

        extracted = model.serialize_requirement_module(
            row,
            dependencies=dependencies,
        )
        self.assertEqual(extracted, legacy)


class RequirementStorageTests(unittest.TestCase):
    @staticmethod
    def make_storage(project_root, app_dir=None):
        def write_atomically(path, raw):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(raw)

        dependencies = storage.RequirementStorageDependencies(
            validate_uid=app.validate_uid,
            get_project_root=lambda: Path(project_root),
            app_dir=Path(app_dir or project_root),
            get_cwd=lambda: Path(project_root),
            walk=os.walk,
            sha256_file=lambda path: hashlib.sha256(
                Path(path).read_bytes()
            ).hexdigest(),
            write_file_atomically=write_atomically,
            recovery_excluded_dirs=frozenset(
                app.REQUIREMENT_RECOVERY_EXCLUDED_DIRS
            ),
            recovery_max_candidates=(
                app.REQUIREMENT_RECOVERY_MAX_CANDIDATES
            ),
        )
        return storage.RequirementStorage(dependencies)

    def test_filename_and_storage_path_match_legacy_behavior(self):
        values = [
            "需求.md",
            " ../嵌套需求.md ",
            "README.MD",
            "",
            "需求.txt",
            "bad\\name.md",
        ]
        for value in values:
            with self.subTest(value=value):
                try:
                    legacy = app.validate_requirement_filename(value)
                except Exception as exc:
                    legacy = (type(exc), str(exc))
                try:
                    extracted = (
                        storage.RequirementStorage.validate_filename(
                            value
                        )
                    )
                except Exception as exc:
                    extracted = (type(exc), str(exc))
                self.assertEqual(extracted, legacy)

        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            requirement_storage = self.make_storage(project_root)
            target = requirement_storage.get_storage_file(
                "requirement-1",
                "需求.md",
            )
            self.assertEqual(
                target,
                project_root
                / "requirements"
                / "requirement-1"
                / "需求.md",
            )

    def test_missing_file_is_recovered_only_by_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            candidate = (
                project_root / "legacy" / "账户流水.md"
            )
            candidate.parent.mkdir(parents=True)
            raw = "# 账户流水\n正文".encode("utf-8")
            candidate.write_bytes(raw)
            target = (
                project_root
                / "requirements"
                / "requirement-1"
                / "账户流水.md"
            )
            requirement_storage = self.make_storage(project_root)
            row = {
                "filename": "账户流水.md",
                "file_path": str(target),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            }

            markdown_text = requirement_storage.read_markdown(row)

            self.assertEqual(markdown_text, raw.decode("utf-8"))
            self.assertEqual(target.read_bytes(), raw)

            wrong_target = (
                project_root
                / "requirements"
                / "requirement-2"
                / "账户流水.md"
            )
            wrong = {
                **row,
                "file_path": str(wrong_target),
                "content_sha256": "0" * 64,
            }
            with self.assertRaises(FileNotFoundError):
                requirement_storage.read_markdown(wrong)


class RequirementRepositoryTests(unittest.TestCase):
    def test_list_and_get_are_project_scoped(self):
        requirement_rows = [
            {
                "id": 2,
                "requirement_uid": "requirement-2",
                "module_count": 1,
            }
        ]
        cursor = FakeCursor(
            fetchones=[requirement_rows[0]],
            fetchalls=[requirement_rows],
        )
        connection = FakeConnection(cursor)
        requirement_repository = repository.RequirementRepository(
            make_repository_dependencies(connection)
        )

        listed = requirement_repository.list_requirements()
        fetched = requirement_repository.get_requirement(
            "requirement-2"
        )

        self.assertEqual(listed, requirement_rows)
        self.assertEqual(fetched, requirement_rows[0])
        list_query, list_parameters = cursor.executions[0]
        self.assertIn("LEFT JOIN `requirement_modules`", list_query)
        self.assertEqual(list_parameters, (7,))
        get_query, get_parameters = cursor.executions[1]
        self.assertIn("status != 'deleted'", get_query)
        self.assertEqual(
            get_parameters,
            (7, "requirement-2"),
        )

    def test_upload_insert_and_delete_cascade_keep_contracts(self):
        cursor = FakeCursor(rowcounts=[1, 1, 1])
        connection = FakeConnection(cursor)
        get_requirement = Mock(
            return_value={"requirement_uid": "requirement-fixed"}
        )
        requirement_repository = repository.RequirementRepository(
            make_repository_dependencies(
                connection,
                get_requirement_by_uid=get_requirement,
            )
        )
        record = {
            "requirement_uid": "requirement-fixed",
            "title": "需求",
            "filename": "需求.md",
            "file_path": "/requirements/需求.md",
            "content_sha256": "a" * 64,
            "created_by": "tester",
            "created_at": 1234,
            "updated_at": 1234,
        }

        created = requirement_repository.create_uploaded_requirement(
            record
        )
        deleted = requirement_repository.delete_requirement(
            "requirement-fixed"
        )

        self.assertEqual(created["requirement_uid"], "requirement-fixed")
        self.assertTrue(deleted)
        self.assertEqual(connection.commit_count, 2)
        get_requirement.assert_called_once_with(
            "requirement-fixed"
        )
        self.assertIn("INSERT INTO", cursor.executions[0][0])
        self.assertIn(
            "JOIN `requirements`",
            cursor.executions[2][0],
        )

    def test_module_update_serializes_json_and_returns_adapter_row(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        get_module = Mock(
            return_value={"module_uid": "module-1"}
        )
        requirement_repository = repository.RequirementRepository(
            make_repository_dependencies(
                connection,
                get_requirement_module=get_module,
            )
        )
        normalized = {
            "module_name": "登录",
            "plan_name": "登录",
            "confidence": 0.8,
            "business_goal": "登录系统",
            "requirement_refs": ["R1"],
            "test_points": ["成功"],
            "matched_inventory": {"page_name": "登录页"},
            "open_questions": [],
            "baseline_required": False,
            "write_risk": False,
            "planner_prompt": "prompt",
        }

        updated = requirement_repository.update_module(
            3,
            "module-1",
            normalized,
            "confirmed",
        )

        self.assertEqual(updated["module_uid"], "module-1")
        parameters = cursor.executions[0][1]
        self.assertEqual(parameters[0:4], (
            "登录",
            "登录",
            "confirmed",
            0.8,
        ))
        self.assertEqual(parameters[5], '["R1"]')
        get_module.assert_called_once_with(3, "module-1")


class RequirementServiceTests(unittest.TestCase):
    def test_upload_normalizes_content_and_persists_record(self):
        created_records = []
        writes = []
        dependencies = service.RequirementServiceDependencies(
            validate_requirement_filename=(
                storage.RequirementStorage.validate_filename
            ),
            get_requirement_storage_file=(
                lambda uid, filename: Path("/requirements")
                / uid
                / filename
            ),
            write_file_atomically=(
                lambda path, raw: writes.append((path, raw))
            ),
            extract_requirement_title=(
                model.extract_requirement_title
            ),
            sha256_bytes=lambda raw: hashlib.sha256(
                raw
            ).hexdigest(),
            current_time_ms=lambda: 1234,
            current_platform_author=lambda: "tester",
            uuid_hex=lambda: "requirement-fixed",
            create_uploaded_requirement=(
                lambda record: (
                    created_records.append(record) or record
                )
            ),
            get_requirement_module=Mock(),
            serialize_requirement_module=Mock(),
            normalize_requirement_module_candidate=Mock(),
            update_requirement_module=Mock(),
            requirement_module_statuses=(
                model.REQUIREMENT_MODULE_STATUSES
            ),
            upload_max_bytes=2 * 1024 * 1024,
        )
        upload = io.BytesIO("# 上传标题\n正文".encode("utf-8"))
        upload.filename = "需求.md"

        created = service.RequirementService(
            dependencies
        ).create_from_upload(upload)

        self.assertEqual(created["title"], "上传标题")
        self.assertEqual(
            created["requirement_uid"],
            "requirement-fixed",
        )
        self.assertEqual(created["created_by"], "tester")
        self.assertEqual(len(writes), 1)
        self.assertEqual(created_records, [created])

    def test_module_update_merges_existing_data_and_validates_status(self):
        existing = {
            "module_uid": "module-1",
            "status": "candidate",
        }
        normalize = Mock(
            return_value={"module_name": "登录"}
        )
        update = Mock(
            return_value={"module_uid": "module-1"}
        )
        dependencies = service.RequirementServiceDependencies(
            validate_requirement_filename=Mock(),
            get_requirement_storage_file=Mock(),
            write_file_atomically=Mock(),
            extract_requirement_title=Mock(),
            sha256_bytes=Mock(),
            current_time_ms=Mock(),
            current_platform_author=Mock(),
            uuid_hex=Mock(),
            create_uploaded_requirement=Mock(),
            get_requirement_module=Mock(
                return_value=existing
            ),
            serialize_requirement_module=Mock(
                return_value={
                    "module_name": "旧名称",
                    "status": "candidate",
                }
            ),
            normalize_requirement_module_candidate=normalize,
            update_requirement_module=update,
            requirement_module_statuses=(
                model.REQUIREMENT_MODULE_STATUSES
            ),
            upload_max_bytes=10,
        )
        requirement_service = service.RequirementService(
            dependencies
        )

        result = requirement_service.update_module(
            3,
            "module-1",
            {
                "module_name": "登录",
                "status": "confirmed",
            },
        )

        self.assertEqual(result["module_uid"], "module-1")
        normalize.assert_called_once_with(
            {
                "module_name": "登录",
                "status": "confirmed",
            }
        )
        update.assert_called_once_with(
            3,
            "module-1",
            {"module_name": "登录"},
            "confirmed",
        )

        with self.assertRaisesRegex(
            ValueError,
            "不支持的候选模块状态",
        ):
            requirement_service.update_module(
                3,
                "module-1",
                {"status": "invalid"},
            )


if __name__ == "__main__":
    unittest.main()
