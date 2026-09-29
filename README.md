# opsharness

An agent harness that stays reliable across models and tools. The same harness runs agents in production and gets tested by worlds with hidden answer keys, so every change gets a score with no grader model in the loop.

Plain Python 3.10+, standard library only.

## The agents

Each agent lives in its own repo and installs this harness as a dependency.

| repo | what the agent does | inspired by (YC S26) |
|---|---|---|
| [opsharness-procurement](https://github.com/prithsk/opsharness-procurement) | part requests, shelf stock, supplier choice, POs | Waybill |
| [opsharness-hotel](https://github.com/prithsk/opsharness-hotel) | room assignment, upgrades, housekeeping, event RFPs | Zaplar |
| [opsharness-ecommerce](https://github.com/prithsk/opsharness-ecommerce) | shipping, returns, lost parcels under a merchant policy | Pango |
| [opsharness-compliance](https://github.com/prithsk/opsharness-compliance) | chat surveillance and trade reconstruction | TovenAI |
| [opsharness-ambient](https://github.com/prithsk/opsharness-ambient) | reminders and events from a day of transcript | Moonshot |

The agents are built from how each company describes its product publicly. They are not those companies' code and have no connection to them.

## What the harness does

1. It validates tool arguments against the schema and returns readable errors to the model, never exceptions.
2. It coerces and counts common slips: numbers sent as strings, whole floats for integers, arrays sent as JSON strings.
3. It repairs malformed JSON arguments when it can (code fences, trailing commas) and rejects them clearly when it cannot.
4. It gates risky actions. The model calls `request_approval` with the exact arguments and gets an id bound to them. A person approves or denies at the terminal or by editing a markdown file.
5. It stops an episode when the same failing call repeats three times.
6. It speaks MCP over stdio, so an agent can use any MCP server. Tools not marked read-only need approval by default.
7. It talks to Anthropic and any OpenAI-compatible API (OpenAI, OpenRouter, vLLM, Ollama) over plain urllib. Your provider key only goes to the provider you configured, never over plain http to a remote host. See SECURITY.md.
8. It logs invalid_args, unknown_tool, tool_errors, gate_blocks, denials and coercions for every run, so harness failures show up apart from model failures.

## Commands

```
pip install -e .                       # plus any agent repos you want
python -m opsharness.run --env all --policy oracle,noop,chaos --seeds 0:10 --workers 8
python -m opsharness.run --env all --seeds 0:10 --workers 8 \
  --policy anthropic:claude-haiku-4-5-20251001,openai:<model-id>
python -m opsharness.show runs/<stamp> --failing
python -m opsharness.agent --project <world> --policy <model>   # run from an agent repo
python -m opsharness.mcp_serve --env <world> --seed 3            # serve a world to any MCP client
```

`run` writes a REPORT.md with mean score per world and model, the spread between models, and harness-health totals. The spread is the number to watch. A reliable harness keeps it small.

## Write your own world

Subclass `Env`, give it tools, a task, an `oracle_plan()` and a `score()`, and put its rules in a markdown file next to the class. Register it in your pyproject:

```toml
[project.entry-points."opsharness.worlds"]
myworld = "my_package.world:MyWorldEnv"
```

Then test it with the shared contract:

```python
from opsharness.testing import WorldContract

class TestMyWorld(WorldContract, unittest.TestCase):
    world = MyWorldEnv
```

The contract checks that the correct plan scores 1.0, doing nothing scores 0.0, seeds are deterministic, skipped approvals get blocked, the harness survives injected slips with exact fault counts, every write tool gets used, and the world scores 1.0 over MCP. `opsharness/toy.py` is a small complete example.

## Verified

- 29 core tests pass against the built-in toy world, and each agent repo passes its own 10 or 11.
- The chaos policy replays correct plans with realistic slips (string numbers, string arrays, misspelled tools, broken JSON, missing arguments, skipped approvals, parallel calls). Every episode still scores 1.0, and every slip gets counted exactly once. Breaking coercion on purpose made the test fail.
- Every world scores 1.0 when served over MCP and driven through the MCP client. Denied actions never reach the backend.

## Not verified yet

- No real model has run. The adapters have never made a live call.
- The MCP client has only talked to this project's own servers. Only the stdio transport exists.
- Approvals come from the terminal or a markdown file. There is no webhook approver yet.

## Maintenance

- `tests.yml` runs the core tests on every push.
- `nightly.yml` installs all five agent repos, reruns oracle, noop and chaos on every world, and adds a cheap model when the ANTHROPIC_API_KEY secret is set.
- The contract's write-tool check fails when a tool stops being used by any correct plan.
