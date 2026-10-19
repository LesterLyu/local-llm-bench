import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from local_llm_bench.cli import main
from local_llm_bench.client import BenchError, Client
from local_llm_bench.protocol import bundle, plan
from local_llm_bench.runner import from_log, measured, summarize


class Protocol(unittest.TestCase):
    def test_sequence_and_sampling_are_frozen(self):
        cases = plan()
        self.assertEqual([(c.name, c.repetition, c.max_tokens, c.temperature) for c in cases], [
            ("conv", 1, 1200, 0.6), ("struct", 1, 8192, 0.0), ("prose", 1, 1200, 0.6), ("pp16k", 1, 32, 0.0),
            ("conv", 2, 1200, 0.6), ("struct", 2, 8192, 0.0), ("prose", 2, 1200, 0.6), ("pp16k", 2, 32, 0.0),
            ("code", 1, 1200, 0.6), ("code", 2, 1200, 0.6)])
        self.assertEqual(cases[3].prompt[:8], "1 4 6 7\n")
        self.assertEqual(cases[7].prompt[:8], "2 5 7 8\n")
        payload = cases[0].payload("test-model")
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual([payload[k] for k in ("top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty")],
                         [0.95, 20, 0.0, 0.0, 1.0])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0]["content"],
                         "Explain how a B-tree differs from an LSM tree, with a worked example of each. About 600 words.")
        self.assertNotIn("seed", payload)

    def test_bundled_data_and_offline_plan(self):
        data, fingerprint = bundle()
        self.assertEqual(data["protocol"], "local-chat-v1")
        self.assertEqual(fingerprint, "98ccb4cdf60bf2d0bf36674b29d1984192199da1ae9d61949b389bb0b6acd156")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["run", "--suite", "local-chat-v1", "--dry-run"]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["canonical_rounds"])
        self.assertEqual(len(report["requests"]), 10)

    def test_fixture_fingerprint_is_independent_of_line_endings(self):
        data, expected = bundle()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with patch("local_llm_bench.protocol.files") as resources:
            read = resources.return_value.joinpath.return_value.read_bytes
            for version in (text, text.replace("\n", "\r\n")):
                read.return_value = version.encode()
                self.assertEqual(bundle()[1], expected)

    def test_missing_round_timing_does_not_become_a_partial_mean(self):
        result = summarize([{"test": "conv", "decode_tok_s": 100}, {"test": "conv", "decode_tok_s": None}])
        self.assertIsNone(result["conv"]["mean"])
        self.assertFalse(result["conv"]["all_engine_timings_present"])

    def test_url_normalization_and_credential_rejection(self):
        self.assertEqual(Client("http://localhost:8080/v1/").base_url, "http://localhost:8080")
        self.assertTrue(Client("http://[::1]:8080").local)
        self.assertFalse(Client("https://inference.example/service").local)
        for url in ("file:///tmp/x", "http://user:key@localhost", "http://localhost?key=secret", "http://localhost/#x"):
            with self.subTest(url=url), self.assertRaises(BenchError):
                Client(url)

    def test_missing_engine_timings_are_not_wall_speed(self):
        row = measured({"usage": {"prompt_tokens": 38, "completion_tokens": 20}}, 2)
        self.assertIsNone(row["decode_tok_s"])
        self.assertIsNone(row["prefill_tok_s"])
        self.assertEqual(row["end_to_end_tok_s"], 10)
        self.assertEqual(row["timings_source"], "unavailable")
        self.assertIsNone(row["cached_tokens"])
        self.assertIsNone(row["read_tokens"])

    def test_native_log_parser_checks_request_identity(self):
        text = "prompt 38 tokens = 0 reused + 38 read in 100.0 ms (380.0 tok/s), 20 generated in 200.0 ms (100.0 tok/s)"
        self.assertEqual(from_log(text)["decode_tok_s"], 100)
        response = {"usage": {"prompt_tokens": 38, "completion_tokens": 21}}
        with self.assertRaisesRegex(BenchError, "do not match"):
            measured(response, 1, text)
        with self.assertRaisesRegex(BenchError, "found 2"):
            from_log(text + "\n" + text)

    def test_nonfinite_engine_rate_is_rejected(self):
        response = {"usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "timings": {"predicted_per_second": float("nan")}}
        with self.assertRaises(BenchError):
            measured(response, 1)


if __name__ == "__main__":
    unittest.main()
