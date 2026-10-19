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
    if any(r.get("protocol") == "all-v1" for r in (old, new)):
        if old.get("protocol") != new.get("protocol"):
            other = new if old.get("protocol") == "all-v1" else old
            name = other.get("protocol", "local-chat-v1")
            full = old if old.get("protocol") == "all-v1" else new
            if name not in ("local-chat-v1", "serving-v1") or name not in full.get("reports", {}):
                raise BenchError("cannot compare different protocol")
            result = compare(full["reports"][name] if full is old else old,
                             full["reports"][name] if full is new else new)
            result["warnings"].insert(0, f"Comparing only {name} from the combined report; other suites are excluded.")
            return result
        for key in ("fixture_sha256", "rounds", "options"):
            if old.get(key) != new.get(key):
                raise BenchError(f"cannot compare different {key}")
        suites = ("local-chat-v1", "serving-v1")
        if any(set(r.get("reports", {})) != set(suites) for r in (old, new)):
            raise BenchError("combined report is missing a suite")
        return {"suites": {name: compare(old["reports"][name], new["reports"][name]) for name in suites},
                "rows": [], "warnings": []}
    if any(r.get("protocol") == "serving-v1" for r in (old, new)) and old.get("protocol") != new.get("protocol"):
        raise BenchError("cannot compare different protocol (serving-v1 requires matching versioned reports)")
    if "protocol" in old and "protocol" in new:
        for key in ("protocol", "fixture_sha256", "rounds"):
            if old.get(key) != new.get(key):
                raise BenchError(f"cannot compare different {key}")
    else:
        warnings.append("Legacy report: protocol identity cannot be verified automatically; confirm the original script.")
    if old.get("protocol") == "serving-v1":
        return compare_serving(old, new)
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


def compare_serving(old, new):
    from .serving import stats, summarize

    identity = lambda r: (r["test"], r["repetition"], r.get("turn"), r.get("phase"), r.get("target_tokens"))
    if (old.get("options") != new.get("options") or old.get("skipped") != new.get("skipped") or
            [identity(r) for r in old["runs"]] != [identity(r) for r in new["runs"]]):
        raise BenchError("cannot compare different serving options, skipped tests or request sequences")
    summaries = [summarize(report["runs"]) for report in (old, new)]
    rows = []

    def add(name, metric, a, b):
        rows.append({"test": name, "metric": metric, "old_median": a, "new_median": b,
                     "change_pct": (b / a - 1) * 100 if a and b is not None else None})

    for name in summaries[0]:
        speed = "prefill_tok_s" if name.startswith("pp") else "decode_tok_s"
        for key in (speed, "ttft_s"):
            add(name, key, *(s[name][key]["median"] for s in summaries))
    for key in ("cache_hit_pct", "reread_tokens"):
        values = [stats(s[key] for s in report.get("agent_cache", {}).get("sessions", []))["median"] for report in (old, new)]
        if any(v is not None for v in values):
            add("agent_session", key, *values)
    return {"rows": rows, "warnings": ["Lower TTFT/re-read counts are better; higher engine tok/s/cache hit rates are better.",
            "Agent replies are generated by each server; inspect token counts, settings and hardware before attributing differences to a build."]}


def table(rows):
    def number(value):
        return f"{value:.1f}" if value is not None else "n/a"
    if rows and "old_median" in rows[0]:
        lines = ["| Test | Metric | Old median | New median | Change |", "|---|---|---:|---:|---:|"]
        for row in rows:
            change = f"{row['change_pct']:+.1f}%" if row["change_pct"] is not None else "n/a"
            lines.append(f"| {row['test']} | {row['metric']} | {number(row['old_median'])} | {number(row['new_median'])} | {change} |")
        return "\n".join(lines)
    lines = ["| Test (tok/s) | Old mean | New mean | Change | Old round 2 | New round 2 |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        change = f"{row['change_pct']:+.1f}%" if row["change_pct"] is not None else "n/a"
        lines.append(f"| {row['test']} | {number(row['old_mean'])} | {number(row['new_mean'])} | {change} | "
                     f"{number(row['old_second'])} | {number(row['new_second'])} |")
    return "\n".join(lines)
