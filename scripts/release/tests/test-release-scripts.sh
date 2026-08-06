#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C
export PYTHONDONTWRITEBYTECODE=1

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TEST_DIR
RELEASE_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
readonly RELEASE_DIR
REPOSITORY_ROOT="$(cd -- "${RELEASE_DIR}/../.." && pwd -P)"
readonly REPOSITORY_ROOT

fail() {
    printf 'release script test: %s\n' "$*" >&2
    exit 1
}

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/test-playwright-release.XXXXXXXX")"
cleanup() {
    rm -rf -- "${temporary_dir}"
}
trap cleanup EXIT

python3 - "${RELEASE_DIR}/release-metadata.schema.json" <<'PY'
import json
import re
import sys

schema = json.load(open(sys.argv[1], encoding="utf-8"))
pattern = schema["$defs"]["image"]["properties"]["reference"]["pattern"]
assert re.fullmatch(pattern, "ghcr.io/example/platform@sha256:" + "a" * 64)
assert not re.fullmatch(pattern, "ghcr.io/example/space image@sha256:" + "a" * 64)
assert not re.fullmatch(pattern, "ghcr.io/example/image@sha256:" + "A" * 64)
runtime_pattern = schema["$defs"]["image"]["properties"]["runtimeReference"]["pattern"]
assert re.fullmatch(runtime_pattern, "ghcr.io/example/platform@sha256:" + "a" * 64)
assert re.fullmatch(
    runtime_pattern,
    "waterfall-ai-test-platform.local/platform:sha256-" + "a" * 64,
)
assert not re.fullmatch(
    runtime_pattern,
    "waterfall-ai-test-platform.local/platform:latest",
)
PY

if python3 "${RELEASE_DIR}/validate-third-party-images.py" \
    "${RELEASE_DIR}/third-party-images.json" \
    --require-platform-distribution >/dev/null 2>&1; then
    fail "unapproved platform distribution gate unexpectedly passed"
fi
if python3 "${RELEASE_DIR}/validate-third-party-images.py" \
    "${RELEASE_DIR}/third-party-images.json" \
    --require-offline-redistribution >/dev/null 2>&1; then
    fail "unapproved MySQL redistribution gate unexpectedly passed"
fi
if python3 "${RELEASE_DIR}/validate-license-bundle.py" \
    "${RELEASE_DIR}" \
    --platform-image "ghcr.io/example/platform@sha256:$(printf 'a%.0s' {1..64})" \
    --platform-config-digest "sha256:$(printf 'd%.0s' {1..64})" \
    --mysql-image "docker.io/library/mysql@sha256:$(printf 'b%.0s' {1..64})" \
    --mysql-config-digest "sha256:$(printf 'e%.0s' {1..64})" \
    >/dev/null 2>&1; then
    fail "incomplete final-image license bundle unexpectedly passed"
fi

if [[ "$(tar --version 2>/dev/null | head -n 1)" != *"GNU tar"* ]]; then
    printf 'release script test: deterministic archive integration skipped (GNU tar unavailable)\n'
    exit 0
fi

source_root="${temporary_dir}/source"
mkdir -p "${source_root}/scripts" "${source_root}/deploy"
cp -R "${RELEASE_DIR}" "${source_root}/scripts/release"
find "${source_root}/scripts/release" -type d -name __pycache__ -prune -exec rm -rf -- {} +
cp "${REPOSITORY_ROOT}/deploy/compose.yaml" "${source_root}/deploy/compose.yaml"
cp "${REPOSITORY_ROOT}/deploy/platform-compose" "${source_root}/deploy/platform-compose"
cp "${REPOSITORY_ROOT}/deploy/preflight-install.py" "${source_root}/deploy/preflight-install.py"
cp "${REPOSITORY_ROOT}/deploy/config.example.json" "${source_root}/deploy/config.example.json"
cp "${REPOSITORY_ROOT}/.env.example" "${source_root}/.env.example"
printf 'test license\n' > "${source_root}/LICENSE"
printf 'test notices\n' > "${source_root}/THIRD_PARTY_NOTICES.md"

platform_digest="sha256:$(printf 'a%.0s' {1..64})"
mysql_digest="sha256:$(printf 'b%.0s' {1..64})"
platform_config_digest="sha256:$(printf 'd%.0s' {1..64})"
mysql_config_digest="sha256:$(printf 'e%.0s' {1..64})"
platform_image="ghcr.io/example/waterfall-ai-test-platform@${platform_digest}"
mysql_image="docker.io/library/mysql:8.4@${mysql_digest}"
mysql_parent="docker.io/library/mysql:8.4@sha256:$(printf 'c%.0s' {1..64})"
git -C "${source_root}" init --quiet
git -C "${source_root}" config user.name release-script-test
git -C "${source_root}" config user.email release-script-test@example.invalid
git -C "${source_root}" add .
GIT_AUTHOR_DATE=1700000000 GIT_COMMITTER_DATE=1700000000 \
    git -C "${source_root}" commit --quiet -m fixture
revision="$(git -C "${source_root}" rev-parse HEAD)"
source_date_epoch="$(git -C "${source_root}" show -s --format=%ct HEAD)"

candidate_root="${temporary_dir}/candidate"
sbom_dir="${candidate_root}/sbom"
license_payload_dir="${candidate_root}/license-payloads"
approval_root="${temporary_dir}/approval"
mkdir -p "${sbom_dir}"
for component in platform mysql; do
    printf '{"spdxVersion":"SPDX-2.3"}\n' > "${sbom_dir}/${component}.spdx.json"
    printf '{"bomFormat":"CycloneDX"}\n' > "${sbom_dir}/${component}.cdx.json"
done
printf 'fake OCI candidate archive\n' > "${candidate_root}/platform-linux-amd64.oci.tar"
python3 - \
    "${license_payload_dir}" \
    "${platform_image}" "${platform_config_digest}" \
    "${mysql_image}" "${mysql_config_digest}" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
components = {
    "platformImage": (sys.argv[2], sys.argv[3]),
    "mysqlImage": (sys.argv[4], sys.argv[5]),
}
for name, (reference, config_digest) in components.items():
    source_path = "/usr/share/common-licenses/MIT"
    artifact_name = hashlib.sha256(source_path.encode()).hexdigest() + ".license"
    component = root / name
    files = component / "files"
    files.mkdir(parents=True)
    content = f"fixture license text for {name}\n".encode()
    (files / artifact_name).write_bytes(content)
    manifest = {
        "schemaVersion": 1,
        "kind": "waterfall-ai-test-platform-final-image-license-files",
        "selectionPolicy": "license-notice-filenames-v1",
        "imageReference": reference,
        "configDigest": config_digest,
        "fileCount": 1,
        "totalBytes": len(content),
        "files": [{
            "sourcePath": source_path,
            "artifactPath": f"files/{artifact_name}",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    (component / "LICENSE-FILES.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
PY
candidate_manifest="${candidate_root}/RELEASE-CANDIDATE.json"
python3 "${source_root}/scripts/release/write-release-candidate.py" \
    --output "${candidate_manifest}" \
    --version 1.2.3-beta.1 \
    --revision "${revision}" \
    --source-url https://github.com/example/waterfall-ai-test-platform \
    --source-date-epoch "${source_date_epoch}" \
    --target-image ghcr.io/example/waterfall-ai-test-platform \
    --platform-digest "${platform_digest}" \
    --platform-config-digest "${platform_config_digest}" \
    --platform-archive "${candidate_root}/platform-linux-amd64.oci.tar" \
    --mysql-image "${mysql_image}" \
    --mysql-parent-index "${mysql_parent}" \
    --mysql-config-digest "${mysql_config_digest}" \
    --sbom-dir "${sbom_dir}" \
    --license-payload-dir "${license_payload_dir}" \
    --workflow-repository example/waterfall-ai-test-platform \
    --workflow-run-id 123 \
    --workflow-run-attempt 1 \
    --workflow-ref refs/heads/main \
    --workflow-sha "${revision}"
python3 "${source_root}/scripts/release/write-release-approval.py" \
    --candidate "${candidate_manifest}" \
    --candidate-root "${candidate_root}" \
    --output-dir "${approval_root}" \
    --platform-distribution-approved true \
    --platform-distribution-evidence https://example.invalid/reviews/platform-distribution \
    --platform-license-complete true \
    --platform-license-evidence https://example.invalid/reviews/platform-license \
    --mysql-offline-approved true \
    --mysql-offline-evidence https://example.invalid/reviews/mysql-distribution \
    --mysql-license-complete true \
    --mysql-license-evidence https://example.invalid/reviews/mysql-license \
    --reviewed-by release-script-test \
    --reviewed-at 2026-08-05T00:00:00Z \
    --workflow-repository example/waterfall-ai-test-platform \
    --workflow-run-id 456 \
    --workflow-run-attempt 1 \
    --workflow-ref refs/heads/main \
    --workflow-sha "${revision}"
license_dir="${approval_root}/licenses"

python3 "${source_root}/scripts/release/release_chain.py" \
    "${candidate_manifest}" \
    "${approval_root}/RELEASE-APPROVAL.json" \
    --candidate-root "${candidate_root}" \
    --approval-root "${approval_root}" \
    --require-offline \
    --expected-version 1.2.3-beta.1 \
    --expected-revision "${revision}" \
    --expected-target-image ghcr.io/example/waterfall-ai-test-platform

cp "${approval_root}/RELEASE-APPROVAL.json" "${temporary_dir}/tampered-approval.json"
python3 - "${temporary_dir}/tampered-approval.json" <<'PY'
import json, sys
path = sys.argv[1]
value = json.load(open(path, encoding="utf-8"))
value["candidateManifestSha256"] = "0" * 64
open(path, "w", encoding="utf-8").write(json.dumps(value) + "\n")
PY
if python3 "${source_root}/scripts/release/release_chain.py" \
    "${candidate_manifest}" "${temporary_dir}/tampered-approval.json" \
    --approval-root "${approval_root}" >/dev/null 2>&1; then
    fail "approval with a tampered candidate hash unexpectedly passed"
fi

common_args=(
    --version 1.2.3-beta.1
    --tag v1.2.3-beta.1
    --revision "${revision}"
    --source-url https://github.com/example/waterfall-ai-test-platform
    --source-date-epoch "${source_date_epoch}"
    --platform-image "${platform_image}"
    --mysql-image "${mysql_image}"
    --sbom-dir "${sbom_dir}"
    --license-dir "${license_dir}"
    --candidate-manifest "${candidate_manifest}"
    --approval-root "${approval_root}"
    --source-root "${source_root}"
)

for iteration in one two; do
    output_dir="${temporary_dir}/online-${iteration}"
    bash "${source_root}/scripts/release/build-bundle.sh" \
        --type online \
        --output-dir "${output_dir}" \
        "${common_args[@]}" >/dev/null
    bash "${RELEASE_DIR}/verify-bundle.sh" \
        --allow-unsigned-local \
        "${output_dir}/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-online.tar.zst"
done
cmp \
    "${temporary_dir}/online-one/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-online.tar.zst" \
    "${temporary_dir}/online-two/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-online.tar.zst"

printf 'fake platform docker save\n' | zstd --threads=1 --quiet --stdout \
    > "${temporary_dir}/platform.tar.zst"
printf 'fake mysql docker save\n' | zstd --threads=1 --quiet --stdout \
    > "${temporary_dir}/mysql.tar.zst"
for iteration in one two; do
    output_dir="${temporary_dir}/offline-${iteration}"
    bash "${source_root}/scripts/release/build-bundle.sh" \
        --type offline \
        --output-dir "${output_dir}" \
        --platform-archive "${temporary_dir}/platform.tar.zst" \
        --mysql-archive "${temporary_dir}/mysql.tar.zst" \
        "${common_args[@]}" >/dev/null
    bash "${RELEASE_DIR}/verify-bundle.sh" \
        --allow-unsigned-local \
        "${output_dir}/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-offline.tar.zst"
done
cmp \
    "${temporary_dir}/offline-one/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-offline.tar.zst" \
    "${temporary_dir}/offline-two/waterfall-ai-test-platform-1.2.3-beta.1-linux-amd64-offline.tar.zst"

[[ "$(grep -Fc 'docker_image_config_digest.py' "${RELEASE_DIR}/runtime/verify")" -eq 2 ]] \
    || fail "runtime verification does not validate both platform and MySQL container identities"
for identity_flag in --container --expected-manifest --expected-config; do
    [[ "$(grep -Fc -- "${identity_flag}" "${RELEASE_DIR}/runtime/verify")" -eq 2 ]] \
        || fail "runtime verification does not bind both container identities with ${identity_flag}"
done

printf 'release script tests passed\n'
