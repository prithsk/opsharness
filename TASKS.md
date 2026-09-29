# Tasks

Each task ends with a gate. Log results in FINDINGS.md.

## 1. Live smoke test across all worlds
Install the five agent repos, then run `python -m opsharness.run --env all --seeds 0:1 --workers 8 --policy anthropic:claude-haiku-4-5-20251001,openai:<model-id>`.
Gate: no `policy_error` stops. If an adapter breaks, fix it and save the real response shape as a canned test in tests/test_harness.py.

## 2. Baseline grid
Same command with `--seeds 0:10`. Harness problems found in any agent repo come back here.
Gate: every harness-tagged failure has a fix with a test, and the spread shrinks on rerun.

## 3. Remote MCP servers
Add the HTTP transport to opsharness/mcp.py.
Gate: tests against a local fake HTTP server pass, and the contract still passes for all five worlds.

## 4. Webhook approver
Add an approver in opsharness/approvals.py that posts the request to a URL and waits for the decision.
Gate: tests against a local fake pass.

## 5. Nightly with a model
Add ANTHROPIC_API_KEY as a GitHub secret.
Gate: one green nightly run with the model step included.

## 6. Publish results
Put the real grid and the main findings in README.md.
Gate: the numbers match a committed run folder.
