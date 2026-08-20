#!/usr/bin/env python3
"""Safely update the local platform while preserving native macOS OpenCode."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEPENDENCY_PATHS = (
    "deploy/Dockerfile",
    "requirements.txt",
    "project-template/package.json",
    "project-template/package-lock.json",
)
TOPOLOGY_PATHS = (
    "deploy/compose.yaml",
    "deploy/compose.build.yaml",
    "deploy/platform-compose",
)
class UpdateError(RuntimeError):
    """A fail-closed deployment error."""


def quote_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run(
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            raise UpdateError(f"Command failed: {quote_command(command)}\n{detail}")
        raise UpdateError(f"Command failed: {quote_command(command)}")
    return result


def output(command: Sequence[str], *, cwd: Path) -> str:
    return run(command, cwd=cwd, capture=True).stdout.strip()


def git_exists(repo: Path, revision: str) -> bool:
    result = run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repo,
        capture=True,
        check=False,
    )
    return result.returncode == 0


def changed_paths(repo: Path, old_revision: str, head: str, paths: Sequence[str]) -> list[str]:
    text = output(
        ["git", "diff", "--name-only", f"{old_revision}..{head}", "--", *paths],
        cwd=repo,
    )
    return [line for line in text.splitlines() if line]


def committed_text(repo: Path, revision: str, path: str) -> str:
    return output(["git", "show", f"{revision}:{path}"], cwd=repo)


def pinned_requirements(text: str) -> dict[str, str] | None:
    requirements: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^;\s]+)", line)
        if match is None:
            return None
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in requirements:
            return None
        requirements[name] = match.group(2)
    return requirements


def requirements_are_additive(repo: Path, old_revision: str, head: str) -> bool:
    old = pinned_requirements(committed_text(repo, old_revision, "requirements.txt"))
    new = pinned_requirements(committed_text(repo, head, "requirements.txt"))
    if old is None or new is None or old == new:
        return False
    return all(new.get(name) == version for name, version in old.items())


def safe_extract(archive: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with tarfile.open(archive, "r") as bundle:
        for member in bundle.getmembers():
            member_path = (destination / member.name).resolve()
            if member_path != destination_root and destination_root not in member_path.parents:
                raise UpdateError(f"Refusing unsafe Git archive member: {member.name}")
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, filter="data")
        else:
            bundle.extractall(destination)


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NativeOpenCodeManifest:
    digest: str
    version: str
    machine: str
    binary: str


@dataclass(frozen=True)
class DeploymentState:
    platform_container: str
    opencode: NativeOpenCodeManifest
    platform_image_id: str
    target_image: str
    image_revision: str
    image_architecture: str


class LocalUpdater:
    def __init__(self, repo: Path, wait_seconds: int) -> None:
        self.repo = repo.resolve()
        self.wait_seconds = wait_seconds
        self.wrapper = self.repo / "deploy" / "platform-compose"
        self.native_opencode = self.repo / "deploy" / "native-opencode.py"
        self.runtime_root = self.repo / "deploy" / ".runtime"
        self.env_file = self.repo / ".env"
        self.head = ""

    def check_environment(self) -> None:
        if platform.system() != "Darwin":
            raise UpdateError("This project Skill is restricted to local macOS deployments.")
        if not self.wrapper.is_file():
            raise UpdateError(f"Missing deployment wrapper: {self.wrapper}")
        if not self.native_opencode.is_file():
            raise UpdateError(f"Missing native OpenCode control: {self.native_opencode}")
        top = Path(output(["git", "rev-parse", "--show-toplevel"], cwd=self.repo)).resolve()
        if top != self.repo:
            raise UpdateError(f"Expected Git root {self.repo}, received {top}")
        self.head = output(["git", "rev-parse", "HEAD"], cwd=self.repo)
        run(["docker", "version"], cwd=self.repo, capture=True)
        run([str(self.wrapper), "validate-config"], cwd=self.repo)

    def service_container(self, service: str) -> str:
        container = output([str(self.wrapper), "ps", "--quiet", service], cwd=self.repo)
        lines = [line for line in container.splitlines() if line]
        if len(lines) != 1:
            raise UpdateError(f"Expected one running {service} container, found {len(lines)}.")
        return lines[0]

    def inspect_format(self, target: str, template: str, *, image: bool = False) -> str:
        kind = "image" if image else "container"
        return output(["docker", kind, "inspect", "--format", template, target], cwd=self.repo)

    def opencode_manifest(self) -> NativeOpenCodeManifest:
        raw = output(
            [
                sys.executable,
                str(self.native_opencode),
                "manifest",
                "--runtime-root",
                str(self.runtime_root),
                "--env-file",
                str(self.env_file),
            ],
            cwd=self.repo,
        )
        payload = json.loads(raw)
        if not payload.get("config") or not payload.get("provider_shape"):
            raise UpdateError("Native OpenCode provider manifest is incomplete.")
        if payload.get("machine") != "arm64":
            raise UpdateError("Native OpenCode is not running from an ARM64 installation.")
        return NativeOpenCodeManifest(
            digest=digest_text(raw),
            version=str(payload.get("version", "")),
            machine=str(payload.get("machine", "")),
            binary=str(payload.get("binary", "")),
        )

    def state(self) -> DeploymentState:
        platform_container = self.service_container("platform")
        image_id = self.inspect_format(platform_container, "{{.Image}}")
        target_image = self.inspect_format(platform_container, "{{.Config.Image}}")
        revision = self.inspect_format(
            image_id,
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            image=True,
        )
        architecture = self.inspect_format(image_id, "{{.Architecture}}", image=True)
        if not re.fullmatch(r"[a-z0-9_]+", architecture):
            raise UpdateError(f"Unexpected platform image architecture: {architecture!r}")
        entrypoint = json.loads(
            self.inspect_format(image_id, "{{json .Config.Entrypoint}}", image=True)
        )
        command = json.loads(self.inspect_format(image_id, "{{json .Config.Cmd}}", image=True))
        supported_entrypoints = (
            ["/usr/local/bin/platform-entrypoint"],
            ["/usr/bin/tini", "--", "/usr/local/bin/platform-entrypoint"],
        )
        if entrypoint not in supported_entrypoints or command != ["platform"]:
            raise UpdateError(
                "The running image has an unsupported entrypoint or default command; "
                "use a reviewed full build."
            )
        return DeploymentState(
            platform_container=platform_container,
            opencode=self.opencode_manifest(),
            platform_image_id=image_id,
            target_image=target_image,
            image_revision=revision,
            image_architecture=architecture,
        )

    def verify_health_readonly(self) -> None:
        for service in ("mysql", "platform"):
            container = self.service_container(service)
            running = self.inspect_format(container, "{{.State.Running}}")
            health = self.inspect_format(
                container,
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
            )
            if running != "true" or health != "healthy":
                raise UpdateError(
                    f"Service {service} is not ready (running={running}, health={health})."
                )

    def verify_running(self) -> None:
        run([str(self.wrapper), "verify"], cwd=self.repo)

    def build_mode(self, old_revision: str, full_build: bool) -> tuple[str, list[str]]:
        if not re.fullmatch(r"[0-9a-f]{40}", old_revision) or not git_exists(self.repo, old_revision):
            if full_build:
                return "full", []
            raise UpdateError(
                "The running image does not identify a locally available Git commit. "
                "Re-run with --full-build after reviewing the current Dockerfile."
            )

        topology = changed_paths(self.repo, old_revision, self.head, TOPOLOGY_PATHS)
        if topology and not full_build:
            raise UpdateError(
                "Deployment topology/safety files changed since the running image; "
                "review them and re-run with --full-build: "
                + ", ".join(topology)
            )
        dependencies = changed_paths(self.repo, old_revision, self.head, DEPENDENCY_PATHS)
        if full_build:
            return "full", dependencies
        if dependencies == ["requirements.txt"] and requirements_are_additive(
            self.repo, old_revision, self.head
        ):
            return "dependency-incremental", dependencies
        if dependencies:
            raise UpdateError(
                "Dependency-critical files changed; review them and re-run with --full-build: "
                + ", ".join(dependencies)
            )
        return "incremental", dependencies

    def archive_head(self, destination: Path) -> None:
        archive = destination / "head.tar"
        source = destination / "source"
        source.mkdir()
        run(
            ["git", "archive", "--format=tar", f"--output={archive}", "HEAD"],
            cwd=self.repo,
        )
        safe_extract(archive, source)

    def incremental_dockerfile(self, revision: str, install_requirements: bool = False) -> str:
        dependency_layer = ""
        if install_requirements:
            dependency_layer = r'''
COPY requirements.txt /tmp/playwright-platform-requirements.txt
RUN python -m pip install --no-cache-dir \
        --requirement /tmp/playwright-platform-requirements.txt \
    && rm /tmp/playwright-platform-requirements.txt
'''
        return f'''ARG BASE_IMAGE=waterfall-ai-test-platform:local
FROM ${{BASE_IMAGE}}

ARG VERSION=local
ARG REVISION={revision}
LABEL org.opencontainers.image.version="${{VERSION}}" \\
      org.opencontainers.image.revision="${{REVISION}}"

USER root
WORKDIR /opt/playwright-platform/app
{dependency_layer}

RUN rm -rf ./__pycache__ ./test_plan_viewer ./static ./templates \
    && find ./project-template -mindepth 1 -maxdepth 1 \
        ! -name node_modules ! -name .opencode -exec rm -rf -- {{}} \\; \
    && find ./project-template/.opencode -mindepth 1 -maxdepth 1 \
        ! -name node_modules -exec rm -rf -- {{}} \\;

COPY --chown=pwuser:pwuser app.py requirements.txt LICENSE THIRD_PARTY_NOTICES.md ./
COPY --chown=pwuser:pwuser test_plan_viewer/ ./test_plan_viewer/
COPY --chown=pwuser:pwuser static/ ./static/
COPY --chown=pwuser:pwuser templates/ ./templates/
COPY --chown=pwuser:pwuser project-template/ ./project-template/
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/platform-entrypoint
COPY --chmod=0755 deploy/healthcheck.sh /usr/local/bin/platform-healthcheck

USER pwuser
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=8s --start-period=20s --retries=8 \\
    CMD ["/usr/local/bin/platform-healthcheck", "platform"]
'''

    def build(self, state: DeploymentState, mode: str) -> str:
        version = f"local-{self.head[:12]}"
        with tempfile.TemporaryDirectory(prefix="waterfall-local-update-") as temp_name:
            temp = Path(temp_name)
            self.archive_head(temp)
            source = temp / "source"
            if mode in {"incremental", "dependency-incremental"}:
                dockerfile = temp / "Dockerfile.incremental"
                dockerfile.write_text(
                    self.incremental_dockerfile(
                        self.head,
                        install_requirements=mode == "dependency-incremental",
                    ),
                    encoding="utf-8",
                )
                command = [
                    "docker",
                    "build",
                    "--platform",
                    f"linux/{state.image_architecture}",
                    "--file",
                    str(dockerfile),
                    "--build-arg",
                    f"BASE_IMAGE={state.target_image}",
                    "--build-arg",
                    f"VERSION={version}",
                    "--build-arg",
                    f"REVISION={self.head}",
                    "--tag",
                    state.target_image,
                    str(source),
                ]
            else:
                command = [
                    "docker",
                    "build",
                    "--platform",
                    f"linux/{state.image_architecture}",
                    "--file",
                    str(source / "deploy" / "Dockerfile"),
                    "--build-arg",
                    f"VERSION={version}",
                    "--build-arg",
                    f"REVISION={self.head}",
                    "--build-arg",
                    "SOURCE_URL=local",
                    "--tag",
                    state.target_image,
                    str(source),
                ]
            print(f"Building {mode} image from committed HEAD {self.head} ...", flush=True)
            run(command, cwd=self.repo)

        new_image = output(["docker", "image", "inspect", "--format", "{{.Id}}", state.target_image], cwd=self.repo)
        new_revision = self.inspect_format(
            new_image,
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            image=True,
        )
        if new_revision != self.head:
            run(["docker", "tag", state.platform_image_id, state.target_image], cwd=self.repo)
            raise UpdateError(
                f"Built image revision mismatch ({new_revision!r}); restored tag to the previous image."
            )
        return new_image

    def wait_for_platform(self) -> str:
        deadline = time.monotonic() + self.wait_seconds
        last = "missing"
        while time.monotonic() < deadline:
            container = self.service_container("platform")
            last = self.inspect_format(
                container,
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
            )
            if last == "healthy":
                return container
            if last == "unhealthy":
                break
            time.sleep(2)
        raise UpdateError(f"Platform did not become healthy within {self.wait_seconds}s (status={last}).")

    def recreate_platform(self) -> str:
        run(
            [
                str(self.wrapper),
                "up",
                "--detach",
                "--no-deps",
                "--force-recreate",
                "--no-build",
                "--pull",
                "never",
                "platform",
            ],
            cwd=self.repo,
        )
        return self.wait_for_platform()

    def assert_opencode_unchanged(
        self, before: NativeOpenCodeManifest
    ) -> NativeOpenCodeManifest:
        after = self.opencode_manifest()
        if after != before:
            raise UpdateError(
                "Native OpenCode preservation check failed: binary, version, provider config, or auth shape changed."
            )
        return after

    def deploy(self, state: DeploymentState, new_image: str) -> str:
        try:
            platform_container = self.recreate_platform()
            self.verify_running()
            self.assert_opencode_unchanged(state.opencode)
            active_image = self.inspect_format(platform_container, "{{.Image}}")
            if active_image != new_image:
                raise UpdateError(
                    f"Platform container image mismatch: expected {new_image}, received {active_image}."
                )
            return platform_container
        except Exception as deployment_error:
            print("Deployment verification failed; restoring the previous platform image ...", file=sys.stderr)
            run(["docker", "tag", state.platform_image_id, state.target_image], cwd=self.repo)
            try:
                self.recreate_platform()
                self.verify_running()
                self.assert_opencode_unchanged(state.opencode)
            except Exception as rollback_error:
                raise UpdateError(
                    f"Deployment failed ({deployment_error}); rollback also failed ({rollback_error})."
                ) from rollback_error
            raise UpdateError(
                f"Deployment failed and the previous platform image was restored: {deployment_error}"
            ) from deployment_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update the local macOS platform container from committed Git HEAD "
            "while preserving native OpenCode."
        )
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Run read-only preflight and print the selected build mode.")
    modes.add_argument("--verify-only", action="store_true", help="Verify the running stack and OpenCode manifest without building.")
    parser.add_argument(
        "--full-build",
        action="store_true",
        help="Build the full deploy/Dockerfile instead of layering committed app files on the current image.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[4],
        help="Repository root (defaults to the project containing this Skill).",
    )
    parser.add_argument("--wait-seconds", type=int, default=180, help="Platform health timeout (default: 180).")
    args = parser.parse_args()
    if args.wait_seconds < 15 or args.wait_seconds > 900:
        parser.error("--wait-seconds must be between 15 and 900")
    if args.verify_only and args.full_build:
        parser.error("--verify-only cannot be combined with --full-build")
    return args


def main() -> int:
    args = parse_args()
    updater = LocalUpdater(args.repo, args.wait_seconds)
    try:
        updater.check_environment()
        state = updater.state()
        updater.verify_health_readonly()

        if args.verify_only:
            updater.verify_running()
            updater.assert_opencode_unchanged(state.opencode)
            print(
                "Verified existing local stack: "
                f"platform={state.platform_container[:12]} "
                f"image_revision={state.image_revision or 'unknown'} "
                f"opencode={state.opencode.version} machine={state.opencode.machine} "
                "provider_manifest=preserved"
            )
            return 0

        mode, dependencies = updater.build_mode(state.image_revision, args.full_build)
        dirty = output(["git", "status", "--short"], cwd=updater.repo)
        print(f"Committed target revision: {updater.head}")
        print(f"Current image revision:   {state.image_revision or 'unknown'}")
        print(f"Selected build mode:      {mode}")
        if dependencies:
            print("Dependency changes:        " + ", ".join(dependencies))
        if dirty:
            print("Local worktree changes are present and will be excluded by git archive HEAD.")
        print(
            "Protected OpenCode:        "
            f"native={state.opencode.version} machine={state.opencode.machine} "
            f"provider_manifest={state.opencode.digest[:12]}"
        )
        if args.dry_run:
            print("Dry run complete; no image was built and no container was recreated.")
            return 0

        updater.verify_running()
        updater.assert_opencode_unchanged(state.opencode)
        new_image = updater.build(state, mode)
        platform_container = updater.deploy(state, new_image)
        print(
            "Local platform update verified: "
            f"revision={updater.head} mode={mode} image={new_image} "
            f"platform={platform_container[:12]} opencode={state.opencode.version} "
            "provider_manifest=preserved"
        )
        return 0
    except (UpdateError, json.JSONDecodeError, OSError, tarfile.TarError) as error:
        print(f"update-local-mac-platform: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
