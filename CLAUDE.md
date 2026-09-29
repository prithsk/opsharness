# Working on opsharness (core)

This repo is the harness. The five agents live in their own repos and install it.

## Commands
- Tests: `python -m unittest discover -s tests -t .` (must stay green)
- Full check with every agent installed: `python -m opsharness.run --env all --policy oracle,noop,chaos --seeds 0:10 --workers 8`

## Rules
- Any change here must keep three things true for every installed world: the oracle scores 1.0, the chaos fault counts stay exact, and the world scores 1.0 over MCP. Install all five agent repos and run their tests before tagging a release.
- Standard library only. State goes in markdown and JSON files.
- Never auto-approve against real systems.
- Tag releases (v0.2.0, v0.3.0 and so on). Agent repos pin a tag, so a breaking change needs a new tag and a pin bump in each agent repo.
- Record findings in FINDINGS.md with the run folder they came from.

Work through TASKS.md in order.
