import re
from pathlib import Path


CJK_NAME_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ASCII_LETTER_PATTERN = re.compile(r"[A-Za-z]")
ARTIFACT_FILENAME_UNSAFE_PATTERN = re.compile(r"[\\/<>:\"|?*\x00]+")


def validate_module_name(module_name):
    if not module_name or module_name.strip() != module_name:
        raise ValueError("Invalid module name.")

    if module_name in {".", ".."}:
        raise ValueError("Invalid module name.")

    if "/" in module_name or "\\" in module_name:
        raise ValueError("Module name cannot contain path separators.")

    if "\x00" in module_name:
        raise ValueError("Invalid module name.")

    return module_name


def has_chinese_text(value, *, pattern=CJK_NAME_PATTERN):
    return bool(pattern.search(str(value or "")))


def has_ascii_letters(value, *, pattern=ASCII_LETTER_PATTERN):
    return bool(pattern.search(str(value or "")))


def is_chinese_artifact_stem(
    stem,
    *,
    has_chinese=has_chinese_text,
    has_ascii=has_ascii_letters,
):
    stem = str(stem or "").strip()
    return bool(stem) and has_chinese(stem) and not has_ascii(stem)


def strip_artifact_suffix(value, suffix):
    text = str(value or "").strip()
    if text.lower().endswith(suffix.lower()):
        return text[: -len(suffix)]
    return text


def stable_numeric_suffix(value):
    total = 0
    for char in str(value or ""):
        total = (total * 131 + ord(char)) % 1000000
    return f"{total:06d}"


def sanitize_chinese_artifact_stem(
    value,
    fallback="测试用例",
    unique_key=None,
    *,
    strip_suffix=strip_artifact_suffix,
    is_chinese_stem=is_chinese_artifact_stem,
    numeric_suffix=stable_numeric_suffix,
    unsafe_pattern=ARTIFACT_FILENAME_UNSAFE_PATTERN,
    ascii_pattern=ASCII_LETTER_PATTERN,
):
    stem = strip_suffix(strip_suffix(value, ".spec.ts"), ".md")
    stem = unsafe_pattern.sub("-", stem)
    stem = ascii_pattern.sub("", stem)
    stem = re.sub(r"[\s._-]+", "-", stem).strip(" -.。_-")
    if not is_chinese_stem(stem):
        fallback_stem = strip_suffix(
            strip_suffix(fallback, ".spec.ts"),
            ".md",
        )
        fallback_stem = unsafe_pattern.sub("-", fallback_stem)
        fallback_stem = ascii_pattern.sub("", fallback_stem)
        fallback_stem = re.sub(r"[\s._-]+", "-", fallback_stem).strip(" -.。_-")
        stem = (
            fallback_stem
            if is_chinese_stem(fallback_stem)
            else "测试用例"
        )
    if unique_key:
        stem = f"{stem}-{numeric_suffix(unique_key)}"
    return stem


def validate_chinese_artifact_stem(
    stem,
    label,
    *,
    is_chinese_stem=is_chinese_artifact_stem,
):
    if not is_chinese_stem(stem):
        raise ValueError(f"{label}必须使用中文名称，且不能包含英文字母。")
    return stem


def validate_plan_filename(filename):
    if not filename or filename.strip() != filename:
        raise ValueError("Invalid plan filename.")

    if filename in {".", ".."}:
        raise ValueError("Invalid plan filename.")

    if "/" in filename or "\\" in filename:
        raise ValueError("Plan filename cannot contain path separators.")

    if "\x00" in filename:
        raise ValueError("Invalid plan filename.")

    if not filename.endswith(".md"):
        raise ValueError("Plan filename must end with .md.")

    return filename


def validate_chinese_plan_filename(
    filename,
    *,
    validate_plan=validate_plan_filename,
    validate_chinese_stem=validate_chinese_artifact_stem,
):
    filename = validate_plan(filename)
    validate_chinese_stem(Path(filename).stem, "测试计划文件名")
    return filename


def get_default_plan_filename(
    module_name,
    *,
    validate_module=validate_module_name,
):
    module_name = validate_module(module_name)
    return f"{module_name}.md"


def get_plan_filename_from_name(
    plan_name,
    module_name,
    *,
    validate_plan=validate_plan_filename,
):
    plan_name = str(plan_name or "").strip()
    if not plan_name:
        plan_name = module_name
    if not plan_name.endswith(".md"):
        plan_name = f"{plan_name}.md"
    return validate_plan(plan_name)


def get_chinese_plan_filename_from_name(
    plan_name,
    module_name,
    fallback_stem=None,
    unique_key=None,
    *,
    plan_filename_from_name=get_plan_filename_from_name,
    is_chinese_stem=is_chinese_artifact_stem,
    has_chinese=has_chinese_text,
    sanitize_stem=sanitize_chinese_artifact_stem,
    validate_chinese_plan=validate_chinese_plan_filename,
):
    candidate = plan_filename_from_name(plan_name, module_name)
    if is_chinese_stem(Path(candidate).stem):
        return candidate
    fallback = fallback_stem or module_name or "测试计划"
    if not has_chinese(fallback):
        fallback = "测试计划"
        unique_key = unique_key or f"{module_name}/{plan_name}"
    stem = sanitize_stem(
        candidate,
        fallback=fallback,
        unique_key=unique_key,
    )
    return validate_chinese_plan(f"{stem}.md")


def get_case_plan_filename_from_title(
    filename,
    title,
    index=None,
    *,
    strip_suffix=strip_artifact_suffix,
    is_chinese_stem=is_chinese_artifact_stem,
    sanitize_stem=sanitize_chinese_artifact_stem,
    validate_chinese_plan=validate_chinese_plan_filename,
):
    candidate = str(filename or "").strip() or str(title or "").strip()
    fallback = str(title or "").strip() or "测试用例"
    candidate_stem = strip_suffix(candidate, ".md")
    unique_key = (
        str(index)
        if index is not None and not is_chinese_stem(candidate_stem)
        else None
    )
    stem = sanitize_stem(
        candidate,
        fallback=fallback,
        unique_key=unique_key,
    )
    return validate_chinese_plan(f"{stem}.md")


def validate_script_filename(filename):
    if not filename or filename.strip() != filename:
        raise ValueError("Invalid script filename.")

    if filename in {".", ".."}:
        raise ValueError("Invalid script filename.")

    if "/" in filename or "\\" in filename:
        raise ValueError("Script filename cannot contain path separators.")

    if "\x00" in filename:
        raise ValueError("Invalid script filename.")

    if not filename.endswith(".spec.ts"):
        raise ValueError("Script filename must end with .spec.ts.")

    return filename


def script_stem_from_filename(
    filename,
    *,
    strip_suffix=strip_artifact_suffix,
):
    return strip_suffix(filename, ".spec.ts")


def validate_chinese_script_filename(
    filename,
    *,
    validate_script=validate_script_filename,
    script_stem=script_stem_from_filename,
    validate_chinese_stem=validate_chinese_artifact_stem,
):
    filename = validate_script(filename)
    validate_chinese_stem(
        script_stem(filename),
        "测试脚本文件名",
    )
    return filename


def is_plan_index_filename(filename):
    path_name = Path(str(filename or "")).name
    stem = Path(path_name).stem
    return (
        path_name.startswith("_")
        or stem in {"用例索引", "case-index"}
        or stem.endswith("-用例索引")
    )


def plan_payload(
    plan_file,
    module_name,
    *,
    default_plan_filename=get_default_plan_filename,
    plan_index_filename=is_plan_index_filename,
):
    return {
        "name": plan_file.stem,
        "filename": plan_file.name,
        "path": str(plan_file),
        "is_default": plan_file.name == default_plan_filename(module_name),
        "is_index": plan_index_filename(plan_file.name),
    }


def get_script_filename_from_plan_filename(
    plan_filename,
    *,
    validate_plan=validate_plan_filename,
    validate_script=validate_script_filename,
):
    plan_filename = validate_plan(plan_filename)
    return validate_script(f"{Path(plan_filename).stem}.spec.ts")


def get_generated_script_filename_from_plan_filename(
    plan_filename,
    *,
    validate_plan=validate_plan_filename,
    is_chinese_stem=is_chinese_artifact_stem,
    validate_chinese_script=validate_chinese_script_filename,
    sanitize_stem=sanitize_chinese_artifact_stem,
):
    plan_filename = validate_plan(plan_filename)
    stem = Path(plan_filename).stem
    if is_chinese_stem(stem):
        return validate_chinese_script(f"{stem}.spec.ts")
    fallback_stem = sanitize_stem(
        stem,
        fallback="测试脚本",
        unique_key=plan_filename,
    )
    return validate_chinese_script(f"{fallback_stem}.spec.ts")
