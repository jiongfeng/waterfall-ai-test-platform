import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app


class OpenCodeTaskCancellationTests(unittest.TestCase):
    def tearDown(self):
        app.OPENCODE_TASKS.clear()

    def test_cancel_uses_persisted_session_and_marks_job_cancelling(self):
        job = {
            "job_id": "planner-1",
            "status": "running",
            "cancel_requested": 0,
            "opencode_session_id": "session-1",
        }
        with (
            patch.object(app, "get_test_job", return_value=job),
            patch.object(app, "update_test_job") as update_job,
            patch.object(app, "abort_opencode_session", return_value=True) as abort,
        ):
            result = app.cancel_opencode_task("planner-1")

        self.assertTrue(result["cancel_requested"])
        self.assertTrue(result["aborted"])
        self.assertEqual(result["status"], "cancelling")
        update_job.assert_called_once_with(
            "planner-1",
            fetch=False,
            status="cancelling",
            cancel_requested=True,
        )
        abort.assert_called_once_with("session-1")

    def test_requirement_analysis_publishes_cancelled_instead_of_failed(self):
        requirement = {
            "requirement_uid": "requirement-1",
            "title": "需求",
        }

        def message(key, **kwargs):
            if key == "task_cancelled_generic":
                return "用户终止了任务。"
            return key

        with (
            patch.object(app, "read_requirement_markdown", return_value="# 需求"),
            patch.object(app, "build_requirement_analysis_prompt", return_value="prompt"),
            patch.object(app, "create_test_job"),
            patch.object(app, "update_test_job"),
            patch.object(app, "get_test_job", return_value=None),
            patch.object(app, "append_test_job_log"),
            patch.object(app, "finish_test_job") as finish_job,
            patch.object(app, "agent_message", side_effect=message),
            patch.object(
                app,
                "send_opencode_prompt_cancellable",
                side_effect=app.OpencodeTaskCancelled("用户终止了任务。"),
            ),
        ):
            chunks = list(
                app.stream_requirement_analysis(
                    requirement,
                    job_id="requirement-analysis-1",
                )
            )

        events = list(app.parse_sse_text_blocks("".join(chunks)))
        self.assertTrue(
            any(
                event == "status" and data.get("status") == "cancelled"
                for event, data in events
            )
        )
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["status"], "cancelled")
        finish_job.assert_called_once_with(
            "requirement-analysis-1",
            "cancelled",
            error="用户终止了任务。",
        )

    def test_stream_cancellation_runs_file_cleanup(self):
        cleanup = Mock()
        with (
            patch.object(app, "register_opencode_task"),
            patch.object(app, "is_opencode_task_cancelled", return_value=True),
            patch.object(app, "cleanup_opencode_task"),
            patch.object(app, "agent_message", return_value="用户终止了任务。"),
        ):
            chunks = list(
                app.stream_plan_generation(
                    "模块",
                    "prompt",
                    Path("/tmp/not-created.md"),
                    setup_targets=[],
                    cancel_job_id="planner-1",
                    cancel_cleanup=cleanup,
                )
            )

        cleanup.assert_called_once_with()
        events = list(app.parse_sse_text_blocks("".join(chunks)))
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
