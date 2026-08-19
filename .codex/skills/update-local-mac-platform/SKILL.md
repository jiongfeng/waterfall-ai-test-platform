---
name: update-local-mac-platform
description: Safely update an existing Waterfall AI test-platform deployment on local macOS from the repository's committed Git HEAD. Use when asked to deploy, redeploy, refresh, or verify this project on the local Mac while preserving the configured OpenCode provider, volumes, and running OpenCode container; prefer the incremental image path and fall back to an explicit full image build only when runtime dependencies changed.
---

# Update Local Mac Platform

Deploy only committed source to the existing local Docker Desktop stack. Preserve OpenCode state and verify both backend health and the affected UI behavior.

## Safety Contract

- Work only on macOS and only with the existing Compose project resolved by `deploy/platform-compose`.
- Never run `docker compose` directly, delete volumes, invoke either OpenCode repair command, or recreate `mysql` or `opencode`.
- Never change or copy the host OpenCode provider/auth files. Do not print credentials, token values, config contents, or `.env` values.
- Build from `git archive HEAD`; report that uncommitted and untracked files are excluded.
- Keep the previous platform image available until deployment and verification succeed.
- Stop if deployment topology or safety-wrapper files changed since the running image revision. Review those changes manually before adapting this Skill.

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

5. If preflight reports deployment-topology changes, do not bypass the check. Inspect `deploy/compose.yaml`, `deploy/platform-compose`, and the relevant deployment documentation, then update this Skill or ask for direction.
6. After deployment, use `$browser:control-in-app-browser` for UI acceptance. Derive priorities from the commits between the former image revision and `HEAD`; use the repository's SauceDemo requirement when test data is needed. Creating a disposable project and writing test data is allowed. For a new project, configure its project settings, generate the seed, run it, and verify the run history/results.
7. Report the deployed Git revision, build mode, platform image/container, unchanged OpenCode container ID and provider manifest, health verification, UI cases, and any remaining risk.

## Verification Only

Check an existing stack without rebuilding or recreating anything:

```bash
python3 .codex/skills/update-local-mac-platform/scripts/update_local_mac.py --verify-only
```

Treat any provider-manifest change, OpenCode container replacement, unhealthy service, failed platform verification, or image-revision mismatch as a failed deployment. The script automatically restores the previous platform image if a newly recreated platform fails verification.
