"""Validation and parsing for portable project archive manifests."""

from dataclasses import dataclass
import io
import json
from pathlib import PurePosixPath
from typing import Any, Callable
import zipfile

from test_plan_viewer.configuration import (
    DEFAULT_PROJECT_LANGUAGE,
    PROJECT_KEY_PATTERN,
    normalize_project_language,
)


PROJECT_EXPORT_FORMAT_VERSION = 1
PROJECT_IMPORT_MAX_BYTES = 200 * 1024 * 1024
PROJECT_IMPORT_MAX_FILES = 5000
PROJECT_IMPORT_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
PROJECT_IMPORT_MANIFEST_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveValidationDependencies:
    """Naming and project-config rules used by archive validation."""

    validate_module_name: Callable[[str], str]
    validate_plan_filename: Callable[[str], str]
    validate_script_filename: Callable[[str], str]
    parse_project_key: Callable[[Any, str], str]
    parse_project_path_segment: Callable[[Any, str, str], str]
    validate_suite_name: Callable[[Any], str]
    validate_suite_description: Callable[[Any], str]
    strip_spec_suffix: Callable[[str], str]


def normalize_project_import_member_name(raw_name, is_dir=False):
    raw_name = str(raw_name or "")
    if not raw_name:
        raise ValueError("导入包包含空路径。")
    if "\\" in raw_name or "\x00" in raw_name:
        raise ValueError(f"导入包路径非法：{raw_name}")

    candidate = (
        raw_name[:-1]
        if is_dir and raw_name.endswith("/")
        else raw_name
    )
    if (
        not candidate
        or candidate.startswith("/")
        or candidate.startswith("./")
        or "//" in candidate
    ):
        raise ValueError(f"导入包路径非法：{raw_name}")

    path = PurePosixPath(candidate)
    if path.is_absolute():
        raise ValueError(f"导入包路径不能是绝对路径：{raw_name}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"导入包路径不能包含路径穿越：{raw_name}")

    normalized = path.as_posix()
    if normalized != candidate:
        raise ValueError(f"导入包路径非法：{raw_name}")
    return normalized


def validate_project_import_member_name(
    raw_name,
    dependencies,
    is_dir=False,
):
    normalized = normalize_project_import_member_name(
        raw_name,
        is_dir=is_dir,
    )
    parts = normalized.split("/")
    if normalized == "manifest.json":
        if is_dir:
            raise ValueError("manifest.json 不能是目录。")
        return normalized

    if parts[0] not in {"specs", "tests"}:
        raise ValueError(
            "导入包只能包含 manifest.json、specs 或 tests："
            f"{raw_name}"
        )
    if is_dir:
        if len(parts) > 2:
            raise ValueError(
                f"导入包只支持模块一级目录：{raw_name}"
            )
        if len(parts) == 2:
            dependencies.validate_module_name(parts[1])
        return normalized

    if len(parts) != 3:
        raise ValueError(
            f"导入包资产必须位于模块目录下：{raw_name}"
        )
    dependencies.validate_module_name(parts[1])
    if parts[0] == "specs":
        dependencies.validate_plan_filename(parts[2])
    else:
        dependencies.validate_script_filename(parts[2])
    return normalized


def parse_project_import_asset_path(
    path,
    asset_type,
    dependencies,
):
    normalized = validate_project_import_member_name(
        path,
        dependencies,
        is_dir=False,
    )
    parts = normalized.split("/")
    expected_root = "specs" if asset_type == "plan" else "tests"
    if parts[0] != expected_root:
        raise ValueError(
            f"manifest 中的 {asset_type} 路径根目录应为 "
            f"{expected_root}：{path}"
        )
    module_name = dependencies.validate_module_name(parts[1])
    filename = (
        dependencies.validate_plan_filename(parts[2])
        if asset_type == "plan"
        else dependencies.validate_script_filename(parts[2])
    )
    return normalized, module_name, filename


def validate_project_import_project(raw_project, dependencies):
    if not isinstance(raw_project, dict):
        raise ValueError("manifest.project 必须是对象。")

    project_key = dependencies.parse_project_key(
        raw_project.get("project_key"),
        "manifest.project.project_key",
    )
    name = str(raw_project.get("name") or project_key).strip()
    if not name:
        raise ValueError("manifest.project.name 不能为空。")
    if len(name) > 128:
        raise ValueError(
            "manifest.project.name 不能超过 128 个字符。"
        )

    return {
        "project_key": project_key,
        "name": name,
        "description": str(
            raw_project.get("description") or ""
        ).strip()[:512],
        "specs_dir": dependencies.parse_project_path_segment(
            raw_project.get("specs_dir"),
            "specs",
            "manifest.project.specs_dir",
        ),
        "tests_dir": dependencies.parse_project_path_segment(
            raw_project.get("tests_dir"),
            "tests",
            "manifest.project.tests_dir",
        ),
        "language": normalize_project_language(
            raw_project.get("language"),
            default=DEFAULT_PROJECT_LANGUAGE,
        ),
    }


def _manifest_asset(
    raw_asset,
    asset_type,
    file_names,
    dependencies,
):
    collection_name = (
        "manifest.plans"
        if asset_type == "plan"
        else "manifest.scripts"
    )
    if not isinstance(raw_asset, dict):
        raise ValueError(f"{collection_name} 必须包含对象。")

    path = str(raw_asset.get("path") or "").strip()
    root = "specs" if asset_type == "plan" else "tests"
    if path:
        normalized, module_name, filename = (
            parse_project_import_asset_path(
                path,
                asset_type,
                dependencies,
            )
        )
    else:
        module_name = dependencies.validate_module_name(
            str(raw_asset.get("module_name") or "").strip()
        )
        filename = (
            dependencies.validate_plan_filename(
                str(raw_asset.get("filename") or "").strip()
            )
            if asset_type == "plan"
            else dependencies.validate_script_filename(
                str(raw_asset.get("filename") or "").strip()
            )
        )
        normalized = f"{root}/{module_name}/{filename}"

    if normalized not in file_names:
        label = "测试计划" if asset_type == "plan" else "测试脚本"
        raise ValueError(
            f"manifest 声明的{label}不存在于导入包：{normalized}"
        )
    return normalized, module_name, filename


def validate_project_import_manifest(
    raw_manifest,
    file_names,
    dependencies,
):
    if not isinstance(raw_manifest, dict):
        raise ValueError("manifest.json 必须是 JSON 对象。")
    if (
        int(raw_manifest.get("format_version") or 0)
        != PROJECT_EXPORT_FORMAT_VERSION
    ):
        raise ValueError("不支持的项目导入包格式版本。")

    project = validate_project_import_project(
        raw_manifest.get("project"),
        dependencies,
    )

    plans = []
    plan_keys = set()
    for raw_plan in raw_manifest.get("plans") or []:
        normalized, module_name, filename = _manifest_asset(
            raw_plan,
            "plan",
            file_names,
            dependencies,
        )
        key = (module_name, filename)
        if key in plan_keys:
            raise ValueError(
                f"manifest 包含重复测试计划："
                f"{module_name}/{filename}"
            )
        plan_keys.add(key)
        plans.append(
            {
                "module_name": module_name,
                "filename": filename,
                "path": normalized,
            }
        )

    scripts = []
    script_keys = set()
    for raw_script in raw_manifest.get("scripts") or []:
        normalized, module_name, filename = _manifest_asset(
            raw_script,
            "script",
            file_names,
            dependencies,
        )
        key = (module_name, filename)
        if key in script_keys:
            raise ValueError(
                f"manifest 包含重复测试脚本："
                f"{module_name}/{filename}"
            )
        script_keys.add(key)

        display_name = (
            str(raw_script.get("display_name") or "").strip()
            or dependencies.strip_spec_suffix(filename)
        )
        script = {
            "module_name": module_name,
            "filename": filename,
            "display_name": display_name[:255],
            "path": normalized,
        }
        raw_from_plan = raw_script.get("from_plan")
        if isinstance(raw_from_plan, dict):
            if raw_from_plan.get("path"):
                (
                    _path,
                    plan_module,
                    plan_filename,
                ) = parse_project_import_asset_path(
                    raw_from_plan.get("path"),
                    "plan",
                    dependencies,
                )
            else:
                plan_module = dependencies.validate_module_name(
                    str(
                        raw_from_plan.get("module_name") or ""
                    ).strip()
                )
                plan_filename = dependencies.validate_plan_filename(
                    str(
                        raw_from_plan.get("filename") or ""
                    ).strip()
                )
            if (plan_module, plan_filename) not in plan_keys:
                raise ValueError(
                    "测试脚本来源计划不存在："
                    f"{plan_module}/{plan_filename}"
                )
            script["from_plan"] = {
                "module_name": plan_module,
                "filename": plan_filename,
            }
        scripts.append(script)

    suites = []
    suite_uids = set()
    suite_names = set()
    for raw_suite in raw_manifest.get("test_suites") or []:
        if not isinstance(raw_suite, dict):
            raise ValueError(
                "manifest.test_suites 必须包含对象。"
            )
        suite_uid = str(
            raw_suite.get("suite_uid")
            or raw_suite.get("id")
            or ""
        ).strip()
        if (
            not suite_uid
            or not PROJECT_KEY_PATTERN.match(suite_uid)
        ):
            raise ValueError(
                "manifest.test_suites[].suite_uid 非法。"
            )
        if suite_uid in suite_uids:
            raise ValueError(
                f"manifest 包含重复测试集 ID：{suite_uid}"
            )
        suite_uids.add(suite_uid)

        name = dependencies.validate_suite_name(
            raw_suite.get("name")
        )
        if name in suite_names:
            raise ValueError(
                f"manifest 包含重复测试集名字：{name}"
            )
        suite_names.add(name)

        items = []
        item_keys = set()
        for index, raw_item in enumerate(
            raw_suite.get("items") or [],
            start=1,
        ):
            if not isinstance(raw_item, dict):
                raise ValueError(
                    "manifest.test_suites[].items 必须包含对象。"
                )
            module_name = dependencies.validate_module_name(
                str(raw_item.get("module_name") or "").strip()
            )
            filename = dependencies.validate_script_filename(
                str(raw_item.get("filename") or "").strip()
            )
            key = (module_name, filename)
            if key not in script_keys:
                raise ValueError(
                    "测试集引用的脚本不存在："
                    f"{module_name}/{filename}"
                )
            if key in item_keys:
                raise ValueError(
                    "测试集包含重复脚本："
                    f"{module_name}/{filename}"
                )
            item_keys.add(key)
            display_name = (
                str(
                    raw_item.get("display_name") or ""
                ).strip()[:255]
                or dependencies.strip_spec_suffix(filename)
            )
            items.append(
                {
                    "module_name": module_name,
                    "filename": filename,
                    "display_name": display_name,
                    "sort_order": int(
                        raw_item.get("sort_order") or index
                    ),
                }
            )
        items.sort(
            key=lambda item: (
                item["sort_order"],
                item["module_name"].lower(),
                item["filename"].lower(),
            )
        )
        suites.append(
            {
                "suite_uid": suite_uid,
                "name": name,
                "description": (
                    dependencies.validate_suite_description(
                        raw_suite.get("description")
                    )
                ),
                "items": items,
            }
        )

    declared_files = {"manifest.json"}
    declared_files.update(plan["path"] for plan in plans)
    declared_files.update(script["path"] for script in scripts)
    extra_files = sorted(set(file_names) - declared_files)
    if extra_files:
        raise ValueError(
            "导入包包含未在 manifest 声明的资产文件："
            f"{extra_files[0]}"
        )

    module_names = sorted(
        {
            plan["module_name"]
            for plan in plans
        }.union(
            {
                script["module_name"]
                for script in scripts
            }
        ),
        key=lambda value: value.lower(),
    )
    return {
        "project": project,
        "modules": [
            {"name": name}
            for name in module_names
        ],
        "plans": plans,
        "scripts": scripts,
        "test_suites": suites,
    }


def parse_project_import_archive(
    archive_bytes,
    dependencies,
):
    if not archive_bytes:
        raise ValueError("导入文件不能为空。")
    if len(archive_bytes) > PROJECT_IMPORT_MAX_BYTES:
        raise ValueError(
            "导入包不能超过 "
            f"{PROJECT_IMPORT_MAX_BYTES // 1024 // 1024}MB。"
        )

    try:
        with zipfile.ZipFile(
            io.BytesIO(archive_bytes),
            "r",
        ) as archive:
            file_names = set()
            total_uncompressed = 0
            member_count = 0
            for info in archive.infolist():
                normalized = validate_project_import_member_name(
                    info.filename,
                    dependencies,
                    is_dir=info.is_dir(),
                )
                if info.is_dir():
                    continue
                member_count += 1
                if member_count > PROJECT_IMPORT_MAX_FILES:
                    raise ValueError(
                        "导入包文件数不能超过 "
                        f"{PROJECT_IMPORT_MAX_FILES}。"
                    )
                if normalized in file_names:
                    raise ValueError(
                        f"导入包包含重复文件：{normalized}"
                    )
                if info.flag_bits & 0x1:
                    raise ValueError(
                        f"导入包不能包含加密文件：{normalized}"
                    )
                total_uncompressed += int(info.file_size or 0)
                if (
                    total_uncompressed
                    > PROJECT_IMPORT_MAX_UNCOMPRESSED_BYTES
                ):
                    limit_mb = (
                        PROJECT_IMPORT_MAX_UNCOMPRESSED_BYTES
                        // 1024
                        // 1024
                    )
                    raise ValueError(
                        f"导入包解压后不能超过 {limit_mb}MB。"
                    )
                file_names.add(normalized)

            if "manifest.json" not in file_names:
                raise ValueError("导入包缺少 manifest.json。")
            manifest_info = archive.getinfo("manifest.json")
            if (
                int(manifest_info.file_size or 0)
                > PROJECT_IMPORT_MANIFEST_MAX_BYTES
            ):
                raise ValueError("manifest.json 过大。")
            try:
                raw_manifest = archive.read(
                    "manifest.json"
                ).decode("utf-8")
                manifest = json.loads(raw_manifest)
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "manifest.json 必须是 UTF-8。"
                ) from exc
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"manifest.json 不是合法 JSON：{exc}"
                ) from exc

            return validate_project_import_manifest(
                manifest,
                file_names,
                dependencies,
            )
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "导入文件不是合法 zip。"
        ) from exc
