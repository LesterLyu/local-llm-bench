"""Offline, shareable summaries; raw endpoint/status/request data stays in JSON."""
import html
from pathlib import Path
import statistics

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


def detailed_markdown(report):
    if report.get("protocol") == "all-v1":
        lines = ["# local-llm-bench full report", "", "Protocol: all-v1", "",
                 "Original Windows tests followed by streaming, 4K/16K/32K and agent tests.", ""]
        for child in report["reports"].values():
            lines += [("#" + line if line.startswith("#") else line) for line in detailed_markdown(child).splitlines()]
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


def compact_markdown(report, language="en"):
    zh = language == "zh"
    children = list(report["reports"].values()) if report.get("protocol") == "all-v1" else [report]
    measured_rows = [r for child in children for r in child["runs"] if r.get("phase", "measurement") != "calibration"]
    repetitions = sorted({r["repetition"] for r in measured_rows}) or [1]
    lines = ["# " + ("本地模型测速报告" if zh else "local-llm-bench report"), "",
             ("协议：" if zh else "Protocol: ") + cell(report.get("protocol", "legacy Windows")),
             ("模型：" if zh else "Model: ") + cell(report.get("model", children[0].get("model", "not recorded"))),
             ("标签：" if zh else "Label: ") + cell(report.get("label", "")), "",
             ("状态：" + ("完整" if report.get("complete", True) else "未完成") if zh else
              "Status: " + ("complete" if report.get("complete", True) else "incomplete")), "",
             (f"共 {sum(len(c['runs']) for c in children)} 个请求：{len(measured_rows)} 次测量，"
              f"{sum(len(c['runs']) for c in children) - len(measured_rows)} 次校准。" if zh else
              f"{sum(len(c['runs']) for c in children)} requests: {len(measured_rows)} measured, "
              f"{sum(len(c['runs']) for c in children) - len(measured_rows)} calibration."), "",
             ("速度单位为 tok/s；预填充和校准行为输入速度，其余为输出速度。原套件汇总取均值，扩展和 Agent 取中位数。"
              if zh else "Speeds are engine tok/s: input for prefill/calibration, output otherwise. Original tests use means; streaming/agent tests use medians."),
             ("Agent 每列对应一次会话。TTFT 为首个非空内容的客户端延迟中位数（秒）。缺失值保留为 —。"
              if zh else "Agent columns represent independent sessions. TTFT is median client time to first nonempty content (seconds). Missing values remain —."),
             ("草稿接受率按接受总数 / 提供总数加权；可能包含 MTP 与后缀匹配草稿。校准不计入性能汇总。"
              if zh else "Draft acceptance is total accepted / total offered; it may include MTP and suffix-lookup drafts. Calibration is excluded from performance aggregates."), ""]
    columns = (["测试"] + [f"第 {r} 次" for r in repetitions] + ["汇总", "TTFT（秒）", "草稿接受率"] if zh else
               ["Test"] + [f"Run {r}" for r in repetitions] + ["Mean / median", "TTFT s", "Draft accepted %"])
    lines += ["| " + " | ".join(columns) + " |", "|---|" + "---:|" * (len(columns) - 1)]

    def fmt(value, digits=2, percent=False):
        return "—" if value is None else f"{value:.{digits}f}" + ("%" if percent else "")

    def add(label, values, aggregate=None, first=None, acceptance=None, percent=False):
        fields = [cell(label)] + [fmt(v, 2 if percent else 1, percent) for v in values]
        fields += [fmt(aggregate, percent=percent), fmt(first, 3), fmt(acceptance, percent=True)]
        lines.append("| " + " | ".join(fields) + " |")

    originals = {"conv": ("聊天", "chat"), "struct": ("JSON", "JSON"), "prose": ("散文", "prose"),
                 "code": ("代码", "code"), "pp16k": ("16K 预填充", "16K prefill")}
    chats = {"chat_1": ("代码短对话", "code chat"), "chat_2": ("解释短对话", "explanation chat"),
             "chat_3": ("建议短对话", "advice chat")}
    calibrations = []
    for child in children:
        rows = child["runs"]
        original = child.get("protocol") in (None, "local-chat-v1")
        for name in dict.fromkeys(r["test"] for r in rows if r.get("phase", "measurement") != "calibration"):
            group = [r for r in rows if r["test"] == name and r.get("phase", "measurement") != "calibration"]
            by_rep = {r["repetition"]: r for r in group}
            if len(by_rep) != len(group):
                raise BenchError(f"duplicate repetition in report for {name}")
            metric = "prefill_tok_s" if name.startswith("pp") else "decode_tok_s"
            values = [(by_rep.get(rep) or {}).get(metric) for rep in repetitions]
            present = [v for v in values if v is not None]
            complete = len(present) == len(group)
            aggregate = (statistics.mean(present) if original else statistics.median(present)) if complete and present else None
            if original:
                label = ("原·" if zh else "Original / ") + originals.get(name, (name, name))[0 if zh else 1]
            elif name in chats:
                label = ("扩展·" if zh else "Streaming / ") + chats[name][0 if zh else 1]
            elif name.startswith("pp"):
                label = ("扩展·" + name[2:].upper() + " 预填充" if zh else "Streaming / " + name[2:].upper() + " prefill")
            elif name.startswith("agent_"):
                turn = name.split("_", 1)[1]
                label = ("Agent·初始回复" if turn == "0" else "Agent·追加文档" + turn) if zh else "Agent / turn " + turn
            else:
                label = name
            add(label, values, aggregate, stats(r.get("ttft_s") for r in group)["median"], drafts(group))
        cache = child.get("agent_cache") or {}
        sessions = {s["repetition"]: s for s in cache.get("sessions", [])}
        if sessions:
            for key, label in (("cache_hit_pct", "Agent 缓存复用率（%）" if zh else "Agent conversation cache hit (%)"),
                               ("reread_tokens", "Agent 旧上下文重读（tokens）" if zh else "Agent earlier tokens re-read")):
                values = [(sessions.get(rep) or {}).get(key) for rep in repetitions]
                aggregate = stats(s.get(key) for s in sessions.values())["median"]
                add(label, values, aggregate, percent=key == "cache_hit_pct")
        calibrations.extend(r for r in rows if r.get("phase") == "calibration")
    for index, row in enumerate(calibrations, 1):
        words = row.get("words")
        label = (f"校准·{words}词" if words else f"校准·请求{index}") if zh else (f"Calibration / {words} words" if words else f"Calibration / request {index}")
        add(label, [row.get("prefill_tok_s")] + [None] * (len(repetitions) - 1), first=row.get("ttft_s"))
    lines += ["", ("校准行分别代表单次长度校准，汇总列留空。" if zh else "Each calibration row is a single length probe; its aggregate is intentionally blank.")]
    for child in children:
        for name, reason in child.get("skipped", {}).items():
            lines.append(("跳过：" if zh else "Skipped: ") + cell(name) + " — " + cell(reason))
        for warning in child.get("warnings", []):
            lines.append(("注意：" if zh else "Note: ") + cell(warning))
    devices = [c.get("gpus", {}).get("devices", []) for c in children]
    for scope, items in enumerate(devices):
        if not items or (scope and items == devices[0]):
            continue
        lines += ["", ("本地 GPU 功率上限：" if zh else "Local GPU power limits: ") + "; ".join(
            f"{cell(d['name'])}: {fmt(d.get('power_limit_w'))} W" for d in items)]
    lines += ["", ("设备可见性不代表文本/视觉/peer 分工。分享前请检查模型名和标签。原始地址、状态、请求、回复及备注保留在 JSON 中。"
                   if zh else "Visible devices do not identify text/vision/peer roles. Review model/label before sharing. Raw endpoint, status, requests, responses and notes stay in JSON."), ""]
    return "\n".join(lines)


def markdown(report, *, layout="compact", language="en"):
    if layout not in ("compact", "detailed") or language not in ("en", "zh"):
        raise BenchError("unsupported report layout or language")
    if layout == "detailed" and language != "en":
        raise BenchError("the detailed report is English; use compact for Chinese")
    return detailed_markdown(report) if layout == "detailed" else compact_markdown(report, language)


def write_markdown(path, report, *, layout="compact", language="en"):
    path = Path(path)
    content = markdown(report, layout=layout, language=language)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError:
        raise BenchError(f"output file already exists: {path}") from None
