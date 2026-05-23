# memdex

**English** | [简体中文](README.zh-CN.md)

Memdex is an agent-facing semantic locator for local projects, repositories,
vaults, and source sets.

It helps an AI agent move from "I do not know where to look" to "I have exact
local evidence." It uses NotebookLM for broad semantic recall, repomix for
deterministic source snapshots, freshness checks for staleness boundaries, and
local files or commands for final authority.

> [!IMPORTANT]
> NotebookLM is a locator, not an authority layer. Exact paths, line numbers,
> implementation status, test results, and completion claims must come from
> local files, tests, command output, or project authority docs.

## Why

Large projects and reference source sets are hard for agents to navigate from a
cold start. `rg` is exact, but only after the agent knows the right terms.
NotebookLM can find related concepts and likely files, but its answers may be
stale, incomplete, or wrong about line numbers.

Memdex connects those two modes:

```text
semantic recall -> local verification -> evidence-backed answer
```

The point is not to replace local search. The point is to make local search
start from better candidates, then force exact claims back through the checkout.

## Locator Analogy

Think of Memdex as a map reader for an agent.

At the start, the agent may only know a vague question. NotebookLM widens the
search space and suggests concepts, files, symbols, tests, or commands. Memdex
then narrows those suggestions against the local checkout until the agent has
paths and evidence it can safely cite.

```text
vague question -> semantic candidates -> local paths -> verified line refs
```

If the semantic index may lag the worktree, Memdex exposes that freshness state
instead of pretending the provider is current.

## Agent-First, Not Index-First

Traditional retrieval tools often make the caller manage the index first:

```text
status -> ensure -> ask
```

Memdex makes `ask` and `locate` the normal entry points. They run freshness
preflight internally, refresh when policy allows, stop when user approval is
required, and print an actionable next command when blocked.

Use:

- `ask` for architecture, design, documentation, module relationships, and
  broad project questions.
- `locate` for files, symbols, tests, commands, config, and local line refs.
- `status`, `pack`, `ensure`, and `refresh` for maintenance or debugging, not
  normal agent Q&A.

## Core Objects

| Term | Plain meaning |
| --- | --- |
| Source Set | The local project, repo, vault, or reference corpus being indexed |
| `.memdex/config.json` | Local source-scope, provider, notebook, and policy config |
| Source Scope | Included roots plus safety exclusions for snapshot generation |
| Repomix Bundle | Deterministic text snapshot produced from the source scope |
| Chunk | Stable whole-file bundle unit uploaded to NotebookLM |
| Notebook Source | Provider-side source created and tracked by Memdex |
| Freshness Preflight | TTL and fingerprint check before provider use |
| Provider Answer | NotebookLM output used for discovery, not final authority |
| Local Verification | `rg`, `sed`, `nl`, tests, commands, or file reads that prove exact claims |
| Temporary Source | Derived NotebookLM-only material with recorded origin and optional TTL |

## How It Works

Memdex asks one routing question per query: should this be answered as semantic
Q&A, or should it locate exact files and lines?

```text
repo checkout
  -> .memdex/config.json
  -> repomix bundle chunks
  -> NotebookLM source set
  -> ask / locate provider query
  -> local path and line verification
```

`ask` and `locate` both start with freshness preflight:

```text
not initialized or needs approval -> stop with next command
fresh or refreshed -> call provider
stale but allowed -> call provider with warning
provider candidates -> local verification where exact claims matter
```

For implementation claims, the provider answer is only a lead. Open the local
file, run the relevant command, or inspect tests before finalizing.

## Install

Install the npm package:

```bash
npm install -g memdex
memdex --help
```

For local development from this monorepo checkout:

```bash
bun install
bun run memdex -- --help
```

Memdex also needs the external tools it orchestrates:

- Node.js 20+
- Bun 1.2+ for local development and package builds
- `git`
- `rg` for local line verification
- `repomix` or `npx repomix`
- `notebooklm` CLI from `notebooklm-py`

Install and authenticate the NotebookLM CLI:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
notebooklm login
notebooklm auth check --test
```

## How To Use

Initialize a target source set:

```bash
memdex init --repo /path/to/repo --create-notebook
```

Ask an architecture or documentation question:

```bash
memdex ask --repo /path/to/repo "Where is retry/backfill documented?"
```

Locate likely implementation files and line refs:

```bash
memdex locate --repo /path/to/repo "invoice export retry command"
```

From this monorepo checkout, prefix commands with `bun run memdex --`:

```bash
bun run memdex -- ask --repo /path/to/repo "Where is retry/backfill documented?"
```

First broad upload is blocked by default. Review `.memdex/config.json`, then
approve the upload explicitly:

```bash
memdex ask --repo /path/to/repo --yes "Where is retry/backfill documented?"
```

If you wrap or relocate the CLI, set `MEMDEX_CMD` so generated next-step
commands point at your wrapper.

## Worktree Reuse

When working from a lightweight Git worktree, reuse an indexed branch worktree
instead of uploading each feature checkout:

```bash
memdex ask --repo-worktree main "Where is retry/backfill documented?"
memdex locate --repo-worktree main "invoice export retry command"
```

`--repo-worktree` must run from inside a Git worktree. It resolves the
checked-out branch worktree, reuses its recorded `.memdex` config and source
state, and does not auto-refresh by default.

Use `--force-refresh` only when you intentionally want to update the indexed
branch worktree. If the branch is not checked out as a worktree, pass the
indexed path explicitly with `--repo`.

## Aligning With The Agent

Humans own upload approval, source scope, safety boundaries, and final
acceptance. Memdex owns the mechanical retrieval path: snapshot, freshness
preflight, provider query, candidate extraction, and local verification support.

A good agent flow is:

```text
need explanation -> memdex ask -> local evidence for exact claims
need location -> memdex locate -> open returned files -> cite local lines
need current index state -> memdex status
need forced rebuild -> memdex refresh --force
```

Do not run `status` or `ensure` before every question. `ask` and `locate`
already run the preflight needed for normal work.

## Rolling Retrieval

Memdex is designed for repeated agent use during long work sessions. Each pass
should leave the agent with clearer local evidence than it had before.

```text
question -> preflight -> provider recall -> local verification -> answer | next command
```

At the end of each pass, the agent should know:

- whether the provider was fresh enough for discovery;
- which claims came from local evidence;
- which paths were stale, missing, or only semantic leads;
- what command or local read should happen next.

If freshness is blocked, upload approval is missing, or provider output cannot
be verified locally, the agent should say that instead of upgrading a lead into
a fact.

## Source Scope

Default include roots cover common source, tests, docs, and command anchors:

```text
src, crates, packages, apps, bins, docs, scripts, tests, xtask,
AGENTS.md, CLAUDE.md, README.md, Cargo.toml, package.json, justfile
```

Default safety exclusions block common sensitive or noisy paths:

```text
.env*, credentials, .git, node_modules, target, dist, build, coverage,
generated caches, public assets, large binary/media/archive files
```

Review `.memdex/config.json` before approving the first broad upload.

## Safety Model

- Do not upload credentials, raw private logs, production exports, private user
  data, dependency folders, build output, generated caches, or unreviewed data.
- Do not trust NotebookLM line numbers or file existence claims without local
  verification.
- Do not delete NotebookLM sources by title prefix alone. Deletion is limited
  to source IDs recorded by Memdex, unless a user explicitly opts into broader
  cleanup.
- Treat stale or blocked freshness states as part of the answer boundary.

## NotebookLM Boundary

This project is not affiliated with Google. It depends on the community
`notebooklm-py` CLI, which automates NotebookLM through unofficial interfaces.
Google may change NotebookLM behavior, limits, authentication, or internal APIs
without notice. Users are responsible for complying with Google and NotebookLM
terms and for uploading only content they are allowed to process in NotebookLM.

## Quick Inspect

```bash
memdex --help
memdex ask --help
memdex locate --help
memdex status --repo /path/to/repo
memdex pack --repo /path/to/repo --dry-run --include-files
memdex ensure --repo /path/to/repo --yes
memdex refresh --repo /path/to/repo --force
memdex temp-source list --repo /path/to/repo
```

## CLI

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

`ask` and `locate` are the agent path. `status`, `pack`, `ensure`, `refresh`,
and `temp-source` are maintenance commands.

## Repository Layout

```text
packages/memdex/        npm package and CLI implementation
skills/memdex/          Agent skill for project retrieval
skills/notebooklm/      Supporting NotebookLM automation guidance
docs/                   Design notes and release docs
```

The CLI package is published as `memdex`.

## Development

```bash
bun install
bun run test
bun run check
```

The CLI is implemented in TypeScript, uses Commander for command routing, and
is bundled with Bun for npm packaging. Provider, packaging, and search work
happens through subprocess calls to external tools.

CI and npm publishing are documented in [docs/release.md](docs/release.md).

## Acknowledgements

This project builds on the community `notebooklm-py` project by Teng Lin for
NotebookLM automation: https://github.com/teng-lin/notebooklm-py

It also uses Repomix for AI-readable repository snapshots:
https://github.com/yamadashy/repomix

## License

MIT
