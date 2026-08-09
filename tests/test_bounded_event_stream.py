import queue
import threading
import time
import unittest

from test_plan_viewer.generation.event_stream import BoundedSseReader


class ListResponse:
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


class BlockingResponse:
    def __init__(self, *, unblock_on_close=True):
        self.closed = threading.Event()
        self.release = threading.Event()
        self.unblock_on_close = unblock_on_close

    def __iter__(self):
        release = self.closed if self.unblock_on_close else self.release
        release.wait()
        if False:
            yield b""

    def close(self):
        self.closed.set()


class ErrorResponse:
    def __iter__(self):
        raise RuntimeError("stream failed")
        yield b""

    def close(self):
        pass


class BoundedSseReaderTests(unittest.TestCase):
    def test_reader_emits_parsed_events_then_explicit_eof(self):
        response = ListResponse(
            [
                b"event: message\n",
                b'data: {"value":1}\n',
                b"\n",
            ]
        )
        with BoundedSseReader(response, max_queue_size=2) as reader:
            event = reader.get(timeout=1)
            eof = reader.get(timeout=1)

            self.assertTrue(reader.thread.daemon)
            self.assertEqual(event.kind, "event")
            self.assertEqual(event.event, "message")
            self.assertEqual(event.data, '{"value":1}')
            self.assertEqual(eof.kind, "eof")
        self.assertTrue(response.closed)

    def test_poll_timeout_returns_control_to_the_caller(self):
        response = BlockingResponse()
        reader = BoundedSseReader(response, join_timeout=0.2).start()
        started_at = time.monotonic()

        self.assertIsNone(reader.poll(timeout=0.02))
        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertTrue(reader.close())
        self.assertFalse(reader.is_alive)

    def test_get_preserves_queue_empty_semantics(self):
        response = BlockingResponse()
        reader = BoundedSseReader(response).start()
        try:
            with self.assertRaises(queue.Empty):
                reader.get(timeout=0.01)
        finally:
            reader.close()

    def test_reader_reports_parsing_or_transport_errors_explicitly(self):
        with BoundedSseReader(ErrorResponse()) as reader:
            item = reader.get(timeout=1)

            self.assertEqual(item.kind, "error")
            self.assertIsInstance(item.error, RuntimeError)
            self.assertEqual(str(item.error), "stream failed")

    def test_queue_never_exceeds_the_configured_bound(self):
        lines = []
        for index in range(20):
            lines.extend([f"data: {index}\n".encode(), b"\n"])
        response = ListResponse(lines)
        reader = BoundedSseReader(response, max_queue_size=2).start()
        try:
            deadline = time.monotonic() + 1
            while reader.pending_count < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(reader.pending_count, 2)
            self.assertLessEqual(reader.pending_count, reader.max_queue_size)
        finally:
            reader.close()

    def test_close_uses_a_bounded_join_even_if_response_does_not_unblock(self):
        response = BlockingResponse(unblock_on_close=False)
        reader = BoundedSseReader(response, join_timeout=0.01).start()
        started_at = time.monotonic()

        self.assertFalse(reader.close())
        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertTrue(reader.thread.daemon)

        response.release.set()
        self.assertTrue(reader.join(timeout=1))


if __name__ == "__main__":
    unittest.main()
