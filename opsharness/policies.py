"""Policies decide the next step. Scripted ones exist to test the scorers.

Model adapters use urllib only, so there are no SDK versions to chase.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


# ---- scripted policies -----------------------------------------------------
class ScriptedPolicy:
    """Replays a fixed list of (tool, args) calls, one per step."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.i = 0

    def act(self, msgs, specs):
        if self.i >= len(self.plan):
            return {"text": "Done.", "calls": []}
        name, args = self.plan[self.i]
        self.i += 1
        return {"text": "", "calls": [{"id": f"c{self.i}", "name": name, "args": args}]}


class NoopPolicy:
    def act(self, msgs, specs):
        return {"text": "Nothing to do.", "calls": []}


# ---- argument repair -------------------------------------------------------
def parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    for candidate in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            out = json.loads(candidate)
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            pass
    return {"__raw__": raw}


# ---- HTTP ------------------------------------------------------------------
def post_json(url, headers, body, tries=5, timeout=120):
    data = json.dumps(body).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers={**headers, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from None
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


# ---- Anthropic -------------------------------------------------------------
def to_anthropic(msgs):
    system = msgs[0]["content"]
    out = []
    for m in msgs[1:]:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = []
            if m["content"]:
                blocks.append({"type": "text", "text": m["content"]})
            for c in m["calls"]:
                args = c["args"] if "__raw__" not in c["args"] else {}
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": args})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(empty)"}]})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["call_id"], "content": m["content"]}
            prev = out[-1] if out else None
            if prev and prev["role"] == "user" and isinstance(prev["content"], list) \
                    and prev["content"] and prev["content"][0].get("type") == "tool_result":
                prev["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return system, out


def from_anthropic(resp):
    text, calls = [], []
    for b in resp.get("content", []):
        if b.get("type") == "text":
            text.append(b["text"])
        elif b.get("type") == "tool_use":
            calls.append({"id": b["id"], "name": b["name"], "args": parse_args(b.get("input"))})
    return {"text": "\n".join(text), "calls": calls}


class AnthropicPolicy:
    def __init__(self, model, max_tokens=2048, temperature=0.0):
        self.model, self.max_tokens, self.temperature = model, max_tokens, temperature
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("set ANTHROPIC_API_KEY")

    def act(self, msgs, specs):
        system, messages = to_anthropic(msgs)
        body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                "system": system, "messages": messages,
                "tools": [{"name": s["name"], "description": s["description"], "input_schema": s["schema"]}
                          for s in specs]}
        resp = post_json("https://api.anthropic.com/v1/messages",
                         {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body)
        return from_anthropic(resp)


# ---- OpenAI-compatible (OpenAI, OpenRouter, vLLM, Ollama, Together...) ------
def to_openai(msgs):
    out = []
    for m in msgs:
        if m["role"] in ("system", "user"):
            out.append({"role": m["role"], "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m["content"] or None}
            if m["calls"]:
                msg["tool_calls"] = [{
                    "id": c["id"], "type": "function",
                    "function": {"name": c["name"],
                                 "arguments": c["args"].get("__raw__") if "__raw__" in c["args"]
                                 else json.dumps(c["args"])}} for c in m["calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["call_id"], "content": m["content"]})
    return out


def from_openai(resp):
    msg = resp["choices"][0]["message"]
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function", {})
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""),
                      "args": parse_args(fn.get("arguments"))})
    return {"text": msg.get("content") or "", "calls": calls}


def _is_local(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith((".localhost", ".test"))


def pick_key(base, custom):
    """Decide which key, if any, goes to this base URL.

    OPENAI_API_KEY only ever goes to OPENAI_BASE_URL (or OpenAI itself when that
    is unset). A URL named in the policy string gets OPENAI_CUSTOM_API_KEY or
    nothing, so a typo or a local server never receives your provider key.
    No key ever travels over plain http to a non-local host.
    """
    configured = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if custom and base != configured:
        key = os.environ.get("OPENAI_CUSTOM_API_KEY")
    else:
        key = os.environ.get("OPENAI_API_KEY")
    if key and urllib.parse.urlparse(base).scheme != "https" and not _is_local(base):
        raise RuntimeError(f"refusing to send an API key over plain http to {base}")
    return key


class OpenAIPolicy:
    def __init__(self, model, base_url=None, temperature=0.0):
        self.model, self.temperature = model, temperature
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.key = pick_key(self.base, custom=base_url is not None)

    def act(self, msgs, specs):
        body = {"model": self.model, "temperature": self.temperature, "messages": to_openai(msgs),
                "tools": [{"type": "function", "function": {
                    "name": s["name"], "description": s["description"], "parameters": s["schema"]}}
                    for s in specs]}
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        resp = post_json(self.base + "/chat/completions", headers, body)
        return from_openai(resp)


def make_policy(spec, env):
    """spec: oracle | noop | chaos | anthropic:<model> | openai:<model>[@<base_url>]"""
    if spec == "oracle":
        return ScriptedPolicy(env.oracle_plan())
    if spec == "noop":
        return NoopPolicy()
    if spec == "chaos":
        from opsharness.chaos import ChaosPolicy
        return ChaosPolicy(env, seed=env.seed)
    kind, _, rest = spec.partition(":")
    if kind == "anthropic":
        return AnthropicPolicy(rest)
    if kind == "openai":
        model, _, base = rest.partition("@")
        return OpenAIPolicy(model, base or None)
    raise ValueError(f"unknown policy '{spec}'")
