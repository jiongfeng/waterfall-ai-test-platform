#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TEST_DIR
RELEASE_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
readonly RELEASE_DIR

fail() {
    printf 'runtime wrapper test: %s\n' "$*" >&2
    exit 1
}

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/test-runtime-wrapper.XXXXXXXX")"
cleanup() {
    rm -rf -- "${temporary_dir}"
}
trap cleanup EXIT

mkdir -p "${temporary_dir}/bin" "${temporary_dir}/deploy"
cp "${RELEASE_DIR}/runtime/platform-compose" "${temporary_dir}/bin/platform-compose"
cp "${RELEASE_DIR}/runtime/.release-common" "${temporary_dir}/bin/.release-common"
chmod 0755 "${temporary_dir}/bin/platform-compose"
printf '%s\n' \
    'PLATFORM_IMAGE=ghcr.io/example/platform@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    'MYSQL_IMAGE=docker.io/library/mysql@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
    > "${temporary_dir}/.env.images"
cat > "${temporary_dir}/deploy/platform-compose" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$@" > "${CAPTURE_PATH:?}"
printf 'PLATFORM_IMAGE=%s\nMYSQL_IMAGE=%s\n' \
    "${PLATFORM_IMAGE:?}" "${MYSQL_IMAGE:?}" > "${CAPTURE_ENV_PATH:?}"
SH
chmod 0755 "${temporary_dir}/deploy/platform-compose"

export CAPTURE_PATH="${temporary_dir}/captured"
export CAPTURE_ENV_PATH="${temporary_dir}/captured-env"
"${temporary_dir}/bin/platform-compose" up --detach
printf '%s\n' up --no-build --pull never --detach > "${temporary_dir}/expected"
cmp "${temporary_dir}/expected" "${CAPTURE_PATH}" \
    || fail "plain up did not force --no-build --pull never"

if "${temporary_dir}/bin/platform-compose" up --pull always >/dev/null 2>&1; then
    fail "--pull always unexpectedly passed"
fi
if "${temporary_dir}/bin/platform-compose" up --build >/dev/null 2>&1; then
    fail "--build unexpectedly passed"
fi

printf '%s\n' \
    'PLATFORM_IMAGE=waterfall-ai-test-platform.local/platform:sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    'MYSQL_IMAGE=waterfall-ai-test-platform.local/mysql:sha256-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
    > "${temporary_dir}/.env.images"
"${temporary_dir}/bin/platform-compose" ps
cmp "${temporary_dir}/.env.images" "${CAPTURE_ENV_PATH}" \
    || fail "offline archive references were not exported unchanged"

printf 'runtime wrapper tests passed\n'
