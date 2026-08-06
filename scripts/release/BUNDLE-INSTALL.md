# Verified bundle installation

This file is trustworthy only after the release-tag verifier has authenticated
the bundle and extracted it with `verify-bundle.sh --extract-to`. Do not extract
an unverified download merely to read these instructions. The complete
pre-extraction procedure is in `docs/deployment.md` at the exact revision named
by `SOURCE-SNAPSHOT.md`.

An offline bundle only removes registry access during installation. It does not
make origin authentication self-contained: verify the original bundle on a
trusted connected staging host, record its outer SHA-256 through an independent
trusted channel, and transfer the exact bytes over controlled media. On an
isolated target, compare that outer digest before using the fixed tag verifier's
local verification and extraction mode. The bundle's own checksums cannot
establish who published it.

The bundle supports Linux/amd64 only. Run Docker through one consistent,
non-root operations account. The final install target must not exist, its parent
must already exist and be writable by that account, and the Compose project
must not own any existing resources.

From this verified extracted directory:

```bash
./bin/preflight
./bin/install \
  --target /absolute/writable/parent/playwright-platform-vX.Y.Z \
  --compose-project playwright-platform-vx-y-z
cd /absolute/writable/parent/playwright-platform-vX.Y.Z
```

The installer creates private `config.json` and `.env` files from the public
examples. Generate five independent local secrets and copy only the two service
passwords into their matching JSON fields:

```bash
chmod 600 .env config.json
python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe
import json

env_path = Path(".env")
config_path = Path("config.json")
secret_names = {
    "PLATFORM_SESSION_SECRET",
    "PLATFORM_ADMIN_PASSWORD",
    "PLATFORM_DB_PASSWORD",
    "OPENCODE_SERVER_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
}
values = {}
lines = []
for line in env_path.read_text(encoding="utf-8").splitlines():
    name, separator, value = line.partition("=")
    if separator and name in secret_names:
        value = value or token_urlsafe(36)
        values[name] = value
        line = f"{name}={value}"
    lines.append(line)
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

config = json.loads(config_path.read_text(encoding="utf-8"))
config["opencode_password"] = values["OPENCODE_SERVER_PASSWORD"]
config["platform_database"]["password"] = values["PLATFORM_DB_PASSWORD"]
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
chmod 600 .env config.json
```

Review target URLs and keep test execution disabled until the target, scripts,
network, mounts, and artifact policy have been approved. The generated
`PLATFORM_COOKIE_SECURE=false` is only for loopback HTTP evaluation; set it to
`true` behind the required TLS reverse proxy and validate the proxy's Host and
forwarded-header policy. Then validate and start only through the release
wrapper:

```bash
./bin/platform-compose validate-config
./bin/platform-compose up --detach
./bin/verify
```

The wrapper forces `--no-build --pull never` during startup. Never call
`docker compose` directly, replace `.env.images`, reuse legacy volumes, or put
`.env`, `config.json`, OAuth/provider state, logs, traces, reports, or backups in
a public issue. A recoverable encrypted backup must keep `mysql_data`,
`platform_projects`, `platform_workspaces` and every workspace Git history, all
four OpenCode XDG volumes, and `config.json`/`.env`/Release metadata as one
consistent recovery point.

`./bin/verify` proves the platform containers, approved image identities, and
local OpenCode health. It does not prove a model Provider is authenticated;
before enabling Agent workflows, run a separate minimal inference smoke with an
organization-approved Provider and dedicated non-production credentials.
