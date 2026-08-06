#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly APP_HOME="${APP_HOME:-/opt/playwright-platform/app}"
readonly TEMPLATE_DIR="${APP_HOME}/project-template"
readonly PROJECTS_ROOT="${PLATFORM_PROJECTS_ROOT:-/data/playwright-projects}"
readonly WORKSPACES_ROOT="${PLATFORM_WORKSPACES_ROOT:-/data/playwright-workspaces}"
readonly DEFAULT_PROJECT_KEY="${PLATFORM_DEFAULT_PROJECT_KEY:-default}"
readonly RUNTIME_HOME="${HOME:-/home/pwuser}"
readonly OPENCODE_CACHE_ROOT="${XDG_CACHE_HOME:-${RUNTIME_HOME}/.cache}/opencode"
readonly OPENCODE_CONFIG_ROOT="${XDG_CONFIG_HOME:-${RUNTIME_HOME}/.config}/opencode"
readonly OPENCODE_DATA_ROOT="${XDG_DATA_HOME:-${RUNTIME_HOME}/.local/share}/opencode"
readonly OPENCODE_STATE_ROOT="${XDG_STATE_HOME:-${RUNTIME_HOME}/.local/state}"
readonly OPENCODE_REPAIR_COMMAND="platform-compose repair-opencode-volumes"

fail() {
    printf 'platform-entrypoint: %s\n' "$*" >&2
    exit 1
}

require_unsigned_integer() {
    local name="$1"
    local value="$2"
    [[ "${value}" =~ ^[0-9]+$ ]] \
        || fail "${name} must be an unsigned integer."
}

validate_platform_config() {
    local config_path="$1"

    [[ -f "${config_path}" && ! -L "${config_path}" ]] \
        || fail "Configuration is not a regular file: ${config_path}"
    [[ -r "${config_path}" ]] \
        || fail "Configuration is not readable by runtime UID $(id -u): ${config_path}"
    [[ ! -w "${config_path}" ]] \
        || fail "Configuration must be mounted read-only: ${config_path}"

    if ! PYTHONPATH="${APP_HOME}" python - "${config_path}" <<'PY'
import sys

from test_plan_viewer.configuration import load_config


try:
    parsed = load_config(sys.argv[1])
except Exception as exc:  # Fail closed without echoing configuration values.
    print(
        f"platform-entrypoint: configuration validation raised {type(exc).__name__}.",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

if parsed.get("error"):
    print(
        f"platform-entrypoint: configuration validation failed: {parsed['error']}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
    then
        fail "Refusing to start with invalid configuration."
    fi
}

validate_opencode_directory() {
    local label="$1"
    local directory="$2"
    local expected_uid="$3"
    local expected_gid="$4"
    local actual_identity
    local actual_mode
    local probe_identity
    local probe_mode
    local write_probe

    [[ -d "${directory}" && ! -L "${directory}" ]] \
        || fail "OpenCode ${label} directory is missing or not a regular directory at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."

    actual_identity="$(stat --format='%u:%g' "${directory}")" \
        || fail "Cannot inspect OpenCode ${label} directory at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
    [[ "${actual_identity}" == "${expected_uid}:${expected_gid}" ]] \
        || fail "OpenCode ${label} directory ${directory} is owned by ${actual_identity}, expected ${expected_uid}:${expected_gid}; run ${OPENCODE_REPAIR_COMMAND}."
    actual_mode="$(stat --format='%a' "${directory}")" \
        || fail "Cannot inspect OpenCode ${label} directory mode at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
    [[ "${actual_mode}" == "700" ]] \
        || fail "OpenCode ${label} directory ${directory} has mode ${actual_mode}, expected 0700; run ${OPENCODE_REPAIR_COMMAND}."
    [[ -r "${directory}" && -w "${directory}" && -x "${directory}" ]] \
        || fail "OpenCode ${label} directory is not readable, writable, and searchable by runtime identity ${expected_uid}:${expected_gid}: ${directory}; run ${OPENCODE_REPAIR_COMMAND}."

    write_probe="$(umask 077; mktemp "${directory}/.platform-write-probe.XXXXXX")" \
        || fail "OpenCode ${label} restricted write probe failed at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
    probe_identity="$(stat --format='%u:%g' "${write_probe}")" \
        || {
            rm -f -- "${write_probe}" || true
            fail "Cannot inspect OpenCode ${label} write probe at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
        }
    probe_mode="$(stat --format='%a' "${write_probe}")" \
        || {
            rm -f -- "${write_probe}" || true
            fail "Cannot inspect OpenCode ${label} write probe mode at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
        }
    if [[ "${probe_identity}" != "${expected_uid}:${expected_gid}" || "${probe_mode}" != "600" ]]; then
        rm -f -- "${write_probe}" || true
        fail "OpenCode ${label} write probe did not preserve runtime identity and mode 0600 at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
    fi
    printf 'runtime-write-probe\n' >"${write_probe}" \
        || {
            rm -f -- "${write_probe}" || true
            fail "OpenCode ${label} write probe could not write at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
        }
    rm -f -- "${write_probe}" \
        || fail "OpenCode ${label} write probe cleanup failed at ${directory}; run ${OPENCODE_REPAIR_COMMAND}."
}

validate_opencode_volumes() {
    local expected_uid
    local expected_gid

    expected_uid="$(id -u)"
    expected_gid="$(id -g)"

    validate_opencode_directory "config volume" "${OPENCODE_CONFIG_ROOT}" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "data volume" "${OPENCODE_DATA_ROOT}" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "data log" "${OPENCODE_DATA_ROOT}/log" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "data repos" "${OPENCODE_DATA_ROOT}/repos" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "cache volume" "${OPENCODE_CACHE_ROOT}" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "state volume" "${OPENCODE_STATE_ROOT}" "${expected_uid}" "${expected_gid}"
    validate_opencode_directory "state data" "${OPENCODE_STATE_ROOT}/opencode" "${expected_uid}" "${expected_gid}"
}

initialize_runtime_directories() {
    [[ "${DEFAULT_PROJECT_KEY}" =~ ^[A-Za-z0-9_.-]+$ ]] \
        || fail "PLATFORM_DEFAULT_PROJECT_KEY contains invalid characters."

    mkdir -p \
        "${PROJECTS_ROOT}" \
        "${WORKSPACES_ROOT}" \
        /tmp/gunicorn

    [[ -w "${PROJECTS_ROOT}" ]] \
        || fail "${PROJECTS_ROOT} is not writable."
    [[ -w "${WORKSPACES_ROOT}" ]] \
        || fail "${WORKSPACES_ROOT} is not writable."
}

initialize_default_project() {
    local project_dir="${PROJECTS_ROOT}/${DEFAULT_PROJECT_KEY}"

    initialize_runtime_directories
    if [[ ! -f "${project_dir}/package.json" ]]; then
        mkdir -p "${project_dir}"
        cp -a "${TEMPLATE_DIR}/." "${project_dir}/"
        sed -i \
            -e "s/{{PACKAGE_NAME}}/${DEFAULT_PROJECT_KEY}/g" \
            -e "s/{{PROJECT_NAME}}/Default Project/g" \
            -e "s/{{TESTS_DIR}}/tests/g" \
            "${project_dir}/package.json" \
            "${project_dir}/package-lock.json" \
            "${project_dir}/playwright.config.ts"
        PYTHONPATH="${APP_HOME}" python -c \
            'import sys; from test_plan_viewer.projects.workspace import mark_generated_workspace_unlicensed; mark_generated_workspace_unlicensed(sys.argv[1])' \
            "${project_dir}"
    fi

    if [[ ! -d "${project_dir}/node_modules/@playwright/test" ]]; then
        rm -rf "${project_dir}/node_modules"
        cp -a \
            "${TEMPLATE_DIR}/node_modules" \
            "${project_dir}/node_modules"
    fi

    if [[ ! -d "${project_dir}/.opencode/node_modules/@opencode-ai/plugin" ]]; then
        rm -rf "${project_dir}/.opencode/node_modules"
        mkdir -p "${project_dir}/.opencode"
        cp -a \
            "${TEMPLATE_DIR}/.opencode/node_modules" \
            "${project_dir}/.opencode/node_modules"
    fi

    mkdir -p "${project_dir}/specs" "${project_dir}/tests"
}

run_platform() {
    local port="${PLATFORM_PORT:-5000}"
    local workers="${GUNICORN_WORKERS:-1}"
    local threads="${GUNICORN_THREADS:-8}"
    local timeout="${GUNICORN_TIMEOUT:-0}"

    : "${PLATFORM_CONFIG_PATH:?PLATFORM_CONFIG_PATH must reference a mounted config file.}"
    : "${PLATFORM_SESSION_SECRET:?PLATFORM_SESSION_SECRET must be supplied at runtime.}"
    : "${PLATFORM_ADMIN_PASSWORD:?PLATFORM_ADMIN_PASSWORD must be supplied at runtime.}"
    validate_platform_config "${PLATFORM_CONFIG_PATH}"

    require_unsigned_integer PLATFORM_PORT "${port}"
    require_unsigned_integer GUNICORN_WORKERS "${workers}"
    require_unsigned_integer GUNICORN_THREADS "${threads}"
    require_unsigned_integer GUNICORN_TIMEOUT "${timeout}"
    (( workers > 0 )) || fail "GUNICORN_WORKERS must be greater than zero."
    (( threads > 0 )) || fail "GUNICORN_THREADS must be greater than zero."

    initialize_default_project
    exec gunicorn \
        --chdir "${APP_HOME}" \
        --bind "0.0.0.0:${port}" \
        --workers "${workers}" \
        --threads "${threads}" \
        --timeout "${timeout}" \
        --worker-tmp-dir /tmp/gunicorn \
        --access-logfile - \
        --error-logfile - \
        --capture-output \
        app:app
}

run_opencode() {
    local port="${OPENCODE_PORT:-4096}"
    local hostname="${OPENCODE_HOSTNAME:-0.0.0.0}"

    : "${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD must be supplied at runtime.}"
    require_unsigned_integer OPENCODE_PORT "${port}"
    initialize_runtime_directories
    validate_opencode_volumes

    exec opencode serve \
        --hostname "${hostname}" \
        --port "${port}"
}

case "${1:-platform}" in
    initialize)
        initialize_default_project
        ;;
    platform)
        run_platform
        ;;
    opencode)
        run_opencode
        ;;
    shell)
        shift
        exec /bin/bash "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
