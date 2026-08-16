import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from test_plan_viewer.configuration import parse_target_system_config
from test_plan_viewer.generation import seed as seed_generation
from test_plan_viewer.web.sse import sse_payload


VALID_SCRIPT = """import { test, expect } from '@playwright/test';

test('login seed', async ({ page }) => {
  await expect(page.locator('body')).toBeVisible();
});
"""


def parse_sse_text(value):
    events = []
    for block in value.replace("\r\n", "\n").strip().split("\n\n"):
        event_name = ""
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if event_name and data_lines:
            events.append(
                (event_name, json.loads("\n".join(data_lines)))
            )
    return events


class SeedModeConfigurationTests(unittest.TestCase):
    def test_legacy_target_system_defaults_to_login_mode(self):
        parsed = parse_target_system_config(
            {"base_url": "https://example.test"}
        )

        self.assertEqual(parsed["seed_mode"], "login")

    def test_unknown_persisted_seed_mode_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "visit_only.*login",
        ):
            parse_target_system_config(
                {
                    "base_url": "https://example.test",
                    "seed_mode": "unknown",
                }
            )

    def test_completion_probe_accepts_identical_content_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            target_file.write_text(VALID_SCRIPT, encoding="utf-8")
            probe = seed_generation.SeedCompletionProbe(
                target_file,
                app.file_hash,
            )
            target_file.write_text(VALID_SCRIPT, encoding="utf-8")
            rewritten_mtime_ns = probe.original_mtime_ns + 1_000_000_000
            os.utime(
                target_file,
                ns=(rewritten_mtime_ns, rewritten_mtime_ns),
            )

            self.assertTrue(probe.check())


class SeedGenerationModeRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.auth_disabled = patch.object(
            app,
            "get_auth_config",
            return_value={"enabled": False},
        )
        self.target_system = {
            "base_url": "https://target.example/app",
            "login_url": "https://target.example/private-login",
            "username": "visit-user-must-not-leak",
            "password": "visit-password-must-not-leak",
            "seed_mode": "login",
        }

    def test_visit_only_writes_fixed_script_without_starting_model_job(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            with (
                self.auth_disabled,
                patch.object(
                    app,
                    "get_current_target_system_config",
                    return_value=self.target_system,
                ),
                patch.object(
                    app,
                    "get_seed_script_file",
                    return_value=target_file,
                ),
                patch.object(
                    app,
                    "get_seed_script_relative_path",
                    return_value="tests/seed/seed.spec.ts",
                ),
                patch.object(
                    app,
                    "build_seed_generation_prompt",
                ) as build_prompt,
                patch.object(app, "create_test_job") as create_job,
                patch.object(
                    app,
                    "stream_plan_generation",
                ) as stream_generation,
                patch.object(
                    app,
                    "sync_script_asset",
                    return_value=None,
                ),
                patch.object(
                    app,
                    "persist_current_seed_mode",
                ) as persist_mode,
            ):
                response = self.client.post(
                    "/api/project-settings/seed/generate",
                    json={"mode": "visit_only"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/event-stream")
            build_prompt.assert_not_called()
            create_job.assert_not_called()
            stream_generation.assert_not_called()
            persist_mode.assert_called_once_with("visit_only")

            content = target_file.read_text(encoding="utf-8")
            self.assertIn(self.target_system["base_url"], content)
            self.assertNotIn(self.target_system["login_url"], content)
            self.assertNotIn(self.target_system["username"], content)
            self.assertNotIn(self.target_system["password"], content)
            self.assertNotIn(".fill(", content)
            self.assertNotIn(".click(", content)

            response_text = response.get_data(as_text=True)
            self.assertNotIn(self.target_system["login_url"], response_text)
            self.assertNotIn(self.target_system["username"], response_text)
            self.assertNotIn(self.target_system["password"], response_text)
            events = parse_sse_text(response_text)
            done = next(data for event, data in events if event == "done")
            self.assertTrue(done["ok"])
            self.assertEqual(done["seed_mode"], "visit_only")

    def test_persist_mode_updates_existing_target_system_json(self):
        database_baseline = {"enabled": False}
        plan_generation = {"default_coverage_profile": "core"}
        with (
            patch.object(
                app,
                "get_current_target_system_config",
                return_value=self.target_system,
            ),
            patch.object(
                app,
                "is_platform_database_enabled",
                return_value=True,
            ),
            patch.object(
                app,
                "get_database_baseline_config",
                return_value=database_baseline,
            ),
            patch.object(
                app,
                "get_plan_generation_config",
                return_value=plan_generation,
            ),
            patch.object(
                app,
                "update_current_project_settings_in_mysql",
                return_value={"project_key": "demo"},
            ) as update_settings,
        ):
            result = app.persist_current_seed_mode("visit_only")

        self.assertEqual(result, {"project_key": "demo"})
        target_system, baseline, generation = update_settings.call_args.args
        self.assertEqual(target_system["seed_mode"], "visit_only")
        self.assertEqual(target_system["username"], self.target_system["username"])
        self.assertEqual(target_system["password"], self.target_system["password"])
        self.assertIs(baseline, database_baseline)
        self.assertIs(generation, plan_generation)

    def test_visit_only_succeeds_without_mysql_or_credentials(self):
        target_system = {
            "base_url": "https://public.example/app",
            "login_url": "/login",
            "username": "",
            "password": "",
            "seed_mode": "login",
        }
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            with (
                self.auth_disabled,
                patch.object(
                    app,
                    "get_current_target_system_config",
                    return_value=target_system,
                ),
                patch.object(
                    app,
                    "get_seed_script_file",
                    return_value=target_file,
                ),
                patch.object(
                    app,
                    "get_seed_script_relative_path",
                    return_value="tests/seed/seed.spec.ts",
                ),
                patch.object(
                    app,
                    "is_platform_database_enabled",
                    return_value=False,
                ),
                patch.object(
                    app,
                    "update_current_project_settings_in_mysql",
                ) as update_settings,
                patch.object(
                    app,
                    "sync_script_asset",
                    return_value=None,
                ),
            ):
                response = self.client.post(
                    "/api/project-settings/seed/generate",
                    json={"mode": "visit_only"},
                )

            self.assertEqual(response.status_code, 200)
            update_settings.assert_not_called()
            done = next(
                data
                for event, data in parse_sse_text(
                    response.get_data(as_text=True)
                )
                if event == "done"
            )
            self.assertEqual(done["seed_mode"], "visit_only")
            self.assertEqual(done["seed_mode_persistence"], "skipped")

    def test_seed_file_marker_is_the_effective_mode_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            target_file.write_text(
                seed_generation.build_visit_only_seed_script(
                    self.target_system["base_url"]
                ),
                encoding="utf-8",
            )
            with patch.object(
                app,
                "get_seed_script_file",
                return_value=target_file,
            ):
                self.assertEqual(
                    app.get_current_seed_mode(self.target_system),
                    "visit_only",
                )
                target_file.write_text(VALID_SCRIPT, encoding="utf-8")
                self.assertEqual(
                    app.get_current_seed_mode(
                        {**self.target_system, "seed_mode": "visit_only"}
                    ),
                    "",
                )

    def test_concurrent_seed_generation_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            lease = seed_generation.acquire_seed_generation_lease(
                target_file
            )
            self.assertIsNotNone(lease)
            try:
                with (
                    self.auth_disabled,
                    patch.object(
                        app,
                        "get_seed_script_file",
                        return_value=target_file,
                    ),
                    patch.object(
                        app,
                        "get_current_target_system_config",
                    ) as get_target_system,
                ):
                    response = self.client.post(
                        "/api/project-settings/seed/generate",
                        json={"mode": "visit_only"},
                    )
            finally:
                lease.release()

            self.assertEqual(response.status_code, 409)
            self.assertFalse(target_file.exists())
            get_target_system.assert_not_called()

    def test_visit_only_restores_previous_file_when_asset_sync_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            original_content = VALID_SCRIPT.encode("utf-8")
            target_file.write_bytes(original_content)
            with (
                self.auth_disabled,
                patch.object(
                    app,
                    "get_current_target_system_config",
                    return_value=self.target_system,
                ),
                patch.object(
                    app,
                    "get_seed_script_file",
                    return_value=target_file,
                ),
                patch.object(
                    app,
                    "sync_script_asset",
                    side_effect=RuntimeError("asset sync failed"),
                ),
            ):
                response = self.client.post(
                    "/api/project-settings/seed/generate",
                    json={"mode": "visit_only"},
                )

            self.assertEqual(response.status_code, 500)
            self.assertEqual(target_file.read_bytes(), original_content)

    def test_mode_persistence_failure_does_not_discard_valid_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            with (
                self.auth_disabled,
                patch.object(
                    app,
                    "get_current_target_system_config",
                    return_value=self.target_system,
                ),
                patch.object(
                    app,
                    "get_seed_script_file",
                    return_value=target_file,
                ),
                patch.object(
                    app,
                    "get_seed_script_relative_path",
                    return_value="tests/seed/seed.spec.ts",
                ),
                patch.object(
                    app,
                    "sync_script_asset",
                    return_value=None,
                ),
                patch.object(
                    app,
                    "persist_current_seed_mode",
                    side_effect=RuntimeError("database unavailable"),
                ),
            ):
                response = self.client.post(
                    "/api/project-settings/seed/generate",
                    json={"mode": "visit_only"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(target_file.exists())
            done = next(
                data
                for event, data in parse_sse_text(
                    response.get_data(as_text=True)
                )
                if event == "done"
            )
            self.assertEqual(done["seed_mode_persistence"], "failed")

    def test_login_mode_keeps_model_generation_and_reports_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file = Path(directory) / "seed.spec.ts"
            target_file.write_text(VALID_SCRIPT, encoding="utf-8")

            def fake_stream(*_args, success_payload_factory, **_kwargs):
                payload = success_payload_factory()
                return iter(
                    [sse_payload("done", {"ok": True, **payload})]
                )

            with (
                self.auth_disabled,
                patch.object(
                    app,
                    "get_current_target_system_config",
                    return_value=self.target_system,
                ),
                patch.object(
                    app,
                    "get_seed_script_file",
                    return_value=target_file,
                ),
                patch.object(
                    app,
                    "get_seed_script_relative_path",
                    return_value="tests/seed/seed.spec.ts",
                ),
                patch.object(
                    app,
                    "build_seed_generation_prompt",
                    return_value="login generation prompt",
                ) as build_prompt,
                patch.object(app, "create_test_job") as create_job,
                patch.object(
                    app,
                    "stream_plan_generation",
                    side_effect=fake_stream,
                ) as stream_generation,
                patch.object(
                    app,
                    "build_setup_targets",
                    return_value=[],
                ),
                patch.object(
                    app,
                    "sync_script_asset",
                    return_value=None,
                ),
                patch.object(
                    app,
                    "persist_current_seed_mode",
                ) as persist_mode,
            ):
                response = self.client.post(
                    "/api/project-settings/seed/generate",
                    json={"mode": "login"},
                )
                response_text = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            build_prompt.assert_called_once_with(
                self.target_system,
                target_file,
            )
            create_job.assert_called_once()
            stream_generation.assert_called_once()
            persist_mode.assert_called_once_with("login")
            events = parse_sse_text(response_text)
            done = next(data for event, data in events if event == "done")
            self.assertEqual(done["seed_mode"], "login")
            self.assertIn(
                seed_generation.LOGIN_SEED_MARKER,
                target_file.read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                seed_generation.VISIT_ONLY_SEED_MARKER,
                target_file.read_text(encoding="utf-8"),
            )

    def test_missing_or_unknown_mode_returns_400_before_loading_project(self):
        with (
            self.auth_disabled,
            patch.object(
                app,
                "get_current_target_system_config",
            ) as get_target_system,
        ):
            missing_response = self.client.post(
                "/api/project-settings/seed/generate",
                json={},
            )
            unknown_response = self.client.post(
                "/api/project-settings/seed/generate",
                json={"mode": "unknown"},
            )

        self.assertEqual(missing_response.status_code, 400)
        self.assertEqual(unknown_response.status_code, 400)
        get_target_system.assert_not_called()


if __name__ == "__main__":
    unittest.main()
