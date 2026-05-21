# codebase-retrieve

[English](README.md) | [简体中文](README.zh-CN.md)

Agent-facing codebase semantic retrieval using NotebookLM, repomix snapshots,
freshness checks, and local line verification.

`codebase-retrieve` helps an AI agent answer repository questions without
treating an LLM answer as ground truth. It builds a project-local NotebookLM
index from reproducible repo bundles, keeps that index fresh enough for
discovery, then routes exact file and line claims back through the local
checkout.

> [!IMPORTANT]
> NotebookLM is used as a semantic locator, not an authority layer. Exact paths,
> line numbers, implementation status, test results, and completion claims must
> come from local files, tests, command output, or project authority docs.

## Why

Large repositories are hard for agents to navigate from cold start. Plain `rg`
is exact but only works when the agent already knows the right words. NotebookLM
can find related concepts and likely files, but its answers may be stale or lack
accurate line numbers.

This project combines both:

- NotebookLM for broad semantic recall.
- Repomix for deterministic, reviewable repo snapshots.
- Local `rg` / `sed` / `nl` verification for exact evidence.
- Freshness checks so agents know when the provider may lag the worktree.

## Features

- `ask`: semantic Q&A over a project notebook.
- `locate`: provider-assisted file/symbol discovery followed by local line refs.
- TTL and fingerprint preflight before each query.
- First broad upload approval gate.
- Incremental chunk uploads with stable whole-file chunk planning.
- Source cleanup limited to NotebookLM source IDs recorded by this tool.
- Temporary NotebookLM sources for derived material such as notes or study aids.
- npm package with a `codebase-retrieve` CLI.
- Codex/OpenAI-style skill files under `skills/`.

## How It Works

```text
repo checkout
  -> .codebase-retrieve/config.json
  -> repomix bundle chunks
  -> NotebookLM source set
  -> ask / locate provider query
  -> local path and line verification
```

`ask` and `locate` are the main entry points. Maintenance commands such as
`status`, `ensure`, and `refresh` are available, but normal agent workflows
should call `ask` or `locate` directly.

## Repository Layout

```text
packages/codebase-retrieve/ npm package and CLI implementation
skills/codebase-retrieve/   Agent skill for codebase retrieval
skills/notebooklm/          Supporting NotebookLM automation guidance
docs/                       Design notes and rationale
```

From this monorepo checkout, use the Bun workspace script:

```bash
bun run cbr -- --help
```

When installed as an npm package, the CLI command is `codebase-retrieve`.
If you wrap or relocate the script, set `CODEBASE_RETRIEVE_CMD` so generated
next-step commands point at your wrapper.

## Requirements

- Python 3.10+
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

## Quick Start

Initialize a target repository:

```bash
bun run cbr -- init \
  --repo /path/to/repo \
  --create-notebook
```

Ask an architecture or documentation question:

```bash
bun run cbr -- ask \
  --repo /path/to/repo \
  "Where is retry/backfill documented?"
```

Locate likely implementation files and line refs:

```bash
bun run cbr -- locate \
  --repo /path/to/repo \
  "invoice export retry command"
```

First broad upload is intentionally blocked unless approved. After reviewing
the source scope, rerun with `--yes`:

```bash
bun run cbr -- ask \
  --repo /path/to/repo \
  --yes \
  "Where is retry/backfill documented?"
```

## Source Scope

Default include roots cover common source, test, docs, and command anchors:

```text
src, crates, packages, apps, bins, docs, scripts, tests, xtask,
AGENTS.md, CLAUDE.md, README.md, Cargo.toml, package.json, justfile
```

Default safety exclusions block common sensitive or noisy paths:

```text
.env*, credentials, .git, node_modules, target, dist, build, coverage,
generated caches, public assets, large binary/media/archive files
```

Review `.codebase-retrieve/config.json` before approving the first broad upload.

## Safety Model

- Do not upload credentials, raw private logs, production exports, private user
  data, dependency folders, build output, generated caches, or unreviewed data.
- Do not trust NotebookLM line numbers or file existence claims without local
  verification.
- Do not delete NotebookLM sources by title prefix alone. Deletion is limited to
  source IDs recorded by this tool, unless a user explicitly opts into broader
  cleanup.
- Treat stale or blocked freshness states as part of the answer boundary.

## NotebookLM Boundary

This project is not affiliated with Google. It depends on the community
`notebooklm-py` CLI, which automates NotebookLM through unofficial interfaces.
Google may change NotebookLM behavior, limits, authentication, or internal APIs
without notice. Users are responsible for complying with Google and NotebookLM
terms and for uploading only content they are allowed to process in NotebookLM.

## Development

Run the local checks:

```bash
bun install
bun run test
bun run check
```

The control plane intentionally depends only on the Python standard library.
Provider, packaging, and search work happens through subprocess calls to
external tools.

CI and npm publishing are documented in [docs/release.md](docs/release.md).

## Acknowledgements

This project builds on the community `notebooklm-py` project by Teng Lin for
NotebookLM automation: https://github.com/teng-lin/notebooklm-py

It also uses Repomix for AI-readable repository snapshots:
https://github.com/yamadashy/repomix
