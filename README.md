# codebase-retrieve

Project-level semantic retrieval for codebases using NotebookLM snapshots,
repomix bundles, freshness checks, and local file/line verification.

This repository is an agent-facing workflow, not a replacement for source
control or tests. NotebookLM is used as a semantic locator. Exact paths, line
numbers, implementation claims, and completion evidence must be verified against
the local checkout.

## Status

Early public extraction from a local agent skill. The CLI is covered by unit
tests for planning, freshness-block handling, incremental upload bookkeeping,
and temporary-source cleanup.

## Contents

- `codebase-retrieve/` - the Codex/OpenAI-style skill and Python CLI.
- `notebooklm/` - supporting NotebookLM automation guidance used by the skill.
- `docs/codebase-retrieve-agent-first-proposal.md` - design notes for the
  ask-first / locate-first command shape.

## Requirements

- Python 3.10+
- `git`
- `rg` for local line verification
- `repomix` or `npx repomix`
- `notebooklm` CLI from `notebooklm-py`

Install NotebookLM CLI:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
notebooklm login
notebooklm auth check --test
```

## Quick Start

From this repository checkout:

```bash
python3 ./codebase-retrieve/scripts/codebase-retrieve.py init --repo /path/to/repo --create-notebook
python3 ./codebase-retrieve/scripts/codebase-retrieve.py ask --repo /path/to/repo "Where is retry/backfill documented?"
python3 ./codebase-retrieve/scripts/codebase-retrieve.py locate --repo /path/to/repo "invoice export retry command"
```

Generated next-step commands use the current script path. Set
`CODEBASE_RETRIEVE_CMD` if you wrap the script in another executable.

For the first broad upload, rerun with `--yes` only after reviewing the source
scope:

```bash
python3 ./codebase-retrieve/scripts/codebase-retrieve.py ask --repo /path/to/repo --yes "Where is retry/backfill documented?"
```

## Safety Boundary

- Do not upload `.env*`, credentials, private raw logs, production exports,
  dependency folders, build output, generated caches, or unreviewed user data.
- First broad upload is blocked unless the user passes `--yes` or runs an
  explicit refresh.
- Provider source cleanup deletes only source IDs recorded by this tool.
- NotebookLM output is discovery only. Verify exact facts locally with `rg`,
  `sed`, `nl`, tests, or project authority docs.

## NotebookLM Notice

This project is not affiliated with Google. It depends on the community
`notebooklm-py` CLI, which automates NotebookLM through unofficial interfaces.
Google may change NotebookLM behavior, limits, or authentication at any time.
Users are responsible for complying with Google and NotebookLM terms and for
uploading only content they are allowed to process in NotebookLM.

## Tests

```bash
python3 codebase-retrieve/tests/test_codebase_retrieve_cli.py
python3 -m py_compile codebase-retrieve/scripts/codebase-retrieve.py
```

## License

MIT. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.
