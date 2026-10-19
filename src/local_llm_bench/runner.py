"""Serial benchmark runner. The server is observed, never reconfigured."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import re
import statistics
import time

from . import __version__
from .client import BenchError
from .hardware import Monitor, gpu_info
from .protocol import PROTOCOL, bundle, plan

LOG_TIMING = re.compile(
    r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in ([\d.]+) ms "
    r"\(([\d.]+) tok/s\), (\d+) generated in ([\d.]+) ms \(([\d.]+) tok/s\)")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def from_log(text):
    matches = LOG_TIMING.findall(text)
    if len(matches) != 1:
        raise BenchError(f"expected one request timing in engine log, found {len(matches)}")
    p, cached, read, pp_ms, pp_rate, n, tg_ms, tg_rate = matches[0]
    return {"prompt_tokens": int(p), "cached_tokens": int(cached), "read_tokens": int(read),
            "prefill_ms": float(pp_ms), "prefill_tok_s": float(pp_rate),
            "output_tokens": int(n), "decode_ms": float(tg_ms), "decode_tok_s": float(tg_rate)}


def measured(answer, wall_s, log_text=None):
    usage = answer.get("usage") or {}
    prompt, count = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (prompt, count)) or not count:
        raise BenchError("response must provide valid usage.prompt_tokens and positive usage.completion_tokens")
    timing = answer.get("timings") or {}
    row = {"prompt_tokens": prompt, "output_tokens": count, "cached_tokens":
           (usage.get("prompt_tokens_details") or {}).get("cached_tokens", timing.get("cache_n")),
           "read_tokens": timing.get("prompt_n"), "prefill_ms": timing.get("prompt_ms"),
           "prefill_tok_s": timing.get("prompt_per_second"), "decode_ms": timing.get("predicted_ms"),
           "decode_tok_s": timing.get("predicted_per_second"), "timings_source": "response.timings" if timing else "unavailable",
           "wall_s": round(wall_s, 4), "end_to_end_tok_s": round(count / wall_s, 3), "timings": timing}
    if log_text is not None:
        native = from_log(log_text)
        if native["prompt_tokens"] != prompt or native["output_tokens"] != count:
            raise BenchError("engine-log token counts do not match this response")
        row.update(native, timings_source="engine_log")
    for key in ("prefill_ms", "prefill_tok_s", "decode_ms", "decode_tok_s"):
        value = row[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise BenchError(f"invalid engine timing {key}: {value!r}")
    if row["read_tokens"] is None and row["cached_tokens"] is not None:
        row["read_tokens"] = max(0, prompt - int(row["cached_tokens"]))
    return row


def summarize(rows):
    result = {}
    for name in ("conv", "struct", "prose", "pp16k", "code"):
        group = [row for row in rows if row["test"] == name]
        key = "prefill_tok_s" if name == "pp16k" else "decode_tok_s"
        values = [row[key] for row in group]
        present = [value for value in values if value is not None]
        full = bool(group) and len(present) == len(group)
        result[name] = {"metric": key, "values": values, "mean": statistics.mean(present) if full else None,
                        "stddev": statistics.stdev(present) if full and len(present) > 1 else None,
                        "second_round": values[1] if len(values) > 1 else None,
                        "all_engine_timings_present": full}
    return result


def run(client, output, *, rounds=2, model=None, engine_log=None, strict_tokens=False,
        hardware=True, label="", notes="", progress=print):
    output = Path(output)
    if output.exists():
        raise BenchError(f"output file already exists: {output}")
    cases = plan(rounds)
    _, fixture_hash = bundle()
    local = hardware and client.local
    report = {"schema_version": 1, "protocol": PROTOCOL, "fixture_sha256": fixture_hash,
              "tool_version": __version__, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "runner": {"platform": platform.platform(), "python": platform.python_version()},
              "endpoint": client.base_url, "label": label, "notes": notes, "rounds": rounds,
              "canonical_rounds": rounds == 2, "hardware_scope": "local runner" if local else "not collected",
              "hardware_note": "Device visibility does not identify text/vision roles.",
              "gpus": gpu_info() if local else {"available": False, "reason": "disabled or non-loopback endpoint"},
              "runs": [], "hardware": [], "warnings": [], "complete": False}
    monitor = Monitor(report["hardware"], local)
    try:
        initial = client.status()
        report["initial_status"] = initial
        model = model or initial.get("model")
        if not model:
            raise BenchError("no model reported by /v1/status; pass --model")
        report["model"] = model
        monitor.start()
        log_path = Path(engine_log) if engine_log else None
        for case in cases:
            before = client.status()
            offset = log_path.stat().st_size if log_path else None
            payload = case.payload(model)
            start = time.perf_counter()
            answer = client.request("/v1/chat/completions", payload)
            wall = time.perf_counter() - start
            log_text = None
            if log_path:
                if log_path.stat().st_size < offset:
                    raise BenchError("engine log rotated during request")
                with log_path.open("rb") as stream:
                    stream.seek(offset)
                    log_text = stream.read().decode("utf-8", "replace")
            row = measured(answer, wall, log_text)
            row.update(test=case.name, repetition=case.repetition, request=payload, response=answer,
                       reference_prompt_tokens=case.reference_prompt_tokens,
                       reference_prompt_tokens_match=row["prompt_tokens"] == case.reference_prompt_tokens)
            report["runs"].append(row)
            write_json(output, report)
            if case.name == "pp16k" and row["cached_tokens"] != 0:
                raise BenchError("pp16k reused prompt tokens or cache reuse is unknown; this is not a fresh 16K prefill measurement. Clear the server's prompt cache or use a freshly loaded service.")
            if not row["reference_prompt_tokens_match"]:
                warning = f"{case.name}: prompt has {row['prompt_tokens']} tokens; Qwen reference has {case.reference_prompt_tokens}"
                report["warnings"].append(warning)
                if strict_tokens:
                    raise BenchError(warning)
            message = ((answer.get("choices") or [{}])[0].get("message") or {})
            if message.get("reasoning_content"):
                raise BenchError("server generated thinking despite enable_thinking=false")
            if case.name == "struct":
                try:
                    content = json.loads(message.get("content", ""))
                    row["json_valid"] = True
                    row["json_items"] = len(content) if isinstance(content, list) else None
                except (ValueError, TypeError):
                    row["json_valid"] = False
                row["reference_output_tokens_match"] = row["output_tokens"] == 7230
                if not row["json_valid"] or row.get("json_items") != 200:
                    raise BenchError("struct fixture must return a JSON array with 200 items")
            after = client.status()
            a, b = before["activity"].get("requests"), after["activity"].get("requests")
            if a is not None and b is not None and b != a + 1:
                raise BenchError("request counter changed unexpectedly; another client or server restart interfered")
            write_json(output, report)
            progress(json.dumps({key: row[key] for key in ("test", "repetition", "prompt_tokens", "cached_tokens",
                                                          "output_tokens", "prefill_tok_s", "decode_tok_s", "wall_s")}))
        report["summary"] = summarize(report["runs"])
        report["final_status"] = client.status()
        if any(not value["all_engine_timings_present"] for value in report["summary"].values()):
            report["warnings"].append("Engine timings missing: end_to_end_tok_s is HTTP throughput, not decode speed.")
        report["complete"] = True
    except BaseException as exc:
        text = str(exc) or type(exc).__name__
        if client.api_key:
            text = text.replace(client.api_key, "<redacted>")
        report["error"] = text
        raise
    finally:
        monitor.close()
        write_json(output, report)
    return report
