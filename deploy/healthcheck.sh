#!/usr/bin/env bash
set -Eeuo pipefail

case "${1:-platform}" in
    platform)
        exec python3 - <<'PY'
import os
import urllib.request

port = int(os.environ.get("PLATFORM_PORT", "5000"))
with urllib.request.urlopen(
    f"http://127.0.0.1:{port}/login",
    timeout=5,
) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
        ;;
    opencode)
        exec python3 - <<'PY'
import base64
import json
import os
import urllib.request

port = int(os.environ.get("OPENCODE_PORT", "4096"))
username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
if not password:
    raise SystemExit(1)

credentials = base64.b64encode(
    f"{username}:{password}".encode("utf-8")
).decode("ascii")
request = urllib.request.Request(
    f"http://127.0.0.1:{port}/global/health",
    headers={"Authorization": f"Basic {credentials}"},
)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
    if response.status != 200 or payload.get("healthy") is not True:
        raise SystemExit(1)
PY
        ;;
    *)
        printf 'usage: %s platform|opencode\n' "$0" >&2
        exit 2
        ;;
esac
