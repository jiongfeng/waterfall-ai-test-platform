---
name: update-local-mac-platform
description: Safely update an existing Waterfall AI test-platform deployment on local macOS from the repository's committed Git HEAD. Use when asked to deploy, redeploy, refresh, or verify this project on the local Mac while preserving the isolated native ARM64 OpenCode provider/auth state and host bind-mounted project data; prefer the incremental image path and fall back to an explicit full image build only when runtime dependencies changed.
---

# Update Local Mac Platform

Deploy only committed source to the existing local Docker Desktop platform/MySQL stack. Preserve the isolated native OpenCode service and host project data, then verify both backend health and the affected UI behavior.

## Safety Contract

- Work only on macOS and only with the existing Compose project resolved by `deploy/platform-compose`.
- The supported topology is: containerized MySQL and platform; native ARM64 OpenCode on `127.0.0.1:4096`; platform access through `http://host.docker.internal:4096`; project/workspace bind mounts whose container targets equal their host absolute paths.
- Never run `docker compose` directly, delete volumes, invoke the legacy OpenCode repair commands, recreate MySQL, or switch back to the x86_64 OpenCode container during an ordinary update.
- Never change the user's global `~/.config/opencode` or `~/.local/share/opencode`. Preserve the deployment-isolated provider/auth roots under `deploy/.runtime/native-opencode` and do not print credentials, token values, config contents, or `.env` values.
- Preserve `deploy/.runtime/data/playwright-projects` and `deploy/.runtime/data/playwright-workspaces`. The former Docker named volumes remain rollback backups and must not be deleted.
- Build from `git archive HEAD`; report that uncommitted and untracked files are excluded.
- Keep the previous platform image available until deployment and verification succeed.
- If deployment topology or safety-wrapper files changed since the running image revision, review them and require `--full-build`; never bypass the review with an incremental image.

## Workflow

1. Inspect `git status --short`, `git log -4 --oneline`, and the current stack. State which committed revision will be deployed and which local changes will be excluded.
2. Run the read-only preflight:

   ```bash
   python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py --dry-run
   ```

3. If preflight selects `incremental` or `dependency-incremental`, run:

   ```bash
   python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py
   ```

   `dependency-incremental` is allowed only when `requirements.txt` contains simple exact pins and the change only adds packages without removing or changing existing versions.

4. If preflight reports other dependency-critical changes, explain that the full build can download packages and takes longer. Validate that path first:

   ```bash
   python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py --dry-run --full-build
   ```

   Then run the real full build only with the user's deployment authorization:

   ```bash
   python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py --full-build
   ```

5. If preflight reports deployment-topology changes, inspect `deploy/compose.yaml`, `deploy/platform-compose`, `deploy/native-opencode.py`, and this Skill. After confirming the native/bind-mount contract remains intact, use the reviewed `--full-build` path.
6. After deployment, use `$browser:control-in-app-browser` for UI acceptance. Derive priorities from the commits between the former image revision and `HEAD`; use the repository's SauceDemo requirement when test data is needed. Creating a disposable project and writing test data is allowed. For a new project, configure its project settings, generate the seed, run it, and verify the run history/results.
7. Report the deployed Git revision, build mode, platform image/container, native OpenCode version/architecture and unchanged provider manifest, bind roots, health verification, UI cases, and any remaining risk.

## One-time migration

For an existing local stack that still uses the bundled OpenCode container and named project volumes, run only this reviewed wrapper command:

```bash
deploy/platform-compose migrate-native-opencode
```

It stops platform/OpenCode writers, copies all six named volumes into private host runtime directories, rebases persisted database paths, starts the isolated native LaunchAgent, recreates only the platform with same-path bind mounts, and verifies the result. It retains all old named volumes. If verification fails, it reverses the database path mapping and restores the legacy topology.

Do not run this migration during an ordinary code-only update, and do not copy provider/auth data into the user's global OpenCode directories.

## Verification Only

Check an existing stack without rebuilding or recreating anything:

```bash
python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py --verify-only
```

Treat any provider-manifest change, non-ARM64 OpenCode binary, unhealthy native service, bind-root mismatch, failed platform verification, or image-revision mismatch as a failed deployment. The script automatically restores the previous platform image if a newly recreated platform fails verification; it does not alter the native provider/auth state.
