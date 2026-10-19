from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from local_llm_bench.cli import main
from local_llm_bench.client import BenchError
from local_llm_bench.combined import plan, run
from local_llm_bench.compare import compare, load
from local_llm_bench.protocol import plan as original_plan
from local_llm_bench.report import markdown
from test_serving import service


class Combined(unittest.TestCase):
    def test_default_plan_contains_both_suites_and_32k(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["run", "--dry-run"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["protocol"], "all-v1")
        self.assertEqual(data["rounds"], {"local-chat-v1": 2, "serving-v1": 3})
        self.assertEqual(len(data["requests"]), 48)
        self.assertEqual([r["suite"] for r in data["requests"][:10]], ["local-chat-v1"] * 10)
        self.assertEqual(sum(r["test"] == "pp32k" for r in data["requests"]), 3)
        self.assertEqual(len(plan(1)["requests"]), 19)

    def test_default_full_run_preserves_both_protocols_and_combined_report(self):
        with tempfile.TemporaryDirectory() as directory, service() as (client, state):
            path = Path(directory) / "all.json"
            report = run(client, path, hardware=False, strict_tokens=True, progress=lambda _: None)
            self.assertEqual(len(state["requests"]), 48)
            self.assertEqual(state["requests"][:10], [c.payload("test-model") for c in original_plan()])
            self.assertFalse(any(p["stream"] for p in state["requests"][:10]))
            self.assertTrue(all(p["stream"] for p in state["requests"][10:]))
            self.assertTrue(report["complete"])
            self.assertEqual(len(load(path)["runs"]), 48)
            self.assertEqual(set(report["reports"]), {"local-chat-v1", "serving-v1"})
            self.assertTrue(all(child["complete"] for child in report["reports"].values()))
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertIn("Historical protocol values", markdown(report))
            self.assertIn("Agent conversation cache", markdown(report))
            self.assertNotIn("test-secret", path.read_text())
            self.assertEqual(set(compare(report, deepcopy(report))["suites"]), {"local-chat-v1", "serving-v1"})
            matching = compare(report["reports"]["local-chat-v1"], report)
            self.assertEqual(len(matching["rows"]), 5)
            self.assertIn("Comparing only local-chat-v1", matching["warnings"][0])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["compare", str(path), str(path)]), 0)
            self.assertIn("Old mean", output.getvalue())
            self.assertIn("Old median", output.getvalue())

    def test_failure_preserves_completed_original_and_partial_extension(self):
        with tempfile.TemporaryDirectory() as directory, service(failure=12) as (client, _):
            path = Path(directory) / "partial.json"
            with self.assertRaises(BenchError):
                run(client, path, hardware=False, progress=lambda _: None)
            report = json.loads(path.read_text())
            self.assertFalse(report["complete"])
            self.assertTrue(report["reports"]["local-chat-v1"]["complete"])
            self.assertFalse(report["reports"]["serving-v1"]["complete"])
            self.assertEqual(len(report["runs"]), 12)
            self.assertNotIn("test-secret", path.read_text())
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaisesRegex(BenchError, "incomplete"):
                load(path)


if __name__ == "__main__":
    unittest.main()
