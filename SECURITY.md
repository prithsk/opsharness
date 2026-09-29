# Security

## Reporting
Report a vulnerability through this repo's private security advisory form (Security tab, then "Report a vulnerability"). Please do not open a public issue for it.

## What the harness protects
- **Provider keys.** OPENAI_API_KEY only goes to OPENAI_BASE_URL, or to OpenAI when that is unset. A base URL named in a policy string (`openai:model@url`) gets OPENAI_CUSTOM_API_KEY or no key at all. No key ever goes over plain http to a non-local host. ANTHROPIC_API_KEY only goes to api.anthropic.com.
- **Writes to real systems.** Any MCP tool its server does not mark read-only needs approval, and the approval id is bound to the exact arguments. Changing one argument after approval blocks the call. `--approver auto` is refused with real servers unless you set OPSHARNESS_ALLOW_AUTO_APPROVE=1.
- **Config secrets.** mcp.json expands `${NAME}` from the environment, so tokens never need to sit in the file. A missing variable stops the run.
- **Local files.** Approval request files are written only inside the approvals folder, with sanitized names.

## What you are responsible for
- **mcp.json runs programs.** Each server entry is a command the harness starts on your machine. Treat an mcp.json like a script, and only run one you wrote or reviewed.
- **Tool output is untrusted.** Text from real systems (emails, tickets, chat logs, web pages) can contain instructions aimed at the model. The approval gate stops unapproved writes, but reads are not gated. Read the arguments of every approval request, and deny anything that sends data somewhere you did not expect.
- **Logs hold data.** runs/ and approvals/ contain everything the agent read and did, which can include customer or personal data. Both folders are gitignored. Do not commit or share them.
- **Tool descriptions are untrusted too.** A malicious MCP server can describe its tools in misleading ways. Only connect servers you trust.

## Supply chain
The harness uses the Python standard library only. The agent repos depend on this repo at a pinned git tag. GitHub Actions run with read-only repository permissions, and Dependabot keeps the pinned actions current.
