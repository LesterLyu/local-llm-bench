# all-v1 (default)

`local-llm-bench run --base-url URL` runs both suites against the same loaded,
idle server, without stopping or reconfiguring it:

1. [`local-chat-v1`](protocol.md): two rounds of conv/struct/prose/pp16k, then
   two consecutive code requests. Exactly the original ten-request protocol.
2. [`serving-v1`](serving.md): three repetitions, with 32K enabled. Two explicit
   calibration requests, nine chats, nine prefill requests and eighteen agent
   turns, for 38 requests.

The default total is **48 requests**, 46 measured plus two calibration requests.
Server context limits can skip individual streaming tests; skips are recorded.
Requests remain serial and adaptive engine state naturally carries across them.
Each sub-suite retains its own order, sampling and aggregation policy. There is
no extra warmup inserted between them.

`--suite all` explicitly selects this default. `--rounds N` overrides repetitions
in both sub-suites: e.g. `--rounds 1` is 5 original plus 14 streaming requests.
The default keeps two original and three streaming repetitions. Full mode always
includes 32K; `--long` is accepted but unnecessary. The optional native log and
strict Qwen token checks apply to the original sub-suite only.

To select an individual suite:

```sh
local-llm-bench run --suite local-chat-v1 --base-url URL
local-llm-bench run --suite serving-v1 --long --base-url URL
local-llm-bench run --dry-run
```

## Report and failure handling

One JSON report identifies `protocol=all-v1`, both fixture fingerprints via a
combined fingerprint, and each sub-suite's repetition count. `reports` contains
the complete child reports, including original requests and responses. Top-level
`runs` provides a measurement index with a `suite` field and no duplicate bodies.
Progress is persisted after each measured response (including calibration).

On an error or interruption, completed original results and any partial extension
results remain embedded in the incomplete combined JSON. Temporary staging files
are removed after embedding. Existing output reports are never overwritten.

Successful runs automatically write a sibling Markdown report (e.g. `run.json`
and `run.md`). The default compact layout merges both suites into one table,
retains all repetitions, uses means for original tests and medians for streaming
tests, and includes TTFT, weighted draft acceptance, cache statistics and separate
calibration rows. Unknown metrics stay unavailable. `--lang zh` selects Chinese;
`--layout detailed` selects the earlier English layout. Existing Markdown files
are checked before any inference request and are never overwritten.

`report INPUT.json --output SHARE.md` renders the same report offline. `compare OLD NEW`
compares combined reports in separate tables, preserving original means/round 2
and streaming medians. A combined report can also be compared with a single-suite
or original Windows legacy report: only the matching child is selected and the
exclusion is announced. Matching protocol/fixture/round/option checks still apply.
