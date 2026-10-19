"""Portable streaming suite adapted from Strata's community benchmark.

The original local-chat-v1 sequence is deliberately separate. See serving.json
and docs/serving.md for the workload, calibration and cache policies.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import platform
import random
import statistics
import uuid

from . import __version__
from .client import BenchError
from .hardware import Monitor, gpu_info
from .runner import measured, write_json

PROTOCOL = "serving-v1"


def bundle():
    data = json.loads(files("local_llm_bench").joinpath("data/serving.json").read_bytes())
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return data, hashlib.sha256(raw).hexdigest()


def long_prompt(words, data):
    rng = random.Random(data["seed"])
    body = " ".join(rng.choice(data["vocab"]) for _ in range(words))
    return f"Here is a list of words:\n{body}\n\nSummarize what kinds of words appear in the list, in five sentences."


def plan(rounds=3, long=False):
    data, fingerprint = bundle()
    requests = [{"test": "calibration", "words": n, "phase": "calibration", "max_tokens": 1}
                for n in data["calibration_words"]]
    for i in range(1, len(data["chats"]) + 1):
        requests.extend({"test": f"chat_{i}", "repetition": r, "max_tokens": data["chat_max_tokens"]}
                        for r in range(1, rounds + 1))
    for target in data["prompt_targets"] + ([data["long_target"]] if long else []):
        requests.extend({"test": f"pp{target // 1024}k", "repetition": r, "target_tokens": target,
                         "max_tokens": data["prompt_max_tokens"]} for r in range(1, rounds + 1))
    for r in range(1, rounds + 1):
        requests.extend({"test": f"agent_{t}", "repetition": r, "turn": t, "max_tokens": data["agent_max_tokens"]}
                        for t in range(data["agent_turns"] + 1))
    return {"protocol": PROTOCOL, "fixture_sha256": fingerprint, "rounds": rounds,
            "long": long, "warmup": False, "requests": requests}


def stats(values):
    values = list(values)
    present = [v for v in values if v is not None]
    full = bool(values) and len(values) == len(present)
    return {"n": len(values), "measured": len(present), "values": values,
            "median": statistics.median(present) if full else None,
            "min": min(present) if full else None, "max": max(present) if full else None}


def summarize(rows):
    result = {}
    for name in dict.fromkeys(r["test"] for r in rows if r["phase"] == "measurement"):
        group = [r for r in rows if r["test"] == name]
        result[name] = {key: stats(r.get(key) for r in group) for key in
                        ("decode_tok_s", "prefill_tok_s", "ttft_s", "wall_s", "prompt_tokens",
                         "cached_tokens", "read_tokens", "output_tokens", "acceptance_pct")}
    return result


def agent_cache(rows):
    sessions = []
    agent = [r for r in rows if r.get("turn") is not None]
    for rep in dict.fromkeys(r["repetition"] for r in agent):
        turns = [r for r in agent if r["repetition"] == rep]
        pairs = list(zip(turns, turns[1:]))
        known = bool(pairs) and all(r["cached_tokens"] is not None for _, r in pairs)
        sessions.append({"repetition": rep,
                         "cache_hit_pct": 100 * sum(r["cached_tokens"] for _, r in pairs) /
                         sum(r["prompt_tokens"] for _, r in pairs) if known else None,
                         "reread_tokens": sum(max(0, p["prompt_tokens"] + p["output_tokens"] - r["cached_tokens"])
                                              for p, r in pairs) if known else None})
    return {"sessions": sessions, "cache_hit_pct": stats(s["cache_hit_pct"] for s in sessions),
            "reread_tokens": stats(s["reread_tokens"] for s in sessions)}


def context_size(status):
    context = status.get("context") or {}
    if isinstance(context, dict):
        context = context.get("native") or context.get("max_positions")
    return context if isinstance(context, int) and context > 0 else None


def run(client, output, *, rounds=3, model=None, long=False, hardware=True, label="", notes="", progress=print):
    if rounds < 1:
        raise BenchError("rounds must be positive")
    output = Path(output)
    if output.exists():
        raise BenchError(f"output file already exists: {output}")
    data, fingerprint = bundle()
    local = hardware and client.local
    report = {"schema_version": 1, "protocol": PROTOCOL, "fixture_sha256": fingerprint,
              "tool_version": __version__, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "runner": {"platform": platform.platform(), "python": platform.python_version()},
              "endpoint": client.base_url, "label": label, "notes": notes, "rounds": rounds,
              "canonical_rounds": rounds == 3, "options": {"long": long}, "warmup": False,
              "hardware_scope": "local runner" if local else "not collected",
              "hardware_note": "Device visibility does not identify text/vision roles.",
              "gpus": gpu_info() if local else {"available": False, "reason": "disabled or non-loopback endpoint"},
              "runs": [], "hardware": [], "warnings": [], "skipped": {}, "complete": False}
    monitor = Monitor(report["hardware"], local)

    def send(name, rep, messages, limit, *, phase="measurement", tools=None, **extra):
        before = client.status()
        payload = dict(deepcopy(data["sampling"]), model=model, messages=deepcopy(messages), max_tokens=limit)
        if tools:
            payload["tools"] = tools
        answer, wall, first = client.stream_chat(payload)
        row = measured(answer, wall)
        row.update(test=name, repetition=rep, phase=phase, ttft_s=first, request=payload, response=answer, **extra)
        report["runs"].append(row)
        write_json(output, report)
        message = answer["choices"][0]["message"]
        if message.get("reasoning_content"):
            raise BenchError("server generated thinking despite enable_thinking=false")
        after = client.status()
        a, b = before["activity"].get("requests"), after["activity"].get("requests")
        if a is not None and b is not None and b != a + 1:
            raise BenchError("request counter changed unexpectedly; another client or server restart interfered")
        progress(json.dumps({key: row[key] for key in ("test", "repetition", "phase", "prompt_tokens",
                                                      "cached_tokens", "decode_tok_s", "prefill_tok_s", "ttft_s")}))
        return row, message

    def fresh(prompt):
        return [{"role": "user", "content": f"[{uuid.uuid4().hex}]\n{prompt}"}]

    try:
        initial = client.status()
        report["initial_status"] = initial
        model = model or initial.get("model")
        if not model:
            raise BenchError("no model reported by /v1/status; pass --model")
        report["model"] = model
        context = context_size(initial)
        if context is None:
            raise BenchError("serving-v1 requires the server's context limit at /v1/status")
        monitor.start()
        points = []
        for words in data["calibration_words"]:
            row, _ = send("calibration", len(points) + 1, fresh(long_prompt(words, data)), 1,
                          phase="calibration", words=words)
            points.append(row["prompt_tokens"])
        slope = (points[1] - points[0]) / (data["calibration_words"][1] - data["calibration_words"][0])
        if slope <= 0:
            raise BenchError("prompt calibration failed: token count did not increase")
        overhead = max(0, points[0] - data["calibration_words"][0] * slope)
        report["calibration"] = {"tokens_per_word": slope, "overhead_tokens": overhead}
        for i, prompt in enumerate(data["chats"], 1):
            for rep in range(1, rounds + 1):
                send(f"chat_{i}", rep, fresh(prompt), data["chat_max_tokens"])
        for target in data["prompt_targets"] + ([data["long_target"]] if long else []):
            name = f"pp{target // 1024}k"
            if target + data["prompt_max_tokens"] + 256 > context:
                report["skipped"][name] = f"context limit {context} is too small"
                continue
            words = max(50, int((target - overhead) / slope))
            for rep in range(1, rounds + 1):
                row, _ = send(name, rep, fresh(long_prompt(words, data)), data["prompt_max_tokens"], target_tokens=target)
                if row["cached_tokens"] != 0:
                    raise BenchError(f"{name} reused prompt tokens or cache reuse is unknown; this is not a fresh prefill measurement")
                if abs(row["prompt_tokens"] - target) > target * 0.1:
                    report["warnings"].append(f"{name}: actual input {row['prompt_tokens']} differs from target {target} by more than 10%")
        agent_context = max(data["agent_min_context"], int(data["agent_words"] * data["agent_turns"] * slope +
                            overhead + 1024 + (data["agent_turns"] + 1) * data["agent_max_tokens"]))
        if context < agent_context:
            report["skipped"]["agent"] = f"context limit {context} is below estimated requirement {agent_context}"
        else:
            for rep in range(1, rounds + 1):
                messages = [{"role": "system", "content": f"[{uuid.uuid4().hex}]\n{data['system']}"},
                            {"role": "user", "content": data["agent_instruction"]}]
                for turn in range(data["agent_turns"] + 1):
                    if turn:
                        body = long_prompt(data["agent_words"], data)
                        messages.append({"role": "user", "content": f"File generated/part{turn - 1}.txt:\n{body}\n\nWhat is this file for? Two sentences."})
                    _, message = send(f"agent_{turn}", rep, messages, data["agent_max_tokens"], tools=data["tools"], turn=turn)
                    messages.append(deepcopy(message))
        report["summary"] = summarize(report["runs"])
        report["agent_cache"] = agent_cache(report["runs"])
        report["final_status"] = client.status()
        if any(r["decode_tok_s"] is None for r in report["runs"]):
            report["warnings"].append("Engine timings missing: end_to_end_tok_s is HTTP throughput, not decode speed.")
        report["complete"] = True
    except BaseException as exc:
        text = str(exc) or type(exc).__name__
        report["error"] = text.replace(client.api_key, "<redacted>") if client.api_key else text
        raise
    finally:
        monitor.close()
        write_json(output, report)
    return report
