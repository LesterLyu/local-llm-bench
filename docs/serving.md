# serving-v1

A serial streaming API workload adapted from Strata's
`tools/strata_bench.py`, `tools/ab_engine.py` and `tools/agent_turns_bench.py` at
commit `296b59d2f73c8f04a805df29c2fa97655adace78`. Fixture provenance and MIT
attribution are distributed with the package. Basalt's conv/struct/prose/prefill
fixtures remain available in the unchanged `local-chat-v1` suite. Both suites
run by default as `all-v1`; this document describes the streaming sub-suite.

## Sequence and requests

The default is three repetitions. There is no hidden warmup or discarded round:

1. Two length-calibration requests, 200 and 800 generated words, one output token
   each. These are recorded with `phase=calibration` and excluded from summaries.
2. Each of three short chat prompts, repeated three times, maximum 100 output tokens.
3. Each input target (4096, 16384, optionally 32768 tokens), repeated three times,
   maximum 24 output tokens.
4. Three independent agent sessions, each with an initial reply followed by five
   growing document turns, maximum 64 output tokens per request. All four tool
   definitions are sent with each request; the instruction requests text answers.
   A generated tool call aborts the run; no tool is executed.

Selecting `--suite serving-v1` alone runs 35 requests (two calibration, 33 measured),
or 38 with `--long`. The full default `all-v1` always includes 32K and therefore
uses the 38-request streaming variant. Context skips can reduce this. `--dry-run` prints the maximum planned
sequence offline; it cannot predict server context skips.

Every request uses `stream=true`, `stream_options.include_usage=true`, thinking
off, temperature 0, top-p .95, top-k 20, min-p 0, presence penalty 0, repetition
penalty 1. No fixed seed is sent to the model. UUID4 prefixes distinguish fresh
prompts. A fresh session UUID starts the agent system message; it is preserved
for that session, as are all prior messages and actual assistant replies.

Fixtures, vocabulary, sampling and limits are in `data/serving.json`; the SHA-256
fingerprint uses canonical JSON and is independent of LF/CRLF formatting.
The word generator uses Python `random.Random(7).choice` over the bundled
vocabulary. The same generated 1500-word body is used for each of the five
synthetic files, with a distinct `generated/partN.txt` name. It is a controlled
cache workload, not a realistic sample of arbitrary repositories.

## Input sizing and cache policy

The calibration slope is `(tokens800 - tokens200) / 600`. Fixed overhead is
`max(0, tokens200 - slope * 200)`. Prompt words are
`max(50, int((target - overhead) / slope))`. Actual input counts are retained;
deviations exceeding 10% warn. Calibration failures abort, rather than falling
back to an unverified token estimate.

Fresh-prefill tests require known zero cached prompt tokens. Agent cache reuse
is intentional, is retained even when zero, and stays null when unknown.
The client neither clears caches nor reconfigures the server.

The server must expose its context limit. A prefill target is skipped when
`target + 24 + 256 > context`. Agent sessions are skipped below the larger of
10240 and the estimated five-document size (`1500 * 5 * slope + overhead + 1024
+ 6 * 64`). An unusual tokenizer/template can still exceed this approximation;
server errors remain visible as incomplete reports.

## Measurements and comparison

- TTFT starts before the HTTP request and ends at the first nonempty content or
  reasoning delta. Heartbeats, role-only and empty deltas do not count. It
  includes network/template/queue time and is not native-engine first-token time.
- SSE must terminate with `[DONE]`. Usage/timings from the final chunk are retained.
  Token counts come from usage, never the number of streamed chunks.
- Engine decode/prefill rates use response timings exclusively. There is no
  client-estimated decode fallback. HTTP throughput is stored separately.
- Draft offered/accepted counts come from `draft_n`/`draft_n_accepted`.
  Acceptance is unavailable when offered is zero or counters are missing.
  Window timings, draft depth and quality scores are not inferred from counters.
- Statistics retain every repetition and report median/min/max. If any member
  lacks a metric, that aggregate stays null, rather than averaging a subset.
- Per-session cache hit percentage is `sum(cached) / sum(prompt) * 100` over
  follow-up turns. Earlier tokens re-read are
  `sum(max(0, previous_prompt + previous_output - current_cached))`.
  Unknown cache counts make these aggregates null.
- Markdown draft acceptance is `sum(accepted) / sum(offered) * 100`, weighted
  by draft counts; it is not the median of request acceptance percentages.

Comparison requires the same protocol, fixture fingerprint, repetitions,
`--long` option, skipped tests and request sequence. Fresh UUIDs intentionally
differ; agent replies are model-generated and may vary. Identical settings and
similar token counts are necessary to interpret differences. Lower TTFT and
re-read counts are better; higher engine throughput and cache-hit rates are better.

## Relationship to the built-in tools

Strata's benchmark may start its own server, perform warmup/calibration, read its
own repository documents, and optionally test concurrent clients. This tool
always uses an already loaded idle server, bundles its workload, separates
calibration, and keeps requests serial. Numbers are not interchangeable.

Basalt's native `basalt-bench` starts an engine and uses pre-tokenized inputs. Its
quality gates and per-window timings require native execution/logs and remain
in that tool. API response draft counters are integrated here for both engines.
Neither native engine startup nor quality evaluation is added to this API suite.
