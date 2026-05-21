---
name: codebase-retrieve
description: Project-level semantic retrieval for codebases using repo snapshots, NotebookLM, repomix, TTL refresh, and local line verification. Use when the user wants to index a repo, refresh a codebase semantic snapshot, ask architecture or implementation questions over a repo, or locate files/functions/tests with semantic search followed by local rg/sed/nl verification.
---

# Codebase Retrieve

## Quick Start

Use this skill when a repository should behave like a semantic knowledge base.
It wraps a provider such as NotebookLM with a project-local config, reproducible
repomix bundle, TTL refresh, and local file/line verification.

Prerequisite: `notebooklm` must be a persistent CLI on PATH. If missing, run:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
```

```bash
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py init --repo . --create-notebook
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ask --repo . "Where is reconnect/backfill implemented?"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py locate --repo . "invoice export retry command"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py pack --repo . --dry-run
```

If the skill is installed outside this repository layout, run the copied script
path directly or set `CODEBASE_RETRIEVE_CMD` to your wrapper command.

New sessions should call `ask` or `locate` directly. Both commands run
freshness preflight internally, refresh or warn when policy allows, and stop
with short next-step guidance when setup or first-upload approval is blocked.
Do not run `status` or `ensure` before every Q&A turn.

Plain `ask` / `locate` output is human-oriented: it hides full freshness
metadata by default and prints only a short warning when the index is stale,
first-upload approval is needed, or auto-refresh is disabled. Use `--json` for
machine output with full `freshness`, or `--verbose` for plain output plus full
freshness metadata.

## Workflow

1. Read `.codebase-retrieve/config.json` or run `init` to create it. Default
   NotebookLM title is `codebase-retrieve:<project_name>`.
2. Use `ask` for architecture/docs questions. It runs freshness preflight before
   provider Q&A.
3. Use `locate` for code location questions. It runs freshness preflight, asks
   the semantic provider for candidate paths/symbols, then verifies exact
   file/line refs locally with `rg -n` and `nl -ba`/`sed`.
4. If preflight returns `not-initialized` or `needs-first-upload-approval`, do
   not call the provider; follow the printed init or `--yes` guidance.
5. Treat NotebookLM as discovery, not authority. Exact path, symbol, line
   number, completion, and evidence claims must come from the local checkout or
   project authority docs.

## Commands

```bash
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py init --repo .
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py init --repo . --create-notebook
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py init --repo . --reuse-existing-notebook
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ask --repo . "question"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ask --repo . --yes "question"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ask --repo . --verbose "question"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ask --repo . --json "question"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py locate --repo . "thing to find"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py locate --repo . --verbose "thing to find"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py locate --repo . --json "thing to find"
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py pack --repo . --dry-run
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py pack --repo . --dry-run --json
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py pack --repo . --dry-run --include-files --json
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py temp-source upload --repo . --kind notes --title retry-design --file /tmp/source.md
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py temp-source list --repo .
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py temp-source cleanup --repo . --kind notes --yes
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py status --repo .
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py ensure --repo . --yes
python3 ./skills/codebase-retrieve/scripts/codebase-retrieve.py refresh --repo . --force
```

Command responsibilities:

- `init`: create project config and bind to a project-specific NotebookLM notebook.
- `ask`: primary semantic Q&A entry; runs freshness preflight before provider Q&A.
- `locate`: primary code-location entry; runs freshness preflight, then semantic
  candidate discovery followed by local `rg` line verification.
- `pack`: maintenance/debug command for deterministic whole-file chunk planning;
  use `--dry-run` before upload when tuning group/chunk config.
- `status`: maintenance/debug command for config, local state, current
  fingerprint, and recorded sources.
- `ensure`: maintenance/prewarm command for TTL / fingerprint / bundle checks
  and upload only when policy says so.
- `refresh --force`: maintenance command for explicit source replacement.

If a repo has not been initialized, `status`, `ensure`, `refresh`, `ask`, and
`locate` should stop with init guidance instead of a raw missing-config error.
In `--json` mode, uninitialized repos return `freshness.status=not-initialized`
with `next.createNotebook`, `next.reuseExistingNotebook`, `next.ask`,
`next.locate`, and `next.askWithFirstUploadApproval`; provider calls are
skipped. `ask` and `locate` also skip provider calls when
`freshness.status=needs-first-upload-approval`; rerun with `--yes` only when the
user already approved the first broad upload.

`init` writes `.codebase-retrieve/config.json` and `.codebase-retrieve/.gitignore`.
State stays local under `.codebase-retrieve/state.local.json`. Repomix bundles
are temporary files under `.codebase-retrieve/cache/`; the script deletes chunk
files after hash comparison or source upload and keeps only source-set metadata
in state.

`ensure`, `refresh`, `ask`, and `locate` use a repo-local `.codebase-retrieve/.lock`
around freshness and upload decisions so concurrent requests do not race into
duplicate NotebookLM source uploads.

Chunked uploads are incremental. The planner keeps previous whole-file chunk
membership when it still fits under `max_chunk_bytes`, stores each rendered
chunk `contentSha256`, reuses ready NotebookLM sources whose content hash has
not changed, and uploads only changed or new chunks. New chunk uploads and
waits run in bounded parallelism while the repo lock is held. Retired old source
IDs are recorded in local state after the new active set commits; the next
locked run retries cleanup from state, so cleanup failure never invalidates the
ready index. A local `.codebase-retrieve/pending-upload.local.json` journal lets
the next run clean partial sources after an interrupted upload.

Derived temporary sources are managed separately from repo chunks. Use
`temp-source upload` for NotebookLM-only materials such as flashcard seeds.
Temporary titles use `cbrtmp--<YYMMDDHHmm>--<kind>--<slug>--<hash>.md` and are
recorded in `temporarySourceSets`. Cleanup deletes state-recorded temp source
IDs only; prefix matches not recorded in local state are reported as
`untrackedPrefixMatches` and are not deleted unless explicitly requested with
`--include-untracked-prefix --yes`.

Notebook naming is project-bound:

```text
notebook_title = codebase-retrieve:<project_name>
source_title_prefix = cbr
source_title = cbr--<YYMMDDHHmm>--<group>--<chunk>--<hash>.md
```

## Safety Rules

- First broad upload should be explicitly requested or run with `--yes`.
- Deleting old provider sources is allowed only after a successful new upload
  and only for source IDs recorded by this tool.
- Temporary source cleanup deletes only state-recorded temp source IDs by
  default. Never delete human-uploaded NotebookLM sources by prefix alone.
- Never upload `.env*`, credentials, private raw logs, dependency folders,
  build output, or generated caches.
- Do not trust provider line numbers. Always verify with local files.
- If provider output names missing/stale paths, report that and fall back to
  local search.

## References

- Config and state schema: [Config Schema](references/config-schema.md)
- Detailed workflow and failure handling: [Workflow](references/workflow.md)
- Agent-first rationale: [Proposal](../docs/codebase-retrieve-agent-first-proposal.md)
