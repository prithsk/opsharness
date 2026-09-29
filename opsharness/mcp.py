"""MCP client (stdio transport), standard library only.

MCPToolset starts one or more MCP servers, lists their tools, and exposes
them through the same Toolset rules the test worlds use: validation,
coercion, approvals and metrics.

Approval default is safe: any tool not marked readOnlyHint=true by its
server needs approval. A project config can override that with patterns.
"""
import fnmatch
import json
import os
import queue
import re
import subprocess
import threading

from opsharness.core import Tool, ToolError, Toolset

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    pass


class MCPServer:
    def __init__(self, name, command, args=(), env=None, timeout=60, cwd=None):
        self.name, self.timeout = name, timeout
        self.proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=cwd,
                                     env={**os.environ, **(env or {})})
        self._lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._id = 0
        self._lock = threading.Lock()
        self.info = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                "clientInfo": {"name": "opsharness", "version": "0.2"}})
        self.notify("notifications/initialized")

    def _read(self):
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method, params=None):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                try:
                    line = self._lines.get(timeout=self.timeout)
                except queue.Empty:
                    raise MCPError(f"{self.name}: no reply to {method} within {self.timeout}s") from None
                if line is None:
                    raise MCPError(f"{self.name}: server exited")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # servers sometimes print logs to stdout
                if msg.get("id") == rid and "method" not in msg:
                    if "error" in msg:
                        raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                    return msg.get("result", {})
                if "method" in msg and "id" in msg:  # server-to-client request we do not support
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601, "message": "not supported by this client"}})

    def list_tools(self):
        tools, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments):
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for c in res.get("content", []):
            parts.append(c.get("text", "") if c.get("type") == "text" else f"[{c.get('type')} content]")
        text = "\n".join(parts)
        if res.get("isError"):
            raise ToolError(text or "tool reported an error")
        if res.get("structuredContent") is not None:
            return res["structuredContent"]
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"result": text}

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # a well-behaved server exits on EOF
                self.proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except (OSError, ValueError):
                pass


def _safe_name(s):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)[:64]


def needs_approval(rules, server, tool, annotations):
    """rules: {"always": [patterns], "never": [patterns]}; patterns match "server/tool"."""
    key = f"{server}/{tool}"
    if any(fnmatch.fnmatch(key, p) for p in rules.get("always", [])):
        return True
    if any(fnmatch.fnmatch(key, p) for p in rules.get("never", [])):
        return False
    return not (annotations or {}).get("readOnlyHint", False)


class MCPToolset(Toolset):
    """Tools from MCP servers, run through the harness rules.

    config = {"servers": {name: {"command": ..., "args": [...], "env": {...}}},
              "approve": {"always": [...], "never": [...]}}
    """
    name = "mcp"

    def __init__(self, config, approver=None, instructions_text=""):
        self.servers = {}
        tools, seen = [], set()
        try:
            for sname, spec in config.get("servers", {}).items():
                srv = MCPServer(sname, **spec)
                self.servers[sname] = srv
                for t in srv.list_tools():
                    name = _safe_name(t["name"])
                    if name in seen:
                        name = _safe_name(f"{sname}__{t['name']}")
                    seen.add(name)
                    schema = t.get("inputSchema") or {"type": "object", "properties": {}}
                    gated = needs_approval(config.get("approve", {}), sname, t["name"], t.get("annotations"))
                    tools.append(Tool(
                        name=name, description=t.get("description", ""),
                        params=dict(schema.get("properties") or {}),
                        required=list(schema.get("required") or []),
                        fn=lambda _srv=srv, _orig=t["name"], **kw: _srv.call_tool(_orig, kw),
                        gated=(lambda a: True) if gated else None,
                        raw_schema=schema,
                        strict=schema.get("additionalProperties") is False))
        except Exception:
            self.close()
            raise
        super().__init__(tools=tools, approver=approver, instructions_text=instructions_text)

    def close(self):
        for srv in self.servers.values():
            srv.close()
