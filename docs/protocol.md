# local-chat-v1

This protocol preserves the original Windows live API test, not the standalone
native `basalt-bench` process protocol. Reports from either program should not be
silently mixed: the native program also differs in prefill output length.

## Requests

For repetitions 1 and 2, run `conv`, `struct`, `prose`, `pp16k` in order. Then run
`code` repetitions 1 and 2. Draft and expert state carry between requests. No
extra warmup is inserted; both full rounds are retained.

All requests use `/v1/chat/completions`, one user message, no streaming, no system
message, `chat_template_kwargs.enable_thinking=false`, `top_p=0.95`, `top_k=20`,
`min_p=0`, `presence_penalty=0`, `repetition_penalty=1`. No fixed seed is sent.

| Fixture | Max output | Temperature | Reference input tokens |
|---|---:|---:|---:|
| conv | 1200 | 0.6 | 38 |
| struct | 8192 | 0 | 98 |
| prose | 1200 | 0.6 | 19 |
| pp16k | 32 | 0 | 16680 |
| code | 1200 | 0.6 | 57 |

The prompts are bundled in `src/local_llm_bench/data/fixtures.json`. The first
four were decoded from Basalt's published `data/bench/*.ids` and verified by
re-rendering/tokenizing them with the reference Qwen tokenizer. Source commit
and ID-file SHA-256 values are recorded in the bundle. The code prompt is the
original Windows script's asyncio worker pool prompt.

For pp16k, prepend `r r+3 r+5 r+6` and a newline to the prefill prompt, where `r`
is the repetition number. This distinguishes the rounds while preserving the
historical input. Across repeated benchmark runs a server with conversation
caching may restore an older prompt: nonzero pp16k cache reuse aborts the run
instead of reporting a cached tail as fresh 16K prefill.

The JSON fixture expects an array with 200 items. The Qwen reference generates
7230 tokens. A different tokenizer can produce a different token count for
identical text; reports retain counts and do not call the number alone a quality
check. For sampled cases, output length/content can vary.

## Reporting

Schema version 1 stores protocol ID, bundled fixture fingerprint, tool version,
endpoint, model, run labels/notes, runner platform, optional local hardware,
initial/final status, every request/response and per-request measurements.
It does not collect hostnames or authentication headers.

Engine rates are `timings.predicted_per_second` and `timings.prompt_per_second`.
Optional native-log mode uses the same summary parser as the original Windows
script and checks its token counts against the response. Native rounding can
differ slightly from API rounding. Source is recorded per request.
Missing rates remain null, even when end-to-end throughput is available.

Every round remains visible. Means include the first round; second-round values
are also reported. Prefill input throughput and decode output throughput remain
distinct. A comparison refuses different declared protocol IDs, fixture hashes
or round counts. Legacy reports can be read with a warning, since their original
protocol and settings cannot be verified from a version field.

Do not infer text, peer or vision assignments from GPU visibility. Record actual
server settings and model quantization in notes. For non-loopback URLs local
hardware collection is disabled, because client hardware is not server hardware.
Loopback forwarding/containers may still require notes describing the real host.

## Version changes

Keep this suite stable. Change the protocol ID when prompts, ordering, output
limits, sampling, prefill nonce policy or warmup policy change. New result fields
need a schema version change only when they break readers. Runtime metadata and
measurement availability must remain explicit rather than being fabricated.
