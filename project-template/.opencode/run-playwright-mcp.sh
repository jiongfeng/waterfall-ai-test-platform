#!/usr/bin/env bash
set -Eeuo pipefail

# The OpenCode server process holds its own authentication and provider
# credentials. The browser MCP must not inherit those values.
exec env -i \
    HOME="${HOME:-/tmp}" \
    LANG="${LANG:-C.UTF-8}" \
    PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}" \
    PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-}" \
    TMPDIR="${TMPDIR:-/tmp}" \
    TZ="${TZ:-UTC}" \
    npx playwright run-test-mcp-server \
        --config playwright.config.ts
