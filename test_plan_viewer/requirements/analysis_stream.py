from dataclasses import dataclass
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True)
class RequirementAnalysisDependencies:
    sanitize_job_id: Callable
    read_markdown: Callable
    build_prompt: Callable
    create_job: Callable
    update_job: Callable
    get_job: Callable
    serialize_job: Callable
    append_log: Callable
    finish_job: Callable
    current_time_ms: Callable
    message: Callable
    send_prompt: Callable
    collect_response_text: Callable
    extract_json: Callable
    normalize_analysis: Callable
    save_modules: Callable
    serialize_module: Callable
    sse_payload: Callable
    cancelled_exception: type
    log_tail_limit: int


def stream_requirement_analysis(requirement, job_id, dependencies):
    deps = dependencies
    job_id = deps.sanitize_job_id(
        job_id or f"requirement-analysis-{uuid4().hex}"
    )
    markdown_text = deps.read_markdown(requirement)
    full_prompt = deps.build_prompt(requirement, markdown_text)
    deps.create_job(
        "requirement_analysis",
        job_id=job_id,
        status="queued",
        prompt=full_prompt,
    )

    def emit_status(status, error=None, extra=None):
        payload = {
            "status": status,
            "requirement_uid": requirement.get("requirement_uid"),
            "job_id": job_id,
            "job": deps.serialize_job(deps.get_job(job_id)),
            "error": error,
        }
        if extra:
            payload.update(extra)
        return deps.sse_payload("status", payload)

    def emit_log(message):
        deps.append_log(job_id, f"{message}\n")
        return deps.sse_payload("log", {"message": message})

    try:
        deps.update_job(
            job_id,
            fetch=False,
            status="running",
            started_at=deps.current_time_ms(),
        )
        yield emit_status("running")
        yield emit_log(deps.message("analysis_created"))
        response = deps.send_prompt(
            full_prompt,
            job_id,
            default_agent="requirement-analyst",
            session_title=deps.message("analysis_created"),
        )
        output_text = deps.collect_response_text(response)
        deps.append_log(job_id, output_text[-deps.log_tail_limit :])
        yield deps.sse_payload("delta", {"text": output_text})
        modules = deps.normalize_analysis(deps.extract_json(output_text))
        saved = deps.save_modules(requirement, modules, job_id)
        serialized = [deps.serialize_module(item) for item in saved]
        deps.finish_job(job_id, "succeeded")
        yield emit_status("succeeded", extra={"modules": serialized})
        yield emit_log(deps.message("analysis_completed", count=len(serialized)))
        yield deps.sse_payload(
            "done",
            {"ok": True, "modules": serialized, "job_id": job_id},
        )
    except deps.cancelled_exception as exc:
        cancellation_message = str(exc)
        deps.append_log(job_id, f"{cancellation_message}\n")
        deps.finish_job(job_id, "cancelled", error=cancellation_message)
        yield emit_status("cancelled", cancellation_message)
        yield emit_log(cancellation_message)
        yield deps.sse_payload(
            "done",
            {
                "ok": False,
                "status": "cancelled",
                "error": cancellation_message,
                "job_id": job_id,
            },
        )
    except Exception as exc:
        failure_message = deps.message("analysis_failed", error=exc)
        deps.append_log(job_id, f"{failure_message}\n")
        deps.finish_job(job_id, "failed", error=failure_message)
        yield emit_status("failed", failure_message)
        yield emit_log(failure_message)
        yield deps.sse_payload(
            "done",
            {
                "ok": False,
                "status": "failed",
                "error": failure_message,
                "job_id": job_id,
            },
        )
