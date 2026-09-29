"""A fault-injecting policy for testing the harness without any API keys.

It replays the oracle plan but makes the mistakes real models make:
numbers as strings, arrays as JSON strings, misspelled tool names, broken
JSON, missing arguments, skipped approvals, and parallel calls. Coercible
slips replace the correct call. Hard slips come first and the correct call
follows, which mimics a model reading the error and retrying.

If the harness is sound, the episode still scores 1.0 and every injected
fault shows up in the metrics exactly once.
"""
import json
import random

COERCIBLE = ("int_as_string", "array_as_string")
HARD = ("unknown_tool", "bad_json", "missing_arg")


class ChaosPolicy:
    def __init__(self, env, seed=0, rate=0.5):
        self.rng = random.Random(f"chaos:{seed}")
        self.expected = {"coercions": 0, "unknown_tool": 0, "invalid_args": 0, "gate_blocks": 0}
        self.turns = self._build(env, env.oracle_plan(), rate)
        self.i = 0

    def _build(self, env, plan, rate):
        tools = {t.name: t for t in env.tools()}
        calls, i = [], 0
        while i < len(plan):
            name, args = plan[i]
            # skip-approval slip: try the gated call bare before asking
            if name == "request_approval" and self.rng.random() < rate:
                target, targs = plan[i + 1]
                bare = {k: v for k, v in targs.items() if k != "approval_id"}
                calls.append((target, bare))
                self.expected["gate_blocks"] += 1
            if name != "request_approval" and self.rng.random() < rate:
                kind = self.rng.choice(COERCIBLE + HARD)
                spec = tools[name].params
                ints = [k for k, v in args.items() if spec.get(k, {}).get("type") == "integer"]
                arrs = [k for k, v in args.items() if spec.get(k, {}).get("type") == "array"]
                if kind == "int_as_string" and ints:
                    k = self.rng.choice(ints)
                    calls.append((name, {**args, k: str(args[k])}))
                    self.expected["coercions"] += 1
                    i += 1
                    continue
                if kind == "array_as_string" and arrs:
                    k = self.rng.choice(arrs)
                    calls.append((name, {**args, k: json.dumps(args[k])}))
                    self.expected["coercions"] += 1
                    i += 1
                    continue
                if kind == "unknown_tool":
                    calls.append((name.replace("_", "", 1) + "s", args))
                    self.expected["unknown_tool"] += 1
                elif kind == "bad_json":
                    calls.append((name, {"__raw__": json.dumps(args)[:-1]}))
                    self.expected["invalid_args"] += 1
                elif kind == "missing_arg" and tools[name].required:
                    drop = self.rng.choice(tools[name].required)
                    calls.append((name, {k: v for k, v in args.items() if k != drop}))
                    self.expected["invalid_args"] += 1
            calls.append((name, args))
            i += 1
        # group some consecutive calls into one turn, like parallel tool use
        turns, j = [], 0
        while j < len(calls):
            n = 2 if self.rng.random() < 0.3 else 1
            turns.append(calls[j:j + n])
            j += n
        return turns

    def act(self, msgs, specs):
        if self.i >= len(self.turns):
            return {"text": "Done.", "calls": []}
        turn = self.turns[self.i]
        self.i += 1
        return {"text": "", "calls": [{"id": f"x{self.i}_{k}", "name": n, "args": a} for k, (n, a) in enumerate(turn)]}
