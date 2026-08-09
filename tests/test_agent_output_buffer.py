import unittest

from test_plan_viewer.agent.output_buffer import AgentOutputBatcher


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AgentOutputBatcherTests(unittest.TestCase):
    def test_ten_thousand_single_character_deltas_preserve_exact_text(self):
        clock = FakeClock()
        batcher = AgentOutputBatcher(clock=clock)
        batches = []

        for index in range(10_000):
            batches.extend(batcher.add(chr(ord("a") + index % 26)))
        final = batcher.finish()
        if final:
            batches.append(final)

        expected = "".join(chr(ord("a") + index % 26) for index in range(10_000))
        self.assertEqual("".join(batch.text for batch in batches), expected)
        self.assertEqual(sum(batch.chunk_count for batch in batches), 10_000)
        self.assertLess(len(batches), 10)

    def test_size_and_interval_thresholds_flush(self):
        clock = FakeClock()
        batcher = AgentOutputBatcher(clock=clock)

        self.assertEqual(batcher.add("a" * 4095), [])
        size_batches = batcher.add("b")
        self.assertEqual(len(size_batches), 1)
        self.assertEqual(size_batches[0].byte_size, 4096)
        self.assertEqual(size_batches[0].reason, "size")

        self.assertEqual(batcher.add("tail"), [])
        clock.advance(0.49)
        self.assertIsNone(batcher.flush_due())
        clock.advance(0.02)
        interval_batch = batcher.flush_due()
        self.assertEqual(interval_batch.text, "tail")
        self.assertEqual(interval_batch.reason, "interval")

    def test_max_batch_bytes_split_large_delta_on_utf8_boundaries(self):
        clock = FakeClock()
        text = "🙂" * 5000
        batcher = AgentOutputBatcher(clock=clock)

        batches = batcher.add(text)
        final = batcher.finish()
        if final:
            batches.append(final)

        self.assertEqual("".join(batch.text for batch in batches), text)
        self.assertTrue(all(batch.byte_size <= 16 * 1024 for batch in batches))
        self.assertTrue(all("�" not in batch.text for batch in batches))
        self.assertIn("max_bytes", {batch.reason for batch in batches})

    def test_pending_time_is_measured_from_previous_flush(self):
        clock = FakeClock(10)
        batcher = AgentOutputBatcher(clock=clock)
        clock.advance(1)

        batches = batcher.add("x")

        self.assertEqual([batch.text for batch in batches], ["x"])
        self.assertEqual(batches[0].reason, "interval")

    def test_finish_and_exception_flush_last_partial_batch_and_close(self):
        normal = AgentOutputBatcher(clock=FakeClock())
        normal.add("normal tail")
        final = normal.finish()
        self.assertEqual(final.text, "normal tail")
        self.assertEqual(final.reason, "finish")
        self.assertTrue(normal.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            normal.add("late")

        failed = AgentOutputBatcher(clock=FakeClock())
        failed.add("error tail")
        error = ValueError("business failure")
        final = failed.flush_for_exception(error)
        self.assertEqual(final.text, "error tail")
        self.assertEqual(final.reason, "exception:ValueError")
        self.assertTrue(failed.closed)

    def test_batch_metadata_is_compatible_with_agent_log_events(self):
        clock = FakeClock(4)
        batcher = AgentOutputBatcher(flush_bytes=2, clock=clock)

        batch = batcher.add("ab")[0]

        self.assertEqual(
            batch.metadata(),
            {
                "batched": True,
                "chunk_count": 1,
                "byte_size": 2,
                "first_at": 4.0,
                "last_at": 4.0,
                "flush_reason": "size",
            },
        )

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            AgentOutputBatcher(flush_bytes=0)
        with self.assertRaises(ValueError):
            AgentOutputBatcher(flush_bytes=10, max_batch_bytes=9)
        with self.assertRaises(ValueError):
            AgentOutputBatcher(flush_interval=0)


if __name__ == "__main__":
    unittest.main()
