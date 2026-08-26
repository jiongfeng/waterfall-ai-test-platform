# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/playwright:v1.61.1-noble@sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48

ARG GUNICORN_VERSION=23.0.0
ARG OPENCODE_VERSION=1.17.18
ARG VERSION=dev
ARG REVISION=unknown
ARG SOURCE_URL

LABEL org.opencontainers.image.title="Waterfall AI" \
      org.opencontainers.image.description="Agent-driven test automation platform for trusted single-tenant teams" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV APP_HOME=/opt/playwright-platform/app \
    HOME=/home/pwuser \
    PATH=/opt/playwright-platform/venv/bin:${PATH} \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/home/pwuser/.cache \
    XDG_CONFIG_HOME=/home/pwuser/.config \
    XDG_DATA_HOME=/home/pwuser/.local/share \
    XDG_STATE_HOME=/home/pwuser/.local/state

USER root

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        git \
        python3 \
        python3-venv \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${APP_HOME}

COPY requirements.txt /tmp/playwright-platform-requirements.txt

RUN python3 -m venv /opt/playwright-platform/venv \
    && python -m pip install --no-cache-dir \
        --requirement /tmp/playwright-platform-requirements.txt \
        "gunicorn==${GUNICORN_VERSION}" \
    && rm /tmp/playwright-platform-requirements.txt

RUN set -eu; \
    opencode_probe_root="$(mktemp -d)"; \
    install -d \
        "${opencode_probe_root}/home" \
        "${opencode_probe_root}/cache" \
        "${opencode_probe_root}/config" \
        "${opencode_probe_root}/data" \
        "${opencode_probe_root}/state"; \
    HOME="${opencode_probe_root}/home" \
    XDG_CACHE_HOME="${opencode_probe_root}/cache" \
    XDG_CONFIG_HOME="${opencode_probe_root}/config" \
    XDG_DATA_HOME="${opencode_probe_root}/data" \
    XDG_STATE_HOME="${opencode_probe_root}/state" \
        node -e 'const major=Number(process.versions.node.split(".")[0]); if (![22, 24].includes(major)) throw new Error(`Unsupported Node.js ${process.version}`)'; \
    HOME="${opencode_probe_root}/home" \
    XDG_CACHE_HOME="${opencode_probe_root}/cache" \
    XDG_CONFIG_HOME="${opencode_probe_root}/config" \
    XDG_DATA_HOME="${opencode_probe_root}/data" \
    XDG_STATE_HOME="${opencode_probe_root}/state" \
        npm install --global --no-audit --no-fund \
            "opencode-ai@${OPENCODE_VERSION}"; \
    HOME="${opencode_probe_root}/home" \
    XDG_CACHE_HOME="${opencode_probe_root}/cache" \
    XDG_CONFIG_HOME="${opencode_probe_root}/config" \
    XDG_DATA_HOME="${opencode_probe_root}/data" \
    XDG_STATE_HOME="${opencode_probe_root}/state" \
        opencode --version; \
    rm -rf -- "${opencode_probe_root}"

COPY --chown=root:root deploy/opencode-provider.py /usr/local/bin/platform-opencode-provider

RUN chmod 0555 /usr/local/bin/platform-opencode-provider

COPY --chown=pwuser:pwuser app.py requirements.txt LICENSE THIRD_PARTY_NOTICES.md ./
COPY --chown=pwuser:pwuser test_plan_viewer/ ./test_plan_viewer/
COPY --chown=pwuser:pwuser static/ ./static/
COPY --chown=pwuser:pwuser templates/ ./templates/
COPY --chown=pwuser:pwuser project-template/ ./project-template/

RUN npm ci --prefix project-template \
        --omit=optional \
        --no-audit \
        --no-fund \
    && npm install --prefix project-template/.opencode \
        --no-package-lock \
        --no-save \
        --omit=optional \
        --no-audit \
        --no-fund \
        "@opencode-ai/plugin@${OPENCODE_VERSION}" \
    && node -e 'const expected="1.61.1"; const actual=require("./project-template/node_modules/@playwright/test/package.json").version; if (actual !== expected) throw new Error(`Expected Playwright ${expected}, received ${actual}`)' \
    && OPENCODE_VERSION="${OPENCODE_VERSION}" node -e 'const expected=process.env.OPENCODE_VERSION; const actual=require("./project-template/.opencode/node_modules/@opencode-ai/plugin/package.json").version; if (actual !== expected) throw new Error(`Expected OpenCode plugin ${expected}, received ${actual}`)' \
    && chown -R pwuser:pwuser project-template \
    && install -d -o pwuser -g pwuser \
        /data/playwright-projects \
        /data/playwright-workspaces \
        /tmp/gunicorn \
    && install -d -o pwuser -g pwuser -m 0700 \
        "${XDG_CACHE_HOME}/opencode" \
        "${XDG_CONFIG_HOME}/opencode" \
        "${XDG_DATA_HOME}/opencode" \
        "${XDG_DATA_HOME}/opencode/log" \
        "${XDG_DATA_HOME}/opencode/repos" \
        "${XDG_STATE_HOME}" \
        "${XDG_STATE_HOME}/opencode" \
    && install -o pwuser -g pwuser -m 0600 /dev/null \
        "${XDG_STATE_HOME}/.volume-initialized" \
    && runtime_uid="$(id -u pwuser)" \
    && runtime_gid="$(id -g pwuser)" \
    && for runtime_path in \
        "${XDG_CACHE_HOME}/opencode" \
        "${XDG_CONFIG_HOME}/opencode" \
        "${XDG_DATA_HOME}/opencode" \
        "${XDG_DATA_HOME}/opencode/log" \
        "${XDG_DATA_HOME}/opencode/repos" \
        "${XDG_STATE_HOME}" \
        "${XDG_STATE_HOME}/opencode"; \
       do \
        test -d "${runtime_path}" \
            && test ! -L "${runtime_path}" \
            && test "$(stat -c '%u:%g' "${runtime_path}")" = "${runtime_uid}:${runtime_gid}" \
            || { \
                printf 'OpenCode runtime directory failed ownership validation: %s\n' "${runtime_path}" >&2; \
                exit 1; \
            }; \
       done \
    && unexpected_owner="$(find \
        "${XDG_CACHE_HOME}/opencode" \
        "${XDG_CONFIG_HOME}/opencode" \
        "${XDG_DATA_HOME}/opencode" \
        "${XDG_STATE_HOME}" \
        -xdev \( ! -uid "${runtime_uid}" -o ! -gid "${runtime_gid}" \) \
        -print -quit)" \
    && test -z "${unexpected_owner}" \
        || { \
            printf 'OpenCode runtime tree contains an unexpected owner: %s\n' "${unexpected_owner}" >&2; \
            exit 1; \
        }

# Ubuntu Noble's openssl package can retain a Jammy-era copyright link after
# libssl3 is replaced by libssl3t64. Repair only that exact, proven migration;
# every other filesystem state fails the image build.
RUN set -eu; \
    license_link=/usr/share/doc/openssl/copyright; \
    old_target=../libssl3/copyright; \
    new_target=../libssl3t64/copyright; \
    old_target_path=/usr/share/doc/libssl3/copyright; \
    new_target_path=/usr/share/doc/libssl3t64/copyright; \
    test -L "${license_link}" \
        || { printf 'Expected OpenSSL copyright path to be a symlink.\n' >&2; exit 1; }; \
    test -f "${new_target_path}" \
        && test ! -L "${new_target_path}" \
        && test -s "${new_target_path}" \
        && test -r "${new_target_path}" \
        || { printf 'Replacement libssl3t64 copyright is not a readable non-empty regular file.\n' >&2; exit 1; }; \
    current_target="$(readlink -- "${license_link}")"; \
    if test "${current_target}" = "${old_target}"; then \
        test ! -e "${old_target_path}" && test ! -L "${old_target_path}" \
            || { printf 'Refusing to replace a non-missing libssl3 copyright target.\n' >&2; exit 1; }; \
        rm -- "${license_link}"; \
        ln -s -- "${new_target}" "${license_link}"; \
    elif test "${current_target}" != "${new_target}"; then \
        printf 'Unexpected OpenSSL copyright symlink target: %s\n' "${current_target}" >&2; \
        exit 1; \
    fi; \
    test "$(readlink -- "${license_link}")" = "${new_target}" \
        && test "$(readlink -f -- "${license_link}")" = "${new_target_path}" \
        && test -r "${license_link}" \
        || { printf 'OpenSSL copyright symlink repair verification failed.\n' >&2; exit 1; }

COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/platform-entrypoint
COPY --chmod=0755 deploy/healthcheck.sh /usr/local/bin/platform-healthcheck

USER pwuser

EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=8s --start-period=20s --retries=8 \
    CMD ["/usr/local/bin/platform-healthcheck", "platform"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/platform-entrypoint"]
CMD ["platform"]
