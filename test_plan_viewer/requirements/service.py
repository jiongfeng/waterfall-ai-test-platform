"""Application services for non-streaming requirement operations."""

from dataclasses import dataclass
from typing import Callable, FrozenSet


@dataclass(frozen=True)
class RequirementServiceDependencies:
    """Capabilities used by requirement use cases."""

    validate_requirement_filename: Callable[[object], str]
    get_requirement_storage_file: Callable[[str, str], object]
    write_file_atomically: Callable[[object, bytes], None]
    extract_requirement_title: Callable[[str, str], str]
    sha256_bytes: Callable[[bytes], str]
    current_time_ms: Callable[[], int]
    current_platform_author: Callable[[], str]
    uuid_hex: Callable[[], str]
    create_uploaded_requirement: Callable[[dict], dict]
    get_requirement_module: Callable[[int, str], dict]
    serialize_requirement_module: Callable[[dict], dict]
    normalize_requirement_module_candidate: Callable[..., dict]
    update_requirement_module: Callable[..., dict]
    requirement_module_statuses: FrozenSet[str]
    upload_max_bytes: int


class RequirementService:
    """Non-streaming requirement use cases."""

    def __init__(self, dependencies):
        if not isinstance(
            dependencies,
            RequirementServiceDependencies,
        ):
            raise TypeError(
                "dependencies must be a "
                "RequirementServiceDependencies instance"
            )
        self.dependencies = dependencies

    def create_from_upload(self, file_storage, title=None):
        if not file_storage:
            raise ValueError("请上传 Markdown 需求文件。")

        filename = (
            self.dependencies.validate_requirement_filename(
                file_storage.filename
            )
        )
        raw = file_storage.read(
            self.dependencies.upload_max_bytes + 1
        )
        if len(raw) > self.dependencies.upload_max_bytes:
            raise ValueError("需求文件不能超过 2MB。")
        try:
            markdown_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "需求文件必须是 UTF-8 编码。"
            ) from exc
        if not markdown_text.strip():
            raise ValueError("需求文件内容不能为空。")

        requirement_uid = self.dependencies.uuid_hex()
        target_file = (
            self.dependencies.get_requirement_storage_file(
                requirement_uid,
                filename,
            )
        )
        self.dependencies.write_file_atomically(target_file, raw)
        now_ms = self.dependencies.current_time_ms()
        requirement_title = (
            str(title or "").strip()[:255]
            or self.dependencies.extract_requirement_title(
                markdown_text,
                filename,
            )
        )
        return self.dependencies.create_uploaded_requirement(
            {
                "requirement_uid": requirement_uid,
                "title": requirement_title,
                "filename": filename,
                "file_path": str(target_file),
                "content_sha256": (
                    self.dependencies.sha256_bytes(raw)
                ),
                "created_by": (
                    self.dependencies.current_platform_author()
                ),
                "created_at": now_ms,
                "updated_at": now_ms,
            }
        )

    def update_module(
        self,
        requirement_id,
        module_uid,
        payload,
    ):
        existing = self.dependencies.get_requirement_module(
            requirement_id,
            module_uid,
        )
        if not existing:
            return None
        normalized = (
            self.dependencies
            .normalize_requirement_module_candidate(
                {
                    **self.dependencies
                    .serialize_requirement_module(existing),
                    **(payload or {}),
                }
            )
        )
        status = str(
            (payload or {}).get("status")
            or existing.get("status")
            or "candidate"
        ).strip()
        if (
            status
            not in self.dependencies.requirement_module_statuses
        ):
            raise ValueError("不支持的候选模块状态。")
        return self.dependencies.update_requirement_module(
            requirement_id,
            module_uid,
            normalized,
            status,
        )
