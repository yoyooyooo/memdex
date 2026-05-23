---
name: memdex
description: Project-level semantic retrieval for local projects, vaults, repositories, and source sets using NotebookLM, repomix snapshots, freshness checks, and local evidence verification. Use when the user wants to initialize or refresh a semantic project index, ask architecture or implementation questions over a repo, locate files/functions/docs, or reuse an indexed Git worktree for search.
---

# Memdex

## Quick Start

Use `ask` or `locate` directly. Both run freshness preflight internally; do not
run `status` or `ensure` before every Q&A turn.

```bash
memdex ask --repo . "Where is reconnect/backfill implemented?"
memdex locate --repo . "invoice export retry command"
memdex ask --repo-worktree main "Where is reconnect/backfill implemented?"
```

First-time setup:

```bash
memdex init --repo . --create-notebook
memdex ask --repo . --yes "question"
```

In this monorepo, use `bun run memdex -- <args>`. Installed npm packages expose
`memdex`. If the skill is copied outside this layout, run the copied script path
directly or set `MEMDEX_CMD` to the wrapper command.

If `notebooklm` is missing, install it persistently:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
```

## Command Routing

- `ask`: architecture, docs, module relationships, status, and broad semantic
  questions. It may cite provider references, but exact claims still need local
  evidence.
- `locate`: files, functions, tests, commands, config, and line refs. It asks
  the provider for candidates, then verifies locally with `rg` / `sed` / `nl`.
- `--repo-worktree <branch>`: from a lightweight Git worktree, reuse an already
  checked-out indexed branch worktree, often `main`, without auto-refresh. Add
  `--force-refresh` only when intentionally updating that indexed worktree.
- `status`, `pack`, `ensure`, `refresh`: maintenance/debug commands, not the
  default agent path.
- `temp-source`: upload/list/cleanup NotebookLM-only derived materials.

Useful commands:

```bash
memdex ask --repo . --json "question"
memdex ask --repo . --verbose "question"
memdex locate --repo . --json "thing to find"
memdex locate --repo-worktree main "thing to find"
memdex pack --repo . --dry-run --include-files --json
memdex refresh --repo . --force
memdex temp-source upload --repo . --kind notes --title retry-design --file /tmp/source.md
```

## Failure Handling

- If preflight returns `not-initialized`, follow the printed `init` guidance and
  skip provider calls.
- If preflight returns `needs-first-upload-approval`, rerun with `--yes` only
  when the user already approved the broad upload.
- If `--repo-worktree` runs outside a Git worktree or cannot find the branch,
  follow the printed `--repo /path/to/indexed-worktree` guidance.
- If provider output names stale or missing paths, say so and fall back to local
  search.

## Safety Rules

- Treat NotebookLM as discovery only. Local files, tests, command output, and
  project docs are the authority for exact evidence.
- Never trust provider line numbers without local verification.
- Do not upload `.env*`, credentials, private raw logs, dependency folders,
  build output, or generated caches.
- Delete only source IDs recorded by this tool. Never delete human-uploaded
  NotebookLM sources by title prefix alone.
- For implementation claims, open local files before finalizing.

## References

- Detailed workflow and failure handling: [Workflow](references/workflow.md)
- Config and state schema: [Config Schema](references/config-schema.md)
