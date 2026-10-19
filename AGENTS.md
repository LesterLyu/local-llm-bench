# Working on local-llm-bench

- Preserve `local-chat-v1`: prompts, sampling, limits, nonce policy, no hidden
  warmup, and the fixed two-round sequence. Protocol changes require a new ID.
- Default `all-v1` runs the original suite first, then three streaming repetitions
  including 32K. Keep each sub-suite's protocol identity and report intact.
- Support both Basalt and Strata over their existing APIs. Never start, stop,
  unload or reconfigure a user's inference service during a benchmark.
- Never substitute HTTP throughput for missing engine decode/prefill rates.
- Keep per-round results, token counts, cache reuse and measured GPU power limits.
  Visible GPUs do not imply text/vision/peer assignments.
- Keep runtime dependencies optional or absent. No machine-specific model/cache
  paths or user benchmark reports in the repository.
- Run `python -m unittest discover -s tests -v` and a package/dry-run smoke check.
  GPU tests use an already loaded idle service, one request at a time.
- Follow the user's requested commit/push scope. Keep credentials out of reports.
