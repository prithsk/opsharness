"""Contract tests every world must pass. Agent repos reuse these.

  class TestHotel(WorldContract, unittest.TestCase):
      world = HotelEnv

A world passes when the correct plan scores 1.0, doing nothing scores 0.0,
seeds are deterministic, skipping approval gets blocked, the harness survives
injected model slips, every write tool is used by some correct plan, and the
world still scores 1.0 when served over MCP. Domain-specific wrong-agent tests
live in the agent repo.
"""
import sys

from opsharness.chaos import ChaosPolicy
from opsharness.core import approval_id_for
from opsharness.loop import run_episode
from opsharness.mcp import MCPToolset
from opsharness.policies import NoopPolicy, ScriptedPolicy

READ_PREFIXES = ("list_", "get_", "search_", "check_")


def play(env, plan, max_steps=300):
    res, _ = run_episode(env, ScriptedPolicy(plan), max_steps=max_steps)
    return res


def strip_approvals(plan):
    return [(t, {k: v for k, v in a.items() if k != "approval_id"}) for t, a in plan if t != "request_approval"]


def served(name, seed, approver=None):
    ts = MCPToolset({"servers": {"sim": {"command": sys.executable,
                                         "args": ["-m", "opsharness.mcp_serve", "--env", name, "--seed", str(seed)]}}},
                    approver=approver)
    ts.task_text = ts.servers["sim"].request("opsharness/task")["task"]
    return ts


def mcp_plan(ts, oracle):
    plan = []
    for tool, args in strip_approvals(oracle):
        if ts._tools[tool].gated:
            plan.append(("request_approval", {"tool": tool, "args": args, "reason": "test"}))
            args = {**args, "approval_id": approval_id_for(tool, args)}
        plan.append((tool, args))
    return plan


class WorldContract:
    world = None
    seeds = range(100)
    mcp_seeds = range(3)

    def test_oracle_is_perfect(self):
        for s in self.seeds:
            env = self.world(s)
            res = play(env, env.oracle_plan())
            self.assertEqual(res["score"], 1.0, f"seed {s}: {res}")
            self.assertEqual(res["metrics"]["gate_blocks"], 0)

    def test_noop_scores_zero(self):
        for s in self.seeds:
            res, _ = run_episode(self.world(s), NoopPolicy())
            self.assertEqual(res["score"], 0.0, f"seed {s}")

    def test_seeds_are_deterministic(self):
        self.assertEqual(self.world(7).oracle_plan(), self.world(7).oracle_plan())
        self.assertNotEqual(str(self.world(7).oracle_plan()), str(self.world(8).oracle_plan()))

    def test_skipping_approval_is_blocked(self):
        if not any(t.gated for t in self.world(0).tools()):
            self.skipTest("world has no gated tools")
        gated_seeds = 0
        for s in self.seeds:
            plan = self.world(s).oracle_plan()
            if not any(t == "request_approval" for t, _ in plan):
                continue
            gated_seeds += 1
            res = play(self.world(s), strip_approvals(plan))
            self.assertGreater(res["metrics"]["gate_blocks"], 0)
            self.assertLess(res["score"], 1.0, f"seed {s}")
        self.assertGreater(gated_seeds, 0, "no seed ever exercises the approval gate")

    def test_harness_survives_injected_faults(self):
        for s in self.seeds:
            env = self.world(s)
            pol = ChaosPolicy(env, seed=s)
            res, _ = run_episode(env, pol, max_steps=400)
            self.assertEqual(res["score"], 1.0, f"seed {s}: {res}")
            self.assertEqual({k: res["metrics"][k] for k in pol.expected}, pol.expected, f"seed {s}")

    def test_every_write_tool_is_used(self):
        used = set()
        for s in self.seeds:
            used |= {t for t, _ in self.world(s).oracle_plan()}
        writers = {t.name for t in self.world(0).tools() if not t.name.startswith(READ_PREFIXES)}
        self.assertEqual(writers - used, set(), "tools no correct plan ever calls")

    def test_scores_perfect_through_mcp(self):
        for s in self.mcp_seeds:
            ts = served(self.world.name, s)
            try:
                play(ts, mcp_plan(ts, self.world(s).oracle_plan()))
                score = ts.servers["sim"].request("opsharness/score")
                self.assertEqual(score["score"], 1.0, f"seed {s}: {score}")
            finally:
                ts.close()
