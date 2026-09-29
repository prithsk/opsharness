"""Production runtime: MCP client behavior, approvers, and the agent command."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from opsharness import agent
from opsharness.approvals import CliApprover, DenyApprover, FileApprover
from opsharness.loop import run_episode
from opsharness.mcp import needs_approval
from opsharness.policies import ScriptedPolicy
from opsharness.testing import mcp_plan, served
from opsharness.toy import ToyEnv


class MCPBehavior(unittest.TestCase):
    def test_write_tools_are_gated_and_reads_are_not(self):
        ts = served("toy", 0)
        try:
            self.assertIsNone(ts._tools["list_tickets"].gated)
            self.assertIsNotNone(ts._tools["set_estimate"].gated)
            self.assertIn("approval_id", ts._tools["set_estimate"].schema()["properties"])
        finally:
            ts.close()

    def test_denied_actions_never_reach_the_system(self):
        ts = served("toy", 1, approver=DenyApprover())
        try:
            res, _ = run_episode(ts, ScriptedPolicy(mcp_plan(ts, ToyEnv(1).oracle_plan())), max_steps=200)
            self.assertEqual(ts.actions, [])
            self.assertGreater(res["metrics"]["denials"], 0)
            self.assertEqual(ts.servers["sim"].request("opsharness/score")["score"], 0.0)
        finally:
            ts.close()

    def test_tool_errors_come_back_readable(self):
        ts = served("toy", 0)
        try:
            out = json.loads(ts.call("list_tickets", {"bogus": 1}))
            self.assertIn("unknown argument", out["error"])
        finally:
            ts.close()


class ApprovalRules(unittest.TestCase):
    def test_default_gates_anything_not_read_only(self):
        self.assertTrue(needs_approval({}, "shop", "refund", {}))
        self.assertTrue(needs_approval({}, "shop", "refund", None))
        self.assertFalse(needs_approval({}, "shop", "get_order", {"readOnlyHint": True}))

    def test_patterns_override(self):
        rules = {"always": ["shop/get_customer*"], "never": ["calendar/*"]}
        self.assertTrue(needs_approval(rules, "shop", "get_customer_pii", {"readOnlyHint": True}))
        self.assertFalse(needs_approval(rules, "calendar", "create_event", {}))

    def test_cli_approver(self):
        said = []
        self.assertTrue(CliApprover(ask=lambda _: "y", say=said.append)("refund", {"id": 1}, "r")[0])
        self.assertFalse(CliApprover(ask=lambda _: "", say=said.append)("refund", {"id": 1}, "r")[0])

    def test_file_approver(self):
        with tempfile.TemporaryDirectory() as d:
            ap = FileApprover(d, timeout=10, poll=0.05)

            def approve_soon():
                for _ in range(100):
                    files = list(Path(d).glob("*.md"))
                    if files:
                        f = files[0]
                        f.write_text(f.read_text().replace("decision: pending", "decision: approved"))
                        return
                    time.sleep(0.05)

            threading.Thread(target=approve_soon).start()
            ok, note = ap("close_ticket", {"ticket_id": "T1"}, "done")
            self.assertTrue(ok, note)
            self.assertFalse(FileApprover(d, timeout=0.2, poll=0.05)("x", {}, "")[0])


class AgentCommand(unittest.TestCase):
    def test_sim_backend_oracle_run_writes_logs(self):
        with tempfile.TemporaryDirectory() as d:
            res = agent.main(["--project", "toy", "--policy", "oracle", "--backend", "sim", "--out", d])
            self.assertEqual(res["score"], 1.0)
            run = next(Path(d).glob("toy/*"))
            self.assertTrue((run / "trace.json").exists())
            self.assertIn("Score against the answer key: 1.0", (run / "summary.md").read_text())

    def test_mcp_backend_plumbing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "mcp.json"
            cfg.write_text(json.dumps({"servers": {"sim": {"command": "python3", "args": [
                "-m", "opsharness.mcp_serve", "--env", "toy"]}}}))
            (Path(d) / "AGENT.md").write_text("## Extra\nBe careful.\n")
            res = agent.main(["--project", "toy", "--config", str(cfg), "--policy", "noop",
                              "--approver", "deny", "--out", str(Path(d) / "runs")])
            self.assertEqual(res["stop"], "final")
            self.assertEqual(res["score"], 0.0)
            trace = json.loads(next(Path(d).glob("runs/toy/*/trace.json")).read_text())
            self.assertIn("Triage every ticket", trace["task"])
            self.assertIn("Be careful.", trace["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
