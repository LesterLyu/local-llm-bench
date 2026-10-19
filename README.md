# local-llm-bench

A fixed live-API benchmark for **Basalt and Strata**, on Windows or Linux. Run the
same requests against either server and compare JSON reports, without compiling
a benchmark binary or pointing at a local model cache.

Python 3.10+, no runtime dependencies. The model must already be loaded and idle.
The tool never starts, stops, unloads or reconfigures your server. Benchmark
requests naturally warm prompt, kernel and expert caches.

[中文说明](README.zh-CN.md) · [Exact protocol](docs/protocol.md)

## Install

```sh
git clone https://github.com/LesterLyu/local-llm-bench.git
cd local-llm-bench
python -m pip install .
```

## Run

Start your inference service normally, then use its actual URL:

```sh
# Basalt example
local-llm-bench run --base-url http://127.0.0.1:18235 --label basalt-build-A

# Strata example
local-llm-bench run --base-url http://127.0.0.1:18234 --label strata-build-A
```

`python -m local_llm_bench` is equivalent if the console command is not on PATH.
An optional trailing `/v1` on the URL is accepted. Model aliases are discovered
from `/v1/status`; override with `--model NAME` if necessary.

The default runs **two rounds** of `conv → struct → prose → pp16k`, followed by
two consecutive `code` requests. Both rounds are retained, including the first;
there is no hidden warmup or discarded measurement.

| Case | Maximum output tokens | Temperature |
|---|---:|---:|
| conv: B-tree vs LSM tree | 1200 | 0.6 |
| struct: fixed JSON array of 200 items | 8192 | 0 |
| prose: essay on the Odyssey | 1200 | 0.6 |
| pp16k: fixed input, round-specific prefix | 32 | 0 |
| code: asyncio worker pool | 1200 | 0.6 |

Every request disables thinking and uses `top_p=0.95`, `top_k=20`, `min_p=0`,
presence penalty 0 and repetition penalty 1. This preserves the original Windows
live-speed script's prompts, request settings and order.

Useful options:

```sh
local-llm-bench run --dry-run
local-llm-bench run --base-url http://127.0.0.1:18235 --output results/build-A.json \
  --label build-A --notes "NVFP4; 5090 text; 5060 Ti vision; spec=7; KV=int8"
```

In PowerShell, put the command on one line or use a backtick for continuation.
`--rounds N` is available, but reports mark non-default round counts as custom.
Use `--strict-tokens` to require the exported Qwen reference prompt counts.

For authenticated services, set `LOCAL_LLM_BENCH_API_KEY` in the environment, or
select another variable with `--api-key-env NAME`. Keys are not saved in reports.

## Measurements

- **Decode and prefill rates** come from each response's `timings`, shared by
  Basalt and Strata. They are engine rates, not HTTP throughput.
- **End-to-end throughput** is stored separately as `end_to_end_tok_s`.
  Missing engine timings remain unavailable; they are never replaced by wall time.
- **Optional native log mode:** `--engine-log /path/to/engine.log` reads that
  request's native summary, matching the original Windows script exactly. It
  checks the summary against the response token counts. Log access is not needed
  for the normal API mode or a remote server.
- **Hardware:** local runs record available NVIDIA devices, actual power limits
  and periodic utilization/memory/power/clock samples. GPU roles are not inferred
  from visibility. Use `--notes` to state text, peer and vision assignments.
  Remote URLs do not collect the client's hardware as if it were the server's.
  Missing `nvidia-smi` is supported. `--no-hardware` disables telemetry.
- **Validation:** the JSON case must return an array with 200 items. Qwen prompt
  token counts are checked and recorded; differences warn by default, since
  other models/templates can tokenize the same text differently. Fresh pp16k
  measurements require zero reused prompt tokens.

Requests are serial. The tool refuses a busy/unloaded service and detects
unexpected request-counter changes. Avoid using the service from another client
during a run. On failure or Ctrl+C, partial results are retained and marked
incomplete. Existing output files are never overwritten.

## Compare

```sh
local-llm-bench compare results/build-A.json results/build-B.json
local-llm-bench compare results/build-A.json results/build-B.json --json
```

The table includes both-round means and separate second-round values. `pp16k`
is input tokens/s; other cases are output tokens/s. The original Windows
`live-speed.py` reports are supported as legacy inputs with an explicit warning
that their protocol identity cannot be checked automatically.

Use the same model, quantization, context, speculation settings, expert tiers,
GPU roles and power limits. Record build labels and server options in `--notes`.
For version comparisons, alternate A/B/A/B in one session; a difference between
two independent runs is an observation, not proof of an engine speedup.

The fixed suite is Qwen-oriented. It measures these workloads, not arbitrary
Codex sessions, tool calling, vision, concurrent serving or output quality.
Changing the prompts, order, limits or sampling requires a new protocol version.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

Mock API integration tests cover Basalt and Strata contracts, exact request
ordering, partial failures, missing timings, authentication and legacy reports.
CI runs on Windows and Linux. Real GPU measurements are intentionally not CI jobs.

MIT licensed; see [upstream fixture attribution](THIRD_PARTY_NOTICES.md).
