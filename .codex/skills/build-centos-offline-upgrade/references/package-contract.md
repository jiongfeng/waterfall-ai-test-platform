# Offline upgrade package contract

## Required layout

```text
<package>/
├── README-zh-CN.md
├── SHA256SUMS
├── SOURCE-SNAPSHOT.md
├── TARGET-METADATA.env
├── VERIFICATION.md
├── upgrade.sh
├── verify-installed.sh
├── rollback.sh
├── finalize-checksums.sh
├── prepare-runtime.sh
└── build/
    ├── Dockerfile
    ├── RUNTIME-SHA256SUMS
    ├── runtime/
    └── wheelhouse/
        └── WHEELHOUSE-SHA256SUMS
```

## Runtime and dependency rules

- Runtime roots are `app.py`, `requirements.txt`, `test_plan_viewer/`, `static/`, `templates/`, and `project-template/`. Detect whether they live at repository root (public layout) or below `test-plan-viewer/` (legacy layout) at the target commit.
- `build/runtime/` must exactly equal those tracked files at the target commit.
- Package every direct and transitive wheel. Linux-native wheels must support CPython 3.10 and x86_64; pure Python wheels are acceptable.
- Pip must use `--no-index --find-links`; Docker must build with `DOCKER_BUILDKIT=0`, `--pull=false`, and `--network=none`. This prevents an ARM Mac BuildKit instance from trying to resolve a local-only AMD64 baseline image from a registry.
- Rebuild rather than reusing the old image when a Python dependency changes.

## Upgrade transaction

Perform these phases in order:

1. Validate server architecture, Compose project, port, current image revision, services, mounts, and absence of running setup restores.
2. Build and inspect the new image before asking for confirmation.
3. Back up `.env`, Compose, config, secrets, resolved Compose, setup state, mount state, and platform MySQL.
4. Recheck running setup restores, stop only platform, atomically replace only `PLATFORM_IMAGE`, and recreate only platform.
5. Wait for `/login`, then compare OpenCode container ID, provider/auth hashes, setup state, secret hashes, Compose hash, DM8 mount source/destination/read-only flag, image ID, and runtime files.
6. Execute product smoke assertions and OpenCode health without model inference or DM8 execution.
7. On any error after switching starts, restore the backed-up `.env` and recreate the old platform.

## Verification design

- Validate observable contracts: installed versions, registered routes, compiled runtime, feature module presence, and rendered-template sources.
- Execute the same embedded smoke Python used by `upgrade.sh` inside the target image before archiving.
- For Jinja `{% include %}`, inspect the included partial directly. Raw `index.html` does not contain the partial's rendered IDs.
- Treat initial connection refusals during the bounded readiness loop as retries, not the root failure.
- Give each assertion an explanatory message or split it into a named check so field failures identify the broken contract.

## Forbidden package behavior

- `docker-compose down`, volume deletion, broad Docker cleanup, or service-wide recreation.
- Replacing the deployment Compose/config/data/secrets from package copies.
- Restarting OpenCode just to apply a platform update.
- Printing credentials, `.env`, OpenCode auth contents, or secret file contents.
- `DELETE`, `TRUNCATE`, or schema changes not explicitly required by the current request.
- Relying only on `bash -n`, Python compilation, or a successful Docker build; these do not prove post-install assertions are correct.

## Naming

Use an unambiguous name containing date, target short revision, baseline short revision, port, offline, and architecture. For example:

```text
playwright-platform-docker-upgrade-YYYYMMDD-<target>-from-<base>-5001-offline-linux-amd64.tar.gz
```

Never silently replace an already delivered archive. Add `fix1`, `fix2`, and so on.
