import unittest
from unittest.mock import Mock, call

from flask import Flask

from test_plan_viewer.auth import model as auth_model
from test_plan_viewer.web.agent_script_preparation import (
    AgentScriptPreparationConflict,
    AgentScriptPreparationWebServices,
    SCRIPT_PREPARATION_ACTIONS,
    create_agent_script_preparation_blueprint,
)


def make_services(**overrides):
    item = {
        "item_id": "script-1",
        "status": "awaiting_human",
        "history": [],
    }
    values = {
        "get_script_preparation_snapshot": Mock(
            return_value={"run_id": "agent-1", "items": [item]}
        ),
        "get_script_preparation_item": Mock(return_value=item),
        "apply_script_preparation_action": Mock(return_value=item),
        "apply_script_preparation_batch_action": Mock(
            return_value={
                "accepted": [{"item_id": "script-1"}],
                "rejected": [],
            }
        ),
        "start_script_preparation_continue": Mock(),
        "claim_script_preparation_continue": Mock(return_value=True),
        "recover_script_preparation_continue": Mock(),
    }
    values.update(overrides)
    return AgentScriptPreparationWebServices(**values)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(
        create_agent_script_preparation_blueprint(services)
    )
    return application


class AgentScriptPreparationBlueprintTests(unittest.TestCase):
    def test_snapshot_and_item_success_contracts(self):
        services = make_services()
        with make_app(services).test_client() as client:
            snapshot = client.get(
                "/api/agent/runs/agent-1/script-preparation"
            )
            item = client.get(
                "/api/agent/runs/agent-1/script-items/script-1"
            )

        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json["snapshot"]["run_id"], "agent-1")
        self.assertIsNone(snapshot.json["error"])
        self.assertEqual(item.status_code, 200)
        self.assertEqual(item.json["item"]["item_id"], "script-1")
        self.assertIsNone(item.json["error"])
        services.get_script_preparation_snapshot.assert_called_once_with(
            "agent-1"
        )
        services.recover_script_preparation_continue.assert_called_once_with(
            "agent-1"
        )
        services.get_script_preparation_item.assert_called_once_with(
            "agent-1", "script-1"
        )

    def test_missing_snapshot_or_item_returns_not_found(self):
        services = make_services(
            get_script_preparation_snapshot=Mock(return_value=None),
            get_script_preparation_item=Mock(return_value=None),
            apply_script_preparation_action=Mock(return_value=None),
        )
        with make_app(services).test_client() as client:
            snapshot = client.get(
                "/api/agent/runs/missing/script-preparation"
            )
            item = client.get(
                "/api/agent/runs/agent-1/script-items/missing"
            )
            action = client.post(
                "/api/agent/runs/agent-1/script-items/missing/actions",
                json={"action": "execute", "expected_revision_id": 1},
            )

        self.assertEqual(snapshot.status_code, 404)
        self.assertEqual(snapshot.json["error"], "Agent 任务不存在。")
        self.assertEqual(item.status_code, 404)
        self.assertEqual(item.json["error"], "脚本项不存在。")
        self.assertEqual(action.status_code, 404)

    def test_all_actions_forward_only_the_supported_payload_fields(self):
        for action in sorted(SCRIPT_PREPARATION_ACTIONS):
            with self.subTest(action=action):
                apply_action = Mock(
                    return_value={
                        "item_id": "script-1",
                        "latest_action": action,
                    }
                )
                services = make_services(
                    apply_script_preparation_action=apply_action
                )
                payload = {
                    "action": action,
                    "original_prompt": "原始 Prompt",
                    "supplemental_prompt": "AI 补充 Prompt",
                    "content": "test('edited', async () => {});",
                    "execute_after_save": False,
                    "expected_revision_id": 7,
                    "ignored_field": "不得传给领域服务",
                }

                with make_app(services).test_client() as client:
                    response = client.post(
                        "/api/agent/runs/agent-1/"
                        "script-items/script-1/actions",
                        json=payload,
                    )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json["accepted"])
                self.assertFalse(response.json["should_continue"])
                self.assertEqual(
                    response.json["item"]["latest_action"], action
                )
                apply_action.assert_called_once_with(
                    "agent-1",
                    "script-1",
                    action=action,
                    original_prompt="原始 Prompt",
                    supplemental_prompt="AI 补充 Prompt",
                    content="test('edited', async () => {});",
                    execute_after_save=False,
                    expected_revision_id=7,
                )
                services.start_script_preparation_continue.assert_not_called()

    def test_actions_reconcile_external_versions_before_mutation(self):
        reconcile = Mock()
        services = make_services(
            reconcile_script_preparation_items=reconcile
        )
        with make_app(services).test_client() as client:
            action = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute", "expected_revision_id": 1},
            )
            batch = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={"action": "abandon", "item_ids": ["script-1"]},
            )
        self.assertEqual(action.status_code, 200)
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(
            reconcile.call_args_list,
            [
                call("agent-1", ["script-1"]),
                call("agent-1", ["script-1"]),
            ],
        )

    def test_action_rejects_unknown_action_and_non_object_json(self):
        services = make_services()
        with make_app(services).test_client() as client:
            missing = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={},
            )
            unsupported = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "delete"},
            )
            non_object = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json=["execute"],
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(non_object.status_code, 400)
        self.assertIn("action 必须", unsupported.json["error"])
        self.assertIn("JSON 对象", non_object.json["error"])
        services.apply_script_preparation_action.assert_not_called()

    def test_batch_action_preserves_per_item_prompts_and_partial_result(self):
        apply_batch = Mock(
            return_value={
                "accepted": [
                    {
                        "item_id": "script-1",
                        "status": "queued",
                    }
                ],
                "rejected": [
                    {
                        "item_id": "script-2",
                        "error": "脚本正在执行。",
                    }
                ],
            }
        )
        services = make_services(
            apply_script_preparation_batch_action=apply_batch
        )
        items = [
            {
                "item_id": "script-1",
                "original_prompt": "脚本 1 原 Prompt",
                "supplemental_prompt": "脚本 1 AI Prompt",
            },
            {
                "item_id": "script-2",
                "original_prompt": "脚本 2 原 Prompt",
                "supplemental_prompt": "脚本 2 AI Prompt",
            },
        ]

        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={
                    "action": "regenerate",
                    "items": items,
                    "execute_after_save": True,
                    "expected_revision_id": 9,
                    "ignored_field": "ignore",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["should_continue"])
        self.assertEqual(
            [item["item_id"] for item in response.json["accepted"]],
            ["script-1"],
        )
        self.assertEqual(
            [item["item_id"] for item in response.json["rejected"]],
            ["script-2"],
        )
        apply_batch.assert_called_once_with(
            "agent-1",
            items,
            action="regenerate",
            execute_after_save=True,
            expected_revision_id=9,
        )
        services.start_script_preparation_continue.assert_not_called()

    def test_batch_accepts_item_ids_and_normalizes_item_results(self):
        services = make_services(
            apply_script_preparation_batch_action=Mock(
                return_value=[
                    {"item_id": "script-1", "accepted": True},
                    {
                        "item_id": "script-2",
                        "accepted": False,
                        "error": "状态不允许。",
                    },
                ]
            )
        )
        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={
                    "action": "execute",
                    "item_ids": ["script-1", "script-2"],
                    "expected_revision_id": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["accepted"]), 1)
        self.assertEqual(len(response.json["rejected"]), 1)

    def test_single_action_starts_continue_worker_and_returns_202(self):
        start_continue = Mock()
        services = make_services(
            apply_script_preparation_action=Mock(
                return_value={
                    "item": {
                        "item_id": "script-1",
                        "status": "ready",
                    },
                    "should_continue": True,
                }
            ),
            start_script_preparation_continue=start_continue,
        )

        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute", "expected_revision_id": 1},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json["should_continue"])
        self.assertTrue(response.json["continuation_claimed"])
        self.assertEqual(response.json["item"]["status"], "ready")
        services.claim_script_preparation_continue.assert_called_once_with(
            "agent-1"
        )
        start_continue.assert_called_once_with("agent-1")

    def test_continue_worker_starts_only_after_revision_barrier_releases(self):
        events = []

        class Barrier:
            def __enter__(inner_self):
                events.append("enter")
                return inner_self

            def __exit__(inner_self, *_args):
                events.append("exit")

        services = make_services(
            apply_script_preparation_action=Mock(
                return_value={
                    "item": {"item_id": "script-1", "status": "ready"},
                    "should_continue": True,
                }
            ),
            script_preparation_barrier=lambda _run_id: Barrier(),
            claim_script_preparation_continue=Mock(
                side_effect=lambda _run_id: events.append("claim") or True
            ),
            start_script_preparation_continue=Mock(
                side_effect=lambda _run_id: events.append("start")
            ),
        )
        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute", "expected_revision_id": 1},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(events, ["enter", "claim", "exit", "start"])

    def test_mutating_action_requires_revision_key_but_preserves_explicit_null(self):
        services = make_services()
        with make_app(services).test_client() as client:
            missing = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute"},
            )
            explicit_null = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute", "expected_revision_id": None},
            )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(explicit_null.status_code, 200)
        self.assertIsNone(
            services.apply_script_preparation_action.call_args.kwargs[
                "expected_revision_id"
            ]
        )

    def test_batch_action_starts_continue_worker_only_when_requested(self):
        start_continue = Mock()
        services = make_services(
            apply_script_preparation_batch_action=Mock(
                return_value={
                    "accepted": [{"item_id": "script-1"}],
                    "rejected": [],
                    "should_continue": True,
                }
            ),
            start_script_preparation_continue=start_continue,
        )

        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={"action": "abandon", "items": ["script-1"]},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json["should_continue"])
        self.assertTrue(response.json["continuation_claimed"])
        services.claim_script_preparation_continue.assert_called_once_with(
            "agent-1"
        )
        start_continue.assert_called_once_with("agent-1")

    def test_already_claimed_continuation_does_not_start_a_second_worker(self):
        start_continue = Mock()
        claim_continue = Mock(return_value=False)
        services = make_services(
            apply_script_preparation_action=Mock(
                return_value={
                    "item": {"item_id": "script-1", "status": "ready"},
                    "should_continue": True,
                }
            ),
            start_script_preparation_continue=start_continue,
            claim_script_preparation_continue=claim_continue,
        )

        with make_app(services).test_client() as client:
            response = client.post(
                "/api/agent/runs/agent-1/script-items/script-1/actions",
                json={"action": "execute", "expected_revision_id": 1},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json["should_continue"])
        self.assertFalse(response.json["continuation_claimed"])
        claim_continue.assert_called_once_with("agent-1")
        start_continue.assert_not_called()

    def test_batch_requires_items_and_valid_result_lists(self):
        services = make_services()
        with make_app(services).test_client() as client:
            empty = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={"action": "abandon", "items": []},
            )

        self.assertEqual(empty.status_code, 400)
        self.assertIn("非空列表", empty.json["error"])
        services.apply_script_preparation_batch_action.assert_not_called()

        invalid_services = make_services(
            apply_script_preparation_batch_action=Mock(
                return_value={"accepted": {}, "rejected": []}
            )
        )
        with make_app(invalid_services).test_client() as client:
            invalid = client.post(
                "/api/agent/runs/agent-1/script-items/batch-actions",
                json={
                    "action": "execute",
                    "items": ["script-1"],
                    "expected_revision_id": 1,
                },
            )

        self.assertEqual(invalid.status_code, 500)
        self.assertIn("accepted/rejected", invalid.json["error"])

    def test_expected_errors_map_to_400_404_409_and_500(self):
        cases = (
            (ValueError("参数错误"), 400, "参数错误"),
            (FileNotFoundError("脚本项不存在"), 404, "脚本项不存在"),
            (
                AgentScriptPreparationConflict("版本已变化"),
                409,
                "版本已变化",
            ),
            (RuntimeError("database offline"), 500, "执行脚本项操作失败"),
        )
        for error, expected_status, expected_message in cases:
            with self.subTest(expected_status=expected_status):
                services = make_services(
                    apply_script_preparation_action=Mock(
                        side_effect=error
                    )
                )
                with make_app(services).test_client() as client:
                    response = client.post(
                        "/api/agent/runs/agent-1/"
                        "script-items/script-1/actions",
                        json={"action": "execute", "expected_revision_id": 1},
                    )

                self.assertEqual(response.status_code, expected_status)
                self.assertIn(expected_message, response.json["error"])

    def test_blueprint_registers_exactly_four_routes(self):
        application = make_app(make_services())
        routes = {
            (method, rule.rule)
            for rule in application.url_map.iter_rules()
            if rule.endpoint != "static"
            for method in rule.methods
            if method in {"GET", "POST"}
        }

        self.assertEqual(
            routes,
            {
                (
                    "GET",
                    "/api/agent/runs/<run_id>/script-preparation",
                ),
                (
                    "GET",
                    "/api/agent/runs/<run_id>/script-items/<item_id>",
                ),
                (
                    "POST",
                    "/api/agent/runs/<run_id>/"
                    "script-items/<item_id>/actions",
                ),
                (
                    "POST",
                    "/api/agent/runs/<run_id>/"
                    "script-items/batch-actions",
                ),
            },
        )

    def test_all_four_routes_require_agent_menu_permission(self):
        endpoint_methods = {
            (
                "agent_script_preparation.get_script_preparation",
                "GET",
            ),
            ("agent_script_preparation.get_script_item", "GET"),
            (
                "agent_script_preparation.apply_script_item_action",
                "POST",
            ),
            (
                "agent_script_preparation.apply_script_item_batch_action",
                "POST",
            ),
        }

        for endpoint, method in endpoint_methods:
            with self.subTest(endpoint=endpoint, method=method):
                self.assertEqual(
                    auth_model.required_permissions_for_endpoint(
                        endpoint, method
                    ),
                    frozenset({"menu.agent"}),
                )

    def test_blueprint_requires_explicit_services(self):
        with self.assertRaisesRegex(
            TypeError, "AgentScriptPreparationWebServices"
        ):
            create_agent_script_preparation_blueprint(object())


if __name__ == "__main__":
    unittest.main()
