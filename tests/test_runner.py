from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from local_llm_bench.client import BenchError, Client
from local_llm_bench.compare import compare, load
from local_llm_bench.protocol import plan
from local_llm_bench.runner import run


@contextmanager
def service(name="basalt", *, busy=False, failure=None, no_timing=False, cached_prefill=False, token_delta=0):
    state = {"requests": [], "counter": 0, "headers": []}
    cases = plan()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.send(200, {"service": name, "engine": "0.0.1" if name == "basalt" else "0.1.44.1",
                            "model": "test-model", "loaded": True,
                            "activity": {"requests": state["counter"], "in_flight": int(busy)}})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["headers"].append(dict(self.headers))
            index = len(state["requests"])
            state["requests"].append(body)
            if failure == index:
                self.send(500, {"error": "simulated failure"})
                return
            case = cases[index]
            prompt = case.reference_prompt_tokens + token_delta
            count = 7230 if case.name == "struct" else 32
            cached = 10 if cached_prefill and case.name == "pp16k" else 0
            text = json.dumps([{"i": i} for i in range(200)]) if case.name == "struct" else "sample text"
            result = {"model": "test-model", "choices": [{"message": {"role": "assistant", "content": text},
                                                          "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": prompt, "completion_tokens": count,
                                "prompt_tokens_details": {"cached_tokens": cached}}}
            if not no_timing:
                result["timings"] = {"cache_n": cached, "prompt_n": prompt - cached, "prompt_ms": 10,
                                     "prompt_per_second": 1000, "predicted_n": count, "predicted_ms": 100,
                                     "predicted_per_second": 320, "draft_n": 20, "draft_n_accepted": 15}
            state["counter"] += 1
            self.send(200, result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{server.server_port}", "test-secret"), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Runner(unittest.TestCase):
    def test_basalt_and_strata_use_identical_requests(self):
        payloads = []
        with tempfile.TemporaryDirectory() as directory:
            for name in ("basalt", "strata"):
                with self.subTest(backend=name), service(name) as (client, state):
                    path = Path(directory) / f"{name}.json"
                    result = run(client, path, hardware=False, strict_tokens=True, progress=lambda _: None)
                    self.assertTrue(result["complete"])
                    self.assertEqual(len(result["runs"]), 10)
                    self.assertEqual(result["summary"]["struct"]["values"], [320, 320])
                    self.assertEqual(result["final_status"]["activity"]["requests"], 10)
                    self.assertNotIn("test-secret", path.read_text())
                    self.assertEqual(state["headers"][0]["Authorization"], "Bearer test-secret")
                    self.assertEqual(state["requests"], [case.payload("test-model") for case in plan()])
                    payloads.append(state["requests"])
            self.assertEqual(payloads[0], payloads[1])

    def test_partial_failure_is_saved_and_not_comparable(self):
        with tempfile.TemporaryDirectory() as directory, service(failure=1) as (client, _):
            path = Path(directory) / "partial.json"
            with self.assertRaisesRegex(BenchError, "HTTP 500"):
                run(client, path, hardware=False, progress=lambda _: None)
            result = json.loads(path.read_text())
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["runs"]), 1)
            self.assertNotIn("test-secret", path.read_text())
            with self.assertRaisesRegex(BenchError, "incomplete"):
                load(path)

    def test_busy_service_is_not_sent_a_generation(self):
        with tempfile.TemporaryDirectory() as directory, service(busy=True) as (client, state):
            with self.assertRaisesRegex(BenchError, "busy"):
                run(client, Path(directory) / "busy.json", hardware=False)
            self.assertEqual(state["requests"], [])

    def test_cached_prefill_is_not_reported_as_fresh_16k(self):
        with tempfile.TemporaryDirectory() as directory, service(cached_prefill=True) as (client, _):
            path = Path(directory) / "cached.json"
            with self.assertRaisesRegex(BenchError, "not a fresh 16K"):
                run(client, path, hardware=False, progress=lambda _: None)
            self.assertFalse(json.loads(path.read_text())["complete"])

    def test_different_tokenizer_warns_or_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            with service(token_delta=1) as (client, _):
                result = run(client, Path(directory) / "warn.json", hardware=False, progress=lambda _: None)
                self.assertTrue(result["complete"])
                self.assertEqual(len(result["warnings"]), 10)
            with service(token_delta=1) as (client, _):
                with self.assertRaisesRegex(BenchError, "Qwen reference"):
                    run(client, Path(directory) / "strict.json", hardware=False, strict_tokens=True, progress=lambda _: None)

    def test_no_engine_timing_remains_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, service(no_timing=True) as (client, _):
            result = run(client, Path(directory) / "wall.json", hardware=False, progress=lambda _: None)
            self.assertIsNone(result["summary"]["conv"]["mean"])
            self.assertGreater(result["runs"][0]["end_to_end_tok_s"], 0)
            self.assertEqual(result["runs"][0]["timings_source"], "unavailable")

    def test_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.json"
            path.write_text("keep")
            with self.assertRaisesRegex(BenchError, "already exists"):
                run(Client("http://localhost:1"), path, hardware=False)
            self.assertEqual(path.read_text(), "keep")


class Compare(unittest.TestCase):
    def legacy(self, rate):
        return {"summary": {}, "runs": [{"test": case.name, "repetition": case.repetition,
                                         "prefill_tok_s": rate, "decode_tok_s": rate} for case in plan()]}

    def test_legacy_and_new_report_with_second_rounds(self):
        old, new = self.legacy(100), self.legacy(110)
        new.update(protocol="local-chat-v1", fixture_sha256="a", rounds=2, complete=True)
        result = compare(old, new)
        self.assertEqual(len(result["rows"]), 5)
        self.assertAlmostEqual(result["rows"][0]["change_pct"], 10)
        self.assertEqual(result["rows"][0]["new_second"], 110)
        self.assertIn("Legacy report", result["warnings"][0])

    def test_declared_protocol_mismatch_is_rejected(self):
        old, new = self.legacy(100), self.legacy(100)
        old.update(protocol="local-chat-v1", fixture_sha256="a", rounds=2)
        new.update(protocol="local-chat-v2", fixture_sha256="a", rounds=2)
        with self.assertRaisesRegex(BenchError, "different protocol"):
            compare(old, new)


if __name__ == "__main__":
    unittest.main()
