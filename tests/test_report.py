from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from local_llm_bench.cli import main
from local_llm_bench.client import BenchError
from local_llm_bench.report import markdown, write_markdown
from test_serving import service


def sample():
    def row(name, rep, speed, offered=10, accepted=5, **extra):
        return dict(test=name, repetition=rep, phase="measurement", decode_tok_s=speed,
                    prefill_tok_s=speed, ttft_s=rep / 10, drafts_offered=offered, drafts_accepted=accepted, **extra)

    original = {"protocol": "local-chat-v1", "model": "test-model", "runs": [
        row("conv", 1, 100, 100, 50), row("conv", 2, 300, 10, 10)], "warnings": []}
    streaming = {"protocol": "serving-v1", "model": "test-model", "runs": [
        row("chat_1", 1, 10), row("chat_1", 2, 30), row("chat_1", 3, 80),
        row("pp4k", 1, 1000), row("pp4k", 2, None), row("pp4k", 3, 3000),
        dict(row("calibration", 1, 250), phase="calibration", words=200)], "warnings": [],
        "agent_cache": {"sessions": [{"repetition": i, "cache_hit_pct": 50 + i, "reread_tokens": 30 - i}
                                     for i in (1, 2, 3)]}}
    return {"protocol": "all-v1", "complete": True, "model": "test-model", "label": "build-A",
            "reports": {"local-chat-v1": original, "serving-v1": streaming}}


class Reports(unittest.TestCase):
    def test_single_table_uses_original_mean_streaming_median_and_weighted_drafts(self):
        text = markdown(sample())
        self.assertEqual(text.count("| Test |"), 1)
        original = next(line for line in text.splitlines() if line.startswith("| Original / chat |"))
        self.assertIn("100.0 | 300.0 | — | 200.00", original)
        self.assertIn("54.55%", original)  # 60/110, not the mean of 50% and 100%.
        stream = next(line for line in text.splitlines() if line.startswith("| Streaming / code chat |"))
        self.assertIn("10.0 | 30.0 | 80.0 | 30.00", stream)
        self.assertIn("0.200", stream)
        self.assertIn("Agent conversation cache hit (%)", text)
        self.assertIn("Calibration / 200 words", text)
        self.assertIn("excluded from performance", text)

    def test_missing_engine_measurements_and_unknown_drafts_stay_unavailable(self):
        report = sample()
        report["reports"]["serving-v1"]["runs"][0]["drafts_offered"] = None
        text = markdown(report)
        prefill = next(line for line in text.splitlines() if line.startswith("| Streaming / 4K prefill |"))
        self.assertIn("1000.0 | — | 3000.0 | —", prefill)
        stream = next(line for line in text.splitlines() if line.startswith("| Streaming / code chat |"))
        self.assertTrue(stream.endswith("| — |"))

    def test_custom_repetitions_and_chinese_keep_one_table_and_escape_metadata(self):
        report = sample()
        fourth = deepcopy(report["reports"]["serving-v1"]["runs"][0])
        fourth.update(repetition=4, decode_tok_s=90)
        report["reports"]["serving-v1"]["runs"].append(fourth)
        report.update(label="build | [link](x)\n<script>", notes="private notes", endpoint="http://private")
        text = markdown(report, language="zh")
        self.assertEqual(text.count("| 测试 |"), 1)
        self.assertIn("第 4 次", text)
        self.assertIn("草稿接受率", text)
        self.assertIn("原·聊天", text)
        self.assertIn("扩展·代码短对话", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("private notes", text)
        self.assertNotIn("http://private", text)

    def test_run_saves_compact_markdown_and_offline_command_renders_same_report(self):
        with tempfile.TemporaryDirectory() as directory, service(original_rounds=1) as (client, state):
            path = Path(directory) / "run.json"
            with redirect_stdout(io.StringIO()):
                code = main(["run", "--base-url", client.base_url, "--rounds", "1", "--no-hardware",
                             "--output", str(path), "--lang", "zh"])
            self.assertEqual(code, 0)
            self.assertEqual(len(state["requests"]), 19)
            md = path.with_suffix(".md")
            report = json.loads(path.read_text())
            self.assertEqual(md.read_text(encoding="utf-8"), markdown(report, language="zh"))
            exported = Path(directory) / "offline.md"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["report", str(path), "--lang", "zh", "--output", str(exported)]), 0)
            self.assertEqual(exported.read_text(encoding="utf-8"), md.read_text(encoding="utf-8"))

    def test_existing_markdown_is_preserved_before_any_api_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.json"
            path.with_suffix(".md").write_text("keep", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(main(["run", "--base-url", "http://localhost:1", "--output", str(path)]), 1)
            self.assertIn("Markdown report already exists", errors.getvalue())
            self.assertFalse(path.exists())
            self.assertEqual(path.with_suffix(".md").read_text(), "keep")

    def test_detailed_layout_remains_available_and_render_errors_leave_no_empty_file(self):
        report = sample()["reports"]["serving-v1"]
        report["model"] = "test-model"
        report["rounds"] = 3
        self.assertIn("medians (min–max)", markdown(report, layout="detailed"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.md"
            with self.assertRaises(BenchError):
                write_markdown(path, report, layout="unsupported")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
