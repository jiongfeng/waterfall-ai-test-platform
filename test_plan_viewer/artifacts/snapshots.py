import hashlib
from pathlib import Path


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def read_file_bytes(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def managed_file_snapshot(
    paths,
    *,
    read_bytes=read_file_bytes,
    digest=sha256_bytes,
):
    snapshot = {}
    for path in paths:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path
        content = read_bytes(path)
        snapshot[str(resolved)] = {
            "path": path,
            "exists": content is not None,
            "content": content,
            "hash": digest(content) if content is not None else "",
        }
    return snapshot


def iter_generation_managed_files(specs_dir, tests_dir):
    specs_dir = Path(specs_dir)
    if specs_dir.exists():
        yield from (item for item in specs_dir.rglob("*.md") if item.is_file())

    tests_dir = Path(tests_dir)
    if tests_dir.exists():
        yield from (item for item in tests_dir.rglob("*.spec.ts") if item.is_file())


def collect_generation_managed_files(
    plan_file,
    target_file,
    managed_files,
):
    files = {plan_file, target_file}
    files.update(managed_files)
    return files


def file_hash(path, *, read_bytes=read_file_bytes, digest=sha256_bytes):
    content = read_bytes(path)
    return digest(content) if content is not None else ""
