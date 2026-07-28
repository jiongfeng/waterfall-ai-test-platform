import unittest
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.setup.model import SetupPreparationError
from test_plan_viewer.web.setup import (
    SetupWebServices,
    create_setup_blueprint,
)


def make_services(**overrides):
    script = {"uid": "restore", "name": "恢复数据库"}
    binding = {"uid": "binding", "script_uid": "restore"}
    values = {
        "list_scripts": Mock(return_value=[script]),
        "save_script": Mock(return_value=script),
        "delete_script": Mock(return_value=True),
        "list_bindings": Mock(return_value=[binding]),
        "save_binding": Mock(return_value=binding),
        "delete_binding": Mock(return_value=True),
        "list_runs": Mock(return_value=[]),
        "get_script": Mock(return_value=script),
        "get_current_project": Mock(
            return_value={"project_key": "demo"}
        ),
        "execute_profile": Mock(
            return_value={"uid": "run-1", "status": "succeeded"}
        ),
        "preparation_error_type": SetupPreparationError,
        "binding_target_types": {
            "project",
            "test_suite",
            "script",
        },
    }
    values.update(overrides)
    return SetupWebServices(**values)


def create_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_setup_blueprint(services)
    )
    return application


class SetupBlueprintTests(unittest.TestCase):
    def test_crud_routes_preserve_payload_and_status_contracts(self):
        services = make_services()
        client = create_app(services).test_client()

        self.assertEqual(
            client.get("/api/setup-scripts").get_json()["scripts"][0]["uid"],
            "restore",
        )
        created = client.post(
            "/api/setup-scripts",
            json={"name": "恢复数据库"},
        )
        updated = client.put(
            "/api/setup-scripts/restore",
            json={"name": "新名称"},
        )
        deleted = client.delete(
            "/api/setup-scripts/restore"
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            services.save_script.call_args_list[0].args,
            ({"name": "恢复数据库"},),
        )
        self.assertEqual(
            services.save_script.call_args_list[1].args,
            ({"name": "新名称"}, "restore"),
        )

    def test_missing_records_and_validation_errors_are_mapped(self):
        services = make_services(
            save_binding=Mock(return_value=None),
            delete_script=Mock(return_value=False),
            list_runs=Mock(side_effect=ValueError("bad limit")),
        )
        client = create_app(services).test_client()

        self.assertEqual(
            client.put(
                "/api/setup-bindings/missing",
                json={},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.delete(
                "/api/setup-scripts/missing"
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                "/api/setup-runs?limit=bad"
            ).status_code,
            400,
        )

    def test_trial_run_builds_the_stable_resolution(self):
        services = make_services()
        client = create_app(services).test_client()

        response = client.post(
            "/api/setup-scripts/restore/trial-run",
            json={
                "target_type": "test_suite",
                "target_key": "suite-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        resolution = services.execute_profile.call_args.args[0]
        self.assertEqual(
            resolution["target"],
            {
                "scope_type": "test_suite",
                "scope_key": "suite-1",
            },
        )

    def test_trial_run_exposes_preparation_failure_summary(self):
        error = SetupPreparationError(
            "恢复失败",
            {"uid": "run-1", "status": "failed"},
        )
        services = make_services(
            execute_profile=Mock(side_effect=error)
        )
        response = create_app(services).test_client().post(
            "/api/setup-scripts/restore/trial-run",
            json={},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["run"]["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
