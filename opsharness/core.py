"""Core harness pieces.

A Toolset holds tools and the rules around them: argument validation with
readable errors, lenient type coercion, an approval gate for risky actions,
and metrics. Production projects run on a Toolset (for example one backed by
MCP servers). An Env is a Toolset plus fake data, a task and a scorer, which
is how the same harness gets tested.
"""
import hashlib
import inspect
import json
import random
from dataclasses import dataclass, field
from pathlib import Path


class ToolError(Exception):
    """Raised by env tools for business-rule failures the model should see."""


APPROVAL_PARAM = {"type": "string",
                  "description": "Approval id from request_approval, needed when the action requires sign-off."}


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    fn: callable
    required: list = field(default_factory=list)
    gated: callable = None  # gated(args) -> True when this call needs approval
    raw_schema: dict = None  # full schema from an MCP server, sent to the model as-is
    strict: bool = True      # reject arguments that are not in params

    def __post_init__(self):
        if self.gated is not None and "approval_id" not in self.params:
            self.params = dict(self.params)
            self.params["approval_id"] = APPROVAL_PARAM

    def schema(self):
        if self.raw_schema is not None:
            out = dict(self.raw_schema)
            out["type"] = "object"
            out["properties"] = dict(out.get("properties") or {})
            if self.gated is not None:
                out["properties"]["approval_id"] = APPROVAL_PARAM
            return out
        return {"type": "object", "properties": self.params, "required": list(self.required)}


def canon(tool_name, args):
    body = {k: v for k, v in args.items() if k != "approval_id"}
    return tool_name + ":" + json.dumps(body, sort_keys=True)


def approval_id_for(tool_name, args):
    return "apv_" + hashlib.sha1(canon(tool_name, args).encode()).hexdigest()[:10]


def _coerce(value, spec, metrics):
    """Return (ok, value). Coerces the common near-misses models produce."""
    t = spec.get("type")
    if t is None:
        return True, value
    if t == "integer":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, int):
            return True, value
        if isinstance(value, float) and value.is_integer():
            metrics["coercions"] += 1
            return True, int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            metrics["coercions"] += 1
            return True, int(value.strip())
        return False, value
    if t == "number":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, (int, float)):
            return True, value
        try:
            out = float(value)
            metrics["coercions"] += 1
            return True, out
        except (TypeError, ValueError):
            return False, value
    if t == "string":
        if isinstance(value, str):
            return True, value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics["coercions"] += 1
            return True, str(value)
        return False, value
    if t == "boolean":
        if isinstance(value, bool):
            return True, value
        if value in ("true", "false"):
            metrics["coercions"] += 1
            return True, value == "true"
        return False, value
    if t == "array":
        if isinstance(value, str):
            # A JSON array serialized as a string is a common slip.
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    metrics["coercions"] += 1
                    value = parsed
            except json.JSONDecodeError:
                metrics["coercions"] += 1
                value = [value]
        if not isinstance(value, list):
            return False, value
        item_spec = spec.get("items")
        if item_spec:
            out = []
            for item in value:
                ok, item = _coerce(item, item_spec, metrics)
                if not ok:
                    return False, value
                out.append(item)
            value = out
        return True, value
    if t == "object":
        return isinstance(value, dict), value
    return True, value


def validate(tool, args, metrics):
    """Return (error_message_or_None, cleaned_args)."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object", args
    if "__raw__" in args:
        return ("arguments were not valid JSON. Send one JSON object that matches "
                f"the schema for {tool.name}."), args
    for name in tool.required:
        if name not in args or args[name] is None:
            return f"missing required argument '{name}'", args
    clean = {}
    for key, value in args.items():
        if key not in tool.params:
            if not tool.strict:
                clean[key] = value
                continue
            return f"unknown argument '{key}'. Valid arguments: {sorted(tool.params)}", args
        if value is None:
            continue
        spec = tool.params[key]
        ok, value = _coerce(value, spec, metrics)
        if not ok:
            return f"argument '{key}' should be of type {spec.get('type')}", args
        if "enum" in spec and value not in spec["enum"]:
            return f"argument '{key}' must be one of {spec['enum']}", args
        clean[key] = value
    return None, clean


class Toolset:
    """Tools plus the harness rules around them: validation, coercion, approvals, metrics.

    Production projects use a Toolset directly (for example MCPToolset).
    Test worlds subclass it as Env and add fake data and a scorer.
    """
    name = "toolset"

    def __init__(self, tools=None, approver=None, instructions_text=""):
        from opsharness.approvals import AutoApprover
        self.approver = approver or AutoApprover()
        self.instructions_text = instructions_text
        self.approved = set()
        self.metrics = {"calls": 0, "tool_errors": 0, "invalid_args": 0, "unknown_tool": 0,
                        "gate_blocks": 0, "coercions": 0, "denials": 0}
        self.actions = []  # successful state-changing calls, for the trace
        self.approvals = []  # every approval decision, for the run log
        self._tools = {t.name: t for t in (self.env_tools() if tools is None else tools)}
        if any(t.gated for t in self._tools.values()):
            self._tools["request_approval"] = Tool(
                name="request_approval",
                description=("Ask a human supervisor to approve an action that needs sign-off. "
                             "Pass the tool name and the exact arguments you intend to call it with "
                             "(without approval_id). Returns an approval_id to include in that call, "
                             "or a denial. Never run a denied action."),
                params={
                    "tool": {"type": "string", "description": "Name of the tool you want to call."},
                    "args": {"type": "object", "description": "The exact arguments for that call."},
                    "reason": {"type": "string", "description": "One sentence on why."},
                },
                required=["tool", "args"],
                fn=self._request_approval,
            )

    def env_tools(self):
        return []

    def task(self):
        return getattr(self, "task_text", "")

    def tools(self):
        return list(self._tools.values())

    def instructions(self, ablate=()):
        return self.instructions_text

    def gated_call(self, tool_name, args):
        """Oracle helper: the calls needed to run tool_name, approval included."""
        tool = self._tools[tool_name]
        if tool.gated and tool.gated(args):
            aid = approval_id_for(tool_name, args)
            return [("request_approval", {"tool": tool_name, "args": args, "reason": "policy"}),
                    (tool_name, {**args, "approval_id": aid})]
        return [(tool_name, args)]

    def _request_approval(self, tool, args, reason=""):
        target = self._tools.get(tool)
        if target is None or target.gated is None:
            raise ToolError(f"'{tool}' does not take approvals. Gated tools: "
                            f"{sorted(n for n, t in self._tools.items() if t.gated)}")
        err, clean = validate(target, {k: v for k, v in args.items() if k != "approval_id"}, self.metrics)
        if err:
            raise ToolError(f"cannot approve, the arguments are invalid: {err}")
        aid = approval_id_for(tool, clean)
        ok, note = self.approver(tool, clean, reason)
        self.approvals.append({"tool": tool, "args": clean, "reason": reason, "approved": ok, "note": note})
        if not ok:
            self.metrics["denials"] += 1
            return {"status": "denied", "note": note,
                    "instruction": "Do not run this action. Tell the user it was denied and why."}
        self.approved.add(aid)
        return {"status": "approved", "approval_id": aid}

    def call(self, name, args):
        """Run one tool call and return a JSON string for the model."""
        self.metrics["calls"] += 1
        tool = self._tools.get(name)
        if tool is None:
            self.metrics["unknown_tool"] += 1
            return json.dumps({"error": f"unknown tool '{name}'. Available: {sorted(self._tools)}"})
        err, clean = validate(tool, args, self.metrics)
        if err:
            self.metrics["invalid_args"] += 1
            return json.dumps({"error": err})
        if tool.gated is not None:
            try:
                needs = tool.gated(clean)
            except Exception:
                needs = False
            if needs:
                aid = clean.get("approval_id")
                if aid is None or aid not in self.approved or aid != approval_id_for(name, clean):
                    self.metrics["gate_blocks"] += 1
                    return json.dumps({"error": (
                        f"'{name}' with these arguments needs approval. Call request_approval with "
                        f"tool='{name}' and exactly these arguments, then retry with the approval_id.")})
        try:
            out = tool.fn(**{k: v for k, v in clean.items() if k != "approval_id"})
        except ToolError as e:
            self.metrics["tool_errors"] += 1
            return json.dumps({"error": str(e)})
        except TypeError as e:
            self.metrics["invalid_args"] += 1
            return json.dumps({"error": f"bad arguments: {e}"})
        if name != "request_approval" and not name.startswith(("list_", "get_", "search_", "check_")):
            self.actions.append({"tool": name, "args": {k: v for k, v in clean.items() if k != "approval_id"}})
        return json.dumps(out, default=str)

    def close(self):
        pass


class Env(Toolset):
    """A test world: a Toolset with generated fake data, a task, an answer key and a scorer."""
    name = "base"

    def __init__(self, seed=0, approver=None):
        self.seed = seed
        self.rng = random.Random(f"{self.name}:{seed}")
        self.build()
        super().__init__(approver=approver)

    def build(self):
        raise NotImplementedError

    def task(self):
        raise NotImplementedError

    def score(self):
        """Return {"score": float in [0, 1], "details": {...}}."""
        raise NotImplementedError

    def oracle_plan(self):
        """Return the list of (tool, args) calls a perfect agent would make."""
        raise NotImplementedError

    def instructions(self, ablate=()):
        path = Path(inspect.getfile(type(self))).with_suffix(".md")
        text = path.read_text()
        if not ablate:
            return text
        kept, skip = [], False
        for line in text.splitlines():
            if line.startswith("## "):
                skip = line[3:].strip().lower() in {a.lower() for a in ablate}
            if not skip:
                kept.append(line)
        return "\n".join(kept)


def f1(tp, fp, fn):
    if tp == 0:
        return 0.0
    p = tp / (tp + fp)
    r = tp / (tp + fn)
    return 2 * p * r / (p + r)
