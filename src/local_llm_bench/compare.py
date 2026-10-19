"""Read current reports or the original Windows live-speed.py reports."""
import json
from pathlib import Path
import statistics

from .client import BenchError


def load(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if result.get("complete") is False or result.get("error"):
        raise BenchError(f"report is incomplete: {path}")
    if not isinstance(result.get("runs"), list) or not result["runs"]:
        raise BenchError(f"report has no measurements: {path}")
    if "protocol" not in result and "summary" not in result:
        raise BenchError(f"legacy report has no completed summary: {path}")
    if any(row.get("test") == "struct" and (row.get("json_valid") is False or
            row.get("json_items") not in (None, 200)) for row in result["runs"]):
        raise BenchError(f"report has an invalid structured output: {path}")
    return result


def compare(old, new):
    warnings = []
    if "protocol" in old and "protocol" in new:
        for key in ("protocol", "fixture_sha256", "rounds"):
            if old.get(key) != new.get(key):
                raise BenchError(f"cannot compare different {key}")
    else:
        warnings.append("Legacy report: protocol identity cannot be verified automatically; confirm the original script.")
    rows = []
    for name in ("conv", "struct", "prose", "pp16k", "code"):
        key = "prefill_tok_s" if name == "pp16k" else "decode_tok_s"
        groups = [[r for r in report["runs"] if r["test"] == name] for report in (old, new)]
        if not groups[0] or len(groups[0]) != len(groups[1]):
            raise BenchError(f"different or missing repetitions for {name}")
        for a, b in zip(*groups):
            if a["repetition"] != b["repetition"]:
                raise BenchError(f"different repetition order for {name}")
        means = [statistics.mean(r[key] for r in group) if all(r.get(key) is not None for r in group) else None
                 for group in groups]
        a, b = means
        rows.append({"test": name, "metric": key, "old_mean": a, "new_mean": b,
                     "change_pct": (b / a - 1) * 100 if a and b is not None else None,
                     "old_second": groups[0][1].get(key) if len(groups[0]) > 1 else None,
                     "new_second": groups[1][1].get(key) if len(groups[1]) > 1 else None})
    warnings.append("Session, model, quantization, GPU roles, power limits and server configuration must match for a fair comparison.")
    return {"rows": rows, "warnings": warnings}


def table(rows):
    def number(value):
        return f"{value:.1f}" if value is not None else "n/a"
    lines = ["| Test (tok/s) | Old mean | New mean | Change | Old round 2 | New round 2 |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        change = f"{row['change_pct']:+.1f}%" if row["change_pct"] is not None else "n/a"
        lines.append(f"| {row['test']} | {number(row['old_mean'])} | {number(row['new_mean'])} | {change} | "
                     f"{number(row['old_second'])} | {number(row['new_second'])} |")
    return "\n".join(lines)
