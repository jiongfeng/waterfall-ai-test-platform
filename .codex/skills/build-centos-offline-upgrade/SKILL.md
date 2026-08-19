---
name: build-centos-offline-upgrade
description: Build, repair, or verify an offline Docker upgrade package for this project's internal CentOS 7.6 x86_64 deployment. Use when asked to make an 内网 Docker 部署升级包, upgrade the port-5001 platform from a stated Git revision, rebuild the platform image without internet access, preserve GLM4.7/OpenCode and DM8 setup-script SSH mounts, diagnose a failed upgrade package, or produce checksum, rollback, and installation instructions.
---

# Build CentOS Offline Upgrade

Package only committed source for the existing `playwright_platform_5001` stack. Build and validate a Linux AMD64 platform image while preserving the live OpenCode/GLM4.7 and DM8 configuration.

## Safety contract

- Treat the user's stated deployed revision as the baseline. Resolve the running image tag from `.env`; validate its OCI revision instead of assuming a tag.
- Build runtime from `git archive <target-revision>`. Exclude uncommitted and untracked files and report them.
- Never overwrite deployed `docker-compose.yml`, `config`, `data`, or `secrets`; change only the unique `PLATFORM_IMAGE` line in `.env`.
- Recreate only `platform` with `--no-deps --force-recreate`. Never recreate or restart `opencode`, MySQL, or volumes.
- Preserve and compare OpenCode provider/auth files, setup scripts and bindings, secrets hashes, OpenCode container ID, and both read-only mounts:
  `/run/secrets/dm_restore_ed25519` and `/run/secrets/dm_known_hosts`.
- Do not delete old tasks or database data unless the current user explicitly requests it. Always take a non-empty MySQL backup before switching images.
- Do not invoke GLM or execute a DM8 restore during packaging or verification.
- Keep the previous platform image until installation and verification succeed. Restore the old `.env` and platform automatically after a failed switch.

Read [references/package-contract.md](references/package-contract.md) before creating or repairing a package.

## Workflow

1. Inspect `git status --short`, recent commits, the requested baseline commit, and existing ignored packages under `deploy-packages/`. Identify the exact committed target revision and the commits included since baseline.
2. Run the complete application tests and linters appropriate to the changes. If runtime dependencies changed, bundle a complete CPython 3.10 Linux AMD64 wheelhouse and pin every direct dependency.
3. Create a new uniquely named package; never overwrite a package already delivered to the user. Use a suffix such as `fix1` for a repair.
4. Detect the target layout before building `build/runtime/`: public releases keep runtime roots at repository root, while legacy commits keep them below `test-plan-viewer/`. Extract the matching layout with `git archive`. Generate per-file runtime and wheelhouse SHA-256 manifests.
5. Build the target image from the deployed baseline image using `DOCKER_BUILDKIT=0 docker build --pull=false --network=none`. The legacy builder is intentional when an ARM Mac holds a local-only AMD64 baseline image; BuildKit may try the registry instead. Require OCI labels for the full target revision and version. Validate `linux/amd64` and `pwuser`.
6. Put feature-specific assertions in an image smoke block inside `upgrade.sh`. Assert against the actual source file: Jinja partial content must be read from the partial, not inferred from `index.html`. Do not assert implementation details that are unrelated to installability.
7. Execute the exact embedded image smoke block against the built image before packaging. This is mandatory; compiling it is insufficient.
8. Generate `upgrade.sh`, `verify-installed.sh`, `rollback.sh`, Chinese deployment instructions, target metadata, internal `SHA256SUMS`, the `.tar.gz`, and its sidecar `.sha256`.
9. Run the project validator:

   ```bash
   python3 .codex/skills/build-centos-offline-upgrade/scripts/validate_upgrade_package.py \
     --package-dir deploy-packages/<package-directory> \
     --repo . \
     --base-revision <full-baseline-revision> \
     --target-revision <full-target-revision> \
     --image <target-image-tag> \
     --archive deploy-packages/<archive>.tar.gz
   ```

10. Extract the archive into a fresh temporary directory and rerun its internal checksums. Report the commit, image ID/architecture/revision, archive path, SHA-256, preservation guarantees, and exact CentOS commands.

## Repairing a failed package

First classify the failure as build, switch/startup, embedded smoke, preservation, or health verification. Reproduce the exact failing assertion or command against the built target image. If the image and feature are healthy but package verification is wrong, repair only package scripts and documentation, issue a uniquely named replacement archive, and explain that the prior automatic rollback preserved the old deployment.

Never tell the user to bypass a failing assertion until it has been reproduced and shown to be an invalid verifier rather than a product failure.
