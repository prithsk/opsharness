"""Tests for the harness layer: the parts that make runs comparable across models."""
import json
import unittest

from opsharness.core import approval_id_for
from opsharness.loop import run_episode
from opsharness.policies import (ScriptedPolicy, from_anthropic, from_openai, parse_args,
                                 to_anthropic, to_openai)
from opsharness.toy import ToyEnv


def err(out):
    return json.loads(out).get("error")


class Validation(unittest.TestCase):
    def setUp(self):
        self.env = ToyEnv(0)

    def test_unknown_tool_lists_real_tools(self):
        e = err(self.env.call("close_tickets", {}))
        self.assertIn("unknown tool", e)
        self.assertIn("close_ticket", e)
        self.assertEqual(self.env.metrics["unknown_tool"], 1)

    def test_missing_and_unknown_args(self):
        self.assertIn("missing required", err(self.env.call("set_estimate", {"ticket_id": "T1"})))
        self.assertIn("unknown argument", err(self.env.call("set_estimate", {"ticket_id": "T1", "hours": 2, "x": 1})))

    def test_enum_is_enforced(self):
        self.assertIn("must be one of", err(self.env.call("close_ticket", {"ticket_id": "T1", "resolution": "done"})))

    def test_coerces_common_slips(self):
        self.assertIsNone(err(self.env.call("set_estimate", {"ticket_id": "T1", "hours": "3"})))
        self.assertIsNone(err(self.env.call("set_estimate", {"ticket_id": "T1", "hours": 3.0})))
        self.assertIsNone(err(self.env.call("tag_ticket", {"ticket_id": "T1", "tags": '["auth"]'})))
        self.assertEqual(self.env.metrics["coercions"], 3)
        self.assertEqual(self.env.tags["T1"], ["auth"])

    def test_bad_json_args_get_a_clear_error(self):
        self.assertIn("not valid JSON", err(self.env.call("list_tickets", {"__raw__": "{oops"})))

    def test_gate_requires_matching_approval(self):
        env = ToyEnv(0)
        plan = env.oracle_plan()
        i = next(i for i, (t, _) in enumerate(plan) if t == "request_approval")
        tool, args = plan[i + 1]
        bare = {k: v for k, v in args.items() if k != "approval_id"}
        self.assertIn("needs approval", err(env.call(tool, bare)))
        self.assertIn("needs approval", err(env.call(tool, {**bare, "approval_id": "apv_fake"})))
        env.call("request_approval", {"tool": tool, "args": bare})
        self.assertIsNone(err(env.call(tool, {**bare, "approval_id": approval_id_for(tool, bare)})))


class Loop(unittest.TestCase):
    def test_stuck_detection(self):
        res, _ = run_episode(ToyEnv(0), ScriptedPolicy([("set_estimate", {})] * 10))
        self.assertEqual(res["stop"], "stuck")

    def test_policy_exception_is_recorded(self):
        class Boom:
            def act(self, m, s):
                raise RuntimeError("401")
        res, _ = run_episode(ToyEnv(0), Boom())
        self.assertTrue(res["stop"].startswith("policy_error"))

    def test_ablation_drops_a_section(self):
        env = ToyEnv(0)
        self.assertIn("## Approvals", env.instructions())
        self.assertNotIn("## Approvals", env.instructions(ablate=("Approvals",)))
        self.assertIn("## Rules", env.instructions(ablate=("Approvals",)))


class RunFolders(unittest.TestCase):
    def test_policy_names_become_safe_folder_names(self):
        from opsharness.run import safe_dirname
        for bad in ["openai:<model-id>", "openai:qwen/qwen3:free@http://localhost:11434/v1", 'a*b?c"d|e']:
            name = safe_dirname(bad)
            self.assertRegex(name, r"^[A-Za-z0-9._-]+$", bad)


class Adapters(unittest.TestCase):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "calls": [
                {"id": "a", "name": "list_tickets", "args": {}}, {"id": "b", "name": "list_tickets", "args": {}}]},
            {"role": "tool", "call_id": "a", "name": "list_tickets", "content": "{}"},
            {"role": "tool", "call_id": "b", "name": "list_tickets", "content": "{}"}]

    def test_anthropic_groups_parallel_results(self):
        system, out = to_anthropic(self.msgs)
        self.assertEqual(system, "sys")
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        self.assertEqual(len(out[2]["content"]), 2)

    def test_anthropic_parse(self):
        r = from_anthropic({"content": [{"type": "text", "text": "hi"},
                                        {"type": "tool_use", "id": "t1", "name": "list_tickets", "input": {}}]})
        self.assertEqual(r["calls"][0]["name"], "list_tickets")

    def test_openai_round_trip(self):
        out = to_openai(self.msgs)
        self.assertEqual(out[2]["tool_calls"][1]["function"]["name"], "list_tickets")
        self.assertEqual(out[3], {"role": "tool", "tool_call_id": "a", "content": "{}"})
        r = from_openai({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "type": "function", "function": {"name": "set_estimate", "arguments": '{"hours": 2,}'}}]}}]})
        self.assertEqual(r["calls"][0]["args"], {"hours": 2})

    def test_parse_args_repairs(self):
        self.assertEqual(parse_args('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_args(""), {})
        self.assertIn("__raw__", parse_args("{a: 1"))


if __name__ == "__main__":
    unittest.main()
