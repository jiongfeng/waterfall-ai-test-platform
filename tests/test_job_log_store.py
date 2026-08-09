import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_plan_viewer.infrastructure.job_logs import BufferedJobLogWriter


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class BufferedJobLogWriterTests(unittest.TestCase):
    def test_stream_lifecycle_opens_once_and_preserves_complete_file(self):
        original_open = Path.open
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "job.log"
            chunks = [f"chunk-{index}:测试\n" for index in range(200)]
            with patch.object(
                Path,
                "open",
                autospec=True,
                side_effect=lambda target, *args, **kwargs: original_open(target, *args, **kwargs),
            ) as open_file:
                with BufferedJobLogWriter(path) as writer:
                    for chunk in chunks:
                        writer.append(chunk)
                    snapshot = writer.snapshot()
                self.assertEqual(open_file.call_count, 1)

            expected = "".join(chunks)
            self.assertEqual(path.read_text(encoding="utf-8"), expected)
            self.assertEqual(snapshot.size, len(expected.encode("utf-8")))
            self.assertEqual(snapshot.tail, expected)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                hashlib.sha256(expected.encode("utf-8")).hexdigest(),
            )

    def test_unicode_tail_crossing_byte_boundary_is_valid_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unicode.log"
            text = "前缀" + ("🙂中文" * 30_000) + "结束"
            with BufferedJobLogWriter(path, tail_bytes=100_000) as writer:
                snapshot = writer.append(text)

            self.assertTrue(text.endswith(snapshot.tail))
            self.assertNotIn("�", snapshot.tail)
            self.assertLessEqual(len(snapshot.tail.encode("utf-8")), 100_000)
            self.assertEqual(snapshot.size, len(text.encode("utf-8")))

    def test_snapshot_due_uses_thirty_seconds_or_one_megabyte(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.log"
            with BufferedJobLogWriter(path, clock=clock) as writer:
                writer.append("small")
                self.assertFalse(writer.snapshot_due())
                clock.advance(29.9)
                self.assertFalse(writer.snapshot_due())
                clock.advance(0.1)
                self.assertTrue(writer.snapshot_due())

                checkpoint = writer.snapshot()
                writer.mark_snapshot_persisted(checkpoint)
                self.assertFalse(writer.snapshot_due())

                writer.append(b"x" * (1024 * 1024))
                self.assertTrue(writer.snapshot_due())
                writer.mark_snapshot_persisted(writer.snapshot())
                self.assertFalse(writer.snapshot_due())
                self.assertTrue(writer.snapshot_due(force=True))

    def test_preexisting_log_is_loaded_and_appended(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.log"
            path.write_text("existing🙂", encoding="utf-8")

            with BufferedJobLogWriter(path) as writer:
                snapshot = writer.append("+new中文")

            self.assertEqual(path.read_text(encoding="utf-8"), "existing🙂+new中文")
            self.assertEqual(snapshot.tail, "existing🙂+new中文")
            self.assertEqual(snapshot.size, len("existing🙂+new中文".encode("utf-8")))

    def test_threads_and_distinct_jobs_do_not_mix_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "project-a" / "job.log"
            second_path = Path(directory) / "project-b" / "job.log"
            with BufferedJobLogWriter(first_path) as first, BufferedJobLogWriter(second_path) as second:
                def append_many(writer, marker):
                    for _ in range(500):
                        writer.append(marker)

                threads = [
                    threading.Thread(target=append_many, args=(first, "甲")),
                    threading.Thread(target=append_many, args=(second, "乙")),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                first_snapshot = first.snapshot()
                second_snapshot = second.snapshot()

            self.assertEqual(first_path.read_text(encoding="utf-8"), "甲" * 500)
            self.assertEqual(second_path.read_text(encoding="utf-8"), "乙" * 500)
            self.assertEqual(first_snapshot.size, len(("甲" * 500).encode("utf-8")))
            self.assertEqual(second_snapshot.size, len(("乙" * 500).encode("utf-8")))

    def test_two_writers_for_same_path_reconcile_size_and_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared.log"
            with BufferedJobLogWriter(path) as first, BufferedJobLogWriter(path) as second:
                first.append("first|")
                second.append("second|")
                snapshot = first.append("third")

            self.assertEqual(path.read_text(encoding="utf-8"), "first|second|third")
            self.assertEqual(snapshot.tail, "first|second|third")
            self.assertEqual(snapshot.size, len(b"first|second|third"))

    def test_close_is_idempotent_and_rejects_late_append(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = BufferedJobLogWriter(Path(directory) / "closed.log")
            writer.append("done")
            writer.close()
            writer.close()

            self.assertTrue(writer.closed)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                writer.append("late")


if __name__ == "__main__":
    unittest.main()
