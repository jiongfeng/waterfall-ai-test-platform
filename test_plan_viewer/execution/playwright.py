"""Playwright configuration discovery and command construction."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable


PLAYWRIGHT_CONFIG_FILENAMES = (
    "playwright.config.ts",
    "playwright.config.mts",
    "playwright.config.cts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)


@dataclass(frozen=True)
class PlaywrightDependencies:
    """Filesystem and executable lookup capabilities."""

    path_is_file: Callable[[Path], bool]
    get_npx_executable: Callable[[], str]


def find_playwright_config(project_root, dependencies):
    for filename in PLAYWRIGHT_CONFIG_FILENAMES:
        config_file = project_root / filename
        if dependencies.path_is_file(config_file):
            return config_file
    return None


def get_config_import_path(config_file, base_dir):
    import_path = config_file.relative_to(base_dir).as_posix()
    if not import_path.startswith("."):
        import_path = f"./{import_path}"

    if config_file.suffix in {".ts", ".mts", ".cts"}:
        return import_path[: -len(config_file.suffix)]

    return import_path


def quote_command_argument(argument):
    argument = str(argument)
    if not argument:
        return '""'
    if any(char.isspace() for char in argument):
        return json.dumps(argument, ensure_ascii=False)
    return argument


def build_playwright_test_command(
    config_file,
    relative_script_paths,
    dependencies,
):
    command_display = [
        "npx",
        "playwright",
        "test",
        "--config",
        str(config_file),
        "--trace=on",
        *relative_script_paths,
    ]
    command = [
        dependencies.get_npx_executable(),
        *command_display[1:],
    ]
    return (
        command,
        " ".join(
            quote_command_argument(item)
            for item in command_display
        ),
    )


def build_playwright_merge_reports_command(
    config_file,
    blob_report_dir,
    dependencies,
):
    command_display = [
        "npx",
        "playwright",
        "merge-reports",
        "--config",
        str(config_file),
        str(blob_report_dir),
    ]
    command = [
        dependencies.get_npx_executable(),
        *command_display[1:],
    ]
    return (
        command,
        " ".join(
            quote_command_argument(item)
            for item in command_display
        ),
    )
