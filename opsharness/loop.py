"""The agent loop. Messages stay in one neutral format; adapters translate."""
import json


def tool_specs(env):
    return [{"name": t.name, "description": t.description, "schema": t.schema()} for t in env.tools()]


def run_episode(env, policy, max_steps=60, ablate=(), stuck_after=3):
    specs = tool_specs(env)
    msgs = [{"role": "system", "content": env.instructions(ablate)},
            {"role": "user", "content": env.task()}]
    stop, steps = "max_steps", 0
    repeat_sig, repeat_n = None, 0
    for steps in range(1, max_steps + 1):
        try:
            out = policy.act(msgs, specs)
        except Exception as e:  # network, auth, bad payloads
            stop = f"policy_error: {type(e).__name__}: {e}"
            break
        calls = out.get("calls") or []
        msgs.append({"role": "assistant", "content": out.get("text") or "", "calls": calls})
        if not calls:
            stop = "final"
            break
        stuck = False
        for c in calls:
            res = env.call(c["name"], c.get("args") or {})
            msgs.append({"role": "tool", "call_id": c["id"], "name": c["name"], "content": res})
            if res.startswith('{"error"'):
                sig = (c["name"], json.dumps(c.get("args"), sort_keys=True, default=str))
                repeat_n = repeat_n + 1 if sig == repeat_sig else 1
                repeat_sig = sig
                if repeat_n >= stuck_after:
                    stuck = True
            else:
                repeat_sig, repeat_n = None, 0
        if stuck:
            stop = "stuck"
            break
    result = {"env": env.name, "seed": getattr(env, "seed", None), "stop": stop, "steps": steps}
    if hasattr(env, "score"):
        result.update(env.score())
    result["metrics"] = dict(env.metrics)
    return result, msgs
