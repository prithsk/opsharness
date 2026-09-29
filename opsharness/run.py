"""Run policies over envs and seeds, then write traces and a markdown report.

Examples:
  python -m opsharness.run --env all --policy oracle,noop --seeds 0:20
  python -m opsharness.run --env compliance --policy anthropic:claude-sonnet-4-6,openai:gpt-4.1 --seeds 0:10
  python -m opsharness.run --env hotel --policy openai:qwen3:32b@http://localhost:11434/v1 --ablate Approvals
  python -m opsharness.run --env all --policy anthropic:claude-haiku-4-5-20251001 --seeds 0:10 --workers 8
"""
import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
import statistics
import time
from pathlib import Path

from opsharness.registry import worlds
from opsharness.loop import run_episode
from opsharness.policies import make_policy


def safe_dirname(s):
    """A folder name that works on Windows, macOS and Linux."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", s).strip("._") or "policy"


def parse_seeds(s):
    if ":" in s:
        a, b = s.split(":")
        return list(range(int(a), int(b)))
    return [int(x) for x in s.split(",")]


def report(rows, policies, env_names):
    lines = ["# Run report", "",
             "Mean score per env and policy (n seeds). Spread is max minus min across model policies "
             "(oracle and noop excluded). A reliable harness keeps spread small.", ""]
    header = "| env | " + " | ".join(policies) + " | spread |"
    lines += [header, "|" + "---|" * (len(policies) + 2)]
    for e in env_names:
        cells, model_means = [], []
        for p in policies:
            xs = [r["score"] for r in rows if r["env"] == e and r["policy"] == p]
            if not xs:
                cells.append("-")
                continue
            m = statistics.mean(xs)
            sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
            cells.append(f"{m:.3f} ± {sd:.3f} (n={len(xs)})")
            if p not in ("oracle", "noop"):
                model_means.append(m)
        spread = f"{max(model_means) - min(model_means):.3f}" if len(model_means) > 1 else "-"
        lines.append(f"| {e} | " + " | ".join(cells) + f" | {spread} |")
    lines += ["", "## Harness health", "",
              "Totals across all runs per policy. High invalid_args or gate_blocks point at the harness "
              "or the prompt, not only the model.", "",
              "| policy | runs | stuck | policy_errors | invalid_args | unknown_tool | tool_errors | gate_blocks | coercions |",
              "|---|---|---|---|---|---|---|---|---|"]
    for p in policies:
        rs = [r for r in rows if r["policy"] == p]
        tot = lambda k: sum(r["metrics"][k] for r in rs)
        lines.append(f"| {p} | {len(rs)} | {sum(r['stop'] == 'stuck' for r in rs)} | "
                     f"{sum(r['stop'].startswith('policy_error') for r in rs)} | {tot('invalid_args')} | "
                     f"{tot('unknown_tool')} | {tot('tool_errors')} | {tot('gate_blocks')} | {tot('coercions')} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="all")
    ap.add_argument("--policy", default="oracle")
    ap.add_argument("--seeds", default="0:10")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--ablate", default="", help="comma-separated AGENT.md section titles to drop")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--workers", type=int, default=1, help="parallel episodes (API calls are I/O bound)")
    a = ap.parse_args()

    ENVS = worlds()
    env_names = sorted(ENVS) if a.env == "all" else a.env.split(",")
    missing = [e for e in env_names if e not in ENVS]
    if missing:
        raise SystemExit(f"not installed: {missing}. Installed: {sorted(ENVS)}")
    policies = a.policy.split(",")
    ablate = tuple(x.strip() for x in a.ablate.split(",") if x.strip())
    out = Path(a.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(p, e, seed) for p in policies for e in env_names for seed in parse_seeds(a.seeds)]

    def one(job):
        p, e, seed = job
        env = ENVS[e](seed)
        try:
            res, msgs = run_episode(env, make_policy(p, env), max_steps=a.max_steps, ablate=ablate)
        except Exception as ex:  # e.g. a missing API key while building the policy
            res, msgs = {"env": e, "seed": seed, "stop": f"policy_error: {ex}", "steps": 0, "score": 0.0,
                         "details": {}, "metrics": dict(env.metrics)}, []
        res["policy"] = p
        d = out / safe_dirname(p) / e
        d.mkdir(parents=True, exist_ok=True)
        (d / f"seed_{seed}.json").write_text(json.dumps({"result": res, "messages": msgs}, indent=1))
        print(f"{p:32s} {e:12s} seed={seed:<3d} score={res['score']:.3f} stop={res['stop']}", flush=True)
        return res

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        rows = list(pool.map(one, jobs))
    (out / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rep = report(rows, policies, env_names)
    if ablate:
        rep = f"Ablated sections: {', '.join(ablate)}\n\n" + rep
    (out / "REPORT.md").write_text(rep)
    print("\n" + rep)
    print(f"traces in {out}")


if __name__ == "__main__":
    main()
