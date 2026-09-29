"""Security checks: where keys go, secrets in config, and the approval gate."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opsharness import agent
from opsharness.approvals import FileApprover
from opsharness.policies import OpenAIPolicy, pick_key


class KeyRouting(unittest.TestCase):
    def test_key_goes_to_the_configured_provider(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "https://openrouter.ai/api/v1"},
                             clear=True):
            self.assertEqual(OpenAIPolicy("m").key, "k")
            self.assertEqual(OpenAIPolicy("m", "https://openrouter.ai/api/v1").key, "k")

    def test_custom_url_never_gets_the_provider_key(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=True):
            self.assertIsNone(OpenAIPolicy("m", "http://localhost:11434/v1").key)
            self.assertIsNone(OpenAIPolicy("m", "https://evil.example/v1").key)

    def test_custom_key_is_opt_in(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_CUSTOM_API_KEY": "c"}, clear=True):
            self.assertEqual(OpenAIPolicy("m", "https://my-vllm.example/v1").key, "c")

    def test_no_key_over_plain_http_to_remote_hosts(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://api.example.com/v1"},
                             clear=True):
            with self.assertRaises(RuntimeError):
                pick_key("http://api.example.com/v1", custom=False)
        with mock.patch.dict(os.environ, {"OPENAI_CUSTOM_API_KEY": "c"}, clear=True):
            self.assertEqual(pick_key("http://localhost:8000/v1", custom=True), "c")


class ConfigSecrets(unittest.TestCase):
    def test_env_vars_expand_in_mcp_json(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"STORE_TOKEN": "t0k"}):
            p = Path(d) / "mcp.json"
            p.write_text('{"servers": {"s": {"command": "x", "args": ["--token", "${STORE_TOKEN}"], '
                         '"env": {"TOKEN": "${STORE_TOKEN}"}}}}')
            cfg = agent.load_config(p)
            self.assertEqual(cfg["servers"]["s"]["args"], ["--token", "t0k"])
            self.assertEqual(cfg["servers"]["s"]["env"], {"TOKEN": "t0k"})

    def test_missing_env_var_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            p = Path(d) / "mcp.json"
            p.write_text('{"servers": {"s": {"command": "x", "env": {"T": "${NOPE}"}}}}')
            with self.assertRaises(SystemExit):
                agent.load_config(p)


class ApprovalSafety(unittest.TestCase):
    def test_auto_approve_is_refused_on_real_servers(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                agent.main(["--project", "toy", "--policy", "noop", "--approver", "auto"])
            self.assertIn("refusing", str(cm.exception))

    def test_approval_files_stay_inside_their_folder(self):
        with tempfile.TemporaryDirectory() as d:
            FileApprover(Path(d) / "approvals", timeout=0.1, poll=0.05)("../../escape", {}, "")
            self.assertEqual([p.parent.name for p in Path(d).rglob("*.md")], ["approvals"])


if __name__ == "__main__":
    unittest.main()
