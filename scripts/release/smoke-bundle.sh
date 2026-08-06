#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR

fail() {
    printf 'release smoke: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: smoke-bundle.sh --type online|offline --bundle PATH --github-repository OWNER/REPO
  --source-ref refs/tags/TAG --source-digest FULL_SHA --port PORT --compose-project NAME
  --release-manifest PATH --release-signature PATH --minisign-public-key PATH
  [--container-http-probe]
  [--allow-preverified-local --expected-bundle-sha256 SHA256]
EOF
}

bundle_type=""
bundle=""
github_repository=""
source_ref=""
source_digest=""
release_manifest=""
release_signature=""
minisign_public_key=""
port=""
compose_project=""
container_http_probe="false"
allow_preverified_local="false"
expected_bundle_sha256=""
while (($#)); do
    case "$1" in
        --type) bundle_type="${2:-}"; shift 2 ;;
        --bundle) bundle="${2:-}"; shift 2 ;;
        --github-repository) github_repository="${2:-}"; shift 2 ;;
        --source-ref) source_ref="${2:-}"; shift 2 ;;
        --source-digest) source_digest="${2:-}"; shift 2 ;;
        --release-manifest) release_manifest="${2:-}"; shift 2 ;;
        --release-signature) release_signature="${2:-}"; shift 2 ;;
        --minisign-public-key) minisign_public_key="${2:-}"; shift 2 ;;
        --port) port="${2:-}"; shift 2 ;;
        --compose-project) compose_project="${2:-}"; shift 2 ;;
        --container-http-probe) container_http_probe="true"; shift ;;
        --allow-preverified-local) allow_preverified_local="true"; shift ;;
        --expected-bundle-sha256) expected_bundle_sha256="${2:-}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; fail "unknown argument: $1" ;;
    esac
done

[[ "${bundle_type}" == "online" || "${bundle_type}" == "offline" ]] \
    || fail "--type must be online or offline"
[[ -f "${bundle}" && ! -L "${bundle}" ]] || fail "--bundle must be a regular file"
[[ "${github_repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
    || fail "--github-repository must be OWNER/REPO"
[[ "${source_ref}" =~ ^refs/tags/v[0-9A-Za-z.-]+$ ]] || fail "--source-ref must be a release tag"
[[ "${source_digest}" =~ ^[0-9a-f]{40}$ ]] || fail "--source-digest must be a complete Git SHA"
if [[ ! "${port}" =~ ^[0-9]+$ ]] || ((port < 1024 || port > 65535)); then
    fail "--port is invalid"
fi
[[ "${compose_project}" =~ ^[a-z0-9][a-z0-9_-]{2,62}$ ]] || fail "--compose-project is invalid"
if [[ "${allow_preverified_local}" == "true" ]]; then
    [[ "${bundle_type}" == "offline" ]] \
        || fail "--allow-preverified-local is restricted to isolated offline smoke"
    [[ "${expected_bundle_sha256}" =~ ^[0-9a-f]{64}$ ]] \
        || fail "--expected-bundle-sha256 is required for preverified local smoke"
    [[ "$(sha256sum "${bundle}" | awk '{print $1}')" == "${expected_bundle_sha256}" ]] \
        || fail "preverified offline bundle changed before isolated smoke"
else
    [[ -z "${expected_bundle_sha256}" ]] \
        || fail "--expected-bundle-sha256 requires --allow-preverified-local"
    for trust_file in "${release_manifest}" "${release_signature}" "${minisign_public_key}"; do
        [[ -f "${trust_file}" && ! -L "${trust_file}" ]] \
            || fail "signed Release trust input is missing: ${trust_file}"
    done
fi

work_root="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/release-smoke.XXXXXXXX")"
extracted="${work_root}/verified-bundle"
install_root="${work_root}/install"
log_path="${work_root}/compose.log"
cleanup() {
    if [[ -x "${install_root}/bin/platform-compose" ]]; then
        "${install_root}/bin/platform-compose" logs --no-color > "${log_path}" 2>&1 || true
        "${install_root}/bin/platform-compose" down >/dev/null 2>&1 || true
    fi
    rm -rf -- "${work_root}"
}
trap cleanup EXIT

if [[ "${allow_preverified_local}" == "true" ]]; then
    verify_args=(--allow-unsigned-local --extract-to "${extracted}")
else
    verify_args=(
        --github-repository "${github_repository}"
        --source-ref "${source_ref}"
        --source-digest "${source_digest}"
        --release-manifest "${release_manifest}"
        --release-signature "${release_signature}"
        --minisign-public-key "${minisign_public_key}"
        --extract-to "${extracted}"
    )
fi
if [[ "${bundle_type}" == "offline" ]]; then
    verify_args+=(--verify-image-archives)
fi
bash "${SCRIPT_DIR}/verify-bundle.sh" "${verify_args[@]}" "${bundle}"
actual_type="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundleType"])' \
    "${extracted}/RELEASE-METADATA.json")"
[[ "${actual_type}" == "${bundle_type}" ]] || fail "bundle type mismatch"

"${extracted}/bin/install" --target "${install_root}" --compose-project "${compose_project}"

admin_password="$(openssl rand -hex 24)"
export SMOKE_ADMIN_PASSWORD="${admin_password}"
export SMOKE_PLATFORM_PORT="${port}"
python3 - "${install_root}/.env" "${install_root}/config.json" <<'PY'
import json
import os
import secrets
import sys

env_path, config_path = sys.argv[1:]
values = {
    "PLATFORM_SESSION_SECRET": secrets.token_urlsafe(48),
    "PLATFORM_ADMIN_PASSWORD": os.environ["SMOKE_ADMIN_PASSWORD"],
    "PLATFORM_DB_PASSWORD": secrets.token_urlsafe(36),
    "OPENCODE_SERVER_PASSWORD": secrets.token_urlsafe(36),
    "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(36),
    "PLATFORM_PORT": os.environ["SMOKE_PLATFORM_PORT"],
}
lines = []
for line in open(env_path, encoding="utf-8").read().splitlines():
    key, separator, _ = line.partition("=")
    if separator and key in values:
        line = f"{key}={values.pop(key)}"
    lines.append(line)
lines.extend(f"{key}={value}" for key, value in values.items())
open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
config = json.load(open(config_path, encoding="utf-8"))
environment = dict(
    line.split("=", 1) for line in lines if "=" in line and not line.startswith("#")
)
config["opencode_password"] = environment["OPENCODE_SERVER_PASSWORD"]
config["platform_database"]["password"] = environment["PLATFORM_DB_PASSWORD"]
open(config_path, "w", encoding="utf-8").write(json.dumps(config, indent=2) + "\n")
PY
unset SMOKE_ADMIN_PASSWORD SMOKE_PLATFORM_PORT
chmod 0600 "${install_root}/.env" "${install_root}/config.json"

wait_for_stack_health() {
    local ready="false"
    for _ in {1..60}; do
        if "${install_root}/bin/verify" >/dev/null 2>&1; then
            ready="true"
            break
        fi
        sleep 5
    done
    [[ "${ready}" == "true" ]] || fail "services did not become healthy"
}

"${install_root}/bin/platform-compose" up --detach --no-build --pull never
wait_for_stack_health

platform_container="$("${install_root}/bin/platform-compose" ps --quiet platform)"
if [[ "${container_http_probe}" == "true" ]]; then
    docker exec --interactive --env "SMOKE_ADMIN_PASSWORD=${admin_password}" \
        "${platform_container}" python - <<'PY'
import http.cookiejar
import json, sys
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:5000"
cookies = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

def login(password):
    request = urllib.request.Request(
        base + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": password}).encode(),
        headers={"Content-Type": "application/json", "Origin": base},
    )
    try:
        return opener.open(request).status
    except urllib.error.HTTPError as error:
        return error.code

assert login("wrong") == 401
assert login(os.environ["SMOKE_ADMIN_PASSWORD"]) == 200
assert opener.open(base + "/api/projects").status == 200
PY
else
    wrong_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --header 'Content-Type: application/json' \
        --header "Origin: http://127.0.0.1:${port}" \
        --data-binary '{"username":"admin","password":"wrong"}' \
        "http://127.0.0.1:${port}/api/auth/login")"
    [[ "${wrong_status}" == "401" ]] || fail "wrong-password login did not return 401"
    correct_status="$(python3 - "${admin_password}" <<'PY' \
        | curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
            --cookie-jar "${work_root}/cookies" \
            --header 'Content-Type: application/json' \
            --header "Origin: http://127.0.0.1:${port}" \
            --data-binary @- \
            "http://127.0.0.1:${port}/api/auth/login"
import json, sys
print(json.dumps({"username": "admin", "password": sys.argv[1]}))
PY
    )"
    [[ "${correct_status}" == "200" ]] || fail "correct login did not return 200"
    projects_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --cookie "${work_root}/cookies" "http://127.0.0.1:${port}/api/projects")"
    [[ "${projects_status}" == "200" ]] || fail "authenticated projects API did not return 200"
fi

docker exec --interactive "${platform_container}" node - <<'NODE'
const { chromium } = require('/opt/playwright-platform/app/project-template/node_modules/@playwright/test');
(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage();
  await page.setContent('<h1>release smoke</h1>');
  if (await page.textContent('h1') !== 'release smoke') process.exitCode = 1;
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
NODE

opencode_container="$("${install_root}/bin/platform-compose" ps --quiet opencode)"
docker exec "${opencode_container}" /bin/sh -ceu '
    for path in \
        "${XDG_CONFIG_HOME}/opencode/release-smoke-sentinel" \
        "${XDG_DATA_HOME}/opencode/release-smoke-sentinel" \
        "${XDG_CACHE_HOME}/opencode/release-smoke-sentinel" \
        "${XDG_STATE_HOME}/opencode/release-smoke-sentinel"
    do
        printf sentinel > "${path}"
    done
'
verify_opencode_sentinels() {
    docker exec "${opencode_container}" /bin/sh -ceu '
        for path in \
            "${XDG_CONFIG_HOME}/opencode/release-smoke-sentinel" \
            "${XDG_DATA_HOME}/opencode/release-smoke-sentinel" \
            "${XDG_CACHE_HOME}/opencode/release-smoke-sentinel" \
            "${XDG_STATE_HOME}/opencode/release-smoke-sentinel"
        do
            test "$(cat "${path}")" = sentinel
        done
    '
}
docker restart "${opencode_container}" >/dev/null
sentinels_ready="false"
for _ in {1..30}; do
    if verify_opencode_sentinels 2>/dev/null; then
        sentinels_ready="true"
        break
    fi
    sleep 2
done
[[ "${sentinels_ready}" == "true" ]] \
    || fail "OpenCode XDG sentinels did not survive container restart"
"${install_root}/bin/platform-compose" up \
    --detach --no-build --pull never --no-deps --force-recreate opencode
opencode_container="$("${install_root}/bin/platform-compose" ps --quiet opencode)"
wait_for_stack_health
verify_opencode_sentinels \
    || fail "OpenCode XDG sentinels did not survive force-recreate"

old_platform_container="${platform_container}"
python3 - "${install_root}/config.json" <<'PY'
import json, sys
path = sys.argv[1]
config = json.load(open(path, encoding="utf-8"))
config["script_execution_timeout_seconds"] = 7199
open(path, "w", encoding="utf-8").write(json.dumps(config, indent=2) + "\n")
PY
chmod 0600 "${install_root}/config.json"
"${install_root}/bin/platform-compose" apply-config
platform_container="$("${install_root}/bin/platform-compose" ps --quiet platform)"
[[ "${platform_container}" != "${old_platform_container}" ]] \
    || fail "apply-config did not recreate the platform container"
wait_for_stack_health
docker exec "${platform_container}" python -c \
    'import json,os; assert json.load(open(os.environ["PLATFORM_CONFIG_PATH"]))["script_execution_timeout_seconds"] == 7199'
"${install_root}/bin/verify"

printf '%s release bundle clean-host smoke passed.\n' "${bundle_type}"
