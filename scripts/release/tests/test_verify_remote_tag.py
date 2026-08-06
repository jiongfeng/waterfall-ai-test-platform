#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import verify_remote_tag  # noqa: E402


COMMIT_SHA = "1" * 40
TAG_OBJECT_SHA = "2" * 40


class RemoteTagTests(unittest.TestCase):
    def test_lightweight_tag_resolves_to_commit(self) -> None:
        with mock.patch.object(
            verify_remote_tag,
            "gh_json",
            return_value={"object": {"type": "commit", "sha": COMMIT_SHA}},
        ):
            self.assertEqual(
                verify_remote_tag.resolve("owner/repository", "v1.2.3"),
                COMMIT_SHA,
            )

    def test_annotated_tag_is_peeled(self) -> None:
        responses = [
            {"object": {"type": "tag", "sha": TAG_OBJECT_SHA}},
            {"object": {"type": "commit", "sha": COMMIT_SHA}},
        ]
        with mock.patch.object(verify_remote_tag, "gh_json", side_effect=responses):
            self.assertEqual(
                verify_remote_tag.resolve("owner/repository", "v1.2.3"),
                COMMIT_SHA,
            )

    def test_annotated_tag_cycle_is_rejected(self) -> None:
        response = {"object": {"type": "tag", "sha": TAG_OBJECT_SHA}}
        with mock.patch.object(verify_remote_tag, "gh_json", return_value=response):
            with self.assertRaisesRegex(ValueError, "cycle"):
                verify_remote_tag.resolve("owner/repository", "v1.2.3")


if __name__ == "__main__":
    unittest.main()
