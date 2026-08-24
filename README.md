<p align="right"><a href="./README.zh-CN.md">简体中文</a></p>

<h1 align="center">Waterfall AI</h1>
<p align="center"><strong>Agent-driven test automation platform</strong></p>

<p align="center">
Turn test requirements into reviewable plans, runnable Playwright tests,<br>
and evidence-rich results in one self-hosted workspace.<br>
Plan, generate, review, execute, and repair with an AI agent.
</p>

<p align="center">
  <a href="https://github.com/jiongfeng/waterfall-ai-test-platform/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jiongfeng/waterfall-ai-test-platform/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/jiongfeng/waterfall-ai-test-platform/releases"><img alt="Release" src="https://img.shields.io/github/v/release/jiongfeng/waterfall-ai-test-platform?include_prereleases"></a>
  <a href="./LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <a href="./docs/support-matrix.md"><img alt="Linux amd64" src="https://img.shields.io/badge/platform-Linux%2Famd64-informational"></a>
</p>

<p align="center">
  <a href="#agent-driven-workflow"><strong>Explore the workflow</strong></a> ·
  <a href="./docs/deployment.md"><strong>Install the signed Beta</strong></a> ·
  <a href="./docs/security-model.md"><strong>Understand the security boundary</strong></a>
</p>

<p align="center">
  <a href="./docs/assets/waterfall-ai-introduction-en.mp4">
    <img src="./docs/assets/waterfall-ai-demo.gif" alt="Waterfall AI turns a SauceDemo shopping requirement into a test plan, a Playwright script, and a verified cart result" width="960">
  </a>
</p>

<p align="center">
  <video src="./docs/assets/waterfall-ai-introduction-en.mp4" controls width="960"></video>
</p>

<p align="center">
  <a href="./docs/assets/waterfall-ai-introduction-en.mp4"><strong>▶ Watch the English introduction video (MP4)</strong></a>
</p>

## Agent-driven workflow

| Plan | Generate | Run and repair |
| --- | --- | --- |
| Turn requirements into reviewable Markdown plans | Generate Playwright tests with Agent assistance and local Git history | Execute tests, collect evidence, repair failures, and verify the result |

> **Public Beta:** The current signed prerelease supports trusted,
> single-tenant Linux/amd64 Docker deployments only. Fresh installation only;
> not a hostile-code sandbox.

Waterfall AI is an independent open-source project built with Playwright. It is
not affiliated with, sponsored by, or endorsed by Microsoft or the Playwright
project.

## What it includes

- project, requirement, test-plan, script, and test-suite management;
- local Git revisions for workspace test assets;
- OpenCode-assisted planning, generation, review, repair, and Agent workflows;
- Playwright execution with logs, reports, screenshots, video, and trace;
- project-scoped setup scripts and execution records;
- authentication, roles, menu permissions, and method-aware API authorization;
- MySQL metadata, project import/export, diagnostics, and recovery-oriented
  records.

The interface and workflows are still evolving. See the
[roadmap](./ROADMAP.md), [changelog](./CHANGELOG.md), and
[support matrix](./docs/support-matrix.md) before adopting the Beta.
Code boundaries and extension rules are maintained in
[ARCHITECTURE.md](./ARCHITECTURE.md).

## Docker quickstart (source checkout)

This source-checkout quickstart is for isolated evaluation on Linux/amd64. It
requires Docker Engine with Compose v2, Git, Python 3, and enough disk space for
the images, browser, database, workspaces, and test artifacts. Use one
consistent account for every command.

For signed Release verification, production-style deployment, backup, repair,
and upgrade constraints, follow the complete [deployment
guide](./docs/deployment.md).

### 1. Prepare configuration

From the repository root:

```bash
cp deploy/config.example.json config.json
cp .env.example .env
chmod 600 config.json .env
./deploy/platform-compose init-config
```

`init-config` fills blank quickstart secrets without printing them and copies
the database and OpenCode passwords into `config.json`. Both files now contain
secrets: do not commit, paste, upload, or share them.

### 2. Validate and start

```bash
./deploy/platform-compose preflight-install
./deploy/platform-compose validate-config
./deploy/platform-compose up --build --detach
./deploy/platform-compose ps
```

The first build may take several minutes. This quickstart supports a fresh
installation only; `preflight-install` rejects an existing runtime or Compose
project.

When the services are healthy, open
[http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in as `admin`.
The password is the local `PLATFORM_ADMIN_PASSWORD` value in `.env`.

The Docker example deliberately uses `https://test.example.invalid` as the
target. Replace it in `config.json` with an authorized test-system URL before
running browser automation. Configure an approved model provider and complete
one credential-free inference smoke test before relying on Agent features.

Useful commands:

```bash
./deploy/platform-compose verify
./deploy/platform-compose logs --follow platform
./deploy/platform-compose down
```

> **Trusted execution default:** The bundled demo enables setup-script and
> Playwright execution. These capabilities run code and do not provide a
> hostile-code sandbox. Use only trusted users, repositories, and isolated test
> targets. For a public, shared, or otherwise untrusted deployment, set the
> following values in `.env` and recreate the platform service:

```dotenv
PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION=false
PLATFORM_ALLOW_TEST_EXECUTION=false
```

```bash
./deploy/platform-compose up --detach --force-recreate platform
```

## Configuration and secrets

For compatibility, `config.json` stores the OpenCode password, platform database
password, and each target system's login username and password. Keep the file
outside source control, restrict it to the service account, and include it only
in encrypted, access-controlled backups.

| Variable | Purpose |
| --- | --- |
| `PLATFORM_SESSION_SECRET` | Overrides the `auth.session_secret` file value |
| `PLATFORM_ADMIN_PASSWORD` | Overrides the `auth.initial_admin_password` file value |
| `PLATFORM_DB_PASSWORD` | Compose MySQL account password; copy the same value to `platform_database.password` |
| `MYSQL_ROOT_PASSWORD` | Compose MySQL bootstrap password |
| `OPENCODE_SERVER_PASSWORD` | Compose OpenCode service password; copy the same value to `opencode_password` |
| `PLATFORM_COOKIE_SECURE` | Set to `true` behind HTTPS |
| `PLATFORM_ALLOW_TEST_EXECUTION` | Allows generated Playwright code; enabled by the bundled demo defaults |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | Allows trusted setup shell code; enabled by the bundled demo defaults |

The Generate Seed menu offers a visit-only mode and a login mode. A visit Seed
uses a fixed script that only opens `base_url`; it creates no model job and
does not write the login URL, username, or password into the Seed. A login
Seed keeps the model-generated login flow and provides the configured login
details to the model. Both modes replace the same
`tests/seed/seed.spec.ts` file.

The platform may still include target-system usernames and passwords in
planning or script-generation prompts and login Seeds. Use only disposable,
least-privilege credentials for an isolated non-production target. Treat the
model provider, workspace Git history, logs, and execution artifacts as being
inside that credential's exposure boundary.

The Compose quickstart fixes the MySQL database and application account to the
values in `deploy/config.example.json`. To customize either identifier, update
both the `mysql` service in `deploy/compose.yaml` and the
`platform_database.database` / `platform_database.user` fields in the copied
root `config.json`; changing only one side prevents the platform from
connecting.

See [configuration.md](./docs/configuration.md) for the full schema and
precedence rules.

## Security boundary

The supported deployment assumes:

- one organization and one trust domain;
- trusted operators, platform users, repositories, generated code, and setup
  scripts;
- one application instance on Linux/amd64 Docker;
- an authorized, isolated, recoverable non-production target system;
- restricted network access to MySQL, OpenCode/model services, and the target;
- TLS and an external access-control layer in front of the platform.

Playwright tests and setup scripts execute code. They share the application
container's operating-system boundary and are **not** isolated from the
platform as hostile code. Execution switches and container restrictions can
reduce accidental exposure; they do not turn the container into a sandbox.
Do not give untrusted users execution permissions, mount a Docker socket, or
connect the platform to production credentials or production data.

Read the complete [security model](./docs/security-model.md) and
[deployment guide](./docs/deployment.md).

## Database baseline: file mode only

The legacy command baseline mode and the old `backup.bat` / `restore.bat`
helpers are removed. `database_baseline` accepts only `mode: "file"` and copies
a file-backed test database:

- if `baseline_path` does not exist, the current `database_path` is copied to
  create it;
- otherwise the baseline is copied back to the runtime database before the
  relevant test flow;
- a lock directory serializes the operation.

Example:

```json
{
  "database_baseline": {
    "enabled": true,
    "mode": "file",
    "database_path": "/data/playwright-projects/default/data/test.db",
    "baseline_path": "/data/playwright-projects/default/.baseline/test.db",
    "lock_path": "/data/playwright-projects/default/.baseline/restore.lock",
    "timeout_seconds": 300
  }
}
```

Do not point this feature at production data. Server databases such as MySQL
are not command-baseline targets; use a separately reviewed setup workflow and
least-privilege test credentials if reset automation is required.

## Sensitive artifacts

Playwright reports, screenshots, video, trace, browser downloads, workspace Git
history, logs, and diagnostic bundles can contain cookies, form values,
personal information, page content, or internal URLs. There is no reliable
automatic sanitizer for every text field or binary artifact.

- restrict artifact access to trusted users;
- define retention and deletion limits;
- encrypt backups and keep `mysql_data`, `platform_projects` (including file
  baselines), `platform_workspaces` and every workspace Git history, all four
  OpenCode XDG volumes, and `config.json`/`.env`/Release metadata as one recovery
  point;
- review every diagnostic bundle before sharing it;
- never attach raw reports, trace, video, configuration, or database dumps to a
  public Issue.

## Brand and release compatibility

The project was renamed from `playwright-test-platform` to
`waterfall-ai-test-platform`. GitHub redirects the old repository URL. The
immutable `v0.1.0-beta.3` release intentionally keeps its original
`playwright-test-platform-*` asset names and
`ghcr.io/jiongfeng/playwright-test-platform` image reference; releases created
after the rename use the Waterfall AI names. Runtime compatibility identifiers
such as the `playwright_platform` database, Python package paths, container
paths, and existing session-cookie names remain unchanged.

## Fresh-install-only upgrade boundary

The current Public Beta supports **fresh installation only**. It does not
support an in-place upgrade from an internal package, a legacy installation, a
source checkout, or an installation whose release metadata is missing or
unknown. The old internal incremental packages are retired and are not release
assets or a public compatibility contract.

- install a Release bundle only into a destination that does not exist (confirm
  and explicitly remove an empty directory first), with new database and
  application volumes;
- do not point the candidate or future Release at a legacy database, workspace, Compose project, or
  volume;
- the read-only `deploy/preflight-install.py` check and
  `deploy/upgrade-matrix.json` deny every unlisted source; the current matrix has
  no supported in-place path;
- do not bypass a denial by deleting only a version marker or by relabeling an
  old image;
- preserve the old environment as one encrypted recovery point containing
  `mysql_data`, `platform_projects`, `platform_workspaces` and every workspace
  Git history, all four OpenCode XDG volumes, and `config.json`/`.env`/Release
  metadata. The project does not currently provide a public legacy export/import tool.

The release-bundle installer runs this read-only check before writing its
destination. To audit the decision manually from an unpacked release bundle:

```bash
python3 deploy/preflight-install.py \
  --target /srv/waterfall-ai-next \
  --release-metadata ./RELEASE-METADATA.json
```

Exit status `10` is a policy denial. The check also denies a fresh install when
the Compose project already owns containers, volumes, or networks. Do not work
around a denial; use a genuinely separate destination and resource set.

Rotate any secret that appeared in a legacy configuration, script, log,
archive, database backup, image, or Git history before manually re-entering
approved settings in the clean environment. Deleting a secret from the latest
file does not remove historical copies.

## Local development and demo workspace

The credential-free [`examples/demo-workspace`](./examples/demo-workspace)
contains one in-memory Playwright test and no `node_modules`. The root
`config.example.json` points to it for local development. Docker uses the
separate `deploy/config.example.json` and a persistent workspace volume.

Backend tests:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

Demo test:

```bash
cd examples/demo-workspace
npm ci
npx playwright install chromium
npm test
```

Do not use real credentials or production targets in development tests.

## Repository map

```text
app.py                  compatibility composition root
test_plan_viewer/       domain, web, repository, and infrastructure modules
static/                 browser code and styles
templates/              Jinja templates
project-template/       generated workspace template
examples/demo-workspace credential-free local example
deploy/                 Docker, install preflight, upgrade policy, and health checks
docs/                   architecture, configuration, deployment, and security
tests/                  Python and JavaScript regression tests
```

## Contributing, support, and security

- Read [CONTRIBUTING.md](./CONTRIBUTING.md) and the
  [Code of Conduct](./CODE_OF_CONDUCT.md) before opening a pull request.
- Use [SUPPORT.md](./SUPPORT.md) for reproducible, sanitized usage reports.
- Report vulnerabilities privately as described in
  [SECURITY.md](./SECURITY.md). Do not disclose credentials or unpatched
  vulnerability details in public Issues or pull requests.
- Project decisions follow [GOVERNANCE.md](./GOVERNANCE.md).

Source code, including the checked-in project template and demo workspace, is
licensed under the [Apache License 2.0](./LICENSE). When the platform copies the
template to create a user workspace, it marks that generated workspace
`private` and `UNLICENSED` so its owner can choose an appropriate license.
