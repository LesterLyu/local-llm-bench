import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

from . import __version__
from .client import BenchError, Client
from .compare import compare, load, table
from .protocol import PROTOCOL, plan
from .runner import run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fixed live-API benchmark for Basalt and Strata; never starts or restarts a model.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    bench = commands.add_parser("run", help="benchmark an already loaded, idle service")
    bench.add_argument("--base-url", default="http://127.0.0.1:8080", help="server URL, with or without /v1")
    bench.add_argument("--model", help="override the alias from /v1/status")
    bench.add_argument("--rounds", type=int, default=2)
    bench.add_argument("--output", type=Path, help="new result JSON file (never overwrites)")
    bench.add_argument("--api-key-env", default="LOCAL_LLM_BENCH_API_KEY", help="environment variable containing the bearer key")
    bench.add_argument("--timeout", type=float, default=300, help="HTTP timeout in seconds")
    bench.add_argument("--engine-log", type=Path, help="optional local log; use native timing summaries as in the original Windows script")
    bench.add_argument("--strict-tokens", action="store_true", help="require Qwen reference prompt token counts")
    bench.add_argument("--no-hardware", action="store_true")
    bench.add_argument("--label", default="", help="build/commit label for this run")
    bench.add_argument("--notes", default="", help="model quantization, server options, GPU text/vision roles, etc.")
    bench.add_argument("--dry-run", action="store_true", help="print the request plan without network or hardware access")
    diff = commands.add_parser("compare", help="compare two completed reports, including original Windows reports")
    diff.add_argument("old")
    diff.add_argument("new")
    diff.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            result = compare(load(args.old), load(args.new))
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(table(result["rows"]))
                print("\npp16k measures input tokens/s; all other rows measure output tokens/s.")
                for warning in result["warnings"]:
                    print("Note: " + warning)
            return 0
        if args.rounds < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise BenchError("rounds and timeout must be positive")
        if args.dry_run:
            print(json.dumps({"protocol": PROTOCOL, "canonical_rounds": args.rounds == 2,
                              "requests": [{"test": c.name, "repetition": c.repetition,
                                            "max_tokens": c.max_tokens, "temperature": c.temperature,
                                            "reference_prompt_tokens": c.reference_prompt_tokens} for c in plan(args.rounds)]}, indent=2))
            return 0
        output = args.output or Path("results") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
        client = Client(args.base_url, os.environ.get(args.api_key_env, ""), args.timeout)
        report = run(client, output, rounds=args.rounds, model=args.model, engine_log=args.engine_log,
                     strict_tokens=args.strict_tokens, hardware=not args.no_hardware, label=args.label, notes=args.notes,
                     progress=lambda line: print(line, flush=True))
        print("\nSummary (engine tok/s; pp16k = input, others = output):")
        for name, value in report["summary"].items():
            mean = value["mean"]
            print(f"  {name}: {mean:.1f}" if mean is not None else f"  {name}: unavailable (HTTP throughput is stored separately)")
        for warning in report["warnings"]:
            print("Note: " + warning)
        print("Results: " + str(output.resolve()))
        return 0
    except KeyboardInterrupt:
        print("Interrupted; partial results were retained.", file=sys.stderr)
        return 130
    except (BenchError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1
