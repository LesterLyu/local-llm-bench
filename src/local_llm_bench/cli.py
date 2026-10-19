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
from . import combined, serving
from .report import markdown, write_markdown


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fixed live-API benchmark for Basalt and Strata; never starts or restarts a model.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    bench = commands.add_parser("run", help="benchmark an already loaded, idle service")
    bench.add_argument("--base-url", default="http://127.0.0.1:8080", help="server URL, with or without /v1")
    bench.add_argument("--model", help="override the alias from /v1/status")
    bench.add_argument("--suite", choices=("all", PROTOCOL, serving.PROTOCOL), default="all",
                       help="all tests including 32K (default), or one suite")
    bench.add_argument("--rounds", type=int, help="override repetitions in each selected suite (default: 2 historical, 3 serving)")
    bench.add_argument("--long", action="store_true", help="add 32K to a serving-v1-only run (always included in all)")
    bench.add_argument("--output", type=Path, help="new result JSON file (never overwrites)")
    bench.add_argument("--api-key-env", default="LOCAL_LLM_BENCH_API_KEY", help="environment variable containing the bearer key")
    bench.add_argument("--timeout", type=float, default=300, help="HTTP timeout in seconds")
    bench.add_argument("--engine-log", type=Path, help="optional local log; use native timing summaries as in the original Windows script")
    bench.add_argument("--strict-tokens", action="store_true", help="require Qwen reference prompt token counts")
    bench.add_argument("--no-hardware", action="store_true")
    bench.add_argument("--label", default="", help="build/commit label for this run")
    bench.add_argument("--notes", default="", help="model quantization, server options, GPU text/vision roles, etc.")
    bench.add_argument("--dry-run", action="store_true", help="print the request plan without network or hardware access")
    bench.add_argument("--layout", choices=("compact", "detailed"), default="compact", help="automatic Markdown report layout")
    bench.add_argument("--lang", choices=("en", "zh"), default="en", help="compact report language (detailed is English)")
    diff = commands.add_parser("compare", help="compare two completed reports, including original Windows reports")
    diff.add_argument("old")
    diff.add_argument("new")
    diff.add_argument("--json", action="store_true")
    view = commands.add_parser("report", help="render a saved report as shareable Markdown, offline")
    view.add_argument("input")
    view.add_argument("--output", type=Path, help="new Markdown file; otherwise print to stdout")
    view.add_argument("--layout", choices=("compact", "detailed"), default="compact")
    view.add_argument("--lang", choices=("en", "zh"), default="en", help="compact report language (detailed is English)")
    args = parser.parse_args(argv)
    try:
        if args.command in ("run", "report") and args.layout == "detailed" and args.lang != "en":
            raise BenchError("the detailed report is English; use --layout compact with --lang zh")
        if args.command == "report":
            report = load(args.input)
            if args.output:
                write_markdown(args.output, report, layout=args.layout, language=args.lang)
                print("Report: " + str(args.output.resolve()))
            else:
                print(markdown(report, layout=args.layout, language=args.lang))
            return 0
        if args.command == "compare":
            result = compare(load(args.old), load(args.new))
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                if "suites" in result:
                    for name, child in result["suites"].items():
                        print("\n" + name + ":")
                        print(table(child["rows"]))
                        for warning in child["warnings"]:
                            print("Note: " + warning)
                else:
                    print(table(result["rows"]))
                if result.get("rows") and "old_mean" in result["rows"][0]:
                    print("\npp16k measures input tokens/s; all other rows measure output tokens/s.")
                for warning in result["warnings"]:
                    print("Note: " + warning)
            return 0
        extended = args.suite == serving.PROTOCOL
        all_tests = args.suite == "all"
        if not all_tests:
            args.rounds = args.rounds if args.rounds is not None else (3 if extended else 2)
        if (args.rounds is not None and args.rounds < 1) or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise BenchError("rounds and timeout must be positive")
        if args.long and not (extended or all_tests):
            raise BenchError("--long requires --suite serving-v1")
        if extended and (args.engine_log or args.strict_tokens):
            raise BenchError("--engine-log and --strict-tokens apply to local-chat-v1 only")
        if args.dry_run:
            if all_tests:
                print(json.dumps(combined.plan(args.rounds), indent=2))
                return 0
            if extended:
                print(json.dumps(serving.plan(args.rounds, args.long), indent=2))
                return 0
            print(json.dumps({"protocol": PROTOCOL, "canonical_rounds": args.rounds == 2,
                              "requests": [{"test": c.name, "repetition": c.repetition,
                                            "max_tokens": c.max_tokens, "temperature": c.temperature,
                                            "reference_prompt_tokens": c.reference_prompt_tokens} for c in plan(args.rounds)]}, indent=2))
            return 0
        output = args.output or Path("results") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
        report_path = output.with_suffix(".md") if output.suffix.lower() == ".json" else Path(str(output) + ".md")
        if report_path.exists():
            raise BenchError(f"Markdown report already exists: {report_path}")
        client = Client(args.base_url, os.environ.get(args.api_key_env, ""), args.timeout)
        options = dict(rounds=args.rounds, model=args.model, hardware=not args.no_hardware, label=args.label, notes=args.notes,
                       progress=lambda line: print(line, flush=True))
        if all_tests:
            report = combined.run(client, output, engine_log=args.engine_log, strict_tokens=args.strict_tokens, **options)
        elif extended:
            report = serving.run(client, output, long=args.long, **options)
        else:
            report = run(client, output, engine_log=args.engine_log, strict_tokens=args.strict_tokens, **options)
        write_markdown(report_path, report, layout=args.layout, language=args.lang)
        print(markdown(report, layout=args.layout, language=args.lang))
        print("Results: " + str(output.resolve()))
        print("Report: " + str(report_path.resolve()))
        return 0
    except KeyboardInterrupt:
        print("Interrupted; partial results were retained.", file=sys.stderr)
        return 130
    except (BenchError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1
