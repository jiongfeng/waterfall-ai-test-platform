#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly APP_HOME="${APP_HOME:-/opt/playwright-platform/app}"
readonly TEMPLATE_DIR="${APP_HOME}/project-template"
readonly PROJECTS_ROOT="${PLATFORM_PROJECTS_ROOT:-/data/playwright-projects}"
readonly WORKSPACES_ROOT="${PLATFORM_WORKSPACES_ROOT:-/data/playwright-workspaces}"
readonly DEFAULT_PROJECT_KEY="${PLATFORM_DEFAULT_PROJECT_KEY:-default}"

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
    [[ -f "${PLATFORM_CONFIG_PATH}" ]] \
        || fail "Configuration file not found: ${PLATFORM_CONFIG_PATH}"

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
