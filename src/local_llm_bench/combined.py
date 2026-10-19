"""Run both versioned suites in order, with one durable combined report."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from . import __version__, protocol, runner, serving
from .client import BenchError

PROTOCOL = "all-v1"


def identity():
    manifest = {"protocol": PROTOCOL, "order": [protocol.PROTOCOL, serving.PROTOCOL],
                "fixtures": {protocol.PROTOCOL: protocol.bundle()[1], serving.PROTOCOL: serving.bundle()[1]},
                "default_rounds": {protocol.PROTOCOL: 2, serving.PROTOCOL: 3}, "long": True}
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def plan(rounds=None):
    old_rounds, new_rounds = rounds if rounds is not None else 2, rounds if rounds is not None else 3
    requests = [{"suite": protocol.PROTOCOL, "test": c.name, "repetition": c.repetition,
                 "max_tokens": c.max_tokens, "temperature": c.temperature,
                 "reference_prompt_tokens": c.reference_prompt_tokens} for c in protocol.plan(old_rounds)]
    requests += [dict(r, suite=serving.PROTOCOL) for r in serving.plan(new_rounds, long=True)["requests"]]
    return {"protocol": PROTOCOL, "fixture_sha256": identity(), "canonical_rounds": rounds is None,
            "rounds": {protocol.PROTOCOL: old_rounds, serving.PROTOCOL: new_rounds},
            "long": True, "requests": requests}


def run(client, output, *, rounds=None, model=None, engine_log=None, strict_tokens=False,
        hardware=True, label="", notes="", progress=print):
    output = Path(output)
    if output.exists():
        raise BenchError(f"output file already exists: {output}")
    sequence = plan(rounds)
    report = {"schema_version": 1, "protocol": PROTOCOL, "fixture_sha256": sequence["fixture_sha256"],
              "tool_version": __version__, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "endpoint": client.base_url, "label": label, "notes": notes, "rounds": sequence["rounds"],
              "canonical_rounds": sequence["canonical_rounds"], "options": {"long": True},
              "reports": {}, "runs": [], "warnings": [], "complete": False}
    output.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(name, path):
        if path.exists():
            report["reports"][name] = json.loads(path.read_text(encoding="utf-8"))
        report["runs"] = [dict({k: v for k, v in row.items() if k not in ("request", "response")}, suite=suite)
                          for suite, child in report["reports"].items() for row in child["runs"]]
        report["warnings"] = [f"{suite}: {w}" for suite, child in report["reports"].items() for w in child["warnings"]]
        runner.write_json(output, report)

    try:
        # Child files are private staging data. Every progress callback and the
        # failure path embeds them in the public report before cleanup.
        with tempfile.TemporaryDirectory(prefix=".llm-bench-", dir=output.parent) as staging:
            for name in (protocol.PROTOCOL, serving.PROTOCOL):
                child_path = Path(staging) / f"{name}.json"

                def update(line):
                    snapshot(name, child_path)
                    progress(f"[{name}] {line}")

                options = dict(rounds=sequence["rounds"][name], model=model, hardware=hardware,
                               label=label, notes=notes, progress=update)
                try:
                    if name == protocol.PROTOCOL:
                        child = runner.run(client, child_path, engine_log=engine_log, strict_tokens=strict_tokens, **options)
                    else:
                        child = serving.run(client, child_path, long=True, **options)
                    report["model"] = child["model"]
                finally:
                    snapshot(name, child_path)
        report["complete"] = True
    except BaseException as exc:
        text = str(exc) or type(exc).__name__
        report["error"] = text.replace(client.api_key, "<redacted>") if client.api_key else text
        raise
    finally:
        runner.write_json(output, report)
    return report
