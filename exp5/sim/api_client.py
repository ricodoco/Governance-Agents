"""Anthropic Messages API client with the same chat() interface as OllamaClient (stdlib only).

Used by sim/api_run.py to run the sealed independent purchasing and delegation sets on a
commercial model without changing any existing module. Temperature 0. Responses are cached
by request hash, so a rerun costs nothing and a crash loses nothing.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"


def extract_json(text: str) -> str:
    """The prompt asks for exactly one JSON object; tolerate code fences or a stray sentence."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        return m.group(1)
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if i != -1 and j > i else t


class AnthropicClient:
    def __init__(self, model, host=None, num_ctx=16384, temperature=0.0, cache_dir=None,
                 timeout=120, max_tokens=300, api_key=None, retries=8, fallback_tokens=2000):
        self.model, self.num_ctx, self.temperature = model, num_ctx, temperature
        self.cache_dir, self.timeout, self.max_tokens, self.retries = cache_dir, timeout, max_tokens, retries
        self.fallback_tokens = fallback_tokens   # a reply cut off at max_tokens is fetched again with this limit
        self.api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
        self.calls = 0
        self._lock = threading.Lock()
        self._send_temperature = True
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _body(self, messages):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "messages": rest}
        if self._send_temperature:
            body["temperature"] = self.temperature
        return body

    def _post(self, body):
        req = urllib.request.Request(API_URL, json.dumps(body).encode(), {
            "Content-Type": "application/json", "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def _send(self, body):
        raw = None
        for attempt in range(self.retries):
            try:
                raw = self._post(body)
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")
                if e.code == 400 and "temperature" in msg and "temperature" in body:
                    self._send_temperature = False       # some models fix their own temperature
                    body = {k: v for k, v in body.items() if k != "temperature"}
                    continue
                if e.code in (408, 409, 429, 500, 502, 503, 504, 529):
                    time.sleep(min(60, 2 ** attempt))
                    continue
                raise RuntimeError(f"API error {e.code}: {msg[:300]}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(min(60, 2 ** attempt))
        if raw is None:
            raise RuntimeError("API unreachable or overloaded after retries")
        return raw

    def chat(self, messages, schema=None) -> dict:
        body = self._body(messages)
        key = hashlib.sha256(json.dumps({"provider": "anthropic", **body}, sort_keys=True).encode()).hexdigest()
        path = os.path.join(self.cache_dir, "api_" + key + ".json") if self.cache_dir else None
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    hit = json.load(f)
                if not (hit.get("truncated") and not hit.get("retried")):
                    return hit
            except ValueError:
                os.remove(path)
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        raw = self._send(body)
        if raw.get("stop_reason") == "max_tokens" and body["max_tokens"] < self.fallback_tokens:
            body = dict(body, max_tokens=self.fallback_tokens)
            raw = self._send(body)
            retried = True
        else:
            retried = False
        text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
        usage = raw.get("usage", {})
        out = {"content": extract_json(text), "raw_text": text, "prompt_eval_count": usage.get("input_tokens", 0),
               "eval_count": usage.get("output_tokens", 0), "done_reason": raw.get("stop_reason", ""),
               "truncated": raw.get("stop_reason") == "max_tokens", "retried": retried}
        with self._lock:
            self.calls += 1
        if path:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(out, f)
            os.replace(tmp, path)
        return out
