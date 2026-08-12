import threading
import time


class OpenCodeTaskRegistry:
    def __init__(
        self,
        *,
        tasks,
        lock,
        get_job,
        update_job,
        database_enabled,
        abort_session,
    ):
        self.tasks = tasks
        self.lock = lock
        self.get_job = get_job
        self.update_job = update_job
        self.database_enabled = database_enabled
        self.abort_session = abort_session

    def register(self, job_id, label=None):
        if not job_id:
            return
        now = time.time()
        with self.lock:
            record = self.tasks.get(job_id) or {}
            record.update(
                {
                    "job_id": job_id,
                    "label": label or record.get("label") or "",
                    "updated_at": now,
                    "created_at": record.get("created_at") or now,
                    "cancel_requested": bool(record.get("cancel_requested")),
                    "session_id": record.get("session_id") or "",
                    "last_db_check": float(record.get("last_db_check") or 0),
                }
            )
            self.tasks[job_id] = record

    def set_session(self, job_id, session_id):
        if not job_id or not session_id:
            return False
        with self.lock:
            record = self.tasks.get(job_id) or {
                "job_id": job_id,
                "created_at": time.time(),
                "cancel_requested": False,
            }
            record["session_id"] = session_id
            record["updated_at"] = time.time()
            self.tasks[job_id] = record
            cancelled = bool(record.get("cancel_requested"))
        try:
            self.update_job(job_id, fetch=False, opencode_session_id=session_id)
        except Exception:
            pass
        return cancelled or self.is_cancelled(job_id, force=True)

    def is_cancelled(self, job_id, *, force=False):
        if not job_id:
            return False
        now = time.monotonic()
        with self.lock:
            record = self.tasks.get(job_id) or {}
            if record.get("cancel_requested"):
                return True
            if not force and now - float(record.get("last_db_check") or 0) < 0.5:
                return False
            record["last_db_check"] = now
            self.tasks[job_id] = record
        if not self.database_enabled():
            return False
        try:
            job = self.get_job(job_id)
        except Exception:
            return False
        cancelled = bool(
            job
            and (
                job.get("cancel_requested")
                or job.get("status") in {"cancelling", "cancelled"}
            )
        )
        if cancelled:
            with self.lock:
                record = self.tasks.get(job_id) or {"job_id": job_id}
                record["cancel_requested"] = True
                record["updated_at"] = time.time()
                self.tasks[job_id] = record
        return cancelled

    def cleanup(self, job_id):
        if job_id:
            with self.lock:
                self.tasks.pop(job_id, None)

    def cancel(self, job_id):
        if not job_id:
            raise ValueError("job_id cannot be empty.")
        try:
            job = self.get_job(job_id)
        except Exception:
            job = None
        if job and job.get("status") in {"succeeded", "failed", "cancelled"}:
            return {
                "cancel_requested": bool(job.get("cancel_requested")),
                "aborted": False,
                "session_id": job.get("opencode_session_id") or "",
                "status": job.get("status"),
                "terminal": True,
            }
        try:
            self.update_job(
                job_id,
                fetch=False,
                status="cancelling",
                cancel_requested=True,
            )
        except Exception:
            pass
        with self.lock:
            record = self.tasks.get(job_id) or {
                "job_id": job_id,
                "created_at": time.time(),
                "session_id": "",
            }
            record["cancel_requested"] = True
            record["updated_at"] = time.time()
            self.tasks[job_id] = record
            session_id = record.get("session_id") or (job or {}).get("opencode_session_id") or ""
        result = {
            "cancel_requested": True,
            "aborted": False,
            "session_id": session_id,
            "status": "cancelling",
        }
        if session_id:
            try:
                self.abort_session(session_id)
                result["aborted"] = True
            except Exception as exc:
                result["abort_error"] = str(exc)
        return result


def send_cancellable_prompt(
    prompt,
    job_id,
    *,
    default_agent,
    session_title,
    ensure_prompt_files,
    register_task,
    is_cancelled,
    create_session,
    build_session_payload,
    set_session,
    send_prompt,
    abort_session,
    task_timeout,
    timeout_error,
    cancelled_error,
    cleanup_task,
):
    ensure_prompt_files()
    register_task(job_id, session_title or "OpenCode")
    result = []
    completed = threading.Event()
    session_id = None
    try:
        if is_cancelled(job_id, force=True):
            raise cancelled_error()
        session = create_session(
            build_session_payload(session_title, prompt, default_agent=default_agent)
        )
        session_id = session.get("id")
        if not session_id:
            raise RuntimeError(f"OpenCode did not return a session id: {session}")
        if set_session(job_id, session_id):
            raise cancelled_error()

        def worker():
            try:
                result.append(("response", send_prompt(session_id, prompt, default_agent=default_agent)))
            except BaseException as exc:
                result.append(("error", exc))
            finally:
                completed.set()

        threading.Thread(target=worker, daemon=True).start()
        timeout_seconds = task_timeout()
        deadline = time.monotonic() + timeout_seconds
        while not completed.wait(0.1):
            if is_cancelled(job_id):
                try:
                    abort_session(session_id)
                except Exception:
                    pass
                raise cancelled_error()
            if time.monotonic() >= deadline:
                try:
                    abort_session(session_id)
                except Exception:
                    pass
                raise RuntimeError(timeout_error(timeout_seconds))
        if is_cancelled(job_id, force=True):
            raise cancelled_error()
        kind, value = result[0]
        if kind == "error":
            raise value
        return value
    finally:
        cleanup_task(job_id)
