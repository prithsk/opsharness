"""A tiny ticket-triage world that ships with the core.

It exists so the core repo can test itself without any agent repo installed.
It uses every feature the harness has: integers, arrays, enums, required
arguments, and an approval gate.
"""
import math

from opsharness.core import Env, Tool, ToolError

COMPONENTS = ["auth", "billing", "search", "mobile", "api"]
TITLES = ["Login loops after reset", "Invoice shows wrong tax", "Search ignores filters",
          "App crashes on rotate", "Rate limit too strict", "Export times out", "Avatar upload fails"]


class ToyEnv(Env):
    name = "toy"

    def build(self):
        r = self.rng
        self.tickets = {}
        for i, title in enumerate(r.sample(TITLES, 5), 1):
            self.tickets[f"T{i}"] = {"id": f"T{i}", "title": title, "component": r.choice(COMPONENTS),
                                     "priority": r.choice(["low", "normal", "high"]),
                                     "status": r.choice(["done", "wontfix_requested", "open"]),
                                     "reported_hours": round(r.uniform(0.5, 9.5), 1)}
        if not any(t["priority"] == "high" and t["status"] != "open" for t in self.tickets.values()):
            t = next(t for t in self.tickets.values())
            t["priority"], t["status"] = "high", "done"
        self.tags, self.estimates, self.closed = {}, {}, {}

    def _t(self, tid):
        if tid not in self.tickets:
            raise ToolError(f"no ticket '{tid}'")
        return self.tickets[tid]

    def list_tickets(self):
        return {"tickets": list(self.tickets.values())}

    def tag_ticket(self, ticket_id, tags):
        self._t(ticket_id)
        self.tags[ticket_id] = sorted(set(tags))
        return {"ticket_id": ticket_id, "tags": self.tags[ticket_id]}

    def set_estimate(self, ticket_id, hours):
        self._t(ticket_id)
        if hours <= 0:
            raise ToolError("hours must be positive")
        self.estimates[ticket_id] = hours
        return {"ticket_id": ticket_id, "hours": hours}

    def close_ticket(self, ticket_id, resolution):
        t = self._t(ticket_id)
        if t["status"] == "open":
            raise ToolError(f"{ticket_id} is still open")
        self.closed[ticket_id] = resolution
        return {"ticket_id": ticket_id, "resolution": resolution}

    def env_tools(self):
        s = {"type": "string"}
        return [
            Tool("list_tickets", "All tickets in the queue.", {}, self.list_tickets),
            Tool("tag_ticket", "Set a ticket's tags.", {"ticket_id": s, "tags": {"type": "array", "items": s}},
                 self.tag_ticket, ["ticket_id", "tags"]),
            Tool("set_estimate", "Set a ticket's estimate in whole hours.",
                 {"ticket_id": s, "hours": {"type": "integer"}}, self.set_estimate, ["ticket_id", "hours"]),
            Tool("close_ticket", "Close a ticket that is not open.",
                 {"ticket_id": s, "resolution": {"type": "string", "enum": ["fixed", "wontfix"]}},
                 self.close_ticket, ["ticket_id", "resolution"],
                 gated=lambda a: a.get("ticket_id") in self.tickets
                 and self.tickets[a["ticket_id"]]["priority"] == "high"),
        ]

    def task(self):
        return "Triage every ticket in the queue."

    def oracle_plan(self):
        plan = []
        for tid, t in self.tickets.items():
            plan.append(("tag_ticket", {"ticket_id": tid, "tags": [t["component"]]}))
            plan.append(("set_estimate", {"ticket_id": tid, "hours": math.ceil(t["reported_hours"])}))
            if t["status"] != "open":
                res = "fixed" if t["status"] == "done" else "wontfix"
                plan += self.gated_call("close_ticket", {"ticket_id": tid, "resolution": res})
        return plan

    def score(self):
        ok = 0
        for tid, t in self.tickets.items():
            want_close = None if t["status"] == "open" else ("fixed" if t["status"] == "done" else "wontfix")
            ok += (self.tags.get(tid) == [t["component"]] and
                   self.estimates.get(tid) == math.ceil(t["reported_hours"]) and
                   self.closed.get(tid) == want_close)
        return {"score": round(ok / len(self.tickets), 4), "details": {"tickets_correct": ok}}
