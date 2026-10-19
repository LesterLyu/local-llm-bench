"""Offline, shareable summaries; raw endpoint/status/request data stays in JSON."""
import html
from pathlib import Path

from .client import BenchError
from .serving import stats


def cell(value):
    text = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    for char in ("\\", "|", "[", "]", "`", "*", "_"):
        text = text.replace(char, "\\" + char)
    return text


def number(value):
    return f"{value:.3f}" if value is not None else "n/a"


def spread(rows, key):
    value = stats(r.get(key) for r in rows)
    if value["median"] is None:
        return "n/a"
    return f"{value['median']:.3f} ({value['min']:.3f}–{value['max']:.3f})"


def drafts(rows):
    if not rows or any(r.get("drafts_offered") is None or r.get("drafts_accepted") is None for r in rows):
        return None
    offered = sum(r["drafts_offered"] for r in rows)
    return 100 * sum(r["drafts_accepted"] for r in rows) / offered if offered else None


def markdown(report):
    if report.get("protocol") == "all-v1":
        lines = ["# local-llm-bench full report", "", "Protocol: all-v1", "",
                 "Original Windows tests followed by streaming, 4K/16K/32K and agent tests.", ""]
        for child in report["reports"].values():
            lines += [("#" + line if line.startswith("#") else line) for line in markdown(child).splitlines()]
            lines.append("")
        return "\n".join(lines)
    rows = [r for r in report["runs"] if r.get("phase", "measurement") == "measurement"]
    lines = ["# local-llm-bench report", "", f"Protocol: {cell(report.get('protocol', 'legacy Windows'))}",
             f"Model: {cell(report.get('model', 'not recorded'))}",
             f"Label: {cell(report.get('label', ''))}", f"Rounds: {report.get('rounds', 'legacy')}", "",
             "Values below are medians (min–max), with all repetitions retained.",
             "Decode/prefill rates are engine timings; TTFT and wall time are measured by the HTTP client.",
             "Draft acceptance is total accepted / total offered, not a mean of percentages.", "",
             "| Test | N | Input tokens | Cached tokens | Decode tok/s | Prefill tok/s | TTFT s | Wall s | Draft accepted % |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in dict.fromkeys(r["test"] for r in rows):
        group = [r for r in rows if r["test"] == name]
        fields = [cell(name), str(len(group))] + [spread(group, key) for key in
                  ("prompt_tokens", "cached_tokens", "decode_tok_s", "prefill_tok_s", "ttft_s", "wall_s")]
        lines.append("| " + " | ".join(fields + [number(drafts(group))]) + " |")
    if report.get("protocol") == "local-chat-v1":
        lines += ["", "## Historical protocol values", "",
                  "The original comparison uses means including round 1, plus separate round-2 values:", "",
                  "| Test | Engine metric | Mean | Round 2 |", "|---|---|---:|---:|"]
        for name, value in report.get("summary", {}).items():
            lines.append(f"| {cell(name)} | {cell(value['metric'])} | {number(value['mean'])} | {number(value['second_round'])} |")
    cache = report.get("agent_cache")
    if cache and cache.get("sessions"):
        lines += ["", "## Agent conversation cache", "",
                  "| Session | Cache hit % (follow-up turns) | Earlier tokens re-read |", "|---|---:|---:|"]
        for session in cache["sessions"]:
            lines.append(f"| {session['repetition']} | {number(session['cache_hit_pct'])} | {number(session['reread_tokens'])} |")
    devices = (report.get("gpus") or {}).get("devices") or []
    if devices:
        lines += ["", "## Local hardware", "", "Visible devices do not identify text/vision/peer roles.", "",
                  "| GPU | Name | Power limit W |", "|---|---|---:|"]
        for device in devices:
            lines.append(f"| {device['index']} | {cell(device['name'])} | {number(device.get('power_limit_w'))} |")
    else:
        lines += ["", "Hardware: not collected or unavailable."]
    if report.get("calibration"):
        lines += ["", "Two prompt-length calibration requests are in JSON and excluded from these statistics."]
    for name, reason in report.get("skipped", {}).items():
        lines.append(f"\nSkipped {cell(name)}: {cell(reason)}")
    for warning in report.get("warnings", []):
        lines.append(f"\nNote: {cell(warning)}")
    lines += ["", "Endpoint, raw status, requests/responses, free-form notes and local paths are omitted from this summary.",
              "Review model/label fields before sharing. Keep model, settings, GPU roles and power limits consistent when comparing.", ""]
    return "\n".join(lines)


def write_markdown(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(markdown(report))
    except FileExistsError:
        raise BenchError(f"output file already exists: {path}") from None
