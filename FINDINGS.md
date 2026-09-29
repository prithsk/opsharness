# Findings

Newest first. Each entry names the run folder it came from.

## 2026-09-29, split into six repos
- The harness is now its own package. Each of the five agents lives in its own repo and registers its world through an entry point.
- The core tests itself with a toy world (29 tests). Each agent repo runs the shared WorldContract plus its own wrong-agent tests.
- The cross-repo grid runs from the core with all five installed, and oracle and chaos score 1.0 on every world.

## 2026-09-29, runtime added
- The harness now runs the five projects as real agents through MCP, with terminal or markdown-file approvals and a log per run.
- Every test world scores 1.0 through the MCP client with the correct plan, so the MCP path adds no correctness loss. Denied approvals never reach the backend.
- The MCP client has not met a third-party server yet.

## 2026-09-28, pre-model verification
- Scorers: the oracle scores 1.0 and noop scores 0.0 on 100 seeds of every env, and 16 wrong-agent tests lose points.
- Harness: the chaos policy injected about 1,900 faults across 500 episodes (numbers as strings, arrays as strings, misspelled tools, broken JSON, missing arguments, skipped approvals, parallel calls). Every episode still scored 1.0, and every fault was counted exactly once. Breaking coercion on purpose made 13 of 150 episodes fail, so the test catches regressions.
- The tool-coverage check found that compliance and ambient offered a request_approval tool with nothing to approve. It is removed now.
- No real model has run yet.
