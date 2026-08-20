"""HTTP delivery and deterministic generation for project Seed scripts."""

from dataclasses import dataclass
from typing import Callable
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context

from test_plan_viewer.generation import seed as seed_generation
from test_plan_viewer.web.sse import sse_payload


@dataclass(frozen=True)
class SeedWebServices:
    """Application capabilities used by the Seed generation endpoint."""

    validate_seed_mode: Callable
    get_target_base_url: Callable
    get_current_target_system_config: Callable
    get_seed_script_file: Callable
    get_seed_script_relative_path: Callable
    build_seed_generation_prompt: Callable
    create_test_job: Callable
    stream_plan_generation: Callable
    build_setup_targets: Callable
    file_hash: Callable
    validate_generated_script_content: Callable
    write_file_atomically: Callable
    sync_script_asset: Callable
    persist_seed_mode: Callable
    serialize_asset: Callable
    list_asset_revisions: Callable
    serialize_revision: Callable
    agent_message: Callable
    module_name: str
    script_filename: str


def visit_only_seed_response(services, base_url, target_file):
    payload = seed_generation.generate_visit_only_seed(
        services,
        base_url,
        target_file,
    )
    success_message = services.agent_message(
        "seed_generation_success",
        target=target_file,
    )
    events = [
        sse_payload(
            "status",
            {
                "status": "succeeded",
                "module_name": services.module_name,
                "target_path": str(target_file),
                **payload,
            },
        ),
        sse_payload("log", {"message": success_message, "job_id": None}),
        sse_payload("done", {"ok": True, **payload}),
    ]
    response = Response(events, mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def generate_project_seed_response(services):
    """Generate either a deterministic visit Seed or an LLM login Seed."""

    lease = None
    release_on_exit = True
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not str(
            payload.get("mode") or ""
        ).strip():
            raise ValueError(
                "mode is required and must be 'visit_only' or 'login'."
            )
        seed_mode = services.validate_seed_mode(payload.get("mode"))
        target_file = services.get_seed_script_file()
        lease = seed_generation.acquire_seed_generation_lease(target_file)
        if lease is None:
            return jsonify(
                {"error": "当前项目正在生成 Seed，请等待完成后重试。"}
            ), 409
        if seed_mode == "visit_only":
            return visit_only_seed_response(
                services,
                services.get_target_base_url(),
                target_file,
            )

        target_system = services.get_current_target_system_config()
        completion_probe = seed_generation.SeedCompletionProbe(
            target_file,
            services.file_hash,
        )
        full_prompt = services.build_seed_generation_prompt(
            target_system,
            target_file,
        )
        job_id = f"generator-{uuid.uuid4().hex}"
        try:
            services.create_test_job(
                "generator",
                job_id=job_id,
                status="queued",
                prompt=full_prompt,
            )
        except Exception as exc:
            return jsonify(
                {"error": f"创建 Seed 生成任务失败：{exc}"}
            ), 500

        def has_seed_output():
            return completion_probe.check()

        def finalize_login_seed():
            return seed_generation.finalize_seed_payload(
                services,
                target_file,
                seed_mode,
                job_id=job_id,
            )

        generation_stream = services.stream_plan_generation(
            services.module_name,
            full_prompt,
            target_file,
            completion_check=has_seed_output,
            target_label=str(target_file),
            session_title=services.agent_message(
                "seed_generation_title"
            ),
            success_message=services.agent_message(
                "seed_generation_success",
                target=target_file,
            ),
            default_agent="playwright-test-generator",
            setup_targets=services.build_setup_targets(
                module_name=services.module_name,
                filename=services.script_filename,
            ),
            success_payload_factory=finalize_login_seed,
            job_id=job_id,
        )
        response = Response(
            stream_with_context(
                seed_generation.release_lease_after(
                    generation_stream,
                    lease,
                )
            ),
            mimetype="text/event-stream",
        )
        response.call_on_close(lease.release)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        release_on_exit = False
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify(
            {"error": f"生成 Seed 脚本失败：{exc}"}
        ), 500
    finally:
        if lease is not None and release_on_exit:
            lease.release()


def create_seed_blueprint(services):
    """Create the project Seed generation delivery boundary."""

    if not isinstance(services, SeedWebServices):
        raise TypeError("services must be a SeedWebServices instance")
    blueprint = Blueprint("seed", __name__)

    @blueprint.post("/api/project-settings/seed/generate")
    def generate_project_seed():
        return generate_project_seed_response(services)

    return blueprint


__all__ = [
    "SeedWebServices",
    "create_seed_blueprint",
    "generate_project_seed_response",
]
