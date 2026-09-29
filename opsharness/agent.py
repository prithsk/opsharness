"""Run a world's project as a real agent.

Run it from an agent repo, next to its mcp.json and AGENT.md:

  python -m opsharness.agent --project hotel --policy anthropic:claude-haiku-4-5-20251001
  python -m opsharness.agent --project hotel --policy openai:<model-id> --task "Prep tonight's arrivals"
  python -m opsharness.agent --project hotel --policy anthropic:... --backend sim   # in-process test world

The default backend reads mcp.json. Out of the box that file serves the
project's test world over MCP, so everything runs with no accounts. Point it
at real MCP servers and the same agent works on real systems. Every run writes
a trace and a markdown summary under runs/live/.
"""
import argparse
import inspect
import json
import os
import re
import sys
import time
from pathlib import Path

from opsharness.approvals import make_approver
from opsharness.loop import run_episode
from opsharness.mcp import MCPError, MCPToolset
from opsharness.policies import make_policy
from opsharness.registry import world

def expand(value):
    """Replace ${NAME} with the environment variable NAME, so tokens stay out of mcp.json."""
    def sub(m):
        if m.group(1) not in os.environ:
            raise SystemExit(f"mcp.json needs the environment variable {m.group(1)}")
        return os.environ[m.group(1)]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, value) if isinstance(value, str) else value


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    for spec in cfg.get("servers", {}).values():
        spec["args"] = [expand(x) for x in spec.get("args", [])]
        spec["env"] = {k: expand(v) for k, v in (spec.get("env") or {}).items()}
        if spec.get("command") in ("python", "python3"):
            spec["command"] = sys.executable
        spec["cwd"] = str(path.parent / spec["cwd"]) if spec.get("cwd") else str(path.parent)
    return cfg


def build(project, backend, approver, seed, config):
    cls = world(project)
    if backend == "sim":
        return cls(seed, approver=approver)
    cfg = load_config(config)
    if not cfg.get("servers"):
        raise SystemExit(f"{config} lists no servers")
    # business rules live once, in the world's markdown; AGENT.md adds real-world guidance
    rules = Path(inspect.getfile(cls)).with_suffix(".md").read_text()
    extra = Path(config).resolve().parent / "AGENT.md"
    text = rules + ("\n\n" + extra.read_text() if extra.exists() else "")
    ts = MCPToolset(cfg, approver=approver, instructions_text=text)
    ts.name = project
    return ts


def remote(ts, method):
    """Ask any server that speaks the opsharness extension (the test worlds do)."""
    for srv in getattr(ts, "servers", {}).values():
        try:
            return srv.request(method)
        except MCPError:
            continue
    return None


def write_log(out, project, res, msgs, ts, task):
    d = out / project / time.strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    (d / "trace.json").write_text(json.dumps({"result": res, "task": task, "messages": msgs,
                                             "approvals": ts.approvals, "actions": ts.actions}, indent=1))
    lines = [f"# {project} run", "", f"Task: {task}", "",
             f"Stop: {res['stop']} after {res['steps']} steps.", ""]
    if "score" in res:
        lines += [f"Score against the answer key: {res['score']}", ""]
    acts = [f"- {a['tool']} {json.dumps(a['args'])}" for a in ts.actions] or ["- none"]
    apps = [f"- {'approved' if a['approved'] else 'DENIED'}: {a['tool']} {json.dumps(a['args'])} ({a['note']})"
            for a in ts.approvals] or ["- none"]
    lines += ["## Actions", ""] + acts + ["", "## Approvals", ""] + apps
    lines += ["", "## Harness health", "", "```", json.dumps(res["metrics"], indent=1), "```", ""]
    final = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant" and m["content"]), "")
    lines += ["## Final message", "", final or "(none)", ""]
    (d / "summary.md").write_text("\n".join(lines))
    return d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="an installed world, e.g. hotel")
    ap.add_argument("--config", default="mcp.json", help="MCP config; AGENT.md is read from the same folder")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--backend", default="mcp", choices=["mcp", "sim"])
    ap.add_argument("--approver", default=None, choices=["cli", "file", "auto", "deny"],
                    help="default: cli for mcp, auto for sim")
    ap.add_argument("--task", default=None)
    ap.add_argument("--seed", type=int, default=0, help="sim backend only")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--out", default="runs/live")
    a = ap.parse_args(argv)

    if a.backend == "mcp" and a.approver == "auto" and os.environ.get("OPSHARNESS_ALLOW_AUTO_APPROVE") != "1":
        raise SystemExit("refusing --approver auto with real servers. Use cli or file, or set "
                         "OPSHARNESS_ALLOW_AUTO_APPROVE=1 if every server in mcp.json is a test world.")
    approver = make_approver(a.approver or ("auto" if a.backend == "sim" else "cli"))
    ts = build(a.project, a.backend, approver, a.seed, a.config)
    try:
        task = a.task or ts.task() or (remote(ts, "opsharness/task") or {}).get("task")
        if not task:
            task = input("Task for the agent: ").strip()
        ts.task_text = task
        if a.backend == "mcp":
            ts.task = lambda: task
        res, msgs = run_episode(ts, make_policy(a.policy, ts), max_steps=a.max_steps)
        if "score" not in res:
            sc = remote(ts, "opsharness/score")
            if sc:
                res.update(sc)
        d = write_log(Path(a.out), a.project, res, msgs, ts, task)
        print((d / "summary.md").read_text())
        print(f"log: {d}")
        return res
    finally:
        ts.close()


if __name__ == "__main__":
    main()
