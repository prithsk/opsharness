"""Serve a test world as an MCP server over stdio.

  python -m opsharness.mcp_serve --env hotel --seed 3

Any MCP client (this harness, Claude Desktop, Claude Code) can then drive the
fake hotel. Approvals are the client's job, so the world's own gate is off
and write tools are marked readOnlyHint=false. The extra method
"opsharness/score" returns the world's score for end-to-end tests.
"""
import argparse
import json
import sys

from opsharness.registry import world

READ_PREFIXES = ("list_", "get_", "search_", "check_")


def serve(env, stdin=sys.stdin, stdout=sys.stdout):
    tools = {n: t for n, t in env._tools.items() if n != "request_approval"}
    for t in tools.values():
        t.gated = None
        t.params.pop("approval_id", None)

    def reply(rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        msg.update({"error": error} if error else {"result": result})
        stdout.write(json.dumps(msg) + "\n")
        stdout.flush()

    for line in stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None:
            continue  # notification
        if method == "initialize":
            reply(rid, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": f"opsharness-{env.name}", "version": "0.2"},
                        "instructions": env.instructions()})
        elif method == "ping":
            reply(rid, {})
        elif method == "tools/list":
            reply(rid, {"tools": [{"name": n, "description": t.description, "inputSchema": t.schema(),
                                   "annotations": {"readOnlyHint": n.startswith(READ_PREFIXES)}}
                                  for n, t in tools.items()]})
        elif method == "tools/call":
            out = env.call(params.get("name", ""), params.get("arguments") or {})
            reply(rid, {"content": [{"type": "text", "text": out}], "isError": out.startswith('{"error"')})
        elif method == "opsharness/task":
            reply(rid, {"task": env.task()})
        elif method == "opsharness/score":
            reply(rid, env.score())
        else:
            reply(rid, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    serve(world(a.env)(a.seed))


if __name__ == "__main__":
    main()
