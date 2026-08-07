import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import app as legacy_app
from test_plan_viewer.generation import prompts
from test_plan_viewer.projects import model as project_model
from test_plan_viewer.web.projects import (
    ProjectWebServices,
    create_projects_blueprint,
)


class ProjectLanguageTests(unittest.TestCase):
    def test_legacy_language_defaults_when_project_is_unavailable(self):
        with (
            patch.object(
                legacy_app,
                "current_context_project",
                return_value=None,
            ),
            patch.object(
                legacy_app,
                "get_current_project",
                side_effect=RuntimeError("project unavailable"),
            ),
        ):
            self.assertEqual(
                legacy_app.get_current_project_language(),
                "en",
            )

    def test_legacy_project_serializes_to_english_default(self):
        project = project_model.serialize_project({"project_key": "legacy"})
        self.assertEqual(project["language"], "en")

    def test_invalid_project_language_is_rejected(self):
        with self.assertRaises(ValueError):
            project_model.normalize_create_project_payload(
                {"project_key": "demo", "name": "Demo", "language": "fr"},
                parse_project_key=lambda value, _name: value,
                parse_project_path_segment=lambda value, default, _name: value or default,
            )

    def test_language_endpoint_requires_built_in_admin(self):
        app = Flask(__name__)
        services = ProjectWebServices(
            list_projects=lambda: [],
            serialize_project=lambda value, **_kwargs: value,
            get_current_project=lambda: {},
            get_project_workspace_root_text=lambda: "",
            create_project=lambda _payload: {},
            parse_target_system_config=lambda value: value,
            get_database_baseline_config=lambda: {},
            get_plan_generation_config=lambda: {},
            parse_database_baseline_config=lambda value: value,
            parse_plan_generation_config=lambda value: value,
            update_project_settings=lambda *_args: {},
            update_project_language=lambda _language: {"language": "en"},
            can_manage_project_language=lambda: False,
            serialize_coverage_profiles=lambda: [],
            get_seed_script_relative_path=lambda: "tests/seed/seed.spec.ts",
        )
        app.register_blueprint(create_projects_blueprint(services))
        response = app.test_client().put("/api/project-language", json={"language": "en"})
        self.assertEqual(response.status_code, 403)

    def test_english_generation_prompt_uses_english_naming_preference(self):
        dependencies = prompts.PromptDependencies(
            get_database_baseline_config=lambda: {"enabled": False},
            get_workspace_relative_path=lambda path: f"specs/{Path(path).name}",
            parse_target_system_config=lambda value: value,
            build_target_login_url=lambda value: "",
            get_seed_script_relative_path=lambda: "tests/seed/seed.spec.ts",
            get_script_test_relative_path=lambda module, filename: f"tests/{module}/{filename}",
            get_project_language=lambda: "en",
        )
        prompt = prompts.build_generation_prompt("Create a plan.", "/tmp/example.md", dependencies)
        self.assertIn("Naming preference", prompt)
        self.assertIn("Test plan save location", prompt)
        self.assertNotIn("命名强约束", prompt)
