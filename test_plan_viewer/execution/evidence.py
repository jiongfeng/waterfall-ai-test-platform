"""Execution video and HTML-report evidence helpers.

Project selection and filesystem access are provided explicitly so this module
does not depend on request state or the legacy application entry point.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import parse as urlparse


PLAYWRIGHT_REPORT_DIR_NAME = "playwright-report"
VIDEO_SUFFIXES = {".webm", ".mp4"}


@dataclass(frozen=True)
class EvidenceDependencies:
    """Project and filesystem capabilities used by evidence helpers."""

    get_project_root: Callable[[], Path]
    get_project_relative_path: Callable[[Path], Path]
    resolve_path: Callable[[Path], Path]
    path_exists: Callable[[Path], bool]
    path_is_file: Callable[[Path], bool]
    path_is_dir: Callable[[Path], bool]
    stat_path: Callable[[Path], Any]
    rglob: Callable[[Path, str], Iterable[Path]]


def get_run_video_file(relative_path, dependencies):
    if not relative_path or "\x00" in relative_path:
        raise ValueError("Invalid video path.")

    project_root = dependencies.resolve_path(
        dependencies.get_project_root()
    )
    video_file = dependencies.resolve_path(project_root / relative_path)

    try:
        video_file.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("Video path is outside project root.") from exc

    if video_file.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("Unsupported video file type.")

    return video_file


def serialize_run_video(video_file, dependencies):
    relative_path = dependencies.get_project_relative_path(video_file)
    relative_url_path = relative_path.as_posix()
    stat = dependencies.stat_path(video_file)

    return {
        "path": str(video_file),
        "relative_path": relative_url_path,
        "url": f"/api/run-videos/{urlparse.quote(relative_url_path)}",
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def get_playwright_report_file(relative_path, dependencies):
    if not relative_path or "\x00" in relative_path:
        raise ValueError("Invalid report path.")

    project_root = dependencies.resolve_path(
        dependencies.get_project_root()
    )
    report_root = dependencies.resolve_path(
        project_root / PLAYWRIGHT_REPORT_DIR_NAME
    )
    report_file = dependencies.resolve_path(project_root / relative_path)

    try:
        report_file.relative_to(report_root)
    except ValueError as exc:
        raise ValueError(
            "Report path is outside playwright-report directory."
        ) from exc

    return report_file


def serialize_playwright_report(report_file, dependencies):
    relative_path = dependencies.get_project_relative_path(report_file)
    relative_url_path = relative_path.as_posix()
    stat = dependencies.stat_path(report_file)

    return {
        "path": str(report_file),
        "relative_path": relative_url_path,
        "url": (
            "/api/playwright-reports/"
            f"{urlparse.quote(relative_url_path)}"
        ),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def find_latest_playwright_report(
    started_at,
    dependencies,
    report_dir=None,
):
    project_root = dependencies.get_project_root()
    report_root = (
        Path(report_dir)
        if report_dir
        else project_root / PLAYWRIGHT_REPORT_DIR_NAME
    )
    report_file = report_root / "index.html"

    if (
        not dependencies.path_exists(report_file)
        or not dependencies.path_is_file(report_file)
    ):
        return None

    try:
        stat = dependencies.stat_path(report_file)
    except OSError:
        return None

    if stat.st_mtime < started_at - 1:
        return None

    return report_file


def find_latest_run_video(
    started_at,
    dependencies,
    results_dir=None,
):
    project_root = dependencies.get_project_root()
    test_results_dir = (
        Path(results_dir)
        if results_dir
        else project_root / "test-results"
    )

    if (
        not dependencies.path_exists(test_results_dir)
        or not dependencies.path_is_dir(test_results_dir)
    ):
        return None

    candidates = []
    for suffix in VIDEO_SUFFIXES:
        for video_file in dependencies.rglob(
            test_results_dir,
            f"*{suffix}",
        ):
            try:
                if not dependencies.path_is_file(video_file):
                    continue
                stat = dependencies.stat_path(video_file)
            except OSError:
                continue

            if stat.st_mtime >= started_at - 1:
                candidates.append((stat.st_mtime, video_file))

    if not candidates:
        return None

    return max(candidates, key=lambda item: item[0])[1]


def build_run_video_result(
    started_at,
    dependencies,
    results_dir=None,
):
    try:
        video_file = find_latest_run_video(
            started_at,
            dependencies,
            results_dir,
        )
        if not video_file:
            return {
                "video": None,
                "video_error": (
                    "未找到本次执行视频，请确认 Playwright 已开启 video "
                    "录制并输出到 test-results。"
                ),
            }

        return {
            "video": serialize_run_video(video_file, dependencies),
            "video_error": None,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "video": None,
            "video_error": f"读取本次执行视频失败：{exc}",
        }


def build_playwright_report_result(
    started_at,
    dependencies,
    report_dir=None,
):
    try:
        report_file = find_latest_playwright_report(
            started_at,
            dependencies,
            report_dir,
        )
        if not report_file:
            return {
                "report": None,
                "report_error": (
                    "未找到本次 Playwright HTML report，请确认已配置 "
                    "html reporter 并输出到 playwright-report。"
                ),
            }

        return {
            "report": serialize_playwright_report(
                report_file,
                dependencies,
            ),
            "report_error": None,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "report": None,
            "report_error": (
                f"读取本次 Playwright HTML report 失败：{exc}"
            ),
        }
