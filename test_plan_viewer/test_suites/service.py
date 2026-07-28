"""Test-suite use-case helpers that depend on project script services."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TestSuiteItemDependencies:
    """Collaborators used while resolving a suite item from user input."""

    validate_module_name: Callable[[str], str]
    validate_script_filename: Callable[[str], str]
    get_script_file: Callable[[str, str], object]
    strip_spec_suffix: Callable[[str], str]


def normalize_suite_item_input(raw_item, dependencies):
    """Validate one requested script and resolve its concrete file."""

    if not isinstance(dependencies, TestSuiteItemDependencies):
        raise TypeError(
            "dependencies must be a TestSuiteItemDependencies instance"
        )
    if not isinstance(raw_item, dict):
        raise ValueError("items must contain objects.")
    module_name = dependencies.validate_module_name(
        str(raw_item.get("module_name") or "").strip()
    )
    filename = dependencies.validate_script_filename(
        str(raw_item.get("filename") or "").strip()
    )
    script_file = dependencies.get_script_file(module_name, filename)
    if not script_file.exists():
        raise FileNotFoundError(
            f"Script file not found: {script_file}"
        )
    display_name = (
        str(raw_item.get("display_name") or "").strip()
        or dependencies.strip_spec_suffix(filename)
    )
    return {
        "module_name": module_name,
        "filename": filename,
        "display_name": display_name[:255],
        "script_file": script_file,
    }
