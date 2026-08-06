from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPOSITORY_ROOT / "deploy" / "Dockerfile"
ENTRYPOINT = REPOSITORY_ROOT / "deploy" / "entrypoint.sh"


class OpenCodeImageContractTests(unittest.TestCase):
    def test_version_probe_is_isolated_from_runtime_home_and_all_xdg_roots(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("opencode_probe_root=\"$(mktemp -d)\"", dockerfile)
        for variable, suffix in (
            ("HOME", "home"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_STATE_HOME", "state"),
        ):
            self.assertIn(
                f'{variable}=\"${{opencode_probe_root}}/{suffix}\"',
                dockerfile,
            )

        version_command = dockerfile.index("opencode --version")
        probe_cleanup = dockerfile.index(
            'rm -rf -- "${opencode_probe_root}"', version_command
        )
        self.assertGreater(probe_cleanup, version_command)

    def test_image_creates_and_fail_closed_checks_every_runtime_directory(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        runtime_directories = {
            '"${XDG_CACHE_HOME}/opencode"': 3,
            '"${XDG_CONFIG_HOME}/opencode"': 3,
            '"${XDG_DATA_HOME}/opencode"': 3,
            '"${XDG_DATA_HOME}/opencode/log"': 2,
            '"${XDG_DATA_HOME}/opencode/repos"': 2,
            '"${XDG_STATE_HOME}"': 3,
            '"${XDG_STATE_HOME}/opencode"': 2,
        }

        for runtime_directory, minimum_occurrences in runtime_directories.items():
            self.assertGreaterEqual(
                dockerfile.count(runtime_directory), minimum_occurrences
            )
        self.assertIn('runtime_uid="$(id -u pwuser)"', dockerfile)
        self.assertIn('runtime_gid="$(id -g pwuser)"', dockerfile)
        self.assertIn("! -uid \"${runtime_uid}\"", dockerfile)
        self.assertIn("! -gid \"${runtime_gid}\"", dockerfile)
        self.assertNotIn("1001", dockerfile)


class OpenCodeEntrypointContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        self._write_executable(
            self.bin_directory / "stat",
            """
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import stat as stat_module
            import sys

            format_argument, target_argument = sys.argv[1:]
            target = Path(target_argument)
            result = os.stat(target)
            requested_format = format_argument.removeprefix("--format=")
            if os.environ.get("TEST_BAD_STAT_PATH") == str(target) and requested_format == "%u:%g":
                print(f"{result.st_uid + 1}:{result.st_gid + 1}")
            elif os.environ.get("TEST_BAD_MODE_PATH") == str(target) and requested_format == "%a":
                print("777")
            elif requested_format == "%u:%g":
                print(f"{result.st_uid}:{result.st_gid}")
            elif requested_format == "%a":
                print(format(stat_module.S_IMODE(result.st_mode), "o"))
            else:
                raise SystemExit(f"unexpected stat invocation: {sys.argv[1:]}")
            """,
        )
        self._write_executable(
            self.bin_directory / "opencode",
            """
            #!/usr/bin/env bash
            printf 'fake-opencode:'
            printf ' %s' "$@"
            printf '\n'
            """,
        )

        self.home = self.root / "home"
        self.config_root = self.root / "config" / "opencode"
        self.data_root = self.root / "data" / "opencode"
        self.cache_root = self.root / "cache" / "opencode"
        self.state_root = self.root / "state"
        for directory in (
            self.home,
            self.config_root,
            self.data_root,
            self.data_root / "log",
            self.data_root / "repos",
            self.cache_root,
            self.state_root,
            self.state_root / "opencode",
        ):
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            directory.chmod(0o700)

        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HOME": str(self.home),
                "OPENCODE_SERVER_PASSWORD": "test-only-password",
                "PATH": f"{self.bin_directory}{os.pathsep}{self.environment['PATH']}",
                "PLATFORM_PROJECTS_ROOT": str(self.root / "projects"),
                "PLATFORM_WORKSPACES_ROOT": str(self.root / "workspaces"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.state_root),
            }
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_executable(self, path: Path, contents: str) -> None:
        path.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run_entrypoint(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "opencode"],
            cwd=REPOSITORY_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_volume_roots_and_required_children_pass_restricted_probes(self):
        completed = self._run_entrypoint()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "fake-opencode: serve --hostname 0.0.0.0 --port 4096",
            completed.stdout,
        )
        self.assertEqual(
            list(self.root.rglob(".platform-write-probe.*")),
            [],
        )

    def test_wrong_owner_fails_closed_and_names_the_repair_command(self):
        self.environment["TEST_BAD_STAT_PATH"] = str(self.data_root / "log")

        completed = self._run_entrypoint()

        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("fake-opencode", completed.stdout)
        self.assertIn("data log", completed.stderr)
        self.assertIn(
            "platform-compose repair-opencode-volumes",
            completed.stderr,
        )

    def test_missing_required_child_fails_instead_of_mutating_the_volume(self):
        (self.data_root / "repos").rmdir()

        completed = self._run_entrypoint()

        self.assertEqual(completed.returncode, 1)
        self.assertFalse((self.data_root / "repos").exists())
        self.assertIn("data repos", completed.stderr)
        self.assertIn(
            "platform-compose repair-opencode-volumes",
            completed.stderr,
        )

    def test_permissive_directory_mode_fails_closed(self):
        self.environment["TEST_BAD_MODE_PATH"] = str(self.cache_root)

        completed = self._run_entrypoint()

        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("fake-opencode", completed.stdout)
        self.assertIn("cache volume", completed.stderr)
        self.assertIn("mode 777, expected 0700", completed.stderr)
        self.assertIn(
            "platform-compose repair-opencode-volumes",
            completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
