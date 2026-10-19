"""The versioned request sequence, shared by every backend."""
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json

PROTOCOL = "local-chat-v1"


@dataclass(frozen=True)
class Case:
    name: str
    repetition: int
    prompt: str
    max_tokens: int
    temperature: float
    reference_prompt_tokens: int

    def payload(self, model):
        return {
            "model": model,
            "messages": [{"role": "user", "content": self.prompt}],
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": self.temperature,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repetition_penalty": 1.0,
            "max_tokens": self.max_tokens,
            "stream": False,
        }


def bundle():
    raw = files("local_llm_bench").joinpath("data/fixtures.json").read_bytes()
    data = json.loads(raw)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return data, hashlib.sha256(canonical).hexdigest()


def plan(rounds=2):
    if rounds < 1:
        raise ValueError("rounds must be positive")
    data, _ = bundle()
    fixtures = data["fixtures"]
    cases = []
    for rep in range(1, rounds + 1):
        for name, limit, temp in (("conv", 1200, 0.6), ("struct", 8192, 0.0),
                                  ("prose", 1200, 0.6), ("pp16k", 32, 0.0)):
            f = fixtures["prefill" if name == "pp16k" else name]
            prompt = f["prompt"]
            if name == "pp16k":
                prompt = f"{rep} {rep + 3} {rep + 5} {rep + 6}\n" + prompt
            cases.append(Case(name, rep, prompt, limit, temp, f["expected_prompt_tokens"]))
    f = fixtures["code"]
    cases.extend(Case("code", rep, f["prompt"], 1200, 0.6, f["expected_prompt_tokens"])
                 for rep in range(1, rounds + 1))
    return cases
