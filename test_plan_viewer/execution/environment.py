"""Execution opt-in guard for repository test processes."""

import os


def require_test_execution_enabled(source=None):
    """Require an explicit opt-in before running repository code."""

    source = os.environ if source is None else source
    enabled = str(
        source.get("PLATFORM_ALLOW_TEST_EXECUTION") or ""
    ).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "Test execution is disabled by default. Set "
            "PLATFORM_ALLOW_TEST_EXECUTION=true only for trusted "
            "single-tenant projects."
        )
