# Playwright Test Platform

[简体中文](./README.zh-CN.md)

Playwright Test Platform is a self-hosted workspace for managing test
requirements, Markdown test plans, Playwright scripts, execution records, and
AI-assisted generation and repair. It keeps test assets in project workspaces
with local Git history and stores platform metadata in MySQL.

> **Public Beta candidate — no public install artifact exists yet**
>
> The current source candidate can be evaluated by a trusted, single-tenant
> team on one Linux/amd64 Docker deployment. It is not a published release, a
> hardened public SaaS, a hostile multi-tenant system, or a security sandbox.
> Keep the UI behind a TLS reverse proxy and an organization access boundary.

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
- one application instance on Linux/amd64 Docker;
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

## Docker quickstart (source checkout)

A Public Beta release is installable only when that specific GitHub Release
attaches a verified Linux/amd64 bundle and its Minisign/checksum material.
An online bundle references the platform image by immutable GHCR digest. A
complete offline bundle is attached only after redistribution of every included
third-party image has been approved; if that asset is absent, the release is not
a complete offline distribution. The source tree and NO-GO templates are not an
installable bundle. Build and installation verification must consume the same
platform image digest.

A source checkout can still build the image locally from `deploy/Dockerfile`.
The first build can take longer on a slow connection. The Playwright base image
and direct application dependencies are pinned, and the project template uses
its checked-in npm lock; some build-tool and transitive resolution still comes
from upstream registries. A formal candidate is therefore identified by the
one built image digest and its SBOM, not by a claim that a later source rebuild
is byte-for-byte identical. Do not describe a locally rebuilt image as the
release image unless its digest matches the release metadata.

The commands below are for a source checkout. A Release bundle must follow the
deployment guide's copyable [download, signature, outer-checksum, and safe
`--extract-to` procedure](./docs/deployment.md#release-下载验证与安全解包). The
verifier must come from the same trusted tag and copy directly from its private
verified bytes into a destination that does not exist. The extracted bundle
runs `./bin/preflight`, then `./bin/install --target ABSOLUTE_PATH`; the
installed copy is managed only through `./bin/platform-compose`. Never
substitute an older internal package for a missing Release asset.

### Prerequisites

- a Linux/amd64 host (the pinned source images and Release runtime are
  single-platform amd64 artifacts);
- Docker Engine with the Compose v2 plugin;
- Git;
- Python 3 for the local secret-generation snippet below;
- enough disk space for the image, browser, MySQL volume, workspaces, and test
  artifacts.

Use one consistent operator identity for every command. It must already have
access to the Docker daemon through an approved rootless setup, a dedicated
Docker-group account, or a consistently applied `sudo` policy. Docker daemon
access is effectively host-root authority. Do not mix privileged and
unprivileged runs: that can leave `.runtime`, configuration, and generated
files owned by different users.

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
./deploy/platform-compose preflight-install
./deploy/platform-compose validate-config
./deploy/platform-compose up --build --detach
./deploy/platform-compose ps
```

`up --build` is the source-only path. The wrapper pulls only the missing MySQL
image at its pinned linux/amd64 digest, builds the platform image once, and then
starts both platform services with `--no-build --pull never`. The base runtime
Compose file cannot build from source. The wrapper also derives one project
identity from the mode-`0600` `.env` file and rejects a conflicting ambient
`COMPOSE_PROJECT_NAME`.

The first command is the fresh-install guard for a source checkout. It uses the
same secured project identity as every later wrapper command, requires an empty
runtime target, and rejects existing containers, volumes, or networks owned by
that project. Run it before the first `up`; normal management of an already
installed current release uses `platform-compose` without rerunning the
fresh-install check.

When all three services are healthy, open
[http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in as `admin`.
You can then run `./deploy/platform-compose verify` to check health, the
read-only container contract, config readability, and all four OpenCode XDG
volumes plus their required data/state directories.

Container health proves only that the platform, MySQL, and OpenCode service
processes answer their local checks. It does **not** prove that a model provider
is configured, authenticated, or able to complete inference. The UI and
non-Agent features can be ready while Agent features are not. Before enabling
Agent workflows, configure an organization-approved provider and complete one
minimal authenticated inference smoke test without real test data or secrets.

The Docker example deliberately uses `https://test.example.invalid` as the
target. Replace it in `config.json` with an authorized test-system URL before
running or generating browser automation, then set that project's `username`
and `password` fields to a dedicated test account.

`platform-compose` is the only supported entry point for this stack. It
validates the host `config.json` at mode `0600`, then stages canonical content
at `deploy/.runtime/secrets/platform-config.json`. The runtime directories are
mode `0700` and the staged file is mode `0444`; the private parent directory is
what prevents other host users from reading it. This ignored runtime copy still
contains secrets: do not edit or commit it, include it in a public/unencrypted
backup, or share it as a diagnostic artifact. After editing the source config,
keep it at `0600` and run `./deploy/platform-compose apply-config`.

Direct `docker compose` commands are unsupported because they bypass config
validation and staging.

Useful commands:

```bash
./deploy/platform-compose logs --follow platform
./deploy/platform-compose down
```

`down` keeps named volumes, and the wrapper rejects `-v` / `--volumes` because
volume removal destroys MySQL data, workspaces, and service state.

If OpenCode refuses to start because an existing config, data, cache, or state
volume has the wrong owner or mode, do not delete or silently reuse the volume.
First stop OpenCode and create one consistent, encrypted, access-controlled
snapshot of all four OpenCode volumes. Record each volume identity and a
SHA-256 of a non-secret sentinel before and after repair; snapshots can contain
OAuth/provider configuration and logs and must never enter this repository or
a public Issue. Then run:

```bash
./deploy/platform-compose repair-opencode-volumes
./deploy/platform-compose verify
```

The explicit repair command resolves only volumes labelled for the secured
Compose project, obtains the runtime UID/GID from the local platform image,
repairs ownership and controlled directory modes, creates only missing controlled
runtime directories, probes every volume as the
non-root runtime user, and recreates OpenCode only after all probes pass. It
stops OpenCode only after image, capability, project, and four-volume prechecks
succeed. From that point onward, a repair or health failure leaves OpenCode
stopped; a precheck failure does not change its prior state. Investigate or
restore the four-volume snapshot. The legacy `repair-state` command remains
state-volume-only.

### 3. Opt in to trusted test execution

Test execution is disabled by default. After reviewing the target, repository,
scripts, network, mounts, and artifact policy, set:

```dotenv
PLATFORM_ALLOW_TEST_EXECUTION=true
```

Then recreate the platform service:

```bash
./deploy/platform-compose up --detach --force-recreate platform
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
- encrypt backups and keep `mysql_data`, `platform_projects` (including file
  baselines), `platform_workspaces` and every workspace Git history, all four
  OpenCode XDG volumes, and `config.json`/`.env`/Release metadata as one recovery
  point;
- review every diagnostic bundle before sharing it;
- never attach raw reports, trace, video, configuration, or database dumps to a
  public Issue.

## Fresh-install-only upgrade boundary

Any eventual initial Public Beta will support **fresh installation only**. It does not
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
  --target /srv/playwright-platform-next \
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
