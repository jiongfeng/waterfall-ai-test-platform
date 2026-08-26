import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPOSITORY_ROOT / "deploy"
CONFIG_CONTROL = DEPLOY_DIR / "configctl.py"
NATIVE_OPENCODE_CONTROL = DEPLOY_DIR / "native-opencode.py"
PLATFORM_COMPOSE = DEPLOY_DIR / "platform-compose"


class DeployRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temp_path = Path(self.temporary_directory.name)

    @staticmethod
    def valid_config():
        config = json.loads(
            (DEPLOY_DIR / "config.example.json").read_text(encoding="utf-8")
        )
        config["opencode_password"] = '测试-$-"-\\-password'
        config["platform_database"]["password"] = "database-test-password"
        config["projects"][0]["description"] = (
            '中文, quotes " and slash \\ and dollar $'
        )
        return config

    def write_source_config(self, path, config=None, mode=0o600):
        payload = self.valid_config() if config is None else config
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        path.chmod(mode)

    @staticmethod
    def run_configctl(*arguments):
        return subprocess.run(
            [sys.executable, str(CONFIG_CONTROL), *map(str, arguments)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_configctl_stages_canonical_config_behind_private_directory(self):
        source = self.temp_path / "config.json"
        destination = (
            self.temp_path / "runtime" / "secrets" / "platform-config.json"
        )
        expected = self.valid_config()
        self.write_source_config(source, expected)

        result = self.run_configctl(
            "stage",
            "--source",
            source,
            "--destination",
            destination,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "staged")
        self.assertEqual(
            stat.S_IMODE((self.temp_path / "runtime").stat().st_mode),
            0o700,
        )
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o444)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), expected)
        self.assertFalse(destination.read_bytes().endswith(b"\n"))
        self.assertNotIn("password", result.stdout)
        self.assertNotIn("password", result.stderr)

        runtime_result = self.run_configctl(
            "validate-runtime",
            "--source",
            destination,
        )
        self.assertEqual(runtime_result.returncode, 0, runtime_result.stderr)

    def test_configctl_stages_native_mac_runtime_overrides_without_mutating_source(self):
        source = self.temp_path / "config.json"
        destination = self.temp_path / "runtime/secrets/platform-config.json"
        projects_root = self.temp_path / "runtime/data/playwright-projects"
        workspaces_root = self.temp_path / "runtime/data/playwright-workspaces"
        original = self.valid_config()
        self.write_source_config(source, original)

        result = self.run_configctl(
            "stage",
            "--source",
            source,
            "--destination",
            destination,
            "--projects-root",
            projects_root,
            "--workspaces-root",
            workspaces_root,
            "--opencode-url",
            "http://host.docker.internal:4096",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        staged = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            staged["opencode_server_url"],
            "http://host.docker.internal:4096",
        )
        self.assertEqual(staged["project_workspace_root"], str(workspaces_root))
        self.assertEqual(
            staged["project_template_dependency_source_root"],
            str(projects_root / "default"),
        )
        self.assertEqual(
            staged["projects"][0]["playwright_project_root"],
            str(projects_root / "default"),
        )
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)

    def test_configctl_rejects_source_permissions_without_leaking_content(self):
        source = self.temp_path / "config.json"
        self.write_source_config(source, mode=0o640)

        result = self.run_configctl("validate", "--source", source)

        self.assertEqual(result.returncode, 1)
        self.assertIn("mode must be 0600", result.stderr)
        self.assertNotIn('测试-$-"-\\-password', result.stderr)

    def test_configctl_rejects_symlink_source(self):
        source = self.temp_path / "config.json"
        symlink = self.temp_path / "config-link.json"
        self.write_source_config(source)
        symlink.symlink_to(source)

        result = self.run_configctl("validate", "--source", symlink)

        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot securely open", result.stderr)

    def test_configctl_rejects_invalid_and_oversized_json(self):
        source = self.temp_path / "config.json"
        source.write_text("{not-json", encoding="utf-8")
        source.chmod(0o600)
        invalid_result = self.run_configctl("validate", "--source", source)

        source.write_bytes(b"{" + (b"x" * (64 * 1024)))
        source.chmod(0o600)
        oversized_result = self.run_configctl("validate", "--source", source)

        self.assertEqual(invalid_result.returncode, 1)
        self.assertIn("invalid JSON", invalid_result.stderr)
        self.assertEqual(oversized_result.returncode, 1)
        self.assertIn("exceeds the 65536-byte", oversized_result.stderr)

    def test_configctl_rejects_missing_runtime_secrets_and_placeholders(self):
        source = self.temp_path / "config.json"
        config = self.valid_config()
        config["opencode_password"] = "replace-me"
        self.write_source_config(source, config)

        result = self.run_configctl("validate", "--source", source)

        self.assertEqual(result.returncode, 1)
        self.assertIn("opencode_password still contains a placeholder", result.stderr)
        self.assertNotIn("replace-me", result.stderr)

    def test_configctl_rejects_an_unsupported_default_project_language(self):
        source = self.temp_path / "config.json"
        config = self.valid_config()
        config["default_project_language"] = "fr"
        self.write_source_config(source, config)

        result = self.run_configctl("validate", "--source", source)

        self.assertEqual(result.returncode, 1)
        self.assertIn("default_project_language", result.stderr)

    def test_configctl_fails_closed_on_nonprivate_runtime_directory(self):
        source = self.temp_path / "config.json"
        runtime = self.temp_path / "runtime"
        destination = runtime / "secrets" / "platform-config.json"
        self.write_source_config(source)
        runtime.mkdir(mode=0o755)

        result = self.run_configctl(
            "stage",
            "--source",
            source,
            "--destination",
            destination,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime directory mode must be 0700", result.stderr)
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o755)

    def test_configctl_resolves_only_a_canonical_compose_project_identity(self):
        environment_file = self.temp_path / ".env"
        environment_file.write_text(
            "SECRET=not-printed\nCOMPOSE_PROJECT_NAME=isolated-stack\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)

        resolved = self.run_configctl(
            "compose-project",
            "--source",
            environment_file,
            "--default",
            "waterfall-ai-test-platform",
        )

        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(resolved.stdout.strip(), "isolated-stack")
        self.assertNotIn("not-printed", resolved.stdout + resolved.stderr)

        environment_file.write_text(
            "COMPOSE_PROJECT_NAME=first\nCOMPOSE_PROJECT_NAME=second\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)
        duplicate = self.run_configctl(
            "compose-project",
            "--source",
            environment_file,
            "--default",
            "waterfall-ai-test-platform",
        )
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("duplicate COMPOSE_PROJECT_NAME", duplicate.stderr)

    def test_configctl_initializes_quickstart_secrets_without_printing_them(self):
        source = self.temp_path / "config.json"
        environment_file = self.temp_path / ".env"
        shutil.copy2(DEPLOY_DIR / "config.example.json", source)
        shutil.copy2(REPOSITORY_ROOT / ".env.example", environment_file)
        source.chmod(0o600)
        environment_file.chmod(0o600)

        result = self.run_configctl(
            "initialize",
            "--config",
            source,
            "--environment",
            environment_file,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "initialized")
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(environment_file.stat().st_mode), 0o600)
        assignments = dict(
            line.split("=", 1)
            for line in environment_file.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        secret_names = (
            "PLATFORM_SESSION_SECRET",
            "PLATFORM_ADMIN_PASSWORD",
            "PLATFORM_DB_PASSWORD",
            "OPENCODE_SERVER_PASSWORD",
            "MYSQL_ROOT_PASSWORD",
        )
        generated = {name: assignments[name] for name in secret_names}
        self.assertTrue(all(generated.values()))
        self.assertEqual(len(set(generated.values())), len(secret_names))
        config = json.loads(source.read_text(encoding="utf-8"))
        self.assertEqual(
            config["opencode_password"],
            generated["OPENCODE_SERVER_PASSWORD"],
        )
        self.assertEqual(
            config["platform_database"]["password"],
            generated["PLATFORM_DB_PASSWORD"],
        )
        for secret in generated.values():
            self.assertNotIn(secret, result.stdout + result.stderr)

        repeated = self.run_configctl(
            "initialize",
            "--config",
            source,
            "--environment",
            environment_file,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        repeated_assignments = dict(
            line.split("=", 1)
            for line in environment_file.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        self.assertEqual(
            {name: repeated_assignments[name] for name in secret_names},
            generated,
        )

    def test_platform_compose_initializes_config_without_docker(self):
        source = self.temp_path / "config.json"
        environment_file = self.temp_path / ".env"
        shutil.copy2(DEPLOY_DIR / "config.example.json", source)
        shutil.copy2(REPOSITORY_ROOT / ".env.example", environment_file)
        source.chmod(0o600)
        environment_file.chmod(0o600)
        environment = os.environ.copy()
        environment.update(
            {
                "PLATFORM_CONFIG_FILE": str(source),
                "PLATFORM_ENV_FILE": str(environment_file),
            }
        )

        result = subprocess.run(
            [str(PLATFORM_COMPOSE), "init-config"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "initialized")
        self.assertNotIn("docker", result.stdout.lower() + result.stderr.lower())

    def test_platform_compose_validates_config_without_docker(self):
        source = self.temp_path / "config.json"
        self.write_source_config(source)
        environment = os.environ.copy()
        environment["PLATFORM_CONFIG_FILE"] = str(source)

        result = subprocess.run(
            [str(PLATFORM_COMPOSE), "validate-config"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "validated")

    def test_platform_compose_uses_explicit_secure_env_file_from_other_cwd(self):
        source = self.temp_path / "config.json"
        environment_file = self.temp_path / ".env"
        runtime = self.temp_path / "runtime"
        executable_directory = self.temp_path / "bin"
        other_directory = self.temp_path / "other"
        capture = self.temp_path / "docker-arguments.txt"
        fake_docker = executable_directory / "docker"
        self.write_source_config(source)
        environment_file.write_text(
            "COMPOSE_PROJECT_NAME=validation-stack\n"
            "PLATFORM_DB_PASSWORD=test-db\n"
            "MYSQL_ROOT_PASSWORD=test-root\n"
            "OPENCODE_SERVER_PASSWORD=test-opencode\n"
            "PLATFORM_SESSION_SECRET=test-session\n"
            "PLATFORM_ADMIN_PASSWORD=test-admin\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)
        executable_directory.mkdir()
        other_directory.mkdir()
        fake_docker.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = compose ] && [ \"$2\" = version ]; then\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' __CALL__ \"$@\" >> \"$FAKE_DOCKER_CAPTURE\"\n",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("COMPOSE_PROJECT_NAME", None)
        environment.update(
            {
                "FAKE_DOCKER_CAPTURE": str(capture),
                "PATH": f"{executable_directory}:{environment['PATH']}",
                "PLATFORM_CONFIG_FILE": str(source),
                "PLATFORM_ENV_FILE": str(environment_file),
                "PLATFORM_RUNTIME_DIR": str(runtime),
                "PLATFORM_OPENCODE_MODE": "container",
            }
        )

        preflight = subprocess.run(
            [str(PLATFORM_COMPOSE), "preflight-install"],
            cwd=other_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        self.assertIn("fresh install", preflight.stdout)
        preflight_arguments = capture.read_text(encoding="utf-8")
        self.assertIn(
            "label=com.docker.compose.project=validation-stack",
            preflight_arguments,
        )
        self.assertFalse(runtime.exists())
        capture.unlink()

        result = subprocess.run(
            [str(PLATFORM_COMPOSE), "ps"],
            cwd=other_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = capture.read_text(encoding="utf-8").splitlines()
        self.assertEqual(arguments[0], "__CALL__")
        self.assertIn("--env-file", arguments)
        self.assertIn(str(environment_file), arguments)
        self.assertIn("--project-directory", arguments)
        self.assertIn(str(REPOSITORY_ROOT), arguments)
        self.assertIn("--project-name", arguments)
        self.assertIn("validation-stack", arguments)
        self.assertEqual(arguments[-1], "ps")

        capture.unlink()
        build_result = subprocess.run(
            [str(PLATFORM_COMPOSE), "up", "--build", "--detach"],
            cwd=other_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(build_result.returncode, 0, build_result.stderr)
        build_lines = capture.read_text(encoding="utf-8").splitlines()
        call_starts = [
            index for index, argument in enumerate(build_lines) if argument == "__CALL__"
        ]
        self.assertEqual(len(call_starts), 3, build_lines)
        pull_arguments = build_lines[call_starts[0] + 1 : call_starts[1]]
        build_arguments = build_lines[call_starts[1] + 1 : call_starts[2]]
        runtime_arguments = build_lines[call_starts[2] + 1 :]
        self.assertEqual(
            pull_arguments[-4:], ["pull", "--policy", "missing", "mysql"]
        )
        self.assertNotIn(str(DEPLOY_DIR / "compose.build.yaml"), pull_arguments)
        self.assertNotIn("platform", pull_arguments[-4:])
        self.assertNotIn("opencode", pull_arguments[-4:])
        self.assertIn(str(DEPLOY_DIR / "compose.build.yaml"), build_arguments)
        self.assertEqual(build_arguments[-2:], ["build", "platform"])
        self.assertEqual(build_arguments.count("build"), 1)
        self.assertNotIn(str(DEPLOY_DIR / "compose.build.yaml"), runtime_arguments)
        self.assertEqual(
            runtime_arguments[-5:],
            ["up", "--no-build", "--pull", "never", "--detach"],
        )

        capture.unlink()
        conflicting_pull = subprocess.run(
            [
                str(PLATFORM_COMPOSE),
                "up",
                "--build",
                "--pull",
                "always",
                "--detach",
            ],
            cwd=other_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(conflicting_pull.returncode, 1)
        self.assertIn("requires --pull never", conflicting_pull.stderr)
        self.assertFalse(capture.exists())

        drifted_environment = environment.copy()
        drifted_environment["COMPOSE_PROJECT_NAME"] = "wrong-stack"
        drifted = subprocess.run(
            [str(PLATFORM_COMPOSE), "ps"],
            cwd=other_directory,
            env=drifted_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(drifted.returncode, 1)
        self.assertIn("Ambient COMPOSE_PROJECT_NAME does not match", drifted.stderr)
        self.assertFalse(capture.exists())

        environment_file.chmod(0o640)
        rejected = subprocess.run(
            [str(PLATFORM_COMPOSE), "ps"],
            cwd=other_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("environment mode must be 0600", rejected.stderr)

    def test_compose_preserves_hardening_and_declares_runtime_mounts(self):
        compose = (DEPLOY_DIR / "compose.yaml").read_text(encoding="utf-8")
        build_compose = (DEPLOY_DIR / "compose.build.yaml").read_text(
            encoding="utf-8"
        )
        wrapper = PLATFORM_COMPOSE.read_text(encoding="utf-8")

        self.assertIn(
            "${MYSQL_IMAGE:-docker.io/library/mysql:8.4@sha256:"
            "1d6b6a8fcee8ff758ff151d017f5203cd06792a0e698f0a593c9dfcb14609cf0}",
            compose,
        )
        self.assertNotIn("build:", compose)
        self.assertEqual(build_compose.count("build:"), 1)
        self.assertIn("context: .", build_compose)
        self.assertNotIn("context: ..", build_compose)
        self.assertIn("opencode_state:/home/pwuser/.local/state", compose)
        self.assertIn(
            "PLATFORM_CONFIG_PATH: /run/secrets/platform-config.json",
            compose,
        )
        self.assertIn("file: ${PLATFORM_RUNTIME_CONFIG_FILE:?", compose)
        self.assertNotIn("PLATFORM_CONFIG_JSON", compose)
        self.assertNotIn("/etc/playwright-platform/config.json", compose)
        self.assertEqual(compose.count("read_only: true"), 2)
        self.assertEqual(compose.count("no-new-privileges:true"), 2)
        self.assertEqual(compose.count("- ALL"), 2)
        self.assertEqual(
            wrapper.count("run_compose exec --no-TTY --interactive=false"),
            4,
        )
        self.assertIn("/usr/local/bin/platform-opencode-provider", wrapper)
        self.assertIn("profiles:", compose)
        self.assertIn("container-opencode", compose)
        self.assertIn("PLATFORM_PROJECTS_MOUNT_TYPE", compose)
        self.assertIn("PLATFORM_WORKSPACES_MOUNT_TYPE", compose)
        self.assertIn("http://host.docker.internal:4096", wrapper)

    def test_native_opencode_is_isolated_and_rejects_an_unmanaged_listener(self):
        helper = NATIVE_OPENCODE_CONTROL.read_text(encoding="utf-8")

        self.assertIn('HOST = "127.0.0.1"', helper)
        self.assertIn('LABEL = "com.waterfall-ai.native-opencode"', helper)
        self.assertIn('"XDG_CONFIG_HOME": str(paths["config"])', helper)
        self.assertIn('"XDG_DATA_HOME": str(paths["data"])', helper)
        self.assertIn("port {PORT} is occupied by an unmanaged OpenCode process", helper)
        self.assertIn("native OpenCode LaunchAgent has no live process", helper)
        self.assertNotIn("0.0.0.0", helper)

    def test_release_runtime_forces_no_build_and_no_pull(self):
        release_root = self.temp_path / "release"
        release_deploy = release_root / "deploy"
        executable_directory = self.temp_path / "bin"
        source = release_root / "config.json"
        environment_file = release_root / ".env"
        runtime = release_deploy / ".runtime"
        capture = self.temp_path / "docker-arguments.txt"
        fake_docker = executable_directory / "docker"
        release_deploy.mkdir(parents=True)
        executable_directory.mkdir()
        for name in ("platform-compose", "configctl.py", "compose.yaml"):
            shutil.copy2(DEPLOY_DIR / name, release_deploy / name)
        self.write_source_config(source)
        environment_file.write_text(
            "COMPOSE_PROJECT_NAME=release-stack\n"
            "PLATFORM_DB_PASSWORD=test-db\n"
            "MYSQL_ROOT_PASSWORD=test-root\n"
            "OPENCODE_SERVER_PASSWORD=test-opencode\n"
            "PLATFORM_SESSION_SECRET=test-session\n"
            "PLATFORM_ADMIN_PASSWORD=test-admin\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)
        fake_docker.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = compose ] && [ \"$2\" = version ]; then\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' \"$@\" >> \"$FAKE_DOCKER_CAPTURE\"\n",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("COMPOSE_PROJECT_NAME", None)
        environment.update(
            {
                "FAKE_DOCKER_CAPTURE": str(capture),
                "PATH": f"{executable_directory}:{environment['PATH']}",
                "PLATFORM_CONFIG_FILE": str(source),
                "PLATFORM_ENV_FILE": str(environment_file),
                "PLATFORM_RUNTIME_DIR": str(runtime),
                "PLATFORM_OPENCODE_MODE": "container",
            }
        )
        packaged_wrapper = release_deploy / "platform-compose"

        forbidden_pull = subprocess.run(
            [str(packaged_wrapper), "up", "--pull", "always", "--detach"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(forbidden_pull.returncode, 1)
        self.assertIn("Release runtime requires --pull never", forbidden_pull.stderr)
        self.assertFalse(capture.exists())

        forbidden_build = subprocess.run(
            [str(packaged_wrapper), "up", "--build", "--detach"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(forbidden_build.returncode, 1)
        self.assertIn("Source build override is unavailable", forbidden_build.stderr)
        self.assertFalse(capture.exists())

        allowed = subprocess.run(
            [str(packaged_wrapper), "up", "--detach"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        arguments = capture.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            arguments[-5:],
            ["up", "--no-build", "--pull", "never", "--detach"],
        )
        self.assertNotIn("pull", arguments[:-4])
        self.assertNotIn("build", arguments)

    def test_repair_opencode_volumes_is_project_scoped_and_restricted(self):
        source = self.temp_path / "config.json"
        environment_file = self.temp_path / ".env"
        runtime = self.temp_path / "runtime"
        executable_directory = self.temp_path / "bin"
        capture = self.temp_path / "docker-calls.jsonl"
        fake_docker = executable_directory / "docker"
        self.write_source_config(source)
        environment_file.write_text(
            "COMPOSE_PROJECT_NAME=validation-stack\n"
            "PLATFORM_DB_PASSWORD=test-db\n"
            "MYSQL_ROOT_PASSWORD=test-root\n"
            "OPENCODE_SERVER_PASSWORD=test-opencode\n"
            "PLATFORM_SESSION_SECRET=test-session\n"
            "PLATFORM_ADMIN_PASSWORD=test-admin\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)
        executable_directory.mkdir()
        fake_docker.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_CAPTURE"], "a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\\n")

if args[:2] == ["compose", "version"]:
    raise SystemExit(0)
if args[:3] == ["compose", "up", "--help"]:
    if os.environ.get("FAKE_COMPOSE_WAIT_SUPPORTED") == "1":
        print("      --wait                  Wait for services to be running|healthy")
        print("      --wait-timeout int      Maximum duration in seconds to wait")
    raise SystemExit(0)
if args and args[0] == "compose":
    if "--force-recreate" in args and os.environ.get("FAKE_COMPOSE_RECREATE_FAIL") == "1":
        raise SystemExit(1)
    if "config" in args:
        print(json.dumps({"services": {"platform": {"image": "test:image"}}}))
    elif "ps" in args:
        print("opencode-container")
    raise SystemExit(0)
if args[:2] == ["image", "inspect"]:
    raise SystemExit(0)
if args and args[0] == "inspect":
    print(json.dumps([{"Mounts": [
        {"Type": "volume", "Destination": "/home/pwuser/.config/opencode", "Name": "config-volume"},
        {"Type": "volume", "Destination": "/home/pwuser/.local/share/opencode", "Name": "data-volume"},
        {"Type": "volume", "Destination": "/home/pwuser/.cache/opencode", "Name": "cache-volume"},
        {"Type": "volume", "Destination": "/home/pwuser/.local/state", "Name": "state-volume"},
    ]}]))
    raise SystemExit(0)
if args[:2] == ["volume", "inspect"]:
    keys = {
        "config-volume": "opencode_config",
        "data-volume": "opencode_data",
        "cache-volume": "opencode_cache",
        "state-volume": "opencode_state",
    }
    print(f"validation-stack:{keys[args[-1]]}")
    raise SystemExit(0)
if args and args[0] == "run":
    if any("id -u pwuser" in argument for argument in args):
        print("1234:2345")
    raise SystemExit(0)
raise SystemExit(1)
""",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("COMPOSE_PROJECT_NAME", None)
        environment.update(
            {
                "FAKE_DOCKER_CAPTURE": str(capture),
                "FAKE_COMPOSE_WAIT_SUPPORTED": "1",
                "PATH": f"{executable_directory}:{environment['PATH']}",
                "PLATFORM_CONFIG_FILE": str(source),
                "PLATFORM_ENV_FILE": str(environment_file),
                "PLATFORM_RUNTIME_DIR": str(runtime),
                "PLATFORM_OPENCODE_MODE": "container",
            }
        )

        result = subprocess.run(
            [str(PLATFORM_COMPOSE), "repair-opencode-volumes"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [
            json.loads(line)
            for line in capture.read_text(encoding="utf-8").splitlines()
        ]
        repair_helpers = [
            call
            for call in calls
            if call and call[0] == "run" and "--user" in call
        ]
        self.assertEqual(len(repair_helpers), 2, calls)
        root_helper = next(
            call
            for call in repair_helpers
            if call[call.index("--user") + 1] == "0:0"
        )
        probe_helper = next(
            call
            for call in repair_helpers
            if call[call.index("--user") + 1] == "1234:2345"
        )
        expected_mounts = {
            "config-volume:/volumes/config",
            "data-volume:/volumes/data",
            "cache-volume:/volumes/cache",
            "state-volume:/volumes/state",
        }
        for helper in (root_helper, probe_helper):
            mounts = {
                helper[index + 1]
                for index, value in enumerate(helper)
                if value == "--volume"
            }
            self.assertEqual(mounts, expected_mounts)
            self.assertIn("--network", helper)
            self.assertEqual(helper[helper.index("--network") + 1], "none")
            self.assertIn("--read-only", helper)
            self.assertIn("ALL", helper)
            self.assertIn("no-new-privileges:true", helper)
            self.assertIn('test ! -L "${directory}"', "\n".join(helper))
        for capability in ("CHOWN", "DAC_OVERRIDE", "FOWNER"):
            self.assertIn(capability, root_helper)
            self.assertNotIn(capability, probe_helper)
        self.assertEqual(root_helper[-2:], ["1234", "2345"])
        root_script = "\n".join(root_helper)
        self.assertLess(
            root_script.index('test ! -L "${directory}"'),
            root_script.index("mkdir -p"),
        )

        compose_calls = [call for call in calls if call and call[0] == "compose"]
        stop_index = next(index for index, call in enumerate(calls) if "stop" in call)
        root_index = calls.index(root_helper)
        self.assertLess(stop_index, root_index)
        recreate = next(call for call in compose_calls if "--force-recreate" in call)
        self.assertIn("--no-build", recreate)
        self.assertEqual(recreate[recreate.index("--pull") + 1], "never")
        self.assertIn("--wait", recreate)
        self.assertEqual(recreate[recreate.index("--wait-timeout") + 1], "180")
        self.assertFalse(any("down" in call for call in compose_calls))
        self.assertFalse(any("--volumes" in call or "-v" in call for call in calls))

        capture.unlink()
        legacy_result = subprocess.run(
            [str(PLATFORM_COMPOSE), "repair-state"],
            cwd=self.temp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(legacy_result.returncode, 0, legacy_result.stderr)
        legacy_calls = [
            json.loads(line)
            for line in capture.read_text(encoding="utf-8").splitlines()
        ]
        legacy_helpers = [
            call
            for call in legacy_calls
            if call and call[0] == "run" and "--user" in call
        ]
        self.assertEqual(len(legacy_helpers), 2, legacy_calls)
        for helper in legacy_helpers:
            mounts = [
                helper[index + 1]
                for index, value in enumerate(helper)
                if value == "--volume"
            ]
            self.assertEqual(mounts, ["state-volume:/state"])
            self.assertIn("test ! -L /state/opencode", "\n".join(helper))
        legacy_root = next(
            helper
            for helper in legacy_helpers
            if helper[helper.index("--user") + 1] == "0:0"
        )
        legacy_root_script = "\n".join(legacy_root)
        self.assertLess(
            legacy_root_script.index("test ! -L /state/opencode"),
            legacy_root_script.index("mkdir -p /state/opencode"),
        )
        legacy_recreate = next(
            call for call in legacy_calls if "--force-recreate" in call
        )
        self.assertIn("--wait", legacy_recreate)
        self.assertFalse(any("down" in call for call in legacy_calls))

        capture.unlink()
        recreate_failure_environment = environment.copy()
        recreate_failure_environment["FAKE_COMPOSE_RECREATE_FAIL"] = "1"
        recreate_failure = subprocess.run(
            [str(PLATFORM_COMPOSE), "repair-opencode-volumes"],
            cwd=self.temp_path,
            env=recreate_failure_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(recreate_failure.returncode, 1)
        self.assertIn("did not become healthy", recreate_failure.stderr)
        recreate_failure_calls = [
            json.loads(line)
            for line in capture.read_text(encoding="utf-8").splitlines()
        ]
        stop_calls = [
            call
            for call in recreate_failure_calls
            if call and call[0] == "compose" and "stop" in call
        ]
        self.assertEqual(len(stop_calls), 2, recreate_failure_calls)
        self.assertIn("--force-recreate", recreate_failure_calls[-2])
        self.assertIn("stop", recreate_failure_calls[-1])

        capture.unlink()
        unsupported_environment = environment.copy()
        unsupported_environment["FAKE_COMPOSE_WAIT_SUPPORTED"] = "0"
        unsupported = subprocess.run(
            [str(PLATFORM_COMPOSE), "repair-opencode-volumes"],
            cwd=self.temp_path,
            env=unsupported_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(unsupported.returncode, 1)
        self.assertIn("must support both --wait and --wait-timeout", unsupported.stderr)
        unsupported_calls = [
            json.loads(line)
            for line in capture.read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(
            any(call[:3] == ["compose", "up", "--help"] for call in unsupported_calls)
        )
        self.assertFalse(any("stop" in call for call in unsupported_calls))
        self.assertFalse(any(call and call[0] == "run" for call in unsupported_calls))
        self.assertFalse(any("--force-recreate" in call for call in unsupported_calls))

    def test_image_and_entrypoint_enforce_state_and_config_contracts(self):
        dockerfile = (DEPLOY_DIR / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (DEPLOY_DIR / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("HOME=/home/pwuser", dockerfile)
        self.assertIn("XDG_STATE_HOME=/home/pwuser/.local/state", dockerfile)
        self.assertIn('"${XDG_STATE_HOME}/opencode"', dockerfile)
        self.assertIn("validate_opencode_volumes", entrypoint)
        self.assertIn("OpenCode ${label} restricted write probe failed", entrypoint)
        self.assertIn("validate_platform_config", entrypoint)
        self.assertIn("Configuration must be mounted read-only", entrypoint)

    def test_image_repairs_only_the_known_openssl_copyright_migration(self):
        dockerfile = (DEPLOY_DIR / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("license_link=/usr/share/doc/openssl/copyright", dockerfile)
        self.assertIn("old_target=../libssl3/copyright", dockerfile)
        self.assertIn("new_target=../libssl3t64/copyright", dockerfile)
        self.assertIn('test -L "${license_link}"', dockerfile)
        self.assertIn('test ! -L "${new_target_path}"', dockerfile)
        self.assertIn('test -s "${new_target_path}"', dockerfile)
        self.assertIn('test -r "${new_target_path}"', dockerfile)
        self.assertIn(
            'test ! -e "${old_target_path}" && test ! -L "${old_target_path}"',
            dockerfile,
        )
        self.assertIn('test "${current_target}" = "${old_target}"', dockerfile)
        self.assertIn('test "${current_target}" != "${new_target}"', dockerfile)
        self.assertIn('rm -- "${license_link}"', dockerfile)
        self.assertIn('ln -s -- "${new_target}" "${license_link}"', dockerfile)
        self.assertIn('readlink -f -- "${license_link}"', dockerfile)

    def test_wrapper_forbids_volume_deletion_and_direct_compose_bypass(self):
        wrapper = PLATFORM_COMPOSE.read_text(encoding="utf-8")
        compose = (DEPLOY_DIR / "compose.yaml").read_text(encoding="utf-8")
        dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(
            encoding="utf-8"
        ).splitlines()

        self.assertIn("-v | --volumes | --volumes=*", wrapper)
        self.assertIn("repair-opencode-volumes", wrapper)
        self.assertIn("repair-state", wrapper)
        self.assertIn("resolve_runtime_identity", wrapper)
        self.assertNotIn("docker image pull", wrapper)
        self.assertIn("repair_state_volume()", wrapper)
        self.assertIn("repair_opencode_volumes()", wrapper)
        self.assertIn("recreate_opencode_and_wait", wrapper)
        self.assertIn("require_compose_wait_support", wrapper)
        self.assertIn('stat --format="%u:%g" "${directory}"', wrapper)
        self.assertIn('stat --format="%a" "${directory}"', wrapper)
        for runtime_path in (
            '"${config_root}"',
            '"${data_root}"',
            '"${data_root}/log"',
            '"${data_root}/repos"',
            '"${cache_root}"',
            '"${state_root}"',
            '"${state_root}/opencode"',
        ):
            self.assertIn(runtime_path, wrapper)
        self.assertIn("--build | --build=*", wrapper)
        self.assertIn('"${BUILD_COMPOSE_FILE}"', wrapper)
        self.assertIn('--project-name "${COMPOSE_PROJECT}"', wrapper)
        self.assertIn("deploy/.runtime/", dockerignore)
        self.assertIn(
            "Run deploy/platform-compose instead of docker compose directly",
            compose,
        )

        rejected = subprocess.run(
            [str(PLATFORM_COMPOSE), "down", "--volumes=true"],
            cwd=self.temp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("is forbidden", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
