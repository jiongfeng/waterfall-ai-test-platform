import unittest
from unittest.mock import Mock

from flask import Flask

from test_plan_viewer.script_preparation.repository import (
    ModuleScriptPreparationConflict,
)
from test_plan_viewer.web.module_script_preparation import (
    ModuleScriptPreparationWebServices,
    create_module_script_preparation_blueprint,
)


def make_app(manager, start_initial=None, start_actions=None):
    application = Flask(__name__)
    services = ModuleScriptPreparationWebServices(
        manager=manager,
        start_initial=start_initial or Mock(),
        start_actions=start_actions or Mock(),
    )
    application.register_blueprint(
        create_module_script_preparation_blueprint(services)
    )
    return application, services


class ModuleScriptPreparationBlueprintTests(unittest.TestCase):
    def test_create_returns_202_and_starts_background_worker(self):
        manager = Mock()
        manager.create_run.return_value = {
            "created": True,
            "run": {"run_id": "script-preparation-1", "status": "queued"},
        }
        manager.get_snapshot.return_value = {
            "run_id": "script-preparation-1",
            "status": "queued",
            "items": [],
        }
        app, services = make_app(manager)

        with app.test_client() as client:
            response = client.post(
                "/api/script-preparation-runs",
                json={
                    "module_name": "登录",
                    "plan_filenames": ["正常登录.md"],
                    "client_request_id": "request-1",
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json["created"])
        manager.create_run.assert_called_once_with(
            module_name="登录",
            plan_filenames=["正常登录.md"],
            client_request_id="request-1",
        )
        services.start_initial.assert_called_once_with("script-preparation-1")

    def test_idempotent_create_does_not_start_duplicate_worker(self):
        manager = Mock()
        manager.create_run.return_value = {
            "created": False,
            "run": {"run_id": "script-preparation-1", "status": "running"},
        }
        manager.get_snapshot.return_value = {
            "run_id": "script-preparation-1",
            "status": "running",
        }
        app, services = make_app(manager)

        with app.test_client() as client:
            response = client.post(
                "/api/script-preparation-runs",
                json={"module_name": "登录", "plan_filenames": ["登录.md"]},
            )

        self.assertEqual(response.status_code, 200)
        services.start_initial.assert_not_called()

    def test_snapshot_item_and_queued_action_contracts(self):
        manager = Mock()
        manager.get_snapshot.return_value = {
            "run_id": "run-1",
            "status": "awaiting_action",
        }
        manager.get_item.return_value = {"item_id": "item-1"}
        manager.apply_or_enqueue_action.return_value = {
            "accepted": True,
            "queued": True,
            "item": {"item_id": "item-1"},
        }
        app, services = make_app(manager)

        with app.test_client() as client:
            snapshot = client.get("/api/script-preparation-runs/run-1")
            item = client.get(
                "/api/script-preparation-runs/run-1/items/item-1"
            )
            action = client.post(
                "/api/script-preparation-runs/run-1/items/item-1/actions",
                json={"action": "execute", "expected_revision_id": 7},
            )

        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(item.status_code, 200)
        self.assertEqual(action.status_code, 202)
        services.start_actions.assert_called_once_with("run-1")
        manager.apply_or_enqueue_action.assert_called_once_with(
            "run-1", "item-1", action="execute", expected_revision_id=7
        )

    def test_batch_preserves_partial_result_and_runs_asynchronously(self):
        manager = Mock()
        manager.enqueue_batch.return_value = {
            "queued": True,
            "accepted": [{"item_id": "item-1"}],
            "rejected": [{"item_id": "item-2", "error": "版本冲突"}],
        }
        app, services = make_app(manager)
        items = [
            {"item_id": "item-1", "expected_revision_id": 1},
            {"item_id": "item-2", "expected_revision_id": 2},
        ]

        with app.test_client() as client:
            response = client.post(
                "/api/script-preparation-runs/run-1/items/batch-actions",
                json={"action": "repair", "items": items},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(response.json["accepted"]), 1)
        self.assertEqual(len(response.json["rejected"]), 1)
        manager.enqueue_batch.assert_called_once_with(
            "run-1", items, action="repair"
        )
        services.start_actions.assert_called_once_with("run-1")

    def test_cancel_and_conflict_statuses(self):
        manager = Mock()
        manager.cancel.return_value = {"run_id": "run-1", "status": "cancelled"}
        app, _services = make_app(manager)
        with app.test_client() as client:
            cancelled = client.post("/api/script-preparation-runs/run-1/cancel")
        self.assertEqual(cancelled.status_code, 202)

        manager.create_run.side_effect = ModuleScriptPreparationConflict(
            "当前模块已有任务。"
        )
        with app.test_client() as client:
            conflict = client.post(
                "/api/script-preparation-runs",
                json={"module_name": "登录", "plan_filenames": ["登录.md"]},
            )
        self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
