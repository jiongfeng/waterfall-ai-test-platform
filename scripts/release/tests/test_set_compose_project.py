#!/usr/bin/env python3

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from set_compose_project import update  # type: ignore[import-not-found]  # noqa: E402


class SetComposeProjectTests(unittest.TestCase):
    def test_replaces_default_without_creating_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(
                "PLATFORM_PORT=5000\nCOMPOSE_PROJECT_NAME=playwright-test-platform\n",
                encoding="utf-8",
            )
            update(path, "release-smoke-123")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines.count("COMPOSE_PROJECT_NAME=release-smoke-123"), 1)
            self.assertEqual(sum(line.startswith("COMPOSE_PROJECT_NAME=") for line in lines), 1)

    def test_rejects_duplicate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("COMPOSE_PROJECT_NAME=one\nCOMPOSE_PROJECT_NAME=two\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                update(path, "release-smoke-123")


if __name__ == "__main__":
    unittest.main()
