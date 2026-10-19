"""Small JSON client; authentication is never included in reports."""
import json
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
