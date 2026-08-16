"""Seed script generation, mode detection, and concurrency coordination."""

import hashlib
import json
from pathlib import Path
import tempfile
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


VISIT_ONLY_SEED_MARKER = "waterfall-seed-mode: visit_only"
LOGIN_SEED_MARKER = "waterfall-seed-mode: login"


def build_visit_only_seed_script(base_url):
    """Render a deterministic Seed that opens the target without auth."""

    target_url = str(base_url or "").strip()
    if not target_url:
        raise ValueError("请先配置被测系统地址。")
    target_url_literal = json.dumps(target_url, ensure_ascii=False)
    return (
        "import { test, expect } from '@playwright/test';\n\n"
        f"// {VISIT_ONLY_SEED_MARKER}\n"
        "// This Seed only visits the configured target system. It does not perform authentication.\n"
        "test('visit target system', async ({ page }) => {\n"
        f"  const targetUrl = {target_url_literal};\n"
        "  const response = await page.goto(targetUrl);\n\n"
        "  expect(response, 'the target system should return a document response').not.toBeNull();\n"
        "  await expect(page.locator('body')).toBeVisible();\n"
        "});\n"
    )


def detect_seed_mode(content, fallback="login"):
    """Infer the effective mode from the canonical Seed file content."""

    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    text = str(content or "")
    if (
        VISIT_ONLY_SEED_MARKER in text
        and LOGIN_SEED_MARKER not in text
    ):
        return "visit_only"
    if LOGIN_SEED_MARKER in text:
        return "login"
    if text.strip():
        return ""
    return fallback if fallback in {"visit_only", "login"} else "login"


def apply_seed_mode_marker(content, seed_mode):
    """Keep exactly one platform-owned mode marker in a Seed script."""

    marker = (
        VISIT_ONLY_SEED_MARKER
        if seed_mode == "visit_only"
        else LOGIN_SEED_MARKER
    )
    owned_markers = {
        f"// {VISIT_ONLY_SEED_MARKER}",
        f"// {LOGIN_SEED_MARKER}",
    }
    lines = [
        line
        for line in str(content or "").splitlines()
        if line.strip() not in owned_markers
    ]
    insert_at = 1 if lines else 0
    if len(lines) > 1 and not lines[1].strip():
        insert_at = 2
    lines.insert(insert_at, f"// {marker}")
    return f"{'\n'.join(lines).rstrip()}\n"


class SeedCompletionProbe:
    """Detect content written after a login Seed request starts."""

    def __init__(self, target_file, file_hash):
        self.target_file = Path(target_file)
        self.file_hash = file_hash
        self.original_hash = file_hash(self.target_file)
        try:
            self.original_mtime_ns = self.target_file.stat().st_mtime_ns
        except FileNotFoundError:
            self.original_mtime_ns = 0

    def check(self):
        try:
            stat = self.target_file.stat()
        except FileNotFoundError:
            return False
        if not self.target_file.is_file() or stat.st_size <= 0:
            return False
        current_hash = self.file_hash(self.target_file)
        return bool(
            (current_hash and current_hash != self.original_hash)
            or stat.st_mtime_ns != self.original_mtime_ns
        )


class SeedGenerationLease:
    """Idempotent lease for one canonical Seed target."""

    def __init__(self, lock, lock_file=None):
        self._lock = lock
        self._lock_file = lock_file
        self._guard = threading.Lock()
        self._released = False

    def release(self):
        with self._guard:
            if self._released:
                return
            self._released = True
            try:
                if self._lock_file is not None:
                    try:
                        fcntl.flock(
                            self._lock_file.fileno(),
                            fcntl.LOCK_UN,
                        )
                    finally:
                        self._lock_file.close()
            finally:
                self._lock.release()


_SEED_LOCKS = {}
_SEED_LOCKS_GUARD = threading.Lock()


def acquire_seed_generation_lease(target_file):
    """Acquire a non-blocking process lease for one Seed file."""

    try:
        key = str(Path(target_file).resolve(strict=False))
    except OSError:
        key = str(Path(target_file))
    with _SEED_LOCKS_GUARD:
        lock = _SEED_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return None
    lock_file = None
    try:
        if fcntl is not None:
            lock_root = Path(tempfile.gettempdir()) / "waterfall-seed-locks"
            lock_root.mkdir(parents=True, exist_ok=True)
            lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
            lock_file = (lock_root / f"{lock_name}.lock").open("a+b")
            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError:
                lock_file.close()
                lock.release()
                return None
        return SeedGenerationLease(lock, lock_file)
    except Exception:
        if lock_file is not None and not lock_file.closed:
            lock_file.close()
        lock.release()
        raise


def release_lease_after(iterable, lease):
    """Keep a Seed lease for the full lifetime of a streaming response."""

    try:
        yield from iterable
    finally:
        lease.release()


def finalize_seed_payload(
    services,
    target_file,
    seed_mode,
    *,
    job_id=None,
):
    """Validate and index a generated Seed without coupling to Flask."""

    content = target_file.read_text(encoding="utf-8")
    marked_content = apply_seed_mode_marker(content, seed_mode)
    if marked_content != content:
        services.write_file_atomically(
            target_file,
            marked_content.encode("utf-8"),
        )
        content = marked_content
    services.validate_generated_script_content(content, target_file.name)
    script_asset = services.sync_script_asset(
        services.module_name,
        target_file,
        change_source="generator",
        source_job_id=job_id,
        from_plan_asset_id=None,
        message=(
            f"generator: {services.module_name}/{target_file.name}"
        ),
    )
    try:
        persistence_result = services.persist_seed_mode(seed_mode)
    except Exception:
        persistence_state = "failed"
    else:
        persistence_state = (
            "skipped" if persistence_result is None else "persisted"
        )
    try:
        revisions = (
            [
                services.serialize_revision(item)
                for item in services.list_asset_revisions(
                    script_asset["asset_id"],
                    10,
                )
            ]
            if script_asset
            else []
        )
    except Exception:
        revisions = []
    return {
        "module_name": services.module_name,
        "filename": services.script_filename,
        "target_path": str(target_file),
        "seed_script_path": services.get_seed_script_relative_path(),
        "seed_mode": seed_mode,
        "seed_mode_persistence": persistence_state,
        "asset": services.serialize_asset(script_asset),
        "revisions": revisions,
    }


def generate_visit_only_seed(services, base_url, target_file):
    """Write and finalize a visit Seed, restoring the old file on failure."""

    try:
        original_content = target_file.read_bytes()
    except FileNotFoundError:
        original_content = None
    content = build_visit_only_seed_script(base_url)
    services.write_file_atomically(target_file, content.encode("utf-8"))
    try:
        return finalize_seed_payload(
            services,
            target_file,
            "visit_only",
        )
    except Exception:
        if original_content is None:
            target_file.unlink(missing_ok=True)
        else:
            services.write_file_atomically(target_file, original_content)
        raise


__all__ = [
    "SeedCompletionProbe",
    "SeedGenerationLease",
    "LOGIN_SEED_MARKER",
    "VISIT_ONLY_SEED_MARKER",
    "acquire_seed_generation_lease",
    "apply_seed_mode_marker",
    "build_visit_only_seed_script",
    "detect_seed_mode",
    "finalize_seed_payload",
    "generate_visit_only_seed",
    "release_lease_after",
]
