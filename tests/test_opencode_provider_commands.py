from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CONTROL = REPOSITORY_ROOT / "deploy" / "opencode-provider.py"
NATIVE_OPENCODE_CONTROL = REPOSITORY_ROOT / "deploy" / "native-opencode.py"
PLATFORM_COMPOSE = REPOSITORY_ROOT / "deploy" / "platform-compose"


class OpenCodeProviderCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        self.capture = self.root / "opencode-arguments.json"
        self.fake_opencode = self.bin_directory / "opencode"
        self.fake_opencode.write_text(
            textwrap.dedent(
                """
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys

                Path(os.environ["FAKE_OPENCODE_CAPTURE"]).write_text(
                    json.dumps({"arguments": sys.argv[1:], "cwd": os.getcwd()}),
                    encoding="utf-8",
                )
                if sys.argv[1:2] == ["models"]:
                    print("example/test-model")
                elif sys.argv[1:3] == ["auth", "list"]:
                    print("example")
                elif sys.argv[1:2] == ["run"]:
                    if os.environ.get("FAKE_SMOKE_FAIL") == "1":
                        raise SystemExit(7)
                    print('{"type":"text","text":"WATERFALL_AI_PROVIDER_OK"}')
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.fake_opencode.chmod(0o755)
        fake_file = self.bin_directory / "file"
        fake_file.write_text(
            "#!/bin/sh\nprintf 'Mach-O 64-bit executable arm64\\n'\n",
            encoding="utf-8",
        )
        fake_file.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "FAKE_OPENCODE_CAPTURE": str(self.capture),
                "PATH": f"{self.bin_directory}{os.pathsep}{self.environment['PATH']}",
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "XDG_STATE_HOME": str(self.root / "state"),
            }
        )

    def run_provider(self, *arguments: str, environment=None):
        return subprocess.run(
            [sys.executable, str(PROVIDER_CONTROL), *arguments],
            cwd=self.root,
            env=environment or self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_set_model_preserves_provider_config_and_secures_the_file(self):
        config_directory = self.root / "config" / "opencode"
        config_directory.mkdir(parents=True, mode=0o700)
        config = config_directory / "opencode.json"
        config.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "provider": {"example": {"name": "Example"}},
                }
            ),
            encoding="utf-8",
        )

        result = self.run_provider("set-model", "example/test-model")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(payload["model"], "example/test-model")
        self.assertEqual(payload["provider"]["example"]["name"], "Example")
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertNotIn("key", result.stdout.lower() + result.stderr.lower())

        status = self.run_provider("show-model")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("example/test-model", status.stdout)

    def test_set_model_rejects_jsonc_and_invalid_model_ids(self):
        config_directory = self.root / "config" / "opencode"
        config_directory.mkdir(parents=True, mode=0o700)
        (config_directory / "opencode.jsonc").write_text(
            '{"model": "example/old"} // keep this comment\n',
            encoding="utf-8",
        )

        jsonc_result = self.run_provider("set-model", "example/new")
        invalid_result = self.run_provider("set-model", "missing-provider")

        self.assertEqual(jsonc_result.returncode, 1)
        self.assertIn("update its model field manually", jsonc_result.stderr)
        self.assertFalse((config_directory / "opencode.json").exists())
        self.assertEqual(invalid_result.returncode, 1)
        self.assertIn("provider/model", invalid_result.stderr)

    def test_models_and_smoke_use_isolated_working_directories(self):
        models_result = self.run_provider("models", "example")
        self.assertEqual(models_result.returncode, 0, models_result.stderr)
        self.assertIn("example/test-model", models_result.stdout)
        models_call = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(models_call["arguments"], ["models", "example"])
        self.assertNotEqual(Path(models_call["cwd"]), REPOSITORY_ROOT)

        smoke_result = self.run_provider("smoke", "example/test-model")
        self.assertEqual(smoke_result.returncode, 0, smoke_result.stderr)
        self.assertEqual(
            smoke_result.stdout.strip(),
            "OpenCode provider inference verified: example/test-model",
        )
        smoke_call = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(
            smoke_call["arguments"][:5],
            [
                "run",
                "--model",
                "example/test-model",
                "--format",
                "json",
            ],
        )
        self.assertIn("WATERFALL_AI_PROVIDER_OK", smoke_call["arguments"][-1])

    def test_smoke_fails_closed_without_printing_provider_output(self):
        environment = self.environment.copy()
        environment["FAKE_SMOKE_FAIL"] = "1"

        result = self.run_provider(
            "smoke",
            "example/test-model",
            environment=environment,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("inference failed", result.stderr)
        self.assertNotIn("WATERFALL_AI_PROVIDER_OK", result.stdout + result.stderr)

    def test_auth_login_rejects_a_noninteractive_terminal(self):
        result = self.run_provider("auth-login")

        self.assertEqual(result.returncode, 1)
        self.assertIn("interactive terminal", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_native_provider_command_uses_the_isolated_runtime_roots(self):
        runtime = self.root / "runtime"
        environment_file = self.root / ".env"
        environment_file.write_text(
            "OPENCODE_SERVER_PASSWORD=test-server-password\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)

        result = subprocess.run(
            [
                sys.executable,
                str(NATIVE_OPENCODE_CONTROL),
                "provider",
                "--runtime-root",
                str(runtime),
                "--env-file",
                str(environment_file),
                "--opencode-binary",
                str(self.fake_opencode),
                "--provider-action",
                "set-model",
                "--provider-value",
                "example/test-model",
            ],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        config = runtime / "native-opencode/config/opencode/opencode.json"
        self.assertEqual(
            json.loads(config.read_text(encoding="utf-8"))["model"],
            "example/test-model",
        )
        self.assertFalse(
            (Path(self.environment["XDG_CONFIG_HOME"]) / "opencode/opencode.json").exists()
        )

    def test_platform_wrapper_exposes_only_the_supported_provider_commands(self):
        wrapper = PLATFORM_COMPOSE.read_text(encoding="utf-8")

        for command in (
            "opencode-auth-login",
            "opencode-auth-list",
            "opencode-models",
            "opencode-set-model",
            "opencode-model-status",
            "opencode-provider-smoke",
        ):
            self.assertIn(command, wrapper)
        self.assertIn("/usr/local/bin/platform-opencode-provider", wrapper)
        self.assertNotIn('opencode-provider-cli "$@"', wrapper)

    def test_platform_wrapper_routes_container_model_configuration(self):
        source = self.root / "config.json"
        environment_file = self.root / ".env"
        runtime = self.root / "runtime"
        docker_capture = self.root / "docker-arguments.json"
        config = json.loads(
            (REPOSITORY_ROOT / "deploy/config.example.json").read_text(
                encoding="utf-8"
            )
        )
        config["opencode_password"] = "test-server-password"
        config["platform_database"]["password"] = "test-database-password"
        source.write_text(json.dumps(config), encoding="utf-8")
        source.chmod(0o600)
        environment_file.write_text(
            "COMPOSE_PROJECT_NAME=provider-test\n"
            "PLATFORM_DB_PASSWORD=test-database-password\n"
            "MYSQL_ROOT_PASSWORD=test-root-password\n"
            "OPENCODE_SERVER_PASSWORD=test-server-password\n"
            "PLATFORM_SESSION_SECRET=test-session-secret\n"
            "PLATFORM_ADMIN_PASSWORD=test-admin-password\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)
        fake_docker = self.bin_directory / "docker"
        fake_docker.write_text(
            textwrap.dedent(
                """
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys

                arguments = sys.argv[1:]
                if arguments[:2] == ["compose", "version"]:
                    raise SystemExit(0)
                Path(os.environ["FAKE_DOCKER_CAPTURE"]).write_text(
                    json.dumps(arguments),
                    encoding="utf-8",
                )
                """
            ).lstrip(),
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)
        environment = self.environment.copy()
        environment.update(
            {
                "FAKE_DOCKER_CAPTURE": str(docker_capture),
                "PLATFORM_CONFIG_FILE": str(source),
                "PLATFORM_ENV_FILE": str(environment_file),
                "PLATFORM_OPENCODE_MODE": "container",
                "PLATFORM_RUNTIME_DIR": str(runtime),
            }
        )
        environment.pop("COMPOSE_PROJECT_NAME", None)

        result = subprocess.run(
            [str(PLATFORM_COMPOSE), "opencode-set-model", "example/test-model"],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(docker_capture.read_text(encoding="utf-8"))
        self.assertEqual(
            arguments[-7:],
            [
                "exec",
                "--no-TTY",
                "--interactive=false",
                "opencode",
                "/usr/local/bin/platform-opencode-provider",
                "set-model",
                "example/test-model",
            ],
        )


if __name__ == "__main__":
    unittest.main()
