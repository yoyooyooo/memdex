# memdex

Memdex is an agent-facing semantic locator for local projects, repositories,
vaults, and source sets.

It uses NotebookLM for broad semantic recall, repomix for deterministic source
snapshots, freshness checks for staleness boundaries, and local files or
commands for final authority.

> NotebookLM is a locator, not an authority layer. Exact paths, line numbers,
> implementation status, test results, and completion claims must come from
> local files, tests, command output, or project authority docs.

## Install

```bash
npm install -g memdex
memdex --help
```

Requirements:

- Node.js 20+
- `git`
- `rg`
- `repomix` or `npx repomix`
- `notebooklm` CLI from `notebooklm-py`

Install and authenticate NotebookLM:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
notebooklm login
notebooklm auth check --test
```

## Usage

Initialize a source set:

```bash
memdex init --repo /path/to/repo --create-notebook
```

Ask semantic project questions:

```bash
memdex ask --repo /path/to/repo "Where is retry/backfill documented?"
```

Locate likely files, symbols, tests, commands, and local line refs:

```bash
memdex locate --repo /path/to/repo "invoice export retry command"
```

First broad upload is blocked by default. Review `.memdex/config.json`, then
approve explicitly:

```bash
memdex ask --repo /path/to/repo --yes "Where is retry/backfill documented?"
```

## Agent Path

Use `ask` and `locate` directly. Both run freshness preflight internally; do
not run `status` or `ensure` before every Q&A turn.

```text
need explanation -> memdex ask -> local evidence for exact claims
need location -> memdex locate -> open returned files -> cite local lines
need current index state -> memdex status
need forced rebuild -> memdex refresh --force
```

## Worktree Reuse

From a lightweight Git worktree, reuse an indexed branch worktree:

```bash
memdex ask --repo-worktree main "Where is retry/backfill documented?"
memdex locate --repo-worktree main "invoice export retry command"
```

`--repo-worktree <branch>` resolves the checked-out worktree for that branch
and reuses its `.memdex` config/state without auto-refresh. Pass
`--force-refresh` only when you intentionally want to update that indexed
branch worktree.

If cwd is not inside a Git worktree, or the branch is not checked out as a
worktree, rerun with `--repo /path/to/indexed-worktree`.

## Commands

```bash
memdex ask [--repo <repo> | --repo-worktree <branch>] [--yes] [--force-refresh] [--json] [--verbose] <question>
memdex locate [--repo <repo> | --repo-worktree <branch>] [--yes] [--force-refresh] [--include-provider-answer] [--json] [--verbose] <query>
memdex init [--repo <repo>] [--notebook-id <id>] [--project-name <name>] [--create-notebook] [--reuse-existing-notebook] [--include <specs>] [--force]
memdex status [--repo <repo>] [--json]
memdex pack [--repo <repo>] [--set-id <id>] [--dry-run] [--include-files] [--json]
memdex ensure [--repo <repo>] [--force] [--yes] [--json]
memdex refresh [--repo <repo>] [--force] [--json]
memdex temp-source upload --repo <repo> --kind <kind> --title <title> --file <file> [--ttl-seconds <seconds>] [--json]
memdex temp-source list [--repo <repo>] [--kind <kind>] [--json]
memdex temp-source cleanup [--repo <repo>] [--kind <kind>] [--set-id <id>] [--expired] [--include-untracked-prefix] --yes [--json]
```

`ask` and `locate` are the normal agent path. `status`, `pack`, `ensure`,
`refresh`, and `temp-source` are maintenance commands.

## From The Monorepo

```bash
bun run memdex -- --help
bun run memdex -- ask --repo /path/to/repo "Where is retry/backfill documented?"
bun run memdex -- locate --repo /path/to/repo "invoice export retry command"
```

Release and CI details live in the repository-level
[release process](https://github.com/yoyooyooo/memdex/blob/main/docs/release.md).
