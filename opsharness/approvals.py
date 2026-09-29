"""Who says yes to a gated action.

Each approver is a callable: approver(tool, args, reason) -> (approved, note).
Tests use AutoApprover. Real runs use CliApprover or FileApprover.
"""
import json
import re
import time
from pathlib import Path


class AutoApprover:
    """Approves everything. For test worlds, where a simulated manager signs off."""

    def __call__(self, tool, args, reason):
        return True, "auto-approved"


class DenyApprover:
    """Denies everything. Useful for dry runs against real systems."""

    def __call__(self, tool, args, reason):
        return False, "dry run, all actions denied"


class CliApprover:
    """Asks the person at the terminal."""

    def __init__(self, ask=input, say=print):
        self.ask, self.say = ask, say

    def __call__(self, tool, args, reason):
        self.say(f"\n[approval needed] {tool}\n  args: {json.dumps(args, indent=2)}\n  reason: {reason or '-'}")
        answer = self.ask("Approve? [y/N] ").strip().lower()
        return (answer in ("y", "yes"), "approved at terminal" if answer in ("y", "yes") else "denied at terminal")


class FileApprover:
    """Writes each request to a markdown file and waits for a person to edit it.

    Change the line `decision: pending` to `decision: approved` or
    `decision: denied`. Anything else after the timeout counts as denied.
    This works from a phone, a shared drive or a git repo with no server.
    """

    def __init__(self, folder="approvals", timeout=900, poll=2.0):
        self.folder, self.timeout, self.poll = Path(folder), timeout, poll
        self.folder.mkdir(parents=True, exist_ok=True)

    def __call__(self, tool, args, reason):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tool_part = re.sub(r"[^A-Za-z0-9_-]", "_", str(tool))[:64]
        path = self.folder / f"{stamp}-{tool_part}.md"
        n = 1
        while path.exists():
            n += 1
            path = self.folder / f"{stamp}-{tool_part}-{n}.md"
        path.write_text(f"# Approval request\n\ntool: {tool}\n\nreason: {reason or '-'}\n\n"
                        f"```json\n{json.dumps(args, indent=2)}\n```\n\ndecision: pending\n")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            for line in path.read_text().splitlines():
                if line.strip().lower().startswith("decision:"):
                    value = line.split(":", 1)[1].strip().lower()
                    if value == "approved":
                        return True, f"approved in {path.name}"
                    if value == "denied":
                        return False, f"denied in {path.name}"
            time.sleep(self.poll)
        return False, f"no decision in {path.name} within {self.timeout}s"


def make_approver(kind, folder="approvals"):
    return {"auto": AutoApprover, "deny": DenyApprover, "cli": CliApprover,
            "file": lambda: FileApprover(folder)}[kind]()
