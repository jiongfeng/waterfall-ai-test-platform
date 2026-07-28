import unittest

from test_plan_viewer.process_output import (
    decode_process_output,
    get_process_output_encoding_candidates,
    score_decoded_process_output,
    summarize_process_output,
)


class ProcessOutputTests(unittest.TestCase):
    def test_decodes_utf8_and_windows_chinese_output(self):
        message = "测试执行成功"

        self.assertEqual(decode_process_output(message.encode("utf-8")), message)
        self.assertEqual(decode_process_output(message.encode("gb18030")), message)

    def test_encoding_candidates_are_normalized_and_unique(self):
        candidates = get_process_output_encoding_candidates()
        normalized = [candidate.lower().replace("_", "-") for candidate in candidates]

        self.assertEqual(normalized[:2], ["utf-8", "utf-8-sig"])
        self.assertEqual(len(normalized), len(set(normalized)))

    def test_mojibake_scores_worse_than_clean_text(self):
        self.assertGreater(
            score_decoded_process_output("锟斤拷\ufffd"),
            score_decoded_process_output("测试执行成功"),
        )

    def test_summary_combines_streams_and_keeps_the_tail(self):
        summary = summarize_process_output(
            "stdout line",
            "stderr ending".encode("utf-8"),
            limit=18,
        )

        self.assertEqual(summary, "line\nstderr ending")


if __name__ == "__main__":
    unittest.main()
