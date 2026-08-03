# Playwright Test Platform

[简体中文](./README.zh-CN.md)

Playwright Test Platform is a self-hosted workspace for managing test
requirements, Markdown test plans, Playwright scripts, execution records, and
AI-assisted generation and repair. It keeps test assets in project workspaces
with local Git history and stores platform metadata in MySQL.

> **Public Beta**
>
> This release supports a trusted, single-tenant team on one Linux Docker
> deployment. It is not a hardened public SaaS, a hostile multi-tenant system,
> or a security sandbox. Keep the UI behind a TLS reverse proxy and an
> organization access boundary.

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

## Security boundary

The supported deployment assumes:

- one organization and one trust domain;
- trusted operators, platform users, repositories, generated code, and setup
  scripts;
- one application instance on Linux Docker;
- an authorized, isolated, recoverable non-production target system;
- restricted network access to MySQL, OpenCode/model services, and the target;
- TLS and an external access-control layer in front of the platform.

Playwright tests and setup scripts execute code. They share the application
container's operating-system boundary and are **not** isolated from the
platform as hostile code. Default-off switches and container restrictions
reduce accidental exposure; they do not turn the container into a sandbox.
Do not give untrusted users execution permissions, mount a Docker socket, or
connect the platform to production credentials or production data.

Read the complete [security model](./docs/security-model.md) and
[deployment guide](./docs/deployment.md).

## 15-minute Docker quickstart

The first image build can take longer on a slow connection because it downloads
the pinned Playwright base image and package versions resolved from the checked-in
lock files.

Public Beta releases are source-only. The project does not currently distribute
a prebuilt container image; the Compose quickstart builds one locally from
`deploy/Dockerfile`. Anyone redistributing that image must first produce and
review a complete SBOM and license bundle for the final artifact.

### Prerequisites

- a Linux host;
- Docker Engine with the Compose v2 plugin;
- Git;
- Python 3 for the local secret-generation snippet below;
- enough disk space for the image, browser, MySQL volume, workspaces, and test
  artifacts.

### 1. Prepare configuration

From the repository root:

```bash
cp deploy/config.example.json config.json
cp .env.example .env
chmod 600 config.json .env
```

Generate independent quickstart secrets without putting their values in command
arguments. The snippet also copies the database and OpenCode service passwords
into the local `config.json`, matching the application's file-based password
fields:

```bash
python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe
import json

path = Path(".env")
config_path = Path("config.json")
secret_names = {
    "PLATFORM_SESSION_SECRET",
    "PLATFORM_ADMIN_PASSWORD",
    "PLATFORM_DB_PASSWORD",
    "OPENCODE_SERVER_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
}
result = []
secrets = {}
for line in path.read_text(encoding="utf-8").splitlines():
    name, separator, value = line.partition("=")
    if separator and name in secret_names:
        value = value or token_urlsafe(36)
        secrets[name] = value
        line = f"{name}={value}"
    result.append(line)
path.write_text("\n".join(result) + "\n", encoding="utf-8")

config = json.loads(config_path.read_text(encoding="utf-8"))
config["opencode_password"] = secrets["OPENCODE_SERVER_PASSWORD"]
config["platform_database"]["password"] = secrets["PLATFORM_DB_PASSWORD"]
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
```

The generated administrator username is `admin`; its password is the local
`PLATFORM_ADMIN_PASSWORD` value in `.env`. Both `.env` and `config.json` now
contain secrets. Do not commit, paste, or upload either file.

### 2. Validate and start

```bash
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml up --build --detach
docker compose --env-file .env -f deploy/compose.yaml ps
```

When all three services are healthy, open
[http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in as `admin`.

The Docker example deliberately uses `https://test.example.invalid` as the
target. Replace it in `config.json` with an authorized test-system URL before
running or generating browser automation, then set that project's `username`
and `password` fields to a dedicated test account.

Useful commands:

```bash
docker compose --env-file .env -f deploy/compose.yaml logs --follow platform
docker compose --env-file .env -f deploy/compose.yaml down
```

`down` keeps named volumes. Removing volumes also removes MySQL data and
workspace state; do that only as an intentional reset.

### 3. Opt in to trusted test execution

Test execution is disabled by default. After reviewing the target, repository,
scripts, network, mounts, and artifact policy, set:

```dotenv
PLATFORM_ALLOW_TEST_EXECUTION=true
```

Then recreate the platform service:

```bash
docker compose --env-file .env -f deploy/compose.yaml up --detach --force-recreate platform
```

The published Compose stack keeps
`PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION=false`. Setup scripts are arbitrary shell
code and must remain disabled unless a trusted operator supplies a deliberately
hardened custom deployment. Enabling either switch is an acceptance of code
execution, not a sandbox guarantee.

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
| `PLATFORM_ALLOW_TEST_EXECUTION` | Explicit opt-in for trusted Playwright code |
| `PLATFORM_ALLOW_HOST_SCRIPT_EXECUTION` | Explicit opt-in for trusted setup shell code; disabled by the published Compose stack |

The platform may include target-system usernames and passwords in planning or
generation prompts and generated seed scripts. Use only disposable,
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
- encrypt backups and keep MySQL plus workspace Git as one recovery point;
- review every diagnostic bundle before sharing it;
- never attach raw reports, trace, video, configuration, or database dumps to a
  public Issue.

## Upgrading from an internal or legacy installation

1. Back up MySQL and every project workspace, including `.git`, as one
   recoverable snapshot.
2. Rotate any secret that has ever appeared in `config.json`, scripts, logs,
   documentation, archives, or Git history.
3. Restore `opencode_password`, `platform_database.password`, project
   `target_system.username` / `target_system.password`, and setup-script
   `environment_overrides` if upgrading from an environment-reference build.
4. An intermediate environment-reference release may already have overwritten
   project credentials or scrubbed setup environment values in MySQL. A code
   rollback cannot reconstruct them; recover from a pre-upgrade backup or enter
   the values again.
5. Remove database-baseline command fields. Use file mode or a reviewed setup
   workflow; do not restore deleted batch helpers.
6. Validate the upgrade on an isolated copy before running the new image.

Deleting a secret from the latest file does not remove it from Git history,
database backups, exported projects, or prior images.

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
deploy/                 Docker image, Compose, entrypoint, and health checks
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
