#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C
umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
DEFAULT_SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly DEFAULT_SOURCE_ROOT

fail() {
    printf 'build-bundle: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: build-bundle.sh --type online|offline --version VERSION --tag TAG
  --revision FULL_SHA --source-url GITHUB_URL --source-date-epoch EPOCH
  --platform-image NAME@sha256:DIGEST --mysql-image NAME@sha256:DIGEST
  --sbom-dir PATH --license-dir PATH --output-dir PATH
  --candidate-manifest PATH --approval-root PATH [--source-root PATH]
  [--third-party-manifest PATH]
  [--platform-archive PATH --mysql-archive PATH]
EOF
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

canonical_path() {
    python3 - "$1" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
}

copy_file() {
    local source="$1"
    local destination="$2"
    [[ -f "${source}" && ! -L "${source}" ]] || fail "required regular file is missing: ${source}"
    mkdir -p -- "$(dirname -- "${destination}")"
    install -m 0644 "${source}" "${destination}"
}

bundle_type=""
version=""
tag=""
revision=""
source_url=""
source_date_epoch=""
platform_image=""
mysql_image=""
sbom_dir=""
license_dir=""
output_dir=""
source_root="${DEFAULT_SOURCE_ROOT}"
third_party_manifest=""
platform_archive=""
mysql_archive=""
candidate_manifest=""
approval_root=""

while (($#)); do
    case "$1" in
        --type|--version|--tag|--revision|--source-url|--source-date-epoch|--platform-image|--mysql-image|--sbom-dir|--license-dir|--output-dir|--source-root|--third-party-manifest|--platform-archive|--mysql-archive|--candidate-manifest|--approval-root)
            (($# >= 2)) || fail "$1 requires a value"
            case "$1" in
                --type) bundle_type="$2" ;;
                --version) version="$2" ;;
                --tag) tag="$2" ;;
                --revision) revision="$2" ;;
                --source-url) source_url="$2" ;;
                --source-date-epoch) source_date_epoch="$2" ;;
                --platform-image) platform_image="$2" ;;
                --mysql-image) mysql_image="$2" ;;
                --sbom-dir) sbom_dir="$2" ;;
                --license-dir) license_dir="$2" ;;
                --output-dir) output_dir="$2" ;;
                --source-root) source_root="$2" ;;
                --third-party-manifest) third_party_manifest="$2" ;;
                --platform-archive) platform_archive="$2" ;;
                --mysql-archive) mysql_archive="$2" ;;
                --candidate-manifest) candidate_manifest="$2" ;;
                --approval-root) approval_root="$2" ;;
            esac
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "unknown argument: $1"
            ;;
    esac
done

require_command python3
require_command git
require_command sha256sum
require_command zstd
require_command tar
[[ "$(tar --version 2>/dev/null | head -n 1)" == *"GNU tar"* ]] \
    || fail "GNU tar is required for deterministic archives"

source_root="$(canonical_path "${source_root}")"
sbom_dir="$(canonical_path "${sbom_dir}")"
license_dir="$(canonical_path "${license_dir}")"
candidate_manifest="$(canonical_path "${candidate_manifest}")"
approval_root="$(canonical_path "${approval_root}")"
mkdir -p -- "${output_dir}"
output_dir="$(canonical_path "${output_dir}")"
[[ -f "${candidate_manifest}" && ! -L "${candidate_manifest}" ]] \
    || fail "--candidate-manifest must identify a regular file"
[[ -d "${approval_root}" && ! -L "${approval_root}" ]] \
    || fail "--approval-root must identify a directory"
approval_manifest="${approval_root}/RELEASE-APPROVAL.json"
[[ -f "${approval_manifest}" && ! -L "${approval_manifest}" ]] \
    || fail "approval root is missing RELEASE-APPROVAL.json"
approved_third_party_manifest="${approval_root}/third-party-images.approved.json"
approved_license_dir="${approval_root}/licenses"
if [[ -n "${third_party_manifest}" ]]; then
    [[ "$(canonical_path "${third_party_manifest}")" == "$(canonical_path "${approved_third_party_manifest}")" ]] \
        || fail "third-party manifest must come from the validated approval artifact"
fi
third_party_manifest="$(canonical_path "${approved_third_party_manifest}")"
[[ "${license_dir}" == "$(canonical_path "${approved_license_dir}")" ]] \
    || fail "license directory must come from the validated approval artifact"

[[ "${bundle_type}" == "online" || "${bundle_type}" == "offline" ]] \
    || fail "--type must be online or offline"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] \
    || fail "invalid release version: ${version}"
[[ "${tag}" == "v${version}" ]] || fail "tag must equal v + version"
[[ "${revision}" =~ ^[0-9a-f]{40}$ ]] || fail "revision must be a complete lowercase Git SHA"
[[ "${source_url}" =~ ^https://github\.com/[^/[:space:]]+/[^/[:space:]]+$ ]] \
    || fail "source URL must identify a GitHub repository"
[[ "${source_date_epoch}" =~ ^[1-9][0-9]*$ ]] || fail "source date epoch must be positive"
for image in "${platform_image}" "${mysql_image}"; do
    [[ "${image}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
        || fail "image references must use immutable sha256 digests: ${image}"
done
target_image="${platform_image%@*}"

chain_args=(
    "${candidate_manifest}"
    "${approval_manifest}"
    --approval-root "${approval_root}"
    --expected-version "${version}"
    --expected-revision "${revision}"
    --expected-target-image "${target_image}"
)
if [[ "${bundle_type}" == "offline" ]]; then
    chain_args+=(--require-offline)
fi
python3 "${source_root}/scripts/release/release_chain.py" "${chain_args[@]}"
candidate_platform_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["platformDigest"])' "${candidate_manifest}")"
candidate_platform_config_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["platformConfigDigest"])' "${candidate_manifest}")"
candidate_mysql_image="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mysqlImage"])' "${candidate_manifest}")"
candidate_mysql_config_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mysqlConfigDigest"])' "${candidate_manifest}")"
[[ "${platform_image}" == "${target_image}@${candidate_platform_digest}" ]] \
    || fail "platform image differs from the approved candidate digest"
[[ "${mysql_image}" == "${candidate_mysql_image}" ]] \
    || fail "MySQL image differs from the approved candidate"

[[ -d "${source_root}/.git" ]] || fail "source root is not a Git checkout: ${source_root}"
[[ "$(git -C "${source_root}" rev-parse HEAD)" == "${revision}" ]] \
    || fail "revision does not match source checkout HEAD"
[[ "$(git -C "${source_root}" show -s --format=%ct HEAD)" == "${source_date_epoch}" ]] \
    || fail "source date epoch does not match the release commit"
git -C "${source_root}" diff --quiet --ignore-submodules -- \
    || fail "source checkout has tracked modifications"
git -C "${source_root}" diff --cached --quiet --ignore-submodules -- \
    || fail "source checkout has staged modifications"
[[ -z "$(git -C "${source_root}" status --porcelain --untracked-files=normal)" ]] \
    || fail "source checkout contains untracked files"

third_party_args=("${third_party_manifest}" --print-mysql-reference --require-platform-distribution)
if [[ "${bundle_type}" == "offline" ]]; then
    third_party_args+=(--require-offline-redistribution)
fi
manifest_mysql_image="$(python3 "${source_root}/scripts/release/validate-third-party-images.py" "${third_party_args[@]}")"
[[ "${mysql_image}" == "${manifest_mysql_image}" ]] \
    || fail "MySQL image differs from the reviewed third-party manifest"
license_args=(
    "${license_dir}"
    --platform-image "${platform_image}"
    --platform-config-digest "${candidate_platform_config_digest}"
    --mysql-image "${mysql_image}"
    --mysql-config-digest "${candidate_mysql_config_digest}"
)
if [[ "${bundle_type}" == "offline" ]]; then
    license_args+=(--require-mysql)
fi
python3 "${source_root}/scripts/release/validate-license-bundle.py" "${license_args[@]}"

for sbom in platform.spdx.json platform.cdx.json mysql.spdx.json mysql.cdx.json; do
    [[ -s "${sbom_dir}/${sbom}" ]] || fail "required SBOM is missing or empty: ${sbom_dir}/${sbom}"
done

if [[ "${bundle_type}" == "offline" ]]; then
    [[ -s "${platform_archive}" && -s "${mysql_archive}" ]] \
        || fail "offline bundles require both image archives"
    zstd --test --quiet "${platform_archive}"
    zstd --test --quiet "${mysql_archive}"
else
    [[ -z "${platform_archive}" && -z "${mysql_archive}" ]] \
        || fail "online bundles must not include image archives"
fi

archive_name="waterfall-ai-test-platform-${version}-linux-amd64-${bundle_type}.tar.zst"
archive_path="${output_dir}/${archive_name}"
[[ ! -e "${archive_path}" ]] || fail "refusing to overwrite existing artifact: ${archive_path}"

staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/playwright-release.XXXXXXXX")"
cleanup() {
    rm -rf -- "${staging_dir}"
}
trap cleanup EXIT
bundle_root="${staging_dir}/waterfall-ai-test-platform-${version}-linux-amd64"
mkdir -p -- "${bundle_root}"

while IFS= read -r -d '' tracked_path; do
    [[ "${tracked_path}" != "deploy/compose.build.yaml" ]] \
        || continue
    copy_file "${source_root}/${tracked_path}" "${bundle_root}/${tracked_path}"
done < <(git -C "${source_root}" ls-files -z -- deploy)

for root_file in .env.example LICENSE THIRD_PARTY_NOTICES.md; do
    copy_file "${source_root}/${root_file}" "${bundle_root}/${root_file}"
done
copy_file "${source_root}/scripts/release/BUNDLE-INSTALL.md" \
    "${bundle_root}/INSTALL.md"
for release_file in \
    docker_image_config_digest.py \
    release-metadata.schema.json \
    validate-metadata.py \
    release_chain.py \
    release_manifest.py \
    minisign.pub \
    license_payload.py \
    third-party-images.json \
    validate-third-party-images.py \
    validate-license-bundle.py; do
    copy_file \
        "${source_root}/scripts/release/${release_file}" \
        "${bundle_root}/scripts/release/${release_file}"
done
mkdir -p -- "${bundle_root}/provenance/candidate" "${bundle_root}/provenance/approval"
copy_file "${candidate_manifest}" \
    "${bundle_root}/provenance/candidate/RELEASE-CANDIDATE.json"
while IFS= read -r -d '' approval_file; do
    approval_relative="${approval_file#"${approval_root}/"}"
    [[ "${approval_relative}" != "${approval_file}" ]] \
        || fail "approval file escaped approval artifact directory"
    copy_file "${approval_file}" "${bundle_root}/provenance/approval/${approval_relative}"
done < <(find "${approval_root}" -type f -print0)
while IFS= read -r -d '' runtime_file; do
    runtime_name="$(basename -- "${runtime_file}")"
    copy_file "${runtime_file}" "${bundle_root}/bin/${runtime_name}"
done < <(find "${source_root}/scripts/release/runtime" -maxdepth 1 -type f -print0)

mkdir -p -- "${bundle_root}/sbom" "${bundle_root}/licenses" "${bundle_root}/assembly"
for sbom in platform.spdx.json platform.cdx.json mysql.spdx.json mysql.cdx.json; do
    copy_file "${sbom_dir}/${sbom}" "${bundle_root}/sbom/${sbom}"
done
copy_file "${source_root}/LICENSE" "${bundle_root}/licenses/PROJECT-LICENSE"
copy_file "${source_root}/THIRD_PARTY_NOTICES.md" "${bundle_root}/licenses/THIRD_PARTY_NOTICES.md"
copy_file "${third_party_manifest}" "${bundle_root}/licenses/third-party-images.json"
copy_file "${license_dir}/LICENSE-REVIEW.json" "${bundle_root}/licenses/LICENSE-REVIEW.json"
while IFS= read -r -d '' license_file; do
    license_relative="${license_file#"${license_dir}/"}"
    [[ "${license_relative}" != "${license_file}" ]] \
        || fail "license file escaped reviewed license directory"
    [[ "${license_relative}" != "LICENSE-REVIEW.json" ]] || continue
    copy_file "${license_file}" "${bundle_root}/licenses/${license_relative}"
done < <(find "${license_dir}" -type f -print0)

if [[ "${bundle_type}" == "offline" ]]; then
    mkdir -p -- "${bundle_root}/images"
    copy_file "${platform_archive}" "${bundle_root}/images/platform-linux-amd64.tar.zst"
    copy_file "${mysql_archive}" "${bundle_root}/images/mysql-linux-amd64.tar.zst"
fi

printf '%s\n' \
    '# Source snapshot' \
    '' \
    "- Tag: \`${tag}\`" \
    "- Revision: \`${revision}\`" \
    "- Source: <${source_url}/tree/${revision}>" \
    > "${bundle_root}/SOURCE-SNAPSHOT.md"
printf '%s\n' \
    "# ${tag}" \
    '' \
    "This asset was generated from \`${revision}\` at \`${source_url}\`." \
    > "${bundle_root}/RELEASE-NOTES.md"

metadata_args=(
    --output "${bundle_root}/RELEASE-METADATA.json"
    --assembly-output "${bundle_root}/assembly/bundle-manifest.json"
    --environment-output "${bundle_root}/.env.images"
    --bundle-type "${bundle_type}"
    --version "${version}"
    --tag "${tag}"
    --revision "${revision}"
    --source-url "${source_url}"
    --source-date-epoch "${source_date_epoch}"
    --platform-image "${platform_image}"
    --platform-config-digest "${candidate_platform_config_digest}"
    --mysql-image "${mysql_image}"
    --mysql-config-digest "${candidate_mysql_config_digest}"
)
if [[ "${bundle_type}" == "offline" ]]; then
    metadata_args+=(
        --platform-archive "${bundle_root}/images/platform-linux-amd64.tar.zst"
        --mysql-archive "${bundle_root}/images/mysql-linux-amd64.tar.zst"
    )
fi
python3 "${source_root}/scripts/release/write-metadata.py" "${metadata_args[@]}"
python3 "${source_root}/scripts/release/validate-metadata.py" \
    "${bundle_root}/RELEASE-METADATA.json" --bundle-root "${bundle_root}"

find "${bundle_root}" -type d -exec chmod 0755 {} +
find "${bundle_root}" -type f -exec chmod 0644 {} +
chmod 0755 "${bundle_root}/bin/"* "${bundle_root}/deploy/platform-compose"
checksum_path="${staging_dir}/SHA256SUMS"
(
    cd -- "${bundle_root}"
    find . -type f -print0 \
        | sort -z \
        | xargs -0 sha256sum > "${checksum_path}"
)
install -m 0644 "${checksum_path}" "${bundle_root}/SHA256SUMS"
find "${bundle_root}" -exec touch --date="@${source_date_epoch}" {} +

tar \
    --sort=name \
    --format=posix \
    --mtime="@${source_date_epoch}" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --pax-option=delete=atime,delete=ctime \
    --directory "${staging_dir}" \
    --create \
    --file - \
    "$(basename -- "${bundle_root}")" \
    | zstd --threads=1 -19 --quiet --stdout > "${archive_path}"

printf '%s\n' "${archive_path}"
