#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly MINISIGN_VERSION="0.12"
readonly MINISIGN_LINUX_SHA256="9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73"
readonly MINISIGN_URL="https://github.com/jedisct1/minisign/releases/download/${MINISIGN_VERSION}/minisign-${MINISIGN_VERSION}-linux.tar.gz"

fail() {
    printf 'install-minisign: %s\n' "$*" >&2
    exit 1
}

[[ $# -eq 1 ]] || fail "usage: $0 ABSOLUTE_DESTINATION"
destination="$1"
[[ "${destination}" == /* ]] || fail "destination must be absolute"
parent="$(dirname -- "${destination}")"
[[ -d "${parent}" && ! -L "${parent}" ]] || fail "destination parent must be an existing directory"
[[ ! -e "${destination}" && ! -L "${destination}" ]] || fail "destination already exists"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"

case "$(uname -m)" in
    x86_64|amd64) architecture="x86_64" ;;
    aarch64|arm64) architecture="aarch64" ;;
    *) fail "unsupported architecture: $(uname -m)" ;;
esac

temporary="$(mktemp -d "${TMPDIR:-/tmp}/minisign-install.XXXXXXXX")"
cleanup() {
    rm -rf -- "${temporary}"
}
trap cleanup EXIT
archive="${temporary}/minisign.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 --output "${archive}" "${MINISIGN_URL}"
printf '%s  %s\n' "${MINISIGN_LINUX_SHA256}" "${archive}" | sha256sum --check --strict
tar --extract --gzip --file "${archive}" --directory "${temporary}"
binary="${temporary}/minisign-linux/${architecture}/minisign"
[[ -f "${binary}" && ! -L "${binary}" ]] || fail "download did not contain the expected binary"
install -m 0755 "${binary}" "${destination}"
"${destination}" -v
