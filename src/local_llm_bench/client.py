"""Small JSON client; authentication is never included in reports."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class BenchError(Exception):
    pass


class Client:
    def __init__(self, base_url, api_key="", timeout=300):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise BenchError("base URL must be an http:// or https:// URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BenchError("base URL must not contain credentials, a query or a fragment")
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.local = parsed.hostname.lower() in ("localhost", "127.0.0.1", "::1")
        self.api_key = api_key
        self.timeout = timeout

    def request(self, path, payload=None):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", "replace")
            if self.api_key:
                detail = detail.replace(self.api_key, "<redacted>")
            raise BenchError(f"HTTP {exc.code} on {path}: {detail}") from None
        except (OSError, ValueError) as exc:
            raise BenchError(f"request to {path} failed: {exc}") from None
        if not isinstance(result, dict):
            raise BenchError(f"{path} returned a non-object JSON response")
        if result.get("error"):
            detail = str(result["error"])
            if self.api_key:
                detail = detail.replace(self.api_key, "<redacted>")
            raise BenchError(detail)
        return result

    def status(self):
        status = self.request("/v1/status")
        if status.get("loaded") is False:
            raise BenchError("model is unloaded; load it in the server before benchmarking")
        if "in_flight" not in (status.get("activity") or {}):
            raise BenchError("server must expose activity.in_flight at /v1/status (Basalt or Strata)")
        if status["activity"]["in_flight"]:
            raise BenchError("server is busy; run the benchmark without other requests")
        return status

    def stream_chat(self, payload):
        """Read chat SSE; TTFT is the first nonempty content/reasoning delta, not a heartbeat."""
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.base_url + "/v1/chat/completions",
                                         data=json.dumps(payload).encode("utf-8"), headers=headers)
        start, first = time.perf_counter(), None
        message = {"role": "assistant", "content": ""}
        answer, events, finished = {"choices": [{"message": message}]}, [], False

        def event(lines):
            nonlocal first, finished
            data = "\n".join(lines)
            if data == "[DONE]":
                finished = True
                return
            chunk = json.loads(data)
            if not isinstance(chunk, dict):
                raise BenchError("stream returned a non-object JSON event")
            if chunk.get("error"):
                raise BenchError(str(chunk["error"]))
            for choice in chunk.get("choices") or []:
                if choice.get("index", 0) != 0:
                    raise BenchError("benchmark expects a single streamed choice")
                delta = choice.get("delta") or {}
                if delta.get("tool_calls"):
                    raise BenchError("serving suite expects text answers; the model called a tool")
                for key in ("content", "reasoning_content"):
                    piece = delta.get(key)
                    if piece:
                        if first is None:
                            first = time.perf_counter() - start
                        message[key] = message.get(key, "") + piece
                if choice.get("finish_reason"):
                    answer["choices"][0]["finish_reason"] = choice["finish_reason"]
            for key in ("usage", "timings", "model"):
                if chunk.get(key) is not None:
                    answer[key] = chunk[key]

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raise BenchError("server did not return a text/event-stream response")
                for raw in response:
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if line.startswith("data:"):
                        events.append(line[5:].lstrip(" "))
                    elif not line and events:
                        event(events)
                        events = []
                        if finished:
                            break
                if events and not finished:
                    event(events)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", "replace")
            if self.api_key:
                detail = detail.replace(self.api_key, "<redacted>")
            raise BenchError(f"HTTP {exc.code} on streamed chat: {detail}") from None
        except (OSError, ValueError, BenchError) as exc:
            detail = str(exc)
            if self.api_key:
                detail = detail.replace(self.api_key, "<redacted>")
            raise BenchError(f"streamed chat failed: {detail}") from None
        if not finished:
            raise BenchError("stream ended without [DONE]; response may be truncated")
        if first is None:
            raise BenchError("stream completed without any content or reasoning tokens")
        return answer, time.perf_counter() - start, first
