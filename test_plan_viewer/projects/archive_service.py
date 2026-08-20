"""Project archive import/export use cases.

The service owns archive orchestration while the application composition root
supplies project context, persistence, asset syncing, and workspace paths.
ZIP validation remains in :mod:`test_plan_viewer.projects.archive`.
"""

from dataclasses import dataclass
import io
import json
from pathlib import Path
from typing import Callable
import zipfile

from test_plan_viewer.projects import archive as project_archive


@dataclass(frozen=True)
class ProjectArchiveServiceDependencies:
    """Application capabilities required by project archive use cases."""

    validation_dependencies: (
        project_archive.ArchiveValidationDependencies
    )
    get_current_project: Callable
    get_current_project_id: Callable
    get_project_root: Callable
    get_specs_dir: Callable
    get_tests_dir: Callable
    get_plan_file: Callable
    get_script_file: Callable
    get_project_relative_path: Callable
    project_relative_path: Callable
    get_platform_database_config: Callable
    ensure_platform_database_schema: Callable
    get_test_assets_table: Callable
    get_test_asset_revisions_table: Callable
    get_platform_projects_table: Callable
    platform_table_sql: Callable
    platform_mysql_connection: Callable
    list_test_suites: Callable
    strip_spec_suffix: Callable
    current_time_ms: Callable
    current_platform_author: Callable
    get_test_suite_tables: Callable
    ensure_playwright_asset_git_repo: Callable
    run_git_command: Callable
    sync_plan_asset: Callable
    sync_script_asset: Callable
    create_project: Callable
    use_project_context: Callable
    remove_tree: Callable


class ProjectArchiveService:
    """Build and restore portable project ZIP archives."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            ProjectArchiveServiceDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "ProjectArchiveServiceDependencies instance"
            )
        self.dependencies = dependencies

    def collect_project_export_files(
        self,
        base_dir,
        suffix,
        zip_root,
    ):
        """Collect validated module assets in deterministic order."""

        base_dir = Path(base_dir)
        files = []
        if not base_dir.exists():
            return files
        if not base_dir.is_dir():
            raise ValueError(
                f"项目资产目录不是目录：{base_dir}"
            )

        validation = self.dependencies.validation_dependencies
        for module_dir in sorted(
            base_dir.iterdir(),
            key=lambda item: item.name.lower(),
        ):
            if not module_dir.is_dir():
                continue
            module_name = validation.validate_module_name(
                module_dir.name
            )
            for asset_file in sorted(
                module_dir.glob(f"*{suffix}"),
                key=lambda item: item.name.lower(),
            ):
                if not asset_file.is_file():
                    continue
                filename = asset_file.name
                if suffix == ".md":
                    validation.validate_plan_filename(filename)
                else:
                    validation.validate_script_filename(filename)
                relative_path = (
                    self.dependencies.get_project_relative_path(
                        asset_file
                    ).as_posix()
                )
                files.append(
                    {
                        "module_name": module_name,
                        "filename": filename,
                        "file": asset_file,
                        "relative_path": relative_path,
                        "zip_path": (
                            f"{zip_root}/{module_name}/{filename}"
                        ),
                    }
                )
        return files

    def list_project_export_asset_rows(self):
        """Index active plan/script rows by project-relative path."""

        config = (
            self.dependencies.get_platform_database_config()
        )
        project_id = self.dependencies.get_current_project_id()
        if not config.get("enabled") or project_id is None:
            return {}

        self.dependencies.ensure_platform_database_schema(config)
        assets_table = self.dependencies.get_test_assets_table(
            config
        )
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {assets_table}
                    WHERE project_id = %s
                      AND asset_type IN ('plan', 'script')
                      AND deleted_at IS NULL
                      AND status = 'active'
                    """,
                    (project_id,),
                )
                rows = cursor.fetchall()

        rows_by_key = {}
        for row in rows:
            try:
                relative_path = (
                    self.dependencies.get_project_relative_path(
                        row.get("current_path") or ""
                    ).as_posix()
                )
            except Exception:
                continue
            rows_by_key[
                (row.get("asset_type"), relative_path)
            ] = row
        return rows_by_key

    def list_project_export_suites(self):
        """Return suites only when project persistence is available."""

        config = (
            self.dependencies.get_platform_database_config()
        )
        if (
            not config.get("enabled")
            or self.dependencies.get_current_project_id() is None
        ):
            return []
        return self.dependencies.list_test_suites()

    def build_project_export_payload(self):
        """Build the manifest and its ordered filesystem members."""

        project = self.dependencies.get_current_project()
        plan_files = self.collect_project_export_files(
            self.dependencies.get_specs_dir(),
            ".md",
            "specs",
        )
        script_files = self.collect_project_export_files(
            self.dependencies.get_tests_dir(),
            ".spec.ts",
            "tests",
        )
        asset_rows = self.list_project_export_asset_rows()

        plan_asset_key_by_id = {}
        for plan in plan_files:
            asset = asset_rows.get(
                ("plan", plan["relative_path"])
            )
            if asset:
                plan_asset_key_by_id[
                    int(asset["asset_id"])
                ] = (
                    plan["module_name"],
                    plan["filename"],
                )

        plans = [
            {
                "module_name": item["module_name"],
                "filename": item["filename"],
                "path": item["zip_path"],
            }
            for item in plan_files
        ]

        scripts = []
        for item in script_files:
            script = {
                "module_name": item["module_name"],
                "filename": item["filename"],
                "display_name": (
                    self.dependencies.strip_spec_suffix(
                        item["filename"]
                    )
                ),
                "path": item["zip_path"],
            }
            asset = asset_rows.get(
                ("script", item["relative_path"])
            )
            from_plan_asset_id = (
                int(asset.get("from_plan_asset_id") or 0)
                if asset
                else 0
            )
            from_plan_key = plan_asset_key_by_id.get(
                from_plan_asset_id
            )
            if from_plan_key:
                script["from_plan"] = {
                    "module_name": from_plan_key[0],
                    "filename": from_plan_key[1],
                }
            scripts.append(script)

        module_names = sorted(
            {
                item["module_name"]
                for item in plan_files
            }.union(
                {
                    item["module_name"]
                    for item in script_files
                }
            ),
            key=lambda value: value.lower(),
        )
        suites = []
        for suite in self.list_project_export_suites():
            suites.append(
                {
                    "suite_uid": (
                        suite.get("suite_uid")
                        or suite.get("id")
                    ),
                    "name": suite.get("name") or "",
                    "description": (
                        suite.get("description") or ""
                    ),
                    "items": [
                        {
                            "module_name": (
                                item.get("module_name") or ""
                            ),
                            "filename": (
                                item.get("filename") or ""
                            ),
                            "display_name": (
                                item.get("display_name")
                                or self.dependencies
                                .strip_spec_suffix(
                                    item.get("filename") or ""
                                )
                            ),
                            "sort_order": int(
                                item.get("sort_order") or index
                            ),
                        }
                        for index, item in enumerate(
                            suite.get("items") or [],
                            start=1,
                        )
                    ],
                }
            )

        manifest = {
            "format_version": (
                project_archive.PROJECT_EXPORT_FORMAT_VERSION
            ),
            "exported_at": self.dependencies.current_time_ms(),
            "project": {
                "project_key": (
                    project.get("project_key") or ""
                ),
                "name": (
                    project.get("name")
                    or project.get("project_key")
                    or ""
                ),
                "description": (
                    project.get("description") or ""
                ),
                "specs_dir": (
                    project.get("specs_dir") or "specs"
                ),
                "tests_dir": (
                    project.get("tests_dir") or "tests"
                ),
                "language": project.get("language") or "en",
            },
            "modules": [
                {
                    "name": name,
                    "has_plans": any(
                        item["module_name"] == name
                        for item in plan_files
                    ),
                    "has_scripts": any(
                        item["module_name"] == name
                        for item in script_files
                    ),
                }
                for name in module_names
            ],
            "plans": plans,
            "scripts": scripts,
            "test_suites": suites,
        }
        return manifest, plan_files, script_files

    def build_project_export_zip(self):
        """Serialize the current project into an in-memory ZIP."""

        (
            manifest,
            plan_files,
            script_files,
        ) = self.build_project_export_payload()
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )
            for item in [*plan_files, *script_files]:
                archive.write(
                    item["file"],
                    item["zip_path"],
                )
        buffer.seek(0)
        return buffer, manifest

    def parse_project_import_archive(self, archive_bytes):
        """Validate and normalize an uploaded project archive."""

        return project_archive.parse_project_import_archive(
            archive_bytes,
            self.dependencies.validation_dependencies,
        )

    def clear_project_import_asset_directory(self, directory):
        """Replace one project-owned asset directory with an empty one."""

        directory = Path(directory)
        project_root = Path(
            self.dependencies.get_project_root()
        ).resolve(strict=False)
        resolved = directory.resolve(strict=False)
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                "导入目标目录必须位于项目目录内。"
            ) from exc
        if resolved == project_root:
            raise ValueError(
                "导入目标目录不能是项目根目录。"
            )
        if directory.exists():
            self.dependencies.remove_tree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    def write_project_import_files(
        self,
        manifest,
        archive_bytes,
    ):
        """Write validated UTF-8 assets into the imported project."""

        self.clear_project_import_asset_directory(
            self.dependencies.get_specs_dir()
        )
        self.clear_project_import_asset_directory(
            self.dependencies.get_tests_dir()
        )

        with zipfile.ZipFile(
            io.BytesIO(archive_bytes),
            "r",
        ) as archive:
            for plan in manifest["plans"]:
                content = archive.read(plan["path"])
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        "测试计划必须是 UTF-8："
                        f"{plan['path']}"
                    ) from exc
                target_file = Path(
                    self.dependencies.get_plan_file(
                        plan["module_name"],
                        plan["filename"],
                    )
                )
                target_file.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                target_file.write_bytes(content)

            for script in manifest["scripts"]:
                content = archive.read(script["path"])
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        "测试脚本必须是 UTF-8："
                        f"{script['path']}"
                    ) from exc
                target_file = Path(
                    self.dependencies.get_script_file(
                        script["module_name"],
                        script["filename"],
                    )
                )
                target_file.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                target_file.write_bytes(content)

    def commit_project_import_file_tree(self):
        """Commit imported asset-tree changes when Git sees a diff."""

        self.dependencies.ensure_playwright_asset_git_repo()
        for directory in (
            self.dependencies.get_specs_dir(),
            self.dependencies.get_tests_dir(),
        ):
            self.dependencies.run_git_command(
                [
                    "add",
                    "-A",
                    "--",
                    self.dependencies.project_relative_path(
                        directory
                    ),
                ]
            )
        staged_diff = self.dependencies.run_git_command(
            ["diff", "--cached", "--quiet"],
            check=False,
        )
        if staged_diff.returncode != 0:
            self.dependencies.run_git_command(
                [
                    "-c",
                    "user.name=Test Plan Viewer",
                    "-c",
                    "user.email=test-plan-viewer@local",
                    "commit",
                    "-m",
                    "import project assets",
                ]
            )

    def sync_project_import_assets(self, manifest):
        """Create plan/script assets and preserve source-plan links."""

        plan_assets = {}
        for plan in manifest["plans"]:
            plan_file = self.dependencies.get_plan_file(
                plan["module_name"],
                plan["filename"],
            )
            asset = self.dependencies.sync_plan_asset(
                plan["module_name"],
                plan_file,
                change_source="import",
                message=(
                    "import plan: "
                    f"{plan['module_name']}/{plan['filename']}"
                ),
            )
            plan_assets[
                (plan["module_name"], plan["filename"])
            ] = asset

        script_assets = {}
        for script in manifest["scripts"]:
            script_file = self.dependencies.get_script_file(
                script["module_name"],
                script["filename"],
            )
            from_plan_asset_id = None
            from_plan = script.get("from_plan")
            if isinstance(from_plan, dict):
                plan_asset = plan_assets.get(
                    (
                        from_plan["module_name"],
                        from_plan["filename"],
                    )
                )
                from_plan_asset_id = (
                    plan_asset.get("asset_id")
                    if plan_asset
                    else None
                )
            asset = self.dependencies.sync_script_asset(
                script["module_name"],
                script_file,
                change_source="import",
                from_plan_asset_id=from_plan_asset_id,
                message=(
                    "import script: "
                    f"{script['module_name']}/"
                    f"{script['filename']}"
                ),
            )
            script_assets[
                (script["module_name"], script["filename"])
            ] = asset
        return plan_assets, script_assets

    def import_project_test_suites(
        self,
        manifest,
        script_assets,
    ):
        """Insert imported suites and items in one database transaction."""

        suites = manifest.get("test_suites") or []
        if not suites:
            return 0

        (
            config,
            suites_table,
            suite_items_table,
        ) = self.dependencies.get_test_suite_tables()
        project_id = self.dependencies.get_current_project_id()
        now_ms = self.dependencies.current_time_ms()
        author = self.dependencies.current_platform_author()
        item_count = 0
        with self.dependencies.platform_mysql_connection(
            config
        ) as connection:
            with connection.cursor() as cursor:
                for suite in suites:
                    cursor.execute(
                        f"""
                        INSERT INTO {suites_table}
                          (project_id, suite_uid, name, description,
                           status, created_by, updated_by, created_at,
                           updated_at, deleted_at)
                        VALUES (%s, %s, %s, %s, 'active', %s, %s,
                                %s, %s, NULL)
                        """,
                        (
                            project_id,
                            suite["suite_uid"],
                            suite["name"],
                            suite["description"],
                            author,
                            author,
                            now_ms,
                            now_ms,
                        ),
                    )
                    suite_id = cursor.lastrowid
                    for sort_order, item in enumerate(
                        suite["items"],
                        start=1,
                    ):
                        script_asset = script_assets.get(
                            (
                                item["module_name"],
                                item["filename"],
                            )
                        )
                        script_file = (
                            self.dependencies.get_script_file(
                                item["module_name"],
                                item["filename"],
                            )
                        )
                        cursor.execute(
                            f"""
                            INSERT INTO {suite_items_table}
                              (project_id, suite_id, script_asset_id,
                               module_name, filename, display_name,
                               script_path, sort_order, created_at,
                               updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s)
                            """,
                            (
                                project_id,
                                suite_id,
                                (
                                    script_asset.get("asset_id")
                                    if script_asset
                                    else None
                                ),
                                item["module_name"],
                                item["filename"],
                                item["display_name"],
                                str(script_file),
                                sort_order,
                                now_ms,
                                now_ms,
                            ),
                        )
                        item_count += 1
            connection.commit()
        return item_count

    def cleanup_imported_project(self, project):
        """Remove a partially imported project and its persisted records."""

        if not project:
            return

        project_id = project.get("project_id")
        config = (
            self.dependencies.get_platform_database_config()
        )
        if config.get("enabled") and project_id is not None:
            self.dependencies.ensure_platform_database_schema(
                config
            )
            tables_by_project = [
                "script_preparation_runs",
                "setup_runs",
                "setup_bindings",
                "setup_scripts",
                "agent_run_attempts",
                "agent_run_events",
                "agent_run_steps",
                "agent_runs",
                "test_suite_items",
                "test_suites",
                "test_run_artifacts",
                "test_run_results",
                "test_runs",
                "job_artifacts",
                "test_jobs",
                "platform_jobs",
                "platform_records",
                "requirement_modules",
                "requirements",
                "page_inventory",
            ]
            projects_table = (
                self.dependencies.get_platform_projects_table(
                    config
                )
            )
            assets_table = (
                self.dependencies.get_test_assets_table(config)
            )
            revisions_table = (
                self.dependencies.get_test_asset_revisions_table(
                    config
                )
            )
            with self.dependencies.platform_mysql_connection(
                config
            ) as connection:
                with connection.cursor() as cursor:
                    for table_name in tables_by_project:
                        cursor.execute(
                            "DELETE FROM "
                            f"{self.dependencies.platform_table_sql(config, table_name)} "
                            "WHERE project_id = %s",
                            (project_id,),
                        )
                    cursor.execute(
                        f"""
                        DELETE FROM {revisions_table}
                        WHERE asset_id IN (
                          SELECT asset_id FROM {assets_table}
                          WHERE project_id = %s
                        )
                        """,
                        (project_id,),
                    )
                    cursor.execute(
                        f"DELETE FROM {assets_table} "
                        "WHERE project_id = %s",
                        (project_id,),
                    )
                    cursor.execute(
                        f"DELETE FROM {projects_table} "
                        "WHERE project_id = %s AND is_default = 0",
                        (project_id,),
                    )
                connection.commit()

        project_root_text = str(
            project.get("playwright_project_root") or ""
        ).strip()
        if project_root_text:
            self.dependencies.remove_tree(
                Path(project_root_text).expanduser(),
                ignore_errors=True,
            )

    def import_project_archive(
        self,
        archive_bytes,
        overrides=None,
    ):
        """Create a project from an archive, cleaning all partial state."""

        overrides = overrides or {}
        manifest = self.parse_project_import_archive(
            archive_bytes
        )
        source_project = manifest["project"]
        project_payload = {
            "project_key": (
                overrides.get("project_key")
                or source_project["project_key"]
            ),
            "name": (
                overrides.get("name")
                or source_project["name"]
            ),
            "description": (
                overrides.get("description")
                or source_project.get("description", "")
            ),
            "specs_dir": (
                overrides.get("specs_dir")
                or source_project.get("specs_dir")
                or "specs"
            ),
            "tests_dir": (
                overrides.get("tests_dir")
                or source_project.get("tests_dir")
                or "tests"
            ),
            "language": (
                overrides.get("language")
                or source_project.get("language")
                or "en"
            ),
        }

        project = None
        try:
            project = self.dependencies.create_project(
                project_payload
            )
            with self.dependencies.use_project_context(project):
                self.write_project_import_files(
                    manifest,
                    archive_bytes,
                )
                self.commit_project_import_file_tree()
                (
                    _plan_assets,
                    script_assets,
                ) = self.sync_project_import_assets(manifest)
                suite_item_count = (
                    self.import_project_test_suites(
                        manifest,
                        script_assets,
                    )
                )
            counts = {
                "modules": len(manifest["modules"]),
                "plans": len(manifest["plans"]),
                "scripts": len(manifest["scripts"]),
                "test_suites": len(
                    manifest["test_suites"]
                ),
                "suite_items": suite_item_count,
            }
            return {
                "project": project,
                "counts": counts,
                "warnings": [],
                "error": None,
            }
        except Exception:
            if project:
                self.cleanup_imported_project(project)
            raise
