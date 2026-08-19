"""Composition helper for Agent and ordinary module script preparation."""

from __future__ import annotations

from contextlib import contextmanager
import threading

from test_plan_viewer.agent import script_preparation as agent_script_preparation
from test_plan_viewer.script_preparation import agent_adapter
from test_plan_viewer.script_preparation.manager import (
    ModuleScriptPreparationManager,
    ModuleScriptPreparationServices,
)
from test_plan_viewer.script_preparation.repository import (
    ModuleScriptPreparationRepository,
    ModuleScriptPreparationRepositoryDependencies,
)
from test_plan_viewer.script_preparation import operations as module_operations
from test_plan_viewer.script_preparation.target_lease import (
    ScriptTargetBusy,
    acquire_script_target_lease,
)
from test_plan_viewer.web.agent_script_preparation import (
    AgentScriptPreparationWebServices,
    create_agent_script_preparation_blueprint,
)
from test_plan_viewer.web.module_script_preparation import (
    ModuleScriptPreparationWebServices,
    create_module_script_preparation_blueprint,
)


_CONTINUATION_GUARD = threading.Lock()
_CONTINUATION_RUNS = set()


def start_agent_script_preparation_continuation(runtime, run_id, *, recover=False):
    if runtime.agent_has_live_task(run_id) is True:
        return False
    recover_interrupted = False
    if recover:
        run = runtime.get_agent_run_row(run_id) or {}
        step = runtime.get_agent_step_row(run_id, "prepare_scripts") or {}
        if run.get("status") == "cancelling" and run.get("current_step") in {
            "prepare_scripts",
            "create_suite",
            "run_suite",
        }:
            recover_interrupted = True
        elif (
            run.get("current_step") == "prepare_scripts"
            and run.get("status") == "running"
            and step.get("status") == "running"
        ):
            recover_interrupted = True
        else:
            if step.get("status") != "succeeded":
                return False
            if run.get("current_step") == "prepare_scripts" and run.get("status") in {
                "running",
                "awaiting_script_action",
            }:
                runtime.claim_agent_script_preparation_continue(run_id)
                run = runtime.get_agent_run_row(run_id) or {}
            if not (
                run.get("status") == "running"
                and run.get("current_step") in {"create_suite", "run_suite"}
            ):
                return False
    with _CONTINUATION_GUARD:
        if run_id in _CONTINUATION_RUNS:
            return False
        _CONTINUATION_RUNS.add(run_id)
    project = runtime.get_current_project()
    author = runtime.current_platform_author()

    def worker():
        try:
            if recover_interrupted:
                with runtime.use_project_context(project), runtime.use_author_context(
                    author or "platform"
                ):
                    try:
                        agent_adapter.recover_interrupted_for_web(runtime, run_id)
                    except ScriptTargetBusy:
                        return
            else:
                runtime.run_agent_script_preparation_continue_workflow(
                    run_id, project, author
                )
        finally:
            with _CONTINUATION_GUARD:
                _CONTINUATION_RUNS.discard(run_id)

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        with _CONTINUATION_GUARD:
            _CONTINUATION_RUNS.discard(run_id)
        raise
    return True


def _script_content(runtime, item):
    script = item.get("current_script")
    if not isinstance(script, dict):
        return None
    path = runtime.get_script_file(item["module_name"], item["filename"])
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _script_revision(runtime, item):
    path = runtime.get_script_file(item["module_name"], item["filename"])
    asset = runtime.get_test_asset_by_path("script", path)
    return asset.get("current_revision_id") if isinstance(asset, dict) else None


def _reconcile_script_revision(runtime, item):
    path = runtime.get_script_file(item["module_name"], item["filename"])
    if not path.is_file():
        return None
    asset = runtime.sync_script_asset(
        item["module_name"],
        path,
        change_source="recovery",
        message=f"script preparation recovery: {item['module_name']}/{item['filename']}",
    )
    return asset.get("current_revision_id") if isinstance(asset, dict) else None


def _start_module_worker(runtime, manager, method_name, run_id):
    project = runtime.get_current_project()
    author = runtime.current_platform_author()

    def worker():
        with runtime.use_project_context(project), runtime.use_author_context(
            author or "platform"
        ):
            getattr(manager, method_name)(run_id)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


@contextmanager
def _use_runtime_context(runtime, project, author):
    with runtime.use_project_context(project), runtime.use_author_context(
        author or "platform"
    ):
        yield


def _heartbeat_context(runtime):
    project = runtime.get_current_project()
    author = runtime.current_platform_author()
    return _use_runtime_context(runtime, project, author)


def register_script_preparation_blueprints(app, runtime):
    """Register both delivery adapters and return the module manager."""

    app.register_blueprint(
        create_agent_script_preparation_blueprint(
            AgentScriptPreparationWebServices(
                get_script_preparation_snapshot=(
                    lambda run_id: agent_adapter.get_snapshot_for_web(
                        runtime, run_id
                    )
                ),
                get_script_preparation_item=(
                    runtime.get_agent_script_preparation_item_for_web
                ),
                apply_script_preparation_action=(
                    lambda run_id, item_id, **parameters: (
                        agent_adapter.apply_action_for_web(
                            runtime, run_id, item_id, **parameters
                        )
                    )
                ),
                apply_script_preparation_batch_action=(
                    lambda run_id, items, **parameters: (
                        agent_adapter.apply_batch_action_for_web(
                            runtime, run_id, items, **parameters
                        )
                    )
                ),
                start_script_preparation_continue=lambda run_id: (
                    start_agent_script_preparation_continuation(runtime, run_id)
                ),
                claim_script_preparation_continue=(
                    runtime.claim_agent_script_preparation_continue
                ),
                reconcile_script_preparation_items=lambda run_id, item_ids: (
                    agent_adapter.reconcile_items_for_web(
                        runtime, run_id, item_ids
                    )
                ),
                script_preparation_barrier=lambda run_id: (
                    agent_adapter.script_preparation_barrier(runtime, run_id)
                ),
                recover_script_preparation_continue=lambda run_id: (
                    start_agent_script_preparation_continuation(
                        runtime, run_id, recover=True
                    )
                ),
                conflict_type=agent_script_preparation.ScriptPreparationConflict,
            )
        )
    )

    repository = ModuleScriptPreparationRepository(
        ModuleScriptPreparationRepositoryDependencies(
            get_platform_database_config=runtime.get_platform_database_config,
            ensure_platform_database_schema=runtime.ensure_platform_database_schema,
            platform_mysql_connection=runtime.platform_mysql_connection,
            get_script_preparation_runs_table=(
                runtime.get_script_preparation_runs_table
            ),
            get_current_project_id=runtime.get_current_project_id,
            current_time_ms=runtime.current_time_ms,
            validate_uid=runtime.validate_uid,
        )
    )
    task_registry = module_operations.ModulePreparationTaskRegistry(
        repository,
        runtime.cancel_opencode_task,
        target_lease=lambda module_name, filename: acquire_script_target_lease(
            runtime, module_name, filename
        ),
        heartbeat_context_factory=lambda: _heartbeat_context(runtime),
    )
    manager = ModuleScriptPreparationManager(
        ModuleScriptPreparationServices(
            repository=repository,
            generate_script=lambda *args, **kwargs: module_operations.generate_script(
                runtime, task_registry, *args, **kwargs
            ),
            execute_script=lambda *args, **kwargs: module_operations.execute_script(
                runtime, task_registry, *args, **kwargs
            ),
            repair_script=lambda *args, **kwargs: module_operations.repair_script(
                runtime, task_registry, *args, **kwargs
            ),
            analyze_failure=lambda *args, **kwargs: module_operations.analyze_failure(
                runtime, task_registry, *args, **kwargs
            ),
            save_script=runtime.save_agent_prepared_script,
            build_generation_prompt=runtime.build_agent_script_generation_prompt,
            build_repair_prompt=runtime.build_agent_script_repair_prompt,
            resolve_script_filename=(
                lambda plan: runtime.get_generated_script_filename_from_plan_filename(
                    plan["plan_filename"]
                )
            ),
            validate_module_name=runtime.validate_module_name,
            validate_plan_filename=runtime.validate_plan_filename,
            get_plan_file=runtime.get_plan_file,
            current_time_ms=runtime.current_time_ms,
            current_author=runtime.current_platform_author,
            get_project_language=runtime.agent_project_language,
            register_task=task_registry.register,
            cleanup_task=task_registry.cleanup,
            request_task_cancel=task_registry.request_cancel,
            load_script_content=lambda item: _script_content(runtime, item),
            get_script_revision=lambda item: _script_revision(runtime, item),
            reconcile_script_revision=lambda item: _reconcile_script_revision(
                runtime, item
            ),
            target_lease=lambda module_name, filename: acquire_script_target_lease(
                runtime, module_name, filename
            ),
        )
    )
    app.register_blueprint(
        create_module_script_preparation_blueprint(
            ModuleScriptPreparationWebServices(
                manager=manager,
                start_initial=lambda run_id: _start_module_worker(
                    runtime, manager, "run_recovery", run_id
                ),
                start_actions=lambda run_id: _start_module_worker(
                    runtime, manager, "run_actions", run_id
                ),
            )
        )
    )
    return manager


__all__ = [
    "register_script_preparation_blueprints",
    "start_agent_script_preparation_continuation",
]
