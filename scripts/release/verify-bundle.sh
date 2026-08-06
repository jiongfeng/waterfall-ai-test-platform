#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR

fail() {
    printf 'verify-bundle: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s (--release-manifest PATH --release-signature PATH --minisign-public-key PATH --github-repository OWNER/REPO --source-ref REF --source-digest SHA | --allow-unsigned-local) [--verify-image-archives] [--extract-to NEW_PATH] BUNDLE.tar.zst\n' "$0"
}

verify_images="false"
github_repository=""
source_ref=""
source_digest=""
release_manifest=""
release_signature=""
minisign_public_key=""
allow_unsigned_local="false"
extract_to=""
while (($# > 1)); do
    case "$1" in
        --verify-image-archives)
            verify_images="true"
            shift
            ;;
        --github-repository)
            (($# >= 2)) || fail "--github-repository requires OWNER/REPO"
            github_repository="$2"
            shift 2
            ;;
        --source-ref)
            (($# >= 2)) || fail "--source-ref requires a Git ref"
            source_ref="$2"
            shift 2
            ;;
        --source-digest)
            (($# >= 2)) || fail "--source-digest requires a Git SHA"
            source_digest="$2"
            shift 2
            ;;
        --release-manifest)
            (($# >= 2)) || fail "--release-manifest requires a path"
            release_manifest="$2"
            shift 2
            ;;
        --release-signature)
            (($# >= 2)) || fail "--release-signature requires a path"
            release_signature="$2"
            shift 2
            ;;
        --minisign-public-key)
            (($# >= 2)) || fail "--minisign-public-key requires a path"
            minisign_public_key="$2"
            shift 2
            ;;
        --allow-unsigned-local)
            allow_unsigned_local="true"
            shift
            ;;
        --extract-to)
            (($# >= 2)) || fail "--extract-to requires a path"
            extract_to="$2"
            shift 2
            ;;
        *) break ;;
    esac
done
[[ $# -eq 1 ]] || {
    usage >&2
    exit 2
}
archive="$1"
original_archive="${archive}"

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
command -v zstd >/dev/null 2>&1 || fail "zstd is required"
[[ -f "${archive}" && ! -L "${archive}" ]] || fail "bundle is not a regular file: ${archive}"
archive_sha_before="$(sha256sum "${archive}" | awk '{print $1}')"
if [[ "${allow_unsigned_local}" == "true" ]]; then
    [[ -z "${github_repository}${source_ref}${source_digest}${release_manifest}${release_signature}${minisign_public_key}" ]] \
        || fail "--allow-unsigned-local cannot be combined with signature identity"
else
    [[ "${github_repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
        || fail "--github-repository OWNER/REPO is required"
    [[ "${source_ref}" =~ ^refs/tags/v[0-9A-Za-z.-]+$ ]] \
        || fail "--source-ref must be a full release tag ref"
    [[ "${source_digest}" =~ ^[0-9a-f]{40}$ ]] \
        || fail "--source-digest must be a complete lowercase Git SHA"
    for trust_file in "${release_manifest}" "${release_signature}" "${minisign_public_key}"; do
        [[ -f "${trust_file}" && ! -L "${trust_file}" ]] \
            || fail "signature trust input is not a regular file: ${trust_file}"
    done
    command -v minisign >/dev/null 2>&1 || fail "minisign is required for Release verification"
    minisign -Vm "${release_manifest}" -x "${release_signature}" \
        -p "${minisign_public_key}" -H -q \
        || fail "Minisign Release manifest verification failed"
    expected_public_key_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["signing"]["publicKeySha256"])' "${release_manifest}")"
    [[ "$(sha256sum "${minisign_public_key}" | awk '{print $1}')" == "${expected_public_key_sha}" ]] \
        || fail "Minisign public key does not match the signed manifest"
    python3 "${SCRIPT_DIR}/release_manifest.py" verify-asset \
        --manifest "${release_manifest}" \
        --asset-root "$(dirname -- "${release_manifest}")" \
        --asset "${archive}" \
        --expected-tag "${source_ref#refs/tags/}" \
        --expected-revision "${source_digest}" \
        --expected-source-url "https://github.com/${github_repository}" \
        || fail "bundle is not bound by the signed Release manifest"
fi
[[ -f "${archive}" && ! -L "${archive}" ]] || fail "bundle changed type during signature verification"
[[ "$(sha256sum "${archive}" | awk '{print $1}')" == "${archive_sha_before}" ]] \
    || fail "bundle changed during signature verification"

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/verify-playwright-release.XXXXXXXX")"
chmod 0700 "${temporary_dir}"
cleanup() {
    rm -rf -- "${temporary_dir}"
}
trap cleanup EXIT
trusted_archive="${temporary_dir}/signed-bundle.tar.zst"
copied_sha="$(python3 - "${archive}" "${trusted_archive}" <<'PY'
import hashlib
import os
import stat
import sys

source_fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode):
        raise SystemExit("source bundle is not a regular file")
    target_fd = os.open(
        sys.argv[2],
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    digest = hashlib.sha256()
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
    finally:
        os.close(target_fd)
finally:
    os.close(source_fd)
print(digest.hexdigest())
PY
)" || fail "could not copy signed bundle into private verification storage"
[[ "${copied_sha}" == "${archive_sha_before}" ]] \
    || fail "private verification copy does not match signed bytes"
archive="${trusted_archive}"
zstd --test --quiet "${archive}" || fail "bundle has invalid zstd framing"
bundle_directory="$(python3 "${SCRIPT_DIR}/inspect-archive.py" "${archive}")" \
    || fail "archive member validation failed"

zstd --decompress --stdout "${archive}" \
    | tar --extract --no-same-owner --no-same-permissions --file - --directory "${temporary_dir}"
[[ "$(sha256sum "${archive}" | awk '{print $1}')" == "${archive_sha_before}" ]] \
    || fail "bundle changed during verification"
bundle_root="${temporary_dir}/${bundle_directory}"
[[ -d "${bundle_root}" && ! -L "${bundle_root}" ]] || fail "bundle root is missing"
if find "${bundle_root}" -type l -print -quit | grep -q .; then
    fail "bundle must not contain symbolic links"
fi
if find "${bundle_root}" ! -type d ! -type f -print -quit | grep -q .; then
    fail "bundle contains a special filesystem object"
fi

for required in \
    RELEASE-METADATA.json \
    INSTALL.md \
    SHA256SUMS \
    .env.images \
    deploy/compose.yaml \
    deploy/platform-compose \
    bin/install \
    bin/preflight \
    bin/platform-compose \
    bin/set_compose_project.py \
    bin/verify \
    provenance/candidate/RELEASE-CANDIDATE.json \
    provenance/approval/RELEASE-APPROVAL.json \
    scripts/release/docker_image_config_digest.py \
    scripts/release/license_payload.py \
    scripts/release/validate-metadata.py; do
    [[ -f "${bundle_root}/${required}" ]] || fail "required bundle member is missing: ${required}"
done
[[ ! -e "${bundle_root}/deploy/compose.build.yaml" ]] \
    || fail "release bundle must not contain the source-build override"

(
    cd -- "${bundle_root}"
    sha256sum --check --strict SHA256SUMS
)
metadata_args=(
    "${bundle_root}/RELEASE-METADATA.json"
    --bundle-root "${bundle_root}"
)
if [[ "${allow_unsigned_local}" != "true" ]]; then
    metadata_args+=(
        --expected-tag "${source_ref#refs/tags/}"
        --expected-revision "${source_digest}"
        --expected-source-url "https://github.com/${github_repository}"
    )
fi
python3 "${SCRIPT_DIR}/validate-metadata.py" "${metadata_args[@]}"

version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "${bundle_root}/RELEASE-METADATA.json")"
[[ "${bundle_directory}" == "waterfall-ai-test-platform-${version}-linux-amd64" ]] \
    || fail "bundle directory version does not match metadata"

if [[ "${verify_images}" == "true" ]]; then
    command -v docker >/dev/null 2>&1 || fail "Docker is required for image archive verification"
    bundle_type="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundleType"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    [[ "${bundle_type}" == "offline" ]] \
        || fail "--verify-image-archives requires an offline bundle"
    platform_image="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"]["platform"]["runtimeReference"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    mysql_image="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"]["mysql"]["runtimeReference"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    platform_config_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"]["platform"]["configDigest"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    mysql_config_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"]["mysql"]["configDigest"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' \
        "${bundle_root}/RELEASE-METADATA.json")"
    zstd --decompress --stdout "${bundle_root}/images/platform-linux-amd64.tar.zst" | docker load
    zstd --decompress --stdout "${bundle_root}/images/mysql-linux-amd64.tar.zst" | docker load
    docker image inspect "${platform_image}" >/dev/null \
        || fail "loaded platform archive does not provide its declared offline tag"
    docker image inspect "${mysql_image}" >/dev/null \
        || fail "loaded MySQL archive does not provide its declared offline tag"
    [[ "$(python3 "${bundle_root}/scripts/release/docker_image_config_digest.py" "${platform_image}")" \
        == "${platform_config_digest}" ]] \
        || fail "loaded platform archive config digest mismatch"
    [[ "$(python3 "${bundle_root}/scripts/release/docker_image_config_digest.py" "${mysql_image}")" \
        == "${mysql_config_digest}" ]] \
        || fail "loaded MySQL archive config digest mismatch"
    [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${platform_image}")" == "linux/amd64" ]] \
        || fail "loaded platform archive is not linux/amd64"
    [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${mysql_image}")" == "linux/amd64" ]] \
        || fail "loaded MySQL archive is not linux/amd64"
    actual_revision="$(docker image inspect \
        --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "${platform_image}")"
    [[ "${actual_revision}" == "${revision}" ]] \
        || fail "loaded platform image revision does not match metadata"
fi

if [[ -n "${extract_to}" ]]; then
    [[ "${extract_to}" == /* ]] || fail "--extract-to must be an absolute path"
    [[ ! -e "${extract_to}" && ! -L "${extract_to}" ]] \
        || fail "--extract-to destination must not exist"
    extract_parent="$(dirname -- "${extract_to}")"
    [[ -d "${extract_parent}" && ! -L "${extract_parent}" ]] \
        || fail "--extract-to parent must be an existing non-symlink directory"
    cp -a -- "${bundle_root}" "${extract_to}"
fi

printf 'Verified %s\n' "${original_archive}"
