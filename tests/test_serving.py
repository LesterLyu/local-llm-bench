from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_llm_bench.cli import main
from local_llm_bench.client import BenchError, Client
from local_llm_bench.compare import compare, load, table
from local_llm_bench.report import markdown, write_markdown
from local_llm_bench.runner import measured
from local_llm_bench.protocol import plan as original_plan
from local_llm_bench.serving import bundle, plan, run, stats


@contextmanager
def service(name="basalt", *, context=65536, no_timing=False, cached=False, unknown_cache=False, failure=None,
            truncated=False, bad_json=False, tool_call=False, interference=False, original_rounds=2):
    state = {"requests": [], "counter": 0, "sessions": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            payload = {"service": name, "loaded": True, "model": "test-model", "context": {"native": context},
                       "activity": {"requests": state["counter"], "in_flight": 0}}
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            index = len(state["requests"])
            state["requests"].append(payload)
            if not payload.get("stream"):
                case = original_plan(original_rounds)[index]
                content = json.dumps([{"i": i} for i in range(200)]) if case.name == "struct" else "Test response."
                result = {"choices": [{"message": {"role": "assistant", "content": content}}],
                          "usage": {"prompt_tokens": case.reference_prompt_tokens,
                                    "completion_tokens": 7230 if case.name == "struct" else 32,
                                    "prompt_tokens_details": {"cached_tokens": 0}},
                          "timings": {"predicted_per_second": 100, "prompt_per_second": 2000}}
                raw = json.dumps(result).encode()
                state["counter"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if index == failure:
                self.wfile.write(b'data: {"error": "test-secret rejected"}\n\n')
                return
            if bad_json:
                self.wfile.write(b'data: invalid JSON\n\n')
                return
            messages = payload["messages"]
            prompt = 40 + sum(len(m["content"].split()) for m in messages)
            n, reused = min(8, payload["max_tokens"]), int(cached)
            if payload.get("tools"):
                key = messages[0]["content"]
                reused = state["sessions"].get(key, 0)
                state["sessions"][key] = prompt + n
            usage = {"prompt_tokens": prompt, "completion_tokens": n}
            if not unknown_cache:
                usage["prompt_tokens_details"] = {"cached_tokens": reused}
            final = {"choices": [], "usage": usage}
            if not no_timing:
                final["timings"] = {"cache_n": reused, "prompt_n": prompt - reused,
                                    "predicted_per_second": 100, "prompt_per_second": 2000,
                                    "draft_n": 10, "draft_n_accepted": 6}
            delta = {"tool_calls": [{"index": 0, "id": "call_x"}]} if tool_call else {"content": "Test response."}
            events = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
                      {"choices": [{"index": 0, "delta": delta}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}, final]
            wire = b": heartbeat\r\n\r\n" + b"".join(
                ("data: " + json.dumps(event) + "\r\n\r\n").encode() for event in events)
            state["counter"] += 2 if interference else 1
            if not truncated:
                wire += b"data: [DONE]\n\n"
            try:
                self.wfile.write(wire)
            except ConnectionError:
                pass  # A malformed/tool-call stream is intentionally closed by the client.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{server.server_port}", "test-secret"), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Streaming(unittest.TestCase):
    def test_ttft_ignores_heartbeat_empty_delta_and_keeps_final_usage(self):
        clock = [0]

        class Response:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                for at, raw in [(0.1, b": ping\n\n"),
                                (0.2, b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'),
                                (1.2, b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'),
                                (2, b'data: {"usage":{"prompt_tokens":5,"completion_tokens":1},"timings":{"predicted_per_second":9}}\n\n'),
                                (2.1, b'data: [DONE]\n\n')]:
                    clock[0] = at
                    yield from raw.splitlines(keepends=True)

        with patch("local_llm_bench.client.urllib.request.urlopen", return_value=Response()), \
                patch("local_llm_bench.client.time.perf_counter", side_effect=lambda: clock[0]):
            answer, wall, first = Client("http://localhost:1").stream_chat({"stream": True})
        self.assertEqual((wall, first), (2.1, 1.2))
        self.assertEqual(answer["usage"]["completion_tokens"], 1)
        self.assertEqual(answer["timings"]["predicted_per_second"], 9)
        self.assertEqual(answer["choices"][0]["message"]["content"], "Hi")

    def test_stream_failures_are_not_silent_partial_success(self):
        for options, error in [({"truncated": True}, "without \\[DONE\\]"), ({"bad_json": True}, "streamed chat failed"),
                               ({"failure": 0}, "<redacted> rejected"), ({"tool_call": True}, "called a tool")]:
            with self.subTest(options=options), service(**options) as (client, _):
                with self.assertRaisesRegex(BenchError, error) as caught:
                    client.stream_chat({"stream": True, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 8})
                self.assertNotIn("test-secret", str(caught.exception))


class Serving(unittest.TestCase):
    def execute(self, directory, client, filename="run.json", **kwargs):
        return run(client, Path(directory) / filename, rounds=1, hardware=False, progress=lambda _: None, **kwargs)

    def test_both_backends_stream_same_workload_and_preserve_agent_prefix(self):
        workloads = []
        with tempfile.TemporaryDirectory() as directory:
            for name in ("basalt", "strata"):
                with service(name) as (client, state), patch("local_llm_bench.serving.uuid.uuid4") as uid:
                    uid.return_value.hex = "fixed"
                    result = self.execute(directory, client, name + ".json", long=True)
                    self.assertTrue(result["complete"])
                    self.assertEqual(len(result["runs"]), 14)
                    self.assertEqual([r["test"] for r in result["runs"]],
                                     [r["test"] for r in plan(1, True)["requests"]])
                    self.assertEqual(result["summary"]["pp32k"]["prompt_tokens"]["median"], 32768)
                    self.assertNotIn("calibration", result["summary"])
                    self.assertGreater(result["agent_cache"]["cache_hit_pct"]["median"], 0)
                    self.assertEqual(result["agent_cache"]["reread_tokens"]["median"], 0)
                    turns = [r for r in result["runs"] if "turn" in r]
                    self.assertEqual(len(turns[0]["request"]["tools"]), 4)
                    for previous, row in zip(turns, turns[1:]):
                        self.assertEqual(row["request"]["messages"][:-2], previous["request"]["messages"])
                        self.assertEqual(row["request"]["messages"][-2], previous["response"]["choices"][0]["message"])
                    self.assertTrue(all(r["request"]["stream"] for r in result["runs"]))
                    self.assertEqual(result["runs"][0]["acceptance_pct"], 60)
                    self.assertNotIn("test-secret", (Path(directory) / (name + ".json")).read_text())
                    workloads.append(state["requests"])
            self.assertEqual(workloads[0], workloads[1])

    def test_length_calibration_recording_context_skips_and_random_nonces(self):
        with tempfile.TemporaryDirectory() as directory, service(context=8192) as (client, state):
            result = self.execute(directory, client)
        self.assertEqual(set(result["skipped"]), {"pp16k", "agent"})
        self.assertEqual(result["calibration"]["tokens_per_word"], 1)
        tags = [p["messages"][0]["content"].splitlines()[0] for p in state["requests"]]
        self.assertEqual(len(tags), len(set(tags)))

    def test_missing_metrics_remain_null_and_partial_runs_stay_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            with service(no_timing=True, unknown_cache=True, context=2048) as (client, _):
                result = self.execute(directory, client, "missing.json")
                self.assertIsNone(result["summary"]["chat_1"]["decode_tok_s"]["median"])
                self.assertIsNotNone(result["summary"]["chat_1"]["ttft_s"]["median"])
            for i, options in enumerate([{"cached": True}, {"interference": True}, {"failure": 3}]):
                with self.subTest(options=options), service(**options) as (client, _):
                    path = Path(directory) / f"failure{i}.json"
                    with self.assertRaises(BenchError):
                        self.execute(directory, client, path.name)
                    self.assertFalse(json.loads(path.read_text())["complete"])
                    with self.assertRaisesRegex(BenchError, "incomplete"):
                        load(path)

    def test_comparison_uses_medians_and_rejects_incompatible_suites(self):
        with tempfile.TemporaryDirectory() as directory, service() as (client, _):
            old = self.execute(directory, client)
        new = deepcopy(old)
        for row in new["runs"]:
            row["decode_tok_s"] *= 2
        result = compare(old, new)
        self.assertEqual(result["rows"][0]["change_pct"], 100)
        self.assertIn("Old median", table(result["rows"]))
        for field, value in [("protocol", "local-chat-v1"), ("fixture_sha256", "other"),
                             ("options", {"long": True}), ("skipped", {"agent": "small context"})]:
            wrong = dict(new, **{field: value})
            with self.subTest(field=field), self.assertRaises(BenchError):
                compare(old, wrong)
        with self.assertRaises(BenchError):
            compare({"runs": old["runs"], "summary": {}}, new)

    def test_offline_plan_fingerprint_missing_stats_and_markdown(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["run", "--suite", "serving-v1", "--dry-run"]), 0)
        self.assertEqual(len(json.loads(out.getvalue())["requests"]), 35)
        self.assertIsNone(stats([100, None])["median"])
        data, fingerprint = bundle()
        self.assertEqual(fingerprint, "f97d8a8ed61128f600fa14fafee0ae5418e67b662102c0116c0cab70b9d6179c")
        with patch("local_llm_bench.serving.files") as resources:
            resources.return_value.joinpath.return_value.read_bytes.return_value = json.dumps(data, indent=2).replace("\n", "\r\n").encode()
            self.assertEqual(bundle()[1], fingerprint)
        with tempfile.TemporaryDirectory() as directory, service() as (client, _):
            result = self.execute(directory, client)
            result.update(notes="secret local notes", initial_status={"key": "hidden"}, endpoint="http://private-server")
            md = markdown(result)
            for private in ("secret local notes", "private-server", '"hidden"', "test-secret"):
                self.assertNotIn(private, md)
            self.assertIn("Agent conversation cache", md)
            target = Path(directory) / "share.md"
            write_markdown(target, result)
            with self.assertRaisesRegex(BenchError, "already exists"):
                write_markdown(target, result)

    def test_draft_counts_are_measured_not_inferred(self):
        base = {"usage": {"prompt_tokens": 50, "completion_tokens": 20}}
        self.assertIsNone(measured(base, 1)["acceptance_pct"])
        for offered, accepted in [(0, 0), (10, 3)]:
            row = measured(dict(base, timings={"draft_n": offered, "draft_n_accepted": accepted}), 1)
            self.assertEqual(row["acceptance_pct"], 30 if offered else None)
        with self.assertRaisesRegex(BenchError, "exceed"):
            measured(dict(base, timings={"draft_n": 2, "draft_n_accepted": 3}), 1)


if __name__ == "__main__":
    unittest.main()
