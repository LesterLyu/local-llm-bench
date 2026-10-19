# Fixture and benchmark attribution

The optional `serving-v1` workload adapts streaming measurements, short prompts,
the seed-7 word-list generator, agent system/tool definitions and cache metrics
from [Niko1221/Strata](https://github.com/Niko1221/Strata), commit
`296b59d2f73c8f04a805df29c2fa97655adace78`, files `tools/strata_bench.py`,
`tools/ab_engine.py` and `tools/agent_turns_bench.py`. The implementation has been
adapted for this project's versioned API-only protocol; it is not the upstream
suite or a claim of identical benchmark results. The upstream MIT notice below
also applies to those adapted portions.

The conv, struct, prose and prefill prompts were decoded from `data/bench/*.ids`
in [jesdga95/basalt](https://github.com/jesdga95/basalt), commit
`63b40f2d2ac12b8cf00d5d1de53dba99b1269d24`. The bundled data records the source
file hashes. No model weights, tokenizer vocabulary or inference engine code is
distributed here.

Basalt is a fork of Strata. Both upstream copyright notices are retained below:

MIT License

Copyright (c) 2026 Jesús Gulfo
Copyright (c) 2026 Niko1221 and the Strata contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
