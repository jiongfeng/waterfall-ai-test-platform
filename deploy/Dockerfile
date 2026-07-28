# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/playwright:v1.61.1-noble@sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48

ARG GUNICORN_VERSION=23.0.0
ARG OPENCODE_VERSION=1.17.18
ARG VERSION=dev
ARG REVISION=unknown
ARG SOURCE_URL

LABEL org.opencontainers.image.title="Playwright Test Platform" \
      org.opencontainers.image.description="Single-tenant Playwright test platform beta" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV APP_HOME=/opt/playwright-platform/app \
    PATH=/opt/playwright-platform/venv/bin:${PATH} \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

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

RUN node -e 'const major=Number(process.versions.node.split(".")[0]); if (![22, 24].includes(major)) throw new Error(`Unsupported Node.js ${process.version}`)' \
    && npm install --global --no-audit --no-fund \
        "opencode-ai@${OPENCODE_VERSION}" \
    && opencode --version

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
        /home/pwuser/.cache/opencode \
        /home/pwuser/.config/opencode \
        /home/pwuser/.local/share/opencode \
        /tmp/gunicorn \
    && install -d -o root -g pwuser -m 0750 \
        /etc/playwright-platform

COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/platform-entrypoint
COPY --chmod=0755 deploy/healthcheck.sh /usr/local/bin/platform-healthcheck

USER pwuser

EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=8s --start-period=20s --retries=8 \
    CMD ["/usr/local/bin/platform-healthcheck", "platform"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/platform-entrypoint"]
CMD ["platform"]
